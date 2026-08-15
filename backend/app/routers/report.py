"""API routes for report management."""

from datetime import datetime
from pathlib import Path as FilePath
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.data_source import DataSource
from app.models.report import Report, ReportItem
from app.schemas.report import (
    ReportCreate,
    ReportDetailResponse,
    ReportGenerateRequest,
    ReportGenerateResponse,
    ReportItemCreate,
    ReportItemReorderRequest,
    ReportItemResponse,
    ReportItemUpdate,
    ReportUpdate,
)
from app.services.report_generator import ReportGeneratorError, generate_report

router = APIRouter(
    prefix="/reports",
    tags=["reports"],
    dependencies=[Depends(get_current_user)],
)


# ---- Report Item Endpoints ----


@router.post(
    "/{report_id}/items",
    response_model=ReportItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_report_item(
    report_id: int, payload: ReportItemCreate, db: Session = Depends(get_db)
) -> ReportItem:
    """Add a new item to a report."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    item = ReportItem(report_id=report_id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.put("/{report_id}/items/{item_id}", response_model=ReportItemResponse)
def update_report_item(
    report_id: int, item_id: int, payload: ReportItemUpdate, db: Session = Depends(get_db)
) -> ReportItem:
    """Update an existing report item."""
    item = db.query(ReportItem).filter(
        ReportItem.id == item_id, ReportItem.report_id == report_id
    ).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report item not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    return item


@router.delete("/{report_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report_item(report_id: int, item_id: int, db: Session = Depends(get_db)) -> None:
    """Delete a report item."""
    item = db.query(ReportItem).filter(
        ReportItem.id == item_id, ReportItem.report_id == report_id
    ).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report item not found")

    db.delete(item)
    db.commit()
    return None


@router.patch("/{report_id}/items/order")
def reorder_report_items(
    report_id: int,
    payload: ReportItemReorderRequest,
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """Atomically update ``order_index`` for a report's items.

    Used by the drag-reorder UI to replace N parallel PUTs with one
    transactional call. All ``item_id`` values must belong to
    ``report_id``; any mismatch returns 422 so the caller can roll
    back the optimistic UI update.
    """
    item_ids = [e.item_id for e in payload.items]

    # Reject duplicate order_index values — the caller must assign unique
    # positions (the frontend's arrayMove-based reorder already does this).
    order_values = [e.order_index for e in payload.items]
    if len(order_values) != len(set(order_values)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="order_index values must be unique",
        )

    rows = (
        db.query(ReportItem)
        .filter(ReportItem.id.in_(item_ids), ReportItem.report_id == report_id)
        .all()
    )
    if len(rows) != len(set(item_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="All item_ids must belong to this report",
        )

    index_by_id = {e.item_id: e.order_index for e in payload.items}
    for row in rows:
        row.order_index = index_by_id[cast(int, row.id)]
    db.commit()
    return {"updated": len(rows)}


# ---- Report CRUD Endpoints ----


@router.get("", response_model=list[ReportDetailResponse])
def list_reports(
    response: Response,
    is_active: bool | None = Query(default=None),
    data_source_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[Report]:
    """List reports with optional filtering and pagination.

    ``limit`` is capped at 500 to keep response payloads bounded; the
    total number of rows matching the filter is returned in the
    ``X-Total-Count`` response header so the caller can drive a pager.
    """
    query = db.query(Report)
    if is_active is not None:
        query = query.filter(Report.is_active == is_active)
    if data_source_id is not None:
        query = query.filter(Report.data_source_id == data_source_id)

    total = query.count()
    response.headers["X-Total-Count"] = str(total)
    # Stable order so offset+limit produces consistent pages.
    return query.order_by(Report.id).offset(offset).limit(limit).all()


@router.post("", response_model=ReportDetailResponse, status_code=status.HTTP_201_CREATED)
def create_report(payload: ReportCreate, db: Session = Depends(get_db)) -> Report:
    """Create a new report with optional initial items."""
    # Check if report name already exists
    existing = db.query(Report).filter(Report.name == payload.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Report named '{payload.name}' already exists",
        )

    # Verify data source exists
    data_source = db.query(DataSource).filter(DataSource.id == payload.data_source_id).first()
    if not data_source:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Data source with id {payload.data_source_id} not found",
        )

    # Extract items before creating report
    items_data = payload.model_dump().get("items", [])
    report_data = {k: v for k, v in payload.model_dump().items() if k != "items"}

    report = Report(**report_data)
    db.add(report)
    db.flush()  # Get the report ID

    # Create report items
    for item_data in items_data:
        item = ReportItem(report_id=report.id, **item_data)
        db.add(item)

    db.commit()
    db.refresh(report)
    return report


@router.get("/{report_id}", response_model=ReportDetailResponse)
def get_report(report_id: int, db: Session = Depends(get_db)) -> Report:
    """Get a single report by ID with all items."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return report


@router.put("/{report_id}", response_model=ReportDetailResponse)
def update_report(report_id: int, payload: ReportUpdate, db: Session = Depends(get_db)) -> Report:
    """Update an existing report."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    update_data = payload.model_dump(exclude_unset=True)

    # Check name uniqueness if name is being updated
    if "name" in update_data and update_data["name"] != report.name:
        existing = db.query(Report).filter(Report.name == update_data["name"]).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Report named '{update_data['name']}' already exists",
            )

    for field, value in update_data.items():
        setattr(report, field, value)

    db.commit()
    db.refresh(report)
    return report


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(report_id: int, db: Session = Depends(get_db)) -> None:
    """Delete a report and all its items."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    db.delete(report)
    db.commit()
    return None


# ---- Report Generation Endpoints ----


@router.post("/generate", response_model=ReportGenerateResponse)
def generate_report_endpoint(
    request: ReportGenerateRequest, db: Session = Depends(get_db)
) -> ReportGenerateResponse:
    """Generate a report and return the output file or preview data."""
    report = db.query(Report).filter(Report.id == request.report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    try:
        result = generate_report(
            report=report,
            output_format=request.output_format,
            parameters=request.parameters,
            db=db,
        )
        # result may include an `errors` dict; pull it out so it goes into
        # the typed `item_errors` field rather than as an arbitrary kwarg.
        item_errors = result.pop("errors", {})
        return ReportGenerateResponse(
            success=True,
            report_id=cast(int, report.id),
            report_name=cast(str, report.name),
            output_format=request.output_format,
            item_errors=item_errors,
            **result,
        )
    except ReportGeneratorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/{report_id}/preview", response_class=HTMLResponse)
def preview_report(
    report_id: int,
    request: Request,
    format: str = Query(default="html", pattern="^html$"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Preview a report without generating a file. Returns raw HTML so the
    frontend iframe can load it directly via <iframe src=...> and scripts
    (Chart.js) execute without being stripped by DOMPurify."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    try:
        result = generate_report(
            report=report,
            output_format=format,
            parameters={},
            db=db,
            preview_only=True,
            base_url=str(request.base_url),
        )
        return HTMLResponse(content=result["preview_data"])
    except ReportGeneratorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/{report_id}/export/{format}", response_class=FileResponse)
def export_report(
    report_id: int,
    format: str = Path(..., pattern="^(excel|html)$"),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Export a generated report file."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{report.name}_{timestamp}"

    try:
        result = generate_report(
            report=report,
            output_format=format,
            parameters={},
            db=db,
        )
        file_path = result.get("file_path")
        if not file_path or not FilePath(file_path).exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Generated file not found",
            )

        media_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if format == "excel"
            else "text/html"
        )
        return FileResponse(
            path=file_path,
            filename=f"{filename}.{format}",
            media_type=media_type,
        )
    except ReportGeneratorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

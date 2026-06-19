"""API routes for report management."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.data_source import DataSource
from app.models.report import Report, ReportItem
from app.schemas.report import (
    ReportCreate,
    ReportDetailResponse,
    ReportGenerateRequest,
    ReportGenerateResponse,
    ReportItemCreate,
    ReportItemResponse,
    ReportItemUpdate,
    ReportUpdate,
)
from app.services.report_generator import ReportGeneratorError, generate_report

router = APIRouter(prefix="/reports", tags=["reports"])


# ---- Report Item Endpoints ----


@router.post(
    "/{report_id}/items",
    response_model=ReportItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_report_item(
    report_id: int, payload: ReportItemCreate, db: Session = Depends(get_db)
):
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
):
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
def delete_report_item(report_id: int, item_id: int, db: Session = Depends(get_db)):
    """Delete a report item."""
    item = db.query(ReportItem).filter(
        ReportItem.id == item_id, ReportItem.report_id == report_id
    ).first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report item not found")

    db.delete(item)
    db.commit()
    return None


# ---- Report CRUD Endpoints ----


@router.get("", response_model=list[ReportDetailResponse])
def list_reports(
    is_active: bool | None = Query(default=None),
    data_source_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """List all reports with optional filtering."""
    query = db.query(Report)
    if is_active is not None:
        query = query.filter(Report.is_active == is_active)
    if data_source_id is not None:
        query = query.filter(Report.data_source_id == data_source_id)
    return query.all()


@router.post("", response_model=ReportDetailResponse, status_code=status.HTTP_201_CREATED)
def create_report(payload: ReportCreate, db: Session = Depends(get_db)):
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
def get_report(report_id: int, db: Session = Depends(get_db)):
    """Get a single report by ID with all items."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return report


@router.put("/{report_id}", response_model=ReportDetailResponse)
def update_report(report_id: int, payload: ReportUpdate, db: Session = Depends(get_db)):
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
def delete_report(report_id: int, db: Session = Depends(get_db)):
    """Delete a report and all its items."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")

    db.delete(report)
    db.commit()
    return None


# ---- Report Generation Endpoints ----


@router.post("/generate", response_model=ReportGenerateResponse)
def generate_report_endpoint(request: ReportGenerateRequest, db: Session = Depends(get_db)):
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
        return ReportGenerateResponse(
            success=True,
            report_id=report.id,
            report_name=report.name,
            output_format=request.output_format,
            **result,
        )
    except ReportGeneratorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/{report_id}/preview", response_model=dict)
def preview_report(
    report_id: int,
    format: str = Query(default="html", pattern="^(html|json)$"),
    db: Session = Depends(get_db),
):
    """Preview a report without generating a file."""
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
        )
        return result
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
):
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
        if not file_path or not Path(file_path).exists():
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

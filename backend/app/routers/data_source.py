"""API routes for data source management."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.data_source import DataSource
from app.schemas.data_source import DataSourceCreate, DataSourceResponse, DataSourceUpdate
from app.services.connection import ConnectionError, test_connection
from app.services.report_generator import evict_engine

router = APIRouter(
    prefix="/data-sources",
    tags=["data-sources"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=list[DataSourceResponse])
def list_data_sources(db: Session = Depends(get_db)):
    """List all configured data sources."""
    return db.query(DataSource).all()


@router.post("", response_model=DataSourceResponse, status_code=status.HTTP_201_CREATED)
def create_data_source(payload: DataSourceCreate, db: Session = Depends(get_db)):
    """Create a new data source."""
    existing = db.query(DataSource).filter(DataSource.name == payload.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Data source named '{payload.name}' already exists",
        )

    source = DataSource(**payload.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.get("/{source_id}", response_model=DataSourceResponse)
def get_data_source(source_id: int, db: Session = Depends(get_db)):
    """Get a single data source by ID."""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
    return source


@router.put("/{source_id}", response_model=DataSourceResponse)
def update_data_source(
    source_id: int, payload: DataSourceUpdate, db: Session = Depends(get_db)
):
    """Update an existing data source."""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(source, field, value)

    db.commit()
    db.refresh(source)
    # Connection URL may have changed (host/port/user/password/db) — drop the
    # cached engine so the next call rebuilds it against the new config.
    evict_engine(source_id)
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_data_source(source_id: int, db: Session = Depends(get_db)):
    """Delete a data source."""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
    db.delete(source)
    db.commit()
    # Free any pooled connections that were bound to the now-deleted source.
    evict_engine(source_id)
    return None


@router.post("/{source_id}/test", response_model=dict)
def test_data_source(source_id: int, db: Session = Depends(get_db)):
    """Test connectivity to a data source."""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")

    try:
        return test_connection(source)
    except ConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

"""API routes for scheduled task management."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.report import Report
from app.schemas.report import ScheduleTaskCreate
from app.services.scheduler import get_scheduler

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/scheduler",
    tags=["scheduler"],
    dependencies=[Depends(get_current_user)],
)


class SchedulerJobResponse(BaseModel):
    """Response schema for scheduler job status."""

    job_id: str
    next_run: str | None
    trigger: str


class SchedulerSyncResponse(BaseModel):
    """Response schema for scheduler sync operation."""

    jobs_loaded: int
    message: str


@router.get("/status", response_model=dict)
def get_scheduler_status():
    """Get the current status of the scheduler."""
    return get_scheduler().get_status()


@router.post("/sync", response_model=SchedulerSyncResponse)
def sync_scheduler(db: Session = Depends(get_db)):
    """Reconcile scheduler with the database — adds jobs for active scheduled
    reports and drops jobs whose DB row no longer qualifies (paused, deleted,
    missing). Delegates to `sync_with_database` so the HTTP path stays in
    lockstep with the sidecar; without orphan removal, periodic re-sync would
    leak stale jobs."""
    scheduler = get_scheduler()
    scheduler.sync_with_database(db)

    # `jobs_loaded` reports the number of currently-active jobs the scheduler
    # holds after the reconcile — matches what callers expected before this
    # endpoint learned to drop orphans.
    count = len(
        db.query(Report).filter(
            Report.is_scheduled == True,  # noqa: E712
            Report.is_active == True,  # noqa: E712
            Report.cron_expression.isnot(None),  # noqa: E712
        ).all()
    )
    msg = f"Synced {count} scheduled reports"
    return SchedulerSyncResponse(jobs_loaded=count, message=msg)


@router.get("/jobs/{report_id}", response_model=SchedulerJobResponse)
def get_job_status(report_id: int):
    """Get the status of a scheduled job for a specific report."""
    scheduler = get_scheduler()
    status_info = scheduler.get_job_status(report_id)

    if not status_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No scheduled job found for report {report_id}",
        )

    return SchedulerJobResponse(**status_info)


@router.post("/jobs/{report_id}", response_model=SchedulerJobResponse)
def create_or_update_job(
    report_id: int,
    payload: ScheduleTaskCreate,
    db: Session = Depends(get_db),
):
    """Create or update a scheduled job for a report."""
    if payload.report_id != report_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="report_id in URL does not match body",
        )

    report_obj = db.query(Report).filter(Report.id == report_id).first()
    if not report_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )

    # Persist schedule + notification config; DB is the single source of truth.
    report_obj.is_scheduled = True
    report_obj.cron_expression = payload.cron_expression
    report_obj.schedule_description = payload.schedule_description
    report_obj.notification_config = payload.notification_config
    report_obj.is_active = payload.is_active
    db.commit()

    scheduler = get_scheduler()
    notification_config = report_obj.notification_config or {}
    try:
        scheduler.add_report_job(
            report_id=report_id,
            cron_expression=payload.cron_expression,
            notification_config=notification_config,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return SchedulerJobResponse(**scheduler.get_job_status(report_id))


@router.delete("/jobs/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(report_id: int, db: Session = Depends(get_db)):
    """Delete a scheduled job for a report."""
    # Update report's schedule configuration
    report_obj = db.query(Report).filter(Report.id == report_id).first()
    if report_obj:
        report_obj.is_scheduled = False
        report_obj.cron_expression = None
        report_obj.schedule_description = None
        report_obj.notification_config = None
        db.commit()

    scheduler = get_scheduler()
    scheduler.remove_report_job(report_id)
    return None

"""API routes for scheduled task management."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.report import Report
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
    scheduler = get_scheduler()
    return {
        "is_running": scheduler.scheduler.running,
        "jobs": [
            {
                "id": job.id,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
            for job in scheduler.scheduler.get_jobs()
        ],
    }


@router.post("/sync", response_model=SchedulerSyncResponse)
def sync_scheduler(db: Session = Depends(get_db)):
    """Sync scheduler with database - load all active scheduled reports."""
    scheduler = get_scheduler()
    db_gen = db.query(Report).filter(
        Report.is_scheduled == True,  # noqa: E712
        Report.is_active == True,  # noqa: E712
        Report.cron_expression.isnot(None),  # noqa: E712
    ).all()

    count = 0
    failed = []
    for report_obj in db_gen:
        try:
            scheduler.add_report_job(
                report_id=report_obj.id,
                cron_expression=report_obj.cron_expression,
            )
            count += 1
        except Exception as exc:
            failed.append({"report_id": report_obj.id, "error": str(exc)})
            logger.warning(f"Failed to add scheduler job for report {report_obj.id}: {exc}")

    msg = f"Synced {count} scheduled reports"
    if failed:
        msg += f", {len(failed)} failed"
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
    cron_expression: str,
    notification_config: dict | None = None,
    db: Session = Depends(get_db),
):
    """Create or update a scheduled job for a report."""
    # Verify report exists
    report_obj = db.query(Report).filter(Report.id == report_id).first()
    if not report_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )

    # Update report's schedule configuration
    report_obj.is_scheduled = True
    report_obj.cron_expression = cron_expression
    db.commit()

    scheduler = get_scheduler()
    try:
        scheduler.add_report_job(
            report_id=report_id,
            cron_expression=cron_expression,
            notification_config=notification_config,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    status_info = scheduler.get_job_status(report_id)
    return SchedulerJobResponse(**status_info)


@router.delete("/jobs/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(report_id: int, db: Session = Depends(get_db)):
    """Delete a scheduled job for a report."""
    # Update report's schedule configuration
    report_obj = db.query(Report).filter(Report.id == report_id).first()
    if report_obj:
        report_obj.is_scheduled = False
        report_obj.cron_expression = None
        db.commit()

    scheduler = get_scheduler()
    scheduler.remove_report_job(report_id)
    return None

"""API routes for scheduled task management."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, TypeAdapter
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.report import Report
from app.models.user import User
from app.schemas.notification import NotificationConfig
from app.schemas.report import ScheduleTaskCreate
from app.services.report import (
    PERMISSION_WRITE,
    get_report_for_user,
)
from app.services.scheduler import InvalidCronExpression, get_scheduler

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


class SchedulerStatusResponse(BaseModel):
    """Response schema for scheduler status."""

    is_running: bool
    jobs: list[SchedulerJobResponse]


@router.get("/status", response_model=SchedulerStatusResponse)
def get_scheduler_status() -> SchedulerStatusResponse:
    """Get the current status of the scheduler."""
    return SchedulerStatusResponse(**get_scheduler().get_status())


@router.post("/sync", response_model=SchedulerSyncResponse)
def sync_scheduler(db: Session = Depends(get_db)) -> SchedulerSyncResponse:
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
def get_job_status(report_id: int) -> SchedulerJobResponse:
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
    user: User = Depends(get_current_user),
) -> SchedulerJobResponse:
    """Create or update a scheduled job for a report.

    批 9.4: write ACL on the report — owner / write-grantee / admin
    can configure scheduling. Read-only consumers (public visibility
    without a grant) cannot install scheduled jobs, even though they
    can preview the report.
    """
    if payload.report_id != report_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="report_id in URL does not match body",
        )

    report_obj = get_report_for_user(
        db, report_id, user, level=PERMISSION_WRITE
    )
    if report_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )

    # Persist schedule + notification config; DB is the single source of truth.
    # ``payload.notification_config`` is a typed Pydantic model (WebhookConfig
    # | EmailConfig | DingTalkConfig | None) — the JSON column needs a plain
    # dict, so we dump the model. ``model_dump(mode="json")`` converts the
    # HttpUrl to a string (instead of leaving it as a Pydantic URL type
    # which the JSON encoder can't serialise).
    report_obj.is_scheduled = True
    report_obj.cron_expression = payload.cron_expression
    report_obj.schedule_description = payload.schedule_description
    report_obj.notification_config = (
        payload.notification_config.model_dump(mode="json")
        if payload.notification_config is not None
        else None
    )
    report_obj.is_active = payload.is_active
    db.commit()

    scheduler = get_scheduler()
    # ``report_obj.notification_config`` is now a dict (dumped at write
    # time). Re-parse into the typed NotificationConfig so the scheduler
    # in-memory state matches the on-disk shape.
    raw_cfg = report_obj.notification_config
    typed_cfg: NotificationConfig | None
    if raw_cfg is None:
        typed_cfg = None
    else:
        typed_cfg = TypeAdapter(NotificationConfig).validate_python(raw_cfg)
    try:
        scheduler.add_report_job(
            report_id=report_id,
            cron_expression=payload.cron_expression,
            notification_config=typed_cfg,
        )
    except InvalidCronExpression as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    job_status = scheduler.get_job_status(report_id)
    if not job_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No scheduled job found for report {report_id}",
        )
    return SchedulerJobResponse(**job_status)


@router.delete("/jobs/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a scheduled job for a report. Write ACL.

    DB update and scheduler removal are independent — the DB write marks
    the report as unscheduled (the sidecar will drop the job on its next
    sync), and the in-process scheduler removal is best-effort (in sidecar
    mode ``SCHEDULER_DISABLED=true`` the web process scheduler is empty,
    so this is a no-op — the sidecar handles it).
    """
    report_obj = get_report_for_user(
        db, report_id, user, level=PERMISSION_WRITE
    )
    if report_obj is None:
        # Still clean up any lingering scheduler job for this report ID.
        scheduler = get_scheduler()
        scheduler.remove_report_job(report_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )

    try:
        report_obj.is_scheduled = False
        report_obj.cron_expression = None
        report_obj.schedule_description = None
        report_obj.notification_config = None
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Failed to update report %d schedule state: %s", report_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove schedule",
        ) from exc

    # Best-effort: remove from the in-process scheduler. In sidecar mode the
    # web-process scheduler is empty (SCHEDULER_DISABLED=true), so this is a
    # no-op; the sidecar picks up the DB change on its next sync.
    try:
        scheduler = get_scheduler()
        scheduler.remove_report_job(report_id)
    except Exception as exc:
        logger.warning(
            "Scheduler removal for report %d failed (sidecar will clean up): %s",
            report_id, exc,
        )
    return None

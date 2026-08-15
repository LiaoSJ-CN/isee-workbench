"""HTTP endpoints for asynchronous report-generation jobs (批 3a).

Surface (all auth-gated; HTML preview stays synchronous on the existing
``/reports/generate`` and ``/reports/{id}/preview`` routes):

* ``POST /reports/{id}/jobs`` — enqueue an Excel render; returns the
  freshly-created :class:`ReportJob` in ``pending`` state so the caller
  can immediately start polling.
* ``GET /jobs/{id}`` — single-job status.
* ``GET /reports/{id}/jobs`` — per-report history (most-recent first).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session  # noqa: F401 — typing-only, kept for handlers

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.report import Report
from app.models.report_job import ReportJob
from app.models.user import User
from app.schemas.job import (
    JobStatus,
    ReportJobCreate,
    ReportJobResponse,
)
from app.services.job_queue import enqueue_report_job

# Pulled out of /reports router so future batch 3b (SSE stream on
# /jobs/{id}/stream) has a natural home without bloating the report
# router further. The mix of /reports/{id}/jobs and /jobs/{id} prefixes
# means two routers with different prefixes — the dependency on
# ``get_current_user`` is duplicated in both, which is intentional
# (the auth contract is per-router, not shared).
jobs_router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(get_current_user)],
)

report_jobs_router = APIRouter(
    prefix="/reports",
    tags=["jobs"],
    dependencies=[Depends(get_current_user)],
)


def _serialize(job: ReportJob) -> ReportJobResponse:
    """Build a typed response from an ORM row.

    Centralised so the ``file_url`` derivation lives in one place; the
    ORM attribute alone doesn't expose it.
    """
    return ReportJobResponse.from_orm_with_url(job)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# Per-IP rate limit on job enqueue. Sharing the same budget as the
# synchronous ``/reports/generate`` endpoint — both paths consume the
# same underlying worker pool — so a single client can't bypass the
# limit by mixing sync + async.
_enqueue_job_limiter = RateLimiter(
    max_requests=settings.reports_generate_rate_limit,
    window_seconds=60,
)


@report_jobs_router.post(
    "/{report_id}/jobs",
    response_model=ReportJobResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_report_job(
    report_id: int,
    payload: ReportJobCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportJobResponse:
    """Enqueue an Excel render of ``report_id`` and return the new job.

    404 if the report is gone — the queue refuses enqueues against
    missing reports so the caller sees the failure synchronously
    instead of a queued ``failed`` row 200ms later.
    """
    # Rate-limit by IP before any DB lookup. Same *budget* as
    # ``/reports/generate`` — both paths share the underlying pool —
    # but a distinct key namespace so the limiter state isn't shared
    # via DB row key collision (each limiter records its own bucket).
    if _enqueue_job_limiter.is_rate_limited(f"enqueue_job:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many job enqueues. Limit: "
                f"{settings.reports_generate_rate_limit}/min/IP."
            ),
            headers={"Retry-After": "60"},
        )

    if not db.query(Report).filter(Report.id == report_id).first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )

    try:
        job = enqueue_report_job(
            db=db,
            report_id=report_id,
            output_format=payload.output_format.value,
            user=user,
            parameters=payload.parameters,
            priority=payload.priority,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return _serialize(job)


@jobs_router.get(
    "/{id}",
    response_model=ReportJobResponse,
)
def get_report_job(
    id: int,
    db: Session = Depends(get_db),
) -> ReportJobResponse:
    """Single-job lookup. 404 if the id was never issued (or has been
    purged — we don't currently prune, but the contract stays stable)."""
    job = db.get(ReportJob, id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    return _serialize(job)


@report_jobs_router.get(
    "/{report_id}/jobs",
    response_model=list[ReportJobResponse],
)
def list_report_jobs(
    report_id: int,
    status_filter: JobStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[ReportJobResponse]:
    """Most-recent-first job history for a single report.

    Supports ``?status=done`` etc. for "show me only failures" filters;
    no separate endpoint because the typical use case is the recent
    pane in the report preview UI.
    """
    if not db.query(Report).filter(Report.id == report_id).first():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )

    q = db.query(ReportJob).filter(ReportJob.report_id == report_id)
    if status_filter is not None:
        q = q.filter(ReportJob.status == status_filter.value)
    rows = (
        q.order_by(ReportJob.created_at.desc(), ReportJob.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [_serialize(r) for r in rows]

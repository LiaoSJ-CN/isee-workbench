"""HTTP endpoints for asynchronous report-generation jobs (批 3a, 批 8.5).

Surface (all auth-gated; HTML preview stays synchronous on the existing
``/reports/generate`` and ``/reports/{id}/preview`` routes):

* ``POST /reports/{id}/jobs`` — enqueue an Excel render; returns the
  freshly-created :class:`ReportJob` in ``pending`` state so the caller
  can immediately start polling.
* ``GET /jobs/{id}`` — single-job status.
* ``GET /jobs/{id}/download`` — serve the worker-produced file by
  basename (批 8.5; previously the frontend called
  ``/reports/{id}/export/excel`` and re-rendered the whole report on
  download, wasting the worker's output).
* ``GET /reports/{id}/jobs`` — per-report history (most-recent first).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session  # noqa: F401 — typing-only, kept for handlers

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.report import Report
from app.models.report_job import JOB_STATUS_DONE, ReportJob
from app.models.user import User
from app.schemas.job import (
    JobOutputFormat,
    JobStatus,
    ReportJobCreate,
    ReportJobResponse,
)
from app.services.data_source import get_data_source_for_user
from app.services.job_queue import enqueue_report_job
from app.services.report import get_report_for_user

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

    report = db.query(Report).filter(Report.id == report_id).first()
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )

    # 批 9.3 + 9.4: enqueueing a render requires both the report
    # itself (read ACL) AND its data source (read ACL). Both layers
    # collapse to the same 404 message — no leak between "report
    # gone", "data source gone", "no report access", and "no DS access".
    ds = get_data_source_for_user(db, report.data_source_id, user)
    if ds is None or get_report_for_user(db, report_id, user) is None:
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
    user: User = Depends(get_current_user),
) -> ReportJobResponse:
    """Single-job lookup. 404 if the id was never issued (or has been
    purged — we don't currently prune, but the contract stays stable).

    批 9.3: gated on read ACL of the report's data source — same
    404 message whether the job is missing or the caller can't see
    the underlying report.
    """
    job = db.get(ReportJob, id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    report = db.get(Report, job.report_id)
    if (
        report is None
        or get_data_source_for_user(db, report.data_source_id, user) is None
        or get_report_for_user(db, job.report_id, user) is None
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    return _serialize(job)


@jobs_router.get("/{id}/download")
def download_report_job_output(
    id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """Serve the worker-produced file for a completed job (批 8.5).

    Closes the async-export loop: previously the frontend polled
    ``/jobs/{id}`` until ``status == 'done'`` and then called
    ``/reports/{id}/export/excel`` to download — that endpoint
    *re-runs* the report generator, discarding the worker's output.
    For a 30-second render the user paid 60s end-to-end. This route
    returns the worker's file directly.

    404 in three cases:

    * unknown job id (never issued / purged),
    * job exists but status != ``done`` (still pending/running, or
      failed with no file to serve),
    * status == ``done`` but the on-disk file is gone (manual cleanup,
      ``generated_reports_dir`` rotated).

    批 9.3: also 404 when the caller no longer has read access to
    the report's data source — the worker output is a snapshot of
    the data, but the ACL decision is made on the resource the
    snapshot was derived from. If the data source was deleted after
    the job completed the lookup naturally fails.

    Path-traversal protection: ``file_path`` may carry whatever the
    worker wrote, so we route through :func:`os.path.basename` and
    resolve relative to ``settings.generated_reports_dir``. A worker
    that wrote ``"../../../etc/passwd"`` resolves to ``passwd`` inside
    the output dir — which doesn't exist there, so the 404 still wins.
    """
    job = db.get(ReportJob, id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    report = db.get(Report, job.report_id)
    if (
        report is None
        or get_data_source_for_user(db, report.data_source_id, user) is None
        or get_report_for_user(db, job.report_id, user) is None
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    if job.status != JOB_STATUS_DONE or not job.file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job output not available",
        )

    safe_basename = os.path.basename(job.file_path)
    full_path = settings.generated_reports_dir / safe_basename
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generated file missing",
        )

    # Dispatch Content-Type on the actual output format (batch 8.1
    # added PDF; future formats slot in here). Unknown values fall
    # through to ``application/octet-stream`` — safer than guessing
    # a misclassified MIME for a brand-new format. ``output_format``
    # is typed ``str | None`` at the ORM layer for legacy rows; treat
    # None as "unknown" too.
    media_type = _media_type_for(job.output_format or "")
    return FileResponse(
        path=full_path,
        filename=safe_basename,
        media_type=media_type,
    )


def _media_type_for(output_format: str) -> str:
    """Map a queued job's ``output_format`` to its HTTP ``Content-Type``.

    Centralised so :func:`download_report_job_output` stays focused
    on auth + path traversal guards, and any future format extension
    (e.g. docx) only touches this one function.
    """
    mapping: dict[str, str] = {
        JobOutputFormat.EXCEL.value: (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        JobOutputFormat.PDF.value: "application/pdf",
    }
    return mapping.get(output_format, "application/octet-stream")


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
    user: User = Depends(get_current_user),
) -> list[ReportJobResponse]:
    """Most-recent-first job history for a single report.

    Supports ``?status=done`` etc. for "show me only failures" filters;
    no separate endpoint because the typical use case is the recent
    pane in the report preview UI.

    批 9.3: gated on DS read ACL — same 404 for "report gone" and
    "no DS access".
    """
    report = db.query(Report).filter(Report.id == report_id).first()
    if (
        report is None
        or get_data_source_for_user(db, report.data_source_id, user) is None
        or get_report_for_user(db, report_id, user) is None
    ):
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

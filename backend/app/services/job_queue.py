"""Asynchronous report-generation job queue.

批 3a: instead of running ``generate_report`` synchronously inside the
HTTP request (which blocks the worker for seconds-to-minutes on big
Excel exports), we offload it to a :class:`ThreadPoolExecutor`.

* The web process owns this executor.
* A job row in :class:`~app.models.report_job.ReportJob` tracks the
  lifecycle (``pending`` → ``done``/``failed``); the frontend polls it.
* HTML preview is intentionally **not** routed here — preview is small
  and the iframe needs an immediate response.

The executor is a process-level singleton. There is exactly one in the
web process and zero in the sidecar scheduler process (sidecar only runs
APScheduler cron ticks, not ad-hoc jobs).  Multi-worker web deployments
will each have their own pool — acceptable for the small ``max_workers``
we use; an external broker (Celery / RQ) is the next step if horizontal
scale becomes a problem.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.report import Report
from app.models.report_job import (
    JOB_STATUS_DONE,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    ReportJob,
)
from app.models.user import User
from app.services.report_generator import ReportGeneratorError, generate_report

logger = logging.getLogger(__name__)

# Pool size for Excel renders. Four is enough that a couple of slow
# exports don't head-of-line block the others, while keeping the
# process from spawning dozens of idle threads under load.
DEFAULT_MAX_WORKERS = 4


# Module-level executor + a Future registry keyed by job id so tests
# (and the rare operator who needs to know "did this finish?") can look
# up the in-flight task without holding a local reference.
_executor: ThreadPoolExecutor | None = None
_futures: dict[int, Future[Any]] = {}


def get_executor() -> ThreadPoolExecutor:
    """Lazy module-level executor singleton.

    Created on first call so the lifespan / scheduler / tests can all
    trigger init without a circular import.
    """
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=DEFAULT_MAX_WORKERS,
            thread_name_prefix="report-job",
        )
        logger.info("Report job executor started with max_workers=%d", DEFAULT_MAX_WORKERS)
    return _executor


def shutdown_executor(wait: bool = False) -> None:
    """Stop the executor and drop in-flight references.

    Called from FastAPI lifespan teardown for graceful shutdown. Pass
    ``wait=True`` only in tests so background tasks complete before the
    next test re-creates state.
    """
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=wait)
        _executor = None
    _futures.clear()


def enqueue_report_job(
    db: Session,
    report_id: int,
    output_format: str,
    user: User,
    parameters: dict[str, Any] | None = None,
    priority: int = 0,
) -> ReportJob:
    """Create a pending :class:`ReportJob` row and submit it to the pool.

    The HTTP caller gets the row back immediately (status=pending) and
    can poll ``GET /jobs/{id}`` for progress. The actual ``generate_report``
    call runs on a worker thread.

    The caller is responsible for flushing + committing the session
    before this returns so the row is visible to the worker that picks
    it up; we commit here as a belt-and-braces guarantee.
    """
    if output_format not in ("excel", "pdf"):
        # HTML stays synchronous (preview needs an immediate response).
        # If someone hits this with "html", reject rather than silently
        # route — the queue exists precisely because sync Excel is too slow.
        raise ValueError(f"output_format must be 'excel' or 'pdf' (got {output_format!r})")

    # Confirm FK target exists; save the caller a 4xx round-trip if not.
    report = db.query(Report).filter(Report.id == report_id).first()
    if report is None:
        raise LookupError(f"Report {report_id} not found")

    job = ReportJob(
        report_id=report_id,
        output_format=output_format,
        priority=priority,
        parameters=parameters or {},
        created_by=cast(str, user.username),
        status=JOB_STATUS_PENDING,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    job_id_value = cast(int, job.id)
    executor = get_executor()
    future = executor.submit(_run_job, job_id_value)
    _futures[job_id_value] = future
    # Don't hold the future forever — once the task finishes, the Future
    # sticks around in our dict but nobody reads it (the row in DB has
    # the truth). Periodic cleanup on submit keeps the dict bounded.
    future.add_done_callback(lambda _f: _futures.pop(job_id_value, None))

    logger.info(
        "Enqueued report job id=%s report_id=%s format=%s by=%s",
        job.id,
        report_id,
        output_format,
        user.username,
    )
    return job


def _run_job(job_id: int) -> None:
    """Worker callback: drive the job through running → done/failed.

    Runs on a pool thread, so it opens its own DB session — the request
    thread's session is closed by the time we get here.
    """
    db = SessionLocal()
    try:
        job = db.get(ReportJob, job_id)
        if job is None:
            logger.error("Report job %s disappeared before execution", job_id)
            return

        job.status = JOB_STATUS_RUNNING
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        report = db.get(Report, cast(int, job.report_id))
        if report is None:
            job.status = JOB_STATUS_FAILED
            job.error = f"Report {job.report_id} no longer exists"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            return

        try:
            result = generate_report(
                report=report,
                output_format=cast(str, job.output_format),
                parameters=cast(dict[str, Any], job.parameters or {}),
                db=db,
            )
        except ReportGeneratorError as exc:
            job.status = JOB_STATUS_FAILED
            job.error = str(exc)
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            logger.warning("Report job %s failed: %s", job_id, exc)
            return
        except Exception as exc:  # noqa: BLE001 — top-level guard so the pool never sees an unhandled exception
            job.status = JOB_STATUS_FAILED
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = datetime.now(timezone.utc)
            db.commit()
            logger.exception("Report job %s crashed: %s", job_id, exc)
            return

        # Happy path — file_path may be missing if a zero-row report
        # somehow produced no output; treat that as success-with-no-file.
        file_path = result.get("file_path") if isinstance(result, dict) else None
        job.file_path = cast(str | None, file_path)
        job.status = JOB_STATUS_DONE
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Report job %s done: %s", job_id, file_path)
    finally:
        db.close()

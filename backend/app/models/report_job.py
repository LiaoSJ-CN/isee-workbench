"""SQLAlchemy model for asynchronous report-generation jobs.

A :class:`ReportJob` is created when a caller wants to offload an
Excel report render to a worker thread (see
:mod:`app/services/job_queue.py`). The row goes through four states:

    pending  → running → done
                         → failed

The frontend polls ``GET /jobs/{id}`` (or, in batch 3b, SSE-streams
``/jobs/{id}/stream``) to track progress.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    pass


# String constants — kept on the model class so router/service code can
# import them instead of hard-coding literals scattered across files.
JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_DONE = "done"
JOB_STATUS_FAILED = "failed"
JOB_STATUSES = (JOB_STATUS_PENDING, JOB_STATUS_RUNNING, JOB_STATUS_DONE, JOB_STATUS_FAILED)
TERMINAL_JOB_STATUSES = (JOB_STATUS_DONE, JOB_STATUS_FAILED)


class ReportJob(Base):
    """An asynchronous report-generation request tracked in the meta DB.

    Unlike :class:`~app.models.report.Report`, this row is transient —
    it represents a single render attempt, not the report's ongoing
    definition. Successful rows are kept so the frontend can list
    ``GET /reports/{id}/jobs`` history; cleanup is the caller's job.
    """

    __tablename__ = "report_jobs"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(
        Integer,
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(16), nullable=False, default=JOB_STATUS_PENDING)
    output_format = Column(String(16), nullable=False)
    priority = Column(Integer, nullable=False, default=0)
    # Snapshot of the params submitted with the request so the worker
    # can replay them. Nullable for callers that don't pass any.
    parameters = Column(JSON, nullable=True, default=dict)

    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    file_path = Column(String(1024), nullable=True)
    error = Column(Text, nullable=True)

    __table_args__ = (
        # Listing endpoint orders by report_id + created_at desc; the
        # composite index keeps that path off a sort.
        Index("ix_report_jobs_report_id_created_at", "report_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ReportJob(id={self.id}, report_id={self.report_id}, "
            f"status='{self.status}', format='{self.output_format}')>"
        )

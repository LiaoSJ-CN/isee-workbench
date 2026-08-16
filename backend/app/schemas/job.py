"""Pydantic schemas for :class:`~app.models.report_job.ReportJob`.

Three surface shapes:

* :class:`ReportJobCreate` — request body for ``POST /reports/{id}/jobs``.
  The caller picks the output format (only ``excel`` is queued; HTML
  preview stays synchronous).
* :class:`ReportJobResponse` — single job payload used by ``GET /jobs/{id}``
  and the ``POST`` response so the frontend can immediately start
  polling without a second round-trip.
* :class:`ReportJobListResponse` — wraps a list (kept as a list, not a
  dict — the existing list endpoints use bare arrays for consistency).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobOutputFormat(str, Enum):
    """Output formats eligible for the async queue.

    HTML is intentionally absent — preview is small and the iframe
    needs an immediate response. Add it here if/when a future
    optimisation queues HTML too.
    """

    EXCEL = "excel"
    PDF = "pdf"


class JobStatus(str, Enum):
    """Job lifecycle states.

    Mirrors the string constants in
    :mod:`app.models.report_job` so Pydantic surfaces them to clients
    without a custom validator.
    """

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ReportJobCreate(BaseModel):
    """POST body for ``/reports/{id}/jobs``.

    ``parameters`` mirrors :class:`~app.schemas.report.ReportGenerateRequest`
    so callers can reuse the same validated payload.
    """

    output_format: JobOutputFormat = Field(
        default=JobOutputFormat.EXCEL,
        description="Only 'excel' is queued today; HTML preview stays synchronous.",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of the values to feed generate_report.",
    )
    priority: int = Field(default=0, ge=0, le=10)


class ReportJobResponse(BaseModel):
    """Single-job payload returned by ``GET /jobs/{id}`` and the POST response.

    ``file_url`` is the basename of ``file_path`` — the existing
    ``/reports/{id}/export/{format}`` endpoint already serves files
    from ``settings.generated_reports_dir`` by basename (SEC-8 / SEC-19),
    so the frontend can download by joining ``file_url`` with the host.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    status: JobStatus
    output_format: JobOutputFormat
    priority: int
    parameters: dict[str, Any] | None = None
    created_by: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    file_path: str | None = None
    file_url: str | None = None
    error: str | None = None

    @classmethod
    def from_orm_with_url(cls, obj: Any) -> "ReportJobResponse":
        """Materialise a ``ReportJob`` row and derive ``file_url`` from ``file_path``."""
        import os

        data = {
            "id": obj.id,
            "report_id": obj.report_id,
            "status": obj.status,
            "output_format": obj.output_format,
            "priority": obj.priority,
            "parameters": obj.parameters,
            "created_by": obj.created_by,
            "created_at": obj.created_at,
            "started_at": obj.started_at,
            "finished_at": obj.finished_at,
            "file_path": obj.file_path,
            "file_url": os.path.basename(obj.file_path) if obj.file_path else None,
            "error": obj.error,
        }
        return cls.model_validate(data)

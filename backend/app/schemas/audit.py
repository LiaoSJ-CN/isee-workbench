"""Pydantic schemas for the audit log (批 9.5).

Two response models — one for a single row, one for a paginated list.
Both lean on :class:`app.models.audit_log.AuditLog` for the source
attributes (``model_config = ConfigDict(from_attributes=True)``), so
the router can hand back the ORM row directly and FastAPI handles
serialization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    """Single audit-log row as returned by ``GET /audit-logs``."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_user_id: int | None
    action: str
    target_type: str
    target_id: int | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str | None
    ip_address: str | None
    user_agent: str | None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    """Paginated list envelope for ``GET /audit-logs``.

    ``total`` is the count of rows matching the active filter (without
    ``limit`` / ``offset`` applied) so the admin UI can show a pager.
    The router also writes the same value into the ``X-Total-Count``
    response header for clients that follow the existing list-endpoint
    convention.
    """

    items: list[AuditLogResponse]
    total: int
    limit: int
    offset: int


__all__ = ["AuditLogResponse", "AuditLogListResponse"]

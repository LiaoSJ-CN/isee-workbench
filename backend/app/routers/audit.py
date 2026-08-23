"""Admin-only audit log read endpoint (批 9.5).

``GET /audit-logs`` returns the rows written by
:func:`app.services.audit.log`. There is no ``POST`` / ``PUT`` /
``DELETE`` endpoint — the log is immutable. The single endpoint is
gated by :data:`app.deps.admin_required` so only users with
``role == 'admin'`` can see the trail; non-admin users get 403.

Filter parameters (all optional) match the columns that have a
single-column index:

- ``actor_user_id`` — "everything user X did"
- ``action`` — "every login / every data_source.create"
- ``target_type`` — "every change to a data_source / report"
- ``target_id`` — only meaningful when combined with ``target_type``;
  cheap thanks to the composite index ``(target_type, target_id)``
- ``since`` / ``until`` — inclusive ISO timestamps on ``created_at``

Pagination follows the project convention (批 5.5): ``limit`` capped
at 500, ``offset`` ≥ 0, total count returned in both the response body
and the ``X-Total-Count`` header.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_required
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogListResponse, AuditLogResponse

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=AuditLogListResponse)
def list_audit_logs(
    response: Response,
    actor_user_id: int | None = Query(default=None, ge=1),
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: int | None = Query(default=None, ge=1),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Annotated[Session, Depends(get_db)] = ...,  # type: ignore[assignment]
    _user: Annotated[User, Depends(admin_required)] = ...,  # type: ignore[assignment]
) -> AuditLogListResponse:
    """List audit-log rows newest-first.

    Admin-only. The dependency is :data:`app.deps.admin_required`
    which 403s on any non-admin caller. The ``_user`` parameter is
    unused — its sole job is to enforce the admin gate via FastAPI.
    """
    query = db.query(AuditLog)
    if actor_user_id is not None:
        query = query.filter(AuditLog.actor_user_id == actor_user_id)
    if action is not None:
        query = query.filter(AuditLog.action == action)
    if target_type is not None:
        query = query.filter(AuditLog.target_type == target_type)
    if target_id is not None:
        query = query.filter(AuditLog.target_id == target_id)
    if since is not None:
        query = query.filter(AuditLog.created_at >= since)
    if until is not None:
        query = query.filter(AuditLog.created_at <= until)

    total = query.count()
    # ``id`` as a tie-breaker keeps the page boundary stable when many
    # rows share a millisecond ``created_at`` (rare but possible).
    rows = (
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    response.headers["X-Total-Count"] = str(total)
    return AuditLogListResponse(
        items=[AuditLogResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


__all__ = ["router"]

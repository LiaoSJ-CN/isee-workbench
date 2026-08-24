"""Append-only audit log (批 9.5).

Every mutating endpoint (auth, data sources, reports, subscriptions,
scheduler, jobs, explorer) appends one row here after a successful
business ``commit()``. Three design rules:

1. **Append-only.** No UPDATE / DELETE endpoint exists. If we ever
   need to "correct" an audit row, the answer is a new compensating
   row — the original stays. Admin sees ``GET /audit-logs`` (admin-only,
   see :mod:`app.routers.audit`) and that's it.

2. **``before`` / ``after`` JSON snapshots.** Both nullable. ``before``
   is missing for create / login; ``after`` is missing for delete.
   Pydantic response schemas (``model_dump(mode='json')``) drive the
   shape so SQLAlchemy internal state never leaks; sensitive fields
   (``password`` / ``password_hash``) are redacted in the service
   layer (``app.services.audit._redact``) regardless of schema.

3. **``actor_user_id`` is FK ``users.id`` with ``ON DELETE SET NULL``.**
   Audit must outlive user deletion — if the audit FK were CASCADE,
   scrubbing a user would erase their history, which defeats the
   point of auditing. We accept that ``actor_user_id`` may be NULL
   on rows for users that have since been removed; the soft
   ``request_id`` column (from :func:`app.middleware.request_id.
   get_request_id`) is still there for cross-referencing logs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    pass


class AuditLog(Base):
    """One row per audited business event.

    A row is created by :func:`app.services.audit.log`, which is called
    by every router's mutating endpoint after the business transaction
    commits. The row is never updated by application code; ``created_at``
    is set once on insert.
    """

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)

    # Who performed the action. Nullable because:
    # - login events write the row before any user is authenticated
    #   (``actor_user_id`` comes from the just-loaded ``user.id`` after
    #   successful credential check, but failed logins aren't logged);
    # - deleted users leave their rows behind with ``actor_user_id=NULL``.
    # ``ondelete=SET NULL`` (not CASCADE) preserves audit history when
    # a user is removed.
    actor_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ``action`` and ``target_type`` are validated at the Python layer
    # (``app.services.audit.ALL_ACTIONS`` / ``ALL_TARGET_TYPES``) rather
    # than via DB CHECK constraints: SQLite / Postgres syntax differs
    # and Alembic migrations grow noisy. Both are indexed for the
    # filter patterns ``?action=...`` and ``?target_type=...`` on
    # ``GET /audit-logs``.
    action = Column(String(64), nullable=False, index=True)
    target_type = Column(String(64), nullable=False, index=True)

    # The affected resource's primary key. ``NULL`` for events that
    # have no concrete resource (``login`` / ``logout`` target_id is
    # always NULL; ``scheduler.sync`` has no row id either). The
    # composite index ``(target_type, target_id)`` is the key path for
    # "show me every change to report #42" queries.
    target_id = Column(Integer, nullable=True)

    # Pre / post snapshots of the mutated resource. Created by
    # :func:`app.services.audit._snapshot`, which prefers Pydantic
    # ``*Response`` schemas (they auto-strip SQLAlchemy state, convert
    # datetimes to ISO strings, and skip server-only fields) over
    # raw ``__dict__``.
    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=True)

    # HTTP context. ``request_id`` is copied from
    # :func:`app.middleware.request_id.get_request_id` so log lines and
    # audit rows line up; ``ip_address`` is ``request.client.host``
    # (rewritten by ProxyHeadersMiddleware when behind a trusted proxy).
    request_id = Column(String(64), nullable=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_audit_log_target_type_target_id", "target_type", "target_id"),
        Index("ix_audit_log_created_at", "created_at"),
        # Composite for the "everything user X did, newest first" query
        # (`WHERE actor_user_id = X ORDER BY created_at DESC`). Lets
        # the planner index-scan in order without a sort step once the
        # table grows past ~100k rows; the single-column ``actor_user_id``
        # index still exists for filters that don't sort.
        Index("ix_audit_log_actor_user_id_created_at", "actor_user_id", "created_at"),
        # `ip_address` filter — "show me everything from 1.2.3.4 in
        # the last 24h" is the common compliance / probe-trail query.
        # String column (max 64 chars) so the index is small.
        Index("ix_audit_log_ip_address", "ip_address"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging
        return (
            f"<AuditLog(id={self.id}, actor_user_id={self.actor_user_id}, "
            f"action='{self.action}', target_type='{self.target_type}', "
            f"target_id={self.target_id})>"
        )

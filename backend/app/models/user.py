"""User model (P3 / SEC-18 + 批 9).

Replaces the pre-P3 shared admin pattern (``settings.admin_password``
compared with ``!=`` in ``routers/auth.py``). The bootstrap admin user
is seeded from settings on first startup; afterwards the bcrypt hash
in this table is the source of truth.

批 9 (RBAC): adds ``role`` and a nullable ``org_id``. ``role`` is one
of ``admin`` / ``editor`` / ``viewer`` — the full set of batch 9
coarse-grained roles. ``org_id`` is reserved for a future multi-tenant
deployment; today every user has ``org_id = NULL`` (single-org).
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.sql import func

from app.database import Base

# Role string constants — keep in sync with the validation in
# ``app.services.auth_state`` and the role gating in ``app.deps``.
ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
ALL_ROLES = (ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER)


class User(Base):
    """An application user. Roles are coarse-grained (admin / editor /
    viewer); resource-level ACL lives in the ``DataSourceAccess`` and
    ``ReportAccess`` models added in 批 9.3 / 9.4."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)  # bcrypt utf-8 hash
    role = Column(
        String(16),
        nullable=False,
        default=ROLE_ADMIN,
        server_default=ROLE_ADMIN,
    )
    # Reserved for a future multi-tenant deployment; every user has
    # ``org_id = NULL`` today. See batch 9 design doc for rationale.
    org_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    disabled = Column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging
        return (
            f"<User(id={self.id}, username='{self.username}', "
            f"role='{self.role}', org_id={self.org_id})>"
        )

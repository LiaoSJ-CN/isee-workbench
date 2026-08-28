"""Per-user Dashboard sharing grants (批 14).

Mirrors :class:`app.models.report_access.ReportAccess` — same shape,
same UNIQUE(dashboard_id, user_id) constraint, same upsert semantics
in the service layer. New dashboards default to ``private`` visibility
so the grant table is the only path for cross-user access on freshly
created dashboards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    from app.models.dashboard import Dashboard
    from app.models.user import User


class DashboardAccess(Base):
    """A read/write grant from one user to a single dashboard."""

    __tablename__ = "dashboard_access"

    id = Column(Integer, primary_key=True, index=True)
    dashboard_id = Column(
        Integer,
        ForeignKey("dashboards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission = Column(String(16), nullable=False)  # "read" | "write"
    granted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "dashboard_id", "user_id", name="uq_dashboard_access_dashboard_user"
        ),
    )

    dashboard: Mapped["Dashboard"] = relationship(
        "Dashboard", back_populates="shares"
    )
    # ``user`` and ``grantor`` relationships are intentionally NOT
    # declared with ``back_populates`` — they're not needed for the
    # ACL helpers and a ``back_populates`` would require mirroring on
    # the User model, which would couple unrelated modules. Lookups
    # are direct via FK (mirroring the pattern in
    # :class:`ReportAccess`).
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    grantor: Mapped["User | None"] = relationship("User", foreign_keys=[granted_by])

    def __repr__(self) -> str:  # pragma: no cover - debugging
        return (
            f"<DashboardAccess(id={self.id}, dashboard_id={self.dashboard_id}, "
            f"user_id={self.user_id}, permission='{self.permission}')>"
        )

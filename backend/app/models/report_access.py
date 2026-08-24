"""Per-user Report sharing grants (批 9.4).

Mirrors :class:`app.models.data_source_access.DataSourceAccess` —
same shape, same UNIQUE(report_id, user_id) constraint, same
upsert semantics in the service layer. New reports default to
``private`` visibility so the grant table is the only path for
cross-user access on freshly created reports. Pre-9.4 reports are
backfilled ``public`` by the migration; their existing admin-only
usage keeps working without any explicit grant row.
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
    from app.models.report import Report
    from app.models.user import User


class ReportAccess(Base):
    """A read/write grant from one user to a single report."""

    __tablename__ = "report_access"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(
        Integer,
        ForeignKey("reports.id", ondelete="CASCADE"),
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
        UniqueConstraint("report_id", "user_id", name="uq_report_access_report_user"),
    )

    report: Mapped["Report"] = relationship("Report", back_populates="shares")
    # ``user`` and ``grantor`` relationships are intentionally NOT
    # declared here — they're not needed for the ACL helpers and a
    # back_populates would require mirroring on the User model, which
    # would couple unrelated modules. Lookups are direct via FK.
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    grantor: Mapped["User | None"] = relationship("User", foreign_keys=[granted_by])

    def __repr__(self) -> str:  # pragma: no cover - debugging
        return (
            f"<ReportAccess(id={self.id}, report_id={self.report_id}, "
            f"user_id={self.user_id}, permission='{self.permission}')>"
        )

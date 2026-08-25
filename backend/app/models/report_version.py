"""ORM models for Report versioning (manual snapshot + restore + diff).

Three tables mirror the live Report / ReportItem / ReportParameter
schemas column-by-column. Snapshot creation copies the live state into
these tables; restore overwrites live state from a chosen row. Diff
pairs items / parameters across snapshots by ``name``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class ReportVersion(Base):
    """One snapshot of a Report.

    Per-report ``version_number`` is monotonic (1, 2, 3, ...) assigned
    at creation time. ``is_pinned`` versions cannot be deleted.
    Mirrors all scalar columns from :class:`app.models.report.Report``
    except ``id``, ``created_at``, ``updated_at``.
    """

    __tablename__ = "report_versions"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(
        Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number = Column(Integer, nullable=False)
    label = Column(String(255), nullable=True)
    is_pinned = Column(Boolean, nullable=False, default=False, server_default="0")
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # --- Mirrored Report columns (snapshot of live state) ---
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    data_source_id = Column(Integer, ForeignKey("data_sources.id"), nullable=False)
    layout_config = Column(JSON, nullable=True)
    is_scheduled = Column(Boolean, nullable=True, default=False)
    cron_expression = Column(String(100), nullable=True)
    schedule_description = Column(String(255), nullable=True)
    notification_config = Column(JSON, nullable=True)
    output_formats = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=True, default=True)
    is_demo = Column(Boolean, nullable=False, default=False, server_default="0")
    visibility = Column(String(16), nullable=False, default="public", server_default="public")
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    org_id = Column(Integer, nullable=True)

    items: Mapped[list["ReportVersionItem"]] = relationship(
        "ReportVersionItem",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="ReportVersionItem.order_index",
    )
    parameters: Mapped[list["ReportVersionParameter"]] = relationship(
        "ReportVersionParameter",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="ReportVersionParameter.order_index",
    )

    __table_args__ = (
        UniqueConstraint("report_id", "version_number", name="uq_report_versions_report_version"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return (
            f"<ReportVersion(id={self.id}, report_id={self.report_id}, "
            f"version_number={self.version_number})>"
        )


class ReportVersionItem(Base):
    """Snapshot of one ReportItem row at snapshot creation time."""

    __tablename__ = "report_version_items"

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(
        Integer, ForeignKey("report_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(255), nullable=False)
    item_type = Column(String(50), nullable=False)
    order_index = Column(Integer, default=0)
    table_name = Column(String(255), nullable=True)
    fields = Column(JSON, nullable=True)
    where_conditions = Column(JSON, nullable=True)
    group_by = Column(JSON, nullable=True)
    order_by = Column(JSON, nullable=True)
    limit = Column(Integer, nullable=True)
    display_config = Column(JSON, nullable=True)
    custom_sql = Column(Text, nullable=True)
    original_item_id = Column(Integer, nullable=True)

    version: Mapped["ReportVersion"] = relationship("ReportVersion", back_populates="items")


class ReportVersionParameter(Base):
    """Snapshot of one ReportParameter row at snapshot creation time."""

    __tablename__ = "report_version_parameters"

    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(
        Integer, ForeignKey("report_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(String(64), nullable=False)
    label = Column(String(255), nullable=False)
    type = Column(String(16), nullable=False)
    required = Column(Boolean, nullable=False, default=True)
    default = Column(JSON, nullable=True)
    options = Column(JSON, nullable=True)
    order_index = Column(Integer, nullable=False, default=0)
    original_parameter_id = Column(Integer, nullable=True)

    version: Mapped["ReportVersion"] = relationship("ReportVersion", back_populates="parameters")

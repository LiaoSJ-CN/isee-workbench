"""SQLAlchemy models for application metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    from app.models.dashboard import DashboardItem
    from app.models.data_source_access import DataSourceAccess


class DataSource(Base):
    """Configured external database source.

    批 9.3 adds owner + org_id (reserved for future multi-tenant) and a
    many-to-many grants table so non-owner users can be explicitly
    granted ``read`` or ``write`` access. Owner = full control; admin
    role bypasses ACL entirely. Existing rows backfilled to admin
    ownership by the 9.3 Alembic migration.
    """

    __tablename__ = "data_sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    db_type = Column(String(50), nullable=False)
    host = Column(String(255), nullable=True)
    port = Column(Integer, nullable=True)
    database = Column(String(255), nullable=False)
    username = Column(String(255), nullable=True)
    password = Column(String(255), nullable=True)
    schema_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)

    # 批 9.3 RBAC. ``owner_user_id`` is nullable so the column can be
    # added before every row is migrated; ON DELETE SET NULL keeps a
    # deleted user from cascading into production data-source deletion.
    owner_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Reserved for a future multi-tenant deployment; NULL today
    # (single-org). Mirrors ``users.org_id`` (added in 批 9.1).
    org_id = Column(Integer, nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # backref from DataSourceAccess rows. cascade="all, delete-orphan"
    # so revoking / deleting a data-source cleans up its grants.
    grants: Mapped[list["DataSourceAccess"]] = relationship(
        "DataSourceAccess",
        back_populates="data_source",
        cascade="all, delete-orphan",
    )
    # Reverse-link for D: chart-type dashboard items that bind directly
    # to this data source. ``viewonly=True`` — the FK already has
    # ``ON DELETE SET NULL`` and we don't want ORM-level cascade. The
    # ``reports`` backref is provided by ``Report.data_source``'s
    # ``backref="reports"`` (see ``models/report.py``).
    dashboard_items: Mapped[list["DashboardItem"]] = relationship(
        "DashboardItem",
        primaryjoin="DataSource.id == DashboardItem.data_source_id",
        viewonly=True,
    )

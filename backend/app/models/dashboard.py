"""SQLAlchemy models for iSee dashboards (批 14).

A dashboard is a user-defined grid composition of report/text/chart
items, layered over existing :class:`Report` (and chart SQL) plumbing.
It mirrors :class:`Report`'s ACL shape — owner + visibility + a
:class:`DashboardAccess` per-user grant table — and adds subscription
plumbing via :class:`DashboardSubscription`.

Why one ``DashboardItem`` table (no child tables):
    The three item types — ``report`` / ``text`` / ``chart`` — share
    the layout columns (x/y/w/h/order_index/item_type/title); only the
    data-side columns diverge. Storing them as a single row keeps the
    layout representation simple for ``react-grid-layout`` (one query
    → one position-update PATCH) and avoids the FK gymnastics of three
    separate tables.

    ``chart`` items reuse :class:`ReportItem` SQL building via a
    transient proxy in :mod:`app.services.dashboard` — no SQL
    duplication.
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
    Text,
)
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.sql import func

from app.database import Base

# Visibility string constants — re-exported from :mod:`app.models.report`
# so a single canonical source stays in sync across models. Kept on this
# module's public surface so ``services.dashboard`` / ``schemas.dashboard``
# can import from either side without circular imports.
from app.models.report import (  # noqa: F401  # re-exported
    ALL_VISIBILITIES,
    VISIBILITY_ORG,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
)

if TYPE_CHECKING:
    from app.models.dashboard_access import DashboardAccess
    from app.models.dashboard_subscription import DashboardSubscription

# Runtime imports so all three dashboard modules register their mappers
# together. SQLAlchemy's class registry does late binding on string
# forward references, but only for classes already loaded — without
# these imports the ``relationship("DashboardSubscription", ...)`` in
# :class:`Dashboard` would fail to resolve at mapper-config time
# (since the router that would otherwise pull them in transitively
# doesn't exist until sub-batch 14.2). The reciprocal
# ``dashboard_subscription.py`` ↔ ``dashboard.py`` cycle is broken by
# keeping that side under ``TYPE_CHECKING``.
from app.models.dashboard_access import DashboardAccess  # noqa: E402, F401
from app.models.dashboard_subscription import DashboardSubscription  # noqa: E402, F401


class Dashboard(Base):
    """A user-authored grid composition of report/text/chart items.

    Mirrors :class:`Report`'s ACL columns (``visibility``,
    ``owner_user_id``, ``org_id``) so the dashboard helpers in
    :mod:`app.services.dashboard` can reuse the same primitives as
    :mod:`app.services.report`. No ``data_source_id`` on the dashboard
    itself — each item carries its own DS reference (direct for
    ``chart`` items, transitive via ``report.data_source_id`` for
    ``report`` items).
    """

    __tablename__ = "dashboards"
    __table_args__ = (
        # Composite index for the dashboard-list query path:
        # ``WHERE owner_user_id = ? AND visibility IN (...)``. Admin
        # bypasses this (sees everything) but per-user list paging
        # does filter by owner; the composite avoids a follow-up sort.
        Index(
            "ix_dashboards_owner_visibility",
            "owner_user_id",
            "visibility",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)

    # 批 9.4 — per-user ownership + visibility. ``visibility`` is the
    # coarse gate; private dashboards require an explicit grant or
    # ownership. Defaults to ``private`` so a freshly created
    # dashboard isn't auto-shared.
    visibility = Column(
        String(16),
        nullable=False,
        default=VISIBILITY_PRIVATE,
        server_default=VISIBILITY_PRIVATE,
    )
    owner_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Reserved for a future multi-tenant deployment (mirrors the same
    # nullable column on Report / User). Always NULL today.
    org_id = Column(Integer, nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    items: Mapped[list["DashboardItem"]] = relationship(
        "DashboardItem",
        back_populates="dashboard",
        cascade="all, delete-orphan",
        order_by="DashboardItem.order_index",
    )
    shares: Mapped[list["DashboardAccess"]] = relationship(
        "DashboardAccess",
        back_populates="dashboard",
        cascade="all, delete-orphan",
    )
    subscriptions: Mapped[list["DashboardSubscription"]] = relationship(
        "DashboardSubscription",
        back_populates="dashboard",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Dashboard(id={self.id}, name='{self.name}', "
            f"visibility='{self.visibility}')>"
        )


class DashboardItem(Base):
    """A single grid cell inside a dashboard.

    One row covers all three item types (``report`` / ``text`` /
    ``chart``). Columns are filled by the API/router based on
    ``item_type``; the renderer/service layer reads only the columns
    relevant for that type. SQL validation for ``chart`` items runs
    through :func:`app.services.report_generator.query_builder.build_query`
    via a transient :class:`ReportItem` proxy assembled in
    :func:`app.services.dashboard.execute_dashboard_chart`.
    """

    __tablename__ = "dashboard_items"

    id = Column(Integer, primary_key=True, index=True)
    dashboard_id = Column(
        Integer,
        ForeignKey("dashboards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Layout — react-grid-layout's 12-col grid coordinate space.
    # x/y/w/h default to 0/0/4/4 so a freshly added item lands in the
    # top-left of the grid.
    x = Column(Integer, nullable=False, default=0)
    y = Column(Integer, nullable=False, default=0)
    w = Column(Integer, nullable=False, default=4)
    h = Column(Integer, nullable=False, default=4)
    order_index = Column(Integer, nullable=False, default=0)

    item_type = Column(String(16), nullable=False)  # "report" | "text" | "chart"
    title = Column(String(255), nullable=True)

    # ---- type="report" ----
    report_id = Column(
        Integer,
        ForeignKey("reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ---- type="chart" ----
    data_source_id = Column(
        Integer,
        ForeignKey("data_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    table_name = Column(String(255), nullable=True)
    fields = Column(JSON, nullable=True, default=list)
    where_conditions = Column(JSON, nullable=True, default=list)
    group_by = Column(JSON, nullable=True, default=list)
    order_by = Column(JSON, nullable=True, default=list)
    limit = Column(Integer, nullable=True, default=1000)
    display_config = Column(JSON, nullable=True, default=dict)
    custom_sql = Column(Text, nullable=True)

    # ---- type="text" ----
    text_content = Column(Text, nullable=True)

    # ---- common ----
    parameters = Column(JSON, nullable=True, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    dashboard: Mapped["Dashboard"] = relationship(
        "Dashboard", back_populates="items"
    )

    def __repr__(self) -> str:
        return (
            f"<DashboardItem(id={self.id}, dashboard_id={self.dashboard_id}, "
            f"type='{self.item_type}')>"
        )

"""SQLAlchemy models for iSee reports."""

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    from app.models.dashboard import DashboardItem
    from app.models.data_source import DataSource
    from app.models.report_access import ReportAccess
    from app.models.report_parameter import ReportParameter

# Visibility string constants — keep in sync with the validation in
# ``schemas.report.ReportVisibility`` and the ACL helpers in
# ``services.report``. Public reports are visible to every authenticated
# user; private reports require an explicit grant. ``org`` (批 13)
# requires the template's owner and the viewer to share the same
# ``org_id``; NULL on either side is treated as a cross-tenant
# mismatch — see ``services.report._is_template_visible_to_user``.
VISIBILITY_PUBLIC = "public"
VISIBILITY_PRIVATE = "private"
VISIBILITY_ORG = "org"
ALL_VISIBILITIES: tuple[str, ...] = (VISIBILITY_PUBLIC, VISIBILITY_PRIVATE, VISIBILITY_ORG)


class Report(Base):
    """Business analysis report configuration."""

    __tablename__ = "reports"
    __table_args__ = (
        # 批 13 — composite index for the gallery query pattern
        # (``WHERE is_template = 1 AND template_category = ?``). Most
        # rows are ``is_template = 0``, so this index serves both the
        # "all templates" scan (first column) and the "templates in a
        # category" scan (both columns).
        Index(
            "ix_reports_template_category",
            "is_template",
            "template_category",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    data_source_id = Column(Integer, ForeignKey("data_sources.id"), nullable=False)

    # Report layout and configuration stored as JSON
    layout_config = Column(JSON, nullable=True, default=dict)

    # Scheduled task configuration
    is_scheduled = Column(Boolean, default=False)
    cron_expression = Column(String(100), nullable=True)
    schedule_description = Column(String(255), nullable=True)
    notification_config = Column(JSON, nullable=True, default=dict)

    # Output configuration
    output_formats = Column(JSON, nullable=True, default=lambda: ["excel", "html"])

    # Status
    is_active = Column(Boolean, default=True)

    # 批 10 demo-badge: marks rows inserted by ``scripts/seed_reports.py``
    # so the ReportList page can tag them with a "示例" badge — a visual
    # cue for new operators that these are seed-time scaffolding, not
    # user-authored reports. Always False for ordinary CRUD; no UI
    # exposes the toggle so end-users can't flag their own reports.
    is_demo = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )

    # 批 13 — 模板市场. A report row can be a template (``is_template``)
    # or an ordinary user-authored report. ``template_category`` is a
    # free-text bucket the admin assigns at save-as-template time (no
    # Categories table — taxonomy management is a future batch). The
    # composite index in ``__table_args__`` speeds up the template-
    # gallery query (``WHERE is_template = 1 AND template_category = ?``).
    # ``template_source_id`` lets a forked report trace back to the
    # template it came from; ON DELETE SET NULL so deleting a template
    # doesn't cascade-wipe user forks.
    is_template = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    template_category = Column(String(64), nullable=True)
    template_source_id = Column(
        Integer,
        ForeignKey("reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # 批 9.4: per-user ownership + visibility. ``visibility`` is the
    # coarse gate; private reports require an explicit grant or
    # ownership. Existing rows default to ``public`` for back-compat
    # with the pre-9.4 single-operator workflow (admin already saw
    # everything; the migration backfills ownership to admin).
    visibility = Column(
        String(16),
        nullable=False,
        default=VISIBILITY_PUBLIC,
        server_default=VISIBILITY_PUBLIC,
    )
    owner_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Reserved for a future multi-tenant deployment (mirrors the same
    # nullable column on DataSource / User). Always NULL today.
    org_id = Column(Integer, nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # 批 3: optimistic-concurrency version counter. SQLAlchemy's
    # ``version_id_col`` mechanism auto-increments this column on every
    # UPDATE so we have a strictly monotonic, sub-second-distinct
    # comparison key for ``If-Match`` checks.
    #
    # Why not derive the ETag from ``updated_at``? On SQLite the
    # default ``CURRENT_TIMESTAMP`` (and ORM ``onupdate=func.now()``)
    # collapses to second precision — two writes inside the same
    # second would collide on the ETag and the lock would silently
    # pass. An integer counter sidesteps the precision issue and is
    # cheaper to compare than a stringified timestamp.
    version = Column(Integer, nullable=False, server_default="1")

    # 批 3: optimistic-concurrency version counter.
    #
    # We intentionally do NOT bind ``version`` to SQLAlchemy's
    # mapper-level ``version_id_col``. Using the ORM hook auto-increments
    # ``version`` on every UPDATE — including ones issued as part of
    # cascade-delete or relationship-refresh housekeeping, which makes
    # the DELETE path fragile (StaleDataError / FK-violation races
    # during fixture teardown). Instead the update_report router
    # increments ``version`` manually via a single
    # ``UPDATE reports SET version = version + 1 WHERE id = ? AND
    # version = ?`` statement, with rowcount=0 → 412.
    #
    # The counter sidesteps the SQLite second-precision problem with
    # ``updated_at`` (see the long-form note in the column comment).
    __mapper_args__: dict[str, Any] = {}

    # Relationships
    data_source: Mapped["DataSource"] = relationship("DataSource", backref="reports")
    # Reverse-link for D: dashboards whose items reference this report.
    # ``viewonly=True`` because ``DashboardItem.report_id`` already has
    # ``ON DELETE SET NULL`` — we don't want ORM-level cascade to
    # silently widen that to ORM writes. The end-user surfaces
    # (``GET /reports/{id}/dashboards`` and the ReportEditor inline
    # section) are read-only navigation.
    dashboard_items: Mapped[list["DashboardItem"]] = relationship(
        "DashboardItem",
        primaryjoin="Report.id == DashboardItem.report_id",
        viewonly=True,
    )
    items: Mapped[list["ReportItem"]] = relationship(
        "ReportItem",
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ReportItem.order_index",
    )
    parameters: Mapped[list["ReportParameter"]] = relationship(
        "ReportParameter",
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ReportParameter.order_index",
    )
    shares: Mapped[list["ReportAccess"]] = relationship(
        "ReportAccess",
        back_populates="report",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Report(id={self.id}, name='{self.name}', visibility='{self.visibility}')>"


class ReportItem(Base):
    """Individual item within a report (chart, table, text block, etc.)."""

    __tablename__ = "report_items"

    id = Column(Integer, primary_key=True, index=True)
    report_id = Column(Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)

    # Item identification
    name = Column(String(255), nullable=False)
    item_type = Column(String(50), nullable=False)  # table, chart, text, metric

    # Display order
    order_index = Column(Integer, default=0)

    # Data query configuration
    table_name = Column(String(255), nullable=True)
    # Example: ["field1", "field2", "SUM(amount) as total"]
    fields = Column(JSON, nullable=True, default=list)
    # Example: [{"field": "status", "operator": "=", "value": "active"}]
    where_conditions = Column(JSON, nullable=True, default=list)
    # Example: ["category", "region"]
    group_by = Column(JSON, nullable=True, default=list)
    # Example: [{"field": "total", "direction": "DESC"}]
    order_by = Column(JSON, nullable=True, default=list)
    limit = Column(Integer, nullable=True, default=1000)

    # Visualization configuration
    display_config = Column(JSON, nullable=True, default=dict)
    # Example display_config: {
    #   "chart_type": "bar|line|pie|table",
    #   "title": "Sales by Region",
    #   "colors": ["#fff", "#000"],
    #   "columns": [{"field": "region", "header": "Region"}, {"field": "total", "header": "Total"}]
    # }

    # Custom SQL (alternative to auto-generated)
    custom_sql = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    report: Mapped["Report"] = relationship("Report", back_populates="items")

    def __repr__(self) -> str:
        return f"<ReportItem(id={self.id}, name='{self.name}', type='{self.item_type}')>"

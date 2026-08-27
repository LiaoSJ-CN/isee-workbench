"""Pydantic schemas for reports and report items."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.notification import NotificationConfig
from app.services.scheduler import validate_cron_expression

# 批 9.4 + 批 13 — visibility is the coarse ACL gate. Public reports
# are visible to every authenticated user; private reports require an
# explicit grant or ownership; ``org`` (批 13) requires the owner's
# and viewer's ``org_id`` to match (NULL on either side is treated as
# a cross-tenant mismatch, so the operator must set ``DEFAULT_ORG_ID``
# to opt in). Mirrors the string constants in :mod:`app.models.report`;
# keep in sync.
ReportVisibility = Literal["public", "private", "org"]


class ItemType(str, Enum):
    """Report item types."""

    TABLE = "table"
    CHART = "chart"
    TEXT = "text"
    METRIC = "metric"


class ChartType(str, Enum):
    """Chart visualization types."""

    BAR = "bar"
    LINE = "line"
    PIE = "pie"
    DOUGHNUT = "doughnut"
    RADAR = "radar"
    POLAR_AREA = "polarArea"
    SCATTER = "scatter"
    BUBBLE = "bubble"
    AREA = "area"
    HORIZONTAL_BAR = "horizontalBar"


class OperatorType(str, Enum):
    """SQL comparison operators."""

    EQ = "="
    NE = "!="
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    LIKE = "LIKE"
    IN = "IN"
    IS_NULL = "IS NULL"
    IS_NOT_NULL = "IS NOT NULL"


# ---- Base Schemas ----


class WhereCondition(BaseModel):
    """Query where condition configuration."""

    field: str = Field(..., description="Field name to filter on")
    operator: OperatorType = Field(default=OperatorType.EQ)
    value: str | int | float | list[Any] | None = Field(default=None, description="Filter value")


class OrderByItem(BaseModel):
    """Order by configuration."""

    field: str
    direction: str = Field(default="ASC", pattern="^(ASC|DESC)$")


class ColumnConfig(BaseModel):
    """Column configuration for table display."""

    field: str
    header: str | None = None
    format: str | None = None  # e.g., "{:.2f}" for number formatting
    width: int | None = None


class DisplayConfig(BaseModel):
    """Visualization configuration for a report item."""

    chart_type: ChartType | None = None
    title: str | None = None
    subtitle: str | None = None
    colors: list[str] | None = None
    columns: list[ColumnConfig] | None = None
    height: int | None = Field(default=300, ge=100, le=1000)
    width: int | None = None
    content: str | None = None
    # 图表额外配置
    show_legend: bool | None = True
    legend_position: str | None = "top"
    show_data_label: bool | None = False
    show_grid: bool | None = True
    stacked: bool | None = False
    horizontal: bool | None = False
    # 坐标轴配置
    x_axis_field: str | None = None
    y_axis_fields: list[str] | None = None
    # 饼图/环形图配置
    show_percentage: bool | None = True
    # 仪表盘配置
    min_value: float | None = None
    max_value: float | None = None
    unit: str | None = None


class ReportItemBase(BaseModel):
    """Base fields for a report item."""

    name: str = Field(..., min_length=1, max_length=255)
    item_type: ItemType = Field(...)
    order_index: int = Field(default=0, ge=0)

    # Data query configuration
    table_name: str | None = Field(default=None, max_length=255)
    fields: list[str] = Field(default_factory=list)
    where_conditions: list[WhereCondition] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[OrderByItem] = Field(default_factory=list)
    limit: int | None = Field(default=1000, ge=1, le=100000)

    # Visualization configuration
    display_config: DisplayConfig | None = Field(default_factory=DisplayConfig)
    custom_sql: str | None = Field(default=None, max_length=5000)


class ReportItemCreate(ReportItemBase):
    """Schema for creating a report item."""

    pass


class ReportItemUpdate(BaseModel):
    """Schema for updating a report item (all fields optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    item_type: ItemType | None = None
    order_index: int | None = Field(default=None, ge=0)
    table_name: str | None = Field(default=None, max_length=255)
    fields: list[str] | None = None
    where_conditions: list[WhereCondition] | None = None
    group_by: list[str] | None = None
    order_by: list[OrderByItem] | None = None
    limit: int | None = Field(default=None, ge=1, le=100000)
    display_config: DisplayConfig | None = None
    custom_sql: str | None = Field(default=None, max_length=5000)


class ReportItemResponse(ReportItemBase):
    """Schema returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReportItemOrderEntry(BaseModel):
    """One row in a batch reorder request."""

    item_id: int = Field(..., ge=1)
    order_index: int = Field(..., ge=0)


class ReportItemReorderRequest(BaseModel):
    """Batch reorder of report items.

    All ``item_id`` values must belong to the target report — the handler
    validates ownership and rejects partial mismatches with 422 so the
    reorder is all-or-nothing.
    """

    items: list[ReportItemOrderEntry] = Field(..., min_length=1)


# ---- Report Schemas ----


class ReportBase(BaseModel):
    """Base fields for a report."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    data_source_id: int = Field(...)
    layout_config: dict[str, Any] | None = Field(default_factory=dict)
    output_formats: list[str] = Field(default_factory=lambda: ["excel", "html"])
    is_active: bool = Field(default=True)
    # 批 9.4 — coarse ACL gate. Defaults to ``private`` for new
    # reports; the migration backfilled existing rows to ``public``
    # so admin's pre-9.4 workflow keeps working.
    visibility: ReportVisibility = Field(default="private")


class ReportCreate(ReportBase):
    """Schema for creating a report."""

    is_scheduled: bool = Field(default=False)
    cron_expression: str | None = Field(default=None, max_length=100)
    schedule_description: str | None = Field(default=None, max_length=255)
    items: list[ReportItemCreate] = Field(default_factory=list)


class ReportUpdate(BaseModel):
    """Schema for updating a report (all fields optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    data_source_id: int | None = None
    layout_config: dict[str, Any] | None = None
    is_scheduled: bool | None = None
    cron_expression: str | None = None
    schedule_description: str | None = None
    output_formats: list[str] | None = None
    is_active: bool | None = None
    visibility: ReportVisibility | None = None
    notification_config: NotificationConfig | None = None

    @field_validator("notification_config", mode="before")
    @classmethod
    def _empty_dict_to_none(cls, v: Any) -> Any:
        """See :meth:`ReportResponse._empty_dict_to_none` — the
        legacy ``dict()`` default has to round-trip as ``None`` so
        Pydantic doesn't trip on the empty-dict discriminator."""
        if isinstance(v, dict) and not v:
            return None
        return v


class ReportResponse(ReportBase):
    """Schema returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    is_scheduled: bool
    cron_expression: str | None = None
    schedule_description: str | None = None
    notification_config: NotificationConfig | None = None
    owner_user_id: int | None = None
    org_id: int | None = None
    # 批 10 demo-badge: True iff the row was inserted by
    # ``scripts/seed_reports.py``. The frontend ReportList renders a
    # "示例" Tag when this is true so operators can tell seed scaffolding
    # apart from reports they authored themselves. Read-only on the API —
    # no create/update field exposes the toggle, so the column can only
    # be flipped by editing the seed script + re-running it.
    is_demo: bool = False
    # 批 13 — template marketplace flags. Same read-only contract as
    # ``is_demo``: server-side derivation (save-as-template sets
    # ``is_template=True``, fork copies set ``template_source_id``) but
    # no client toggle. ``template_category`` is admin-supplied free
    # text — surfaced on the gallery card for filtering/grouping.
    is_template: bool = False
    template_category: str | None = None
    template_source_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("notification_config", mode="before")
    @classmethod
    def _empty_dict_to_none(cls, v: Any) -> Any:
        """Pre-union, the SQLAlchemy column default was ``dict()`` —
        old rows (and any new rows that haven't set the field) come
        back as ``{}``. The discriminated union rejects an empty
        dict because it has no ``type`` discriminator, so map the
        legacy shape to ``None`` for backwards compatibility."""
        if isinstance(v, dict) and not v:
            return None
        return v


class ReportDetailResponse(ReportResponse):
    """Report with all items included."""

    items: list[ReportItemResponse] = Field(default_factory=list)


# ---- Report Generation Schemas ----


class ReportGenerateRequest(BaseModel):
    """Request to generate a report."""

    report_id: int
    output_format: str = Field(default="excel", pattern="^(excel|html)$")
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Runtime parameters for the report"
    )


class ReportGenerateResponse(BaseModel):
    """Response containing generated report info."""

    success: bool
    report_id: int
    report_name: str
    output_format: str
    file_path: str | None = None
    preview_data: dict[str, Any] | None = None
    error: str | None = None
    # Per-item query failures from generate_report. Keys are item.name; values
    # are the human-readable error message (already surfaced as a banner in
    # the preview HTML). Empty dict = all items succeeded.
    item_errors: dict[str, str] = Field(default_factory=dict)


class ScheduleTaskCreate(BaseModel):
    """Schema for creating a scheduled task."""

    report_id: int
    cron_expression: str = Field(...)
    schedule_description: str | None = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)
    notification_config: NotificationConfig | None = None

    @field_validator("cron_expression")
    @classmethod
    def _validate_cron(cls, value: str) -> str:
        """Validate via the shared service-layer validator so cron
        expressions are checked consistently regardless of entry point
        (API request, sync_with_database, add_report_job)."""
        validate_cron_expression(value)
        return value


# ---------------------------------------------------------------------------
# Duplicate (批 10.3)
# ---------------------------------------------------------------------------


class ReportDuplicateRequest(BaseModel):
    """Payload for ``POST /reports/{id}/duplicate``.

    ``name`` is optional. If omitted, the server picks
    ``<original_name> (副本)`` with a numeric suffix on collision.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)


# ---------------------------------------------------------------------------
# Template marketplace (批 13)
# ---------------------------------------------------------------------------


class SaveAsTemplateRequest(BaseModel):
    """Payload for ``POST /reports/{id}/save-as-template``.

    ``visibility`` is mandatory and must match :data:`ReportVisibility`
    — server-side validation in the router. ``category`` is an
    admin-supplied free-text bucket the gallery uses for grouping;
    no length floor, but cap at 64 chars to match the column.
    """

    visibility: ReportVisibility
    category: str | None = Field(default=None, max_length=64)


class ForkFromTemplateRequest(BaseModel):
    """Payload for ``POST /reports/{id}/from-template``.

    ``name`` is optional. If omitted, the server picks
    ``<template_name> (副本)`` with a numeric suffix on collision
    (reuses ``duplicate_report`` machinery).
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)


# ---------------------------------------------------------------------------
# Report shares (批 9.4)
# ---------------------------------------------------------------------------


class ReportShareCreate(BaseModel):
    """Payload for ``POST /reports/{id}/shares``.

    Mirrors :class:`app.schemas.data_source.GrantCreate` — read/write
    binary, no admin tier. Upserts on ``(report_id, user_id)``.
    """

    user_id: int = Field(..., ge=1)
    permission: Literal["read", "write"]


class ReportShareResponse(BaseModel):
    """One :class:`app.models.report_access.ReportAccess` row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    user_id: int
    permission: str
    granted_by: int | None = None
    created_at: datetime | None = None

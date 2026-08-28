"""Pydantic schemas for dashboards and dashboard items (批 14).

Mirrors :mod:`app.schemas.report` — same visibility Literal, same
``ConfigDict(from_attributes=True)`` response pattern, same
``default_factory=list`` for JSON list defaults. Re-uses
:class:`app.schemas.report.WhereCondition` / :class:`OrderByItem` /
:class:`DisplayConfig` so a chart item's SQL configuration matches
what the report side already accepts.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.report import DisplayConfig, OrderByItem, WhereCondition

# Visibility string constants — kept in sync with
# :data:`app.models.report.VISIBILITY_*`. Re-declared as a Literal here
# so Pydantic can drive OpenAPI docs / discriminated unions.
DashboardVisibility = Literal["public", "private", "org"]


# ---- Item schemas ----


class DashboardItemBase(BaseModel):
    """Base fields for a dashboard item.

    Each ``item_type`` only uses a subset of the type-specific fields
    — the router validates which (and the service-layer renderers
    ignore the rest). Keeping them on a single schema avoids 3×
    schema duplication for a small surface area.
    """

    item_type: Literal["report", "text", "chart"]
    title: str | None = Field(default=None, max_length=255)
    order_index: int = Field(default=0, ge=0)
    # Layout coordinates — react-grid-layout's 12-col grid.
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    w: int = Field(default=4, ge=1, le=12)
    h: int = Field(default=4, ge=1, le=24)

    # ---- type="report" ----
    report_id: int | None = Field(default=None, ge=1)

    # ---- type="chart" ----
    data_source_id: int | None = Field(default=None, ge=1)
    table_name: str | None = Field(default=None, max_length=255)
    fields: list[str] = Field(default_factory=list)
    where_conditions: list[WhereCondition] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[OrderByItem] = Field(default_factory=list)
    limit: int | None = Field(default=1000, ge=1, le=100000)
    display_config: DisplayConfig | None = Field(default_factory=DisplayConfig)
    custom_sql: str | None = Field(default=None, max_length=5000)

    # ---- type="text" ----
    text_content: str | None = None

    # ---- common ----
    parameters: dict[str, Any] = Field(default_factory=dict)


class DashboardItemCreate(DashboardItemBase):
    """Schema for creating a dashboard item."""

    pass


class DashboardItemUpdate(BaseModel):
    """Schema for updating a dashboard item (all fields optional)."""

    title: str | None = Field(default=None, max_length=255)
    order_index: int | None = Field(default=None, ge=0)
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)
    w: int | None = Field(default=None, ge=1, le=12)
    h: int | None = Field(default=None, ge=1, le=24)

    report_id: int | None = Field(default=None, ge=1)

    data_source_id: int | None = Field(default=None, ge=1)
    table_name: str | None = Field(default=None, max_length=255)
    fields: list[str] | None = None
    where_conditions: list[WhereCondition] | None = None
    group_by: list[str] | None = None
    order_by: list[OrderByItem] | None = None
    limit: int | None = Field(default=None, ge=1, le=100000)
    display_config: DisplayConfig | None = None
    custom_sql: str | None = Field(default=None, max_length=5000)

    text_content: str | None = None
    parameters: dict[str, Any] | None = None


class DashboardItemResponse(DashboardItemBase):
    """Schema returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    dashboard_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DashboardItemLayoutEntry(BaseModel):
    """One row in a batch layout-update request — the
    ``onLayoutChange`` callback from ``react-grid-layout`` posts these
    in one PATCH so the save is one round trip."""

    item_id: int = Field(..., ge=1)
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    w: int = Field(..., ge=1, le=12)
    h: int = Field(..., ge=1, le=24)
    order_index: int | None = Field(default=None, ge=0)


class DashboardItemLayoutRequest(BaseModel):
    """Batch layout PATCH for the whole grid."""

    items: list[DashboardItemLayoutEntry] = Field(..., min_length=1)


# ---- Dashboard schemas ----


class DashboardBase(BaseModel):
    """Base fields for a dashboard."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    visibility: DashboardVisibility = Field(default="private")


class DashboardCreate(DashboardBase):
    """Schema for creating a dashboard."""

    items: list[DashboardItemCreate] = Field(default_factory=list)


class DashboardUpdate(BaseModel):
    """Schema for updating a dashboard (all fields optional)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    visibility: DashboardVisibility | None = None


class DashboardResponse(DashboardBase):
    """Schema returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int | None = None
    org_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DashboardDetailResponse(DashboardResponse):
    """Dashboard with all items included."""

    items: list[DashboardItemResponse] = Field(default_factory=list)


# ---- Shares (批 14) ----


class DashboardShareCreate(BaseModel):
    """Payload for ``POST /dashboards/{id}/shares``.

    Mirrors :class:`ReportShareCreate` — read/write binary, no admin
    tier. Upserts on ``(dashboard_id, user_id)``.
    """

    user_id: int = Field(..., ge=1)
    permission: Literal["read", "write"]


class DashboardShareResponse(BaseModel):
    """One :class:`app.models.dashboard_access.DashboardAccess` row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    dashboard_id: int
    user_id: int
    permission: str
    granted_by: int | None = None
    created_at: datetime | None = None


# ---- Duplicate (批 14) ----


class DashboardDuplicateRequest(BaseModel):
    """Payload for ``POST /dashboards/{id}/duplicate``."""

    name: str | None = Field(default=None, min_length=1, max_length=255)

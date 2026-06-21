"""Pydantic schemas for reports and report items."""

from datetime import datetime
from enum import Enum
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    value: str | int | float | list | None = Field(default=None, description="Filter value")


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
    output_formats: list[str] = Field(default_factory=list)
    is_active: bool = Field(default=True)


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
    notification_config: dict[str, Any] | None = None


class ReportResponse(ReportBase):
    """Schema returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    is_scheduled: bool
    cron_expression: str | None = None
    schedule_description: str | None = None
    notification_config: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
    notification_config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("cron_expression")
    @classmethod
    def _validate_cron(cls, value: str) -> str:
        """Delegate per-field range validation to APScheduler's CronTrigger.

        Earlier versions used a brittle regex that only checked segment count
        + character class, so e.g. ``99 9 * * * *`` slipped through Pydantic
        and surfaced as a 400 from the scheduler router. Constructing
        ``CronTrigger(...)`` is the authoritative check (same library that
        actually parses the expression at job-add time), and lets invalid
        expressions surface as 422 at request-parse time.
        """
        parts = value.split()
        if len(parts) != 6:
            raise ValueError(
                "Cron expression must have 6 fields: min hour dom mon dow year"
            )
        try:
            CronTrigger(
                minute=parts[0],
                hour=parts[1],
                day=parts[2],
                month=parts[3],
                day_of_week=parts[4],
                year=parts[5],
            )
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid cron expression: {exc}") from exc
        return value

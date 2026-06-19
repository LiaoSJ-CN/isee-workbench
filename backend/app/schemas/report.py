"""Pydantic schemas for reports and report items."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    AREA = "area"
    SCATTER = "scatter"


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
    colors: list[str] | None = None
    columns: list[ColumnConfig] | None = None
    height: int | None = Field(default=300, ge=100, le=1000)


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


class ReportResponse(ReportBase):
    """Schema returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    is_scheduled: bool
    cron_expression: str | None = None
    schedule_description: str | None = None
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


class ScheduleTaskCreate(BaseModel):
    """Schema for creating a scheduled task."""

    report_id: int
    cron_expression: str = Field(
        ...,
        pattern="^[0-9*,/-]+ [0-9*,/-]+ [0-9*,/-]+ "
        "[0-9*,/-]+ [0-9*,/-]+ [0-9*,/-]+$",
    )
    schedule_description: str | None = Field(default=None, max_length=255)
    is_active: bool = Field(default=True)
    notification_config: dict[str, Any] = Field(default_factory=dict)

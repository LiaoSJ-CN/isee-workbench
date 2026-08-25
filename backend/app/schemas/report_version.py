"""Pydantic schemas for the report-versioning endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Summary shapes — used by GET list (no items/parameters inline)
# ---------------------------------------------------------------------------


class ReportVersionItemSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    item_type: str
    order_index: int


class ReportVersionParameterSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    label: str
    type: str
    required: bool
    order_index: int


class ReportVersionSummary(BaseModel):
    """One row in the history list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    version_number: int
    label: str | None
    is_pinned: bool
    created_by: int | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Full snapshot response — used by GET /versions/{vid}
# ---------------------------------------------------------------------------


class ReportVersionItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    item_type: str
    order_index: int
    table_name: str | None = None
    fields: list[str] | None = None
    where_conditions: list[Any] | None = None
    group_by: list[str] | None = None
    order_by: list[Any] | None = None
    limit: int | None = None
    display_config: dict[str, Any] | None = None
    custom_sql: str | None = None


class ReportVersionParameterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    label: str
    type: str
    required: bool
    default: Any | None = None
    options: list[str] | None = None
    order_index: int


class ReportVersionResponse(ReportVersionSummary):
    """Full snapshot returned by GET /versions/{vid}."""

    name: str
    description: str | None = None
    data_source_id: int
    layout_config: dict[str, Any] | None = None
    is_scheduled: bool
    cron_expression: str | None = None
    schedule_description: str | None = None
    notification_config: dict[str, Any] | None = None
    output_formats: list[str] | None = None
    is_active: bool
    visibility: str
    owner_user_id: int | None = None
    org_id: int | None = None

    items: list[ReportVersionItemResponse] = Field(default_factory=list)
    parameters: list[ReportVersionParameterResponse] = Field(default_factory=list)


class ReportVersionCreate(BaseModel):
    """POST /reports/{id}/versions payload."""

    label: str | None = Field(default=None, max_length=255)


# ---------------------------------------------------------------------------
# Diff shapes — used by GET /versions/{vid}/diff
# ---------------------------------------------------------------------------


class FieldChange(BaseModel):
    field: str
    old_value: Any | None = None
    new_value: Any | None = None


class ItemDiff(BaseModel):
    name: str
    changes: list[FieldChange]


class ParameterDiff(BaseModel):
    name: str
    changes: list[FieldChange]


class ReportVersionDiff(BaseModel):
    base_version: int
    target_version: int | None = None  # None means current live Report

    report_changes: list[FieldChange] = Field(default_factory=list)
    items_added: list[ReportVersionItemResponse] = Field(default_factory=list)
    items_removed: list[ReportVersionItemResponse] = Field(default_factory=list)
    items_modified: list[ItemDiff] = Field(default_factory=list)
    parameters_added: list[ReportVersionParameterResponse] = Field(default_factory=list)
    parameters_removed: list[ReportVersionParameterResponse] = Field(default_factory=list)
    parameters_modified: list[ParameterDiff] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Restore response
# ---------------------------------------------------------------------------


class ReportVersionRestoreResponse(BaseModel):
    """Response of POST /versions/{vid}/restore — wraps the new live Report."""

    report: dict  # ReportResponse (dict to avoid circular import at type-check time)
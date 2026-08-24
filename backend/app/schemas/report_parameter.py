"""Pydantic schemas for report parameters (batch 4a).

First discriminated union in the codebase: ``type`` is the discriminator
key, chosen via ``Field(discriminator="type")`` over an ``Annotated``
Union of five variants. Each variant enforces its own type-specific
constraints (e.g. ``EnumParam`` requires a non-empty ``options`` list;
``DateParam.default`` must be ISO-8601). The discriminated form gives a
clean OpenAPI ``oneOf`` schema and lets the frontend build raw JSON
without importing an enum.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ParameterType(str, Enum):
    """Server-side enum mirrored from the ``type`` column."""

    STRING = "string"
    NUMBER = "number"
    DATE = "date"
    ENUM = "enum"
    BOOL = "bool"


class ReportParameterBase(BaseModel):
    """Shared fields across all parameter variants."""

    name: str = Field(
        ...,
        max_length=64,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="Identifier used in {name} placeholders. ASCII letters, "
        "digits, underscore; must not start with a digit.",
    )
    label: str = Field(..., min_length=1, max_length=255)
    required: bool = Field(default=True)
    order_index: int = Field(default=0, ge=0)


class StringParam(ReportParameterBase):
    type: Literal["string"] = "string"
    default: str | None = None


class NumberParam(ReportParameterBase):
    type: Literal["number"] = "number"
    default: float | int | None = None


class DateParam(ReportParameterBase):
    type: Literal["date"] = "date"
    default: str | None = None

    @field_validator("default")
    @classmethod
    def _validate_iso8601(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # Accept ISO-8601 date or datetime; reject anything else with a clear message.
        try:
            datetime.fromisoformat(v)
        except ValueError as exc:
            raise ValueError(f"default must be ISO-8601 (got {v!r})") from exc
        return v


class EnumParam(ReportParameterBase):
    type: Literal["enum"] = "enum"
    options: list[str] = Field(..., min_length=1)
    default: str | None = None

    @field_validator("default")
    @classmethod
    def _default_in_options(cls, v: str | None, info: Any) -> str | None:
        if v is None:
            return v
        options: list[str] = info.data.get("options") or []
        if v not in options:
            raise ValueError(f"default {v!r} is not in options {options!r}")
        return v


class BoolParam(ReportParameterBase):
    type: Literal["bool"] = "bool"
    default: bool | None = None


ReportParameterCreate = Annotated[
    Union[StringParam, NumberParam, DateParam, EnumParam, BoolParam],
    Field(discriminator="type"),
]


class ReportParameterResponse(BaseModel):
    """Schema returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    name: str
    label: str
    # Returned as plain string ("string"/"number"/...) via the str-enum.
    type: ParameterType
    required: bool
    default: Any | None = None
    options: list[str] | None = None
    order_index: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReportParameterUpdate(BaseModel):
    """All fields Optional — allow re-typing via the same enum.

    PUT is fully general: callers may rename, change ``label``, switch
    ``type``, or replace ``default``/``options``. Uniqueness on
    ``(report_id, name)`` is enforced server-side; a colliding rename
    returns 409.
    """

    name: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str | None = Field(default=None, min_length=1, max_length=255)
    required: bool | None = None
    default: Any | None = None
    options: list[str] | None = Field(default=None, min_length=1)
    order_index: int | None = Field(default=None, ge=0)
    type: ParameterType | None = None

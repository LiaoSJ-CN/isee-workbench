"""Pydantic schemas for :class:`app.models.report_subscription.ReportSubscription`
(批 8.3).

Three flavours:

* :class:`ReportSubscriptionCreate` — POST body. ``report_id`` is
  required on create; everything else has sensible defaults.
* :class:`ReportSubscriptionUpdate` — PATCH body. All fields optional;
  ``None``/absent means "don't change".
* :class:`ReportSubscriptionResponse` — Serialised ORM row. Includes
  bookkeeping timestamps (``last_run_at``, ``next_run_at``) so the
  UI can render "Last run: 3h ago" without a separate endpoint.

Notification config reuses the 批 6b discriminated union
(:class:`~app.schemas.notification.NotificationConfig`). Pydantic
validates the payload shape on the way in and (on the way out) only
insofar as ``model_validate_json`` accepts it; we expose it as a
generic ``dict`` on the response because the union is read-only on
``from_attributes``. The router uses :func:`NotificationConfig` on
the wire boundary — see :mod:`app.routers.subscription`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.notification import NotificationConfig


class ReportSubscriptionCreate(BaseModel):
    """Create payload for ``POST /subscriptions``."""

    model_config = ConfigDict(extra="forbid")

    report_id: int = Field(..., ge=1, description="Target report id.")
    cron_expression: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="6-field cron expression (min hour dom mon dow year).",
    )
    parameters: dict[str, Any] = Field(default_factory=dict)
    notification_config: NotificationConfig | None = None


class ReportSubscriptionUpdate(BaseModel):
    """PATCH payload for ``PATCH /subscriptions/{id}``.

    All fields optional. Cron and notification changes re-validate the
    same way as create; supplying ``is_active=False`` pauses without
    deleting the row.
    """

    model_config = ConfigDict(extra="forbid")

    cron_expression: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    parameters: dict[str, Any] | None = None
    notification_config: NotificationConfig | None = None
    is_active: bool | None = None


class ReportSubscriptionResponse(BaseModel):
    """Serialised :class:`ReportSubscription` row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int
    report_id: int
    cron_expression: str
    parameters: dict[str, Any] | None = None
    notification_config: dict[str, Any] | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None

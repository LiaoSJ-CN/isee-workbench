"""Pydantic schemas for :class:`app.models.dashboard_subscription.DashboardSubscription`
(批 14).

Mirrors :mod:`app.schemas.report_subscription` — same CRUD shape
(create / update / response), same cron-validation contract, same
``notification_config`` discriminated-union plumbing. The only
substantive difference is ``dashboard_id`` instead of ``report_id``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.notification import NotificationConfig


class DashboardSubscriptionCreate(BaseModel):
    """Create payload for ``POST /dashboard-subscriptions``."""

    model_config = ConfigDict(extra="forbid")

    dashboard_id: int = Field(..., ge=1, description="Target dashboard id.")
    cron_expression: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="6-field cron expression (min hour dom mon dow year).",
    )
    parameters: dict[str, Any] = Field(default_factory=dict)
    notification_config: NotificationConfig | None = None


class DashboardSubscriptionUpdate(BaseModel):
    """PATCH payload for ``PATCH /dashboard-subscriptions/{id}``.

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


class DashboardSubscriptionResponse(BaseModel):
    """Serialised :class:`DashboardSubscription` row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int
    dashboard_id: int
    cron_expression: str
    parameters: dict[str, Any] | None = None
    notification_config: dict[str, Any] | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    # Incremental-dedup fingerprint (批 14.4). NULL on first run;
    # the dispatcher compares against the freshly-computed fingerprint
    # to decide whether to render + send. Exposed so the UI can show
    # "上次变更" for power users, but most operators don't need to read it.
    last_fingerprint: str | None = None

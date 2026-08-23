"""HTTP endpoints for per-user report subscriptions (批 8.3).

Surface (all auth-gated; the sidecar scheduler runs the actual ticks):

* ``POST /subscriptions`` — create a new subscription for the current
  user. Validates ``cron_expression`` at the service layer; returns
  404 when the target report is gone.
* ``GET /subscriptions`` — list *my* subscriptions (owner-scoped at
  the SQL filter, never via the auth layer). Optional
  ``?report_id=N`` filter.
* ``GET /subscriptions/{id}`` — single-subscription lookup, owner-scoped.
* ``PATCH /subscriptions/{id}`` — partial update (cron, parameters,
  notification_config, is_active). Re-validates cron when changed.
* ``DELETE /subscriptions/{id}`` — hard delete + APScheduler prune.
* ``POST /subscriptions/{id}/pause`` and ``.../resume`` — convenience
  sugar over ``PATCH {is_active: ...}`` for callers that don't want
  to hand-roll the body.

Rate limiting: reuses the same budget as ``/reports/generate`` so a
single user can't spam subscription creation. Distinct key namespace
keeps the limiter state separate.
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session  # noqa: F401 — typing-only

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.user import User
from app.schemas.report_subscription import (
    ReportSubscriptionCreate,
    ReportSubscriptionResponse,
    ReportSubscriptionUpdate,
)
from app.services import audit as audit_service
from app.services.scheduler import InvalidCronExpression
from app.services.subscription import (
    create_subscription,
    delete_subscription,
    get_subscription,
    update_subscription,
)
from app.services.subscription import (
    list_my_subscriptions as list_my_subscriptions_service,
)

router = APIRouter(
    prefix="/subscriptions",
    tags=["subscriptions"],
    dependencies=[Depends(get_current_user)],
)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# Same budget as ``/reports/generate``; the rationale is identical —
# subscription creation is a small write but consumes scheduler slots
# at tick time, so the same client budget caps both.
_create_limiter = RateLimiter(
    max_requests=settings.reports_generate_rate_limit,
    window_seconds=60,
)


@router.post(
    "",
    response_model=ReportSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_my_subscription(
    payload: ReportSubscriptionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportSubscriptionResponse:
    """Create a subscription owned by the current user."""
    if _create_limiter.is_rate_limited(f"sub_create:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many subscription creates. Limit: "
                f"{settings.reports_generate_rate_limit}/min/IP."
            ),
            headers={"Retry-After": "60"},
        )

    try:
        sub = create_subscription(
            db=db,
            owner_user_id=cast(int, user.id),
            report_id=payload.report_id,
            cron_expression=payload.cron_expression,
            parameters=payload.parameters,
            notification_config=payload.notification_config,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InvalidCronExpression as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    # 批 9.5: audit successful create. ``before`` is None (no pre-image).
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_SUBSCRIPTION_CREATE,
        target_type=audit_service.TARGET_TYPE_REPORT_SUBSCRIPTION,
        target_id=cast(int, sub.id),
        before=None,
        after=sub,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return ReportSubscriptionResponse.model_validate(sub)


@router.get(
    "",
    response_model=list[ReportSubscriptionResponse],
)
def list_my_subscriptions(
    report_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReportSubscriptionResponse]:
    """List the current user's subscriptions, newest-first."""
    rows = list_my_subscriptions_service(
        db,
        cast(int, user.id),
        report_id=report_id,
        limit=limit,
        offset=offset,
    )
    return [ReportSubscriptionResponse.model_validate(r) for r in rows]


@router.get(
    "/{subscription_id}",
    response_model=ReportSubscriptionResponse,
)
def get_my_subscription(
    subscription_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportSubscriptionResponse:
    sub = get_subscription(db, subscription_id, cast(int, user.id))
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )
    return ReportSubscriptionResponse.model_validate(sub)


@router.patch(
    "/{subscription_id}",
    response_model=ReportSubscriptionResponse,
)
def update_my_subscription(
    subscription_id: int,
    payload: ReportSubscriptionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportSubscriptionResponse:
    sub = get_subscription(db, subscription_id, cast(int, user.id))
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )
    # 批 9.5: snapshot before mutation so the audit row carries a
    # before/after diff (cron / parameters / notification_config / active).
    before_snapshot = audit_service._snapshot(sub)
    try:
        updated = update_subscription(
            db,
            sub,
            cron_expression=payload.cron_expression,
            parameters=payload.parameters,
            notification_config=payload.notification_config,
            is_active=payload.is_active,
        )
    except InvalidCronExpression as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_SUBSCRIPTION_UPDATE,
        target_type=audit_service.TARGET_TYPE_REPORT_SUBSCRIPTION,
        target_id=cast(int, sub.id),
        before=before_snapshot,
        after=updated,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return ReportSubscriptionResponse.model_validate(updated)


@router.delete(
    "/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_my_subscription(
    subscription_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    sub = get_subscription(db, subscription_id, cast(int, user.id))
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )
    before_snapshot = audit_service._snapshot(sub)
    delete_subscription(db, sub)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_SUBSCRIPTION_DELETE,
        target_type=audit_service.TARGET_TYPE_REPORT_SUBSCRIPTION,
        target_id=subscription_id,
        before=before_snapshot,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return None


@router.post(
    "/{subscription_id}/pause",
    response_model=ReportSubscriptionResponse,
)
def pause_my_subscription(
    subscription_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportSubscriptionResponse:
    sub = get_subscription(db, subscription_id, cast(int, user.id))
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )
    # 批 9.5: snapshot the active=true state before flipping to false.
    before_snapshot = audit_service._snapshot(sub)
    updated = update_subscription(db, sub, is_active=False)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_SUBSCRIPTION_PAUSE,
        target_type=audit_service.TARGET_TYPE_REPORT_SUBSCRIPTION,
        target_id=cast(int, sub.id),
        before=before_snapshot,
        after=updated,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return ReportSubscriptionResponse.model_validate(updated)


@router.post(
    "/{subscription_id}/resume",
    response_model=ReportSubscriptionResponse,
)
def resume_my_subscription(
    subscription_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportSubscriptionResponse:
    sub = get_subscription(db, subscription_id, cast(int, user.id))
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )
    # 批 9.5: snapshot the active=false state before flipping to true.
    before_snapshot = audit_service._snapshot(sub)
    updated = update_subscription(db, sub, is_active=True)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_SUBSCRIPTION_RESUME,
        target_type=audit_service.TARGET_TYPE_REPORT_SUBSCRIPTION,
        target_id=cast(int, sub.id),
        before=before_snapshot,
        after=updated,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return ReportSubscriptionResponse.model_validate(updated)

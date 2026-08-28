"""HTTP endpoints for per-user dashboard subscriptions (批 14.2).

Mirrors :mod:`app.routers.subscription` — same CRUD surface, same
rate-limit budget, same owner-scoped lookups. Job-ID namespacing is
``dsub_<id>`` (vs ``sub_<id>`` for reports) so the sidecar scheduler
keeps the two streams separate.

The endpoint surface is wired in sub-batch 14.2; the dispatch logic
(incremental dedup + render + send) lands in sub-batch 14.4. For
now, the cron tick fires :func:`_execute_dashboard_subscription`
which only updates ``last_run_at``.
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
from app.schemas.dashboard_subscription import (
    DashboardSubscriptionCreate,
    DashboardSubscriptionResponse,
    DashboardSubscriptionUpdate,
)
from app.services import audit as audit_service
from app.services.dashboard_subscription import (
    InvalidCronExpression,
    create_subscription,
    delete_subscription,
    get_subscription,
    list_my_subscriptions,
    update_subscription,
)

router = APIRouter(
    prefix="/dashboard-subscriptions",
    tags=["dashboard-subscriptions"],
    dependencies=[Depends(get_current_user)],
)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# Same budget as the report subscription create — the rationale is
# identical: subscription creation is a small write but consumes
# scheduler slots at tick time, so the same client budget caps both.
_create_limiter = RateLimiter(
    max_requests=settings.reports_generate_rate_limit,
    window_seconds=60,
)


@router.post(
    "",
    response_model=DashboardSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_my_dashboard_subscription(
    payload: DashboardSubscriptionCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DashboardSubscriptionResponse:
    """Create a dashboard subscription owned by the current user."""
    if _create_limiter.is_rate_limited(
        f"dash_sub_create:{_client_ip(request)}"
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many dashboard subscription creates. Limit: "
                f"{settings.reports_generate_rate_limit}/min/IP."
            ),
            headers={"Retry-After": "60"},
        )

    try:
        sub = create_subscription(
            db=db,
            owner_user_id=cast(int, user.id),
            dashboard_id=payload.dashboard_id,
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

    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_SUBSCRIPTION_CREATE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD_SUBSCRIPTION,
        target_id=cast(int, sub.id),
        before=None,
        after=sub,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return DashboardSubscriptionResponse.model_validate(sub)


@router.get(
    "",
    response_model=list[DashboardSubscriptionResponse],
)
def list_my_dashboard_subscriptions(
    dashboard_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DashboardSubscriptionResponse]:
    """List the current user's dashboard subscriptions, newest-first."""
    rows = list_my_subscriptions(
        db,
        cast(int, user.id),
        dashboard_id=dashboard_id,
        limit=limit,
        offset=offset,
    )
    return [DashboardSubscriptionResponse.model_validate(r) for r in rows]


@router.get(
    "/{subscription_id}",
    response_model=DashboardSubscriptionResponse,
)
def get_my_dashboard_subscription(
    subscription_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DashboardSubscriptionResponse:
    sub = get_subscription(db, subscription_id, cast(int, user.id))
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard subscription not found",
        )
    return DashboardSubscriptionResponse.model_validate(sub)


@router.patch(
    "/{subscription_id}",
    response_model=DashboardSubscriptionResponse,
)
def update_my_dashboard_subscription(
    subscription_id: int,
    payload: DashboardSubscriptionUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DashboardSubscriptionResponse:
    sub = get_subscription(db, subscription_id, cast(int, user.id))
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard subscription not found",
        )
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
        target_type=audit_service.TARGET_TYPE_DASHBOARD_SUBSCRIPTION,
        target_id=cast(int, sub.id),
        before=before_snapshot,
        after=updated,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return DashboardSubscriptionResponse.model_validate(updated)


@router.delete(
    "/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_my_dashboard_subscription(
    subscription_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    sub = get_subscription(db, subscription_id, cast(int, user.id))
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard subscription not found",
        )
    before_snapshot = audit_service._snapshot(sub)
    delete_subscription(db, sub)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_SUBSCRIPTION_DELETE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD_SUBSCRIPTION,
        target_id=subscription_id,
        before=before_snapshot,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return None


@router.post(
    "/{subscription_id}/pause",
    response_model=DashboardSubscriptionResponse,
)
def pause_my_dashboard_subscription(
    subscription_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DashboardSubscriptionResponse:
    sub = get_subscription(db, subscription_id, cast(int, user.id))
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard subscription not found",
        )
    before_snapshot = audit_service._snapshot(sub)
    updated = update_subscription(db, sub, is_active=False)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_SUBSCRIPTION_PAUSE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD_SUBSCRIPTION,
        target_id=cast(int, sub.id),
        before=before_snapshot,
        after=updated,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return DashboardSubscriptionResponse.model_validate(updated)


@router.post(
    "/{subscription_id}/resume",
    response_model=DashboardSubscriptionResponse,
)
def resume_my_dashboard_subscription(
    subscription_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DashboardSubscriptionResponse:
    sub = get_subscription(db, subscription_id, cast(int, user.id))
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard subscription not found",
        )
    before_snapshot = audit_service._snapshot(sub)
    updated = update_subscription(db, sub, is_active=True)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_SUBSCRIPTION_RESUME,
        target_type=audit_service.TARGET_TYPE_DASHBOARD_SUBSCRIPTION,
        target_id=cast(int, sub.id),
        before=before_snapshot,
        after=updated,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return DashboardSubscriptionResponse.model_validate(updated)

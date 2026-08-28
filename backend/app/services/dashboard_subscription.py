"""Per-user Dashboard subscription CRUD + scheduler stub (批 14.2).

Mirrors :mod:`app.services.subscription` so the sidecar scheduler can
reuse the same APScheduler instance — job IDs are namespaced
(``dsub_<id>`` vs ``sub_<id>``) so the two streams don't collide.

**Sub-batch 2 scope** — this module ships the CRUD endpoints + the
reconciliation hook + the APScheduler stub. The actual *dispatch*
logic (incremental dedup + render + send) lands in **sub-batch 14.4**
where ``dispatch_dashboard_subscription`` is implemented. For now,
``_execute_dashboard_subscription`` is a stub that updates
``last_run_at`` so the cron tick lifecycle is testable end-to-end.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlalchemy.orm import Session

from app.models.dashboard import Dashboard
from app.models.dashboard_subscription import DashboardSubscription
from app.schemas.notification import NotificationConfig
from app.services.scheduler import (
    InvalidCronExpression,
    get_scheduler,
    validate_cron_expression,
)

logger = logging.getLogger(__name__)


def _job_id(subscription_id: int) -> str:
    """APScheduler job id namespace for dashboard subscriptions.

    Distinct from :func:`app.services.subscription._job_id` so the
    reconciler can walk both streams without cross-stream collisions.
    """
    return f"dsub_{subscription_id}"


# ---------------------------------------------------------------------------
# CRUD (mirrors app.services.subscription.create_subscription)
# ---------------------------------------------------------------------------


def create_subscription(
    db: Session,
    *,
    owner_user_id: int,
    dashboard_id: int,
    cron_expression: str,
    parameters: dict[str, Any] | None,
    notification_config: NotificationConfig | None,
) -> DashboardSubscription:
    """Persist a new dashboard subscription and register it with
    APScheduler.

    ``cron_expression`` is validated up-front so an invalid row never
    reaches the DB. ``dashboard_id`` must reference an existing
    dashboard — otherwise the worker tick will fail later with a less
    helpful error.
    """
    validate_cron_expression(cron_expression)

    dashboard = (
        db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
    )
    if dashboard is None:
        raise LookupError(f"Dashboard {dashboard_id} not found")

    sub = DashboardSubscription(
        owner_user_id=owner_user_id,
        dashboard_id=dashboard_id,
        cron_expression=cron_expression,
        parameters=parameters or {},
        notification_config=(
            notification_config.model_dump(mode="json")
            if notification_config is not None
            else None
        ),
        is_active=True,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    # Persisted → schedule. Failures here lose the tick but leave the
    # row — operator can re-schedule from the UI by re-saving.
    _schedule_subscription(sub)
    return sub


def list_my_subscriptions(
    db: Session,
    owner_user_id: int,
    *,
    dashboard_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DashboardSubscription]:
    """Owner-scoped subscription listing, most-recent-first."""
    q = db.query(DashboardSubscription).filter(
        DashboardSubscription.owner_user_id == owner_user_id
    )
    if dashboard_id is not None:
        q = q.filter(DashboardSubscription.dashboard_id == dashboard_id)
    return (
        q.order_by(
            DashboardSubscription.updated_at.desc().nullslast(),
            DashboardSubscription.id.desc(),
        )
        .limit(limit)
        .offset(offset)
        .all()
    )


def get_subscription(
    db: Session,
    subscription_id: int,
    owner_user_id: int,
) -> DashboardSubscription | None:
    """Owner-scoped single lookup. ``None`` for any 404 case
    (not-found, wrong owner) so the router can return the same 404
    for both."""
    return (
        db.query(DashboardSubscription)
        .filter(
            DashboardSubscription.id == subscription_id,
            DashboardSubscription.owner_user_id == owner_user_id,
        )
        .first()
    )


def update_subscription(
    db: Session,
    subscription: DashboardSubscription,
    *,
    cron_expression: str | None = None,
    parameters: dict[str, Any] | None = None,
    notification_config: NotificationConfig | None = None,
    is_active: bool | None = None,
) -> DashboardSubscription:
    """Apply a partial update. Re-validates cron when it changes so a
    bad update rejects at the service layer (vs leaving a broken
    row that 422s on every subsequent tick)."""
    if (
        cron_expression is not None
        and cron_expression != subscription.cron_expression
    ):
        validate_cron_expression(cron_expression)
        subscription.cron_expression = cron_expression

    if parameters is not None:
        subscription.parameters = parameters

    if notification_config is not None:
        subscription.notification_config = notification_config.model_dump(
            mode="json"
        )

    if is_active is not None:
        subscription.is_active = is_active

    db.commit()
    db.refresh(subscription)

    if subscription.is_active:
        _schedule_subscription(subscription)
    else:
        _unschedule_subscription(subscription)
    return subscription


def delete_subscription(
    db: Session,
    subscription: DashboardSubscription,
) -> None:
    """Hard-delete + APScheduler prune. ``DELETE`` is destructive by
    design — pausing (``is_active=False``) is the non-destructive
    alternative."""
    sid = cast(int, subscription.id)
    _unschedule_subscription(subscription)
    db.delete(subscription)
    db.commit()
    _ = sid  # silence unused warning if the helper is extended


# ---------------------------------------------------------------------------
# Scheduler integration
# ---------------------------------------------------------------------------


def _schedule_subscription(sub: DashboardSubscription) -> None:
    """Add or replace the APScheduler job for *sub*.

    Same idempotent semantics as :func:`app.services.subscription._schedule_subscription`
    — APScheduler's ``add_job(..., replace_existing=True)`` means a
    re-save on the same row overwrites cleanly.
    """
    parts = cast(str, sub.cron_expression).split()
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger(
        minute=parts[0],
        hour=parts[1],
        day=parts[2],
        month=parts[3],
        day_of_week=parts[4],
        year=parts[5],
    )
    scheduler = get_scheduler()
    job_id = _job_id(cast(int, sub.id))
    scheduler.scheduler.add_job(
        func=_execute_dashboard_subscription,
        trigger=trigger,
        id=job_id,
        args=[cast(int, sub.id)],
        replace_existing=True,
    )
    logger.info(
        "Scheduled dashboard subscription job %s (dashboard_id=%s, owner=%s)",
        job_id,
        sub.dashboard_id,
        sub.owner_user_id,
    )


def _unschedule_subscription(sub: DashboardSubscription) -> None:
    """Drop the APScheduler job if present; no-op when missing."""
    scheduler = get_scheduler()
    job_id = _job_id(cast(int, sub.id))
    if scheduler.scheduler.get_job(job_id):
        scheduler.scheduler.remove_job(job_id)
        logger.info(
            "Unscheduled dashboard subscription job %s (dashboard_id=%s)",
            job_id,
            sub.dashboard_id,
        )


def sync_dashboard_subscriptions_with_database(db: Session) -> None:
    """Reconcile APScheduler with the database (sidecar bootstrap).

    Mirrors :func:`app.services.subscription.sync_subscriptions_with_database`
    — walks every active subscription, ensures the matching APScheduler
    job exists, and prunes orphans. Called from the sidecar on a
    periodic tick (``SCHEDULER_RESYNC_INTERVAL``).
    """
    subs = (
        db.query(DashboardSubscription)
        .filter(DashboardSubscription.is_active.is_(True))
        .all()
    )
    scheduler = get_scheduler()
    expected_ids = set()
    for sub in subs:
        expected_ids.add(_job_id(cast(int, sub.id)))
        _schedule_subscription(sub)

    # Prune APScheduler jobs for dashboard subscriptions that are no
    # longer active (deleted, paused, or the row is gone). The job
    # id prefix is the namespace — keep the prune narrow so report
    # subscription jobs are untouched.
    for job in scheduler.scheduler.get_jobs():
        if job.id.startswith("dsub_") and job.id not in expected_ids:
            scheduler.scheduler.remove_job(job.id)
            logger.info("Pruned orphan dashboard subscription job %s", job.id)


def _execute_dashboard_subscription(subscription_id: int) -> None:
    """APScheduler entry point — stub for sub-batch 2.

    The real implementation lands in **sub-batch 14.4** with the
    incremental dedup logic + render + send. For now we only update
    ``last_run_at`` so the cron lifecycle is testable end-to-end and
    ``sync_dashboard_subscriptions_with_database`` can verify the
    job fires without doing actual work.
    """
    from datetime import datetime, timezone

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        sub = db.get(DashboardSubscription, subscription_id)
        if sub is None or not sub.is_active:
            return
        sub.last_run_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            "Dashboard subscription %s fired (stub — sub-batch 14.4 "
            "wires the dispatch logic)",
            subscription_id,
        )
    finally:
        db.close()


__all__ = [
    "InvalidCronExpression",
    "create_subscription",
    "delete_subscription",
    "get_subscription",
    "list_my_subscriptions",
    "sync_dashboard_subscriptions_with_database",
    "update_subscription",
]

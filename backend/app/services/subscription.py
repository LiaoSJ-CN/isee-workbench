"""Per-user report subscription service (批 8.3).

This module owns CRUD for :class:`app.models.report_subscription.ReportSubscription`
plus the bridge to APScheduler. The shape mirrors the existing
:mod:`app.services.scheduler` (wholesale report scheduling) but with
two key differences:

* **Per-user ownership**: every list/get/update/delete filters by
  ``owner_user_id``. Operators can't read another user's rows from
  this module — that's a router concern that the helper returns
  ``None`` for unauthorized lookups so the router can 404.
* **Independent notification config**: a subscription's notification
  destination is its own, not inherited from the report. This is the
  whole point — one report, N subscribers, N destinations.

APScheduler job IDs are namespaced (``sub_<id>`` vs ``report_<id>``)
so ``sync_with_database`` (report-level) and
``sync_subscriptions_with_database`` (subscription-level) can each
prune only their own stream without stepping on each other.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.report import Report
from app.models.report_subscription import ReportSubscription
from app.schemas.notification import NotificationConfig
from app.services.report_generator import generate_report
from app.services.scheduler import (
    InvalidCronExpression,
    _send_notification,
    get_scheduler,
    validate_cron_expression,
)

logger = logging.getLogger(__name__)


# Job id namespace — must differ from ``report_<id>`` in scheduler.py
# so the two reconcilers don't trip on each other's rows.
def _job_id(subscription_id: int) -> str:
    return f"sub_{subscription_id}"


# ----------------------------------------------------------------------
# CRUD
# ----------------------------------------------------------------------


def create_subscription(
    db: Session,
    *,
    owner_user_id: int,
    report_id: int,
    cron_expression: str,
    parameters: dict[str, Any] | None,
    notification_config: NotificationConfig | None,
) -> ReportSubscription:
    """Persist a new subscription and register it with APScheduler.

    ``cron_expression`` is validated up-front so an invalid row never
    reaches the DB. ``report_id`` must reference an existing report —
    otherwise the worker tick will fail later with a less helpful
    error.
    """
    validate_cron_expression(cron_expression)

    report = db.query(Report).filter(Report.id == report_id).first()
    if report is None:
        raise LookupError(f"Report {report_id} not found")

    sub = ReportSubscription(
        owner_user_id=owner_user_id,
        report_id=report_id,
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
    report_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ReportSubscription]:
    """Owner-scoped subscription listing, most-recent-first.

    ``report_id`` is an optional filter — when present, narrows to
    subscriptions on a specific report (handy for the "this report's
    subscribers" page if we ever expose one).
    """
    q = db.query(ReportSubscription).filter(
        ReportSubscription.owner_user_id == owner_user_id
    )
    if report_id is not None:
        q = q.filter(ReportSubscription.report_id == report_id)
    return (
        q.order_by(
            ReportSubscription.updated_at.desc().nullslast(),
            ReportSubscription.id.desc(),
        )
        .limit(limit)
        .offset(offset)
        .all()
    )


def get_subscription(
    db: Session,
    subscription_id: int,
    owner_user_id: int,
) -> ReportSubscription | None:
    """Owner-scoped single lookup. ``None`` for any 404 case
    (not-found, wrong owner) so the router can return the same 404
    for both."""
    return (
        db.query(ReportSubscription)
        .filter(
            ReportSubscription.id == subscription_id,
            ReportSubscription.owner_user_id == owner_user_id,
        )
        .first()
    )


def update_subscription(
    db: Session,
    subscription: ReportSubscription,
    *,
    cron_expression: str | None = None,
    parameters: dict[str, Any] | None = None,
    notification_config: NotificationConfig | None = None,
    is_active: bool | None = None,
) -> ReportSubscription:
    """Apply a partial update. Re-validates cron when it changes so a
    bad update rejects at the service layer (vs leaving a broken
    row that 422s on every subsequent tick)."""
    if cron_expression is not None and cron_expression != subscription.cron_expression:
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

    # Reschedule — covers all four change types. Simpler than tracking
    # which field changed; APScheduler ``add_job(..., replace_existing=True)``
    # is idempotent.
    if subscription.is_active:
        _schedule_subscription(subscription)
    else:
        _unschedule_subscription(subscription)
    return subscription


def delete_subscription(
    db: Session,
    subscription: ReportSubscription,
) -> None:
    """Hard-delete + APScheduler prune. ``DELETE /subscriptions/{id}``
    is destructive by design — pausing (``is_active=False``) is the
    non-destructive alternative."""
    sid = cast(int, subscription.id)
    _unschedule_subscription(subscription)
    db.delete(subscription)
    db.commit()
    # ``sid`` only used to silence ``unused`` warnings if we extend
    # the helper later; explicit reference keeps the lint happy.
    _ = sid


# ----------------------------------------------------------------------
# Scheduler integration
# ----------------------------------------------------------------------


def _schedule_subscription(sub: ReportSubscription) -> None:
    """Add or replace the APScheduler job for *sub*.

    Picked up by the sidecar reconciler; called directly on create /
    update so single-process dev mode (where the reconciler may not
    be running yet) works end to end.
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
        func=_execute_subscription,
        trigger=trigger,
        id=job_id,
        args=[cast(int, sub.id)],
        replace_existing=True,
    )
    logger.info(
        "Scheduled subscription job %s (report_id=%s, owner=%s)",
        job_id, sub.report_id, sub.owner_user_id,
    )


def _unschedule_subscription(sub: ReportSubscription) -> None:
    """Drop the APScheduler job if present; no-op when missing."""
    scheduler = get_scheduler()
    job_id = _job_id(cast(int, sub.id))
    if scheduler.scheduler.get_job(job_id):
        scheduler.scheduler.remove_job(job_id)
        logger.info(
            "Unscheduled subscription job %s (report_id=%s)",
            job_id, sub.report_id,
        )


def sync_subscriptions_with_database(db: Session) -> None:
    """Idempotent reconciler — same contract as
    :func:`app.services.scheduler.sync_with_database` but operating on
    the ``sub_<id>`` job-id namespace.

    Adds/updates jobs for every active subscription; removes jobs
    whose DB row no longer qualifies (paused or hard-deleted). Safe
    to call periodically from the sidecar.
    """
    db.expire_all()

    active_subs = (
        db.query(ReportSubscription)
        .filter(ReportSubscription.is_active == True)  # noqa: E712
        .all()
    )
    active_ids: set[int] = set()
    for sub in active_subs:
        try:
            _schedule_subscription(sub)
            active_ids.add(cast(int, sub.id))
        except InvalidCronExpression as exc:
            logger.error(
                "Subscription %s has invalid cron %r: %s",
                sub.id, sub.cron_expression, exc,
            )
        except Exception as exc:  # noqa: BLE001 — top-level guard for the reconciler
            logger.exception(
                "Failed to schedule subscription %s: %s", sub.id, exc
            )

    # Prune — drop sub_<id> jobs whose row is no longer active.
    scheduler = get_scheduler()
    for job in scheduler.scheduler.get_jobs():
        job_id = job.id
        if not job_id or not job_id.startswith("sub_"):
            continue
        try:
            sid = int(job_id[len("sub_"):])
        except ValueError:
            continue
        if sid not in active_ids:
            scheduler.scheduler.remove_job(job_id)
            logger.info("Removed orphan subscription job %s", job_id)


# ----------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------


def _execute_subscription(subscription_id: int) -> None:
    """APScheduler callback: drive the subscription through one tick.

    Mirrors :func:`app.services.scheduler._execute_scheduled_report`
    but uses the subscription's own notification config and
    parameters. Opens its own DB session — the request thread's
    session is closed by the time we run.
    """
    db = SessionLocal()
    try:
        sub = db.get(ReportSubscription, subscription_id)
        if sub is None:
            logger.error(
                "Subscription %s disappeared before execution",
                subscription_id,
            )
            return
        if not sub.is_active:
            logger.info(
                "Subscription %s is inactive, skipping", subscription_id
            )
            return

        report = db.get(Report, cast(int, sub.report_id))
        if report is None:
            logger.error(
                "Report %s for subscription %s no longer exists",
                sub.report_id, subscription_id,
            )
            return
        if not report.is_active:
            logger.info(
                "Report %s for subscription %s is inactive, skipping",
                report.id, subscription_id,
            )
            return

        # Generate a single Excel snapshot per subscription tick —
        # the operator picks ``output_formats`` on the report; we
        # use ``excel`` because the email channel is the typical
        # consumer for subscriptions. Multiple-format runs would
        # force the recipient's webhooks to handle N attachments
        # per tick; defer to a future ``output_format`` column if
        # demand appears.
        result = generate_report(
            report=report,
            output_format="excel",
            parameters=cast(dict[str, Any], sub.parameters or {}),
            db=db,
        )
        file_path = result.get("file_path") if isinstance(result, dict) else None
        if file_path and sub.notification_config:
            # notification_config is stored dict-shaped; rehydrate to
            # the typed union before dispatch.
            from pydantic import TypeAdapter

            _adapter: TypeAdapter[NotificationConfig] = TypeAdapter(
                NotificationConfig
            )
            typed: NotificationConfig = _adapter.validate_python(
                sub.notification_config
            )
            _send_notification(
                typed,
                report,
                [cast(str, file_path)],
            )
        elif file_path:
            logger.info(
                "Subscription %s produced %s but no notification_config — "
                "file written but not delivered",
                subscription_id, file_path,
            )

        sub.last_run_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            "Completed subscription %s tick (report_id=%s)",
            subscription_id, report.id,
        )
    except Exception as exc:  # noqa: BLE001 — top-level guard for APScheduler thread
        logger.exception(
            "Subscription %s tick crashed: %s", subscription_id, exc
        )
    finally:
        db.close()

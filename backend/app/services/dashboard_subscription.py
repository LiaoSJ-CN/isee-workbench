"""Per-user Dashboard subscription CRUD + scheduler dispatch (批 14.4).

Mirrors :mod:`app.services.subscription` so the sidecar scheduler can
reuse the same APScheduler instance — job IDs are namespaced
(``dsub_<id>`` vs ``sub_<id>``) so the two streams don't collide.

**Sub-batch 4 scope** — wires the cron tick to the actual dispatch
pipeline:

1. Compute a per-item **fingerprint** (MD5 hex). Items contribute
   different signals:
   * ``report`` → ``r<id>:<report.updated_at>`` (cheap, no query)
   * ``chart`` → ``c<id>:<rows-hash>`` (runs the chart SQL once per tick)
   * ``text`` → not part of the fingerprint (static text doesn't drive
     "did anything change")
2. Compare against the subscription's stored ``last_fingerprint``;
   identical → stamp ``last_run_at`` and skip delivery.
3. On change, render the dashboard via
   :func:`app.services.dashboard.render_dashboard_html`, write the
   HTML under ``settings.generated_reports_dir``, then dispatch via
   the existing :func:`app.services.scheduler._send_notification`
   union (webhook / 钉钉 / 飞书 / 企微 / email).

The senders were written for ``Report`` but only read ``.id`` /
``.name``; we wrap the dashboard in a small structural shim so the
existing sender pipeline is reused unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy.orm import Session

from app.config import settings
from app.models.dashboard import Dashboard
from app.models.dashboard_subscription import DashboardSubscription
from app.schemas.notification import NotificationConfig
from app.services.scheduler import (
    InvalidCronExpression,
    _send_notification,
    get_scheduler,
    validate_cron_expression,
)

logger = logging.getLogger(__name__)

# Dashboard names flow into filenames — keep only path-safe chars so
# we don't have to re-sanitise on the way to disk.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


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
    """APScheduler entry point — drives one cron tick.

    Incremental-dedup loop:

    1. Load the subscription; bail on missing or paused.
    2. Compute the dashboard fingerprint via
       :func:`_compute_dashboard_fingerprint`. If unchanged since the
       last tick (``last_fingerprint`` matches), stamp ``last_run_at``
       and skip the network round-trip.
    3. Render the dashboard HTML, write it under
       ``settings.generated_reports_dir``, and dispatch through the
       shared :func:`app.services.scheduler._send_notification` union.
    4. Persist the new fingerprint + ``last_run_at``.

    The whole tick is wrapped in a top-level ``except`` so an APScheduler
    thread crash doesn't take down the sidecar — same contract as
    :func:`app.services.subscription._execute_subscription`.
    """
    from app.database import SessionLocal
    from app.models.user import User

    db = SessionLocal()
    try:
        sub = db.get(DashboardSubscription, subscription_id)
        if sub is None or not sub.is_active:
            return

        dashboard = db.get(Dashboard, cast(int, sub.dashboard_id))
        if dashboard is None:
            logger.error(
                "Dashboard %s for subscription %s no longer exists",
                sub.dashboard_id,
                subscription_id,
            )
            return

        # Lazy import — service→service dependency is fine, but keep
        # the dispatcher module light when only CRUD is exercised.
        from app.services.dashboard import render_dashboard_html

        fingerprint = _compute_dashboard_fingerprint(db, dashboard)
        previous = sub.last_fingerprint
        if previous is not None and previous == fingerprint:
            sub.last_run_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(
                "Dashboard subscription %s tick: fingerprint unchanged, "
                "skipping send",
                subscription_id,
            )
            return

        # First run or fingerprint changed → render + write + send.
        owner = db.get(User, cast(int, sub.owner_user_id))
        if owner is None:
            logger.error(
                "Owner user %s for subscription %s missing — skipping send",
                sub.owner_user_id,
                subscription_id,
            )
            sub.last_run_at = datetime.now(timezone.utc)
            db.commit()
            return

        rendered = render_dashboard_html(db, dashboard, owner)
        file_path = _write_dashboard_html(dashboard, rendered["html"])
        logger.info(
            "Dashboard %s rendered: %d items ok, %d failed",
            dashboard.id,
            rendered["items_rendered"],
            rendered["items_failed"],
        )

        if sub.notification_config:
            from pydantic import TypeAdapter

            adapter: TypeAdapter[NotificationConfig] = TypeAdapter(
                NotificationConfig
            )
            typed: NotificationConfig = adapter.validate_python(
                sub.notification_config
            )
            _send_notification(
                typed,
                _dashboard_sender_shim(dashboard),
                [file_path],
            )
        else:
            logger.info(
                "Dashboard subscription %s produced %s but no "
                "notification_config — file written, not delivered",
                subscription_id,
                file_path,
            )

        sub.last_run_at = datetime.now(timezone.utc)
        sub.last_fingerprint = fingerprint
        db.commit()
        logger.info(
            "Dashboard subscription %s tick complete (sent=%s)",
            subscription_id,
            bool(sub.notification_config),
        )
    except Exception as exc:  # noqa: BLE001 — top-level guard
        logger.exception(
            "Dashboard subscription %s tick crashed: %s",
            subscription_id,
            exc,
        )
    finally:
        db.close()


def _compute_dashboard_fingerprint(
    db: Session, dashboard: Dashboard
) -> str:
    """Hash the parts of *dashboard* that drive a "did anything
    change?" decision.

    Per-item tokens:

    * ``report`` — ``f"r<id>:<report.updated_at.isoformat()>"``. We
      rely on the ORM bumping ``updated_at`` whenever the report
      definition mutates; an unchanged token → no send.
    * ``chart`` — ``f"c<id>:<md5(rows)>"``. Requires one SQL execution
      per chart item; that's the cost of "did the data move?" — no
      cheaper signal exists when the underlying DB can churn under us.
    * ``text`` — intentionally omitted. Static text doesn't make a
      dashboard "newsworthy"; the operator wants notifications about
      *data*, not about edits to a greeting banner.

    Items are sorted by id so reordering grid cells doesn't perturb
    the hash.

    Returns a 32-char hex MD5 digest.
    """
    tokens: list[str] = []
    sorted_items = sorted(
        dashboard.items,
        key=lambda it: int(it.id) if it.id is not None else 0,
    )
    for item in sorted_items:
        item_type = item.item_type or ""
        item_id = int(item.id) if item.id is not None else 0
        if item_type == "report":
            from app.models.report import Report

            rid = item.report_id
            if rid is None:
                # Report item with no linked report — token reflects
                # that emptiness so un-linking still triggers a send.
                tokens.append(f"r{item_id}:<none>")
                continue
            report = db.get(Report, int(rid))
            updated = (
                report.updated_at.isoformat()
                if report is not None and report.updated_at is not None
                else "<none>"
            )
            tokens.append(f"r{item_id}:{updated}")
        elif item_type == "chart":
            from app.services.dashboard import execute_dashboard_chart

            try:
                data = execute_dashboard_chart(
                    db, item, _system_user(db)
                )
                rows_blob = json.dumps(
                    data["rows"], sort_keys=True, default=str
                )
                row_hash = hashlib.md5(rows_blob.encode("utf-8")).hexdigest()
            except Exception as exc:  # noqa: BLE001
                # SQL error / DS gate failure / etc — treat the chart
                # as "changed" so the dispatcher sends and the operator
                # can see the inline error.
                logger.warning(
                    "Fingerprint chart exec failed for item %s: %s",
                    item_id,
                    exc,
                )
                row_hash = f"err:{exc!r}"
            tokens.append(f"c{item_id}:{row_hash}")
        # text: skipped on purpose
    payload = "\n".join(tokens)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _system_user(db: Session) -> Any:
    """Return an admin-flavored user for the chart-execute path.

    The chart executor needs *some* :class:`User` to satisfy the
    data-source ACL gate. We use the first admin so the gate
    short-circuits without per-user ACL bookkeeping — the dispatcher
    is acting on behalf of the subscription owner who already passed
    the visibility checks when the row was created.
    """
    from app.models.user import User

    user = db.query(User).filter(User.role == "admin").first()
    if user is not None:
        return user
    # Fallback — any user. The fingerprint path tolerates failures
    # (treated as "changed"); we just need something to pass the type.
    return db.query(User).first()


def _dashboard_sender_shim(dashboard: Dashboard) -> Any:
    """Tiny structural shim so the existing notification senders
    accept a dashboard.

    The senders (:func:`_send_webhook`, :func:`_send_feishu`,
    :func:`_send_wechatwork`, :func:`_send_email`) only read
    ``.id`` and ``.name`` off the second positional arg. ``SimpleNamespace``
    exposes both attributes without inheriting any of :class:`Report`'s
    schema — the dispatcher doesn't want to fake a report row just to
    borrow a sender interface.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        id=int(dashboard.id) if dashboard.id is not None else 0,
        name=str(dashboard.name or ""),
    )


def _write_dashboard_html(dashboard: Dashboard, html: str) -> str:
    """Persist the rendered HTML under ``settings.generated_reports_dir``.

    Filename shape mirrors the report generator (``<safe>_<ts>_<rand>.html``)
    so cleanup scripts that glob the directory keep working without a
    new branch.
    """
    import secrets

    out_dir = settings.generated_reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _SAFE_NAME_RE.sub("_", str(dashboard.name))[:80] or "dashboard"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rand = secrets.token_hex(4)
    out_path = out_dir / f"{safe_name}_{timestamp}_{rand}.html"
    out_path.write_text(html, encoding="utf-8")
    return str(out_path)


__all__ = [
    "InvalidCronExpression",
    "create_subscription",
    "delete_subscription",
    "get_subscription",
    "list_my_subscriptions",
    "sync_dashboard_subscriptions_with_database",
    "update_subscription",
]

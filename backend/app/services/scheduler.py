"""Scheduled task service for automatic report generation."""

import base64
import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from typing import Any, cast

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.middleware.metrics import webhook_delivery_attempts_total
from app.models.report import Report
from app.schemas.notification import (
    DingTalkConfig,
    EmailConfig,
    FeishuConfig,
    NotificationConfig,
    WebhookConfig,
    WeChatWorkConfig,
)
from app.services.report_generator import generate_report
from app.services.ssrf_guard import SSRFBlocked, create_webhook_client, validate_webhook_url

logger = logging.getLogger(__name__)


class InvalidCronExpression(ValueError):
    """Raised when a cron expression fails validation.

    Extends ValueError for backward compatibility — callers that catch
    ValueError will still handle this. Distinct type allows targeted
    handling (e.g. 400 in HTTP vs 500 for other ValueErrors)."""


def validate_cron_expression(expression: str) -> None:
    """Validate a 6-field cron expression (min hour dom mon dow year).

    Raises InvalidCronExpression if the expression is malformed.
    Safe to call from both Pydantic validators and service-layer code.
    """
    parts = expression.split()
    if len(parts) != 6:
        raise InvalidCronExpression(
            "Cron expression must have 6 fields: min hour dom mon dow year"
        )
    try:
        CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            year=parts[5],
        )
    except (ValueError, TypeError) as exc:
        raise InvalidCronExpression(f"Invalid cron expression: {exc}") from exc


class ReportScheduler:
    """Manages scheduled report generation tasks."""

    def __init__(self) -> None:
        """Initialize the scheduler."""
        self.scheduler = BackgroundScheduler()
        self._is_running = False

    def start(self) -> None:
        """Start the scheduler."""
        if not self._is_running:
            self.scheduler.start()
            self._is_running = True
            logger.info("Report scheduler started")

    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        if self._is_running:
            self.scheduler.shutdown(wait=False)
            self._is_running = False
            logger.info("Report scheduler shutdown")

    def add_report_job(
        self,
        report_id: int,
        cron_expression: str,
        notification_config: NotificationConfig | None = None,
    ) -> str:
        """Add or update a scheduled job for a report.

        Args:
            report_id: The report ID to schedule
            cron_expression: 6-field cron expression (min hour dom mon dow year)
            notification_config: Typed notification config (Webhook/Email/DingTalk)

        Returns:
            The job ID

        Raises:
            InvalidCronExpression: if cron_expression is malformed.
        """
        job_id = f"report_{report_id}"

        validate_cron_expression(cron_expression)
        parts = cron_expression.split()
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            year=parts[5],
        )

        # Remove existing job if present
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        self.scheduler.add_job(
            func=_execute_scheduled_report,
            trigger=trigger,
            id=job_id,
            args=[report_id, notification_config],
            replace_existing=True,
        )

        logger.info(f"Added scheduled job {job_id} with cron: {cron_expression}")
        return job_id

    def remove_report_job(self, report_id: int) -> bool:
        """Remove a scheduled job for a report."""
        job_id = f"report_{report_id}"
        job = self.scheduler.get_job(job_id)
        if job:
            self.scheduler.remove_job(job_id)
            logger.info(f"Removed scheduled job {job_id}")
            return True
        return False

    @staticmethod
    def _format_next_run(job: Any) -> str | None:
        """Format a job's next_run_time as ISO string, or None."""
        next_run = getattr(job, "next_run_time", None)
        return next_run.isoformat() if next_run else None

    def get_job_status(self, report_id: int) -> dict[str, Any] | None:
        """Get the status of a scheduled job."""
        job_id = f"report_{report_id}"
        job = self.scheduler.get_job(job_id)
        if not job:
            return None

        return {
            "job_id": job.id,
            "next_run": self._format_next_run(job),
            "trigger": str(job.trigger),
        }

    def get_status(self) -> dict[str, Any]:
        """Return the current scheduler status without exposing internals."""
        return {
            "is_running": self.scheduler.running,
            "jobs": [
                {
                    "job_id": job.id,
                    "next_run": self._format_next_run(job),
                    "trigger": str(job.trigger),
                }
                for job in self.scheduler.get_jobs()
            ],
        }

    def sync_with_database(self, db: Session) -> None:
        """Reconcile scheduler jobs with the database.

        Adds or updates jobs for active scheduled reports, and removes
        jobs whose DB row no longer matches the active filter (e.g. a
        report was unscheduled via DELETE /scheduler/jobs/{id}). The
        method is idempotent and safe to call periodically — that's the
        contract the sidecar relies on.
        """
        # Expire any objects already loaded in this session so we always
        # read fresh state from the database (sidecar reuses sessions).
        db.expire_all()

        # Get all active scheduled reports from database
        reports = db.query(Report).filter(
            Report.is_scheduled == True,  # noqa: E712
            Report.is_active == True,  # noqa: E712
            Report.cron_expression.isnot(None),  # noqa: E712
        ).all()
        active_ids = {r.id for r in reports}

        failed = 0
        for report in reports:
            try:
                self.add_report_job(
                    report_id=cast(int, report.id),
                    cron_expression=cast(str, report.cron_expression),
                    notification_config=report.notification_config,
                )
            except Exception as exc:
                failed += 1
                logger.error(f"Failed to schedule report {report.id}: {exc}")
        if failed and failed == len(reports):
            logger.warning(
                "All %d active scheduled reports failed to sync. "
                "Check CRON expressions and database connectivity.",
                failed,
            )

        # Drop jobs whose DB row no longer qualifies (unscheduled, paused,
        # deleted). Without this, a periodic re-sync would leak stale jobs.
        for job in self.scheduler.get_jobs():
            job_id = job.id
            if not job_id.startswith("report_"):
                continue
            try:
                report_id = int(job_id[len("report_"):])
            except ValueError:
                continue
            if report_id not in active_ids:
                self.scheduler.remove_job(job_id)
                logger.info(f"Removed orphan scheduler job {job_id}")


# Global scheduler instance
_scheduler: ReportScheduler | None = None


def get_scheduler() -> ReportScheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = ReportScheduler()
    return _scheduler


def _execute_scheduled_report(
    report_id: int, notification_config: NotificationConfig | None
) -> None:
    """Execute a scheduled report generation.

    This is called by APScheduler and should not be called directly.
    """
    logger.info(f"Executing scheduled report {report_id}")

    db = None
    try:
        db = SessionLocal()
        report = db.query(Report).filter(Report.id == report_id).first()
        if not report:
            logger.error(f"Report {report_id} not found")
            return

        if not report.is_active:
            logger.info(f"Report {report_id} is inactive, skipping")
            return

        # Generate report for each configured output format
        output_formats = report.output_formats or ["excel"]
        generated_files = []

        for output_format in output_formats:
            try:
                result = generate_report(
                    report=report,
                    output_format=output_format,
                    parameters={},
                    db=db,
                )
                file_path = result.get("file_path")
                if file_path:
                    generated_files.append(file_path)
            except Exception as exc:
                logger.error(f"Failed to generate {output_format} for report {report_id}: {exc}")

        # Send notification if configured
        if notification_config and generated_files:
            _send_notification(notification_config, report, generated_files)

        logger.info(
            f"Completed scheduled report {report_id}, "
            f"generated {len(generated_files)} files"
        )

    except Exception as exc:
        logger.error(f"Error executing scheduled report {report_id}: {exc}")
    finally:
        if db:
            db.close()


def _sign_payload(payload: dict[str, Any], secret: str, timestamp: str) -> str:
    """Return an HMAC-SHA256 hex digest over *timestamp* + JSON body (SEC-4)."""
    body = f"{timestamp}.{payload}"
    return hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _send_notification(
    notification_config: NotificationConfig | None,
    report: Report,
    file_paths: list[str],
) -> None:
    """Send notification about generated report.

    Dispatches on the typed ``NotificationConfig`` variant (批 6b.4
    discriminated union). Webhook + DingTalk share the same delivery
    pipeline — only the URL field name differs (``url`` vs
    ``webhook_url``). Email is logged-but-not-sent (sender TBD).

    Feishu (批 8.4) and WeChat Work (批 8.4) go through their own
    senders because each provider's signing protocol differs:

    * Feishu signs inside the JSON body (``timestamp`` + ``sign``)
      using a string-as-key HMAC pattern.
    * WeChat Work bot URLs authenticate via a ``key=`` query
      parameter and don't sign at all.

    P4 hardening (SEC-4, SEC-8, SEC-14, PY-4) applies to all
    outbound variants:

    * Payload carries basenames only — no absolute paths.
    * HTTPS-only in production (``webhook_https_only``).
    * SSRF guard resolves + IP-pins the destination.
    """
    if notification_config is None:
        return

    if isinstance(notification_config, (WebhookConfig, DingTalkConfig)):
        # Both webhook variants share the same delivery pipeline; only
        # the URL field name differs (``url`` vs ``webhook_url``).
        # The per-config ``secret`` is honoured when present; otherwise
        # :func:`_send_webhook` falls back to the global
        # ``settings.webhook_secret`` for backwards compatibility with
        # operators who configured signing at the app level rather than
        # per-report.
        if isinstance(notification_config, WebhookConfig):
            url = str(notification_config.url)
            per_config_secret = notification_config.secret
        else:
            url = str(notification_config.webhook_url)
            per_config_secret = notification_config.secret
        _send_webhook(
            webhook_url=url,
            report=report,
            file_paths=file_paths,
            secret=per_config_secret,
        )
    elif isinstance(notification_config, FeishuConfig):
        _send_feishu(
            webhook_url=str(notification_config.webhook_url),
            secret=notification_config.secret,
            report=report,
            file_paths=file_paths,
        )
    elif isinstance(notification_config, WeChatWorkConfig):
        _send_wechatwork(
            webhook_url=str(notification_config.webhook_url),
            report=report,
            file_paths=file_paths,
        )
    elif isinstance(notification_config, EmailConfig):
        logger.info(f"Email notification for report {report.id} (not implemented)")
    else:
        # Pydantic should make this unreachable — the union only has
        # the documented variants. Log and bail rather than silently
        # swallow.
        logger.error(
            "Unknown notification_config type for report %s: %r",
            report.id, notification_config,
        )


def _feishu_signature(timestamp: str, secret: str) -> str:
    """Compute Feishu's base64-encoded HMAC-SHA256 signature (批 8.4).

    Feishu's protocol is unusual: it folds the timestamp + secret
    into a single ``"\n"``-joined key, takes the digest of an empty
    message, and base64-encodes the result. This shape doesn't fit
    :func:`_sign_payload` (which signs ``timestamp.payload`` over
    the JSON body), so it stays a separate helper.
    """
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _send_feishu(
    webhook_url: str,
    secret: str | None,
    report: Report,
    file_paths: list[str],
) -> None:
    """Feishu bot webhook delivery (批 8.4).

    Same SSRF + HTTPS + IP-pinning guard as :func:`_send_webhook`,
    but signing semantics differ: when ``secret`` is configured,
    the signature lands *inside* the JSON body as ``timestamp`` /
    ``sign`` keys. Feishu's protocol is documented at
    https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
    — sign algorithm there has been stable since the feature's GA.

    Payload shape (``msg_type: "text"`` keeps it dependency-free —
    no rich-card rendering needed for a "report ready" notice).
    """
    if settings.webhook_https_only and not webhook_url.startswith("https://"):
        scheme = webhook_url.split("://")[0] if "://" in webhook_url else "unknown"
        logger.error(
            f"Refusing Feishu webhook for report {report.id}: "
            f"HTTPS required but URL uses {scheme} scheme"
        )
        webhook_delivery_attempts_total.labels(outcome="https_required").inc()
        return

    try:
        validate_webhook_url(webhook_url)
    except SSRFBlocked as exc:
        logger.error(
            f"Refusing Feishu webhook for report {report.id}: "
            f"URL blocked by SSRF guard: {exc}"
        )
        webhook_delivery_attempts_total.labels(outcome="ssrf_blocked").inc()
        return

    safe_files = [os.path.basename(p) for p in file_paths]
    file_list = "\n".join(safe_files) if safe_files else "(no files)"
    text = (
        f"报表「{report.name}」已生成\n"
        f"生成时间: {datetime.now(timezone.utc).isoformat()}\n"
        f"文件:\n{file_list}"
    )
    payload: dict[str, Any] = {
        "msg_type": "text",
        "content": {"text": text},
    }

    if secret:
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        payload["timestamp"] = timestamp
        payload["sign"] = _feishu_signature(timestamp, secret)

    try:
        client = create_webhook_client(webhook_url)
        resp = client.post(webhook_url, json=payload)
        resp.raise_for_status()
        logger.info(f"Sent Feishu notification for report {report.id}")
        webhook_delivery_attempts_total.labels(outcome="success").inc()
    except Exception as exc:
        logger.error(
            f"Failed to send Feishu notification for report {report.id}: {exc}"
        )
        webhook_delivery_attempts_total.labels(outcome="http_error").inc()


def _send_wechatwork(
    webhook_url: str,
    report: Report,
    file_paths: list[str],
) -> None:
    """WeChat Work bot webhook delivery (批 8.4).

    Posts a plain ``msgtype: "markdown"`` envelope. WeChat Work
    bots authenticate via the ``key=...`` query parameter set at
    bot-creation time — we don't add any signing header or body
    key. The receiver must accept that the URL itself is the
    shared secret; this matches the documented behaviour for
    legacy / non-encrypted bots.

    Same SSRF + HTTPS + IP-pinning gates as the other senders.
    """
    if settings.webhook_https_only and not webhook_url.startswith("https://"):
        scheme = webhook_url.split("://")[0] if "://" in webhook_url else "unknown"
        logger.error(
            f"Refusing WeChat Work webhook for report {report.id}: "
            f"HTTPS required but URL uses {scheme} scheme"
        )
        webhook_delivery_attempts_total.labels(outcome="https_required").inc()
        return

    try:
        validate_webhook_url(webhook_url)
    except SSRFBlocked as exc:
        logger.error(
            f"Refusing WeChat Work webhook for report {report.id}: "
            f"URL blocked by SSRF guard: {exc}"
        )
        webhook_delivery_attempts_total.labels(outcome="ssrf_blocked").inc()
        return

    safe_files = [os.path.basename(p) for p in file_paths]
    file_lines = "\n".join(f"- `{f}`" for f in safe_files) if safe_files else "_no files_"
    content = (
        f"**报表「{report.name}」已生成**\n"
        f"> 生成时间: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"{file_lines}"
    )
    payload: dict[str, Any] = {"msgtype": "markdown", "markdown": {"content": content}}

    try:
        client = create_webhook_client(webhook_url)
        resp = client.post(webhook_url, json=payload)
        resp.raise_for_status()
        logger.info(f"Sent WeChat Work notification for report {report.id}")
        webhook_delivery_attempts_total.labels(outcome="success").inc()
    except Exception as exc:
        logger.error(
            f"Failed to send WeChat Work notification for report {report.id}: {exc}"
        )
        webhook_delivery_attempts_total.labels(outcome="http_error").inc()


def _send_webhook(
    webhook_url: str,
    report: Report,
    file_paths: list[str],
    secret: str | None = None,
) -> None:
    """Common webhook delivery path used by ``WebhookConfig`` and
    ``DingTalkConfig``. URL has already been validated at the Pydantic
    layer (``HttpUrl``); this function applies the runtime gates.

    ``secret`` is the per-config signing key from
    ``WebhookConfig.secret`` / ``DingTalkConfig.secret``. When unset
    (the historical default — operators configure signing globally via
    ``settings.webhook_secret``), we fall back to that env-level
    secret so existing deployments keep working unchanged. Per-config
    takes precedence over the global fallback, which matches the
    intent in ``notification.py``: each report can opt into its own
    signing key without forcing every other report through the same
    key.
    """

    # --- Scheme gate (SEC-14) ---
    if settings.webhook_https_only and not webhook_url.startswith("https://"):
        scheme = webhook_url.split("://")[0] if "://" in webhook_url else "unknown"
        logger.error(
            f"Refusing webhook notification for report {report.id}: "
            f"HTTPS required but URL uses {scheme} scheme"
        )
        webhook_delivery_attempts_total.labels(outcome="https_required").inc()
        return

    # --- SSRF gate (PY-4: also resolves & validates IPs) ---
    try:
        validate_webhook_url(webhook_url)
    except SSRFBlocked as exc:
        logger.error(
            f"Refusing webhook notification for report {report.id}: "
            f"URL blocked by SSRF guard: {exc}"
        )
        webhook_delivery_attempts_total.labels(outcome="ssrf_blocked").inc()
        return

    # --- Build payload ---
    # P4 (SEC-8): strip directory components — the receiver has no
    # business knowing the server's filesystem layout.
    safe_files = [os.path.basename(p) for p in file_paths]
    payload: dict[str, Any] = {
        "report_name": report.name,
        "report_id": report.id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": safe_files,
    }

    # --- Sign (SEC-4) ---
    # Per-config wins over the global ``WEBHOOK_SECRET`` env var. The
    # truthy check normalises empty-string-as-cleared to "no signing"
    # — same convention :func:`_send_feishu` uses for its in-body
    # ``secret`` field, so behaviour is consistent across senders.
    effective_secret = secret if secret else settings.webhook_secret
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if effective_secret:
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        headers["X-Webhook-Timestamp"] = timestamp
        headers["X-Webhook-Signature"] = _sign_payload(
            payload, effective_secret, timestamp
        )

    # --- Send with IP-pinned transport (PY-4) ---
    try:
        client = create_webhook_client(webhook_url)
        resp = client.post(webhook_url, json=payload, headers=headers)
        resp.raise_for_status()
        logger.info(f"Sent webhook notification for report {report.id}")
        webhook_delivery_attempts_total.labels(outcome="success").inc()
    except Exception as exc:
        logger.error(f"Failed to send webhook notification for report {report.id}: {exc}")
        webhook_delivery_attempts_total.labels(outcome="http_error").inc()

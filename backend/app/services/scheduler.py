"""Scheduled task service for automatic report generation."""

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
    NotificationConfig,
    WebhookConfig,
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

    P4 hardening (SEC-4, SEC-8, SEC-14, PY-4) applies to both webhook
    variants:

    * Payload is HMAC-SHA256 signed (``X-Webhook-Signature``).
    * Timestamp (``X-Webhook-Timestamp``) enables replay detection.
    * ``files`` carries basenames only — no absolute paths.
    * HTTPS-only in production (``webhook_https_only``).
    * Connection is IP-pinned to the SSRF-validated address.
    """
    if notification_config is None:
        return

    if isinstance(notification_config, (WebhookConfig, DingTalkConfig)):
        # Both webhook variants share the same delivery pipeline; only
        # the URL field name differs (``url`` vs ``webhook_url``).
        if isinstance(notification_config, WebhookConfig):
            url = str(notification_config.url)
        else:
            url = str(notification_config.webhook_url)
        _send_webhook(
            webhook_url=url,
            report=report,
            file_paths=file_paths,
        )
    elif isinstance(notification_config, EmailConfig):
        logger.info(f"Email notification for report {report.id} (not implemented)")
    else:
        # Pydantic should make this unreachable — the union only has
        # three variants. Log and bail rather than silently swallow.
        logger.error(
            "Unknown notification_config type for report %s: %r",
            report.id, notification_config,
        )


def _send_webhook(webhook_url: str, report: Report, file_paths: list[str]) -> None:
    """Common webhook delivery path used by ``WebhookConfig`` and
    ``DingTalkConfig``. URL has already been validated at the Pydantic
    layer (``HttpUrl``); this function applies the runtime gates."""

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
    secret = settings.webhook_secret
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if secret:
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        headers["X-Webhook-Timestamp"] = timestamp
        headers["X-Webhook-Signature"] = _sign_payload(payload, secret, timestamp)

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

"""Scheduled task service for automatic report generation."""

import logging
from datetime import datetime
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session

from app.models.report import Report
from app.services.report_generator import generate_report

logger = logging.getLogger(__name__)


class ReportScheduler:
    """Manages scheduled report generation tasks."""

    def __init__(self):
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
        notification_config: dict[str, Any] | None = None,
    ) -> str:
        """Add or update a scheduled job for a report.

        Args:
            report_id: The report ID to schedule
            cron_expression: 6-field cron expression (min hour dom mon dow year)
            notification_config: Configuration for notifications

        Returns:
            The job ID
        """
        job_id = f"report_{report_id}"

        # Parse cron expression (min hour dom mon dow year)
        parts = cron_expression.split()
        if len(parts) != 6:
            raise ValueError("Cron expression must have 6 fields: min hour dom mon dow year")

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
            args=[report_id, notification_config or {}],
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

    def get_job_status(self, report_id: int) -> dict[str, Any] | None:
        """Get the status of a scheduled job."""
        job_id = f"report_{report_id}"
        job = self.scheduler.get_job(job_id)
        if not job:
            return None

        return {
            "job_id": job.id,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
        }

    def sync_with_database(self, db: Session) -> None:
        """Sync scheduler jobs with database.

        This should be called on startup to load all active scheduled reports.
        """
        # Get all active scheduled reports from database
        reports = db.query(Report).filter(
            Report.is_scheduled == True,  # noqa: E712
            Report.is_active == True,  # noqa: E712
            Report.cron_expression.isnot(None),  # noqa: E712
        ).all()

        for report in reports:
            try:
                self.add_report_job(
                    report_id=report.id,
                    cron_expression=report.cron_expression,
                )
            except Exception as exc:
                logger.error(f"Failed to schedule report {report.id}: {exc}")


# Global scheduler instance
_scheduler: ReportScheduler | None = None


def get_scheduler() -> ReportScheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = ReportScheduler()
    return _scheduler


def _execute_scheduled_report(report_id: int, notification_config: dict[str, Any]) -> None:
    """Execute a scheduled report generation.

    This is called by APScheduler and should not be called directly.
    """
    from app.database import SessionLocal

    logger.info(f"Executing scheduled report {report_id}")

    db = SessionLocal()
    try:
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
        db.close()


def _send_notification(
    notification_config: dict[str, Any],
    report: Report,
    file_paths: list[str],
) -> None:
    """Send notification about generated report.

    Currently supports webhook notifications.
    """
    notification_type = notification_config.get("type")

    if notification_type == "webhook":
        import httpx

        webhook_url = notification_config.get("webhook_url")
        if webhook_url:
            payload = {
                "report_name": report.name,
                "report_id": report.id,
                "generated_at": datetime.now().isoformat(),
                "files": file_paths,
            }
            try:
                httpx.post(webhook_url, json=payload, timeout=30)
                logger.info(f"Sent webhook notification for report {report.id}")
            except Exception as exc:
                logger.error(f"Failed to send webhook notification: {exc}")

    elif notification_type == "email":
        # Email notification would require SMTP configuration
        # This is a placeholder for future implementation
        logger.info(f"Email notification for report {report.id} (not implemented)")

"""Sidecar process that owns the scheduler tick loop.

Run alongside web workers when ``SCHEDULER_DISABLED=true`` is set in the
web workers' environment, so only this process drives APScheduler and
each scheduled job runs exactly once per tick (instead of N times under
``gunicorn -w N``).

Usage::

    python -m app.scheduler_runner

The sidecar re-syncs from the database every
``settings.scheduler_resync_interval`` seconds (default 30), so changes
made via the web API propagate to the running scheduler within one
interval. SIGTERM / SIGINT trigger a graceful shutdown.
"""

import logging
import signal
import threading
from types import FrameType

from app.config import settings
from app.database import SessionLocal
from app.services.scheduler import get_scheduler
from app.services.subscription import sync_subscriptions_with_database

logger = logging.getLogger(__name__)


def run(
    stop_event: threading.Event | None = None,
    resync_interval: int | None = None,
) -> None:
    """Run the sidecar loop until *stop_event* is set (or forever, if None).

    Splits the public function from signal-handling glue in :func:`main`
    so tests can drive the loop with their own event and interval.
    """
    scheduler = get_scheduler()
    scheduler.start()

    stop = stop_event or threading.Event()
    interval = (
        resync_interval if resync_interval is not None else settings.scheduler_resync_interval
    )
    logger.info("Scheduler sidecar started; re-syncing every %ss", interval)

    while not stop.is_set():
        db = SessionLocal()
        try:
            scheduler.sync_with_database(db)
            # Subscriptions live on the same APScheduler instance but
            # carry their own job-id namespace (``sub_<id>`` vs
            # ``report_<id>``); reconciling them in lockstep with the
            # report jobs means a subscription created via the web API
            # lands on the sidecar's tick loop within one interval.
            sync_subscriptions_with_database(db)
        except Exception as exc:  # never let the loop die on a transient DB error
            logger.error("Sync iteration failed: %s", exc)
        finally:
            db.close()
        if stop.wait(interval):
            break

    scheduler.shutdown()
    logger.info("Scheduler sidecar stopped")


def main() -> None:
    """Console-script entry point: install signal handlers and call :func:`run`."""
    logging.basicConfig(level=logging.INFO)

    stop = threading.Event()

    def _on_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    run(stop)


if __name__ == "__main__":
    main()

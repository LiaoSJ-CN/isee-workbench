"""FastAPI application entry point."""

import logging
import logging.handlers
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from alembic import command as alembic_command
from app.config import settings
from app.database import SessionLocal
from app.middleware.csrf import CSRFMiddleware
from app.middleware.metrics import setup_metrics
from app.middleware.proxy_headers import ProxyHeadersMiddleware
from app.middleware.request_id import RequestIDMiddleware, install_request_id_log_factory
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.sentry import init_sentry
from app.models import audit_log as _audit_log_module  # noqa: F401  # 批 9.5
from app.models import data_source as _data_source_module  # noqa: F401
from app.models import rate_limit as _rate_limit_module  # noqa: F401
from app.models import report as _report_module  # noqa: F401
from app.models import report_job as _report_job_module  # noqa: F401
from app.models import revoked_token as _revoked_token_module  # noqa: F401
from app.models import user as _user_module  # noqa: F401
from app.models.user import User
from app.routers import (
    audit,  # 批 9.5
    auth,
    data_source,
    explorer,
    jobs,
    report,
    scheduler,
    subscription,
)
from app.services.job_queue import shutdown_executor
from app.services.password import hash_password
from app.services.scheduler import get_scheduler

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _configure_logging() -> None:
    """Configure root logging once at application startup.

    Called from lifespan so it runs after settings are resolved and
    before any request is served, rather than at import time.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s [%(request_id)s]: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                LOG_DIR / "app.log",
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
                encoding="utf-8",
            ),
        ],
    )


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


def _seed_admin_user() -> None:
    """Idempotently create the bootstrap admin user from settings.

    P3 (SEC-18): replaces the pre-P3 ``settings.admin_password`` plaintext
    compare. On first start, the configured password is bcrypt-hashed and
    stored in ``users``. Subsequent starts are no-ops; rotating the
    bootstrap password requires either updating the row directly or
    removing it so this function recreates it.
    """
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == settings.admin_username).first()
        if existing is not None:
            return
        db.add(
            User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
            )
        )
        db.commit()
        logger.info(
            "Seeded bootstrap admin user '%s' (id will be assigned)",
            settings.admin_username,
        )
    finally:
        db.close()


def _run_migrations() -> None:
    """Apply Alembic migrations to ``settings.database_url``.

    Replaces the old module-level ``Base.metadata.create_all`` +
    ``ensure_columns`` dance. Running through Alembic gives us a real
    migration history (so future schema changes have upgrade/downgrade
    scripts) and removes the silent "create tables but not columns"
    footgun of ``create_all``.

    Idempotent — calling against a database already at head is a no-op
    (Alembic short-circuits on the version table match).
    """
    cfg = AlembicConfig(str(ALEMBIC_INI))
    # env.py reads settings.database_url itself, but be explicit so
    # future test fixtures that swap the URL via monkeypatch keep
    # working without re-importing alembic internals.
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    alembic_command.upgrade(cfg, "head")
    logger.info("Alembic migrations applied")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager."""
    _configure_logging()
    # Inject ``request_id`` onto every LogRecord so log lines emitted
    # during request handling can be correlated with the response's
    # ``X-Request-ID`` header. Must run after ``_configure_logging``
    # (which installs its own factory) so we wrap it.
    install_request_id_log_factory()
    if init_sentry():
        logger.info(
            "Sentry initialized (environment=%s, traces_sample_rate=%s)",
            settings.sentry_environment or "unset",
            settings.sentry_traces_sample_rate,
        )
    # Schema migrations first — admin seed and scheduler sync both
    # query tables that must exist.
    _run_migrations()
    _seed_admin_user()
    if settings.scheduler_disabled:
        logger.info(
            "Scheduler is DISABLED in this process — "
            "run 'python -m app.scheduler_runner' as a sidecar for "
            "scheduled report generation."
        )
    else:
        logger.info(
            "Scheduler is ENABLED in this process — "
            "for multi-worker deployments set SCHEDULER_DISABLED=true "
            "and run the sidecar separately."
        )
        scheduler = get_scheduler()
        db = SessionLocal()
        try:
            scheduler.sync_with_database(db)
            scheduler.start()
        finally:
            db.close()

    yield

    if not settings.scheduler_disabled:
        get_scheduler().shutdown()
    # Stop the report-job executor pool. ``wait=False`` so an in-flight
    # render doesn't hold up process exit — the job row stays in
    # ``running`` state and would need an operator-side reconcile
    # (out of scope for batch 3a; plan checkpoint noted this risk).
    shutdown_executor(wait=False)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

# Outermost middleware: stamp every request with X-Request-ID and
# expose it via the request_id contextvar so downstream middleware,
# route handlers, and the logging factory all see it.
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Cookie"],
)

# Rewrite request.client from X-Forwarded-For when the immediate peer
# is a trusted proxy (P3.5 / PY-12). Must run before route handlers
# so the rate limiter sees the real client IP.
app.add_middleware(ProxyHeadersMiddleware)

# Attach baseline security headers to every response (P5 / SEC-5).
app.add_middleware(SecurityHeadersMiddleware)

# CSRF defence (批 6b.3) — sits inside SecurityHeadersMiddleware so the
# 403 response still carries the X-Content-Type-Options etc. headers.
# Inside CORS so the preflight handler is unaffected.
app.add_middleware(CSRFMiddleware)

app.include_router(auth.router)
app.include_router(data_source.router)
app.include_router(report.router)
app.include_router(scheduler.router)
app.include_router(explorer.router)
# Async report-generation jobs (批 3a). Two routers because the surface
# mixes /reports/{id}/jobs and /jobs/{id} prefixes.
app.include_router(jobs.report_jobs_router)
app.include_router(jobs.jobs_router)
# Per-user report subscriptions (批 8.3).
app.include_router(subscription.router)
# Admin-only audit log (批 9.5).
app.include_router(audit.router)

# Prometheus /metrics + default HTTP histogram. Must come AFTER the
# routers so Instrumentator sees the final route table. Idempotent for
# tests that re-import the module — Instrumentator is process-singleton
# per app instance.
setup_metrics(app)

# Serve locally-bundled Chart.js so generated HTML previews work without external CDN.
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health")
def health_check() -> dict[str, str]:
    """Health check — includes database connectivity probe."""
    db_status = "ok"
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
    except Exception as exc:
        db_status = "unavailable"
        logger.error("Health check: database probe failed — %s", exc)

    overall = "ok" if db_status == "ok" else "unhealthy"
    return {
        "status": overall,
        "database": db_status,
    }

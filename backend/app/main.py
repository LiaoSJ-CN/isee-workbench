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
from app.models import dashboard as _dashboard_module  # noqa: F401  # 批 14
from app.models import dashboard_access as _dashboard_access_module  # noqa: F401  # 批 14
from app.models import (
    dashboard_subscription as _dashboard_subscription_module,  # noqa: F401  # 批 14
)
from app.models import data_source as _data_source_module  # noqa: F401
from app.models import rate_limit as _rate_limit_module  # noqa: F401
from app.models import report as _report_module  # noqa: F401
from app.models import report_job as _report_job_module  # noqa: F401
from app.models import revoked_token as _revoked_token_module  # noqa: F401
from app.models import user as _user_module  # noqa: F401
from app.models.data_source import DataSource
from app.models.user import User
from app.routers import (
    admin_data_sources,  # 批 E
    admin_grants,  # batch user-management Stage 2
    admin_metrics,  # 批 12
    admin_users,  # batch user-management Stage 1
    audit,  # 批 9.5
    auth,
    dashboard,  # 批 14
    dashboard_subscription,  # 批 14
    data_source,
    explorer,
    jobs,
    report,
    report_version,
    scheduler,
    search,  # 批 A — global command-palette search
    subscription,
    users,  # A3 (post-批-report-versioning)
)
from app.services.dashboard_subscription import (
    sync_dashboard_subscriptions_with_database,
)
from app.services.job_queue import shutdown_executor
from app.services.password import hash_password
from app.services.scheduler import get_scheduler
from app.services.subscription import sync_subscriptions_with_database

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
                # 批 13 — stamp org_id from settings so an admin
                # working in a multi-tenant deployment can author
                # ``org``-tier templates. Existing admins aren't
                # retroactively stamped (intentional — backfilling
                # silently would surprise operators running with
                # user-screled accounts).
                org_id=settings.default_org_id,
            )
        )
        db.commit()
        logger.info(
            "Seeded bootstrap admin user '%s' (id will be assigned)",
            settings.admin_username,
        )
    finally:
        db.close()


def _seed_demo_data() -> None:
    """Optionally rebuild demo data when ``data_sources`` is empty.

    Closes a dev-only gap where ``scripts/seed_erp_demo.py`` builds the
    business warehouse but never writes the ``data_sources`` metadata
    row, so a fresh ``app.db`` would leave ``seed_reports.py`` exiting
    with ``PREFERRED_NAME='sqlite_demo'`` lookup failure.

    Gated by **both**:

    1. ``settings.seed_demo_on_startup`` (env var
       ``SEED_DEMO_ON_STARTUP``) — default False; opt-in per deployment.
    2. ``data_sources`` table empty at lifespan startup — defensive
       even if the flag is accidentally enabled in production with
       pre-existing data.

    Failures are logged but never abort startup — bootstrap is a
    developer convenience, not a service dependency. The lazy import
    keeps ``scripts.bootstrap_demo`` out of the import-time graph so a
    stripped-down deployment that omits ``scripts/`` still boots.
    """
    if not settings.seed_demo_on_startup:
        return
    db = SessionLocal()
    try:
        ds_count = db.query(DataSource).count()
    finally:
        db.close()
    if ds_count > 0:
        logger.info(
            "seed_demo_data: skipped — data_sources has %d row(s) (non-empty guard)",
            ds_count,
        )
        return
    logger.warning(
        "seed_demo_data: data_sources empty AND SEED_DEMO_ON_STARTUP=true — "
        "rebuilding demo ERP warehouse + reports + dashboards "
        "(this REPLACES backend/data/erp_demo.db)"
    )
    try:
        # Lazy import — scripts/ is not on the import path by default;
        # bootstrap_demo itself adds backend/ to sys.path. Kept here so
        # the dependency stays out of the top-level graph.
        from scripts import bootstrap_demo  # noqa: WPS433
    except Exception:
        logger.exception(
            "seed_demo_data: could not import scripts.bootstrap_demo "
            "(scripts/ missing?) — skipping"
        )
        return
    try:
        status = bootstrap_demo.run()
        logger.info("seed_demo_data: bootstrap completed — %s", status)
    except Exception:
        logger.exception(
            "seed_demo_data: bootstrap failed (continuing startup)"
        )


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
    _seed_demo_data()
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
            # Subscriptions reuse the same APScheduler instance but live
            # on the ``sub_<id>`` namespace; reconciling them here
            # keeps single-process dev (``SCHEDULER_DISABLED=false``)
            # self-sufficient without depending on the sidecar.
            sync_subscriptions_with_database(db)
            # Dashboard subscriptions reuse the same APScheduler
            # instance on the ``dsub_<id>`` namespace (批 14).
            sync_dashboard_subscriptions_with_database(db)
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
# Report versioning (snapshot / list / diff / restore / delete).
app.include_router(report_version.router)
# Dashboard CRUD + items + layout + shares + preview (批 14.2).
app.include_router(dashboard.router)
app.include_router(scheduler.router)
app.include_router(explorer.router)
# Async report-generation jobs (批 3a). Two routers because the surface
# mixes /reports/{id}/jobs and /jobs/{id} prefixes.
app.include_router(jobs.report_jobs_router)
app.include_router(jobs.jobs_router)
# Per-user report subscriptions (批 8.3).
app.include_router(subscription.router)
# Per-user dashboard subscriptions (批 14.2) — CRUD endpoint surface;
# dispatch logic lands in 批 14.4.
app.include_router(dashboard_subscription.router)
# Admin-only audit log (批 9.5).
app.include_router(audit.router)
# Admin-only DataSource connection-pool metrics (批 12).
app.include_router(admin_metrics.router)
# Admin-only DataSource mutations (批 E) — currently: rotate-password.
app.include_router(admin_data_sources.router)
# Admin-only User CRUD + password reset (batch user-management Stage 1).
app.include_router(admin_users.router)
# Admin-only centralised grant / revoke (batch user-management Stage 2).
app.include_router(admin_grants.router)
# User listing for client-side created_by resolution (A3, post-批-report-versioning).
app.include_router(users.router)
# Global command-palette search (批 A — 联合搜索).
app.include_router(search.router)

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

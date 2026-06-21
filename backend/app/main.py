"""FastAPI application entry point."""

import logging
import logging.handlers
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.db_migrations import ensure_columns
from app.middleware.proxy_headers import ProxyHeadersMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.models import data_source as _data_source_module  # noqa: F401
from app.models import rate_limit as _rate_limit_module  # noqa: F401
from app.models import report as _report_module  # noqa: F401
from app.models import revoked_token as _revoked_token_module  # noqa: F401
from app.models import user as _user_module  # noqa: F401
from app.models.user import User
from app.routers import auth, data_source, explorer, report, scheduler
from app.services.password import hash_password
from app.services.scheduler import get_scheduler

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _configure_logging() -> None:
    """Configure root logging once at application startup.

    Called from lifespan so it runs after settings are resolved and
    before any request is served, rather than at import time.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
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
# Database bootstrap
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

Base.metadata.create_all(bind=engine)
# Backfill any columns added to models after the table was first created;
# create_all only creates missing tables, never missing columns.
ensure_columns(engine)


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager."""
    _configure_logging()
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


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

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

app.include_router(auth.router)
app.include_router(data_source.router)
app.include_router(report.router)
app.include_router(scheduler.router)
app.include_router(explorer.router)

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

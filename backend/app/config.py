"""Application configuration."""

import base64
import logging
import os
import secrets
import warnings
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "iSee Data Analysis Workbench"
    debug: bool = False
    database_url: str = f"sqlite:///{Path(__file__).parent.parent / 'app.db'}"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # --- Scheduler ---
    # Sidecar deployment: when true the web process skips starting
    # APScheduler. Run ``python -m app.scheduler_runner`` as a separate
    # sidecar so only one process owns the tick loop — fixes the
    # "gunicorn -w N → job runs N times" bug.
    # Defaults to True so the web process is scheduler-disabled by default;
    # set to False for single-process dev convenience.
    scheduler_disabled: bool = True
    scheduler_resync_interval: int = 30

    # --- Auth ---
    admin_username: str = "admin"
    admin_password: str = "admin"
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 24       # 1 day
    refresh_token_days: int = 7

    # --- Brute-force protection ---
    # Max login attempts per IP per minute before returning 429.
    login_rate_limit: int = 10

    # --- Trusted proxies (P3.5 / PY-12) ---
    # IPs or CIDR subnets of reverse proxies that may set X-Forwarded-For.
    # When the immediate peer is in this list, the rightmost non-trusted
    # hop in X-Forwarded-For is used as the real client IP (needed for
    # accurate rate-limit attribution behind nginx / HAProxy).
    # Default empty — safe for dev (no proxy) and direct-connect deploys.
    trusted_proxies: list[str] = []

    # --- Webhook security (P4) ---
    # Shared secret for HMAC-SHA256 signing of webhook payloads.
    # The receiver validates the X-Webhook-Signature header with the same
    # secret. Empty by default — webhooks are still sent, but unsigned.
    webhook_secret: str = ""
    # When True, webhook URLs must use HTTPS (blocks plaintext HTTP).
    # Default True for production safety; set False for local dev/testing.
    webhook_https_only: bool = False
    # Max age (seconds) of a webhook timestamp for replay protection.
    # Payloads older than this are rejected by the receiver.
    webhook_timestamp_max_age: int = 300  # 5 min

    # --- Cookie auth (P3 / SEC-6) ---
    # When True, login/refresh set HttpOnly+SameSite cookies; the
    # ``Authorization: Bearer`` header remains supported as a fallback
    # (CLI / curl). Set False to revert to header-only auth (legacy
    # clients that can't deal with cookies).
    cookie_auth_enabled: bool = True
    # Cookie ``Secure`` flag. MUST be True in production (HTTPS); False
    # in local dev so the browser accepts the cookie on ``http://localhost``.
    cookie_secure: bool = False
    # Cookie ``SameSite`` policy. ``Lax`` blocks cross-site POST (CSRF
    # defense) while still allowing the cookie to flow on same-site
    # XHR and top-level GET navigations — matches the Vite/nginx
    # reverse-proxy topology where the API is on the same origin as
    # the SPA.
    cookie_samesite: str = "lax"
    # Names — keep the defaults; only change if the SPA needs to
    # distinguish two deployments on the same hostname.
    access_cookie_name: str = "access_token"
    refresh_cookie_name: str = "refresh_token"

    # --- Database pool ---
    # Only applied when DATABASE_URL is not SQLite; SQLite uses a
    # single-connection NullPool which ignores these.
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- Data-source password encryption at rest ---
    # Fernet key (urlsafe-base64, 32 bytes). If unset a random key is
    # generated so development works out of the box, but production
    # MUST pin this — changing the key makes stored passwords unreadable.
    encryption_key: str = ""

    # --- Report output ---
    # Directory where generated report files (HTML / Excel) are saved.
    generated_reports_dir: Path = Path(__file__).resolve().parent.parent / "generated_reports"

    # --- Logging ---
    log_level: str = "INFO"


def _resolve_jwt_key(raw: str) -> str:
    """Return a usable JWT signing key, generating one if needed."""
    if raw:
        return raw
    generated = secrets.token_urlsafe(48)
    warnings.warn(
        "JWT_SECRET_KEY is not set; using an ephemeral random key. "
        "All tokens will be invalidated on every restart. "
        "Set JWT_SECRET_KEY in backend/.env for stable tokens.",
        stacklevel=2,
    )
    return generated


def _resolve_encryption_key(raw: str) -> str:
    """Return a usable Fernet key, generating one if needed."""
    if raw:
        return raw
    generated = base64.urlsafe_b64encode(os.urandom(32)).decode()
    logger.warning(
        "ENCRYPTION_KEY is not set; using an ephemeral random key. "
        "Encrypted data-source passwords will become unreadable on restart. "
        "Set ENCRYPTION_KEY in backend/.env for stable encryption."
    )
    return generated


settings = Settings()
settings.jwt_secret_key = _resolve_jwt_key(settings.jwt_secret_key)
settings.encryption_key = _resolve_encryption_key(settings.encryption_key)

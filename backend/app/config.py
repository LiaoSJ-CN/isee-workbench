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

    # CSRF defence (批 6b.3). When enabled, ``CSRFMiddleware`` rejects
    # state-changing requests whose ``Origin`` is not in ``cors_origins``.
    # Disable only for tests / scripts that intentionally post from an
    # untracked origin; production should leave this on.
    csrf_enabled: bool = True

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

    # --- API rate limits (批 6b.2) ---
    # Per-IP, per-minute ceilings on the expensive / write-prone endpoints.
    # Defaults are tuned for a single-user dev box — tighten via env vars
    # (``EXPLORER_QUERY_RATE_LIMIT=30 REPORTS_GENERATE_RATE_LIMIT=10``)
    # before going to a shared deployment.
    explorer_query_rate_limit: int = 30
    reports_generate_rate_limit: int = 10

    # --- Trusted proxies (P3.5 / PY-12) ---
    # IPs or CIDR subnets of reverse proxies that may set X-Forwarded-For.
    # When the immediate peer is in this list, the rightmost non-trusted
    # hop in X-Forwarded-For is used as the real client IP (needed for
    # accurate rate-limit attribution behind nginx / HAProxy).
    # Default empty — safe for dev (no proxy) and direct-connect deploys.
    trusted_proxies: list[str] = []

    # --- Security headers (P5 / SEC-5) ---
    # When True, the SecurityHeadersMiddleware attaches X-Content-Type-Options,
    # X-Frame-Options, Referrer-Policy, and Permissions-Policy to every response.
    security_headers_enabled: bool = True

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

    # --- Data explorer ---
    # Maximum rows returned by /explorer/query (prevents memory exhaustion).
    explorer_max_rows: int = 10_000
    # Statement timeout in seconds for explorer queries. Only applied when
    # the target DB supports it (PostgreSQL-based). 0 = no timeout.
    explorer_statement_timeout: int = 30

    # --- Report output ---
    # Directory where generated report files (HTML / Excel) are saved.
    generated_reports_dir: Path = Path(__file__).resolve().parent.parent / "generated_reports"

    # --- Logging ---
    log_level: str = "INFO"

    # --- Sentry (批 6a) ---
    # Empty by default (local dev). Set to a DSN like
    # ``https://<key>@o<org>.ingest.sentry.io/<project>`` to enable.
    # When set, ``init_sentry()`` runs at lifespan startup and the
    # ``RequestIDMiddleware`` propagates ``X-Request-ID`` onto every
    # Sentry event as a tag.
    sentry_dsn: str = ""
    # Sentry environment tag — e.g. "production", "staging". Empty
    # defaults to "development" inside the SDK.
    sentry_environment: str = ""
    # Fraction of requests to capture as performance transactions.
    # 0.0 (default) disables tracing; 1.0 captures all. 0.1 is a typical
    # production sample rate.
    sentry_traces_sample_rate: float = 0.0

    # --- Audit log retention (批 11.1) ---
    # Drop ``audit_log`` rows older than this many days during the
    # purge sweep. ``0`` (default) disables the sweep entirely — the
    # table grows without bound. Set to e.g. ``180`` for a 6-month
    # compliance window. Operators wire the actual delete call from
    # system cron or the scheduler sidecar; the config just controls
    # *which* rows the sweep targets.
    audit_log_retention_days: int = 0

    # --- Email / SMTP (批 8.3) ---
    # Plain SMTP delivery used by ``EmailConfig`` notifications (which
    # subscriptions reach the user through). Configuration follows the
    # ``smtplib`` standard library — supports STARTTLS on 587 and
    # implicit TLS on 465 via ``smtp_use_ssl``.
    #
    # Defaults are empty so the server boots without an SMTP backend
    # configured; ``_send_email`` raises an actionable error if any
    # dispatch attempts to send before the operator sets these. Local
    # dev typically points at mailhog / mailpit
    # (``SMTP_HOST=localhost SMTP_PORT=1025``) without auth.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    # ``From:`` header used for outbound mail. Falls back to
    # ``{smtp_user}@{smtp_host}`` when unset (with a log warning).
    smtp_from_address: str = ""
    # Display name shown in the ``From:`` header. Falls back to
    # ``settings.app_name``.
    smtp_from_name: str = ""
    # STARTTLS upgrade on the plaintext SMTP connection (port 587 path).
    # Most modern SMTP servers require this; only disable for trusted
    # local relays (mailhog, in-cluster SMTP).
    smtp_use_starttls: bool = True
    # Implicit TLS — set True for SMTPS on port 465. Mutually exclusive
    # with ``smtp_use_starttls``; if both are on, STARTTLS wins (the
    # connection has already been encrypted before STARTTLS would run).
    smtp_use_ssl: bool = False


class ConfigurationError(RuntimeError):
    """Fatal configuration error — app cannot start safely."""


def _resolve_jwt_key(raw: str, debug: bool) -> str:
    """Return a usable JWT signing key.

    In dev mode a missing key generates an ephemeral one with a warning.
    In production a missing key is fatal — random keys invalidate every
    token on restart, breaking all existing sessions silently.
    """
    if raw:
        return raw
    if not debug:
        raise ConfigurationError(
            "JWT_SECRET_KEY is not set. "
            "Set JWT_SECRET_KEY in backend/.env for stable tokens. "
            "Ephemeral keys are only allowed when DEBUG=true."
        )
    generated = secrets.token_urlsafe(48)
    warnings.warn(
        "JWT_SECRET_KEY is not set; using an ephemeral random key. "
        "All tokens will be invalidated on every restart. "
        "Set JWT_SECRET_KEY in backend/.env for stable tokens.",
        stacklevel=2,
    )
    return generated


def _resolve_encryption_key(raw: str, debug: bool) -> str:
    """Return a usable Fernet key.

    Same policy as _resolve_jwt_key: fatal in production, ephemeral in dev.
    """
    if raw:
        return raw
    if not debug:
        raise ConfigurationError(
            "ENCRYPTION_KEY is not set. "
            "Set ENCRYPTION_KEY in backend/.env for stable encryption. "
            "Ephemeral keys are only allowed when DEBUG=true."
        )
    generated = base64.urlsafe_b64encode(os.urandom(32)).decode()
    logger.warning(
        "ENCRYPTION_KEY is not set; using an ephemeral random key. "
        "Encrypted data-source passwords will become unreadable on restart. "
        "Set ENCRYPTION_KEY in backend/.env for stable encryption."
    )
    return generated


settings = Settings()
settings.jwt_secret_key = _resolve_jwt_key(settings.jwt_secret_key, settings.debug)
settings.encryption_key = _resolve_encryption_key(settings.encryption_key, settings.debug)

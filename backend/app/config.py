"""Application configuration."""

import secrets
import warnings
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Business Analysis Report Backend"
    debug: bool = False
    database_url: str = f"sqlite:///{Path(__file__).parent.parent / 'app.db'}"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Auth — single shared admin user backed by an env-var password.
    admin_username: str = "admin"
    admin_password: str = "admin"
    # JWT signing key. If unset, generate one per process and warn loudly —
    # all tokens become invalid on every restart in that case.
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 24       # 1 day
    refresh_token_days: int = 7


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


settings = Settings()
settings.jwt_secret_key = _resolve_jwt_key(settings.jwt_secret_key)

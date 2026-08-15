"""Sentry integration (批 6a).

Conditional init — when ``SENTRY_DSN`` is empty (the default for local
dev) the SDK is never imported past a feature-flag check, so there's
zero runtime cost and no network egress.

When ``SENTRY_DSN`` is set we:
- Initialize the SDK once at lifespan startup with FastAPI + stdlib
  logging integrations.
- Use ``before_send`` to stamp the current ``request_id`` (from the
  contextvar installed by :mod:`app.middleware.request_id`) onto every
  event. That gives Sentry the same correlation key the response
  header carries, so an event in the dashboard can be traced back to
  one specific request via the client.
- Filter out HTTPException-shaped events so 4xx responses don't pollute
  the issue stream. sentry-sdk's FastApiIntegration already excludes
  unhandled ``HTTPException``; the extra guard in :func:`_filter_event`
  belt-and-braces captures the case where an HTTPException is
  re-raised or captured manually.

Performance tracing (``traces_sample_rate``) is off by default — set
``SENTRY_TRACES_SAMPLE_RATE=0.1`` (or similar) to enable.

This module intentionally has no other responsibilities — logging
format, request id propagation, and Sentry capture all stay
independent, so any one of them can be disabled without touching the
others.
"""

from __future__ import annotations

from typing import Any

from app.config import settings


def init_sentry() -> bool:
    """Initialize Sentry if ``SENTRY_DSN`` is set. Returns whether init ran.

    Safe to call more than once — the SDK no-ops on repeat
    ``sentry_sdk.init`` with identical config.
    """
    dsn = settings.sentry_dsn
    if not dsn:
        return False

    # Import lazily so the SDK isn't loaded when Sentry is disabled.
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # Capture warnings (logger.warning) and above as breadcrumbs; only
        # ``error`` becomes an event by default — matches what most teams
        # want from a log integration.
        integrations=[
            LoggingIntegration(level=None, event_level=None),
            FastApiIntegration(),
            StarletteIntegration(),
        ],
        before_send=_filter_event,
        before_send_transaction=_filter_transaction,
    )
    return True


def _filter_event(event: Any, _hint: dict[str, Any]) -> Any:
    """Stamp ``request_id`` on the event and drop 4xx-shaped captures.

    ``sentry-sdk``'s FastApi integration already skips HTTPException;
    this guard catches manual ``capture_exception`` calls that surface a
    plain exception class derived from HTTPException.
    """
    _stamp_request_id(event)

    # If the event's only exception is an HTTPException (or subclass),
    # drop it. Real bugs in HTTPException subclasses are vanishingly
    # rare; the noise of every 404/422 being an issue outweighs the
    # signal.
    exception = event.get("exception", {})
    values = exception.get("values", []) if isinstance(exception, dict) else []
    if values and all(_is_http_exception(v) for v in values):
        return None
    return event


def _filter_transaction(
    event: Any, _hint: dict[str, Any]
) -> Any:
    """Stamp request_id on performance transactions too."""
    _stamp_request_id(event)
    return event


def _stamp_request_id(event: dict[str, Any]) -> None:
    """Copy the current request id (if any) into ``event.tags.request_id``."""
    # Local import to avoid a circular import at module load —
    # ``request_id`` imports nothing from this module, but keeping the
    # import inside the function keeps the dependency graph obvious.
    from app.middleware.request_id import get_request_id

    request_id = get_request_id()
    if not request_id or request_id == "-":
        return
    tags = event.setdefault("tags", {})
    if isinstance(tags, dict):
        tags.setdefault("request_id", request_id)


def _is_http_exception(value: dict[str, Any]) -> bool:
    """Return True if a Sentry exception entry looks like an HTTPException."""
    if not isinstance(value, dict):
        return False
    mechanism = value.get("mechanism") or {}
    if isinstance(mechanism, dict) and mechanism.get("type") == "generic":
        # FastApi integration sets this for HTTPException; trust it.
        handled = mechanism.get("handled")
        if handled is True:
            return True
    # Fallback: check the type string.
    exc_type = (value.get("type") or "").lower()
    return exc_type.endswith("httpexception") or "starlette.exceptions" in exc_type

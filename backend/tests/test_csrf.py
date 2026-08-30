"""Tests for 批 6b.3 — CSRF middleware (Origin header check).

Rewritten 2026-08-30 as direct middleware unit tests after the
[[test-pollution-anti-pattern]] audit. The previous suite routed
through ``/reports/1/items`` as a real-auth-passing fixture, which
left empty ``name='x'`` rows on the demo ``财务经营月报`` because the
CSRF tests only assert ``status != 403`` — they PASS but the row
sticks.

These tests invoke ``CSRFMiddleware.__call__`` directly with a
hand-built ASGI ``scope`` dict. No TestClient, no route, no DB. The
leak shape can no longer be produced.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.types import Receive, Scope, Send


def _build_scope(
    *,
    method: str = "POST",
    path: str = "/reports/1/items",
    headers: list[tuple[bytes, bytes]] | None = None,
    host: bytes = b"testserver",
) -> Scope:
    """Hand-rolled ASGI ``scope`` dict for a single HTTP request.

    ``headers`` is the ASGI wire format: list of (name, value) byte
    pairs. ``Host`` defaults to ``testserver`` — TestClient's
    ``base_url`` — which is what the same-origin trust check compares
    against.
    """
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers or [],
        "server": (host.decode("latin-1"), 80),
    }


async def _drive_middleware(
    scope: Scope,
    *,
    csrf_enabled: bool | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Invoke ``CSRFMiddleware`` once against *scope*.

    Returns ``(messages, downstream_called)``: ``messages`` is what the
    middleware sent upstream (typically a 403 response shape), and
    ``downstream_called`` is whether the inner app was reached.

    ``csrf_enabled`` lets a test toggle the setting without touching
    ``monkeypatch`` directly. ``None`` means "leave the current
    setting alone".
    """
    from app.middleware import csrf as csrf_module

    saved: bool | None = None
    if csrf_enabled is not None:
        saved = csrf_module.settings.csrf_enabled
        csrf_module.settings.csrf_enabled = csrf_enabled

    messages: list[dict[str, Any]] = []
    downstream_called = {"value": False}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    async def downstream(receive: Receive, _send: Send, _scope: Scope) -> None:
        downstream_called["value"] = True

    mw = csrf_module.CSRFMiddleware(app=downstream)
    try:
        await mw(scope, receive, send)
    finally:
        if saved is not None:
            csrf_module.settings.csrf_enabled = saved

    return messages, downstream_called["value"]


def _header_value(messages: list[dict[str, Any]], name: bytes) -> bytes | None:
    """Extract a header value from the ``http.response.start`` message."""
    for msg in messages:
        if msg["type"] != "http.response.start":
            continue
        for h_name, h_value in msg.get("headers", []):
            if h_name == name:
                return h_value
    return None


def _response_status(messages: list[dict[str, Any]]) -> int | None:
    for msg in messages:
        if msg["type"] == "http.response.start":
            return int(msg["status"])
    return None


# ---------------------------------------------------------------------------
# Origin trust
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_with_whitelisted_origin_is_allowed() -> None:
    """``Origin`` matching ``settings.cors_origins[0]`` must reach the app."""
    from app.config import settings

    scope = _build_scope(
        method="POST",
        headers=[(b"origin", settings.cors_origins[0].encode("latin-1"))],
    )
    messages, downstream_called = await _drive_middleware(scope)
    assert downstream_called, "whitelisted origin must NOT trip CSRF"
    assert _response_status(messages) is None, (
        "downstream was reached; CSRF must not have started a response"
    )


@pytest.mark.asyncio
async def test_post_with_untrusted_origin_returns_403() -> None:
    """Plan §6b.3 — reject state-changing requests whose ``Origin`` is
    not in the whitelist. The 403 must come BEFORE auth/database
    processing — CSRF is the outermost check.
    """
    scope = _build_scope(
        method="POST",
        headers=[(b"origin", b"http://evil.example.com")],
    )
    messages, downstream_called = await _drive_middleware(scope)
    assert not downstream_called, "untrusted origin must short-circuit before the app"
    assert _response_status(messages) == 403, (
        f"untrusted origin must return 403, got {_response_status(messages)}"
    )
    body = b"".join(m["body"] for m in messages if m["type"] == "http.response.body")
    assert b"CSRF" in body, "403 body must surface the CSRF marker"


@pytest.mark.asyncio
async def test_post_with_no_origin_is_allowed() -> None:
    """Server-to-server callers (curl, scripts) don't send ``Origin``.

    The middleware treats missing ``Origin`` as allowed — only
    *present-but-untrusted* origins are rejected.
    """
    scope = _build_scope(method="POST")
    _messages, downstream_called = await _drive_middleware(scope)
    assert downstream_called, "missing Origin must NOT trip CSRF"


@pytest.mark.asyncio
async def test_post_with_same_origin_is_allowed() -> None:
    """``Origin`` netloc == ``Host`` header → treated as same-origin, even
    if the explicit whitelist doesn't list it. Keeps local dev
    frictionless without weakening the cross-site defence.
    """
    scope = _build_scope(
        method="POST",
        host=b"testserver",
        headers=[(b"origin", b"http://testserver"), (b"host", b"testserver")],
    )
    _messages, downstream_called = await _drive_middleware(scope)
    assert downstream_called, "same-origin Origin must NOT trip CSRF"


# ---------------------------------------------------------------------------
# Method / path exemptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_with_untrusted_origin_is_allowed() -> None:
    """GET (and HEAD/OPTIONS) are not state-changing — CSRF doesn't
    apply. Browsers enforce read-side isolation via CORS, not CSRF.
    """
    scope = _build_scope(
        method="GET",
        path="/reports",
        headers=[(b"origin", b"http://evil.example.com")],
    )
    _messages, downstream_called = await _drive_middleware(scope)
    assert downstream_called, "GETs are exempt from CSRF"


@pytest.mark.asyncio
async def test_metrics_endpoint_is_exempt() -> None:
    """Prometheus scrapers send GETs (which are exempt) but we also
    carve ``/metrics`` out explicitly in case someone adds a POST
    probe. Verify with an untrusted ``Origin`` that 403 is *not*
    triggered even if the request is POST-shaped.
    """
    scope = _build_scope(
        method="POST",
        path="/metrics",
        headers=[(b"origin", b"http://evil.example.com")],
    )
    _messages, downstream_called = await _drive_middleware(scope)
    assert downstream_called, "/metrics must bypass CSRF"


@pytest.mark.asyncio
async def test_health_endpoint_is_exempt() -> None:
    """Load balancers probe ``/health`` from anywhere — ``Origin`` is
    never set in those calls, but if someone configures a probe with
    one we still want to pass.
    """
    scope = _build_scope(
        method="POST",
        path="/health",
        headers=[(b"origin", b"http://monitoring.internal")],
    )
    _messages, downstream_called = await _drive_middleware(scope)
    assert downstream_called, "/health must bypass CSRF"


# ---------------------------------------------------------------------------
# Setting toggle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_setting_lets_everything_through() -> None:
    """``settings.csrf_enabled = False`` disables the gate entirely —
    useful for tests / scripts. The middleware reads the setting on
    every request via ``settings.csrf_enabled``.
    """
    scope = _build_scope(
        method="POST",
        headers=[(b"origin", b"http://evil.example.com")],
    )
    _messages, downstream_called = await _drive_middleware(scope, csrf_enabled=False)
    assert downstream_called, "csrf_enabled=False must let untrusted origins through"

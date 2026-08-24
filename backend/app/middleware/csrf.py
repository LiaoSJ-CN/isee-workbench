"""CSRF defence middleware (批 6b.3).

Rejects ``POST`` / ``PUT`` / ``PATCH`` / ``DELETE`` requests whose
``Origin`` header is present but doesn't match the configured
whitelist. State-changing methods from an unknown origin are the
canonical CSRF attack surface; defence-in-depth on top of the
``Authorization: Bearer`` header (which browsers never auto-attach
cross-origin).

Two design notes worth keeping in mind while editing this file:

1. **Missing Origin is allowed** — server-to-server callers (curl,
   ``httpx``, CI scripts) don't send ``Origin``. The middleware only
   rejects when the header is *present* but *untrusted*. A stricter
   "Origin must be present" rule would break every script that POSTs
   to the API.

2. **Same-origin is always allowed** — when the request's ``Host``
   header matches the ``Origin`` netloc (e.g. both ``localhost:8000``)
   we treat it as same-origin even if ``Host`` isn't in the explicit
   whitelist. This keeps local development frictionless when the
   operator hasn't listed ``http://localhost:8000`` in
   ``settings.cors_origins``.

Skipped paths (method-irrelevant): ``/metrics``, ``/health``,
``/docs``, ``/openapi.json``, ``/redoc`` — Prometheus scrape + the
Swagger UI don't carry ``Origin`` from a hostile site.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any
from urllib.parse import urlparse

from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings

logger = logging.getLogger(__name__)

# State-changing methods. ``GET`` / ``HEAD`` / ``OPTIONS`` are always
# allowed — CORS preflight (OPTIONS) is what the browser uses to
# verify a cross-origin write, so we must let it through.
_STATE_CHANGING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Paths exempt from CSRF enforcement entirely. The Prometheus scrape
# endpoint and the OpenAPI surface don't accept mutations; the SPA
# itself doesn't talk to these directly.
_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/metrics",
        "/health",
        "/docs",
        "/docs/oauth2-redirect",
        "/openapi.json",
        "/redoc",
    }
)


class CSRFMiddleware:
    """Reject state-changing requests from untrusted origins.

    The whitelist is read from ``settings.cors_origins`` at startup.
    Operators in production should keep that list tight; if the SPA
    is on ``https://app.example.com`` and the API on
    ``https://api.example.com``, list only the SPA's origin (CORS
    preflight will block the API from a third-party page anyway).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._whitelist: set[str] = set(settings.cors_origins or [])
        if settings.csrf_enabled:
            logger.info(
                "CSRF middleware enabled with whitelist: %s",
                sorted(self._whitelist),
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Read the setting per request so tests can flip it via
        # ``monkeypatch.setattr(settings, "csrf_enabled", False)``
        # without restarting the app. The whitelist is still cached
        # at startup — flipping ``cors_origins`` requires a restart,
        # which is the intended behaviour for that knob.
        if (
            not settings.csrf_enabled
            or scope["type"] != "http"
            or scope["method"] not in _STATE_CHANGING_METHODS
        ):
            await self.app(scope, receive, send)
            return

        # Path-based exemption — Prometheus scrapers + Swagger UI don't
        # carry an Origin header, and they're never state-changing from
        # the operator's perspective.
        path = scope.get("path", "")
        if path in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        origin = self._header_value(scope, b"origin")
        if not origin:
            # Missing Origin — let it through (server-to-server callers).
            await self.app(scope, receive, send)
            return

        if self._is_trusted(origin, scope):
            await self.app(scope, receive, send)
            return

        # Reject with 403 + a plain JSON body. We deliberately don't
        # include CORS headers on this response — the browser already
        # knows the request was bad.
        logger.warning(
            "CSRF rejection: method=%s path=%s origin=%s",
            scope["method"],
            path,
            origin,
        )
        await self._send_forbidden(send)

    # ---- helpers --------------------------------------------------------

    @staticmethod
    def _header_value(scope: Scope, name: bytes) -> str | None:
        for header_name, header_value in scope.get("headers", []):
            if header_name == name:
                try:
                    decoded: str = header_value.decode("latin-1")
                    return decoded
                except UnicodeDecodeError:
                    return None
        return None

    def _is_trusted(self, origin: str, scope: Scope) -> bool:
        """True iff ``origin`` is in the configured whitelist OR is
        same-origin with the request (Origin netloc == Host header)."""
        if origin in self._whitelist:
            return True

        host = self._header_value(scope, b"host")
        if not host:
            return False

        # Compare netloc only — scheme is normalised away so a request
        # arriving over http vs. https against the same host doesn't
        # accidentally fail (we trust transport-layer TLS / proxy
        # headers for that).
        try:
            origin_netloc = urlparse(origin).netloc.lower()
        except ValueError:
            return False
        return origin_netloc == host.lower()

    @staticmethod
    async def _send_forbidden(send: Send) -> None:
        body = b'{"detail":"CSRF: origin not allowed"}'
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    # Expose the body type so callers can monkey-patch / introspect.
    _MutableMapping = MutableMapping
    _Any = Any

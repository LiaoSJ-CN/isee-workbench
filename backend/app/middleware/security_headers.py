"""Security response headers middleware (P5 / SEC-5).

Adds a baseline set of HTTP security headers to every response.
Headers are configurable via ``settings.security_headers_enabled``.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings

_SECURITY_HEADERS: list[tuple[str, str]] = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("X-XSS-Protection", "0"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
]


class SecurityHeadersMiddleware:
    """Attach baseline security headers to every HTTP response (P5 / SEC-5)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not settings.security_headers_enabled:
            await self.app(scope, receive, send)
            return

        async def _send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers: list[Any] = list(message.get("headers", []))
                existing = {h[0].decode("latin-1").lower() for h in headers}
                for name, value in _SECURITY_HEADERS:
                    if name.lower() not in existing:
                        headers.append((name.encode("latin-1"), value.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, _send)

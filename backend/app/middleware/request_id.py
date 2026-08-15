"""Request ID middleware + logging integration (批 6a).

Reads ``X-Request-ID`` from the incoming request (generating
``uuid4().hex`` when absent), exposes it via a ``contextvars.ContextVar``
so any code running while a request is in flight — including downstream
middleware, route handlers, services, and background tasks spawned from
a handler — can read it, and echoes it back in the response header.

Logging integration is opt-in: :func:`install_request_id_log_factory`
is called once at process startup (from the FastAPI lifespan) and
attaches a ``request_id`` attribute to every :class:`logging.LogRecord`.
Outside of HTTP requests (lifespan setup, scheduler ticks, etc.) the
attribute falls back to ``"-"`` so existing logs aren't affected.

Why a contextvar (not just ``request.state``):
    ``request.state`` is only visible inside the ASGI request handler.
    The logging factory runs in whatever task is emitting the log
    (e.g. a worker thread inside an async route), and that task
    doesn't have access to ``request.state``. ``ContextVar`` is the
    asyncio-safe way to propagate the id across the call chain.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Header name in two forms: bytes for the ASGI scope, str for the response.
_HEADER_BYTES = b"x-request-id"
_HEADER_STR = "X-Request-ID"

# Module-level contextvar — shared between middleware (writer) and the
# logging factory (reader). default="-" preserves existing log lines for
# code paths that never go through HTTP (lifespan, scheduler tick).
_request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Sentinel exposed for type annotations / tests.
NO_REQUEST_ID = "-"


def get_request_id() -> str:
    """Return the current request id, or ``"-"`` if no request is in flight."""
    return _request_id_var.get()


def install_request_id_log_factory() -> None:
    """Install a ``LogRecord`` factory that attaches ``request_id`` to every record.

    Idempotent — calling more than once is a no-op (the factory already
    wraps the original). The factory is installed from the FastAPI
    lifespan so log lines emitted anywhere in the web process carry the
    id. The scheduler sidecar doesn't need this; its logs have no
    request context and fall through with ``"-"``.
    """
    current = logging.getLogRecordFactory()
    if getattr(current, "_isee_request_id_factory", False):
        return  # Already installed.

    base_factory = current

    def _factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base_factory(*args, **kwargs)
        record.request_id = _request_id_var.get()
        return record

    _factory._isee_request_id_factory = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(_factory)


class RequestIDMiddleware:
    """Read/write ``X-Request-ID`` and propagate it via the contextvar.

    Registered as the outermost middleware so every other middleware
    (CORS, proxy headers, security headers, …) sees the id in its logs.
    Non-HTTP scopes (lifespan, websocket) are passed through unchanged.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._extract_request_id(scope.get("headers", []))
        if not request_id:
            request_id = uuid.uuid4().hex

        # Set the contextvar so downstream code (and the logging factory)
        # can read it. Starlette populates ``Request.state`` from
        # ``scope["state"]``, so setting here also surfaces
        # ``request.state.request_id`` to FastAPI handlers.
        token = _request_id_var.set(request_id)
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = request_id

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append(
                    (_HEADER_STR.encode("latin-1"), request_id.encode("latin-1"))
                )
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            _request_id_var.reset(token)

    @staticmethod
    def _extract_request_id(headers: list[tuple[bytes, bytes]]) -> str:
        """Return the first non-empty ``X-Request-ID`` value, or ``""``.

        Header lookup is case-insensitive per RFC 7230 — ASGI lowercases
        everything, so a single byte compare is enough. Multiple values
        are tolerated (first wins) so a buggy proxy that double-sets the
        header doesn't crash the request.
        """
        for name, value in headers:
            if name == _HEADER_BYTES:
                decoded = value.decode("latin-1").strip()
                if decoded:
                    return decoded
        return ""

"""Sliding-window rate limiter (P3 / PY-8: DB-backed).

Pre-P3: counters were kept in a ``defaultdict(list)`` in process memory.
With ``gunicorn -w N`` (or any multi-worker deployment) each worker
maintained its own dict, so 10 attempts × N workers = 10N actual login
tries — the rate limit was effectively meaningless.

P3: each attempt is inserted into ``rate_limit_events``. All workers
read/write the same table, so the limit is global. The trade-off is
one INSERT and one COUNT per call (and one DELETE of expired rows
per call) — acceptable for the login path which is already
DB-bounded.

Usage::

    from app.config import settings
    from app.middleware.rate_limit import RateLimiter

    login_limiter = RateLimiter(
        max_requests=settings.login_rate_limit,
        window_seconds=60,
    )

    @router.post("/login")
    def login(req: LoginRequest, request: Request):
        if login_limiter.is_rate_limited(request.client.host):
            raise HTTPException(429, "Too many login attempts")
        ...
"""

from __future__ import annotations

from time import time
from typing import Any, Callable, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from app.database import SessionLocal
from app.models.rate_limit import RateLimitEvent


class RateLimiter:
    """Sliding-window rate limiter keyed on an arbitrary string.

    Each call to ``is_rate_limited(key)``:
    1. Prunes events for ``key`` older than the window.
    2. Inserts a fresh event.
    3. Counts remaining events for ``key``.
    4. Returns whether the post-insert count exceeds ``max_requests``.

    Combined check-and-record — must be called **once** per request.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: int = 60,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._max_requests = max_requests
        self._window = window_seconds
        self._session_factory = session_factory

    def is_rate_limited(self, key: str) -> bool:
        now = time()
        floor = now - self._window
        db = self._session_factory()
        try:
            # Prune expired rows for this key.
            db.execute(
                delete(RateLimitEvent).where(RateLimitEvent.key == key, RateLimitEvent.ts < floor)
            )
            # Record this attempt. SQLAlchemy plugin types Column[Float]
            # as Float | None even though we declared it NOT NULL; suppress
            # the false positive at the call site.
            db.add(RateLimitEvent(key=key, ts=now))  # type: ignore[arg-type]
            db.commit()
            # Count post-insert.
            count = db.scalar(
                select(func.count()).select_from(RateLimitEvent).where(RateLimitEvent.key == key)
            )
            assert count is not None  # count() is non-null for non-empty filter
            return count > self._max_requests
        finally:
            db.close()


def prune_older_than(seconds: int, session_factory: Callable[[], Session] = SessionLocal) -> int:
    """Safety-net cleanup: drop events older than *seconds* across all keys.

    Returns the number of rows removed. Intended for periodic invocation
    (e.g. a startup task) so the table doesn't grow unbounded if a key
    goes silent. Per-call pruning in ``is_rate_limited`` already keeps
    the active bucket bounded, so this is a backstop, not a hot path.
    """
    floor = time() - seconds
    db = session_factory()
    try:
        result = cast(
            CursorResult[Any],
            db.execute(delete(RateLimitEvent).where(RateLimitEvent.ts < floor)),
        )
        db.commit()
        return int(result.rowcount or 0)
    finally:
        db.close()


# Re-export sessionmaker import path for callers that need to inject
# a custom factory (e.g. tests using a tmp sqlite).
__all__ = ["RateLimiter", "prune_older_than", "sessionmaker"]

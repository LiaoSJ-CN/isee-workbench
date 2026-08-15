"""SQLAlchemy engine cache for the report generator (批 5.2).

Module-level cache, keyed by ``DataSource.id``. Reports that share a
``DataSource`` reuse one pool of connections across calls, avoiding
the TCP + auth handshake tax on every generation. Eviction is
explicit: callers that mutate a ``DataSource`` row must call
:func:`evict_engine` so the next call rebuilds the engine with the
new connection URL.

Tests that need to clear the cache (so stale engines don't leak
between cases) reference ``_engine_cache`` directly — see
``tests/conftest.py``.
"""

from __future__ import annotations

import re
import threading
from typing import cast

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.models.data_source import DataSource
from app.services.connection import build_connection_url

# Filename-safe subset: word chars, CJK Unified Ideographs, hyphen, dot.
_SAFE_FILENAME_RE = re.compile(r"[^\w一-鿿\-.]+")
_FILENAME_MAX_LEN = 200

# Module-level engine cache, keyed by DataSource.id.
_engine_cache: dict[int, Engine] = {}
_engine_cache_lock = threading.Lock()


def get_or_create_engine(data_source: DataSource) -> Engine:
    """Return the cached Engine for ``data_source``, building one on miss.

    Double-checked locking keeps the fast path (cache hit) lock-free
    while staying safe under concurrent first-time access.
    ``pool_pre_ping=True`` on remote backends makes SQLAlchemy discard
    stale pooled connections (e.g. after the remote DB restarts)
    instead of failing the next query.

    Underscore-prefix-free public name; the legacy ``_get_or_create_engine``
    import path is re-exported from the package ``__init__`` so existing
    callers (routers, tests) keep working.
    """
    cached = _engine_cache.get(cast(int, data_source.id))
    if cached is not None:
        return cached
    with _engine_cache_lock:
        cached = _engine_cache.get(cast(int, data_source.id))
        if cached is not None:
            return cached
        url = build_connection_url(data_source)
        if data_source.db_type == "sqlite":
            engine = create_engine(url)
        else:
            engine = create_engine(
                url,
                connect_args={"connect_timeout": 30},
                pool_pre_ping=True,
            )
        _engine_cache[cast(int, data_source.id)] = engine
        return engine


def evict_engine(data_source_id: int) -> None:
    """Drop the cached engine for ``data_source_id`` and dispose its pool.

    Call this after updating or deleting a DataSource so the next
    call rebuilds the engine with the new connection URL.
    """
    with _engine_cache_lock:
        engine = _engine_cache.pop(data_source_id, None)
        if engine is not None:
            engine.dispose()


def safe_filename(name: str, fallback: str = "report") -> str:
    """Sanitize a string for use as a filename component.

    Strips path separators and other unsafe characters to prevent
    path traversal (e.g. ``../../etc/passwd``). Keeps word chars,
    CJK ideographs, hyphen, and dot. Falls back to ``fallback`` when
    the result would be empty, and caps length to avoid OS limits.

    Underscore-prefix-free public name; ``_safe_filename`` import path
    is re-exported from the package ``__init__`` for backwards
    compatibility with tests that imported the helper directly.
    """
    sanitized = _SAFE_FILENAME_RE.sub("_", name).strip("._") or fallback
    return sanitized[:_FILENAME_MAX_LEN]


# Underscore-prefixed aliases — the old module-level names that some
# callers (routers/explorer.py, tests/conftest.py, tests/test_*.py)
# still import. Re-exported from the package __init__ so we don't
# break the legacy import path. Prefer the unprefixed names in new code.
_get_or_create_engine = get_or_create_engine
_safe_filename = safe_filename

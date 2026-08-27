"""Module-level SQLAlchemy engine cache behavior.

The cache is process-global, so every test here uses the
``engine_cache_cleanup`` fixture to start with an empty cache and tear
down any engines it created.
"""

from types import SimpleNamespace
from typing import Any

from app.services.report_generator import (
    ReportGenerator,
    _engine_cache,
    _get_or_create_engine,
    evict_engine,
)


def _fake_sqlite_source(source_id: int, db_path: str) -> Any:
    """Minimal DataSource stand-in: only the fields the cache and
    ``build_connection_url`` actually read.

    批 12: also carries ``name`` (used by the pool-metrics label) and
    ``db_type`` (used to pick the health heuristic).
    """
    return SimpleNamespace(
        id=source_id,
        name=f"pytest-cache-{source_id}",
        db_type="sqlite",
        host="",
        port=0,
        database=db_path,
        username="",
        password="",
    )


def test_repeated_lookup_returns_same_engine(engine_cache_cleanup, tmp_sqlite_path) -> None:
    ds = _fake_sqlite_source(90001, tmp_sqlite_path)
    e1 = _get_or_create_engine(ds)
    e2 = _get_or_create_engine(ds)
    e3 = _get_or_create_engine(ds)
    assert e1 is e2 is e3
    assert len(_engine_cache) == 1
    assert 90001 in _engine_cache


def test_different_sources_get_different_engines(engine_cache_cleanup, tmp_sqlite_path) -> None:
    ds_a = _fake_sqlite_source(90010, tmp_sqlite_path)
    ds_b = _fake_sqlite_source(90011, tmp_sqlite_path + "_b")
    e_a = _get_or_create_engine(ds_a)
    e_b = _get_or_create_engine(ds_b)
    assert e_a is not e_b
    assert {90010, 90011} <= set(_engine_cache.keys())


def test_cached_engine_actually_runs_queries(engine_cache_cleanup, tmp_sqlite_path) -> None:
    ds = _fake_sqlite_source(90020, tmp_sqlite_path)
    eng = _get_or_create_engine(ds)
    from sqlalchemy import text

    with eng.connect() as conn:
        conn.execute(text("CREATE TABLE t (x INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (1), (2), (3)"))
        conn.commit()
        total = conn.execute(text("SELECT SUM(x) FROM t")).scalar()
    assert total == 6


def test_report_generator_reuses_cached_engine(engine_cache_cleanup, tmp_sqlite_path) -> None:
    """The whole point of the cache: ReportGenerator open/close must
    NOT create or dispose engines per call."""
    ds = _fake_sqlite_source(90030, tmp_sqlite_path)
    e0 = _get_or_create_engine(ds)

    engines_seen = set()
    for _ in range(5):
        g = ReportGenerator(ds).__enter__()
        engines_seen.add(id(g.engine))
        g.__exit__(None, None, None)

    assert engines_seen == {id(e0)}, (
        f"expected all 5 cycles to reuse the same engine, got {len(engines_seen)} ids"
    )
    # And the cache must still contain exactly one entry.
    assert len(_engine_cache) == 1


def test_evict_engine_drops_entry_and_triggers_rebuild(
    engine_cache_cleanup, tmp_sqlite_path
) -> None:
    ds = _fake_sqlite_source(90040, tmp_sqlite_path)
    e1 = _get_or_create_engine(ds)
    evict_engine(90040)
    assert 90040 not in _engine_cache
    e2 = _get_or_create_engine(ds)
    assert e1 is not e2, "after eviction, next lookup must build a fresh engine"


def test_evict_engine_unknown_id_is_noop(engine_cache_cleanup, tmp_sqlite_path) -> None:
    ds = _fake_sqlite_source(90050, tmp_sqlite_path)
    _get_or_create_engine(ds)
    before = set(_engine_cache.keys())
    evict_engine(999999)  # not in cache
    assert set(_engine_cache.keys()) == before


def test_evict_engine_calls_dispose(engine_cache_cleanup, tmp_sqlite_path) -> None:
    """evict_engine must dispose the engine so pooled connections are
    released (matters for the CRUD path where the source is being
    deleted and we want no leaked file handles)."""
    ds = _fake_sqlite_source(90060, tmp_sqlite_path)
    _get_or_create_engine(ds)
    eng = _engine_cache[90060]
    # Spy on dispose by swapping the method on the instance.
    called = {"n": 0}
    original_dispose = eng.dispose

    def spy_dispose():
        called["n"] += 1
        return original_dispose()

    eng.dispose = spy_dispose  # type: ignore[method-assign]
    try:
        evict_engine(90060)
        assert called["n"] == 1
        assert 90060 not in _engine_cache
    finally:
        # Restore (in case the engine is still referenced somewhere).
        eng.dispose = original_dispose  # type: ignore[method-assign]

"""Unit tests for the per-DataSource connection-pool metrics store (批 12).

Covers:
- registration / unregistration round-trip
- checkout / checkin / invalidate event counters update correctly
- ``engine.pool.checkedout()`` drives the live ``active`` snapshot
- leak detection (checkout without checkin leaves the cumulative
  counters showing an imbalance — the pool's source-of-truth itself
  would also reflect a real leak, but we assert on the counters
  because that's what the admin page reads)
- health thresholds (green / yellow / red) for sqlite vs remote + the
  timeout / avg-held / saturation heuristics
- listeners attached to an unregistered engine are silent no-ops
- the engine.py wiring (``get_or_create_engine`` registers,
  ``evict_engine`` unregisters)

SQLite + :class:`~sqlalchemy.pool.QueuePool` is used for the
event-driven tests because SQLite's default SingletonThreadPool
doesn't fire checkout/checkin events. The unit tests for the store
itself don't need a real pool — they drive the record_* methods
directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from app.services import connection_metrics as cm
from app.services.connection_metrics import (
    attach_event_listeners,
    get_all_stats,
    register_engine,
    reset_for_testing,
    unregister_engine,
)
from app.services.report_generator import _get_or_create_engine, evict_engine

# ---- fixtures ------------------------------------------------------------


@pytest.fixture
def metrics_store_clean():
    """Reset the in-memory + Prometheus state before AND after the test."""
    reset_for_testing()
    yield
    reset_for_testing()


def _queue_pool_sqlite_engine(tmp_sqlite_path: str, pool_size: int = 5):
    """SQLite engine with QueuePool so checkout/checkin events fire.

    The default SQLite pool (SingletonThreadPool) doesn't emit
    pool events, which means the metrics store would never see any
    traffic. QueuePool gives us real checkout/checkin cycles.
    """
    return create_engine(
        f"sqlite:///{tmp_sqlite_path}",
        poolclass=QueuePool,
        pool_size=pool_size,
    )


# ---- registration --------------------------------------------------------


def test_register_then_get_stats_returns_pool_entry(
    metrics_store_clean, tmp_sqlite_path
):
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path)
    try:
        register_engine(
            engine, data_source_id=42, name="sales-ds", db_type="sqlite"
        )
        stats = get_all_stats()
        assert len(stats) == 1
        s = stats[0]
        assert s.data_source_id == 42
        assert s.name == "sales-ds"
        assert s.db_type == "sqlite"
        assert s.active == 0
        assert s.checkouts_total == 0
        assert s.health == "green"
    finally:
        unregister_engine(engine)
        engine.dispose()


def test_unregister_drops_state(metrics_store_clean, tmp_sqlite_path):
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path)
    register_engine(engine, data_source_id=1, name="t", db_type="sqlite")
    attach_event_listeners(engine)
    assert len(get_all_stats()) == 1

    unregister_engine(engine)
    engine.dispose()

    assert get_all_stats() == []


def test_unregister_unknown_engine_is_noop(metrics_store_clean, tmp_sqlite_path):
    """Calling unregister on an engine we never tracked must not raise."""
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path)
    try:
        unregister_engine(engine)  # no prior register_engine
        assert get_all_stats() == []
    finally:
        engine.dispose()


# ---- event-driven counters ----------------------------------------------


def test_checkout_and_checkin_increment_counters(
    metrics_store_clean, tmp_sqlite_path
):
    """Real pool events drive the counters — full integration path."""
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path)
    try:
        register_engine(engine, data_source_id=10, name="t", db_type="sqlite")
        attach_event_listeners(engine)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        # After close, the connection is returned to the pool.
        s = get_all_stats()[0]
        assert s.checkouts_total == 1
        assert s.checkins_total == 1
        # active reads from engine.pool.checkedout(), not from our
        # counter — both should agree here (1 - 1 = 0 in counters,
        # and pool.checkedout() == 0 because the conn was returned).
        assert s.active == 0
    finally:
        unregister_engine(engine)
        engine.dispose()


def test_invalidate_event_increments_counter(
    metrics_store_clean, tmp_sqlite_path
):
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path)
    try:
        register_engine(engine, data_source_id=11, name="t", db_type="sqlite")
        attach_event_listeners(engine)
        # Borrow a conn, force invalidate, close. The checkin event
        # still fires on close but with the connection already
        # invalidated — invalidate increments before that.
        conn = engine.connect()
        conn.invalidate()
        conn.close()
        s = get_all_stats()[0]
        assert s.invalidations_total >= 1
    finally:
        unregister_engine(engine)
        engine.dispose()


def test_event_listener_noop_for_unregistered_engine(
    metrics_store_clean, tmp_sqlite_path
):
    """Engines without register_engine must not blow up on events."""
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path)
    try:
        attach_event_listeners(engine)  # no register_engine first
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        # No state for this engine — get_all_stats stays empty.
        assert get_all_stats() == []
    finally:
        engine.dispose()


# ---- store-level (no real pool) ------------------------------------------


def test_record_checkout_then_checkin_balances_counters(
    metrics_store_clean, tmp_sqlite_path
):
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path)
    try:
        register_engine(engine, data_source_id=20, name="t", db_type="sqlite")
        mock = object()
        cm._store.record_checkout(engine, mock)
        cm._store.record_checkin(engine, mock)
        s = get_all_stats()[0]
        assert s.checkouts_total == 1
        assert s.checkins_total == 1
    finally:
        unregister_engine(engine)
        engine.dispose()


def test_leak_detection_via_unmatched_checkout(
    metrics_store_clean, tmp_sqlite_path
):
    """A checkout without a paired checkin shows up as an imbalance
    in the cumulative counters. (The engine.pool.checkedout() source
    of truth would also reflect a real leak; we test the counters
    because that's what the admin page reads.)"""
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path)
    try:
        register_engine(engine, data_source_id=21, name="t", db_type="postgresql")
        mock = object()
        cm._store.record_checkout(engine, mock)
        # intentionally NO record_checkin
        s = get_all_stats()[0]
        assert s.checkouts_total == 1
        assert s.checkins_total == 0
        # Cleanup so the next test starts clean.
        cm._store.record_checkin(engine, mock)
    finally:
        unregister_engine(engine)
        engine.dispose()


def test_record_timeout_increments_counter(
    metrics_store_clean, tmp_sqlite_path
):
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path)
    try:
        register_engine(engine, data_source_id=22, name="t", db_type="postgresql")
        cm._store.record_checkout(engine, object())
        cm._store.record_timeout(engine)
        s = get_all_stats()[0]
        assert s.timeouts_total == 1
        # Single timeout on single checkout -> 100% timeout rate,
        # which on a remote DS is "red".
        assert s.health == "red"
    finally:
        unregister_engine(engine)
        engine.dispose()


# ---- health thresholds ---------------------------------------------------


def test_health_green_for_sqlite_even_under_load(
    metrics_store_clean, tmp_sqlite_path
):
    """SQLite always reports green — SingletonThreadPool has no pressure."""
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path)
    try:
        register_engine(engine, data_source_id=30, name="t", db_type="sqlite")
        # Manufacture a "bad" metric snapshot to prove SQLite short-circuits.
        state = cm._store._states[30]
        state.timeouts_total = 100
        state.checkouts_total = 1
        s = get_all_stats()[0]
        assert s.health == "green"
    finally:
        unregister_engine(engine)
        engine.dispose()


def test_health_yellow_when_avg_held_high(
    metrics_store_clean, tmp_sqlite_path
):
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path)
    try:
        register_engine(engine, data_source_id=31, name="t", db_type="postgresql")
        state = cm._store._states[31]
        state.held_seconds_sum = 4.0  # 4 seconds held across 1 conn
        state.held_seconds_count = 1
        state.checkouts_total = 1
        s = get_all_stats()[0]
        assert s.health == "yellow"
    finally:
        unregister_engine(engine)
        engine.dispose()


def test_health_red_when_timeout_rate_high(
    metrics_store_clean, tmp_sqlite_path
):
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path, pool_size=5)
    try:
        register_engine(engine, data_source_id=32, name="t", db_type="postgresql")
        state = cm._store._states[32]
        state.timeouts_total = 10
        state.checkouts_total = 100  # 10 % timeout rate
        s = get_all_stats()[0]
        assert s.health == "red"
    finally:
        unregister_engine(engine)
        engine.dispose()


def test_health_yellow_when_pool_saturated(
    metrics_store_clean, tmp_sqlite_path
):
    """active >= pool_size should warn even if timeout_rate is low."""
    engine = _queue_pool_sqlite_engine(tmp_sqlite_path, pool_size=2)
    try:
        register_engine(engine, data_source_id=33, name="t", db_type="postgresql")
        c1 = engine.connect()
        c2 = engine.connect()
        try:
            s = get_all_stats()[0]
            assert s.active >= s.pool_size
            assert s.health == "yellow"
        finally:
            c2.close()
            c1.close()
    finally:
        unregister_engine(engine)
        engine.dispose()


# ---- engine.py wiring ----------------------------------------------------


def test_get_or_create_engine_registers_metrics(
    engine_cache_cleanup, tmp_sqlite_path
):
    """``get_or_create_engine`` must call ``register_engine`` so the
    pool shows up in admin stats without any extra wiring."""
    ds = SimpleNamespace(
        id=90001,
        name="wireup-ds",
        db_type="sqlite",
        host="",
        port=0,
        database=tmp_sqlite_path,
        username="",
        password="",
    )
    _get_or_create_engine(ds)
    try:
        stats = get_all_stats()
        assert any(s.data_source_id == 90001 and s.name == "wireup-ds" for s in stats)
    finally:
        evict_engine(90001)
        assert get_all_stats() == []


def test_evict_engine_unregisters_metrics(
    engine_cache_cleanup, tmp_sqlite_path
):
    ds = SimpleNamespace(
        id=90002,
        name="wireup-evict-ds",
        db_type="sqlite",
        host="",
        port=0,
        database=tmp_sqlite_path,
        username="",
        password="",
    )
    _get_or_create_engine(ds)
    assert any(s.data_source_id == 90002 for s in get_all_stats())
    evict_engine(90002)
    assert not any(s.data_source_id == 90002 for s in get_all_stats())


def test_re_register_after_evict_starts_fresh_state(
    engine_cache_cleanup, tmp_sqlite_path
):
    """After evict, the next ``get_or_create_engine`` must start with
    zeroed counters — not bleed the previous engine's history."""
    ds = SimpleNamespace(
        id=90003,
        name="rebuild-ds",
        db_type="sqlite",
        host="",
        port=0,
        database=tmp_sqlite_path,
        username="",
        password="",
    )
    first = _get_or_create_engine(ds)
    # Simulate some traffic on the first engine.
    cm._store.record_checkout(first, object())
    evict_engine(90003)
    # Rebuild — the new engine must start clean.
    _get_or_create_engine(ds)
    try:
        s = next(s for s in get_all_stats() if s.data_source_id == 90003)
        assert s.checkouts_total == 0
        assert s.checkins_total == 0
    finally:
        evict_engine(90003)

"""Per-DataSource connection-pool metrics (批 12).

Surfaces pool activity (active connections, checkout / checkin / invalidate
events) plus a rolling 24h history to back the admin monitoring page and
the Prometheus ``/metrics`` endpoint.

The active count comes from ``engine.pool.checkedout()`` — SQLAlchemy's
pool already maintains it as ground truth, so we don't double-count via
events. We still listen to ``checkout`` / ``checkin`` / ``invalidate`` to
drive the rate counters and the 5-minute history buckets (which power the
admin chart).

Health heuristic:

* ``green``  : SQLite always (single-thread pool, no pressure). For others,
  zero timeouts, avg held < 1s, active ≤ pool_size × 0.8.
* ``yellow``: timeout_rate < 5 %, avg held < 5s, active ≤ pool_size.
* ``red``   : anything worse.

The threshold constants live next to :func:`_MetricsStore._compute_health`
so they're easy to tune without grep hunting.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, cast

from prometheus_client import Counter, Gauge
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# 24h at 5-minute granularity = 288 buckets.
_BUCKET_SECONDS = 300
_HISTORY_BUCKETS = (24 * 60 * 60) // _BUCKET_SECONDS  # 288


# ---- Prometheus metrics --------------------------------------------------

# Label values are stringified so they show up cleanly in Grafana
# queries; numeric ids make for bad dashboard filter widgets.
_active_gauge = Gauge(
    "ds_pool_active_connections",
    "Currently checked-out (in-use) connections per DataSource.",
    labelnames=("data_source_id", "name"),
)
_checkout_counter = Counter(
    "ds_pool_checkout_total",
    "Cumulative successful checkout events per DataSource.",
    labelnames=("data_source_id", "name"),
)
_checkin_counter = Counter(
    "ds_pool_checkin_total",
    "Cumulative checkin (return-to-pool) events per DataSource.",
    labelnames=("data_source_id", "name"),
)
_invalidate_counter = Counter(
    "ds_pool_invalidate_total",
    "Cumulative connection invalidations per DataSource.",
    labelnames=("data_source_id", "name"),
)


# ---- Data classes --------------------------------------------------------

@dataclass(frozen=True)
class BucketStats:
    """Aggregated counts for one 5-minute history bucket."""

    bucket_ts: int  # unix seconds at the start of the bucket
    checkouts: int
    checkins: int
    invalidations: int


@dataclass(frozen=True)
class PoolStats:
    """Live snapshot of pool metrics for one DataSource."""

    data_source_id: int
    name: str
    db_type: str
    active: int
    pool_size: int
    checkouts_total: int
    checkins_total: int
    invalidations_total: int
    timeouts_total: int
    avg_held_ms: float
    timeout_rate: float
    health: str  # "green" | "yellow" | "red"
    history: list[BucketStats]


@dataclass
class _SourceState:
    """Per-DataSource mutable state owned by the store."""

    engine: Engine
    data_source_id: int
    name: str
    db_type: str
    checkouts_total: int = 0
    checkins_total: int = 0
    invalidations_total: int = 0
    held_seconds_sum: float = 0.0
    held_seconds_count: int = 0
    timeouts_total: int = 0
    history: deque[BucketStats] = field(
        default_factory=lambda: deque(maxlen=_HISTORY_BUCKETS)
    )
    # Map id(dbapi_conn) -> monotonic checkout timestamp. Held-time
    # tracking relies on this; checkin pops the entry and accumulates
    # the elapsed monotonic seconds into held_seconds_sum.
    _in_flight: dict[int, float] = field(default_factory=dict)


# ---- Store ---------------------------------------------------------------


class _MetricsStore:
    """Thread-safe in-memory + Prometheus-backed pool metrics store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[int, _SourceState] = {}  # ds_id -> state
        self._engine_ids: dict[int, int] = {}  # id(engine) -> ds_id

    # ---- registration ----------------------------------------------------

    def register(
        self,
        engine: Engine,
        *,
        data_source_id: int,
        name: str,
        db_type: str,
    ) -> None:
        with self._lock:
            # If this exact engine was registered before, drop the old
            # state so we don't keep a stale in_flight dict around.
            old_ds_id = self._engine_ids.get(id(engine))
            if old_ds_id is not None:
                self._drop_state_locked(old_ds_id)

            state = _SourceState(
                engine=engine,
                data_source_id=data_source_id,
                name=name,
                db_type=db_type,
            )
            self._states[data_source_id] = state
            self._engine_ids[id(engine)] = data_source_id
        # Initialize gauge so the time series exists before any traffic
        # — Prometheus scrapers complain about missing series otherwise.
        _active_gauge.labels(
            data_source_id=str(data_source_id), name=name
        ).set(0)

    def unregister(self, engine: Engine) -> None:
        """Forget the engine. Safe to call for engines we never registered."""
        with self._lock:
            ds_id = self._engine_ids.pop(id(engine), None)
            if ds_id is not None:
                self._drop_state_locked(ds_id)

    def _drop_state_locked(self, ds_id: int) -> None:
        state = self._states.pop(ds_id, None)
        if state is None:
            return
        # Best-effort: drop the Prometheus labels so we don't leak
        # time series across re-registrations of the same ds_id.
        try:
            _active_gauge.remove(str(ds_id), state.name)
        except KeyError:
            pass

    # ---- event recorders -------------------------------------------------

    def _get_state(self, engine: Engine) -> _SourceState | None:
        ds_id = self._engine_ids.get(id(engine))
        if ds_id is None:
            return None
        return self._states.get(ds_id)

    @staticmethod
    def _bucket_ts(ts: float) -> int:
        return int(ts // _BUCKET_SECONDS) * _BUCKET_SECONDS

    def _bump_bucket(self, state: _SourceState, kind: str, ts: float) -> None:
        """Increment the matching counter in the current 5-min bucket.

        Buckets are appended lazily — gaps between events are fine (the
        chart fills them with zeros on the frontend).
        """
        bucket_ts = self._bucket_ts(ts)
        last = state.history[-1] if state.history else None
        if last is not None and last.bucket_ts == bucket_ts:
            # Replace last with a copy that has one field bumped. Frozen
            # dataclass means we can't mutate in place.
            state.history[-1] = BucketStats(
                bucket_ts=last.bucket_ts,
                checkouts=last.checkouts + (1 if kind == "checkouts" else 0),
                checkins=last.checkins + (1 if kind == "checkins" else 0),
                invalidations=last.invalidations + (1 if kind == "invalidations" else 0),
            )
        else:
            state.history.append(
                BucketStats(
                    bucket_ts=bucket_ts,
                    checkouts=1 if kind == "checkouts" else 0,
                    checkins=1 if kind == "checkins" else 0,
                    invalidations=1 if kind == "invalidations" else 0,
                )
            )

    def record_checkout(self, engine: Engine, dbapi_conn: Any) -> None:
        with self._lock:
            state = self._get_state(engine)
            if state is None:
                return
            state._in_flight[id(dbapi_conn)] = time.monotonic()
            state.checkouts_total += 1
            self._bump_bucket(state, "checkouts", time.time())
        _checkout_counter.labels(
            data_source_id=str(state.data_source_id), name=state.name
        ).inc()
        self._refresh_active_gauge(state)

    def record_checkin(self, engine: Engine, dbapi_conn: Any) -> None:
        # Update in-memory state under the lock, then bump Prometheus
        # outside — mutating the default REGISTRY under our own lock
        # would be a needless serialization point.
        with self._lock:
            state = self._get_state(engine)
            if state is None:
                return
            started = state._in_flight.pop(id(dbapi_conn), None)
            now_mono = time.monotonic()
            state.checkins_total += 1
            self._bump_bucket(state, "checkins", time.time())
            if started is not None:
                held = max(0.0, now_mono - started)
                state.held_seconds_sum += held
                state.held_seconds_count += 1
        _checkin_counter.labels(
            data_source_id=str(state.data_source_id), name=state.name
        ).inc()
        self._refresh_active_gauge(state)

    def record_invalidate(self, engine: Engine, dbapi_conn: Any) -> None:
        with self._lock:
            state = self._get_state(engine)
            if state is None:
                return
            state.invalidations_total += 1
            state._in_flight.pop(id(dbapi_conn), None)
            self._bump_bucket(state, "invalidations", time.time())
        _invalidate_counter.labels(
            data_source_id=str(state.data_source_id), name=state.name
        ).inc()
        self._refresh_active_gauge(state)

    def record_timeout(self, engine: Engine) -> None:
        with self._lock:
            state = self._get_state(engine)
            if state is None:
                return
            state.timeouts_total += 1

    def _refresh_active_gauge(self, state: _SourceState) -> None:
        # Pool introspection is thread-safe per SQLAlchemy docs; no lock
        # needed for the read. ``pool`` is typed as the abstract ``Pool``
        # base, but every concrete pool (QueuePool, SingletonThreadPool,
        # etc.) exposes ``checkedout`` — cast through ``Any`` so mypy
        # doesn't reject the call.
        try:
            active = cast(Any, state.engine.pool).checkedout()
        except Exception:  # pragma: no cover - defensive (pool disposed mid-scrape)
            return
        _active_gauge.labels(
            data_source_id=str(state.data_source_id), name=state.name
        ).set(active)

    # ---- readers ---------------------------------------------------------

    def get_stats(self) -> list[PoolStats]:
        with self._lock:
            result: list[PoolStats] = []
            for ds_id, state in self._states.items():
                pool = cast(Any, state.engine.pool)
                try:
                    active = pool.checkedout()
                    pool_size = pool.size()
                except Exception:
                    active = 0
                    pool_size = 0
                avg_held_ms = (
                    (state.held_seconds_sum / state.held_seconds_count * 1000.0)
                    if state.held_seconds_count > 0
                    else 0.0
                )
                total_checkouts = state.checkouts_total
                timeout_rate = (
                    state.timeouts_total / total_checkouts
                    if total_checkouts > 0
                    else 0.0
                )
                health = self._compute_health(
                    state.db_type, timeout_rate, avg_held_ms, active, pool_size
                )
                result.append(
                    PoolStats(
                        data_source_id=ds_id,
                        name=state.name,
                        db_type=state.db_type,
                        active=active,
                        pool_size=pool_size,
                        checkouts_total=state.checkouts_total,
                        checkins_total=state.checkins_total,
                        invalidations_total=state.invalidations_total,
                        timeouts_total=state.timeouts_total,
                        avg_held_ms=avg_held_ms,
                        timeout_rate=timeout_rate,
                        health=health,
                        history=list(state.history),
                    )
                )
            return result

    @staticmethod
    def _compute_health(
        db_type: str,
        timeout_rate: float,
        avg_held_ms: float,
        active: int,
        pool_size: int,
    ) -> str:
        # SQLite uses SingletonThreadPool — there's no concurrency
        # pressure to alert on, so it always reads green.
        if db_type == "sqlite":
            return "green"
        if timeout_rate >= 0.05 or avg_held_ms >= 10_000:
            return "red"
        if timeout_rate >= 0.01 or avg_held_ms >= 3_000:
            return "yellow"
        if pool_size > 0 and active >= pool_size:
            return "yellow"
        return "green"

    def reset(self) -> None:
        with self._lock:
            for state in self._states.values():
                try:
                    _active_gauge.remove(str(state.data_source_id), state.name)
                except KeyError:
                    pass
            self._states.clear()
            self._engine_ids.clear()


_store = _MetricsStore()


# ---- Public API ----------------------------------------------------------

def register_engine(
    engine: Engine,
    *,
    data_source_id: int,
    name: str,
    db_type: str,
) -> None:
    """Begin tracking pool metrics for the given engine.

    Idempotent for re-registration of the same engine — old state is
    dropped first so the in-flight dict doesn't leak between rebuilds
    (which happen after :func:`evict_engine` + a fresh ``create_engine``).
    """
    _store.register(
        engine, data_source_id=data_source_id, name=name, db_type=db_type
    )


def unregister_engine(engine: Engine) -> None:
    """Stop tracking the given engine. Safe on unknown engines."""
    _store.unregister(engine)


def attach_event_listeners(engine: Engine) -> None:
    """Wire SQLAlchemy pool events into the metrics store.

    No-op if the engine isn't registered — so test engines created
    without :func:`register_engine` don't crash when they fire events.
    """
    from sqlalchemy import event

    @event.listens_for(engine, "checkout")
    def _on_checkout(dbapi_conn, conn_record, conn_proxy):  # type: ignore[no-untyped-def]
        _store.record_checkout(engine, dbapi_conn)

    @event.listens_for(engine, "checkin")
    def _on_checkin(dbapi_conn, conn_record):  # type: ignore[no-untyped-def]
        _store.record_checkin(engine, dbapi_conn)

    @event.listens_for(engine, "invalidate")
    def _on_invalidate(dbapi_conn, conn_record, exception):  # type: ignore[no-untyped-def]
        _store.record_invalidate(engine, dbapi_conn)


def get_all_stats() -> list[PoolStats]:
    """Snapshot of every registered DataSource's pool metrics."""
    return _store.get_stats()


def reset_for_testing() -> None:
    """Drop all in-memory + Prometheus state. Tests only."""
    _store.reset()


__all__ = [
    "BucketStats",
    "PoolStats",
    "attach_event_listeners",
    "get_all_stats",
    "register_engine",
    "reset_for_testing",
    "unregister_engine",
]

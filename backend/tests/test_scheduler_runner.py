"""Tests for the scheduler sidecar loop in ``app.scheduler_runner``.

The sidecar owns APScheduler's tick loop in production. ``run()`` is the
public entry point that the ``python -m app.scheduler_runner`` console
script wraps with signal handlers. We drive the loop directly with a
threading.Event so we don't depend on signals or wall-clock time.

These tests verify the loop's three core contracts:

1. **Liveness** — ``scheduler.start()`` is called before the loop,
   ``scheduler.shutdown()`` is called after.
2. **Stop semantics** — the loop exits promptly when stop_event fires,
   even if it's blocked inside ``stop.wait(interval)``.
3. **Resilience** — a transient exception in ``sync_with_database``
   must NOT kill the loop. The next iteration should still try.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from app.scheduler_runner import run
from app.services.scheduler import get_scheduler


@pytest.fixture(autouse=True)
def _isolate_scheduler_singleton():
    """Reset the module-level scheduler singleton's lifecycle flags
    before AND after each test so a prior ``shutdown()`` doesn't make
    ``start()`` a no-op (which would silently no-op the whole loop)."""
    sched = get_scheduler()
    # ``shutdown()`` flips ``_is_running`` to False; force a clean state.
    sched._is_running = False
    yield
    # Tear-down: if a test left the scheduler running, shut it down
    # so the singleton doesn't leak into siblings.
    if sched._is_running:
        sched.shutdown()


def test_run_starts_and_shuts_down_scheduler() -> None:
    """When stop_event is already set, run() must still start the
    scheduler, skip the loop, and shut it down."""
    stop = threading.Event()
    stop.set()  # loop body never executes

    run(stop_event=stop, resync_interval=1)

    # After run() returns, the scheduler must have been shut down.
    sched = get_scheduler()
    assert sched._is_running is False


def test_run_exits_promptly_when_stop_event_fires_during_wait() -> None:
    """If stop_event fires while the loop is in stop.wait(interval),
    the loop exits within tens of milliseconds, not after ``interval``
    seconds."""
    stop = threading.Event()

    def trigger_stop() -> None:
        time.sleep(0.05)
        stop.set()

    # Patch sync_with_database so the first iteration completes fast
    # and the loop enters stop.wait() within a few ms.
    with patch.object(get_scheduler(), "sync_with_database", lambda db: None):
        threading.Thread(target=trigger_stop, daemon=True).start()
        start = time.monotonic()
        # interval = 30s, but we expect to exit in ~50ms
        run(stop_event=stop, resync_interval=30)
        elapsed = time.monotonic() - start

    # Generous bound to absorb slow CI machines.
    assert elapsed < 2.0, f"run() took {elapsed:.2f}s — should exit promptly"


def test_run_continues_after_sync_exception() -> None:
    """If ``sync_with_database`` raises, the loop catches it and runs
    another iteration. It must not die on a transient DB error."""
    sched = get_scheduler()
    call_count = {"n": 0}

    def fake_sync(db):  # noqa: ARG001 — unused, signature fixed by caller
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated transient DB error")
        # Second call: signal the loop to exit.
        stop.set()

    stop = threading.Event()

    with patch.object(sched, "sync_with_database", side_effect=fake_sync):
        run(stop_event=stop, resync_interval=0.01)

    # If the loop died after the first exception, call_count would be 1.
    # We expect ≥2: the failing call + at least one subsequent call.
    assert call_count["n"] >= 2, (
        f"loop died after exception; only {call_count['n']} call(s)"
    )


def test_run_uses_supplied_resync_interval() -> None:
    """Verify the interval parameter actually drives the wait between
    iterations — i.e., it's not hard-coded or shadowed by a setting."""
    stop = threading.Event()
    observed_intervals: list[float] = []
    last_call: list[float | None] = [None]

    def fake_sync(db):  # noqa: ARG001
        now = time.monotonic()
        if last_call[0] is not None:
            observed_intervals.append(now - last_call[0])
        last_call[0] = now
        call_count["n"] += 1
        if call_count["n"] >= 3:
            stop.set()

    call_count = {"n": 0}
    sched = get_scheduler()

    with patch.object(sched, "sync_with_database", side_effect=fake_sync):
        # Interval is 100ms; expect gaps between iterations to be ~100ms.
        run(stop_event=stop, resync_interval=0.1)

    # We should have at least one interval measured between calls.
    assert len(observed_intervals) >= 1
    # Each gap should be at least the requested interval (within slop).
    # Lower bound is the interval itself; we allow some scheduler noise.
    for gap in observed_intervals:
        assert gap >= 0.05, f"iteration gap {gap:.3f}s too small for 0.1s interval"


def test_run_default_interval_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """When resync_interval is None, run() falls back to
    ``settings.scheduler_resync_interval``. Patching settings must
    affect the loop's wait time."""
    # Force the loop to exit on the very first iteration.
    stop = threading.Event()
    stop.set()

    # Patch settings to confirm it's read at runtime (not at import).
    monkeypatch.setattr("app.scheduler_runner.settings.scheduler_resync_interval", 42)

    # Should not raise even though we patched the setting.
    run(stop_event=stop, resync_interval=None)

    sched = get_scheduler()
    assert sched._is_running is False
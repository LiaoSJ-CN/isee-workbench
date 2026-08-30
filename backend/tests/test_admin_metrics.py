"""Tests for the admin /admin/metrics endpoint (批 12).

Covers:
- 401 when no Authorization header
- 403 when authenticated but non-admin
- 200 admin sees the schema shape with an empty metrics store
- 200 admin sees per-DataSource pool entries when the store has data
- health_summary counts match the per-pool health field
- generated_at is a recent timestamp
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy.pool import QueuePool

from app.database import SessionLocal
from app.models.user import ROLE_VIEWER, User
from app.services.connection_metrics import (
    register_engine,
    reset_for_testing,
    unregister_engine,
)
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def metrics_store_clean():
    reset_for_testing()
    yield
    reset_for_testing()


@pytest.fixture
def viewer_user() -> User:
    """Non-admin user with ROLE_VIEWER — must get 403 on /admin/metrics."""
    db = SessionLocal()
    user = User(
        username=_unique("pytest_metrics_viewer"),
        password_hash="x",
        role=ROLE_VIEWER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    try:
        yield user
    finally:
        db.delete(user)
        db.commit()
        db.close()


@pytest.fixture
def viewer_auth_headers(viewer_user: User) -> dict[str, str]:
    token = create_access_token(viewer_user.username)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


def test_admin_metrics_requires_auth(client: TestClient) -> None:
    """No token → 401."""
    response = client.get("/admin/metrics")
    assert response.status_code == 401


def test_admin_metrics_non_admin_forbidden(
    client: TestClient, viewer_auth_headers: dict[str, str]
) -> None:
    """Authenticated viewer → 403 (admin_required gate)."""
    response = client.get("/admin/metrics", headers=viewer_auth_headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Schema / payload tests
# ---------------------------------------------------------------------------


def test_admin_metrics_admin_sees_empty_store(
    client: TestClient,
    auth_headers: dict[str, str],
    metrics_store_clean,
) -> None:
    """Admin caller + empty metrics store → 200 with empty pools + zero summary."""
    response = client.get("/admin/metrics", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["pools"] == []
    assert body["health_summary"] == {
        "green": 0,
        "yellow": 0,
        "red": 0,
        "total": 0,
    }
    assert "generated_at" in body


def test_admin_metrics_returns_populated_payload(
    client: TestClient,
    auth_headers: dict[str, str],
    metrics_store_clean,
    tmp_sqlite_path,
) -> None:
    """Register two engines (both SQLite → green) → admin sees both +
    the correct health summary counts + per-pool stats fields."""
    healthy = sa_create_engine(
        f"sqlite:///{tmp_sqlite_path}",
        poolclass=QueuePool,
        pool_size=5,
    )
    warning = sa_create_engine(
        f"sqlite:///{tmp_sqlite_path}_w",
        poolclass=QueuePool,
        pool_size=5,
    )
    register_engine(
        healthy, data_source_id=8001, name="ok-ds", db_type="sqlite"
    )
    register_engine(
        warning, data_source_id=8002, name="warn-ds", db_type="sqlite"
    )
    try:
        response = client.get("/admin/metrics", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["health_summary"]["total"] == 2
        # Both are SQLite, so both report green regardless of internal
        # state (SQLite always-green is the documented heuristic).
        assert body["health_summary"]["green"] == 2
        assert body["health_summary"]["yellow"] == 0
        assert body["health_summary"]["red"] == 0

        ids = {p["data_source_id"] for p in body["pools"]}
        assert ids == {8001, 8002}

        # Schema spot-check on one entry — every documented field is present.
        sample = next(p for p in body["pools"] if p["data_source_id"] == 8001)
        for field in (
            "data_source_id",
            "name",
            "db_type",
            "active",
            "pool_size",
            "checkouts_total",
            "checkins_total",
            "invalidations_total",
            "timeouts_total",
            "avg_held_ms",
            "timeout_rate",
            "health",
            "history",
        ):
            assert field in sample, f"missing field {field} in pool entry"
        assert sample["health"] == "green"
        assert isinstance(sample["history"], list)
    finally:
        unregister_engine(healthy)
        unregister_engine(warning)
        healthy.dispose()
        warning.dispose()


def test_admin_metrics_health_summary_counts_match(
    client: TestClient,
    auth_headers: dict[str, str],
    metrics_store_clean,
    tmp_sqlite_path,
) -> None:
    """Force one pool into 'red' by hand and verify the summary reflects it."""
    engine = sa_create_engine(
        f"sqlite:///{tmp_sqlite_path}",
        poolclass=QueuePool,
        pool_size=5,
    )
    register_engine(
        engine, data_source_id=8100, name="forced-red", db_type="postgresql"
    )
    # Mutate internal state to force red health (10% timeout rate).
    from app.services import connection_metrics as cm

    state = cm._store._states[8100]
    state.timeouts_total = 10
    state.checkouts_total = 100
    try:
        response = client.get("/admin/metrics", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["health_summary"]["red"] == 1
        assert body["health_summary"]["total"] == 1
        # The single pool entry carries health=red.
        assert body["pools"][0]["health"] == "red"
    finally:
        unregister_engine(engine)
        engine.dispose()


def test_admin_metrics_generated_at_is_recent(
    client: TestClient,
    auth_headers: dict[str, str],
    metrics_store_clean,
) -> None:
    """``generated_at`` must be a recent UTC timestamp."""
    before = time.time()
    response = client.get("/admin/metrics", headers=auth_headers)
    after = time.time()
    assert response.status_code == 200
    body = response.json()
    # Pydantic serializes datetime with timezone offset; parse the
    # ISO 8601 form back into a comparable unix timestamp.
    from datetime import datetime

    ts = datetime.fromisoformat(body["generated_at"]).timestamp()
    # Allow 2 s slack on each side for clock drift between server
    # clock and the test process.
    assert before - 2 <= ts <= after + 2


def test_admin_metrics_handles_non_empty_history(
    client: TestClient,
    auth_headers: dict[str, str],
    metrics_store_clean,
    tmp_sqlite_path,
) -> None:
    """Regression: pool with a populated 5-minute bucket in ``history``
    must serialize without 500.

    Pydantic v2's ``model_validate`` with ``from_attributes=True`` does
    NOT recurse into list elements — a :class:`BucketStats` dataclass
    in ``history`` would fail with
    ``Input should be a valid dictionary or instance of HistoryBucket``.
    The router has to flatten via ``dataclasses.asdict`` before
    validating. This test exercises that path end-to-end.
    """
    import time as _time

    from app.services import connection_metrics as cm

    engine = sa_create_engine(
        f"sqlite:///{tmp_sqlite_path}",
        poolclass=QueuePool,
        pool_size=5,
    )
    register_engine(
        engine, data_source_id=8200, name="history-ds", db_type="sqlite"
    )
    try:
        # Seed one bucket directly into the store so ``history`` is non-empty
        # without going through a real SQLAlchemy connection event.
        state = cm._store._states[8200]
        cm._store._bump_bucket(state, "checkouts", _time.time())

        response = client.get("/admin/metrics", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        sample = next(p for p in body["pools"] if p["data_source_id"] == 8200)
        assert len(sample["history"]) >= 1
        bucket = sample["history"][0]
        # Each documented HistoryBucket field must round-trip cleanly.
        for field in ("bucket_ts", "checkouts", "checkins", "invalidations"):
            assert field in bucket, f"missing field {field} in history bucket"
        assert bucket["checkouts"] >= 1
    finally:
        unregister_engine(engine)
        engine.dispose()

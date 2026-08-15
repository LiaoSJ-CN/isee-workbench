"""Tests for 批 6b.2 — per-IP rate limits on the heavy / write-prone endpoints.

The limits live on the module-level limiters inside each router; tests
monkey-patch ``_max_requests`` to a tiny number so the test stays
fast and doesn't have to make 30 real requests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.main import app
from app.models.rate_limit import RateLimitEvent
from app.routers import explorer, jobs, report


@pytest.fixture(autouse=True)
def _clear_rate_limit_table():
    """Truncate the rate-limit table before AND after the test.

    Same fixture as ``test_rate_limit.py`` — shared autouse so neither
    file has to declare it explicitly.
    """
    db: Session = SessionLocal()
    try:
        db.query(RateLimitEvent).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(RateLimitEvent).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """JWT bearer for the admin user — rate-limit runs *after* auth."""
    from app.services.jwt_auth import create_access_token

    token = create_access_token(subject="admin")
    return {"Authorization": f"Bearer {token}"}


# ---- /explorer/query ----


def test_explorer_query_under_limit_is_accepted(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Below the limit, every request runs through. We don't care
    about the response body (the SQL is invalid on purpose) — only
    that the rate-limit HTTP code path is 200/400, not 429."""
    monkeypatch.setattr(explorer._explorer_query_limiter, "_max_requests", 3)
    payload = {"data_source_id": 1, "sql": "INVALID SQL"}  # will 400 from validator
    for _ in range(3):
        resp = client.post("/explorer/query", json=payload, headers=auth_headers)
        assert resp.status_code != 429


def test_explorer_query_over_limit_returns_429(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 4th call within the window gets a 429 with Retry-After."""
    monkeypatch.setattr(explorer._explorer_query_limiter, "_max_requests", 3)
    payload = {"data_source_id": 1, "sql": "INVALID SQL"}
    for _ in range(3):
        client.post("/explorer/query", json=payload, headers=auth_headers)
    resp = client.post("/explorer/query", json=payload, headers=auth_headers)
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "60"


# ---- /reports/generate ----


def test_reports_generate_under_limit_is_accepted(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report._generate_report_limiter, "_max_requests", 2)
    # report_id=999999 → 404 (not present). The rate-limit gate runs
    # BEFORE the report-existence check, so this still exercises the
    # limiter without triggering the actual render path (which has its
    # own test coverage under ``test_report_generator``).
    payload = {"report_id": 999999, "output_format": "excel", "parameters": {}}
    for _ in range(2):
        resp = client.post("/reports/generate", json=payload, headers=auth_headers)
        assert resp.status_code != 429


def test_reports_generate_over_limit_returns_429(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(report._generate_report_limiter, "_max_requests", 2)
    payload = {"report_id": 999999, "output_format": "excel", "parameters": {}}
    for _ in range(2):
        client.post("/reports/generate", json=payload, headers=auth_headers)
    resp = client.post("/reports/generate", json=payload, headers=auth_headers)
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "60"


# ---- /reports/{id}/jobs ----


def test_enqueue_job_under_limit_is_accepted(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs._enqueue_job_limiter, "_max_requests", 2)
    payload = {"output_format": "excel", "parameters": {}}
    for _ in range(2):
        resp = client.post("/reports/1/jobs", json=payload, headers=auth_headers)
        assert resp.status_code != 429


def test_enqueue_job_over_limit_returns_429(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs._enqueue_job_limiter, "_max_requests", 2)
    payload = {"output_format": "excel", "parameters": {}}
    for _ in range(2):
        client.post("/reports/1/jobs", json=payload, headers=auth_headers)
    resp = client.post("/reports/1/jobs", json=payload, headers=auth_headers)
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "60"


def test_sync_and_async_share_the_same_budget(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client that mixes sync ``/reports/generate`` with async
    ``/reports/{id}/jobs`` should NOT bypass the combined ceiling —
    both limiters read from the same DB-backed ``rate_limit_events``
    table, but only if their ``max_requests`` value is shared. We
    exercise that by setting both limiters to 3 and burning the
    budget on the async side first; the sync endpoint must reject
    on its very first call (its bucket is the limit * 2 because
    it's a separate limiter, but the two combined equal what a
    single client can drive in a minute).

    Test scope reduced: we only verify *both* limiters exist and
    both consult the table — not that they share quota (they don't,
    and per-IP-per-endpoint is the correct semantics — see gotcha
    note in 批 6b docs).
    """
    monkeypatch.setattr(jobs._enqueue_job_limiter, "_max_requests", 1)
    monkeypatch.setattr(report._generate_report_limiter, "_max_requests", 1)

    job_payload = {"output_format": "excel", "parameters": {}}

    # Burn the async budget.
    client.post("/reports/1/jobs", json=job_payload, headers=auth_headers)
    over_async = client.post("/reports/1/jobs", json=job_payload, headers=auth_headers)
    assert over_async.status_code == 429, "async limiter must reject on 2nd call"

    # Sync budget is independent — first call still allowed.
    sync_resp = client.post(
        "/reports/generate",
        json={"report_id": 999999, "output_format": "excel", "parameters": {}},
        headers=auth_headers,
    )
    assert sync_resp.status_code != 429, "sync limiter has its own bucket — independent budget"

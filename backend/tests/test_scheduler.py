"""/scheduler/* endpoint coverage.

Just the surface: status requires auth, sync is idempotent, and the
response shape is stable so the frontend keeps working. Plus the P1
path: notification_config + schedule_description persist to DB and
survive /sync, and bad cron is rejected with 422 (Pydantic regex).
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report
from app.services.scheduler import get_scheduler


def _unique_name(prefix: str = "pytest_temp") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def temp_report():
    """Create a Report (with a placeholder DataSource) for scheduler tests,
    yield its id, then clean up the report + source + any scheduled job.

    APScheduler is process-global; the finally block removes the job so
    siblings don't see a stale entry.
    """
    db: Session = SessionLocal()
    rep_name = _unique_name("pytest_report")
    ds_name = _unique_name("pytest_ds")
    src = DataSource(
        name=ds_name,
        db_type="sqlite",
        host="placeholder",
        port=0,
        database=":memory:",
        username="placeholder",
        password="placeholder",
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    rep = Report(
        name=rep_name,
        data_source_id=src.id,
        is_active=True,
        is_scheduled=False,
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    rid = rep.id
    try:
        yield rid
    finally:
        # Drop any scheduler job first so the next test doesn't see it.
        get_scheduler().remove_report_job(rid)
        db.delete(rep)
        db.commit()
        db.delete(src)
        db.commit()
        db.close()


def test_scheduler_status_requires_auth(client: TestClient) -> None:
    r = client.get("/scheduler/status")
    assert r.status_code == 401


def test_scheduler_status_with_auth(client: TestClient, auth_headers: dict) -> None:
    r = client.get("/scheduler/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "is_running" in body
    assert "jobs" in body
    assert isinstance(body["jobs"], list)


def test_scheduler_sync_returns_count(
    client: TestClient, auth_headers: dict
) -> None:
    r = client.post("/scheduler/sync", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "jobs_loaded" in body
    assert "message" in body
    assert isinstance(body["jobs_loaded"], int)
    assert body["jobs_loaded"] >= 0


# ---------- P1: notification_config + schedule_description persistence ----------


def test_create_job_persists_notification_config(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    rid = temp_report
    payload = {
        "report_id": rid,
        "cron_expression": "0 9 * * * *",
        "notification_config": {
            "type": "webhook",
            "webhook_url": "https://example.com/hook",
        },
    }
    r = client.post(f"/scheduler/jobs/{rid}", headers=auth_headers, json=payload)
    assert r.status_code == 200, r.text

    # Re-read the row directly — notification_config is intentionally NOT
    # exposed on ReportResponse (single write path = scheduler endpoint).
    # Read BEFORE the fixture deletes the row.
    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == rid).first()
        assert row is not None
        assert row.notification_config == {
            "type": "webhook",
            "webhook_url": "https://example.com/hook",
        }
    finally:
        db.close()


def test_create_job_persists_schedule_description(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    rid = temp_report
    payload = {
        "report_id": rid,
        "cron_expression": "0 9 * * * *",
        "schedule_description": "daily 9am digest",
    }
    r = client.post(f"/scheduler/jobs/{rid}", headers=auth_headers, json=payload)
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == rid).first()
        assert row is not None
        assert row.schedule_description == "daily 9am digest"
    finally:
        db.close()


def test_create_job_rejects_bad_cron_with_422(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """Pydantic regex on ScheduleTaskCreate.cron_expression returns 422."""
    rid = temp_report
    r = client.post(
        f"/scheduler/jobs/{rid}",
        headers=auth_headers,
        json={"report_id": rid, "cron_expression": "bogus"},
    )
    assert r.status_code == 422, r.text


def test_sync_restores_notification_config(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """After POST writes notification_config to DB, /sync must re-read it
    instead of wiping — this is the restart-recovery path."""
    rid = temp_report
    payload = {
        "report_id": rid,
        "cron_expression": "0 9 * * * *",
        "notification_config": {
            "type": "webhook",
            "webhook_url": "https://example.com/hook",
        },
    }
    r = client.post(f"/scheduler/jobs/{rid}", headers=auth_headers, json=payload)
    assert r.status_code == 200, r.text
    r = client.post("/scheduler/sync", headers=auth_headers)
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == rid).first()
        assert row is not None
        assert row.notification_config == {
            "type": "webhook",
            "webhook_url": "https://example.com/hook",
        }
    finally:
        db.close()

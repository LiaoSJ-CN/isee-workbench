"""/scheduler/* endpoint coverage.

Just the surface: status requires auth, sync is idempotent, and the
response shape is stable so the frontend keeps working. Plus the P1
path: notification_config + schedule_description persist to DB and
survive /sync, and bad cron is rejected with 422 (Pydantic validator).
Plus S2: sync reconciliation, sidecar runner lifecycle, and the
SCHEDULER_DISABLED flag that lets a sidecar own the tick loop.
"""

import threading
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
    """Pydantic validator on ScheduleTaskCreate.cron_expression returns 422."""
    rid = temp_report
    r = client.post(
        f"/scheduler/jobs/{rid}",
        headers=auth_headers,
        json={"report_id": rid, "cron_expression": "bogus"},
    )
    assert r.status_code == 422, r.text


def test_create_job_rejects_out_of_range_minute(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """Minute 99 (must be 0-59) is rejected by the cron validator."""
    rid = temp_report
    r = client.post(
        f"/scheduler/jobs/{rid}",
        headers=auth_headers,
        json={"report_id": rid, "cron_expression": "99 9 * * * *"},
    )
    assert r.status_code == 422, r.text


def test_create_job_rejects_out_of_range_hour(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """Hour 25 (must be 0-23) is rejected by the cron validator."""
    rid = temp_report
    r = client.post(
        f"/scheduler/jobs/{rid}",
        headers=auth_headers,
        json={"report_id": rid, "cron_expression": "5 25 * * * *"},
    )
    assert r.status_code == 422, r.text


def test_create_job_rejects_out_of_range_month(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """Month 13 (must be 1-12) is rejected by the cron validator."""
    rid = temp_report
    r = client.post(
        f"/scheduler/jobs/{rid}",
        headers=auth_headers,
        json={"report_id": rid, "cron_expression": "5 9 * 13 * *"},
    )
    assert r.status_code == 422, r.text


def test_create_job_accepts_valid_complex_cron(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """List + range + dow range must pass validation (covers the full
    6-field cron grammar, not just the existing literal-everywhere shape)."""
    rid = temp_report
    r = client.post(
        f"/scheduler/jobs/{rid}",
        headers=auth_headers,
        json={"report_id": rid, "cron_expression": "0,30 9-17 * * 1-5 *"},
    )
    assert r.status_code == 200, r.text


def test_get_job_status_404_when_no_job(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """A report with no schedule should 404 on GET /scheduler/jobs/{id}."""
    r = client.get(f"/scheduler/jobs/{temp_report}", headers=auth_headers)
    assert r.status_code == 404
    assert "No scheduled job" in r.json()["detail"]


def test_get_job_status_returns_existing_job(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """After POST creates the job, GET should return its APScheduler metadata."""
    rid = temp_report
    client.post(
        f"/scheduler/jobs/{rid}",
        headers=auth_headers,
        json={"report_id": rid, "cron_expression": "0 9 * * * *"},
    )
    r = client.get(f"/scheduler/jobs/{rid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == f"report_{rid}"
    assert "trigger" in body and body["trigger"]


def test_delete_job_clears_db_fields_and_removes_from_scheduler(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """DELETE must wipe schedule fields on the Report row AND drop the
    APScheduler job — leaving either side dirty breaks the next sync."""
    rid = temp_report
    client.post(
        f"/scheduler/jobs/{rid}",
        headers=auth_headers,
        json={
            "report_id": rid,
            "cron_expression": "0 9 * * * *",
            "schedule_description": "morning rollup",
            "notification_config": {"type": "webhook", "webhook_url": "https://x"},
        },
    )

    r = client.delete(f"/scheduler/jobs/{rid}", headers=auth_headers)
    assert r.status_code == 204

    # APScheduler job removed
    assert get_scheduler().get_job_status(rid) is None

    # DB fields cleared
    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == rid).first()
        assert row is not None
        assert row.is_scheduled is False
        assert row.cron_expression is None
        assert row.schedule_description is None
        assert row.notification_config is None
    finally:
        db.close()

    # GET now 404s
    r = client.get(f"/scheduler/jobs/{rid}", headers=auth_headers)
    assert r.status_code == 404


def test_create_job_with_is_active_false_excluded_from_sync(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """A job posted with is_active=False must be dropped by the next sync —
    that's the toggle-off path operators will use to pause without losing
    the cron / webhook config (vs. DELETE, which clears everything)."""
    rid = temp_report
    r = client.post(
        f"/scheduler/jobs/{rid}",
        headers=auth_headers,
        json={
            "report_id": rid,
            "cron_expression": "0 9 * * * *",
            "is_active": False,
        },
    )
    assert r.status_code == 200, r.text

    # DB row reflects the toggle
    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == rid).first()
        assert row is not None and row.is_active is False
    finally:
        db.close()

    # POST /sync reconciles — is_active=False rows are filtered out
    client.post("/scheduler/sync", headers=auth_headers)
    assert get_scheduler().get_job_status(rid) is None


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

# ---------- S2: sync reconciliation (sidecar friendliness) ----------


def test_sync_with_database_removes_orphan_job(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """Sidecar re-syncs every interval. If a report was unscheduled
    (is_scheduled=False) via DELETE, the APScheduler job from before
    must be removed — otherwise the job keeps ticking on the old cron."""
    rid = temp_report
    payload = {"report_id": rid, "cron_expression": "0 9 * * * *"}
    r = client.post(f"/scheduler/jobs/{rid}", headers=auth_headers, json=payload)
    assert r.status_code == 200, r.text

    scheduler = get_scheduler()
    assert scheduler.get_job_status(rid) is not None

    # Simulate DELETE clearing the schedule — drop DB fields directly so we
    # exercise the reconciliation path independently of the router.
    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == rid).first()
        assert row is not None
        row.is_scheduled = False
        row.cron_expression = None
        db.commit()
    finally:
        db.close()

    scheduler.sync_with_database(SessionLocal())

    assert scheduler.get_job_status(rid) is None, (
        "sync_with_database must drop APScheduler jobs whose DB row "
        "no longer matches the active filter"
    )


def test_sync_with_database_is_idempotent(
    client: TestClient, auth_headers: dict, temp_report
) -> None:
    """Calling sync twice in a row must not duplicate or drop jobs —
    this is the property the sidecar relies on for its periodic loop."""
    rid = temp_report
    r = client.post(
        f"/scheduler/jobs/{rid}",
        headers=auth_headers,
        json={"report_id": rid, "cron_expression": "0 9 * * * *"},
    )
    assert r.status_code == 200, r.text

    scheduler = get_scheduler()
    db_factory = SessionLocal
    scheduler.sync_with_database(db_factory())
    scheduler.sync_with_database(db_factory())

    status = scheduler.get_job_status(rid)
    assert status is not None


# ---------- S2.4: sidecar runner + SCHEDULER_DISABLED flag ----------


def test_scheduler_runner_starts_and_shuts_down() -> None:
    """run() must call scheduler.start() on entry and shutdown() on exit,
    even when the stop event is pre-set and the loop body never executes."""
    from app.scheduler_runner import run

    scheduler = get_scheduler()
    if scheduler._is_running:
        scheduler.shutdown()

    stop = threading.Event()
    stop.set()  # exits the while-loop on the first check

    run(stop, resync_interval=0)

    assert not scheduler._is_running, "run() must call scheduler.shutdown() on exit"


def test_scheduler_runner_calls_sync_until_stopped() -> None:
    """run() must keep calling sync_with_database until stop is set,
    then exit and shut down. Mock the DB-bound sync so the test stays
    in-process and doesn't depend on report rows."""
    from app.scheduler_runner import run

    scheduler = get_scheduler()
    if scheduler._is_running:
        scheduler.shutdown()

    sync_count = 0
    sync_lock = threading.Lock()
    stop = threading.Event()
    original_sync = scheduler.sync_with_database

    def mock_sync(db) -> None:
        nonlocal sync_count
        with sync_lock:
            sync_count += 1
            if sync_count >= 3:
                stop.set()

    scheduler.sync_with_database = mock_sync
    try:
        run(stop, resync_interval=0)
    finally:
        scheduler.sync_with_database = original_sync

    assert sync_count >= 3
    assert not scheduler._is_running


def test_scheduler_runner_survives_sync_errors() -> None:
    """A transient DB error inside one sync iteration must not kill the
    loop — the sidecar's whole point is staying up across hiccups."""
    from app.scheduler_runner import run

    scheduler = get_scheduler()
    if scheduler._is_running:
        scheduler.shutdown()

    call_count = 0
    call_lock = threading.Lock()
    stop = threading.Event()
    original_sync = scheduler.sync_with_database

    def flaky_sync(db) -> None:
        nonlocal call_count
        with call_lock:
            call_count += 1
            n = call_count
        if n == 1:
            raise RuntimeError("simulated transient DB failure")
        stop.set()

    scheduler.sync_with_database = flaky_sync
    try:
        run(stop, resync_interval=0)
    finally:
        scheduler.sync_with_database = original_sync

    assert call_count >= 2, "loop must retry after a sync error"
    assert not scheduler._is_running


def test_scheduler_disabled_lifespan_skips_startup(monkeypatch) -> None:
    """With SCHEDULER_DISABLED=true, the FastAPI lifespan must NOT start
    APScheduler — that's the contract that lets a sidecar own the tick
    loop without fighting the web process."""
    from app.main import app

    monkeypatch.setattr("app.config.settings.scheduler_disabled", True)

    scheduler = get_scheduler()
    if scheduler._is_running:
        scheduler.shutdown()

    with TestClient(app):
        pass

    assert not scheduler._is_running, (
        "lifespan must not start APScheduler when SCHEDULER_DISABLED=true"
    )


# ---------- SSRF guard integration ----------


def _stub_report(name: str = "ssrf_test") -> Report:
    """Minimal in-memory Report for _send_notification — no DB commit."""
    return Report(id=9999, name=name)


def test_send_notification_blocks_webhook_to_loopback(monkeypatch, caplog) -> None:
    """A webhook URL pointing at 127.0.0.1 must be rejected before any
    outbound HTTP. Verifies the guard is wired into _send_notification,
    not just sitting in a module no one calls."""
    import logging

    from app.services import scheduler as scheduler_module

    called = []

    def fake_post(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("httpx.post must NOT be called for a blocked URL")

    monkeypatch.setattr(scheduler_module.httpx, "post", fake_post)

    with caplog.at_level(logging.ERROR, logger="app.services.scheduler"):
        scheduler_module._send_notification(
            notification_config={"type": "webhook", "webhook_url": "http://127.0.0.1:8000/x"},
            report=_stub_report(),
            file_paths=["/tmp/r.xlsx"],
        )

    assert called == [], "httpx.post must never be called for a blocked URL"
    assert any("SSRF guard" in rec.message for rec in caplog.records), (
        "operator-visible error must mention the SSRF guard so the cause is obvious"
    )


def test_send_notification_blocks_webhook_to_private_ip_literal(monkeypatch) -> None:
    """Same as above but for an RFC1918 IPv4 literal — covers the IP-literal
    branch of the guard, not just the DNS branch."""
    from app.services import scheduler as scheduler_module

    called = []

    def fake_post(*args, **kwargs):
        called.append(1)
        raise AssertionError("must not be called")

    monkeypatch.setattr(scheduler_module.httpx, "post", fake_post)

    scheduler_module._send_notification(
        notification_config={"type": "webhook", "webhook_url": "http://10.0.0.5/x"},
        report=_stub_report(),
        file_paths=[],
    )

    assert called == []


def test_send_notification_blocks_webhook_with_disallowed_scheme(monkeypatch) -> None:
    """file:// and other non-http schemes must also be rejected — covers the
    scheme allow-list, which is the cheapest rejection and easiest to miss."""
    from app.services import scheduler as scheduler_module

    called = []

    def fake_post(*args, **kwargs):
        called.append(1)
        raise AssertionError("must not be called")

    monkeypatch.setattr(scheduler_module.httpx, "post", fake_post)

    scheduler_module._send_notification(
        notification_config={"type": "webhook", "webhook_url": "file:///etc/passwd"},
        report=_stub_report(),
        file_paths=[],
    )

    assert called == []


def test_send_notification_delivers_valid_webhook(monkeypatch) -> None:
    """Sanity check: a public IP literal passes the guard and httpx.post
    is invoked exactly once with the expected payload + follow_redirects=False."""
    from app.services import scheduler as scheduler_module

    captured = []

    class FakeResponse:
        status_code = 200

    def fake_post(url, **kwargs):
        captured.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(scheduler_module.httpx, "post", fake_post)

    scheduler_module._send_notification(
        notification_config={"type": "webhook", "webhook_url": "https://8.8.8.8/hook"},
        report=Report(id=42, name="ok"),
        file_paths=["/tmp/r.xlsx"],
    )

    assert len(captured) == 1
    url, kwargs = captured[0]
    assert url == "https://8.8.8.8/hook"
    assert kwargs["follow_redirects"] is False, (
        "explicit follow_redirects=False prevents a 30x from smuggling a host"
    )
    assert kwargs["timeout"] == 30
    assert kwargs["json"]["report_id"] == 42

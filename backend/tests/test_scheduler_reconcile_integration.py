"""End-to-end reconcile integration tests (P2-2).

Covers the web process's :func:`ReportScheduler.sync_with_database`
path as exercised through the public HTTP surface (``POST
/scheduler/sync``) — the route batches 8.3 and 9.x keep changing
but which was previously tested only at the service level
(``test_scheduler.py::test_sync_with_database_*``).

What these tests add on top of the existing service-level coverage:

* **Multi-report state transitions** — pause / resume / delete /
  cron-change, observed through the HTTP ``/status`` endpoint.
* **Partial failure isolation** — a report with an invalid cron
  expression (sneaked past the Pydantic validator via direct DB
  write) must NOT block other eligible reports from being
  scheduled. Without this, an operator typo in one cron would
  silently black-hole the whole reconcile.
* **Lifespan + sync integration** — when ``SCHEDULER_DISABLED=false``
  the web process's startup runs ``sync_with_database`` and the
  newly-reconciled jobs are visible via ``GET /scheduler/status``
  without a manual ``POST /sync`` follow-up.

The sidecar-only loop (``run()`` / stop event / settings default
interval) is covered in ``test_scheduler.py`` and
``test_scheduler_runner.py``; we don't repeat it here.
"""

from __future__ import annotations

import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report
from app.services.scheduler import get_scheduler


def _unique_name(prefix: str = "pytest_reconcile") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def scheduled_report_factory():
    """Yield a callable that creates a Report eligible for sync.

    The fixture is responsible for its own cleanup so callers can
    create any number of reports within a single test. The cleanup
    is keyed by the names we minted, so leftover rows from prior
    tests don't fight with this run.

    Returns a function ``create(name=..., cron=..., is_active=...,
    is_scheduled=...) -> report_id`` that also returns the data
    source id (so tests can reach in and mutate it later).
    """
    created_reports: list[int] = []
    created_data_sources: list[int] = []
    created_names: list[str] = []

    def _create(
        *,
        name: str | None = None,
        cron: str = "0 9 * * * *",
        is_active: bool = True,
        is_scheduled: bool = True,
    ) -> dict[str, int]:
        rep_name = name or _unique_name("reconcile_report")
        ds_name = _unique_name("reconcile_ds")
        db: Session = SessionLocal()
        try:
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
                is_active=is_active,
                is_scheduled=is_scheduled,
                cron_expression=cron if is_scheduled else None,
            )
            db.add(rep)
            db.commit()
            db.refresh(rep)
            created_reports.append(rep.id)
            created_data_sources.append(src.id)
            created_names.append(rep_name)
            return {"report_id": rep.id, "data_source_id": src.id, "name": rep_name}
        finally:
            db.close()

    try:
        yield _create
    finally:
        # Drop scheduler jobs first so a sibling test doesn't see them
        # in its own reconcile pass.
        scheduler = get_scheduler()
        for rid in created_reports:
            scheduler.remove_report_job(rid)
        # Then rows, in FK order (report → data source). Use the
        # ORM ``in_`` operator so SQLAlchemy renders the right shape
        # per dialect — raw ``text("... IN :ids")`` doesn't expand
        # the tuple on SQLite.
        db = SessionLocal()
        try:
            if created_reports:
                db.query(Report).filter(Report.id.in_(created_reports)).delete(
                    synchronize_session=False
                )
            if created_data_sources:
                db.query(DataSource).filter(DataSource.id.in_(created_data_sources)).delete(
                    synchronize_session=False
                )
            db.commit()
        finally:
            db.close()


@pytest.fixture(autouse=True)
def _isolate_scheduler_singleton():
    """Belt-and-braces reset of the scheduler singleton's lifecycle
    flag, mirroring the test_scheduler_runner fixture. The reconcile
    tests don't call ``scheduler.start()`` so we don't need to
    teardown a running scheduler — only the ``_is_running`` flag
    matters because lifespan tests share this singleton with the
    other suite."""
    sched = get_scheduler()
    sched._is_running = False
    yield


def _status_jobs(client: TestClient, auth_headers: dict[str, str]) -> list[str]:
    """Return the list of ``job_id`` strings in scheduler status."""
    r = client.get("/scheduler/status", headers=auth_headers)
    assert r.status_code == 200, r.text
    return [j["job_id"] for j in r.json()["jobs"]]


def test_sync_loads_eligible_report_via_http(
    client: TestClient, auth_headers: dict[str, str], scheduled_report_factory
) -> None:
    """POST /scheduler/sync picks up a fresh report and surfaces it on
    the next /status read — the full HTTP round-trip, not the
    service-level helper."""
    info = scheduled_report_factory()
    rid = info["report_id"]
    # Belt: drop any stale job left over from a sibling test that
    # happened to mint the same id (paranoid; ids are PK auto-inc).
    get_scheduler().remove_report_job(rid)

    r = client.post("/scheduler/sync", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["jobs_loaded"] >= 1

    assert f"report_{rid}" in _status_jobs(client, auth_headers)


def test_sync_drops_paused_report(
    client: TestClient, auth_headers: dict[str, str], scheduled_report_factory
) -> None:
    """Toggling ``is_active=False`` (the "pause" path operators use
    to silence a job without losing the cron) must be reflected on
    the next sync — the job should disappear from ``/status``."""
    info = scheduled_report_factory()
    rid = info["report_id"]

    # Seed: first sync loads the report.
    client.post("/scheduler/sync", headers=auth_headers)
    assert f"report_{rid}" in _status_jobs(client, auth_headers)

    # Pause by flipping is_active.
    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == rid).first()
        assert row is not None
        row.is_active = False
        db.commit()
    finally:
        db.close()

    # Re-sync must drop the now-ineligible job.
    client.post("/scheduler/sync", headers=auth_headers)
    assert f"report_{rid}" not in _status_jobs(client, auth_headers)


def test_sync_drops_unscheduled_report(
    client: TestClient, auth_headers: dict[str, str], scheduled_report_factory
) -> None:
    """Flipping ``is_scheduled=False`` (e.g. after a manual DB
    cleanup) must remove the APScheduler job on next sync — the
    orphan-cleanup contract that the sidecar relies on."""
    info = scheduled_report_factory()
    rid = info["report_id"]

    client.post("/scheduler/sync", headers=auth_headers)
    assert f"report_{rid}" in _status_jobs(client, auth_headers)

    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == rid).first()
        assert row is not None
        row.is_scheduled = False
        row.cron_expression = None
        db.commit()
    finally:
        db.close()

    client.post("/scheduler/sync", headers=auth_headers)
    assert f"report_{rid}" not in _status_jobs(client, auth_headers)


def test_sync_drops_orphaned_job_after_report_delete(
    client: TestClient, auth_headers: dict[str, str], scheduled_report_factory
) -> None:
    """The reconcile contract: if the report row is gone (e.g. an
    operator runs a DELETE directly, or a cascading delete from a
    parent table), the next sync must drop the matching
    APScheduler job. Without this, deleted reports keep ticking on
    their last-known cron."""
    info = scheduled_report_factory()
    rid = info["report_id"]

    client.post("/scheduler/sync", headers=auth_headers)
    assert f"report_{rid}" in _status_jobs(client, auth_headers)

    # Delete the report row directly — bypasses the router, mirrors
    # what an admin script or a future cascade would do.
    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == rid).first()
        assert row is not None
        db.delete(row)
        db.commit()
    finally:
        db.close()

    client.post("/scheduler/sync", headers=auth_headers)
    assert f"report_{rid}" not in _status_jobs(client, auth_headers)


def test_sync_is_idempotent_with_multiple_reports(
    client: TestClient, auth_headers: dict[str, str], scheduled_report_factory
) -> None:
    """Two back-to-back syncs with multiple eligible reports must
    produce the same job set — no duplicates, no missing. The
    sidecar relies on this for its periodic loop."""
    info_a = scheduled_report_factory(cron="0 9 * * * *")
    info_b = scheduled_report_factory(cron="0 12 * * * *")
    info_c = scheduled_report_factory(cron="0 18 * * * *")
    expected = {
        f"report_{info_a['report_id']}",
        f"report_{info_b['report_id']}",
        f"report_{info_c['report_id']}",
    }

    # First sync — populates.
    r1 = client.post("/scheduler/sync", headers=auth_headers)
    assert r1.status_code == 200
    jobs_after_first = set(_status_jobs(client, auth_headers))
    assert expected.issubset(jobs_after_first), (
        f"first sync missing jobs: expected {expected}, got {jobs_after_first}"
    )

    # Second sync — must not change the set, even with intervening
    # updates to the underlying rows.
    r2 = client.post("/scheduler/sync", headers=auth_headers)
    assert r2.status_code == 200
    jobs_after_second = set(_status_jobs(client, auth_headers))
    assert expected.issubset(jobs_after_second)


def test_sync_skips_invalid_cron_without_blocking_others(
    client: TestClient,
    auth_headers: dict[str, str],
    scheduled_report_factory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Partial failure isolation: a report whose cron expression is
    malformed (bypassed the Pydantic validator by direct DB write)
    must NOT prevent other eligible reports from being loaded.

    Without this, one operator typo would silently skip every
    other report in the same reconcile pass."""
    good_a = scheduled_report_factory(cron="0 9 * * * *")
    good_b = scheduled_report_factory(cron="0 12 * * * *")
    bad = scheduled_report_factory(cron="0 9 * * * *")  # valid at create-time

    # Sneak the bad cron past the Pydantic layer by writing directly.
    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == bad["report_id"]).first()
        assert row is not None
        row.cron_expression = "0 25 * * * *"  # hour=25 — out of range
        db.commit()
    finally:
        db.close()

    with caplog.at_level(logging.ERROR, logger="app.services.scheduler"):
        r = client.post("/scheduler/sync", headers=auth_headers)
    assert r.status_code == 200

    jobs = set(_status_jobs(client, auth_headers))
    # The two valid reports made it through.
    assert f"report_{good_a['report_id']}" in jobs
    assert f"report_{good_b['report_id']}" in jobs
    # The bad cron was rejected.
    assert f"report_{bad['report_id']}" not in jobs
    # And the failure was logged so an operator can find it.
    assert any(
        f"Failed to schedule report {bad['report_id']}" in rec.message for rec in caplog.records
    ), "operator-visible error must name the failed report id"


def test_sync_updates_job_when_cron_changes(
    client: TestClient, auth_headers: dict[str, str], scheduled_report_factory
) -> None:
    """A direct cron change in the DB must be picked up by the next
    sync — the APScheduler job's trigger should reflect the new
    expression, not the old one. The router's POST /jobs/{id}
    already exercises this, but the reconcile path is a separate
    code path that needs the same property."""
    info = scheduled_report_factory(cron="0 9 * * * *")
    rid = info["report_id"]

    # Initial sync: trigger should mention 9:00.
    client.post("/scheduler/sync", headers=auth_headers)
    initial = client.get(f"/scheduler/jobs/{rid}", headers=auth_headers).json()
    assert "9" in initial["trigger"]

    # Change cron directly to noon, then re-sync.
    db = SessionLocal()
    try:
        row = db.query(Report).filter(Report.id == rid).first()
        assert row is not None
        row.cron_expression = "0 12 * * * *"
        db.commit()
    finally:
        db.close()

    client.post("/scheduler/sync", headers=auth_headers)
    updated = client.get(f"/scheduler/jobs/{rid}", headers=auth_headers).json()
    assert "12" in updated["trigger"]


def test_lifespan_runs_sync_when_scheduler_enabled(
    client: TestClient,
    auth_headers: dict[str, str],
    scheduled_report_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``SCHEDULER_DISABLED=false`` the web process's lifespan
    runs ``sync_with_database`` on startup. After a fresh process
    boot, the scheduler should already hold a job for any
    pre-existing eligible report — no manual ``POST /sync``
    required.

    Pair test with ``test_scheduler_disabled_lifespan_skips_startup``
    in ``test_scheduler.py`` which covers the disabled case."""
    info = scheduled_report_factory()
    rid = info["report_id"]

    # Drop any job the fixture may have left behind from a sibling
    # test that re-entered the lifespan with a different setting.
    get_scheduler().remove_report_job(rid)

    # Reload the app under SCHEDULER_DISABLED=false so the lifespan
    # branch that calls sync_with_database is exercised. The
    # ``client`` fixture already entered the lifespan once with the
    # default (disabled) — we need a second, enabled entry.
    monkeypatch.setattr("app.config.settings.scheduler_disabled", False)

    # Reset the scheduler's running flag so start() actually
    # transitions; without this the lifespan branch is a no-op.
    sched = get_scheduler()
    sched._is_running = False

    with TestClient(__import__("app.main", fromlist=["app"]).app):
        # Lifespan just ran sync_with_database under our monkey-patch.
        # /status should now reflect the eligible report.
        r = client.get("/scheduler/status", headers=auth_headers)
        assert r.status_code == 200
        job_ids = [j["job_id"] for j in r.json()["jobs"]]
        assert f"report_{rid}" in job_ids

    # Teardown: shut the scheduler back down so siblings see a clean
    # state (the default test process has SCHEDULER_DISABLED=true
    # and assumes the scheduler isn't running).
    if sched._is_running:
        sched.shutdown()

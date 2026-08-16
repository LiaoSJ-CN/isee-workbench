"""Tests for the async report-job queue (批 3a).

Covers:

* :func:`enqueue_report_job` — pending row + executor.submit
* :func:`_run_job` — running → done on success, running → failed on error
* HTTP endpoints — auth, validation, response shape, history filter

Worker-callback tests call :func:`_run_job` directly instead of going
through :class:`ThreadPoolExecutor`. The executor wiring is one
integration test (``test_enqueue_submits_to_executor``) that lets the
real pool pick the task up and polls the row until terminal — slow on
purpose so we notice if the executor wiring breaks.
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report
from app.models.report_job import (
    JOB_STATUS_DONE,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    ReportJob,
)
from app.models.user import User
from app.services.job_queue import (
    _futures,
    _run_job,
    enqueue_report_job,
    get_executor,
)


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def temp_report_with_sqlite() -> Any:
    """A Report backed by an in-memory SQLite DataSource, with no items.

    Items aren't needed for the queue contract — the only path under
    test is "row created → executor submitted → _run_job drives status
    transitions".  The Excel renderer walks ``report.items``; for
    failure tests that's fine (the renderer raises on no items), and for
    success tests we keep it empty so the render path stays fast.
    """
    db: Session = SessionLocal()
    rep_name = _unique("pytest_job_report")
    ds_name = _unique("pytest_job_ds")
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
        # Clean up jobs + report + source so siblings don't see leftovers.
        db.query(ReportJob).filter(ReportJob.report_id == rid).delete()
        db.commit()
        db.delete(rep)
        db.commit()
        db.delete(src)
        db.commit()
        db.close()


def _wait_for_terminal(job_id: int, timeout: float = 5.0) -> ReportJob | None:
    """Poll the row until status hits done/failed or timeout expires.

    Used by the executor integration test — the worker thread can be
    busy with siblings from earlier tests in the same module.

    Re-runs a fresh ``query().filter()`` each iteration rather than
    ``db.get()`` because ``get`` caches in the session's identity map
    and would return the stale ``pending`` row even after the worker
    commits ``done``.
    """
    deadline = time.monotonic() + timeout
    db = SessionLocal()
    try:
        while time.monotonic() < deadline:
            db.expire_all()
            row = (
                db.query(ReportJob)
                .filter(ReportJob.id == job_id)
                .one_or_none()
            )
            if row is not None and row.status in (JOB_STATUS_DONE, JOB_STATUS_FAILED):
                return row
            time.sleep(0.05)
        return None
    finally:
        db.close()


# ----------------- enqueue_report_job -----------------


def test_enqueue_creates_pending_row_and_submits(
    temp_report_with_sqlite: int, db_setup: Any
) -> None:
    """A pending row lands in DB and a Future is registered for the job."""
    db, user = db_setup
    rid = temp_report_with_sqlite
    _futures.clear()

    job = enqueue_report_job(
        db=db, report_id=rid, output_format="excel", user=user, parameters={}
    )
    try:
        assert job.status == JOB_STATUS_PENDING
        assert job.report_id == rid
        assert job.created_by == user.username
        assert job.output_format == "excel"
        # Future registered so callers can introspect in-flight work.
        assert isinstance(_futures.get(job.id), Future)
    finally:
        # Drain the future so we don't leak a worker.
        fut = _futures.pop(job.id, None)
        if fut is not None:
            fut.result(timeout=5)


def test_enqueue_rejects_unknown_report(db_setup: Any) -> None:
    """Missing report → LookupError (router maps to 404)."""
    db, user = db_setup
    with pytest.raises(LookupError):
        enqueue_report_job(
            db=db, report_id=99_999_999, output_format="excel", user=user, parameters={}
        )


def test_enqueue_rejects_html_format(db_setup: Any) -> None:
    """HTML preview stays synchronous — ValueError if someone queues it."""
    db, user = db_setup
    # Find any report id (we don't need a valid one — the format check fires first).
    rid = db.query(Report).first()
    if rid is None:
        pytest.skip("no reports in DB")
    with pytest.raises(ValueError):
        enqueue_report_job(
            db=db,
            report_id=int(rid.id),
            output_format="html",
            user=user,
            parameters={},
        )


def test_enqueue_submits_to_executor(
    temp_report_with_sqlite: int, db_setup: Any
) -> None:
    """End-to-end: executor actually picks up the task and finishes it.

    Uses a real :class:`ThreadPoolExecutor` so a wiring bug (e.g.
    submit() throwing, or the future never resolving) shows up here.

    Empty-items report → renderer writes a Summary-only workbook, so
    we expect ``done`` here (an item-bearing fixture is covered by
    :func:`test_run_job_success_path`).
    """
    db, user = db_setup
    rid = temp_report_with_sqlite
    _futures.clear()

    job = enqueue_report_job(
        db=db, report_id=rid, output_format="excel", user=user, parameters={}
    )

    final = _wait_for_terminal(job.id)
    assert final is not None, "executor did not finish job within timeout"
    assert final.status == JOB_STATUS_DONE
    assert final.file_path is not None
    assert final.started_at is not None
    assert final.finished_at is not None


# ----------------- _run_job (synchronous, no executor) -----------------


def test_run_job_marks_failed_when_report_missing(db_setup: Any) -> None:
    """A Report that disappears between enqueue and run is marked failed."""
    db, _ = db_setup
    job = ReportJob(
        report_id=99_999_999,
        output_format="excel",
        parameters={},
        created_by="pytest",
        status=JOB_STATUS_PENDING,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    _run_job(int(job.id))

    db.refresh(job)
    assert job.status == JOB_STATUS_FAILED
    assert "no longer exists" in (job.error or "")
    assert job.finished_at is not None


def test_run_job_records_started_and_finished(
    temp_report_with_sqlite: int, db_setup: Any
) -> None:
    """Running a job populates started_at / finished_at on the row."""
    db, _ = db_setup
    rid = temp_report_with_sqlite

    job = ReportJob(
        report_id=rid,
        output_format="excel",
        parameters={},
        created_by="pytest",
        status=JOB_STATUS_PENDING,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    _run_job(int(job.id))

    db.refresh(job)
    # Empty items → Summary-only Excel writes successfully, so status=done.
    assert job.status == JOB_STATUS_DONE
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.started_at <= job.finished_at


def test_run_job_success_path(temp_report_with_sqlite: int, db_setup: Any) -> None:
    """Add a text item (no SQL needed) → run succeeds with file_path set."""
    db, _ = db_setup
    rid = temp_report_with_sqlite

    # Text items skip the SQL pipeline so the renderer always succeeds.
    from app.models.report import ReportItem

    db.add(
        ReportItem(
            report_id=rid, name="hello", item_type="text", order_index=0
        )
    )
    db.commit()

    job = ReportJob(
        report_id=rid,
        output_format="excel",
        parameters={},
        created_by="pytest",
        status=JOB_STATUS_PENDING,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    _run_job(int(job.id))

    db.refresh(job)
    assert job.status == JOB_STATUS_DONE
    assert job.file_path is not None
    assert job.error is None
    assert job.finished_at is not None


# ----------------- HTTP endpoints -----------------


def test_post_jobs_requires_auth(
    client: TestClient, temp_report_with_sqlite: int
) -> None:
    r = client.post(f"/reports/{temp_report_with_sqlite}/jobs", json={})
    assert r.status_code == 401


def test_post_jobs_creates_pending_job(
    client: TestClient,
    temp_report_with_sqlite: int,
    auth_headers: dict,
) -> None:
    r = client.post(
        f"/reports/{temp_report_with_sqlite}/jobs",
        json={"output_format": "excel", "parameters": {}},
        headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["report_id"] == temp_report_with_sqlite
    assert body["status"] in {"pending", "running", "done", "failed"}
    assert body["output_format"] == "excel"
    assert body["created_by"]  # admin user
    # Drain so the executor doesn't leak across tests.
    _wait_for_terminal(body["id"])


def test_post_jobs_404_for_missing_report(
    client: TestClient, auth_headers: dict
) -> None:
    r = client.post(
        "/reports/99999999/jobs", json={}, headers=auth_headers
    )
    assert r.status_code == 404


def test_post_jobs_rejects_html(client: TestClient, temp_report_with_sqlite: int,
                                 auth_headers: dict) -> None:
    """output_format=html fails — the enum only exposes 'excel'."""
    r = client.post(
        f"/reports/{temp_report_with_sqlite}/jobs",
        json={"output_format": "html"},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_get_job_returns_row(
    client: TestClient,
    temp_report_with_sqlite: int,
    auth_headers: dict,
) -> None:
    create = client.post(
        f"/reports/{temp_report_with_sqlite}/jobs",
        json={"output_format": "excel"},
        headers=auth_headers,
    )
    assert create.status_code == 201
    job_id = create.json()["id"]

    r = client.get(f"/jobs/{job_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["id"] == job_id


def test_get_job_404_for_unknown_id(
    client: TestClient, auth_headers: dict
) -> None:
    r = client.get("/jobs/99999999", headers=auth_headers)
    assert r.status_code == 404


def test_get_job_requires_auth(client: TestClient) -> None:
    r = client.get("/jobs/1")
    assert r.status_code == 401


def test_list_jobs_history(
    client: TestClient,
    temp_report_with_sqlite: int,
    auth_headers: dict,
) -> None:
    rid = temp_report_with_sqlite
    # Create two jobs.
    for _ in range(2):
        client.post(
            f"/reports/{rid}/jobs",
            json={"output_format": "excel"},
            headers=auth_headers,
        )

    r = client.get(f"/reports/{rid}/jobs", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 2
    # Most recent first.
    assert rows[0]["created_at"] >= rows[-1]["created_at"]


def test_list_jobs_filter_by_status(
    client: TestClient,
    temp_report_with_sqlite: int,
    auth_headers: dict,
) -> None:
    rid = temp_report_with_sqlite
    r = client.get(
        f"/reports/{rid}/jobs",
        params={"status": "failed"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    for row in r.json():
        assert row["status"] == "failed"


def test_list_jobs_pagination(
    client: TestClient,
    temp_report_with_sqlite: int,
    auth_headers: dict,
) -> None:
    rid = temp_report_with_sqlite
    r = client.get(
        f"/reports/{rid}/jobs",
        params={"limit": 1, "offset": 0},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert len(r.json()) <= 1


def test_list_jobs_404_for_missing_report(
    client: TestClient, auth_headers: dict
) -> None:
    r = client.get("/reports/99999999/jobs", headers=auth_headers)
    assert r.status_code == 404


# ----------------- GET /jobs/{id}/download (批 8.5) -----------------


def _make_done_job(
    db: Session,
    report_id: int,
    file_path: str | None,
    status: str = JOB_STATUS_DONE,
) -> ReportJob:
    """Insert a ReportJob row in a chosen terminal state with optional file_path."""
    job = ReportJob(
        report_id=report_id,
        output_format="excel",
        parameters={},
        created_by="pytest",
        status=status,
        file_path=file_path,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_download_job_requires_auth(client: TestClient) -> None:
    r = client.get("/jobs/1/download")
    assert r.status_code == 401


def test_download_job_serves_worker_file(
    client: TestClient,
    temp_report_with_sqlite: int,
    auth_headers: dict,
    tmp_path: Path,
) -> None:
    """A ``done`` job whose ``file_path`` points to a real file → 200 + bytes.

    Uses pytest's ``tmp_path`` so the test never touches
    ``settings.generated_reports_dir``; instead we monkeypatch the
    settings attribute to point at the temp dir, mirroring the
    worker's real write target.
    """
    db = SessionLocal()
    try:
        rid = temp_report_with_sqlite
        out_dir = tmp_path / "reports_out"
        out_dir.mkdir()
        target = out_dir / "report_42.xlsx"
        target.write_bytes(b"fake-xlsx-bytes")

        job = _make_done_job(db, rid, file_path=str(target))
        job_id = job.id
    finally:
        db.close()

    # Settings.generated_reports_dir is module-level; monkeypatch via
    # FastAPI dependency would be heavier than the test needs. We swap
    # it directly and restore in finally.
    from app.config import settings

    original = settings.generated_reports_dir
    settings.generated_reports_dir = tmp_path / "reports_out"
    try:
        r = client.get(f"/jobs/{job_id}/download", headers=auth_headers)
    finally:
        settings.generated_reports_dir = original

    assert r.status_code == 200
    assert (
        r.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert r.content == b"fake-xlsx-bytes"
    # FileResponse sets a Content-Disposition with the basename.
    assert "report_42.xlsx" in r.headers.get("content-disposition", "")


def test_download_job_404_for_unknown_id(
    client: TestClient, auth_headers: dict
) -> None:
    r = client.get("/jobs/99999999/download", headers=auth_headers)
    assert r.status_code == 404


def test_download_job_404_when_pending(
    client: TestClient,
    temp_report_with_sqlite: int,
    auth_headers: dict,
) -> None:
    """A pending job has no file yet — 404 with a clear message."""
    db = SessionLocal()
    try:
        rid = temp_report_with_sqlite
        job = _make_done_job(
            db, rid, file_path=None, status=JOB_STATUS_PENDING
        )
        job_id = job.id
    finally:
        db.close()

    r = client.get(f"/jobs/{job_id}/download", headers=auth_headers)
    assert r.status_code == 404
    assert "not available" in r.json()["detail"]


def test_download_job_404_when_failed(
    client: TestClient,
    temp_report_with_sqlite: int,
    auth_headers: dict,
) -> None:
    """Failed jobs have file_path=None — same 404 contract as pending."""
    db = SessionLocal()
    try:
        rid = temp_report_with_sqlite
        job = _make_done_job(
            db, rid, file_path=None, status=JOB_STATUS_FAILED
        )
        job_id = job.id
    finally:
        db.close()

    r = client.get(f"/jobs/{job_id}/download", headers=auth_headers)
    assert r.status_code == 404


def test_download_job_404_when_done_but_file_missing(
    client: TestClient,
    temp_report_with_sqlite: int,
    auth_headers: dict,
    tmp_path: Path,
) -> None:
    """``status=done`` with a stale ``file_path`` (manual cleanup) → 404.

    We point file_path at a path that does NOT exist to simulate the
    case where the worker wrote a file but it was rotated out from
    under us. The 404 message distinguishes this from "not done".
    """
    db = SessionLocal()
    try:
        rid = temp_report_with_sqlite
        out_dir = tmp_path / "missing_reports_out"
        out_dir.mkdir()  # exists, but the named file inside does not
        stale = out_dir / "never_existed.xlsx"
        job = _make_done_job(db, rid, file_path=str(stale))
        job_id = job.id
    finally:
        db.close()

    from app.config import settings

    original = settings.generated_reports_dir
    settings.generated_reports_dir = tmp_path / "missing_reports_out"
    try:
        r = client.get(f"/jobs/{job_id}/download", headers=auth_headers)
    finally:
        settings.generated_reports_dir = original

    assert r.status_code == 404
    assert "missing" in r.json()["detail"].lower()


def test_download_job_strips_directory_components(
    client: TestClient,
    temp_report_with_sqlite: int,
    auth_headers: dict,
    tmp_path: Path,
) -> None:
    """``file_path`` carrying traversal segments resolves to basename only.

    If a future worker writes ``../../etc/passwd``, the endpoint should
    look for ``passwd`` inside ``generated_reports_dir`` (not follow
    the ``..`` up to ``/etc``). Even if that file happened to exist
    there (it won't), it would only be readable inside the output dir.
    Here we just confirm the path resolves to the basename — no file
    means 404, but the security guarantee is that we never escape.
    """
    db = SessionLocal()
    try:
        rid = temp_report_with_sqlite
        out_dir = tmp_path / "traversal_out"
        out_dir.mkdir()
        job = _make_done_job(
            db,
            rid,
            file_path="../../../etc/passwd",  # tries to escape
        )
        job_id = job.id
    finally:
        db.close()

    from app.config import settings

    original = settings.generated_reports_dir
    settings.generated_reports_dir = tmp_path / "traversal_out"
    try:
        r = client.get(f"/jobs/{job_id}/download", headers=auth_headers)
    finally:
        settings.generated_reports_dir = original

    # The ``..`` segments are stripped to ``passwd`` and looked up
    # inside the output dir → not found → 404 (never reads /etc/passwd).
    assert r.status_code == 404


# ----------------- fixtures shared across the file -----------------


@pytest.fixture
def db_setup() -> Any:
    """Pair of (Session, admin User) so each test gets a clean handle."""
    db = SessionLocal()
    user = db.query(User).filter(User.username == "admin").first()
    if user is None:
        db.close()
        pytest.skip("admin user not seeded")
    try:
        yield db, user
    finally:
        db.close()


# Touch the executor at module load so the singleton exists by the time
# the integration test runs (avoids first-call latency being interpreted
# as a hang by the polling loop).
@pytest.fixture(autouse=True)
def _warm_executor() -> Any:
    get_executor()
    yield

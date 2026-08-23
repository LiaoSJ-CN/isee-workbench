"""Tests for the PDF export feature (批 8.1).

Covers three layers:

* :func:`app.services.report_generator.renderers.pdf.render_pdf`
  — pure-unit: mock weasyprint so the test never needs the heavy
  native libraries. Exercises both the happy path (weasyprint called
  with the HTML renderer's output) and the friendlier errors we
  raise when weasyprint or its native libs are missing.
* :func:`enqueue_report_job` — confirms ``output_format="pdf"`` is
  accepted at the queue layer and that ``"html"`` (sync preview) is
  still rejected.
* HTTP ``/jobs/{id}/download`` — ``Content-Type`` dispatch based on
  ``ReportJob.output_format``: Excel keeps the spreadsheet MIME
  (regression guard for batch 8.5), PDF gets ``application/pdf``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report
from app.models.report_job import (
    JOB_STATUS_DONE,
    JOB_STATUS_PENDING,
    ReportJob,
)
from app.models.user import User
from app.services.job_queue import (
    _futures,
    enqueue_report_job,
)
from app.services.report_generator.errors import ReportGeneratorError
from app.services.report_generator.renderers import pdf as pdf_mod
from app.services.report_generator.renderers.pdf import render_pdf

# -------------------- helpers --------------------


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def db_setup() -> Any:
    """Pair of (Session, admin User). Mirrors the local fixture in
    ``test_job_queue.py`` — defined here too so this file is self-contained."""
    db = SessionLocal()
    user = db.query(User).filter(User.username == "admin").first()
    if user is None:
        db.close()
        pytest.skip("admin user not seeded")
    try:
        yield db, user
    finally:
        db.close()


@pytest.fixture
def stub_report() -> Report:
    """An empty Report row backed by an in-memory SQLite DataSource.

    PDF testing focuses on the render dispatch and the data-source
    attachment; we don't need items because we mock the renderer
    downstream so no SQL ever fires.
    """
    db: Session = SessionLocal()
    rep_name = _unique("pytest_pdf_report")
    ds_name = _unique("pytest_pdf_ds")
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
        yield rep
    finally:
        db.query(ReportJob).filter(ReportJob.report_id == rid).delete()
        db.commit()
        db.delete(rep)
        db.commit()
        db.delete(src)
        db.commit()
        db.close()


# -------------------- render_pdf unit tests --------------------


def test_render_pdf_invokes_weasyprint_with_html_string(monkeypatch: Any) -> None:
    """render_pdf must call weasyprint.HTML(string=...).write_pdf()
    on the HTML renderer's output."""
    captured: dict[str, Any] = {}

    class _FakeHTML:
        def __init__(self, *, string: str | None = None, **_kw: Any) -> None:
            captured["string"] = string

        def write_pdf(self) -> bytes:
            captured["write_pdf_called"] = True
            return b"%PDF-1.4\n%fake-pdf-bytes\n"

    class _FakeWeasyprint:
        HTML = _FakeHTML

    monkeypatch.setattr(pdf_mod, "_load_weasyprint", lambda: _FakeWeasyprint)

    out = render_pdf(
        data={"only": pd.DataFrame({"x": [1, 2]})},
        report=Report(name="unit-test", id=1),
        base_url=None,
        errors=None,
    )

    assert out.startswith(b"%PDF-")
    assert captured["write_pdf_called"] is True
    # The HTML passed to weasyprint must be a non-empty string — proves
    # we routed through render_html.
    assert isinstance(captured["string"], str)
    assert "<html" in captured["string"].lower()


def test_render_pdf_missing_weasyprint_raises_actionable_error(
    monkeypatch: Any,
) -> None:
    """No weasyprint installed → friendly error naming the install command.

    Tests :func:`_load_weasyprint` directly rather than render_pdf so
    the error message comes from the real loader with the real
    Install instruction (the loader wraps ImportError/OSError into
    :class:`ReportGeneratorError`).
    """
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "weasyprint" or name.startswith("weasyprint."):
            raise ImportError("No module named 'weasyprint'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(ReportGeneratorError) as exc_info:
        pdf_mod._load_weasyprint()
    msg = str(exc_info.value).lower()
    assert "weasyprint" in msg
    assert "pip install" in msg


def test_render_pdf_missing_native_libs_raises_actionable_error(
    monkeypatch: Any,
) -> None:
    """OSError (missing libpango etc.) should fall through the same
    friendly error path rather than crashing the worker.

    weasyprint itself triggers an OSError when the package is
    installed but the shared libraries (libpango / libgobject / …)
    aren't, because the C extension fails to load. Simulate that
    by making the underlying import raise OSError directly.
    """
    import builtins

    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "weasyprint" or name.startswith("weasyprint."):
            raise OSError("libgobject-2.0-0: cannot open shared object")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(ReportGeneratorError) as exc_info:
        pdf_mod._load_weasyprint()
    msg = str(exc_info.value).lower()
    assert "native libraries" in msg


# -------------------- queue + format dispatch --------------------


def test_enqueue_accepts_pdf_format(db_setup: Any, stub_report: Report) -> None:
    """`output_format='pdf'` is accepted by the queue (新增批 8.1)."""
    db, user = db_setup
    _futures.clear()

    job = enqueue_report_job(
        db=db,
        report_id=stub_report.id,
        output_format="pdf",
        user=user,
        parameters={},
    )
    try:
        assert job.status == JOB_STATUS_PENDING
        assert job.output_format == "pdf"
    finally:
        fut = _futures.pop(job.id, None)
        if fut is not None:
            fut.result(timeout=5)


def test_enqueue_rejects_html_format(db_setup: Any, stub_report: Report) -> None:
    """HTML preview stays synchronous. Regression guard for batch 3a."""
    db, user = db_setup
    with pytest.raises(ValueError, match="excel.*pdf"):
        enqueue_report_job(
            db=db,
            report_id=stub_report.id,
            output_format="html",
            user=user,
            parameters={},
        )


def test_enqueue_rejects_unknown_format(
    db_setup: Any, stub_report: Report
) -> None:
    """Anything outside 'excel' / 'pdf' raises ValueError (router → 400)."""
    db, user = db_setup
    with pytest.raises(ValueError):
        enqueue_report_job(
            db=db,
            report_id=stub_report.id,
            output_format="docx",
            user=user,
            parameters={},
        )


# -------------------- /jobs/{id}/download media_type dispatch --------------------


def _write_completed_job(
    db: Session,
    report_id: int,
    user: User,
    *,
    output_format: str,
    filename_suffix: str,
) -> ReportJob:
    """Insert a done-state job pointing at an on-disk file. Bypasses
    the executor so the download endpoint can be exercised in isolation."""
    # Drop the file under settings.generated_reports_dir so the
    # download route's traversal guard accepts it.
    from app.config import settings

    target = settings.generated_reports_dir / f"{filename_suffix}.{output_format}"
    target.write_bytes(b"fake-bytes-for-content-type-dispatch")
    job = ReportJob(
        report_id=report_id,
        output_format=output_format,
        priority=0,
        parameters={},
        created_by=user.username,
        status=JOB_STATUS_DONE,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        file_path=str(target),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_download_pdf_job_serves_application_pdf(
    client: TestClient,
    auth_headers: dict[str, str],
    db_setup: Any,
    stub_report: Report,
) -> None:
    """A done PDF job downloads with ``Content-Type: application/pdf``."""
    db, user = db_setup
    job = _write_completed_job(
        db,
        report_id=stub_report.id,
        user=user,
        output_format="pdf",
        filename_suffix=f"pytest_pdf_{uuid.uuid4().hex[:8]}",
    )
    try:
        resp = client.get(f"/jobs/{job.id}/download", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/pdf")
        assert resp.content == b"fake-bytes-for-content-type-dispatch"
    finally:
        db.query(ReportJob).filter(ReportJob.id == job.id).delete()
        db.commit()

        target = Path(str(job.file_path))
        if target.exists():
            target.unlink()
        db.close()


def test_download_excel_job_keeps_spreadsheet_mime(
    client: TestClient,
    auth_headers: dict[str, str],
    db_setup: Any,
    stub_report: Report,
) -> None:
    """Regression guard: the Excel MIME dispatch from batch 8.5
    survives the batch 8.1 refactor."""
    db, user = db_setup
    job = _write_completed_job(
        db,
        report_id=stub_report.id,
        user=user,
        output_format="excel",
        filename_suffix=f"pytest_xlsx_{uuid.uuid4().hex[:8]}",
    )
    try:
        resp = client.get(f"/jobs/{job.id}/download", headers=auth_headers)
        assert resp.status_code == 200
        assert "spreadsheetml.sheet" in resp.headers["content-type"]
    finally:
        db.query(ReportJob).filter(ReportJob.id == job.id).delete()
        db.commit()

        target = Path(str(job.file_path))
        if target.exists():
            target.unlink()
        db.close()


def test_download_unknown_format_uses_octet_stream(
    client: TestClient,
    auth_headers: dict[str, str],
    db_setup: Any,
    stub_report: Report,
) -> None:
    """Future formats (or stale rows from a renamed config) fall back
    to application/octet-stream rather than 500-ing on a missing key."""
    db, user = db_setup
    job = _write_completed_job(
        db,
        report_id=stub_report.id,
        user=user,
        output_format="docx",
        filename_suffix=f"pytest_octet_{uuid.uuid4().hex[:8]}",
    )
    try:
        resp = client.get(f"/jobs/{job.id}/download", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/octet-stream")
    finally:
        db.query(ReportJob).filter(ReportJob.id == job.id).delete()
        db.commit()
        target = Path(str(job.file_path))
        if target.exists():
            target.unlink()
        db.close()


# -------------------- sync GET /reports/{id}/export/pdf --------------------
# Regression guard for batch 8.1: the sync export route was extended
# to accept "pdf" alongside "excel" and "html". A future refactor
# that reverts the path regex (or the MIME map) would 422 the route
# silently for users who hit it directly. Pin both edges.


def test_sync_export_pdf_serves_application_pdf(
    monkeypatch: Any,
    client: TestClient,
    auth_headers: dict[str, str],
    db_setup: Any,
    stub_report: Report,
) -> None:
    """``GET /reports/{id}/export/pdf`` returns ``Content-Type:
    application/pdf`` with a ``%PDF-``-prefixed body.

    Avoids invoking the real ``generate_report`` (and thus the
    weasyprint native libs) by patching the renderer to return a
    known magic-bytes payload. The MIME is what we're asserting —
    the renderer correctness is covered by
    :func:`test_render_pdf_invokes_weasyprint_with_html_string` and
    the ``test_render_pdf_missing_*`` failure-mode tests above.
    """
    fake_pdf = b"%PDF-1.4\n%fake-pdf-bytes-for-mime-dispatch\n%%EOF"

    def fake_generate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Write into the real ``generated_reports_dir`` so the route's
        # ``FileResponse(path=file_path)`` can find it — same pattern
        # the production ``generate_report`` uses.
        from app.config import settings

        target_dir = Path(str(settings.generated_reports_dir))
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"pytest_sync_pdf_{uuid.uuid4().hex[:8]}.pdf"
        target.write_bytes(fake_pdf)
        return {"file_path": str(target)}

    monkeypatch.setattr(
        "app.routers.report.generate_report", fake_generate
    )

    try:
        r = client.get(
            f"/reports/{stub_report.id}/export/pdf",
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/pdf")
        # Magic-bytes prefix — a real PDF starts with ``%PDF-``. The
        # patched renderer emits the same prefix so any future
        # regression that swaps the body (e.g. streams HTML by
        # accident) gets caught here.
        assert r.content[:5] == b"%PDF-"
    finally:
        # Best-effort cleanup — the route accepts whatever path the
        # fake_generate hands back, so we can't tie these to a known
        # id. Use a glob + suffix to find ours.
        for orphan in Path("generated_reports").glob("pytest_sync_pdf_*.pdf"):
            try:
                orphan.unlink()
            except OSError:
                pass


def test_sync_export_rejects_unknown_format(
    client: TestClient,
    auth_headers: dict[str, str],
    db_setup: Any,
    stub_report: Report,
) -> None:
    """The path regex on ``GET /reports/{id}/export/{format}``
    rejects anything outside ``^(excel|html|pdf)$`` with 422.

    Companion to :func:`test_sync_export_pdf_serves_application_pdf`
    — together they pin both edges of the format dispatch.
    """
    r = client.get(
        f"/reports/{stub_report.id}/export/docx",
        headers=auth_headers,
    )
    assert r.status_code == 422

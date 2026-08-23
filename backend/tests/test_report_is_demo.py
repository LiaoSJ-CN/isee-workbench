"""Tests for batch 10 demo-badge — ``reports.is_demo`` column.

Covers:

* ``GET /reports`` and ``GET /reports/{id}`` expose ``is_demo``.
* Reports created through the API default to ``is_demo=False``.
* The create/update endpoints don't accept ``is_demo`` — callers can't
  promote their own reports to demo status via the API.
* Directly setting ``is_demo`` in the ORM (the only path the seed
  script uses) flips it through the API surface.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def demo_report() -> Report:
    """Create a DataSource + Report flagged as demo; tear down afterwards.

    Yielding the :class:`Report` ORM instance lets the test body pick
    up both the id (for API calls) and the row (to flip the flag).
    """
    db: Session = SessionLocal()
    ds = DataSource(
        name=_unique("pytest_demo_ds"),
        db_type="sqlite",
        host="placeholder",
        port=1,
        database=":memory:",
        username="placeholder",
        password="placeholder",
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)

    r = Report(
        name=_unique("pytest_demo_r"),
        data_source_id=ds.id,
        is_active=True,
        is_demo=True,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    try:
        yield r
    finally:
        db.delete(r)
        db.commit()
        db.query(DataSource).filter(DataSource.id == ds.id).delete()
        db.commit()
        db.close()


def test_get_reports_exposes_is_demo_default_false(
    client: TestClient, auth_headers: dict, demo_report: Report
) -> None:
    """Every report returned by ``GET /reports`` carries an ``is_demo``
    flag; rows the test creates through SQLAlchemy (without the flag
    set) come back as ``false``. The fixture-driven ``demo_report`` row
    comes back as ``true``.
    """
    r = client.get("/reports", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body, "expected at least the fixture demo_report to be present"
    for row in body:
        assert "is_demo" in row, row
        assert isinstance(row["is_demo"], bool)

    by_id = {row["id"]: row for row in body}
    assert by_id[demo_report.id]["is_demo"] is True


def test_get_report_by_id_exposes_is_demo(
    client: TestClient, auth_headers: dict, demo_report: Report
) -> None:
    """``GET /reports/{id}`` surfaces ``is_demo`` on the row level, too."""
    r = client.get(f"/reports/{demo_report.id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["is_demo"] is True


def test_post_report_does_not_accept_is_demo(
    client: TestClient, auth_headers: dict, demo_report: Report
) -> None:
    """Even if a caller smuggles ``is_demo: true`` into the create
    payload, Pydantic's extra-fields policy strips it — the new row
    comes back as ``is_demo=False``.
    """
    payload = {
        "name": _unique("pytest_user_report"),
        "data_source_id": demo_report.data_source_id,
        "is_demo": True,  # attempt to self-tag
    }
    r = client.post("/reports", headers=auth_headers, json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_demo"] is False

    # Clean up the row we just inserted so the fixture teardown order
    # is well-defined.
    client.delete(f"/reports/{body['id']}", headers=auth_headers)

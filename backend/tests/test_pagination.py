"""Pagination contract for list endpoints.

Covers the ``limit`` / ``offset`` query params and the
``X-Total-Count`` response header on ``GET /reports`` and
``GET /data-sources``.

Mutating tests use uniquely-named rows and tear them down so they do
not pollute the dev ``app.db``.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report


def _unique_name(prefix: str = "pytest_temp") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def temp_data_source_for_reports():
    """Create a sqlite data source with three reports attached, yield
    the (ds_id, report_ids) tuple, then delete (reports first, then ds)
    so we don't pollute the dev ``app.db``.
    """
    db: Session = SessionLocal()
    ds_name = _unique_name("ds")
    src = DataSource(
        name=ds_name,
        db_type="sqlite",
        host="placeholder",
        port=1,
        database=":memory:",
        username="placeholder",
        password="placeholder",
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    ds_id = src.id

    report_ids: list[int] = []
    try:
        for i in range(3):
            r = Report(
                name=f"{ds_name}_r{i}",
                data_source_id=ds_id,
                is_active=True,
            )
            db.add(r)
        db.commit()
        report_ids = [
            row.id
            for row in db.query(Report).filter(Report.data_source_id == ds_id).all()
        ]
        yield ds_id, report_ids
    finally:
        # Delete reports first to satisfy FK from reports.data_source_id
        # (no ON DELETE CASCADE on that relation).
        for rid in report_ids:
            db.query(Report).filter(Report.id == rid).delete()
        db.query(DataSource).filter(DataSource.id == ds_id).delete()
        db.commit()
        db.close()


# ---- /reports pagination ----


def test_list_reports_default_limit_and_total_count(
    client: TestClient, auth_headers: dict, temp_data_source_for_reports
) -> None:
    """Default request returns at most 50 rows and exposes
    X-Total-Count for the matched set.
    """
    _ds_id, report_ids = temp_data_source_for_reports
    r = client.get("/reports", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "x-total-count" in {k.lower() for k in r.headers.keys()}
    total = int(r.headers["X-Total-Count"])
    assert total >= len(report_ids)
    # We seeded 3 reports; default limit=50 should return all of them.
    body = r.json()
    returned_ids = {item["id"] for item in body}
    assert set(report_ids).issubset(returned_ids)


def test_list_reports_limit_offset(
    client: TestClient, auth_headers: dict, temp_data_source_for_reports
) -> None:
    """limit=1&offset=0 returns the first report; offset=1 returns the
    next one; X-Total-Count reports the size of the matched set.
    """
    _ds_id, report_ids = temp_data_source_for_reports
    r1 = client.get(
        "/reports",
        headers=auth_headers,
        params={"limit": 1, "offset": 0, "data_source_id": _ds_id},
    )
    assert r1.status_code == 200, r1.text
    assert int(r1.headers["X-Total-Count"]) == 3
    body1 = r1.json()
    assert len(body1) == 1
    assert body1[0]["id"] == report_ids[0]

    r2 = client.get(
        "/reports",
        headers=auth_headers,
        params={"limit": 1, "offset": 1, "data_source_id": _ds_id},
    )
    assert r2.status_code == 200
    assert int(r2.headers["X-Total-Count"]) == 3
    body2 = r2.json()
    assert len(body2) == 1
    assert body2[0]["id"] == report_ids[1]
    assert body1[0]["id"] != body2[0]["id"]


def test_list_reports_offset_past_end_is_empty(
    client: TestClient, auth_headers: dict, temp_data_source_for_reports
) -> None:
    """offset >= total returns an empty body but still reports the
    correct total in the header."""
    _ds_id, _report_ids = temp_data_source_for_reports
    r = client.get(
        "/reports",
        headers=auth_headers,
        params={"limit": 10, "offset": 9999, "data_source_id": _ds_id},
    )
    assert r.status_code == 200, r.text
    assert int(r.headers["X-Total-Count"]) == 3
    assert r.json() == []


def test_list_reports_invalid_limit_is_422(client: TestClient, auth_headers: dict) -> None:
    r = client.get("/reports", headers=auth_headers, params={"limit": 0})
    assert r.status_code == 422
    r = client.get("/reports", headers=auth_headers, params={"limit": 501})
    assert r.status_code == 422


def test_list_reports_invalid_offset_is_422(client: TestClient, auth_headers: dict) -> None:
    r = client.get("/reports", headers=auth_headers, params={"offset": -1})
    assert r.status_code == 422


# ---- /data-sources pagination ----


def test_list_data_sources_default_limit_and_total_count(
    client: TestClient, auth_headers: dict
) -> None:
    r = client.get("/data-sources", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    # Header presence is what we care about — the exact value depends on
    # whatever dev data is currently in app.db.
    assert "x-total-count" in {k.lower() for k in r.headers.keys()}
    total = int(r.headers["X-Total-Count"])
    assert total >= 0
    assert len(r.json()) == min(total, 50)


def test_list_data_sources_limit_offset(
    client: TestClient, auth_headers: dict, temp_data_source_for_reports
) -> None:
    """Pagination works on /data-sources and is stable across offset."""
    ds_id, _ = temp_data_source_for_reports
    r1 = client.get(
        "/data-sources",
        headers=auth_headers,
        params={"limit": 1, "offset": 0},
    )
    assert r1.status_code == 200, r1.text
    assert int(r1.headers["X-Total-Count"]) >= 1
    body1 = r1.json()
    assert len(body1) == 1

    # Filtering by exact id is not supported here; just confirm a second
    # page returns a *different* row when there are at least 2 sources.
    r2 = client.get(
        "/data-sources",
        headers=auth_headers,
        params={"limit": 1, "offset": 1},
    )
    assert r2.status_code == 200
    assert int(r2.headers["X-Total-Count"]) >= 2
    body2 = r2.json()
    if len(body2) == 1:
        assert body2[0]["id"] != body1[0]["id"]


def test_list_data_sources_offset_past_end_is_empty(
    client: TestClient, auth_headers: dict
) -> None:
    r = client.get(
        "/data-sources",
        headers=auth_headers,
        params={"limit": 10, "offset": 999999},
    )
    assert r.status_code == 200
    assert r.json() == []


def test_list_data_sources_invalid_limit_is_422(
    client: TestClient, auth_headers: dict
) -> None:
    r1 = client.get("/data-sources", headers=auth_headers, params={"limit": 0})
    assert r1.status_code == 422
    r2 = client.get("/data-sources", headers=auth_headers, params={"limit": 501})
    assert r2.status_code == 422


def test_list_data_sources_invalid_offset_is_422(
    client: TestClient, auth_headers: dict
) -> None:
    r = client.get("/data-sources", headers=auth_headers, params={"offset": -1})
    assert r.status_code == 422

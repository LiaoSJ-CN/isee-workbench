"""Data source CRUD + connection test + engine cache eviction.

Mutating tests use a uniquely-named source (``pytest_temp_<rand>``) and
tear it down on exit so they don't pollute the dev ``app.db``.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.services.report_generator import (
    _engine_cache,
    _get_or_create_engine,
    evict_engine,
)


def _unique_name(prefix: str = "pytest_temp") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def temp_data_source():
    """Create a sqlite data source for the test, yield its id, then delete."""
    db: Session = SessionLocal()
    name = _unique_name()
    src = DataSource(
        name=name,
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
    sid = src.id
    try:
        yield sid, name
    finally:
        db.delete(src)
        db.commit()
        # Belt-and-suspenders: drop any cached engine for the deleted id.
        evict_engine(sid)
        db.close()


def test_list_data_sources_requires_auth(client: TestClient) -> None:
    r = client.get("/data-sources")
    assert r.status_code == 401


def test_list_data_sources_with_auth(client: TestClient, auth_headers: dict) -> None:
    r = client.get("/data-sources", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_create_and_get_data_source(client: TestClient, auth_headers: dict) -> None:
    name = _unique_name()
    payload = {
        "name": name,
        "db_type": "sqlite",
        "host": "h",
        "port": 1,
        "database": ":memory:",
        "username": "u",
        "password": "p",
    }
    ds_id: int | None = None
    try:
        r = client.post("/data-sources", headers=auth_headers, json=payload)
        assert r.status_code == 201, r.text
        ds_id = r.json()["id"]

        r = client.get(f"/data-sources/{ds_id}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["name"] == name
    finally:
        if ds_id is not None:
            client.delete(f"/data-sources/{ds_id}", headers=auth_headers)


def test_create_duplicate_name_is_409(client: TestClient, auth_headers: dict) -> None:
    name = _unique_name()
    payload = {
        "name": name,
        "db_type": "sqlite",
        "host": "h",
        "port": 1,
        "database": ":memory:",
        "username": "u",
        "password": "p",
    }
    first_id: int | None = None
    try:
        r1 = client.post("/data-sources", headers=auth_headers, json=payload)
        assert r1.status_code == 201
        first_id = r1.json()["id"]
        r2 = client.post("/data-sources", headers=auth_headers, json=payload)
        assert r2.status_code == 409
    finally:
        if first_id is not None:
            client.delete(f"/data-sources/{first_id}", headers=auth_headers)


def test_get_unknown_data_source_is_404(client: TestClient, auth_headers: dict) -> None:
    r = client.get("/data-sources/9999999", headers=auth_headers)
    assert r.status_code == 404


def test_update_data_source_evicts_cached_engine(
    client: TestClient, auth_headers: dict, engine_cache_cleanup
) -> None:
    """Mutating a DataSource must invalidate the cached engine so the
    next caller rebuilds with the new connection URL.

    Regression guard: the connection-pool reuse refactor wired this up;
    if it ever silently regresses, this test fails.
    """
    name = _unique_name()
    payload = {
        "name": name,
        "db_type": "sqlite",
        "host": "h",
        "port": 1,
        "database": ":memory:",
        "username": "u",
        "password": "p",
    }
    r = client.post("/data-sources", headers=auth_headers, json=payload)
    assert r.status_code == 201
    ds_id = r.json()["id"]
    try:
        # Prime the cache by looking up the engine once.
        from app.models.data_source import DataSource as DS

        db = SessionLocal()
        try:
            ds = db.query(DS).filter(DS.id == ds_id).first()
            assert ds is not None
            _get_or_create_engine(ds)
        finally:
            db.close()
        assert ds_id in _engine_cache

        # Now update via the API — the router should evict the engine.
        r = client.put(
            f"/data-sources/{ds_id}",
            headers=auth_headers,
            json={"description": "updated by pytest"},
        )
        assert r.status_code == 200, r.text
        assert ds_id not in _engine_cache, "update must evict cached engine"
    finally:
        client.delete(f"/data-sources/{ds_id}", headers=auth_headers)


def test_delete_data_source_evicts_cached_engine(
    client: TestClient, auth_headers: dict, engine_cache_cleanup
) -> None:
    name = _unique_name()
    payload = {
        "name": name,
        "db_type": "sqlite",
        "host": "h",
        "port": 1,
        "database": ":memory:",
        "username": "u",
        "password": "p",
    }
    r = client.post("/data-sources", headers=auth_headers, json=payload)
    assert r.status_code == 201
    ds_id = r.json()["id"]

    try:
        from app.models.data_source import DataSource as DS

        db = SessionLocal()
        try:
            ds = db.query(DS).filter(DS.id == ds_id).first()
            _get_or_create_engine(ds)
        finally:
            db.close()
        assert ds_id in _engine_cache

        r = client.delete(f"/data-sources/{ds_id}", headers=auth_headers)
        assert r.status_code == 204
        assert ds_id not in _engine_cache, "delete must evict cached engine"
    finally:
        # delete already happened; this is a safety net for the assertion path.
        pass


def test_delete_data_source_with_reports_returns_409(
    client: TestClient, auth_headers: dict
) -> None:
    """A data source still referenced by a report cannot be deleted.

    ``reports.data_source_id`` is NOT NULL with no cascade, so without
    the pre-check SQLAlchemy nulls the FK on delete and the request
    dies as a 500 IntegrityError — which the e2e cleanup swallowed,
    leaving orphaned data sources in the dev database.
    """
    r = client.post(
        "/data-sources",
        headers=auth_headers,
        json={
            "name": _unique_name(),
            "db_type": "sqlite",
            "host": "h",
            "port": 1,
            "database": ":memory:",
            "username": "u",
            "password": "p",
        },
    )
    assert r.status_code == 201
    ds_id = r.json()["id"]

    r = client.post(
        "/reports",
        headers=auth_headers,
        json={"name": f"blocker-{ds_id}", "data_source_id": ds_id, "items": []},
    )
    assert r.status_code == 201, r.text
    report_id = r.json()["id"]

    try:
        r = client.delete(f"/data-sources/{ds_id}", headers=auth_headers)
        assert r.status_code == 409, r.text
        assert f"blocker-{ds_id}" in r.json()["detail"]
    finally:
        client.delete(f"/reports/{report_id}", headers=auth_headers)

    # Once the blocking report is gone the data source deletes cleanly.
    r = client.delete(f"/data-sources/{ds_id}", headers=auth_headers)
    assert r.status_code == 204, r.text


def test_test_connection_endpoint(client: TestClient, auth_headers: dict, temp_data_source) -> None:
    sid, _ = temp_data_source
    r = client.post(f"/data-sources/{sid}/test", headers=auth_headers)
    # :memory: sqlite test should succeed.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("success") is True

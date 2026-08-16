"""Tests for the schema-browser endpoint and introspection service."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.main import app
from app.models.data_source import DataSource
from app.services.schema_introspection import (
    SchemaIntrospectionError,
    introspect_schema,
)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers(auth_headers: dict[str, str]) -> dict[str, str]:
    return auth_headers


# ---------------------------------------------------------------------------
# Service-layer tests — SQLite (real in-memory connection)
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_source(tmp_path) -> DataSource:
    """Create a DataSource pointing at an on-disk SQLite file with a
    couple of pre-populated tables, so the introspection service has
    real metadata to walk."""
    db_file = tmp_path / "schema_test.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT
        );
        CREATE VIEW active_customers AS
            SELECT id, name FROM customers WHERE email IS NOT NULL;
        """
    )
    conn.commit()
    conn.close()

    # DataSource.password is encrypted at rest in production; here we
    # store an empty string because SQLite doesn't need auth. Other
    # required fields (host/port/username) are populated with placeholders
    # so Pydantic doesn't complain (even though the introspection code
    # path bypasses them for SQLite).
    return DataSource(
        id=9999,
        name="schema-test-sqlite",
        db_type="sqlite",
        database=str(db_file),
        host="localhost",
        port=0,
        username="",
        password="",
        schema_name=None,
        description="",
    )


def test_introspect_sqlite_returns_user_tables(sqlite_source: DataSource) -> None:
    """Walks an on-disk SQLite DB and returns both tables and views,
    but drops ``sqlite_*`` system bookkeeping."""
    tables = introspect_schema(sqlite_source)

    names = sorted(t.name for t in tables)
    # ``sqlite_sequence`` MUST be excluded.
    assert "customers" in names
    assert "active_customers" in names
    assert all(not n.startswith("sqlite_") for n in names)


def test_introspect_sqlite_returns_columns(sqlite_source: DataSource) -> None:
    """Each table has its columns listed with name, type, nullable."""
    tables = introspect_schema(sqlite_source)
    customers = next(t for t in tables if t.name == "customers")

    assert customers.schema_name == "main"
    by_name = {c.name: c for c in customers.columns}
    assert set(by_name) == {"id", "name", "email"}

    assert by_name["id"].type.upper().startswith("INTEGER")
    # ``name TEXT NOT NULL`` is the unambiguous NOT NULL case; ``id``
    # is INTEGER PRIMARY KEY which SQLite reports as notnull=0 even
    # though it's implicitly NOT NULL (a SQLite quirk, not a bug).
    assert by_name["name"].nullable is False
    assert by_name["email"].nullable is True


def test_introspect_sqlite_returns_empty_for_no_tables(tmp_path) -> None:
    """A SQLite file with no user tables returns an empty list, not an error."""
    db_file = tmp_path / "empty.db"
    sqlite3.connect(db_file).close()  # create the file but no tables

    src = DataSource(
        id=1, name="empty", db_type="sqlite", database=str(db_file),
        host="", port=0, username="", password="", schema_name=None,
    )
    assert introspect_schema(src) == []


# ---------------------------------------------------------------------------
# Service-layer tests — connection failures
# ---------------------------------------------------------------------------


def test_introspect_postgres_unreachable_returns_error(tmp_path) -> None:
    """A postgres-style source pointing at a missing host surfaces
    SchemaIntrospectionError, not a raw SQLAlchemy exception."""
    src = DataSource(
        id=1,
        name="bad-postgres",
        db_type="postgresql",
        host="127.0.0.1",
        port=1,  # closed port — connect_timeout will fire
        database="x",
        username="x",
        # encryption key in conftest makes any string round-trip-able.
        password="x",
        schema_name=None,
    )
    with pytest.raises(SchemaIntrospectionError):
        introspect_schema(src)


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


def test_schema_endpoint_requires_auth(client: TestClient) -> None:
    """Unauthenticated request returns 401."""
    # Pick any existing source id from the seeded app DB; the auth
    # check fires before the data source lookup so the id doesn't matter.
    res = client.get("/data-sources/1/schema")
    assert res.status_code == 401


def test_schema_endpoint_404_for_missing_source(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Authenticated request for a non-existent source returns 404."""
    res = client.get("/data-sources/99999/schema", headers=auth_headers)
    assert res.status_code == 404


def test_schema_endpoint_502_when_source_unreachable(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Authenticated request against an unreachable source returns 502
    (upstream failure, not our API)."""
    db: Session = SessionLocal()
    try:
        # Insert a Postgres source that points nowhere — same host/port
        # used by the unreachable-service test. The DataSource model has
        # a unique constraint on ``name`` so use a uuid-suffixed name to
        # survive across test runs that share the dev app.db.
        import uuid

        bad = DataSource(
            name=f"bad-test-source-{uuid.uuid4().hex[:8]}",
            db_type="postgresql",
            host="127.0.0.1",
            port=1,
            database="x",
            username="x",
            password="x",
            schema_name=None,
        )
        db.add(bad)
        db.commit()
        db.refresh(bad)
        bad_id = bad.id
    finally:
        db.close()

    res = client.get(f"/data-sources/{bad_id}/schema", headers=auth_headers)
    assert res.status_code == 502
    assert "Failed to introspect schema" in res.json()["detail"]


def test_schema_endpoint_happy_path_sqlite(
    client: TestClient, auth_headers: dict[str, str], tmp_path
) -> None:
    """End-to-end: create a SQLite source, hit the endpoint, get tables."""
    db_file = tmp_path / "happy.db"
    sqlite3.connect(db_file).close()
    db: Session = SessionLocal()
    try:
        import uuid

        src = DataSource(
            name=f"happy-sqlite-source-{uuid.uuid4().hex[:8]}",
            db_type="sqlite",
            database=str(db_file),
            host="",
            port=0,
            username="",
            password="",
            schema_name=None,
        )
        db.add(src)
        db.commit()
        db.refresh(src)
        src_id = src.id
    finally:
        db.close()

    res = client.get(f"/data-sources/{src_id}/schema", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body == {"tables": []}  # empty DB → empty tree

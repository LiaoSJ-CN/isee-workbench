"""/explorer/query endpoint coverage.

Exercises the SELECT-only safety check, the data-source lookup, and
the happy/sad path of a real query against the seeded sqlite source.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.data_source import DataSource
from app.services.report_generator import _get_or_create_engine
from app.services.sql_validator import UnsafeSQLError, validate_select_only


@pytest.fixture
def seeded_sqlite_source() -> DataSource:
    db: Session = SessionLocal()
    try:
        src = db.query(DataSource).filter(DataSource.db_type == "sqlite").first()
        if not src:
            pytest.skip("no sqlite data source; create one in the UI first")
        return src
    finally:
        db.close()


def test_explorer_requires_auth(client: TestClient) -> None:
    r = client.post("/explorer/query", json={"data_source_id": 1, "sql": "SELECT 1"})
    assert r.status_code == 401


def test_explorer_rejects_non_select(
    client: TestClient, auth_headers: dict, seeded_sqlite_source: DataSource
) -> None:
    for bad in [
        "DROP TABLE x",
        "DELETE FROM x",
        "INSERT INTO x VALUES (1)",
        "UPDATE x SET a=1",
        "CREATE TABLE x (a int)",
        "ALTER TABLE x ADD COLUMN b int",
        "TRUNCATE x",
    ]:
        r = client.post(
            "/explorer/query",
            headers=auth_headers,
            json={"data_source_id": seeded_sqlite_source.id, "sql": bad},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is False, f"non-SELECT {bad!r} must be rejected"
        assert "Only SELECT" in (body.get("error") or "")


def test_explorer_rejects_unknown_data_source(
    client: TestClient, auth_headers: dict
) -> None:
    r = client.post(
        "/explorer/query",
        headers=auth_headers,
        json={"data_source_id": 9999999, "sql": "SELECT 1"},
    )
    assert r.status_code == 404


def test_explorer_runs_select_against_seeded_sqlite(
    client: TestClient, auth_headers: dict, seeded_sqlite_source: DataSource
) -> None:
    r = client.post(
        "/explorer/query",
        headers=auth_headers,
        json={
            "data_source_id": seeded_sqlite_source.id,
            "sql": "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 5",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert "name" in body["columns"]
    assert 0 < body["row_count"] <= 5


def test_explorer_sql_error_returns_failure_not_500(
    client: TestClient, auth_headers: dict, seeded_sqlite_source: DataSource
) -> None:
    r = client.post(
        "/explorer/query",
        headers=auth_headers,
        json={
            "data_source_id": seeded_sqlite_source.id,
            "sql": "SELECT * FROM table_that_does_not_exist",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["row_count"] == 0
    assert body["error"]  # populated with the SQL error message


@pytest.mark.parametrize(
    "sql",
    [
        # Stacked statements — `;` enables running multiple statements; the
        # explorer must be strictly SELECT-only, no second clause allowed.
        "SELECT 1; SELECT 2",
        "SELECT 1;",
        "SELECT 1; -- anything",
        "SELECT 1;--DROP TABLE x",
    ],
)
def test_validate_select_only_rejects_any_semicolon(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        # Comments are no-ops to the database; what matters is the SQL
        # the engine will actually execute. `SELECT /*note*/ 1` and
        # `SELECT 1 -- note` are semantically identical to `SELECT 1`.
        "SELECT 1",
        "SELECT 1 -- trailing line comment",
        "SELECT /*inline*/ 1",
        "SELECT 1 /* trailing block */",
    ],
)
def test_validate_select_only_allows_benign_comments(sql: str) -> None:
    validate_select_only(sql)  # must not raise


def test_explorer_populates_engine_cache(
    client: TestClient, auth_headers: dict, seeded_sqlite_source: DataSource
) -> None:
    """Regression: explorer previously built a fresh engine on every query
    and immediately disposed it, wasting TCP/auth on remote backends and
    losing `pool_pre_ping=True`. It should now share
    `_engine_cache` with `report_generator` so subsequent queries reuse
    the engine and pick up the pre-ping protection.
    """
    # Force a miss so the test is order-independent: evict any cached engine
    # for this data source first, then assert that a query repopulates it.
    from app.services.report_generator import _engine_cache, evict_engine

    evict_engine(seeded_sqlite_source.id)
    assert seeded_sqlite_source.id not in _engine_cache

    r = client.post(
        "/explorer/query",
        headers=auth_headers,
        json={
            "data_source_id": seeded_sqlite_source.id,
            "sql": "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 1",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True

    assert seeded_sqlite_source.id in _engine_cache


def test_explorer_row_cap_applies_to_unbounded_select(
    client: TestClient, auth_headers: dict, seeded_sqlite_source: DataSource, monkeypatch
) -> None:
    """Regression: a user-supplied SELECT with no LIMIT clause must still
    be capped at ``settings.explorer_max_rows``. The endpoint wraps the
    user SQL in ``SELECT * FROM (…) AS _explorer_sub LIMIT N`` so the
    cap fires regardless of what the user wrote. A query that orders by
    a stable column must also preserve ORDER BY through the wrap.
    """
    monkeypatch.setattr(settings, "explorer_max_rows", 5)

    engine = _get_or_create_engine(seeded_sqlite_source)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS _test_explorer_cap"))
        conn.execute(
            text(
                "CREATE TABLE _test_explorer_cap (id INTEGER PRIMARY KEY, label TEXT)"
            )
        )
        # 20 rows; cap=5 means the response should hold rows 1..5 in id order.
        for i in range(1, 21):
            conn.execute(
                text("INSERT INTO _test_explorer_cap VALUES (:i, :l)"),
                {"i": i, "l": f"row-{i}"},
            )

    try:
        r = client.post(
            "/explorer/query",
            headers=auth_headers,
            json={
                "data_source_id": seeded_sqlite_source.id,
                "sql": "SELECT id, label FROM _test_explorer_cap ORDER BY id",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert body["row_count"] == 5
        # ORDER BY must survive the subquery wrap — first 5 ids, in order.
        assert [row["id"] for row in body["rows"]] == [1, 2, 3, 4, 5]
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS _test_explorer_cap"))

"""Tests for ``scripts/bootstrap_demo.py``.

The bootstrap flow is destructive: ``ensure_warehouse`` always rebuilds
``erp_demo.db`` from scratch, and ``ensure_data_source_row`` plus
``ensure_demo_reports`` write to the metadata DB. Every test runs in a
fully isolated tmp_path SQLite so dev state stays untouched.

The fixture swaps ``app.database.engine`` / ``SessionLocal`` (used by
``ensure_data_source_row``) and rebinds ``scripts.bootstrap_demo``'s
``META_DB`` / ``DB_PATH`` (which are bound at import time, so source-level
patches wouldn't propagate).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def bootstrap_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Build a fresh metadata + warehouse world for ``bootstrap_demo.run``.

    Returns ``{"engine", "SessionLocal", "meta_db", "warehouse_path"}``.

    Schema is built via ``alembic upgrade head`` against the temp
    ``metadata_url`` so the on-disk tables match the migration chain
    exactly (no ``Base.metadata.create_all`` drift).
    """
    from alembic.config import Config as AlembicConfig
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from alembic import command
    from app.config import settings

    # --- temp metadata DB ---
    meta_db = tmp_path / "meta.db"
    metadata_url = f"sqlite:///{meta_db}"
    # alembic's env.py sets ``config.set_main_option("sqlalchemy.url",
    # settings.database_url)`` at module load — must patch BEFORE running
    # ``command.upgrade`` or the migration targets dev ``app.db``.
    monkeypatch.setattr(settings, "database_url", metadata_url)
    test_engine = create_engine(metadata_url, connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Swap app.database so any SessionLocal() inside the SUT talks to tmp DB.
    monkeypatch.setattr("app.database.engine", test_engine)
    monkeypatch.setattr("app.database.SessionLocal", TestSessionLocal)

    # alembic upgrade head — env.py picks up ``settings.database_url``
    # which we patched above, so the migration targets the temp DB.
    cfg = AlembicConfig("alembic.ini")
    command.upgrade(cfg, "head")

    # --- temp warehouse path (don't pre-create) ---
    warehouse_path = tmp_path / "erp_demo.db"

    # --- rebind bootstrap_demo's module-level names ---
    # bootstrap_demo imports DB_PATH / META_DB / SessionLocal at module
    # load time. Source-level patches (e.g. seed_reports.META_DB) won't
    # propagate — patch bootstrap_demo's own attributes.
    monkeypatch.setattr("scripts.bootstrap_demo.DB_PATH", warehouse_path)
    monkeypatch.setattr("scripts.bootstrap_demo.META_DB", str(meta_db))
    monkeypatch.setattr("scripts.bootstrap_demo.SessionLocal", TestSessionLocal)
    # seed_reports.seed() reads its own META_DB at call time.
    monkeypatch.setattr("scripts.seed_reports.META_DB", str(meta_db))

    yield {
        "engine": test_engine,
        "SessionLocal": TestSessionLocal,
        "meta_db": meta_db,
        "warehouse_path": warehouse_path,
    }

    test_engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_creates_warehouse_when_missing(bootstrap_isolated: dict[str, Any]) -> None:
    """``ensure_warehouse`` unlinks any existing file and rebuilds from scratch.

    After ``run()`` the warehouse file exists and contains the seeded
    ``dim_supplier`` table (first DDL statement in ``scripts/seed_erp_demo``).
    """
    from scripts import bootstrap_demo

    assert not bootstrap_isolated["warehouse_path"].exists()
    status = bootstrap_demo.run()

    wp = bootstrap_isolated["warehouse_path"]
    assert wp.exists()
    assert status["warehouse"]["path"] == str(wp)
    assert status["warehouse"]["rebuilt"] is True

    # Sanity-check the warehouse actually has data (dim_supplier is the first
    # table in seed_erp_demo.DDL).
    conn = sqlite3.connect(wp)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM dim_supplier").fetchone()[0]
    finally:
        conn.close()
    assert rows > 0, "warehouse rebuild produced empty dim_supplier"


def test_run_creates_data_source_row(bootstrap_isolated: dict[str, Any]) -> None:
    """``ensure_data_source_row`` inserts exactly one row pointing at the warehouse.

    The ``database`` column must be the absolute warehouse path
    (``build_connection_url`` requires absolute for sqlite).
    """
    from sqlalchemy import select

    from app.models.data_source import DataSource
    from scripts import bootstrap_demo

    SessionLocal = bootstrap_isolated["SessionLocal"]

    # Pre-condition: empty data_sources.
    db = SessionLocal()
    try:
        assert db.execute(select(DataSource)).scalars().all() == []
    finally:
        db.close()

    status = bootstrap_demo.run()

    db = SessionLocal()
    try:
        rows = db.execute(select(DataSource)).scalars().all()
    finally:
        db.close()

    assert len(rows) == 1
    ds = rows[0]
    assert ds.name == "sqlite_demo"  # seed_reports.PREFERRED_NAME
    assert ds.db_type == "sqlite"
    assert Path(ds.database).is_absolute()
    assert Path(ds.database).resolve() == bootstrap_isolated["warehouse_path"].resolve()
    assert status["data_source"]["name"] == "sqlite_demo"
    assert status["data_source"]["id"] == ds.id
    assert status["data_source"]["existed"] is False


def test_run_inserts_all_demo_reports(bootstrap_isolated: dict[str, Any]) -> None:
    """All three demo reports land with ``is_demo=1`` and ``is_template=1``.

    Reads back via sqlite3 (not ORM) so we hit the raw INSERT path
    inside ``seed_reports.seed`` and confirm no ORM-level filtering is
    hiding rows.
    """
    from scripts import bootstrap_demo

    bootstrap_demo.run()

    conn = sqlite3.connect(bootstrap_isolated["meta_db"])
    try:
        rows = conn.execute(
            "SELECT name, is_demo, is_template FROM reports WHERE is_demo = 1 ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 3
    names = {r[0] for r in rows}
    assert names == {"财务经营月报", "应收账款分析", "供应商付款与应付分析"}
    for _name, is_demo, is_template in rows:
        assert is_demo == 1
        assert is_template == 1


def test_run_is_idempotent(bootstrap_isolated: dict[str, Any]) -> None:
    """A second ``run()`` does not duplicate rows and reports ``existed=True``.

    - ``data_source.existed`` flips to True
    - ``reports`` count stays at 3 (seed_reports.keep_existing only
      deletes is_demo=1 rows and re-inserts them)
    - ``warehouse.path`` stays the same
    """
    from sqlalchemy import func, select

    from app.models.data_source import DataSource
    from scripts import bootstrap_demo

    SessionLocal = bootstrap_isolated["SessionLocal"]

    bootstrap_demo.run()  # first run

    db = SessionLocal()
    try:
        first_ds_count = db.execute(select(func.count(DataSource.id))).scalar_one()
    finally:
        db.close()
    assert first_ds_count == 1

    # Count demo reports via raw SQL (more accurate — seed_reports writes
    # directly via sqlite3).
    conn = sqlite3.connect(bootstrap_isolated["meta_db"])
    try:
        first_reports_count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE is_demo = 1"
        ).fetchone()[0]
    finally:
        conn.close()
    assert first_reports_count == 3

    status2 = bootstrap_demo.run()  # second run

    assert status2["data_source"]["existed"] is True
    assert status2["warehouse"]["rebuilt"] is True  # always rebuilt
    assert status2["warehouse"]["path"] == str(bootstrap_isolated["warehouse_path"])

    db = SessionLocal()
    try:
        second_ds_count = db.execute(select(func.count(DataSource.id))).scalar_one()
    finally:
        db.close()

    conn = sqlite3.connect(bootstrap_isolated["meta_db"])
    try:
        second_reports_count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE is_demo = 1"
        ).fetchone()[0]
        report_names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM reports WHERE is_demo = 1 ORDER BY name"
            ).fetchall()
        }
    finally:
        conn.close()

    assert second_ds_count == 1, "second run duplicated the DataSource row"
    assert second_reports_count == 3, "second run duplicated demo reports"
    assert report_names == {"财务经营月报", "应收账款分析", "供应商付款与应付分析"}


def test_ensure_data_source_row_preserves_user_edits(
    bootstrap_isolated: dict[str, Any],
) -> None:
    """Pre-existing DataSource row with the same name is NOT overwritten.

    A user may have edited ``description`` / ``database`` on the demo row;
    re-running bootstrap must respect that. The contract is:
    ``ensure_data_source_row`` returns ``(existing_row, existed=True)``
    when the by-name lookup hits, so we never DELETE + INSERT.
    """
    from sqlalchemy import select

    from app.models.data_source import DataSource
    from scripts import bootstrap_demo

    SessionLocal = bootstrap_isolated["SessionLocal"]

    # Pre-insert a row claiming the demo name with a custom description.
    db = SessionLocal()
    try:
        sentinel = DataSource(
            name="sqlite_demo",
            db_type="sqlite",
            host="h",
            port=1,
            database="/tmp/some-other-warehouse.db",
            username="u",
            password="p",
            description="USER-EDITED description — must survive bootstrap",
        )
        db.add(sentinel)
        db.commit()
        db.refresh(sentinel)
        sentinel_id = sentinel.id
    finally:
        db.close()

    _ds, existed = bootstrap_demo.ensure_data_source_row(
        bootstrap_isolated["warehouse_path"]
    )
    assert existed is True

    db = SessionLocal()
    try:
        row = db.execute(
            select(DataSource).where(DataSource.name == "sqlite_demo")
        ).scalar_one()
    finally:
        db.close()

    # id unchanged (no DELETE + INSERT)
    assert row.id == sentinel_id
    # description preserved
    assert row.description == "USER-EDITED description — must survive bootstrap"
    # database NOT redirected to the rebuilt warehouse
    assert row.database == "/tmp/some-other-warehouse.db"

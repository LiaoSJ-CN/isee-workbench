"""Tests for the ``_seed_demo_data`` lifespan hook in ``app/main.py``.

Validates the double-gating contract:

* ``settings.seed_demo_on_startup = False`` (default) → hook never runs,
  even on an empty ``data_sources`` table.
* ``settings.seed_demo_on_startup = True`` + non-empty
  ``data_sources`` → hook short-circuits via the empty-table guard
  (a pre-existing DS row is left untouched).
* ``settings.seed_demo_on_startup = True`` + empty
  ``data_sources`` → hook runs ``bootstrap_demo.run()``; warehouse is
  rebuilt and the demo DS + 3 reports are inserted.

Each test calls ``_isolate_lifespan_world`` to redirect
``app.database`` + ``scripts.bootstrap_demo`` at a tmp SQLite file, so
the dev ``app.db`` and ``backend/data/erp_demo.db`` stay untouched.
The TestClient is entered INSIDE each test so the per-test
``seed_demo_on_startup`` value is set BEFORE lifespan fires.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _isolate_lifespan_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> dict[str, Any]:
    """Redirect all of bootstrap_demo + app.database to a fresh tmp SQLite.

    Returns ``{"engine", "SessionLocal", "meta_db", "warehouse_path"}``.
    Must be called BEFORE ``TestClient(app)`` enters lifespan so alembic
    upgrade + the seed hooks target the temp DB.

    The test must NOT additionally use the ``client`` fixture from
    conftest — that fixture enters lifespan against the dev app.db.
    """
    from alembic.config import Config as AlembicConfig
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from alembic import command
    from app.config import settings

    meta_db = tmp_path / "lifespan_meta.db"
    metadata_url = f"sqlite:///{meta_db}"
    # alembic's env.py reads ``settings.database_url`` at module load —
    # patch BEFORE alembic runs so it targets the temp DB.
    monkeypatch.setattr(settings, "database_url", metadata_url)
    test_engine = create_engine(metadata_url, connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Real lifespan uses ``app.database.SessionLocal`` for the empty-table
    # guard; bootstrap_demo uses ``scripts.bootstrap_demo.SessionLocal``.
    # Both must point at the temp engine so the hook + bootstrap share
    # the same DB.
    monkeypatch.setattr("app.database.engine", test_engine)
    monkeypatch.setattr("app.database.SessionLocal", TestSessionLocal)

    # ``app.main.SessionLocal`` and ``scripts.bootstrap_demo.SessionLocal``
    # were BOUND at module-import time (``from app.database import
    # SessionLocal``); patching ``app.database.SessionLocal`` afterwards
    # doesn't rebind those names. Patch both call sites explicitly so
    # the lifespan hooks write to the temp DB, not the dev app.db.
    monkeypatch.setattr("app.main.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("scripts.bootstrap_demo.SessionLocal", TestSessionLocal)

    # alembic upgrade head against the temp URL.
    cfg = AlembicConfig("alembic.ini")
    command.upgrade(cfg, "head")

    # Redirect bootstrap_demo paths so it doesn't touch dev artifacts.
    warehouse_path = tmp_path / "erp_demo.db"
    monkeypatch.setattr("scripts.bootstrap_demo.DB_PATH", warehouse_path)
    monkeypatch.setattr("scripts.bootstrap_demo.META_DB", str(meta_db))
    monkeypatch.setattr("scripts.bootstrap_demo.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("scripts.seed_reports.META_DB", str(meta_db))

    return {
        "engine": test_engine,
        "SessionLocal": TestSessionLocal,
        "meta_db": meta_db,
        "warehouse_path": warehouse_path,
    }


def _shutdown_engine(world: dict[str, Any]) -> None:
    world["engine"].dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_lifespan_skips_when_env_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SEED_DEMO_ON_STARTUP=false`` (the default) → hook is a no-op.

    ``data_sources`` stays empty, no warehouse file is created, no demo
    reports are inserted.
    """
    from sqlalchemy import select

    from app.config import settings
    from app.main import app
    from app.models.data_source import DataSource

    monkeypatch.setattr(settings, "seed_demo_on_startup", False)
    world = _isolate_lifespan_world(monkeypatch, tmp_path)
    try:
        with TestClient(app):
            pass  # lifespan fires here

        SessionLocal = world["SessionLocal"]
        db = SessionLocal()
        try:
            assert db.execute(select(DataSource)).scalars().all() == []
        finally:
            db.close()

        assert not world["warehouse_path"].exists()
        conn = sqlite3.connect(world["meta_db"])
        try:
            report_count = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        finally:
            conn.close()
        assert report_count == 0
    finally:
        _shutdown_engine(world)


def test_lifespan_skips_when_table_non_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SEED_DEMO_ON_STARTUP=true`` + non-empty ``data_sources`` → skip.

    The empty-table guard protects operators who accidentally set the
    flag in production with pre-existing data — the existing row is
    not overwritten, no warehouse is rebuilt, no reports are inserted.
    """
    from sqlalchemy import select

    from app.config import settings
    from app.main import app
    from app.models.data_source import DataSource

    monkeypatch.setattr(settings, "seed_demo_on_startup", True)
    world = _isolate_lifespan_world(monkeypatch, tmp_path)
    SessionLocal = world["SessionLocal"]

    # Pre-insert a sentinel DS row that MUST survive the lifespan.
    sentinel_db_path = "/tmp/sentinel-warehouse.db"
    db = SessionLocal()
    try:
        sentinel = DataSource(
            name="user-existing-source",
            db_type="sqlite",
            host="h",
            port=1,
            database=sentinel_db_path,
            username="u",
            password="p",
            description="USER-OWNED DataSource — must NOT be touched by _seed_demo_data",
        )
        db.add(sentinel)
        db.commit()
        db.refresh(sentinel)
        sentinel_id = sentinel.id
    finally:
        db.close()

    try:
        with TestClient(app):
            pass  # lifespan fires; hook observes non-empty guard and skips

        db = SessionLocal()
        try:
            rows = db.execute(select(DataSource)).scalars().all()
        finally:
            db.close()

        assert len(rows) == 1
        assert rows[0].id == sentinel_id
        assert rows[0].name == "user-existing-source"
        assert rows[0].database == sentinel_db_path

        # No warehouse rebuilt, no demo reports inserted.
        assert not world["warehouse_path"].exists()
        conn = sqlite3.connect(world["meta_db"])
        try:
            report_count = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        finally:
            conn.close()
        assert report_count == 0
    finally:
        _shutdown_engine(world)


def test_lifespan_seeds_when_empty_and_env_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SEED_DEMO_ON_STARTUP=true`` + empty ``data_sources`` → bootstrap runs.

    The hook delegates to ``scripts.bootstrap_demo.run()``. End state:
    warehouse exists, one ``DataSource`` named ``sqlite_demo``, three
    demo reports with ``is_demo=1``.
    """
    from sqlalchemy import select

    from app.config import settings
    from app.main import app
    from app.models.data_source import DataSource

    monkeypatch.setattr(settings, "seed_demo_on_startup", True)
    world = _isolate_lifespan_world(monkeypatch, tmp_path)
    try:
        with TestClient(app):
            pass  # lifespan fires; hook observes empty guard and bootstraps

        SessionLocal = world["SessionLocal"]

        db = SessionLocal()
        try:
            rows = db.execute(select(DataSource)).scalars().all()
        finally:
            db.close()

        assert len(rows) == 1
        ds = rows[0]
        assert ds.name == "sqlite_demo"
        assert Path(ds.database).resolve() == world["warehouse_path"].resolve()
        assert world["warehouse_path"].exists()

        conn = sqlite3.connect(world["meta_db"])
        try:
            report_rows = conn.execute(
                "SELECT name FROM reports WHERE is_demo = 1 ORDER BY name"
            ).fetchall()
        finally:
            conn.close()

        assert {r[0] for r in report_rows} == {
            "财务经营月报",
            "应收账款分析",
            "供应商付款与应付分析",
        }
    finally:
        _shutdown_engine(world)

"""One-shot recovery of the bundled demo data set.

Closes a dev-only gap where ``scripts/seed_erp_demo.py`` builds the
business warehouse (``backend/data/erp_demo.db``) but **never** writes
the corresponding ``data_sources`` metadata row. After a fresh
``backend/app.db``, ``scripts/seed_reports.py`` would ``sys.exit(2)``
because ``PREFERRED_NAME='sqlite_demo'`` resolves to zero rows.

This script orchestrates the three steps a dev operator used to do by
hand:

1. rebuild ``backend/data/erp_demo.db`` from scratch
2. insert / verify the ``sqlite_demo`` DataSource metadata row,
   pointing at the warehouse's absolute path
3. re-insert the three demo reports (idempotent — non-demo reports
   are preserved)

Idempotency:

- ``ensure_warehouse`` always rebuilds ``erp_demo.db`` (the warehouse
  is meant to be reproducible; that's what the operator gets).
- ``ensure_data_source_row`` returns ``existed=True`` when a row with
  ``PREFERRED_NAME`` is already present and skips the insert. This
  preserves any operator edits to ``description`` / ``database`` etc.
- ``ensure_demo_reports`` only deletes ``is_demo=1`` rows before
  re-inserting the three seed rows, via the patched
  ``scripts/seed_reports.seed(keep_existing=True)`` path.

CLI:

    python scripts/bootstrap_demo.py

prints a JSON status dict and exits 0. Safe to run repeatedly.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPTS_DIR.parent
# Make sure ``from app import ...`` works when this script is run as
# ``python scripts/bootstrap_demo.py`` from anywhere (repo root, etc.).
# Must run BEFORE the ``from app.*`` imports below.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
# Importing ``DataSource`` triggers mapper configuration; SQLAlchemy
# then walks ``DataSource.grants`` (relationship to ``DataSourceAccess``).
# Both sides of the relationship must be registered before the mapper
# fully initialises — importing only ``DataSource`` raises
# ``InvalidRequestError: When initializing mapper ... expression
# 'DataSourceAccess' failed to locate a name``.
from app.models.data_source import DataSource  # noqa: E402
from app.models.data_source_access import DataSourceAccess  # noqa: E402,F401
from app.models.user import User  # noqa: E402

from scripts.seed_dashboards import seed as seed_dashboards_seed  # noqa: E402
from scripts.seed_erp_demo import DB_PATH, DDL, seed as seed_erp_seed  # noqa: E402
from scripts.seed_reports import (  # noqa: E402
    META_DB,
    PREFERRED_NAME,
    REPORTS,
    seed as seed_reports_seed,
)

logger = logging.getLogger(__name__)

# Kept in sync with seed_reports.PREFERRED_NAME — we reuse the constant
# so any rename propagates automatically.
DEMO_DS_NAME = PREFERRED_NAME
DEMO_DS_DESCRIPTION = "Pre-seeded ERP demo data warehouse (auto-recovered)"
DEMO_DS_DB_TYPE = "sqlite"

EXPECTED_DEMO_REPORT_NAMES: frozenset[str] = frozenset(r["name"] for r in REPORTS)


def ensure_warehouse() -> Path:
    """Drop & rebuild the ERP warehouse. Returns its absolute path.

    Mirrors ``seed_erp_demo.main()`` without going through argparse, so
    this script can be invoked from a lifespan hook or a pytest fixture
    without sys.argv side-effects.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        for ddl in DDL:
            conn.execute(ddl)
        seed_erp_seed(conn)
        conn.commit()
    finally:
        conn.close()
    logger.info("bootstrap_demo: rebuilt warehouse at %s", DB_PATH.resolve())
    return DB_PATH.resolve()


def ensure_data_source_row(warehouse_path: Path) -> tuple[DataSource, bool]:
    """Insert the demo DataSource metadata row if absent.

    Idempotent: query by ``PREFERRED_NAME`` first; if present, return
    the existing row (and ``existed=True``) so we never overwrite an
    operator edit. Otherwise insert a fresh row pointing at the
    warehouse's absolute path (required by
    ``app.services.connection.build_connection_url``).

    Returns ``(DataSource, existed)``.
    """
    db = SessionLocal()
    try:
        existing = db.execute(
            select(DataSource).where(DataSource.name == DEMO_DS_NAME)
        ).scalar_one_or_none()
        if existing is not None:
            logger.info(
                "bootstrap_demo: DataSource %r already present (id=%s), skipping insert",
                DEMO_DS_NAME,
                existing.id,
            )
            return existing, True

        # Reuse the same admin-resolution order as seed_reports: first
        # admin wins. The bootstrap hook runs after _seed_admin_user so
        # an admin is almost always present; we just fall back to NULL
        # defensively.
        admin = db.execute(
            select(User).where(User.role == "admin").order_by(User.id).limit(1)
        ).scalar_one_or_none()

        ds = DataSource(
            name=DEMO_DS_NAME,
            db_type=DEMO_DS_DB_TYPE,
            database=str(warehouse_path),  # absolute path required
            description=DEMO_DS_DESCRIPTION,
            owner_user_id=admin.id if admin is not None else None,
            org_id=settings.default_org_id,
        )
        db.add(ds)
        db.commit()
        db.refresh(ds)
        logger.info(
            "bootstrap_demo: created DataSource %r (id=%s, database=%s)",
            DEMO_DS_NAME,
            ds.id,
            warehouse_path,
        )
        return ds, False
    finally:
        db.close()


def ensure_demo_reports(ds_id: int) -> dict[str, bool]:
    """Re-insert the three demo report rows.

    The patched ``seed_reports.seed(keep_existing=True)`` (see
    ``backend/scripts/seed_reports.py``) only deletes ``is_demo=1`` rows
    before re-inserting, so any non-demo operator reports survive.

    Returns ``{report_name: present_after}``.
    """
    seed_reports_seed(keep_existing=True, data_source_id=ds_id)

    conn = sqlite3.connect(META_DB)
    try:
        present: dict[str, bool] = {}
        for name in EXPECTED_DEMO_REPORT_NAMES:
            row = conn.execute(
                "SELECT 1 FROM reports WHERE name = ? AND is_demo = 1", (name,)
            ).fetchone()
            present[name] = row is not None
    finally:
        conn.close()
    return present


def run() -> dict[str, Any]:
    """Top-level orchestrator. Idempotent. Safe to call repeatedly.

    Returns a JSON-serialisable status dict suitable for logging or
    test assertions:

    .. code-block:: python

        {
            "warehouse":  {"path": "<abs>", "rebuilt": True},
            "data_source": {"name": "sqlite_demo", "id": 1, "existed": False},
            "reports":    {"财务经营月报": True, ...},
        }
    """
    logger.info("bootstrap_demo: starting")
    warehouse_path = ensure_warehouse()
    ds, ds_existed = ensure_data_source_row(warehouse_path)
    # ``ensure_data_source_row`` guarantees a persisted row (either
    # freshly inserted or fetched), so ``id`` is non-null on return;
    # the assertion narrows the type for mypy without changing runtime
    # behaviour.
    assert ds.id is not None, "ensure_data_source_row returned a row without id"
    reports = ensure_demo_reports(int(ds.id))
    # Demo dashboard (批 14.6) — depends on the demo reports above for
    # the report-ref lookup, hence placed last. ``keep_existing=True``
    # preserves operator-authored dashboards with non-matching names.
    dashboards = seed_dashboards_seed(keep_existing=True)
    status: dict[str, Any] = {
        "warehouse": {"path": str(warehouse_path), "rebuilt": True},
        "data_source": {"name": ds.name, "id": ds.id, "existed": ds_existed},
        "reports": reports,
        "dashboards": dashboards,
    }
    logger.info("bootstrap_demo: done — %s", json.dumps(status, ensure_ascii=False))
    return status


def main() -> None:
    """CLI entry — prints JSON status to stdout."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(json.dumps(run(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
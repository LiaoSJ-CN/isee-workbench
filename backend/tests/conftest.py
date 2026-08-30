"""Shared pytest fixtures.

Tests rely on:
  - A running FastAPI app under ``app.main:app``.
  - An isolated, per-invocation tmpfile SQLite for the metadata DB —
    see the "Isolated test DB" block below. No test can ever touch
    ``backend/app.db``.
  - Demo data (one DataSource, three Reports, one Dashboard) seeded by
    ``app.main.lifespan._seed_demo_data`` on the first TestClient start.
    Subsequent TestClient contexts short-circuit (data_sources non-empty).
  - ``JWT_SECRET_KEY`` set in the environment before the app modules are
    imported, so the access tokens we mint here use a stable key.

Tests that need a fresh sqlite (engine cache, data source CRUD for
non-mutating checks) build their own engine against ``tmp_sqlite_path`` —
they don't share the session-level tmpfile and don't pollute any other
DB.
"""

import atexit
import os
import sys
import tempfile
from pathlib import Path

# Ensure backend root is on sys.path so `from app...` works regardless
# of where pytest is invoked from.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# ---------------------------------------------------------------------------
# Isolated test DB — session-scoped, never touches ``backend/app.db``
# ---------------------------------------------------------------------------
#
# 2026-08-30 root-cause fix: tests used to run against the live dev
# metadata DB, with conftest's ``_cleanup_leaked_data_source_rows``
# autouse fixture as a band-aid to prune whatever test rows slipped
# through. The band-aid was whack-a-mole (every new test naming
# convention needed a LIKE-clause extension) and any SIGKILL between
# test and cleanup leaked.
#
# Instead, every ``pytest`` invocation now binds a fresh tmpfile SQLite
# to ``app.database.engine`` + ``SessionLocal`` + the seed scripts'
# ``META_DB`` constant. The tmpfile is auto-deleted at process exit.
# Even a SIGKILL in the middle of a test cannot touch ``backend/app.db``.
#
# Three env vars are set BEFORE any ``from app...`` import below —
# they're read at module instantiation:
#   1. ``DATABASE_URL`` — Pydantic ``Settings.database_url`` (env-var
#      aware). ``app.database.engine`` binds to this at module load.
#   2. ``SEED_DEMO_ON_STARTUP=true`` — opt the lifespan into running
#      ``_seed_demo_data`` on first TestClient start, so tests that
#      depend on the demo warehouse / reports see them.
#   3. ``JWT_SECRET_KEY`` / ``ENCRYPTION_KEY`` (below) — stability for
#      access tokens minted during the test session.

_tmpdir = tempfile.TemporaryDirectory(prefix="isee_test_db_")
atexit.register(_tmpdir.cleanup)
_TEST_DB_PATH = Path(_tmpdir.name) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ["SEED_DEMO_ON_STARTUP"] = "true"

# Stable JWT secret so tokens minted in tests verify on subsequent
# requests. Must be set BEFORE app modules import settings.
os.environ.setdefault("JWT_SECRET_KEY", "pytest-secret-do-not-use-in-prod")
# Stable encryption key for data-source password encryption at rest.
os.environ.setdefault("ENCRYPTION_KEY", "2wjRI6T24tbe64kcfOGqMlTCUrg5gzk82QE8BTYbpNc=")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# The seed scripts use raw ``sqlite3.connect(META_DB)`` — they bypass the
# SQLAlchemy engine. Redirect their bound names to our tmpfile so demo
# reports / dashboards land alongside the metadata we manage via ORM.
# Done here, AFTER the imports, so the patches take effect on subsequent
# ``bootstrap_demo.run()`` calls from ``lifespan._seed_demo_data``.
import scripts.bootstrap_demo  # noqa: E402
import scripts.seed_dashboards  # noqa: E402
import scripts.seed_erp_demo  # noqa: E402
import scripts.seed_reports  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services.jwt_auth import create_access_token  # noqa: E402
from app.services.report_generator import _engine_cache  # noqa: E402

scripts.bootstrap_demo.META_DB = _TEST_DB_PATH
scripts.seed_dashboards.META_DB = _TEST_DB_PATH
scripts.seed_reports.META_DB = _TEST_DB_PATH


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    """Materialise the tmpfile DB once per pytest session.

    Runs ``alembic upgrade head`` + seeds the admin user + invokes
    the seed helpers (NOT ``bootstrap_demo.run()``) to populate the
    demo DataSource / reports / dashboard rows. After this, every
    test — including those that touch ``SessionLocal`` directly without
    going through the ``client`` fixture — sees a fully-seeded metadata
    DB.

    Tests that use the ``client`` fixture will re-enter the lifespan,
    which short-circuits (alembic detects head, ``_seed_admin_user``
    no-ops because the admin exists, ``_seed_demo_data`` skips because
    ``data_sources`` is non-empty). Cheap per-test cost.

    Why we bypass ``bootstrap_demo.run()``: that function calls
    ``ensure_warehouse()`` which deletes the dev
    ``backend/data/erp_demo.db`` and rebuilds it from scratch. We want
    to *use* the existing dev warehouse, not clobber it. Tests that
    need to exercise the rebuild path (e.g. ``test_bootstrap_demo``)
    set up their own isolation and call ``bootstrap_demo.run()``
    directly — we must not pre-patch ``ensure_warehouse`` from conftest
    or those tests would break.
    """
    from alembic.config import Config as AlembicConfig

    from alembic import command as alembic_command
    from app.main import _seed_admin_user

    cfg = AlembicConfig(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    alembic_command.upgrade(cfg, "head")

    # Admin must exist before the seed helpers run so the demo
    # DataSource gets a real ``owner_user_id``.
    _seed_admin_user()

    warehouse_path = scripts.seed_erp_demo.DB_PATH.resolve()
    if not warehouse_path.exists():
        raise RuntimeError(
            f"Dev warehouse {warehouse_path} missing. "
            "Run scripts/seed_erp_demo.py to create it."
        )

    ds, _ = scripts.bootstrap_demo.ensure_data_source_row(warehouse_path)
    scripts.seed_reports.seed(keep_existing=True, data_source_id=int(ds.id))
    scripts.seed_dashboards.seed(keep_existing=True)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Authorization header with a freshly-minted access token for admin."""
    token = create_access_token(settings.admin_username)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client() -> TestClient:
    """A FastAPI TestClient bound to the real ``app`` instance.

    Use ``client`` (not a function-scoped fresh app) so router-level
    state and the APScheduler singleton behave like production.

    The session-tmpfile metadata DB means each ``pytest`` invocation
    starts with a clean slate; demo data is seeded by the lifespan on
    the first ``TestClient`` context entry. No test writes reach
    ``backend/app.db``.
    """
    with TestClient(app) as c:
        yield c


@pytest.fixture
def tmp_sqlite_path(tmp_path) -> str:
    """Path to a fresh sqlite file under pytest's tmp dir.

    The file is not pre-created — caller's responsibility to populate if
    needed. Always auto-cleaned by pytest's tmp_path teardown. This is
    independent of the session-level isolated DB used by ``client``.
    """
    return str(tmp_path / "test.db")


@pytest.fixture
def engine_cache_cleanup():
    """Clear the module-level engine cache before AND after the test.

    Engine cache is process-global; without this, a test polluting the
    cache can leak into siblings. We also evict on teardown to free DB
    file handles on Windows / test parallelism.

    批 12: also reset the connection-pool metrics store, since
    ``get_or_create_engine`` now calls ``register_engine`` and stale
    state would leak across tests otherwise.
    """
    from app.services.connection_metrics import reset_for_testing

    _engine_cache.clear()
    reset_for_testing()
    yield
    # Dispose any engines left behind so sqlite file handles are released.
    for engine in list(_engine_cache.values()):
        engine.dispose()
    _engine_cache.clear()
    reset_for_testing()


@pytest.fixture(autouse=True)
def _reset_rate_limit_table():
    """Truncate rate_limit_events between tests so login attempts don't
    accumulate and trigger 429 on later tests in the same process.

    The rate limiter is DB-backed since P3.2, so the in-memory reset
    that lived here during P3.1 is gone — we now wipe the shared
    ``rate_limit_events`` table.

    Runs BEFORE the test's ``client`` fixture, which is what fires the
    lifespan that creates tables. So the very first test's pre-yield
    runs against an empty tmpfile — swallow ``OperationalError`` for
    "no such table" defensively (the table will exist by the time the
    test body executes).
    """
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from app.database import SessionLocal

    def _wipe() -> None:
        db = SessionLocal()
        try:
            db.execute(text("DELETE FROM rate_limit_events"))
            db.commit()
        except OperationalError:
            # Table doesn't exist yet — lifespan hasn't fired. Skip;
            # the post-yield run will catch the table once the first
            # ``client`` fixture has materialised it.
            db.rollback()
        finally:
            db.close()

    _wipe()
    yield
    _wipe()


@pytest.fixture(autouse=True)
def _cleanup_orphan_rows():
    """Per-test sweep — see ``_cleanup_orphan_rows`` for the SQL.

    Runs BEFORE the test body. Function scope means it can't catch
    state captured by module-scoped fixtures; see
    ``_cleanup_orphan_rows_module`` for that.
    """
    _sweep_orphan_rows()
    yield


@pytest.fixture(scope="module", autouse=True)
def _cleanup_orphan_rows_module():
    """Module-scoped sweep that runs BEFORE any module's first test.

    Required because some tests declare a module-scoped ``seeded_*``
    fixture (e.g. ``test_xss_regression.seeded_reports``) that captures
    a snapshot at module setup time. If only a function-scoped autouse
    ran, the module-scoped snapshot has already seen polluted state
    by the time per-test cleanup fires.

    Autouse within module scope runs first (before explicit module
    fixtures), so this fires before ``seeded_reports`` is materialised.
    """
    _sweep_orphan_rows()
    yield


def _sweep_orphan_rows() -> None:
    """Sweep rows that dangle because their parent is gone.

    SQLite has FK enforcement OFF by default, so a test that
    ``DELETE FROM data_sources`` leaves Reports with a dead
    ``data_source_id``. Other tests that iterate all reports (e.g.
    ``test_xss_regression``) crash on the orphan. We can't turn FK
    enforcement on globally (some tests rely on the lax behavior), so
    we do the cleanup ourselves at test boundaries.

    Note on what this does NOT do: no name-pattern LIKE sweep. With the
    session-tmpfile isolated DB, each test uses a unique UUID for its
    rows, so accumulated test rows are distinct by construction. The
    OLD conftest relied on knowing every possible test prefix
    (r_%, ds_%, r2_%, ...) — that's the band-aid the
    user called out on 2026-08-30. The design fix is the isolated
    tmpfile itself; the name-pattern sweep is no longer needed. Adding
    it back just recreates the whack-a-mole (every new test naming
    convention needs a LIKE-clause extension).

    Called from BOTH the function-scoped and module-scoped autouse
    fixtures above.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        db.execute(
            text(
                "DELETE FROM data_source_access "
                "WHERE data_source_id NOT IN (SELECT id FROM data_sources)"
            )
        )
        db.execute(
            text(
                "DELETE FROM reports "
                "WHERE data_source_id NOT IN (SELECT id FROM data_sources)"
            )
        )
        db.execute(
            text("DELETE FROM report_access WHERE report_id NOT IN (SELECT id FROM reports)")
        )
        db.execute(
            text("DELETE FROM report_items WHERE report_id NOT IN (SELECT id FROM reports)")
        )
        db.execute(
            text(
                "DELETE FROM report_parameters WHERE report_id NOT IN (SELECT id FROM reports)"
            )
        )
        db.execute(
            text("DELETE FROM report_versions WHERE report_id NOT IN (SELECT id FROM reports)")
        )
        db.execute(
            text(
                "DELETE FROM report_version_items "
                "WHERE version_id NOT IN (SELECT id FROM report_versions)"
            )
        )
        db.execute(
            text(
                "DELETE FROM report_version_parameters "
                "WHERE version_id NOT IN (SELECT id FROM report_versions)"
            )
        )
        db.execute(
            text(
                "DELETE FROM report_subscriptions "
                "WHERE report_id NOT IN (SELECT id FROM reports)"
            )
        )
        db.execute(
            text("DELETE FROM report_jobs WHERE report_id NOT IN (SELECT id FROM reports)")
        )
        db.commit()
    except OperationalError:
        db.rollback()
    except Exception:
        db.rollback()
    finally:
        db.close()

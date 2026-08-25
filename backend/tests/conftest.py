"""Shared pytest fixtures.

Tests rely on:
  - A running FastAPI app under ``app.main:app`` (uses the real
    ``app.db`` SQLite metadata database — same as dev).
  - A seeded dataset: at least one active ``Report`` and one ``DataSource``.
    Run ``python scripts/seed_reports.py`` once if the DB is empty.
  - ``JWT_SECRET_KEY`` set in the environment before the app modules are
    imported, so the access tokens we mint here use a stable key.

Tests that need a fresh sqlite (engine cache, data source CRUD for
non-mutating checks) use the ``tmp_sqlite_path`` fixture, which gives
an isolated file under pytest's tmp dir and never touches ``app.db``.
"""

import os
import sys
from pathlib import Path

# Ensure backend root is on sys.path so `from app...` works regardless
# of where pytest is invoked from.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# Stable JWT secret so tokens minted in tests verify on subsequent
# requests. Must be set BEFORE app modules import settings.
os.environ.setdefault("JWT_SECRET_KEY", "pytest-secret-do-not-use-in-prod")
# Stable encryption key for data-source password encryption at rest.
os.environ.setdefault("ENCRYPTION_KEY", "2wjRI6T24tbe64kcfOGqMlTCUrg5gzk82QE8BTYbpNc=")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services.jwt_auth import create_access_token  # noqa: E402
from app.services.report_generator import _engine_cache  # noqa: E402


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
    """
    with TestClient(app) as c:
        yield c


@pytest.fixture
def tmp_sqlite_path(tmp_path) -> str:
    """Path to a fresh sqlite file under pytest's tmp dir.

    The file is not pre-created — caller's responsibility to populate if
    needed. Always auto-cleaned by pytest's tmp_path teardown.
    """
    return str(tmp_path / "test.db")


@pytest.fixture
def engine_cache_cleanup():
    """Clear the module-level engine cache before AND after the test.

    Engine cache is process-global; without this, a test polluting the
    cache can leak into siblings. We also evict on teardown to free DB
    file handles on Windows / test parallelism.
    """
    _engine_cache.clear()
    yield
    # Dispose any engines left behind so sqlite file handles are released.
    for engine in list(_engine_cache.values()):
        engine.dispose()
    _engine_cache.clear()


@pytest.fixture(autouse=True)
def _reset_rate_limit_table():
    """Truncate rate_limit_events between tests so login attempts don't
    accumulate and trigger 429 on later tests in the same process.

    The rate limiter is DB-backed since P3.2, so the in-memory reset
    that lived here during P3.1 is gone — we now wipe the shared
    ``rate_limit_events`` table.
    """
    from sqlalchemy import text

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM rate_limit_events"))
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM rate_limit_events"))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _cleanup_leaked_data_source_rows():
    """Delete leaked ``data_sources`` rows between tests.

    Belt-and-braces against tests that insert a DataSource row to set
    up a scenario but never tear it down. We prune everything that
    looks like test pollution:

    * ``port = 0`` — SQLite in-flight rows. ``DataSourceResponse``
      enforces ``port >= 1`` so these silently 422 ``GET
      /data-sources`` responses when accumulated.
    * ``name LIKE 'bad-test-source-%'`` — rows inserted by
      ``test_schema_introspection`` to drive unreachable-service
      tests.
    * ``name LIKE 'pytest_%'`` — every ``_make_ds`` helper across the
      suite uses a ``pytest_<scenario>_ds_<uuid>`` prefix. Without a
      global prune the dev ``app.db`` grows past ``limit=500`` (the
      route's documented cap) and the ACL list test stops seeing
      its own freshly-inserted row.
    * ``name LIKE 'happy-sqlite-source-%'`` — rows inserted by
      ``test_schema_introspection`` happy-path tests; each points
      at a temp ``happy.db`` that pytest cleans up between runs,
      so the dangling DB row opens against a non-existent file.
    * ``name LIKE 'debug_%'`` — interactive scratch rows.
    * ``name LIKE 'ds-owner\\_%' ESCAPE '\\'`` / ``name LIKE 'ds\\_%' ESCAPE '\\'`` —
      rows inserted by the T4 / T5 report-versioning fixtures (each scenario
      gets a fresh uuid-suffixed DataSource).

    Plus their companion Report / ReportItem / ReportParameter rows:

    * ``reports.name LIKE 'r-owner\\_%' ESCAPE '\\'`` / ``reports.name LIKE
      'r\\_%' ESCAPE '\\'`` — T4 / T5 report-versioning fixtures. Without
      the prune, the XSS regression test renders every active report against
      a SQLite datasource whose schema doesn't include the test table
      (``orders``) and raises ``sqlite3.OperationalError``.

    Individual tests that create DataSource rows should still call
    the local ``_delete_ds`` (or equivalent) helper as their primary
    cleanup; this fixture is the safety net.
    """
    from sqlalchemy import text

    from app.database import SessionLocal

    def _cleanup() -> None:
        db = SessionLocal()
        try:
            # Orphan cleanup: child rows whose parent has been deleted.
            # SQLite FK enforcement is OFF by default, so DELETE on the
            # parent does not cascade. These queries sweep up orphans across
            # all the tables that can leak test data. Without this, SQLite
            # rowid reuse lets a later test's `original.id` = 345 match an
            # orphan `DataSourceAccess.data_source_id = 345`, breaking
            # `assert count == 1` style assertions (root cause of the ~30%
            # flake rate observed after T5 fixtures started creating
            # ``ds-owner_<uuid>`` DataSources).
            db.execute(
                text(
                    "DELETE FROM data_source_access "
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
            db.execute(text("DELETE FROM data_sources WHERE port = 0"))
            db.execute(text("DELETE FROM data_sources WHERE name LIKE 'bad-test-source-%'"))
            db.execute(text("DELETE FROM data_sources WHERE name LIKE 'pytest\\_%' ESCAPE '\\'"))
            db.execute(text("DELETE FROM data_sources WHERE name LIKE 'happy-sqlite-source-%'"))
            db.execute(text("DELETE FROM data_sources WHERE name LIKE 'debug_%'"))
            # T4 / T5 report-versioning fixtures use a uuid-suffixed
            # ``ds-owner_<uuid>`` / ``ds_<uuid>`` pattern. Prune both shapes.
            db.execute(
                text(
                    "DELETE FROM data_sources "
                    "WHERE name LIKE 'ds-owner\\_%' ESCAPE '\\' "
                    "   OR name LIKE 'ds\\_%' ESCAPE '\\'"
                )
            )
            # ``test_render_html_error_message_is_html_escaped`` and friends used
            # to leave scratch ``report_items`` rows with the giveaway shape
            # ``name='x' AND table_name IN ('t','x')``. Prune anything that looks
            # like that — they're attached to real reports (by id) so we can't
            # safely drop by report_id, but the marker combo is unique enough.
            db.execute(
                text(
                    "DELETE FROM report_items "
                    "WHERE name = 'x' "
                    "  AND table_name IN ('t', 'x') "
                    "  AND fields = '[\"a\"]'"
                )
            )
            # T4 / T5 report-versioning fixtures create Report + ReportItem +
            # ReportParameter rows that share a uuid-suffixed ``r-owner_<uuid>``
            # / ``r_<uuid>`` name. Delete the child rows explicitly first (the
            # SQLAlchemy cascade is ORM-level; the raw DELETE below doesn't
            # trigger it), then the parent reports. Without this the XSS
            # regression test iterates all active reports and crashes on
            # ``sqlite3.OperationalError`` for missing test tables like
            # ``orders``.
            db.execute(
                text(
                    "DELETE FROM report_items "
                    "WHERE report_id IN ("
                    "    SELECT id FROM reports "
                    "    WHERE name LIKE 'r-owner\\_%' ESCAPE '\\' "
                    "       OR name LIKE 'r\\_%' ESCAPE '\\'"
                    ")"
                )
            )
            db.execute(
                text(
                    "DELETE FROM report_parameters "
                    "WHERE report_id IN ("
                    "    SELECT id FROM reports "
                    "    WHERE name LIKE 'r-owner\\_%' ESCAPE '\\' "
                    "       OR name LIKE 'r\\_%' ESCAPE '\\'"
                    ")"
                )
            )
            # T5 tests also call ``create_snapshot()`` which inserts
            # ``report_versions`` + ``report_version_items`` +
            # ``report_version_parameters`` rows. The FKs declare ON DELETE
            # CASCADE but SQLite only enforces that when
            # ``PRAGMA foreign_keys = ON``, which the app connection does not
            # set. Prune the grandchild + child + parent rows explicitly so
            # the next ``test_list_versions_ordered_desc`` run starts from a
            # clean slate (otherwise stale versions show up against a
            # re-incremented ``reports.id``).
            db.execute(
                text(
                    "DELETE FROM report_version_items "
                    "WHERE version_id IN ("
                    "    SELECT v.id FROM report_versions v "
                    "    JOIN reports r ON r.id = v.report_id "
                    "    WHERE r.name LIKE 'r-owner\\_%' ESCAPE '\\' "
                    "       OR r.name LIKE 'r\\_%' ESCAPE '\\'"
                    ")"
                )
            )
            db.execute(
                text(
                    "DELETE FROM report_version_parameters "
                    "WHERE version_id IN ("
                    "    SELECT v.id FROM report_versions v "
                    "    JOIN reports r ON r.id = v.report_id "
                    "    WHERE r.name LIKE 'r-owner\\_%' ESCAPE '\\' "
                    "       OR r.name LIKE 'r\\_%' ESCAPE '\\'"
                    ")"
                )
            )
            db.execute(
                text(
                    "DELETE FROM report_versions "
                    "WHERE report_id IN ("
                    "    SELECT id FROM reports "
                    "    WHERE name LIKE 'r-owner\\_%' ESCAPE '\\' "
                    "       OR name LIKE 'r\\_%' ESCAPE '\\'"
                    ")"
                )
            )
            db.execute(
                text(
                    "DELETE FROM reports "
                    "WHERE name LIKE 'r-owner\\_%' ESCAPE '\\' "
                    "   OR name LIKE 'r\\_%' ESCAPE '\\'"
                )
            )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    # Run cleanup both before AND after each test: the post-test prune
    # catches what THIS test left behind, but a fresh ``pytest`` invocation
    # starts against whatever the previous invocation's last test leaked
    # (since the previous yield's post-cleanup never ran). Running
    # pre-cleanup too makes every first-test-of-the-run start from a
    # known-empty slate (regression for
    # ``test_clone_data_source_leaves_source_grants_intact`` race).
    _cleanup()
    yield
    _cleanup()

"""Alembic upgrade/downgrade roundtrip for the report_versions migration.

Runs against an isolated sqlite file under ``tmp_path`` — ``alembic/env.py``
overrides the alembic ``sqlalchemy.url`` with ``settings.database_url`` at
every command call, so a bare ``cfg.set_main_option("sqlite:///:memory:")``
would be silently discarded and the downgrade would mutate the dev ``app.db``
schema. Patching ``settings.database_url`` here keeps the roundtrip on a
throwaway file while leaving ``env.py`` (which is prod-correct: it must use
``settings``) untouched.
"""

from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import settings


def test_report_versions_migration_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "alembic_roundtrip.db"
    test_url = f"sqlite:///{db_path}"
    # ``alembic/env.py`` reads ``settings.database_url`` on every command, so
    # patch it before invoking upgrade/downgrade. monkeypatch restores it
    # after the test so siblings don't see the throwaway URL.
    monkeypatch.setattr(settings, "database_url", test_url)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", test_url)
    script = ScriptDirectory.from_config(cfg)

    rev = None
    for r in script.walk_revisions():
        if "report_versions" in (r.doc or "") or "add report versions" in (r.doc or ""):
            rev = r.revision
            break
    assert rev is not None, "report_versions migration not found"

    upgrade(cfg, rev)
    downgrade(cfg, "-1")
    upgrade(cfg, rev)

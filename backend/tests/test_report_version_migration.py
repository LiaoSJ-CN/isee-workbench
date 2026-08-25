"""Alembic upgrade/downgrade roundtrip for the report_versions migration."""

from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_report_versions_migration_roundtrip():
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", "sqlite:///:memory:")
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

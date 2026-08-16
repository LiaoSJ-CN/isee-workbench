"""Tests for TODO-8 (NotificationConfig legacy payload normalization).

Two layers of coverage:

* :func:`normalize_legacy_notification_config` — pure function over a
  single dict. Covers every shape we know how to handle plus the
  adversarial ones (None, empty, both-url-fields, unknown-type).

* End-to-end alembic upgrade — inserts mixed-shape rows directly into
  a SQLite DB, runs the new revision, asserts the on-disk payloads
  round-trip through the new ``NotificationConfig`` validator. The
  downgrade is a no-op (the pre-6b ``dict | None`` accepts the new
  shape too) so we only verify upgrade behaviour here.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.config import settings
from app.schemas.notification import NotificationConfig
from app.services.notification_migration import (
    LEGACY_WEBHOOK_FIELD,
    NEW_WEBHOOK_FIELD,
    WEBHOOK_TYPE,
    normalize_legacy_notification_config,
)

# ----- pure function -----


def test_normalize_none_returns_none() -> None:
    assert normalize_legacy_notification_config(None) is None


def test_normalize_empty_dict_returns_none() -> None:
    """``{}`` is mapped to ``None`` by the Pydantic field validator —
    this function should not rewrite it."""
    assert normalize_legacy_notification_config({}) is None


def test_normalize_correct_webhook_is_noop() -> None:
    """Already-correct webhook shape — no rewrite."""
    cfg = {"type": WEBHOOK_TYPE, NEW_WEBHOOK_FIELD: "https://x"}
    assert normalize_legacy_notification_config(cfg) is None


def test_normalize_renames_webhook_url_to_url() -> None:
    """The headline case: ``type=webhook`` + legacy ``webhook_url``."""
    cfg = {"type": WEBHOOK_TYPE, LEGACY_WEBHOOK_FIELD: "https://x"}
    new = normalize_legacy_notification_config(cfg)
    assert new == {"type": WEBHOOK_TYPE, NEW_WEBHOOK_FIELD: "https://x"}


def test_normalize_preserves_secret_during_rename() -> None:
    cfg = {
        "type": WEBHOOK_TYPE,
        LEGACY_WEBHOOK_FIELD: "https://x",
        "secret": "topsecret",
    }
    new = normalize_legacy_notification_config(cfg)
    assert new == {
        "type": WEBHOOK_TYPE,
        NEW_WEBHOOK_FIELD: "https://x",
        "secret": "topsecret",
    }


def test_normalize_dingtalk_is_noop() -> None:
    """DingTalkConfig legitimately uses ``webhook_url`` — must not rename."""
    cfg = {"type": "dingtalk", LEGACY_WEBHOOK_FIELD: "https://dt"}
    assert normalize_legacy_notification_config(cfg) is None


def test_normalize_email_is_noop() -> None:
    cfg = {"type": "email", "to": ["a@x"], "subject": "hi"}
    assert normalize_legacy_notification_config(cfg) is None


def test_normalize_data_inconsistency_leaves_alone() -> None:
    """``type=webhook`` with BOTH ``webhook_url`` and ``url`` is data
    corruption — refuse to guess which one to keep."""
    cfg = {
        "type": WEBHOOK_TYPE,
        LEGACY_WEBHOOK_FIELD: "https://legacy",
        NEW_WEBHOOK_FIELD: "https://new",
    }
    assert normalize_legacy_notification_config(cfg) is None


def test_normalize_legacy_dict_without_type_infers_webhook() -> None:
    """Pre-6b payloads often lack ``type`` entirely. Default to webhook
    when a URL-shaped field is present."""
    cfg = {LEGACY_WEBHOOK_FIELD: "https://x"}
    new = normalize_legacy_notification_config(cfg)
    assert new == {"type": WEBHOOK_TYPE, NEW_WEBHOOK_FIELD: "https://x"}


def test_normalize_legacy_dict_with_url_infers_webhook() -> None:
    cfg = {NEW_WEBHOOK_FIELD: "https://x", "secret": "s"}
    new = normalize_legacy_notification_config(cfg)
    assert new == {"type": WEBHOOK_TYPE, NEW_WEBHOOK_FIELD: "https://x", "secret": "s"}


def test_normalize_unknown_legacy_shape_leaves_alone() -> None:
    """A bare email-shaped dict (no type) — we can't tell what it is
    without guessing. Leave alone so the operator sees the 422."""
    cfg = {"to": ["a@x"], "subject": "hi"}
    assert normalize_legacy_notification_config(cfg) is None


def test_normalize_unknown_type_leaves_alone() -> None:
    """Future union variants — don't touch."""
    cfg = {"type": "slack", "channel": "#x"}
    assert normalize_legacy_notification_config(cfg) is None


def test_normalize_does_not_mutate_input() -> None:
    """The function must return a new dict, not mutate in place — the
    alembic migration walks rows and would race on shared references
    if we mutated the input."""
    cfg = {"type": WEBHOOK_TYPE, LEGACY_WEBHOOK_FIELD: "https://x"}
    snapshot = dict(cfg)
    normalize_legacy_notification_config(cfg)
    assert cfg == snapshot


# ----- end-to-end alembic upgrade -----


@pytest.fixture
def fresh_db(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Create a fresh SQLite DB in tmp_path and point all of our
    database plumbing at it.

    We let alembic build the schema by running ``upgrade head`` — that
    way the on-disk schema matches the migration chain exactly, no
    ``Base.metadata.create_all`` vs alembic mismatch. The default
    ``settings.database_url`` would resolve to dev ``app.db`` and
    pollute the working copy; tmp_path keeps every test isolated.
    """
    db_path = tmp_path / "notif_migration.db"
    test_url = f"sqlite:///{db_path}"

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(settings, "database_url", test_url)
    test_engine = create_engine(test_url, connect_args={"check_same_thread": False})
    # SessionLocal is bound to the original engine at module load —
    # swap both so any code path that holds a reference still talks
    # to the temp DB.
    test_session = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr("app.database.engine", test_engine)
    monkeypatch.setattr("app.database.SessionLocal", test_session)

    # Build the schema up to (but NOT including) our new revision.
    # The new migration is data-only — we want each test to actually
    # invoke its ``upgrade()`` body, so we leave the DB at the previous
    # revision. The tests then call ``_run_alembic("c0a2b1d4e5f6")``
    # which triggers the migration.
    from alembic.config import Config as AlembicConfig

    from alembic import command

    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", test_url)
    command.upgrade(cfg, "222001adeb57")

    yield
    test_engine.dispose()
    # tmp_path cleanup is automatic; no teardown needed for the DB file.


def _insert_report(notification_config: Any) -> int:
    """Insert a Report row with the given notification_config and return id.

    Uses uuid-suffixed names so repeated test runs (and same-test
    multi-row inserts) don't trip the ``reports.name`` unique constraint.

    NOTE: we look up ``SessionLocal`` via the ``app.database`` module
    each call so monkeypatched fixtures redirect us to the tmp DB —
    a top-of-file ``from app.database import SessionLocal`` captures
    the original sessionmaker at import time.
    """
    from app import database as _database

    suffix = uuid.uuid4().hex[:8]
    db = _database.SessionLocal()
    try:
        db.execute(
            text(
                "INSERT INTO data_sources (name, db_type, host, port, database, "
                "username, password) VALUES (:n, 'sqlite', 'h', 0, ':memory:', 'u', 'p')"
            ),
            {"n": f"notif-ds-{suffix}"},
        )
        src_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        cfg_json = (
            json.dumps(notification_config)
            if notification_config is not None
            else None
        )
        db.execute(
            text(
                "INSERT INTO reports (name, data_source_id, is_active, "
                "is_scheduled, notification_config) VALUES (:n, :ds, 1, 0, :cfg)"
            ),
            {"n": f"notif-rep-{suffix}", "ds": src_id, "cfg": cfg_json},
        )
        rep_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
        db.commit()
        return int(rep_id)
    finally:
        db.close()


def _read_config(rep_id: int) -> Any:
    """Read notification_config for the given report id.

    Same module-attr lookup pattern as :func:`_insert_report`.
    """
    from app import database as _database

    db = _database.SessionLocal()
    try:
        row = db.execute(
            text("SELECT notification_config FROM reports WHERE id = :id"),
            {"id": rep_id},
        ).scalar()
        if row is None:
            return None
        return json.loads(row) if isinstance(row, str) else row
    finally:
        db.close()


def test_alembic_upgrade_normalizes_legacy_rows(fresh_db: Any) -> None:
    """Insert a mix of legacy shapes, run the new revision, assert every
    row is either normalized or (for unknown shapes) untouched — never
    lost."""
    legacy_rows = {
        # already correct → no change
        "ok_webhook": {"type": WEBHOOK_TYPE, NEW_WEBHOOK_FIELD: "https://a"},
        # headline rename
        "legacy_webhook_url": {
            "type": WEBHOOK_TYPE,
            LEGACY_WEBHOOK_FIELD: "https://b",
        },
        # rename + secret preserved
        "legacy_webhook_with_secret": {
            "type": WEBHOOK_TYPE,
            LEGACY_WEBHOOK_FIELD: "https://c",
            "secret": "k",
        },
        # dingtalk — webhook_url is correct field name, untouched
        "dingtalk": {"type": "dingtalk", LEGACY_WEBHOOK_FIELD: "https://d"},
        # email — untouched
        "email": {"type": "email", "to": ["a@x"], "subject": "hi"},
        # no type, has webhook_url → inferred webhook
        "no_type_webhook_url": {LEGACY_WEBHOOK_FIELD: "https://e"},
        # no type, has url → inferred webhook
        "no_type_url": {NEW_WEBHOOK_FIELD: "https://f"},
        # data inconsistency — untouched (will still 422 on read)
        "both_url_fields": {
            "type": WEBHOOK_TYPE,
            LEGACY_WEBHOOK_FIELD: "https://legacy",
            NEW_WEBHOOK_FIELD: "https://new",
        },
        # unknown shape — untouched
        "unknown": {"to": ["a@x"], "subject": "hi"},
    }

    rep_ids = {label: _insert_report(cfg) for label, cfg in legacy_rows.items()}
    _run_alembic(target="c0a2b1d4e5f6")

    expectations = {
        "ok_webhook": {"type": WEBHOOK_TYPE, NEW_WEBHOOK_FIELD: "https://a"},
        "legacy_webhook_url": {"type": WEBHOOK_TYPE, NEW_WEBHOOK_FIELD: "https://b"},
        "legacy_webhook_with_secret": {
            "type": WEBHOOK_TYPE,
            NEW_WEBHOOK_FIELD: "https://c",
            "secret": "k",
        },
        "dingtalk": {"type": "dingtalk", LEGACY_WEBHOOK_FIELD: "https://d"},
        "email": {"type": "email", "to": ["a@x"], "subject": "hi"},
        "no_type_webhook_url": {"type": WEBHOOK_TYPE, NEW_WEBHOOK_FIELD: "https://e"},
        "no_type_url": {"type": WEBHOOK_TYPE, NEW_WEBHOOK_FIELD: "https://f"},
        "both_url_fields": {
            "type": WEBHOOK_TYPE,
            LEGACY_WEBHOOK_FIELD: "https://legacy",
            NEW_WEBHOOK_FIELD: "https://new",
        },
        "unknown": {"to": ["a@x"], "subject": "hi"},
    }

    for label, expected in expectations.items():
        actual = _read_config(rep_ids[label])
        assert actual == expected, f"{label}: got {actual!r}, expected {expected!r}"

    # And confirm the rewritten shapes round-trip through the new
    # Pydantic union — the operator-visible win.
    from pydantic import TypeAdapter

    validator = TypeAdapter(NotificationConfig)
    rewritten_ids = [
        rep_ids["legacy_webhook_url"],
        rep_ids["legacy_webhook_with_secret"],
        rep_ids["no_type_webhook_url"],
        rep_ids["no_type_url"],
    ]
    for rid in rewritten_ids:
        cfg = _read_config(rid)
        # Validates the dict against the union discriminator and returns
        # the matching concrete variant (WebhookConfig / EmailConfig /
        # DingTalkConfig).
        assert validator.validate_python(cfg) is not None


def test_alembic_upgrade_handles_null_rows(fresh_db: Any) -> None:
    """NULL rows are a no-op — the migration must not crash on them."""
    _insert_report(None)
    _insert_report(None)
    _run_alembic(target="c0a2b1d4e5f6")
    # No assertion needed — reaching here without exception is the test.


def test_alembic_downgrade_is_noop(fresh_db: Any) -> None:
    """Downgrade should succeed without altering data — pre-6b schema
    accepts the new shape too."""
    rid = _insert_report({"type": WEBHOOK_TYPE, LEGACY_WEBHOOK_FIELD: "https://x"})
    _run_alembic(target="c0a2b1d4e5f6")
    _run_alembic(target="222001adeb57")

    # After downgrade, the data is still the normalized shape (the
    # downgrade is intentionally a no-op). The pre-6b dict field would
    # have accepted the original legacy shape just as well.
    assert _read_config(rid) == {
        "type": WEBHOOK_TYPE,
        NEW_WEBHOOK_FIELD: "https://x",
    }


# ----- alembic helpers -----


def _run_alembic(target: str) -> None:
    """Run alembic up-or-down to ``target``.

    Schema and alembic_version are both populated by ``fresh_db``
    (via ``command.upgrade "222001adeb57"``). All this helper does
    is run upgrade/downgrade between the prior revision and the
    target.
    """
    from alembic.config import Config as AlembicConfig

    from alembic import command

    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)

    if target == "c0a2b1d4e5f6":
        command.upgrade(cfg, target)
    elif target == "222001adeb57":
        command.downgrade(cfg, target)
    else:
        raise ValueError(f"unsupported alembic target in tests: {target}")

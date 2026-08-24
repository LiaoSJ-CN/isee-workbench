"""Tests for ``app.db_migrations.ensure_columns``.

``Base.metadata.create_all`` only creates missing TABLES, not missing
COLUMNS, so adding a ``Column(...)`` to a model is silently invisible
on a database that was created before the column was introduced. The
production case this fixes: long-running deployment upgrades from a
version of the app that pre-dated a new column. ``ensure_columns``
backfills such columns at startup so the schema catches up to the
model without a manual ``ALTER TABLE``.

Skip-not-null-without-default behavior is intentional: existing rows
cannot satisfy ``NOT NULL`` without a value, and backfilling values
out of scope here. Operators must write a manual migration for those.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine


def _engine_for(db_path: str) -> Engine:
    return create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )


# --- behavior tests against isolated metadata -------------------------------


def test_adds_missing_column_to_existing_table(tmp_sqlite_path: str) -> None:
    from app.db_migrations import ensure_columns

    eng = _engine_for(tmp_sqlite_path)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name VARCHAR(50))"))

    md = MetaData()
    Table(
        "widgets",
        md,
        Column("id", Integer, primary_key=True),
        Column("name", String(50)),
        Column("color", String(20), nullable=True),
    )

    added = ensure_columns(eng, metadata=md)

    assert added == [("widgets", "color")]
    cols = {c["name"] for c in inspect(eng).get_columns("widgets")}
    assert cols == {"id", "name", "color"}


def test_idempotent_when_schema_matches_metadata(tmp_sqlite_path: str) -> None:
    """Re-running ``ensure_columns`` on a matching schema is a no-op."""
    from app.db_migrations import ensure_columns

    eng = _engine_for(tmp_sqlite_path)
    md = MetaData()
    Table(
        "widgets",
        md,
        Column("id", Integer, primary_key=True),
        Column("name", String(50)),
    )
    md.create_all(eng)

    assert ensure_columns(eng, metadata=md) == []
    assert ensure_columns(eng, metadata=md) == []


def test_adds_multiple_missing_columns_in_one_pass(tmp_sqlite_path: str) -> None:
    from app.db_migrations import ensure_columns

    eng = _engine_for(tmp_sqlite_path)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE widgets (id INTEGER PRIMARY KEY)"))

    md = MetaData()
    Table(
        "widgets",
        md,
        Column("id", Integer, primary_key=True),
        Column("name", String(50), nullable=True),
        Column("color", String(20), nullable=True),
        Column("qty", Integer, nullable=True),
    )

    added = ensure_columns(eng, metadata=md)

    assert sorted(added) == [
        ("widgets", "color"),
        ("widgets", "name"),
        ("widgets", "qty"),
    ]
    cols = {c["name"] for c in inspect(eng).get_columns("widgets")}
    assert cols == {"id", "name", "color", "qty"}


def test_skips_not_null_column_without_server_default(tmp_sqlite_path: str, caplog) -> None:
    """NOT NULL without ``server_default`` cannot be backfilled on a
    populated table; ``ensure_columns`` logs a warning and skips it."""
    import logging

    from app.db_migrations import ensure_columns

    eng = _engine_for(tmp_sqlite_path)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE widgets (id INTEGER PRIMARY KEY)"))
        # Pre-existing row forces a default to be supplied for NOT NULL.
        conn.execute(text("INSERT INTO widgets DEFAULT VALUES"))

    md = MetaData()
    Table(
        "widgets",
        md,
        Column("id", Integer, primary_key=True),
        Column("required_flag", Integer, nullable=False),
    )

    with caplog.at_level(logging.WARNING):
        added = ensure_columns(eng, metadata=md)

    assert added == []
    cols = {c["name"] for c in inspect(eng).get_columns("widgets")}
    assert "required_flag" not in cols
    assert any(
        "required_flag" in record.message and "manual" in record.message.lower()
        for record in caplog.records
    ), caplog.text


def test_adds_not_null_column_with_server_default(tmp_sqlite_path: str) -> None:
    """NOT NULL with ``server_default`` can be added safely — the existing
    row is backfilled with the default value (SQLite and PG11+ behavior)."""
    from app.db_migrations import ensure_columns

    eng = _engine_for(tmp_sqlite_path)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE widgets (id INTEGER PRIMARY KEY)"))
        # Pre-existing row forces the engine to backfill on ALTER.
        conn.execute(text("INSERT INTO widgets DEFAULT VALUES"))

    md = MetaData()
    Table(
        "widgets",
        md,
        Column("id", Integer, primary_key=True),
        Column(
            "status",
            String(20),
            nullable=False,
            server_default="active",
        ),
    )

    added = ensure_columns(eng, metadata=md)

    assert added == [("widgets", "status")]
    with eng.connect() as conn:
        status = conn.execute(text("SELECT status FROM widgets")).scalar()
    assert status == "active"


def test_skips_table_missing_from_database(tmp_sqlite_path: str) -> None:
    """A table declared in metadata but not created yet (e.g. ``create_all``
    has not run) is skipped silently — that case is ``create_all``'s job."""
    from app.db_migrations import ensure_columns

    eng = _engine_for(tmp_sqlite_path)
    md = MetaData()
    Table(
        "widgets",
        md,
        Column("id", Integer, primary_key=True),
        Column("color", String(20), nullable=True),
    )

    added = ensure_columns(eng, metadata=md)

    assert added == []


def test_does_not_touch_unrelated_tables(tmp_sqlite_path: str) -> None:
    from app.db_migrations import ensure_columns

    eng = _engine_for(tmp_sqlite_path)
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE widgets (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE gadgets (id INTEGER PRIMARY KEY)"))

    md = MetaData()
    Table(
        "widgets",
        md,
        Column("id", Integer, primary_key=True),
        Column("color", String(20), nullable=True),
    )
    Table(
        "gadgets",
        md,
        Column("id", Integer, primary_key=True),
    )

    added = ensure_columns(eng, metadata=md)

    assert added == [("widgets", "color")]
    gadget_cols = {c["name"] for c in inspect(eng).get_columns("gadgets")}
    assert gadget_cols == {"id"}


# --- regression test against the real Base.metadata -------------------------


def test_real_metadata_backfills_notification_config_column(
    tmp_sqlite_path: str,
) -> None:
    """Reproduces the original bug: a database created before
    ``Report.notification_config`` existed gets the column on next
    startup, with no manual ``ALTER TABLE`` needed.

    This is the deployment-upgrade scenario: long-running install,
    table was created by an older version of the app, the model gains
    a new column, the next process restart must catch the schema up.
    """
    from app.db_migrations import ensure_columns

    eng = _engine_for(tmp_sqlite_path)
    # Simulate an older deployment: create the reports table WITHOUT the
    # notification_config column that exists in the current model.
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE reports (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    description TEXT,
                    data_source_id INTEGER NOT NULL,
                    layout_config JSON,
                    is_scheduled BOOLEAN,
                    cron_expression VARCHAR(100),
                    schedule_description VARCHAR(255),
                    output_formats JSON,
                    is_active BOOLEAN,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )

    # Real Base.metadata declares notification_config — it should be added.
    added = ensure_columns(eng)

    assert ("reports", "notification_config") in added
    cols = {c["name"] for c in inspect(eng).get_columns("reports")}
    assert "notification_config" in cols
    # Base.metadata also has data_sources and report_items which do NOT
    # exist in this old-DB simulation; ensure_columns must skip them.
    # We assert that by checking the return only listed real additions.
    for table, _ in added:
        assert table == "reports", f"unexpected table backfilled: {table}"

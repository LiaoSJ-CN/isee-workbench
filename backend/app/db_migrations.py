"""Lightweight runtime schema-migration helper.

``Base.metadata.create_all`` (called from ``app.main``) only creates
**missing TABLES**, never adds **missing COLUMNS** to existing tables.
That means adding a new ``Column(...)`` to a model is silently invisible
on databases that were created before the column was introduced — the
column never makes it into the schema, INSERTs start failing, and the
operator has to run a manual ``ALTER TABLE``.

``ensure_columns`` fills that gap at startup: it compares the columns
declared in SQLAlchemy ``MetaData`` against what the live database has,
and emits ``ALTER TABLE ADD COLUMN`` for any missing ones. It is
idempotent — running it on a schema that already matches metadata is a
no-op — and safe to invoke on every process boot.

What it does NOT do (and why):
    - Does not create tables. That is ``create_all``'s job; missing
      tables are skipped silently.
    - Does not backfill ``NOT NULL`` columns without a ``server_default``.
      Existing rows cannot satisfy ``NOT NULL`` without a value, and
      guessing one is out of scope. Those emit a warning and are skipped
      so operators can write a manual migration.
    - Does not drop columns, rename columns, or change types. Add/remove
      via a real migration tool if/when that becomes necessary.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.schema import CreateColumn

from app.database import Base

logger = logging.getLogger(__name__)


def ensure_columns(
    engine: Engine,
    metadata: Optional[MetaData] = None,
) -> list[tuple[str, str]]:
    """Idempotently add columns declared in ``metadata`` that the DB lacks.

    Parameters
    ----------
    engine:
        The SQLAlchemy engine bound to the target database.
    metadata:
        Schema to reconcile against. Defaults to ``Base.metadata`` so the
        production call from ``app.main`` does not need to pass anything;
        tests pass an isolated ``MetaData()`` for determinism.

    Returns
    -------
    list[tuple[str, str]]
        ``(table_name, column_name)`` pairs that were added. Empty when
        the schema already matches.
    """
    if metadata is None:
        metadata = Base.metadata

    inspector = inspect(engine)
    added: list[tuple[str, str]] = []

    with engine.begin() as conn:
        for table_name, table in metadata.tables.items():
            try:
                existing = {c["name"] for c in inspector.get_columns(table_name)}
            except NoSuchTableError:
                # create_all will create it on the next restart; not our job.
                continue

            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable and column.server_default is None:
                    logger.warning(
                        "ensure_columns: skipping %s.%s — NOT NULL without "
                        "server_default requires a manual migration",
                        table_name,
                        column.name,
                    )
                    continue

                # ``CreateColumn`` compiles to dialect-aware DDL, including
                # type, nullability, and server default. Some dialects emit
                # the leading column identifier; strip it so ADD COLUMN
                # can place the name itself.
                ddl = str(CreateColumn(column).compile(dialect=engine.dialect))
                quoted_name = f'"{column.name}"'
                if ddl.startswith(quoted_name):
                    ddl = ddl[len(quoted_name) :].lstrip()

                sql = f'ALTER TABLE "{table_name}" ADD COLUMN {ddl}'
                conn.execute(text(sql))
                added.append((table_name, column.name))

    return added

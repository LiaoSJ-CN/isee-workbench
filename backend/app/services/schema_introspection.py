"""Schema introspection for the schema-browser endpoint (批 8.2).

Walks the connected data source's metadata tables and returns a list of
``TableInfo`` (table name + columns). Used by the React frontend to
populate the schema tree in DataExplorer.

Backend coverage
----------------
- **PostgreSQL / OpenGauss / DWS** use the ANSI-compliant
  ``information_schema.columns`` view. We filter by ``table_schema``
  (defaults to ``"public"``) and restrict ``table_type`` to
  ``BASE TABLE`` + ``VIEW`` so the tree shows only user-visible objects.

- **SQLite** doesn't expose ``information_schema.columns`` reliably, so
  we walk ``sqlite_master`` (list of tables/views) and use
  ``pragma_table_info`` to fetch columns. SQLite has no schema
  namespace — the schema name is reported as ``"main"`` to match the
  SQL ``main`` schema.

Both paths produce identical ``TableInfo`` shape so the frontend can
render uniformly. Connection failures bubble up as
``SchemaIntrospectionError`` and the router translates them to HTTP
502 (we're a proxy to the upstream DB).
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.models.data_source import DataSource
from app.schemas.data_source import ColumnInfo, TableInfo
from app.services.connection import build_connection_url

logger = logging.getLogger(__name__)


class SchemaIntrospectionError(Exception):
    """Raised when the upstream data source can't be introspected.

    The router maps this to HTTP 502 (Bad Gateway) because the failure
    is on the upstream side, not the workbench API itself.
    """


def _resolve_schema_name(source: DataSource, override: str | None) -> str:
    """Pick the schema to introspect, with sensible defaults per dialect."""
    if override:
        return override
    if source.schema_name:
        return str(source.schema_name)
    if source.db_type == "sqlite":
        # SQLite has a single schema named "main".
        return "main"
    return "public"


def _introspect_postgres(engine: Engine, schema: str) -> list[TableInfo]:
    """Walk ``information_schema.columns`` for a Postgres-family database."""
    sql = text(
        """
        SELECT
            c.table_name,
            c.column_name,
            c.data_type,
            (c.is_nullable = 'YES') AS is_nullable
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON t.table_schema = c.table_schema
         AND t.table_name = c.table_name
        WHERE c.table_schema = :schema
          AND t.table_type IN ('BASE TABLE', 'VIEW')
        ORDER BY c.table_name, c.ordinal_position
        """
    )
    by_table: dict[str, TableInfo] = {}
    with engine.connect() as conn:
        for row in conn.execute(sql, {"schema": schema}):
            table_name = str(row.table_name)
            entry = by_table.get(table_name)
            if entry is None:
                entry = TableInfo(name=table_name, schema_name=schema)
                by_table[table_name] = entry
            entry.columns.append(
                ColumnInfo(
                    name=str(row.column_name),
                    type=str(row.data_type),
                    nullable=bool(row.is_nullable),
                )
            )
    return list(by_table.values())


def _introspect_sqlite(engine: Engine, schema: str) -> list[TableInfo]:
    """Walk ``sqlite_master`` + ``pragma_table_info`` for SQLite."""
    list_sql = text(
        """
        SELECT name, type
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    )
    tables: list[TableInfo] = []
    with engine.connect() as conn:
        for row in conn.execute(list_sql):
            table_name = str(row.name)
            # ``pragma_table_info`` returns one row per column.
            # Schema identifier ``main`` is implicit in SQLite; we
            # quote the table name so a weird name like ``select``
            # can't break the query.
            pragma = text(f'PRAGMA "{schema}".table_info("{table_name}")')
            columns: list[ColumnInfo] = []
            for col in conn.execute(pragma):
                # pragma_table_info columns: cid, name, type, notnull,
                # dflt_value, pk. ``notnull`` is 0/1 (not a boolean);
                # invert so the schema response stays typed.
                columns.append(
                    ColumnInfo(
                        name=str(col.name),
                        type=str(col.type) if col.type is not None else "",
                        nullable=not bool(col.notnull),
                    )
                )
            tables.append(TableInfo(name=table_name, schema_name=schema, columns=columns))
    return tables


def introspect_schema(
    source: DataSource,
    *,
    schema_name: str | None = None,
) -> list[TableInfo]:
    """Return the list of user tables for ``source``.

    ``schema_name`` overrides the data source's configured schema; pass
    ``None`` to use the source's ``schema_name`` field (or ``"public"``
    / ``"main"`` for the dialect default).

    Raises:
        SchemaIntrospectionError: connection failed, permission denied,
            or the schema does not exist.
    """
    schema = _resolve_schema_name(source, schema_name)
    url = build_connection_url(source)
    if source.db_type == "sqlite":
        engine = create_engine(url)
    else:
        engine = create_engine(url, connect_args={"connect_timeout": 10})

    try:
        if source.db_type == "sqlite":
            return _introspect_sqlite(engine, schema)
        return _introspect_postgres(engine, schema)
    except SQLAlchemyError as exc:
        logger.error("Schema introspection failed for source %s: %s", source.id, exc)
        raise SchemaIntrospectionError("Failed to introspect schema") from exc
    finally:
        engine.dispose()

"""SQL query builder for report items (批 5.2).

Takes a :class:`~app.models.report.ReportItem` and a parameters dict,
returns ``(query, params)`` ready to hand to a SQLAlchemy connection.

All name/operator validation delegates to
:mod:`app.services.sql_validator`, so a single AST-based defense
covers the explorer's raw query, the report-item auto-builder, and
``custom_sql`` templates.

Why a free function (not a method on ``ReportGenerator``):
    The builder is pure — given the same item + params it returns
    the same SQL — and lifting it out of the class makes it trivially
    unit-testable in isolation. The :class:`ReportGenerator` class
    below keeps ``build_query`` as a thin method that delegates here
    so callers using ``with ReportGenerator(...) as gen: gen.build_query(...)``
    keep working.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.models.report import ReportItem
from app.services.report_generator.errors import ReportGeneratorError
from app.services.sql_validator import (
    UnsafeSQLError,
    build_safe_where_clause,
    is_safe_qualified_identifier,
    is_safe_select_expression,
    substitute_parameters,
)

logger = logging.getLogger(__name__)


def build_query(
    item: ReportItem, parameters: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Build SQL query from report item configuration with parameterized values.

    Returns:
        Tuple of (query_string, parameters_dict) for safe query execution.

    Raises:
        ReportGeneratorError: any unsafe input (bad table/field name,
            disallowed WHERE operator, malformed ``custom_sql``).
    """
    try:
        if item.custom_sql:
            # ``substitute_parameters`` also validates the resulting
            # SQL, so a ``custom_sql`` that hides DML behind a {param}
            # is caught here.
            return substitute_parameters(item.custom_sql, parameters)

        table_name = item.table_name
        if not table_name:
            raise ReportGeneratorError(
                f"Report item '{item.name}' has no table_name defined"
            )
        if not is_safe_qualified_identifier(table_name):
            raise ReportGeneratorError(f"Invalid table name: {table_name}")

        # Build SELECT clause. ``*`` is passed through; every other
        # entry is parsed by sqlglot and rejected if it contains a
        # statement separator, a comment, a quoted identifier, or a
        # forbidden AST node.
        fields = item.fields if item.fields else ["*"]
        validated_fields: list[str] = []
        for f in fields:
            if f == "*" or is_safe_select_expression(f):
                validated_fields.append(f)
            else:
                raise ReportGeneratorError(
                    f"Invalid field/expression in SELECT: {f}"
                )
        select_clause = ", ".join(validated_fields)

        # Build WHERE clause via the whitelisted-operator helper.
        where_parts: list[str] = []
        params: dict[str, Any] = {}
        param_index = 0

        for cond in (item.where_conditions or []):
            field = cond.get("field") if isinstance(cond, dict) else cond.field
            operator = cond.get("operator") if isinstance(cond, dict) else cond.operator
            value = cond.get("value") if isinstance(cond, dict) else cond.value

            # Resolve a ``{param}`` value before validation so the
            # operator sees the real type.
            if (
                isinstance(value, str)
                and value.startswith("{")
                and value.endswith("}")
            ):
                value = parameters.get(value[1:-1], value)

            fragment, param_index = build_safe_where_clause(
                str(field), str(operator), value, params, param_index=param_index
            )
            where_parts.append(fragment)

        # Build GROUP BY clause.
        group_by_clause = ""
        if item.group_by:
            validated_group_by: list[str] = []
            for f in item.group_by:
                if is_safe_qualified_identifier(f):
                    validated_group_by.append(f)
                else:
                    raise ReportGeneratorError(
                        f"Invalid field name in GROUP BY: {f}"
                    )
            group_by_clause = f" GROUP BY {', '.join(validated_group_by)}"

        # Build ORDER BY clause (direction stays whitelisted to ASC/DESC).
        order_by_parts: list[str] = []
        for ob in (item.order_by or []):
            field = ob.get("field") if isinstance(ob, dict) else ob.field
            direction = (
                ob.get("direction", "ASC") if isinstance(ob, dict) else ob.direction
            )
            if not is_safe_qualified_identifier(str(field)):
                raise ReportGeneratorError(
                    f"Invalid field name in ORDER BY: {field}"
                )
            if direction.upper() not in ("ASC", "DESC"):
                direction = "ASC"
            order_by_parts.append(f"{field} {direction}")
        order_by_clause = (
            f" ORDER BY {', '.join(order_by_parts)}" if order_by_parts else ""
        )

        # Build LIMIT clause (validate integer; bind it as a param).
        limit_clause = ""
        if item.limit is not None:
            try:
                limit_val = int(item.limit)
                if limit_val > 0:
                    limit_clause = " LIMIT :limit_param"
                    params["limit_param"] = limit_val
            except (ValueError, TypeError):
                logger.warning(
                    "Invalid limit=%r for report_item %s — "
                    "ignoring, query will be unbounded",
                    item.limit, item.id,
                )

        # Assemble query.
        query = f"SELECT {select_clause} FROM {table_name}"
        if where_parts:
            query += " WHERE " + " AND ".join(where_parts)
        query += group_by_clause + order_by_clause + limit_clause
        return query, params

    except UnsafeSQLError as exc:
        # Surface validator errors through the existing public
        # exception type so the router / tests that catch
        # ``ReportGeneratorError`` keep working.
        raise ReportGeneratorError(str(exc)) from None


def execute_query(engine: Engine, query: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    """Execute a SQL query with parameters and return results as a DataFrame.

    Free function (formerly ``ReportGenerator.execute_query``). Takes the
    engine explicitly so callers can pick a different connection per
    call without going through the context manager.
    """
    from sqlalchemy.exc import SQLAlchemyError

    try:
        with engine.connect() as conn:
            if params:
                df = pd.read_sql(text(query), conn, params=params)
            else:
                df = pd.read_sql(text(query), conn)
        return df
    except SQLAlchemyError as exc:
        raise ReportGeneratorError(f"Query execution failed: {exc}") from exc

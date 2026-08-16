"""Centralized SQL safety validation.

Single source of truth for every SQL fragment the application accepts
from a user: the explorer endpoint's raw query, and the report-item
auto-builder's table / field / where / operator arguments. The module
wraps ``sqlglot`` so we lean on a real AST instead of the regex +
keyword-list + comment-stripping defenses the previous code used —
those missed several injection shapes (CTE-wrapped DML, dollar-quote
bypass, operator injection in WHERE, quote/comment characters in
field expressions).

Public surface
--------------
- :class:`UnsafeSQLError` — raised on any rejection.
- :func:`validate_select_only` — for the explorer's raw SQL.
- :func:`is_safe_identifier` / :func:`is_safe_qualified_identifier`
  — for table / field names.
- :func:`is_safe_select_expression` — for SELECT-list entries.
- :func:`build_safe_where_clause` — for WHERE fragments with a
  whitelisted operator and bound value.
- :func:`substitute_parameters` — for ``custom_sql`` templates that
  use ``{key}`` placeholders; values are bound, not interpolated,
  and the result is then validated.
- :data:`ALLOWED_WHERE_OPERATORS` — the operator whitelist.
"""

from __future__ import annotations

import re
from typing import Any, Final, NoReturn, cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from app.middleware.metrics import sql_validator_rejections_total


def _reject(rule: str, message: str) -> NoReturn:
    """Increment ``sql_validator_rejections_total{rule=...}`` and raise.

    Every rejection path in this module goes through here so the
    Prometheus counter stays in sync with the actual failure reason.
    Callers should pass a stable ``rule`` token (not the user-facing
    message) — the token is what dashboards alert on.
    """
    sql_validator_rejections_total.labels(rule=rule).inc()
    raise UnsafeSQLError(message)

# ---------- public error type ----------


class UnsafeSQLError(ValueError):
    """Raised when SQL or an SQL fragment fails safety validation."""


# ---------- operator whitelist (SEC-16) ----------


ALLOWED_WHERE_OPERATORS: Final[frozenset[str]] = frozenset(
    {
        "=", "!=", "<>", "<", "<=", ">", ">=",
        "LIKE", "ILIKE", "NOT LIKE", "NOT ILIKE",
        "IN", "NOT IN",
        "IS NULL", "IS NOT NULL",
        "BETWEEN", "NOT BETWEEN",
    }
)


# ---------- internal AST policy ----------


# Statement kinds that are never allowed anywhere in a user-supplied
# query (top-level, inside a CTE, inside a subquery). Names match
# ``sqlglot.exp.<Name>``. ``Command`` is a generic wrapper that
# sqlglot uses when it can't make sense of a statement (e.g. ``VACUUM``,
# ``CALL my_proc``, ``EXPLAIN ANALYZE …``) — the only safe default
# is to reject it because we can't verify what it would execute.
_FORBIDDEN_NODE_KINDS: Final[frozenset[str]] = frozenset(
    k.lower() for k in (
        # DML
        "Insert", "Update", "Delete", "Merge",
        # DDL
        "Drop", "TruncateTable",
        "Alter", "Create",
        "Grant", "Revoke",
        # admin / session
        "Copy", "Call",
        "Set", "Pragma", "Lock",
        "Vacuum", "Reindex",
        # sqlglot fallbacks
        "Command", "Describe", "Use",
    )
)

# Bound on how many dotted parts a qualified identifier may have
# (schema.table.column…). 8 covers any realistic naming scheme and
# stops a malicious `a.b.c.d.…` from expanding unboundedly.
_MAX_QUALIFIED_DEPTH: Final[int] = 8

_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# A SELECT-list entry must not contain a statement separator or a
# SQL comment marker. sqlglot would happily parse past them, but
# the previous regex was strict on these and we want to keep the
# same contract — comments are a common payload-hiding trick.
_FIELD_BAD_PATTERNS: Final[tuple[str, ...]] = (";", "--", "/*")

# A whole user query must start (after leading whitespace and
# comments) with SELECT or WITH. This is belt-and-suspenders for
# sqlglot quirks where e.g. ``REINDEX`` is parsed as a bare ``Column``
# or ``VACUUM`` is parsed as a generic ``Command`` — the parse
# would otherwise let the query past the AST walk.
_TOP_LEVEL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:--[^\n]*\n|/\*.*?\*/|\s)*(SELECT|WITH)\b",
    re.IGNORECASE | re.DOTALL,
)

# A bare statement separator is never allowed anywhere in user SQL.
# Stacked-statement cases (e.g. ``SELECT 1; SELECT 2``) are also
# caught by sqlglot's multi-statement check, but this text-level
# pre-check also catches a trailing ``;`` which sqlglot would
# silently swallow as part of a single statement.
_BARE_SEMICOLON_RE: Final[re.Pattern[str]] = re.compile(
    # `;` not inside a single-quoted string. Even number of `'` after
    # the `;` ⇒ we are outside any open literal.
    r";(?=(?:[^']*'[^']*')*[^']*$)",
)


# ---------- internals ----------


def _walk_for_forbidden(node: exp.Expression) -> None:
    """Recursively assert no node in the AST is a forbidden kind.

    ``SELECT ... INTO newtab`` is a write even though the top-level
    is a ``Select``, so we also reject any ``exp.Into`` node we
    encounter.
    """
    kind = type(node).__name__.lower()
    if kind in _FORBIDDEN_NODE_KINDS:
        _reject("forbidden_node", f"forbidden statement kind: {type(node).__name__}")
    if isinstance(node, exp.Into):
        _reject("select_into", "SELECT INTO is not allowed")
    for child in node.iter_expressions():
        _walk_for_forbidden(child)


def _has_quoted_identifier(node: exp.Expression) -> bool:
    """True if the AST has any quoted or backticked identifier.

    Quoted identifiers let a user smuggle arbitrary characters
    (including statement separators and comments) into what should
    be a plain column or table name. We don't need quoted identifiers
    anywhere in this app, so reject them outright.
    """
    for n in node.walk():
        if isinstance(n, exp.Identifier) and n.args.get("quoted"):
            return True
    return False


# ---------- public surface ----------


def validate_select_only(sql: str, *, dialect: str | None = None) -> None:
    """Parse ``sql`` and assert it is a single, safe SELECT.

    Walks the AST (including CTEs and subqueries) and rejects any
    data-modifying, DDL, or admin statement. Comments and whitespace
    are handled by sqlglot natively; we do not strip them ourselves.

    Raises:
        UnsafeSQLError: parse failure, multi-statement, forbidden
            AST node anywhere, top-level not a ``Select``.
    """
    if not sql or not sql.strip():
        _reject("empty", "empty SQL")

    # Cheap pre-check: reject anything that doesn't even start with
    # SELECT or WITH. Defends against sqlglot's quirks where e.g.
    # ``REINDEX`` is parsed as a bare ``Column`` and would otherwise
    # look like a valid single-column SELECT.
    if not _TOP_LEVEL_PATTERN.match(sql):
        _reject(
            "not_select_top_level",
            "only SELECT (or WITH … SELECT) statements are allowed",
        )

    # Belt-and-suspenders: sqlglot silently swallows a trailing
    # ``;`` as part of a single statement. The pre-check above
    # caught ``SELECT 1; SELECT 2`` via the multi-statement path,
    # but a lone ``SELECT 1;`` would slip through. Reject any bare
    # ``;`` so the explorer stays a strict single-statement sandbox.
    if _BARE_SEMICOLON_RE.search(sql):
        _reject("bare_semicolon", "statement separator ';' is not allowed")

    try:
        statements = sqlglot.parse(sql, read=dialect)
    except ParseError as exc:
        _reject("parse_error", f"unparseable SQL: {exc}")

    if not statements:
        _reject("empty", "empty SQL")
    if len(statements) > 1:
        _reject(
            "multi_stmt",
            f"multiple statements not allowed ({len(statements)} found)",
        )

    stmt = statements[0]
    if stmt is None:
        # sqlglot only returns None entries for blank / pure-comment
        # inputs; the pre-checks above already ruled those out.
        _reject("empty", "empty SQL")
    # Walk BEFORE the top-level isinstance check so CTE-wrapped DML
    # (``WITH del AS (DELETE …) SELECT * FROM del``) is caught even
    # though the outer node is a Select.
    _walk_for_forbidden(cast(exp.Expression, stmt))
    # Union is a read-only set operation between two Selects; allow
    # it. Everything else (Command, Describe, Use, …) is rejected
    # here on top of the per-node walk above.
    if not isinstance(stmt, (exp.Select, exp.Union)):
        _reject(
            "not_select_ast",
            f"only SELECT statements are allowed (got {type(stmt).__name__})",
        )


def is_safe_identifier(name: str) -> bool:
    """True iff ``name`` is a single plain SQL identifier.

    Rejects empty, leading-digit, and any character outside
    ``[A-Za-z0-9_]`` (so quoted strings, backticks, dots, semicolons,
    comments, whitespace — all rejected).
    """
    return bool(name) and _IDENTIFIER_RE.fullmatch(name) is not None


def is_safe_qualified_identifier(name: str) -> bool:
    """True iff ``name`` is a dotted chain of safe identifiers
    (e.g. ``schema.table`` or ``db.schema.table.column``). Each
    segment must independently be a safe identifier; depth is
    bounded to :data:`_MAX_QUALIFIED_DEPTH`.
    """
    if not name:
        return False
    parts = name.split(".")
    if not 1 <= len(parts) <= _MAX_QUALIFIED_DEPTH:
        return False
    return all(is_safe_identifier(p) for p in parts)


def is_safe_select_expression(expr: str) -> bool:
    """True iff ``expr`` is a safe SELECT-list entry.

    Accepts ``*``, plain columns, qualified columns, common function
    calls (with their string/numeric arguments), arithmetic, and
    string concatenation. Rejects anything with a statement
    separator, a SQL comment, a quoted/backticked identifier, or a
    forbidden AST node anywhere in the expression.
    """
    if not expr or not expr.strip():
        return False
    s = expr.strip()
    if s == "*":
        return True
    if any(pat in s for pat in _FIELD_BAD_PATTERNS):
        return False
    # Wrap in a synthetic SELECT so comma-separated lists parse as a
    # single projection and the AST walker has a Select as parent.
    # Catch both ParseError AND TokenError — adversarial inputs (a stray
    # quote, NUL byte, unbalanced bracket) raise TokenError from the
    # tokenizer before the parser even runs. Treat any parse failure as
    # "not a safe expression" rather than letting it propagate to the
    # caller as a 500.
    try:
        parsed = sqlglot.parse_one(f"SELECT {s}", read=None)
    except (ParseError, TokenError):
        return False
    if parsed is None:
        return False
    parsed_expr = cast(exp.Expression, parsed)
    if _has_quoted_identifier(parsed_expr):
        return False
    try:
        _walk_for_forbidden(parsed_expr)
    except UnsafeSQLError:
        return False
    return True


def build_safe_where_clause(
    field: str,
    operator: str,
    value: Any,
    params: dict[str, Any],
    *,
    param_index: int,
    param_prefix: str = "p",
) -> tuple[str, int]:
    """Build a single WHERE fragment with a whitelisted operator.

    The ``operator`` argument is matched against
    :data:`ALLOWED_WHERE_OPERATORS`; anything else is rejected. The
    value is **never** interpolated into the SQL text — it is
    stored in ``params`` under ``{param_prefix}{param_index}`` (and
    subsequent indices) for binding at execution time.

    Args:
        field: column name; must pass :func:`is_safe_identifier`.
        operator: one of :data:`ALLOWED_WHERE_OPERATORS`.
        value: the right-hand side; list for ``IN``/``BETWEEN``,
            otherwise scalar (``None`` only for ``IS NULL``/``IS NOT NULL``).
        params: mutated in place with new bind values.
        param_index: the next free slot in the caller's param namespace.
        param_prefix: prefix for the generated bind names.

    Returns:
        ``(fragment, next_param_index)`` so the caller can chain
        multiple conditions in a loop.
    """
    if not is_safe_identifier(field):
        _reject("invalid_field", f"invalid field name: {field!r}")
    op_upper = operator.strip().upper()
    if op_upper not in ALLOWED_WHERE_OPERATORS:
        _reject("invalid_operator", f"operator not allowed: {operator!r}")

    if op_upper in ("IS NULL", "IS NOT NULL"):
        return f"{field} {op_upper}", param_index

    if op_upper in ("IN", "NOT IN"):
        if not isinstance(value, list) or not value:
            _reject("in_requires_list", f"{op_upper} requires a non-empty list")
        names: list[str] = []
        for v in value:
            name = f"{param_prefix}{param_index}"
            params[name] = v
            names.append(f":{name}")
            param_index += 1
        return f"{field} {op_upper} ({', '.join(names)})", param_index

    if op_upper in ("BETWEEN", "NOT BETWEEN"):
        if not isinstance(value, list) or len(value) != 2:
            _reject("between_requires_pair", f"{op_upper} requires a 2-element list")
        lo = f"{param_prefix}{param_index}"
        params[lo] = value[0]
        param_index += 1
        hi = f"{param_prefix}{param_index}"
        params[hi] = value[1]
        param_index += 1
        return f"{field} {op_upper} :{lo} AND :{hi}", param_index

    name = f"{param_prefix}{param_index}"
    params[name] = value
    param_index += 1
    return f"{field} {op_upper} :{name}", param_index


def substitute_parameters(
    sql: str, parameters: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Replace ``{key}`` placeholders in ``sql`` with ``:key`` binds.

    The values from ``parameters`` are returned in a params dict for
    later binding — they are never interpolated into the SQL text.
    The substituted SQL is then validated as a single safe SELECT
    so a custom_sql template that hides DML behind a parameter is
    rejected at substitution time.

    Placeholders without a matching key are left as literal ``{key}``
    text; the subsequent ``validate_select_only`` call will reject
    any input that ends up syntactically invalid.
    """
    if not sql:
        _reject("empty", "empty SQL")
    out = sql
    query_params: dict[str, Any] = {}
    for key, value in parameters.items():
        placeholder = "{" + str(key) + "}"
        if placeholder in out:
            out = out.replace(placeholder, f":{key}")
            query_params[key] = value
    validate_select_only(out)
    return out, query_params

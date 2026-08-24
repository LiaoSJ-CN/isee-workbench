"""Tests for app.services.sql_validator.

Covers the round-2 audit's Phase 2 (SQL injection consolidation):
PY-1, PY-3, PY-11, PY-13, SEC-2, SEC-9, SEC-16.

The module replaces the previous regex/keyword-list defenses in
``routers/explorer.is_safe_sql`` and the per-line regexes in
``services.report_generator.build_query``. All validation is now
delegated to ``sqlglot.parse`` so we lean on a real AST rather than
textual heuristics that the old comment-stripping approach missed.
"""

from __future__ import annotations

import pytest

from app.services.sql_validator import (
    ALLOWED_WHERE_OPERATORS,
    UnsafeSQLError,
    build_safe_where_clause,
    is_safe_identifier,
    is_safe_qualified_identifier,
    is_safe_select_expression,
    substitute_parameters,
    validate_select_only,
)

# ============================================================
# validate_select_only — happy path
# ============================================================


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT * FROM users",
        "SELECT id, name FROM users WHERE active = TRUE",
        "SELECT u.id FROM users u WHERE u.id > 10 ORDER BY u.id LIMIT 5",
        "SELECT COUNT(*), SUM(amount) FROM orders GROUP BY region",
        "SELECT a.id FROM a JOIN b ON a.id = b.a_id",
        "SELECT id FROM (SELECT id FROM users) sub",
        # SELECT-only CTE — legitimate
        "WITH active AS (SELECT * FROM users WHERE active) SELECT * FROM active",
        # Comments in the middle are inert, not a bypass
        "SELECT 1 /* harmless */ FROM dual",
        "SELECT 1 -- trailing comment\nFROM dual",
        # UNION of two SELECTs is still SELECT-only at the top level
        "SELECT id FROM a UNION ALL SELECT id FROM b",
    ],
)
def test_validate_select_only_accepts_pure_select(sql: str) -> None:
    validate_select_only(sql)  # must not raise


# ============================================================
# validate_select_only — DDL/DML rejection (was the FORBIDDEN_KEYWORDS list)
# ============================================================


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE users",
        "DELETE FROM users",
        "INSERT INTO users VALUES (1)",
        "UPDATE users SET name = 'x'",
        "CREATE TABLE x (a int)",
        "ALTER TABLE x ADD COLUMN b int",
        "TRUNCATE users",
        "GRANT ALL ON x TO public",
        "REVOKE ALL ON x FROM public",
    ],
)
def test_validate_select_only_rejects_ddl_dml(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select_only(sql)


# ============================================================
# SEC-9 — missing primitives (COPY, CALL, SET, PRAGMA, LOCK, VACUUM, REINDEX)
# ============================================================


@pytest.mark.parametrize(
    "sql",
    [
        # PostgreSQL bulk load
        "COPY users FROM '/tmp/x.csv'",
        # Stored-procedure invocation
        "CALL my_procedure(1)",
        # Session config (e.g. `SET search_path = pg_catalog`)
        "SET search_path = public",
        # SQLite introspection / config
        "PRAGMA table_info(users)",
        "PRAGMA writable_schema = 1",
        # Advisory lock statement
        "LOCK TABLE users IN ACCESS EXCLUSIVE MODE",
        # SQLite maintenance
        "VACUUM",
        "REINDEX",
        # MERGE (upsert) is a write
        "MERGE INTO users USING (SELECT 1 AS id) s "
        "ON users.id = s.id WHEN MATCHED THEN UPDATE SET name = 1",
    ],
)
def test_validate_select_only_rejects_admin_primitives(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select_only(sql)


# ============================================================
# Stacked statements & non-SELECT prefix
# ============================================================


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "SELECT 1;",
        "SELECT 1; -- anything",
        "SELECT 1;--DROP TABLE x",
        # Comments hiding a trailing semicolon
        "SELECT 1 /* ; */; SELECT 2",
        # Non-SELECT top-level
        "WITH del AS (DELETE FROM users RETURNING *) SELECT * FROM del",
        "EXPLAIN ANALYZE DELETE FROM users",
        "SHOW TABLES",
        "DESCRIBE users",
        "USE other_db",
    ],
)
def test_validate_select_only_rejects_multi_or_non_select(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select_only(sql)


# ============================================================
# PY-1 — WITH/CTE that contains DML is rejected (recursive check)
# ============================================================


@pytest.mark.parametrize(
    "sql",
    [
        # PostgreSQL data-modifying CTE
        "WITH del AS (DELETE FROM users RETURNING id) SELECT id FROM del",
        "WITH ins AS (INSERT INTO log(ts) VALUES (now()) RETURNING 1) SELECT * FROM ins",
        "WITH upd AS (UPDATE users SET active = TRUE RETURNING id) SELECT * FROM upd",
        # Nested inside a subquery
        "SELECT * FROM (WITH del AS (DELETE FROM users RETURNING id) SELECT * FROM del) sub",
    ],
)
def test_validate_select_only_rejects_dml_in_cte(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select_only(sql)


# ============================================================
# PY-11 — dollar-quote / backtick / comment-bypass
#
# The old comment-stripping regex let a payload like
#   SELECT * FROM x WHERE name = '$$' OR 1=1 -- '
# pass the keyword check (after -- was stripped) and reach the engine.
# With sqlglot, the parser sees the OR as a real clause and the
# resulting query is a tautology. The new module still rejects any
# text containing statement separators, comment markers, or non-SELECT
# shapes, so a hand-crafted OR-1=1 payload via a comment is caught.
# ============================================================


@pytest.mark.parametrize(
    "sql",
    [
        # Comment-hiding-the-rest trick. The old validator would
        # strip the `-- '` then look for DML keywords — none, so it
        # passed. The new validator parses the AST and rejects any
        # input with statement separators / trailing junk.
        "SELECT 1; DROP TABLE x -- ",
        # MySQL backtick identifier wrapping a statement
        "SELECT `id` FROM users WHERE `id` = 1; DROP TABLE x",
    ],
)
def test_validate_select_only_rejects_comment_bypass(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select_only(sql)


# ============================================================
# Parse errors
# ============================================================


@pytest.mark.parametrize(
    "sql",
    [
        "SELEC 1",  # typo
        "SELECT FROM",  # missing target
        "((",  # unmatched
    ],
)
def test_validate_select_only_rejects_unparseable(sql: str) -> None:
    with pytest.raises(UnsafeSQLError):
        validate_select_only(sql)


# ============================================================
# is_safe_identifier / is_safe_qualified_identifier
# ============================================================


@pytest.mark.parametrize("name", ["users", "_users", "user_id", "camelCase", "T1", "_"])
def test_is_safe_identifier_accepts_plain(name: str) -> None:
    assert is_safe_identifier(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "",
        "1users",  # leading digit
        "users;",
        "users--",
        "users.name",  # dot is not allowed in single identifier
        "'users'",  # quoted string
        '"users"',  # quoted identifier
        "`users`",  # MySQL backtick
        "users OR 1=1",
        "users;DROP",
        "users\nFROM",
    ],
)
def test_is_safe_identifier_rejects_unsafe(name: str) -> None:
    assert is_safe_identifier(name) is False, f"expected unsafe: {name!r}"


@pytest.mark.parametrize(
    "name",
    [
        "users",
        "public.users",
        "db.schema.public.users",
    ],
)
def test_is_safe_qualified_identifier_accepts_dotted(name: str) -> None:
    assert is_safe_qualified_identifier(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "",
        "1.users",
        "public..users",
        "public.users;",
        '"public".users',
        "public.`users`",
        "public.users--",
    ],
)
def test_is_safe_qualified_identifier_rejects_unsafe(name: str) -> None:
    assert is_safe_qualified_identifier(name) is False


# ============================================================
# is_safe_select_expression
# ============================================================


@pytest.mark.parametrize(
    "expr",
    [
        "*",
        "users.id",
        "id",
        "SUM(amount)",
        "SUM(amount) AS total",
        "COUNT(DISTINCT user_id)",
        "COALESCE(name, 'unknown')",
        "amount * 1.1",
        "a + b - c",
        "name || ' suffix'",
    ],
)
def test_is_safe_select_expression_accepts(expr: str) -> None:
    assert is_safe_select_expression(expr) is True


@pytest.mark.parametrize(
    "expr",
    [
        # Statement separator inside the field
        "id; DROP TABLE x",
        # SQL comment hiding trailing junk
        "id -- comment",
        "id /* comment */",
        # Quoted identifier wrapping a statement
        "`id`",
        # DML inside an "expression"
        "(DELETE FROM x RETURNING id) AS sub",
    ],
)
def test_is_safe_select_expression_rejects(expr: str) -> None:
    assert is_safe_select_expression(expr) is False, f"expected unsafe: {expr!r}"


# ============================================================
# build_safe_where_clause — operator whitelist (SEC-16)
# ============================================================


def test_build_safe_where_clause_eq_binds_value() -> None:
    params: dict = {}
    frag, next_idx = build_safe_where_clause("age", "=", 18, params, param_index=0)
    assert frag == "age = :p0"
    assert next_idx == 1
    assert params == {"p0": 18}


def test_build_safe_where_clause_string_value_binds() -> None:
    params: dict = {}
    frag, _ = build_safe_where_clause("name", "LIKE", "%foo%", params, param_index=0)
    assert frag == "name LIKE :p0"
    assert params == {"p0": "%foo%"}


def test_build_safe_where_clause_in_list_expands_params() -> None:
    params: dict = {}
    frag, next_idx = build_safe_where_clause("id", "IN", [1, 2, 3], params, param_index=0)
    assert frag == "id IN (:p0, :p1, :p2)"
    assert next_idx == 3
    assert params == {"p0": 1, "p1": 2, "p2": 3}


def test_build_safe_where_clause_is_null_emits_no_param() -> None:
    params: dict = {}
    frag, next_idx = build_safe_where_clause("deleted_at", "IS NULL", None, params, param_index=0)
    assert frag == "deleted_at IS NULL"
    assert params == {}
    assert next_idx == 0


def test_build_safe_where_clause_is_not_null_emits_no_param() -> None:
    params: dict = {}
    frag, _ = build_safe_where_clause("email", "IS NOT NULL", None, params, param_index=0)
    assert frag == "email IS NOT NULL"
    assert params == {}


def test_build_safe_where_clause_between_binds_two_params() -> None:
    params: dict = {}
    frag, next_idx = build_safe_where_clause("amount", "BETWEEN", [10, 100], params, param_index=0)
    assert frag == "amount BETWEEN :p0 AND :p1"
    assert next_idx == 2
    assert params == {"p0": 10, "p1": 100}


def test_build_safe_where_clause_param_index_increments_across_calls() -> None:
    params: dict = {}
    _, idx = build_safe_where_clause("a", "=", 1, params, param_index=0, param_prefix="w")
    _, idx = build_safe_where_clause("b", ">", 2, params, param_index=idx, param_prefix="w")
    assert params == {"w0": 1, "w1": 2}
    assert idx == 2


def test_build_safe_where_clause_rejects_bad_operator() -> None:
    # The whole point of SEC-16: the old code interpolated `operator`
    # raw into the WHERE fragment, so an attacker could send
    # `{"operator": "OR 1=1"}` and inject.
    for bad in [
        "OR 1=1",
        "; DROP TABLE x",
        "REGEXP",
    ]:
        with pytest.raises(UnsafeSQLError):
            build_safe_where_clause("id", bad, 1, {}, param_index=0)


def test_build_safe_where_clause_rejects_bad_field() -> None:
    with pytest.raises(UnsafeSQLError):
        build_safe_where_clause("id; DROP TABLE x", "=", 1, {}, param_index=0)


def test_build_safe_where_clause_rejects_in_with_non_list() -> None:
    with pytest.raises(UnsafeSQLError):
        build_safe_where_clause("id", "IN", "1,2,3", {}, param_index=0)


def test_build_safe_where_clause_rejects_between_with_wrong_arity() -> None:
    with pytest.raises(UnsafeSQLError):
        build_safe_where_clause("amount", "BETWEEN", [10], {}, param_index=0)


def test_allowed_where_operators_is_a_frozenset() -> None:
    assert isinstance(ALLOWED_WHERE_OPERATORS, frozenset)
    # Smoke check: well-known operators must be in the whitelist, well-known
    # injection patterns must not.
    assert "=" in ALLOWED_WHERE_OPERATORS
    assert "LIKE" in ALLOWED_WHERE_OPERATORS
    assert "IS NULL" in ALLOWED_WHERE_OPERATORS
    assert "BETWEEN" in ALLOWED_WHERE_OPERATORS
    assert "OR" not in ALLOWED_WHERE_OPERATORS
    assert "UNION" not in ALLOWED_WHERE_OPERATORS


# ============================================================
# substitute_parameters — {key} → :key, then validate
# ============================================================


def test_substitute_parameters_replaces_braces_with_bind() -> None:
    sql, params = substitute_parameters("SELECT * FROM users WHERE id = {user_id}", {"user_id": 5})
    assert sql == "SELECT * FROM users WHERE id = :user_id"
    assert params == {"user_id": 5}


def test_substitute_parameters_leaves_unbound_placeholders_literal() -> None:
    # The template uses {name} but `parameters` doesn't supply it;
    # the placeholder stays as a literal and validate_select_only is
    # the safety net.
    sql, params = substitute_parameters("SELECT * FROM users WHERE name = {name}", {})
    assert sql == "SELECT * FROM users WHERE name = {name}"
    assert params == {}


def test_substitute_parameters_validates_result() -> None:
    # Even a "custom" template that looks like a SELECT must be
    # validated after substitution. A custom_sql template that hides
    # a DML after a {param} should be rejected.
    with pytest.raises(UnsafeSQLError):
        substitute_parameters("SELECT 1; DROP TABLE x; -- {x}", {"x": 1})


def test_substitute_parameters_rejects_template_with_dml_cte() -> None:
    with pytest.raises(UnsafeSQLError):
        substitute_parameters(
            "WITH d AS (DELETE FROM users RETURNING id) SELECT * FROM d",
            {},
        )


def test_substitute_parameters_pure_select_passes() -> None:
    sql, params = substitute_parameters(
        "SELECT * FROM users WHERE id = {uid} AND active = {flag}",
        {"uid": 1, "flag": True},
    )
    assert sql == "SELECT * FROM users WHERE id = :uid AND active = :flag"
    assert params == {"uid": 1, "flag": True}

"""Property-based fuzz tests for ``app.services.sql_validator``.

The validator is the security boundary between user-supplied SQL and the
data warehouse. The regex + AST checks must never:

1. Raise an unexpected exception type (e.g. ``RecursionError`` from
   deeply nested parentheses, ``KeyError`` from a malformed token, etc.)
2. Accept a string that contains forbidden SQL constructs (DML/DDL,
   statement separator, SQL comment) when those constructs are checked
   by a specific rule.
3. Crash on adversarial unicode / whitespace / NUL byte input.

Hypothesis explores string spaces that example-based tests don't reach.
The main property the plan asks for is:

> "任意字符串要么 is_safe_sql 接受要么明确拒绝，永不抛非 UnsafeSQLError 的异常"

We assert that for every public function and for arbitrary input.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from app.services.sql_validator import (
    ALLOWED_WHERE_OPERATORS,
    UnsafeSQLError,
    is_safe_identifier,
    is_safe_qualified_identifier,
    is_safe_select_expression,
    substitute_parameters,
    validate_select_only,
)


# Strategies reused across tests.

# A "safe" identifier per the validator's regex.
_SAFE_ID = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,15}", fullmatch=True)

# Forbidden DDL/DML keyword prefix tokens. ``validate_select_only`` must
# reject anything that starts with these, regardless of what follows.
_FORBIDDEN_KEYWORDS = st.sampled_from(
    [
        "DROP", "DELETE", "UPDATE", "INSERT", "TRUNCATE",
        "ALTER", "CREATE", "GRANT", "REVOKE", "COPY",
        "CALL", "SET", "PRAGMA", "LOCK", "VACUUM", "REINDEX",
    ]
)

# A tail that is "anything that looks like SQL suffix" — adversarial junk
# that we expect to be rejected by the per-token rule, not by the AST
# walker.
_SQL_TAIL = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=0,
    max_size=30,
)


# ----------------------------------------------------------------------------
# is_safe_identifier
# ----------------------------------------------------------------------------


@given(st.text(max_size=80))
def test_is_safe_identifier_never_raises(s: str) -> None:
    """For any string, returns bool without raising."""
    result = is_safe_identifier(s)
    assert isinstance(result, bool)


@given(st.text(max_size=80))
@settings(suppress_health_check=[HealthCheck.filter_too_much])
def test_is_safe_identifier_implies_regex_match(s: str) -> None:
    """If True, s must match the validator's identifier regex.

    Most random strings fail the regex (the validator is strict on
    purpose), so the assume() filter rejects almost every input. The
    check stays valuable because it confirms the validator never
    accepts a string that violates its own contract.
    """
    assume(is_safe_identifier(s))
    # First char: letter or underscore.
    assert s[0].isalpha() or s[0] == "_"
    # All chars: alphanumeric or underscore (no dot, no space, no ';', etc.).
    for c in s:
        assert c.isalnum() or c == "_"


@given(_SAFE_ID)
def test_is_safe_identifier_accepts_well_formed(s: str) -> None:
    """Sanity: well-formed identifiers are accepted."""
    assert is_safe_identifier(s) is True


# ----------------------------------------------------------------------------
# is_safe_qualified_identifier
# ----------------------------------------------------------------------------


@given(st.text(max_size=80))
def test_is_safe_qualified_identifier_never_raises(s: str) -> None:
    """For any string, returns bool without raising."""
    result = is_safe_qualified_identifier(s)
    assert isinstance(result, bool)


@given(st.lists(_SAFE_ID, min_size=1, max_size=3).map(".".join))
def test_is_safe_qualified_identifier_accepts_dotted(s: str) -> None:
    """Sanity: dotted safe identifiers are accepted (up to depth 8)."""
    assert is_safe_qualified_identifier(s) is True


# ----------------------------------------------------------------------------
# is_safe_select_expression
# ----------------------------------------------------------------------------


@given(st.text(max_size=80))
def test_is_safe_select_expression_never_raises(s: str) -> None:
    """For any string, returns bool — never raises (catches UnsafeSQLError)."""
    result = is_safe_select_expression(s)
    assert isinstance(result, bool)


@given(st.sampled_from(["*", "column", "table.column", "SUM(amount)"]))
def test_is_safe_select_expression_accepts_common(expr: str) -> None:
    """Sanity: common SELECT-list entries are accepted."""
    assert is_safe_select_expression(expr) is True


@given(st.sampled_from(["col; DROP TABLE x", "col--comment", "col/*c*/"]))
def test_is_safe_select_expression_rejects_injection(expr: str) -> None:
    """Sanity: injection-shaped expressions are rejected."""
    assert is_safe_select_expression(expr) is False


# ----------------------------------------------------------------------------
# validate_select_only — the main property
# ----------------------------------------------------------------------------


@given(st.text(max_size=120))
@settings(max_examples=50)
def test_validate_select_only_never_raises_non_unsafe_error(s: str) -> None:
    """The core plan property: any string returns None or raises
    ``UnsafeSQLError``; no other exception type may leak out.
    """
    try:
        result = validate_select_only(s)
        assert result is None
    except UnsafeSQLError:
        pass  # Expected rejection.
    except Exception as exc:  # noqa: BLE001 — intentional broad catch
        pytest.fail(
            f"validate_select_only raised unexpected exception "
            f"{type(exc).__name__}: {exc}"
        )


@given(_FORBIDDEN_KEYWORDS, _SQL_TAIL)
def test_validate_select_only_rejects_forbidden_keywords(kw: str, tail: str) -> None:
    """Anything starting with a forbidden DDL/DML/admin keyword is rejected."""
    sql = f"{kw} {tail}"
    with pytest.raises(UnsafeSQLError):
        validate_select_only(sql)


@given(st.sampled_from([
    "SELECT 1",
    "SELECT a, b FROM t",
    "WITH x AS (SELECT 1) SELECT * FROM x",
    "SELECT 1 UNION SELECT 2",
]))
def test_validate_select_only_accepts_basic_selects(sql: str) -> None:
    """Sanity: canonical safe SELECTs are accepted."""
    # validate_select_only returns None on success; no exception means OK.
    assert validate_select_only(sql) is None


# ----------------------------------------------------------------------------
# substitute_parameters
# ----------------------------------------------------------------------------


@given(
    st.text(max_size=80),
    st.dictionaries(
        keys=st.text(
            alphabet=st.characters(min_codepoint=97, max_codepoint=122),
            min_size=1,
            max_size=8,
        ),
        values=st.one_of(st.integers(), st.text(max_size=20), st.none()),
        max_size=3,
    ),
)
def test_substitute_parameters_never_raises_non_unsafe_error(
    sql: str, params: dict
) -> None:
    """For any (sql, params) input, returns a (str, dict) tuple OR raises
    ``UnsafeSQLError``; no other exception type may leak out.
    """
    try:
        out, bound = substitute_parameters(sql, params)
        assert isinstance(out, str)
        assert isinstance(bound, dict)
    except UnsafeSQLError:
        pass
    except Exception as exc:  # noqa: BLE001 — intentional broad catch
        pytest.fail(
            f"substitute_parameters raised unexpected exception "
            f"{type(exc).__name__}: {exc}"
        )


# ----------------------------------------------------------------------------
# ALLOWED_WHERE_OPERATORS — sanity
# ----------------------------------------------------------------------------


def test_allowed_where_operators_contains_core_set() -> None:
    """The whitelist must contain the operators the report builder emits."""
    must_have = {"=", "!=", "<", "<=", ">", ">=", "LIKE", "IN", "IS NULL"}
    assert must_have <= ALLOWED_WHERE_OPERATORS
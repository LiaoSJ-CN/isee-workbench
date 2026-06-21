"""ReportGenerator unit tests: build_query, _safe_filename, render_html.

These tests don't need a DB or a network — they construct lightweight
``SimpleNamespace`` stand-ins for the ORM models so the SQL-building
and HTML-rendering logic can be exercised in isolation.
"""

from types import SimpleNamespace

import pandas as pd
import pytest

from app.schemas.report import DisplayConfig
from app.services.report_generator import ReportGenerator, _safe_filename


# ---------- _safe_filename (path traversal prevention) ----------

@pytest.mark.parametrize(
    "raw,must_contain",
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system", "windows"),
        ("foo/bar", "foo"),
        ("财务经营月报", "财务经营月报"),
        ("name with spaces", "name_with_spaces"),
        ("....", "report"),
        ("", "report"),
        ("report.name", "report.name"),
        ("a$b%c@d", "a_b_c_d"),
    ],
)
def test_safe_filename_sanitizes(raw: str, must_contain: str) -> None:
    got = _safe_filename(raw)
    assert must_contain in got
    # No path separator or parent-dir marker in result.
    assert "/" not in got
    assert "\\" not in got
    assert ".." not in got.split("_")


def test_safe_filename_length_capped() -> None:
    raw = "a" * 500
    got = _safe_filename(raw)
    assert len(got) == 200


# ---------- DisplayConfig round-trip ----------
# Locks the snake_case contract. The frontend `DisplayConfig` TS interface
# mirrors these field names; if anyone flips Pydantic to populate_by_name=True
# or extra='allow', these assertions will fail instead of silently accepting
# camelCase keys (which used to drop chart toggles on save — see #12).

def test_display_config_accepts_snake_case_fields() -> None:
    cfg = DisplayConfig(
        show_legend=False,
        legend_position="bottom",
        show_grid=False,
        show_data_label=True,
    )
    assert cfg.show_legend is False
    assert cfg.legend_position == "bottom"
    assert cfg.show_grid is False
    assert cfg.show_data_label is True


def test_display_config_drops_unknown_camelcase_keys() -> None:
    # Pydantic default `extra='ignore'` should silently drop the camelCase
    # variants that the old (buggy) frontend form used to send. If this ever
    # stops being true, the rename in #12 has been undone and chart toggles
    # may regress — update the test alongside the rename.
    cfg = DisplayConfig.model_validate(
        {"showLegend": False, "legendPosition": "bottom", "showGrid": False}
    )
    # Defaults preserved — camelCase payload did NOT populate snake_case fields.
    assert cfg.show_legend is True
    assert cfg.legend_position == "top"
    assert cfg.show_grid is True


def test_display_config_extra_keys_present_in_model_dump() -> None:
    # Extra camelCase keys should be excluded from model_dump (round-trip),
    # so persisting + re-reading cannot resurrect them.
    cfg = DisplayConfig.model_validate({"showLegend": False, "show_legend": False})
    dumped = cfg.model_dump()
    assert "show_legend" in dumped
    assert "showLegend" not in dumped


# ---------- build_query ----------

def _gen() -> ReportGenerator:
    """Build a generator without touching DB / network."""
    g = ReportGenerator.__new__(ReportGenerator)
    g.data_source = None
    return g


def _item(**overrides) -> SimpleNamespace:
    base = dict(
        name="t",
        item_type="table",
        table_name="tbl",
        fields=["*"],
        where_conditions=[],
        group_by=[],
        order_by=[],
        limit=None,
        custom_sql=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_build_query_simple_select_all() -> None:
    g = _gen()
    sql, params = g.build_query(_item(), {})
    assert sql == "SELECT * FROM tbl"
    assert params == {}


def test_build_query_with_where_parameterized() -> None:
    g = _gen()
    item = _item(where_conditions=[{"field": "x", "operator": "=", "value": 7}])
    sql, params = g.build_query(item, {})
    assert sql == "SELECT * FROM tbl WHERE x = :p0"
    assert params == {"p0": 7}


def test_build_query_with_where_template_substitution() -> None:
    g = _gen()
    item = _item(where_conditions=[{"field": "x", "operator": "=", "value": "{y}"}])
    sql, params = g.build_query(item, {"y": 42})
    assert sql == "SELECT * FROM tbl WHERE x = :p0"
    assert params == {"p0": 42}


def test_build_query_in_clause() -> None:
    g = _gen()
    item = _item(where_conditions=[{"field": "x", "operator": "IN", "value": [1, 2, 3]}])
    sql, params = g.build_query(item, {})
    assert sql == "SELECT * FROM tbl WHERE x IN (:p0, :p1, :p2)"
    assert params == {"p0": 1, "p1": 2, "p2": 3}


def test_build_query_like_clause() -> None:
    g = _gen()
    item = _item(where_conditions=[{"field": "name", "operator": "LIKE", "value": "%foo%"}])
    sql, params = g.build_query(item, {})
    assert sql == "SELECT * FROM tbl WHERE name LIKE :p0"
    assert params == {"p0": "%foo%"}


def test_build_query_group_by_and_order_by() -> None:
    g = _gen()
    item = _item(
        fields=["region", "SUM(amount) AS total"],
        group_by=["region"],
        order_by=[{"field": "total", "direction": "DESC"}],
    )
    sql, _ = g.build_query(item, {})
    assert "GROUP BY region" in sql
    assert "ORDER BY total DESC" in sql


def test_build_query_rejects_missing_table_name() -> None:
    g = _gen()
    with pytest.raises(Exception):
        g.build_query(_item(table_name=""), {})


def test_build_query_rejects_sql_injection_in_table_name() -> None:
    g = _gen()
    with pytest.raises(Exception):
        g.build_query(_item(table_name="tbl; DROP TABLE users"), {})


def test_build_query_rejects_invalid_where_field() -> None:
    g = _gen()
    item = _item(where_conditions=[{"field": "x; DROP", "operator": "=", "value": 1}])
    with pytest.raises(Exception):
        g.build_query(item, {})


def test_build_query_limit_becomes_param() -> None:
    g = _gen()
    item = _item(limit=50)
    sql, params = g.build_query(item, {})
    assert "LIMIT :limit_param" in sql
    assert params["limit_param"] == 50


def test_build_query_custom_sql_uses_substitution() -> None:
    """custom_sql bypasses the validator but only for {param} substitution;
    caller is responsible for safety."""
    g = _gen()
    item = _item(custom_sql="SELECT * FROM t WHERE id = {uid}")
    sql, params = g.build_query(item, {"uid": 99})
    assert sql == "SELECT * FROM t WHERE id = 99"
    assert params == {}


# ---------- render_html (XSS escaping surface) ----------

SCRIPT_TAG = "<script>alert(1)</script>"
IMG_TAG = '<img src=x onerror=alert(2)>'
QUOTES = "<'\"&>"


def test_render_html_escapes_all_user_data() -> None:
    g = _gen()
    report = SimpleNamespace(
        name=f"Name {SCRIPT_TAG}",
        description=f"Desc {IMG_TAG}",
        items=[
            SimpleNamespace(
                name=f"Item {QUOTES}",
                item_type="table",
                table_name="dummy",
                display_config={
                    "title": f"Title {SCRIPT_TAG}",
                    "subtitle": f"Sub {IMG_TAG}",
                },
            ),
            SimpleNamespace(
                name=f"Metric {QUOTES}",
                item_type="metric",
                table_name="dummy",
                display_config={},
            ),
            SimpleNamespace(
                name=f"Text {QUOTES}",
                item_type="text",
                table_name="dummy",
                display_config={"content": f"<b>raw {SCRIPT_TAG}</b>"},
            ),
        ],
    )
    data = {
        f"Item {QUOTES}": pd.DataFrame({f"col{SCRIPT_TAG}": [f"v{QUOTES}", SCRIPT_TAG, 1, 2.5]}),
        f"Metric {QUOTES}": pd.DataFrame({f"mcol{IMG_TAG}": [42.0]}),
        f"Text {QUOTES}": pd.DataFrame(),
    }

    html = g.render_html(data, report)

    # Raw payloads must not appear anywhere in text contexts.
    assert SCRIPT_TAG not in html
    assert IMG_TAG not in html
    # Escaped forms must appear (proves escaping actually happened).
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html
    assert "&amp;" in html
    assert ("&quot;" in html) or ("&#x27;" in html)
    # <title> is escaped.
    assert "<title>Name &lt;script&gt;alert(1)&lt;/script&gt;</title>" in html


def test_render_html_text_block_escapes_content() -> None:
    g = _gen()
    report = SimpleNamespace(
        name="r",
        description=None,
        items=[
            SimpleNamespace(
                name="t",
                item_type="text",
                table_name="x",
                display_config={"content": SCRIPT_TAG},
            )
        ],
    )
    html = g.render_html({}, report)
    assert SCRIPT_TAG not in html
    assert "&lt;script&gt;" in html


def test_render_html_metric_card_renders_value() -> None:
    g = _gen()
    report = SimpleNamespace(
        name="r",
        description=None,
        items=[
            SimpleNamespace(
                name="m",
                item_type="metric",
                table_name="x",
                display_config={},
            )
        ],
    )
    data = {"m": pd.DataFrame({"count": [12345]})}
    html = g.render_html(data, report)
    assert "class='metric'" in html
    # np.int64 now goes through numbers.Integral and gets a thousands separator.
    assert "12,345" in html, f"expected 12,345 in:\n{html[:400]}"


def test_render_html_metric_card_formats_float64_with_separator() -> None:
    """np.float64 goes through numbers.Real and gets ',' + 2 decimals."""
    g = _gen()
    report = SimpleNamespace(
        name="r",
        description=None,
        items=[
            SimpleNamespace(
                name="m",
                item_type="metric",
                table_name="x",
                display_config={},
            )
        ],
    )
    data = {"m": pd.DataFrame({"rate": [1234.5]})}
    html = g.render_html(data, report)
    assert "1,234.50" in html, f"expected 1,234.50 in:\n{html[:400]}"


def test_render_html_table_renders_columns_and_rows() -> None:
    g = _gen()
    report = SimpleNamespace(
        name="r",
        description=None,
        items=[
            SimpleNamespace(
                name="tbl",
                item_type="table",
                table_name="x",
                display_config={"title": "My Table"},
            )
        ],
    )
    data = {"tbl": pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})}
    html = g.render_html(data, report)
    assert "<table>" in html
    assert "<th>a</th>" in html
    assert "<th>b</th>" in html
    assert "My Table" in html

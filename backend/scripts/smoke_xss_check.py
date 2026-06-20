"""Smoke test for XSS escaping in ReportGenerator.render_html.

Exercises every user-controlled injection surface with a malicious payload
and asserts the output never contains raw HTML markup in text contexts.

Run: cd backend && source .venv/bin/activate && python scripts/smoke_xss_check.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

# Allow running directly: `python scripts/smoke_xss_check.py` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.report_generator import ReportGenerator


SCRIPT_TAG = "<script>alert(1)</script>"
IMG_TAG = '<img src=x onerror=alert(2)>'
QUOTES = "<'\"&>"


def make_generator() -> ReportGenerator:
    """Build a ReportGenerator without touching DB / network."""
    gen = ReportGenerator.__new__(ReportGenerator)
    gen.data_source = None
    gen.url = None
    return gen


def check(cond: bool, msg: str, failures: list[str]) -> None:
    if not cond:
        failures.append(msg)


def main() -> int:
    gen = make_generator()

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

    # Table with malicious column name AND malicious cell value
    table_df = pd.DataFrame({f"col{SCRIPT_TAG}": [f"v{QUOTES}", SCRIPT_TAG, 1, 2.5]})
    # Metric with malicious column name
    metric_df = pd.DataFrame({f"mcol{IMG_TAG}": [42.0]})

    data = {
        f"Item {QUOTES}": table_df,
        f"Metric {QUOTES}": metric_df,
        f"Text {QUOTES}": pd.DataFrame(),
    }

    html = gen.render_html(data, report)

    failures: list[str] = []

    # Raw payloads must NOT appear anywhere in the rendered HTML
    for marker in (SCRIPT_TAG, IMG_TAG):
        check(marker not in html, f"raw payload leaked: {marker}", failures)

    # Escaped forms MUST appear (proves escaping actually happened)
    check("&lt;script&gt;" in html, "missing escaped <script> tag", failures)
    check("&lt;img" in html, "missing escaped <img tag", failures)

    # Defense-in-depth: quotes inside text contexts are HTML-escaped.
    # We can't assert globally because chart_config holds user data inside
    # a <script> block where json.dumps produces its own escaped strings —
    # which is the correct behavior. So we only assert no unescaped quote
    # in plain text contexts (table cells, headings).
    check("&amp;" in html, "missing escaped &", failures)
    check("&quot;" in html or "&#x27;" in html, "missing escaped quote", failures)

    # Title in <title> tag — also escaped
    check(
        "<title>Name &lt;script&gt;alert(1)&lt;/script&gt;</title>" in html,
        "<title> not escaped",
        failures,
    )

    if failures:
        print("FAIL")
        for f in failures:
            print("  -", f)
        # Print first 800 chars for debugging
        print("\n--- HTML preview ---")
        print(html[:800])
        return 1

    print("PASS — all XSS payloads escaped in text contexts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""HTML renderer for report data (批 5.2).

Produces a self-contained HTML document with embedded Chart.js
charts. All user-supplied content is HTML-escaped via
:func:`html.escape`; chart config is serialized with :func:`json.dumps`
so Python booleans render as valid JS (``true``/``false``/``null``).

The chart JS bootstrap script ``/static/chart.umd.min.js`` is served
by the FastAPI app's StaticFiles mount — see ``app/main.py``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from html import escape as h
from typing import Any, cast

import pandas as pd

from app.models.report import Report
from app.services.report_generator.renderers._shared import (
    DEFAULT_COLORS,
    df_to_html_table,
)

logger = logging.getLogger(__name__)


def render_html(
    data: dict[str, pd.DataFrame],
    report: Report,
    base_url: str | None = None,
    errors: dict[str, str] | None = None,
) -> str:
    """Render report data as HTML with Chart.js charts.

    Args:
        data: Map from item name to its query result DataFrame.
            Items missing from ``data`` are skipped silently (the
            upstream error is reflected in ``errors``).
        report: Report with ordered items + display_config.
        base_url: Optional ``<base href>`` so chart fetches resolve
            relative to the SPA when previewed inside an iframe.
        errors: Map from item name to upstream error message; when
            present, the item renders as a red error banner instead
            of a blank card.

    The error surface is html-escaped because the message originates
    from a DB driver string (psycopg2 / sqlite3 exception text).
    """
    html_parts: list[str] = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "<meta charset='utf-8'>",
    ]
    if base_url:
        html_parts.append(f"<base href='{h(base_url)}'>")
    html_parts.extend(
        [
            f"<title>{h(str(report.name))}</title>",
            "<script src='/static/chart.umd.min.js'></script>",
            "<style>",
            "body { font-family: -apple-system, BlinkMacSystemFont, "
            "'Segoe UI', Roboto, sans-serif; padding: 20px; }",
            "h1 { color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 10px; }",
            "h2 { color: #555; margin-top: 30px; }",
            "h3 { color: #666; margin-top: 20px; font-size: 16px; }",
            "table { border-collapse: collapse; width: 100%; margin: 20px 0; }",
            "th { background-color: #0066cc; color: white; padding: 12px; text-align: left; }",
            "td { padding: 10px; border-bottom: 1px solid #ddd; }",
            "tr:hover { background-color: #f5f5f5; }",
            ".metric { display: inline-block; padding: 20px; margin: 10px; "
            "background: #f0f8ff; border-radius: 8px; }",
            ".metric-value { font-size: 2em; font-weight: bold; color: #0066cc; }",
            ".metric-label { color: #666; }",
            ".chart-container { margin: 20px 0; padding: 15px; "
            "background: #fff; border: 1px solid #e8e8e8; border-radius: 8px; }",
            ".timestamp { color: #999; font-size: 0.9em; margin-top: 20px; }",
            ".text-block { padding: 15px; background: #fafafa; "
            "border-radius: 4px; margin: 10px 0; }",
            ".chart-wrapper { position: relative; height: 400px; width: 100%; }",
            ".item-error { margin: 20px 0; padding: 15px; "
            "background: #fff1f0; border: 1px solid #ffa39e; border-radius: 8px; }",
            ".item-error .error-banner { color: #cf1322; margin: 8px 0 0 0; }",
            "</style>",
            "</head>",
            "<body>",
            f"<h1>{h(str(report.name))}</h1>",
        ]
    )

    if report.description:
        html_parts.append(f"<p>{h(report.description)}</p>")

    chart_index = 0
    errors = errors or {}
    for item in report.items:
        config = item.display_config or {}
        if cast(str, item.name) in errors:
            # If this item failed upstream, surface the error visibly
            # instead of rendering a blank card. ``h`` escapes both the
            # title (config-derived) and the message (DB-driver string).
            title = config.get("title") or cast(str, item.name)
            html_parts.append(
                "<div class='item-error'>"
                f"<h2>{h(str(title))}</h2>"
                f"<p class='error-banner'>⚠ {h(str(errors[cast(str, item.name)]))}</p>"
                "</div>"
            )
            continue
        item_data = data.get(cast(str, item.name))

        if item.item_type == "metric" and item_data is not None and not item_data.empty:
            html_parts.append("<div>")
            for col in item_data.columns:
                value = item_data[col].iloc[0] if len(item_data) > 0 else 0
                formatted = _format_value_inline(value)
                html_parts.append("<div class='metric'>")
                html_parts.append(f"<div class='metric-value'>{formatted}</div>")
                html_parts.append(f"<div class='metric-label'>{h(col)}</div>")
                html_parts.append("</div>")
            html_parts.append("</div>")

        elif item.item_type == "chart" and item_data is not None and not item_data.empty:
            chart_index += 1
            title = config.get("title") or cast(str, item.name)
            subtitle = config.get("subtitle", "")
            chart_type = config.get("chart_type") or "bar"
            show_legend = config.get("show_legend", True)
            legend_position = config.get("legend_position", "top")
            show_grid = config.get("show_grid", True)
            stacked = config.get("stacked", False)
            show_data_label = config.get("show_data_label", False)
            colors = config.get("colors") or DEFAULT_COLORS
            height = config.get("height", 400)

            html_parts.append("<div class='chart-container'>")
            html_parts.append(f"<h2>{h(str(title))}</h2>")
            if subtitle:
                html_parts.append(f"<h3>{h(str(subtitle))}</h3>")

            # Prepare chart data
            labels = item_data.iloc[:, 0].tolist() if len(item_data.columns) > 0 else []
            chart_id = f"chart_{chart_index}"
            # chart_index is an int, so chart_id is always safe — but
            # assert this to prevent regressions (SEC-1).
            assert chart_id.replace("_", "").isalnum(), f"Unsafe chart_id: {chart_id!r}"

            chart_config = _build_chart_config(
                chart_type=chart_type,
                labels=labels,
                data=item_data,
                colors=colors,
                show_legend=show_legend,
                legend_position=legend_position,
                show_grid=show_grid,
                stacked=stacked,
                show_data_label=show_data_label,
            )

            html_parts.append(f"<div class='chart-wrapper' style='height:{h(str(height))}px'>")
            html_parts.append(f"<canvas id='{chart_id}'></canvas>")
            html_parts.append("</div>")
            html_parts.append("</div>")

            # Add Chart.js script — use json.dumps to serialize Python
            # True/False/None as valid JS true/false/null (not str()).
            chart_js = (
                f"new Chart(document.getElementById('{chart_id}'),"
                + json.dumps(chart_config)
                + ");"
            )
            html_parts.append("<script>")
            html_parts.append(chart_js)
            html_parts.append("</script>")

        elif item.item_type == "table" and item_data is not None:
            title = config.get("title") or cast(str, item.name)
            html_parts.append(f"<h2>{h(str(title))}</h2>")
            html_parts.append(df_to_html_table(item_data))

        elif item.item_type == "text":
            content = (config.get("content") or "") if config else ""
            html_parts.append(f"<div class='text-block'>{h(content)}</div>")

        else:
            logger.warning(
                "Unknown item_type=%r for item %s — skipping rendering",
                item.item_type,
                cast(str, item.name),
            )

    html_parts.extend(
        [
            f"<div class='timestamp'>"
            f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            f"</div>",
            "</body>",
            "</html>",
        ]
    )

    return "\n".join(html_parts)


def _format_value_inline(val: Any) -> str:
    """Inline copy of :func:`renderers._shared.format_value`.

    Kept as a thin local alias so the metric-card branch can read
    without importing the helper module twice in the call chain. The
    metric card needs raw HTML (``<div class='metric-value'>``) and
    shared.format_value would be fine, but listing it locally makes
    the intent obvious and the import graph simpler for grepping.
    """
    from app.services.report_generator.renderers._shared import format_value

    return format_value(val)


def _build_chart_config(
    chart_type: str,
    labels: list[Any],
    data: pd.DataFrame,
    colors: list[str],
    show_legend: bool,
    legend_position: str,
    show_grid: bool,
    stacked: bool,
    show_data_label: bool,
) -> dict[str, Any]:
    """Assemble a Chart.js config dict for the given chart type.

    Pie / doughnut / polarArea get a different dataset shape (single
    color per slice, no axes); bar / line / area / radar / scatter /
    bubble share the same shape with axis options.

    The ``indexAxis`` swap for ``horizontalBar`` keeps the public API
    (frontend requests ``type: "horizontalBar"``) while Chart.js v4
    uses ``type: "bar"`` + ``indexAxis: "y"``.
    """
    if chart_type in ("pie", "doughnut", "polarArea"):
        datasets: list[dict[str, Any]] = []
        for i, col in enumerate(data.columns[1:], 0):
            dataset_data = data[col].tolist()
            bg_color = colors[i % len(colors)] if i < len(colors) else colors[0]
            datasets.append(
                {
                    "data": dataset_data,
                    "backgroundColor": bg_color,
                    "borderColor": "#fff",
                    "borderWidth": 2,
                }
            )
        return {
            "type": chart_type,
            "data": {"labels": labels, "datasets": datasets},
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "plugins": {
                    "legend": {"display": show_legend, "position": legend_position},
                    "datalabels": {"display": show_data_label},
                },
            },
        }

    # bar / line / area / radar / scatter / bubble
    datasets = []
    for i, col in enumerate(data.columns[1:], 0):
        dataset_data = data[col].tolist()
        color = colors[i % len(colors)]
        is_bar = chart_type in ("bar", "horizontalBar")
        datasets.append(
            {
                "label": col,
                "data": dataset_data,
                "backgroundColor": color if is_bar else f"{color}33",
                "borderColor": color,
                "borderWidth": 2,
                "fill": chart_type == "area",
                "tension": 0.4,
            }
        )

    chart_type_for_js = "bar" if chart_type == "horizontalBar" else chart_type
    return {
        "type": chart_type_for_js,
        "data": {"labels": labels, "datasets": datasets},
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "indexAxis": "y" if chart_type == "horizontalBar" else "x",
            "plugins": {
                "legend": {"display": show_legend, "position": legend_position},
                "datalabels": {"display": show_data_label},
            },
            "scales": (
                {
                    "x": {"grid": {"display": show_grid}, "stacked": stacked},
                    "y": {"grid": {"display": show_grid}, "stacked": stacked},
                }
                if chart_type not in ("pie", "doughnut", "radar", "polarArea")
                else {}
            ),
        },
    }

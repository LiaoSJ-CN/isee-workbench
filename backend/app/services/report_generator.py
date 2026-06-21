"""Service for generating business analysis reports."""

import numbers
import re
import threading
from datetime import datetime
from html import escape as h
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.data_source import DataSource
from app.models.report import Report, ReportItem
from app.services.connection import build_connection_url


class ReportGeneratorError(Exception):
    """Raised when report generation fails."""


# Filename-safe subset: word chars, CJK Unified Ideographs, hyphen, dot.
_SAFE_FILENAME_RE = re.compile(r"[^\w一-鿿\-.]+")
_FILENAME_MAX_LEN = 200


# Module-level engine cache, keyed by DataSource.id. Lets reports that share
# a DataSource reuse one pool of connections across calls, avoiding the
# TCP+auth handshake tax on every generation. Eviction is explicit: callers
# that mutate a DataSource row must call ``evict_engine`` so the next call
# rebuilds the engine with the new connection URL.
_engine_cache: dict[int, Engine] = {}
_engine_cache_lock = threading.Lock()


def _get_or_create_engine(data_source: DataSource) -> Engine:
    """Return the cached Engine for ``data_source``, building one on miss.

    Double-checked locking keeps the fast path (cache hit) lock-free while
    staying safe under concurrent first-time access. ``pool_pre_ping=True``
    on remote backends makes SQLAlchemy discard stale pooled connections
    (e.g. after the remote DB restarts) instead of failing the next query.
    """
    cached = _engine_cache.get(data_source.id)
    if cached is not None:
        return cached
    with _engine_cache_lock:
        cached = _engine_cache.get(data_source.id)
        if cached is not None:
            return cached
        url = build_connection_url(data_source)
        if data_source.db_type == "sqlite":
            engine = create_engine(url)
        else:
            engine = create_engine(
                url,
                connect_args={"connect_timeout": 30},
                pool_pre_ping=True,
            )
        _engine_cache[data_source.id] = engine
        return engine


def evict_engine(data_source_id: int) -> None:
    """Drop the cached engine for ``data_source_id`` and dispose its pool.

    Call this after updating or deleting a DataSource so the next call
    rebuilds the engine with the new connection URL.
    """
    with _engine_cache_lock:
        engine = _engine_cache.pop(data_source_id, None)
    if engine is not None:
        engine.dispose()


def _safe_filename(name: str, fallback: str = "report") -> str:
    """Sanitize a string for use as a filename component.

    Strips path separators and other unsafe characters to prevent
    path traversal (e.g. ``../../etc/passwd``). Keeps word chars,
    CJK ideographs, hyphen, and dot. Falls back to ``fallback`` when
    the result would be empty, and caps length to avoid OS limits.
    """
    sanitized = _SAFE_FILENAME_RE.sub("_", name).strip("._") or fallback
    return sanitized[:_FILENAME_MAX_LEN]


class ReportGenerator:
    """Generates reports from configured report definitions."""

    def __init__(self, data_source: DataSource):
        """Initialize the generator with a data source."""
        self.data_source = data_source

    def __enter__(self):
        """Get (or create) cached database engine for the data source."""
        self.engine = _get_or_create_engine(self.data_source)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Intentionally do not dispose — engine is cached for reuse.

        Connections stay in the pool for the next caller that hits the
        same DataSource. Call ``evict_engine(ds_id)`` when the underlying
        DataSource config changes.
        """

    def build_query(
        self, item: ReportItem, parameters: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Build SQL query from report item configuration with parameterized values.

        Returns:
            Tuple of (query_string, parameters_dict) for safe query execution.
        """
        if item.custom_sql:
            # Substitute parameters in custom SQL (only for parameter placeholders)
            sql = item.custom_sql
            for key, value in parameters.items():
                sql = sql.replace(f"{{{key}}}", str(value))
            return sql, {}

        # Auto-generate query from configuration
        table_name = item.table_name
        if not table_name:
            raise ReportGeneratorError(f"Report item '{item.name}' has no table_name defined")

        # Validate and sanitize table name (only alphanumeric and underscore)
        import re
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', table_name):
            raise ReportGeneratorError(f"Invalid table name: {table_name}")

        # Build SELECT clause (validate field names)
        # Allow: *, table.field, field, and expressions like SUM(x) as y
        fields = item.fields if item.fields else ["*"]
        validated_fields = []
        for f in fields:
            if f == "*":
                validated_fields.append(f)
            # Allow SQL expressions (functions, aliases, etc.) in SELECT clause
            elif re.match(r'^[a-zA-Z_][a-zA-Z0-9_.\s,()+-/*=<>!\'"]+$', f):
                validated_fields.append(f)
            else:
                raise ReportGeneratorError(f"Invalid field/expression in SELECT: {f}")
        select_clause = ", ".join(validated_fields)

        # Build WHERE clause with parameterized values
        where_parts = []
        params: dict[str, Any] = {}
        param_index = 0

        for cond in (item.where_conditions or []):
            field = cond.get("field") if isinstance(cond, dict) else cond.field
            operator = cond.get("operator") if isinstance(cond, dict) else cond.operator
            value = cond.get("value") if isinstance(cond, dict) else cond.value

            # Validate field name
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', field):
                raise ReportGeneratorError(f"Invalid field name: {field}")

            # Handle parameter substitution
            if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
                param_key = value[1:-1]
                value = parameters.get(param_key, value)

            if operator == "IN" and isinstance(value, list):
                in_params = []
                for v in value:
                    param_name = f"p{param_index}"
                    params[param_name] = v
                    in_params.append(f":{param_name}")
                    param_index += 1
                where_parts.append(f"{field} IN ({', '.join(in_params)})")
            elif operator == "IS NULL":
                where_parts.append(f"{field} IS NULL")
            elif operator == "IS NOT NULL":
                where_parts.append(f"{field} IS NOT NULL")
            elif operator == "LIKE":
                param_name = f"p{param_index}"
                params[param_name] = value
                where_parts.append(f"{field} LIKE :{param_name}")
                param_index += 1
            elif isinstance(value, str):
                param_name = f"p{param_index}"
                params[param_name] = value
                where_parts.append(f"{field} {operator} :{param_name}")
                param_index += 1
            elif value is None:
                where_parts.append(f"{field} {operator} NULL")
            else:
                param_name = f"p{param_index}"
                params[param_name] = value
                where_parts.append(f"{field} {operator} :{param_name}")
                param_index += 1

        # Build GROUP BY clause (validate field names)
        group_by_clause = ""
        if item.group_by:
            validated_group_by = []
            for f in item.group_by:
                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', f):
                    validated_group_by.append(f)
                else:
                    raise ReportGeneratorError(f"Invalid field name in GROUP BY: {f}")
            group_by_clause = f" GROUP BY {', '.join(validated_group_by)}"

        # Build ORDER BY clause (validate field names)
        order_by_parts = []
        for ob in (item.order_by or []):
            field = ob.get("field") if isinstance(ob, dict) else ob.field
            direction = ob.get("direction", "ASC") if isinstance(ob, dict) else ob.direction

            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', field):
                raise ReportGeneratorError(f"Invalid field name in ORDER BY: {field}")
            if direction.upper() not in ("ASC", "DESC"):
                direction = "ASC"
            order_by_parts.append(f"{field} {direction}")

        order_by_clause = f" ORDER BY {', '.join(order_by_parts)}" if order_by_parts else ""

        # Build LIMIT clause (validate integer)
        limit_clause = ""
        if item.limit:
            try:
                limit_val = int(item.limit)
                if limit_val > 0:
                    limit_clause = " LIMIT :limit_param"
                    params["limit_param"] = limit_val
            except (ValueError, TypeError):
                pass  # Skip invalid limit

        # Assemble query
        query = f"SELECT {select_clause} FROM {table_name}"
        if where_parts:
            query += " WHERE " + " AND ".join(where_parts)
        query += group_by_clause + order_by_clause + limit_clause

        return query, params

    def execute_query(self, query: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
        """Execute a SQL query with parameters and return results as DataFrame."""
        try:
            with self.engine.connect() as conn:
                if params:
                    df = pd.read_sql(text(query), conn, params=params)
                else:
                    df = pd.read_sql(text(query), conn)
            return df
        except SQLAlchemyError as exc:
            raise ReportGeneratorError(f"Query execution failed: {exc}") from exc

    def render_html(
        self,
        data: dict[str, pd.DataFrame],
        report: Report,
        base_url: str | None = None,
    ) -> str:
        """Render report data as HTML with Chart.js charts."""
        # Default colors for charts
        default_colors = [
            "#0066cc", "#52c41a", "#faad14", "#f5222d", "#722ed1",
            "#13c2c2", "#fa8c16", "#eb2f96", "#2f54eb", "#24bdbd",
        ]

        html_parts = [
            "<!DOCTYPE html>",
            "<html>",
            "<head>",
            "<meta charset='utf-8'>",
        ]
        if base_url:
            html_parts.append(f"<base href='{h(base_url)}'>")
        html_parts.extend([
            f"<title>{h(report.name)}</title>",
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
            "</style>",
            "</head>",
            "<body>",
            f"<h1>{h(report.name)}</h1>",
        ])

        if report.description:
            html_parts.append(f"<p>{h(report.description)}</p>")

        chart_index = 0
        # Render each item
        for item in report.items:
            item_data = data.get(item.name)
            config = item.display_config or {}

            if item.item_type == "metric" and item_data is not None and not item_data.empty:
                # Render as metric cards
                html_parts.append("<div>")
                for col in item_data.columns:
                    value = item_data[col].iloc[0] if len(item_data) > 0 else 0
                    formatted = self._format_value(value)
                    html_parts.append("<div class='metric'>")
                    html_parts.append(f"<div class='metric-value'>{formatted}</div>")
                    html_parts.append(f"<div class='metric-label'>{h(col)}</div>")
                    html_parts.append("</div>")
                html_parts.append("</div>")

            elif item.item_type == "chart" and item_data is not None and not item_data.empty:
                chart_index += 1
                title = config.get("title") or item.name
                subtitle = config.get("subtitle", "")
                chart_type = config.get("chart_type") or "bar"
                show_legend = config.get("show_legend", True)
                legend_position = config.get("legend_position", "top")
                show_grid = config.get("show_grid", True)
                stacked = config.get("stacked", False)
                show_data_label = config.get("show_data_label", False)
                colors = config.get("colors") or default_colors
                height = config.get("height", 400)

                html_parts.append("<div class='chart-container'>")
                html_parts.append(f"<h2>{h(title)}</h2>")
                if subtitle:
                    html_parts.append(f"<h3>{h(subtitle)}</h3>")

                # Prepare chart data
                labels = item_data.iloc[:, 0].tolist() if len(item_data.columns) > 0 else []
                chart_id = f"chart_{chart_index}"

                # Handle different chart types
                if chart_type in ("pie", "doughnut", "polarArea"):
                    # For pie/doughnut, use first column as labels, rest as data
                    datasets = []
                    for i, col in enumerate(item_data.columns[1:], 0):
                        dataset_data = item_data[col].tolist()
                        bg_color = colors[i % len(colors)] if i < len(colors) else colors[0]
                        datasets.append({
                            "data": dataset_data,
                            "backgroundColor": bg_color,
                            "borderColor": "#fff",
                            "borderWidth": 2,
                        })
                    chart_config = {
                        "type": chart_type,
                        "data": {
                            "labels": labels,
                            "datasets": datasets,
                        },
                        "options": {
                            "responsive": True,
                            "maintainAspectRatio": False,
                            "plugins": {
                                "legend": {"display": show_legend, "position": legend_position},
                                "datalabels": {"display": show_data_label},
                            },
                        },
                    }
                else:
                    # For bar, line, area, radar, scatter, bubble
                    datasets = []
                    for i, col in enumerate(item_data.columns[1:], 0):
                        dataset_data = item_data[col].tolist()
                        color = colors[i % len(colors)]
                        is_bar = chart_type in ("bar", "horizontalBar")
                        dataset = {
                            "label": col,
                            "data": dataset_data,
                            "backgroundColor": color if is_bar else f"{color}33",
                            "borderColor": color,
                            "borderWidth": 2,
                            "fill": chart_type == "area",
                            "tension": 0.4,
                        }
                        datasets.append(dataset)

                    chart_type_for_js = "bar" if chart_type == "horizontalBar" else chart_type
                    chart_config = {
                        "type": chart_type_for_js,
                        "data": {
                            "labels": labels,
                            "datasets": datasets,
                        },
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

                html_parts.append(f"<div class='chart-wrapper' style='height:{h(str(height))}px'>")
                html_parts.append(f"<canvas id='{chart_id}'></canvas>")
                html_parts.append("</div>")
                html_parts.append("</div>")

                # Add Chart.js script — use json.dumps to serialize Python
                # True/False/None as valid JS true/false/null (not str()).
                import json
                chart_js = (
                    f"new Chart(document.getElementById('{chart_id}'),"
                    + json.dumps(chart_config)
                    + ");"
                )
                html_parts.append("<script>")
                html_parts.append(chart_js)
                html_parts.append("</script>")

            elif item.item_type == "table" and item_data is not None:
                title = config.get("title") or item.name
                html_parts.append(f"<h2>{h(title)}</h2>")
                html_parts.append(self._df_to_html_table(item_data))

            elif item.item_type == "text":
                content = config.get("content", "") if config else ""
                html_parts.append(f"<div class='text-block'>{h(content)}</div>")

        html_parts.extend([
            f"<div class='timestamp'>"
            f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            f"</div>",
            "</body>",
            "</html>",
        ])

        return "\n".join(html_parts)

    def _df_to_html_table(self, df: pd.DataFrame, max_rows: int = 100) -> str:
        """Convert a DataFrame to an HTML table."""
        if df.empty:
            return "<p>No data available.</p>"

        # Limit rows for display
        display_df = df.head(max_rows)

        html = ["<table>"]

        # Header
        html.append("<thead><tr>")
        for col in display_df.columns:
            html.append(f"<th>{h(col)}</th>")
        html.append("</tr></thead>")

        # Body
        html.append("<tbody>")
        for _, row in display_df.iterrows():
            html.append("<tr>")
            for val in row:
                formatted = self._format_value(val)
                html.append(f"<td>{formatted}</td>")
            html.append("</tr>")
        html.append("</tbody>")

        html.append("</table>")

        if len(df) > max_rows:
            html.append(f"<p style='color:#666;'>Showing {max_rows} of {len(df)} rows</p>")

        return "".join(html)

    def _format_value(self, val: Any) -> str:
        """Format a value for display.

        Uses ``numbers.Integral``/``numbers.Real`` ABCs instead of ``int``/``float``
        so ``np.int64`` and ``np.float64`` (which lose their built-in inheritance
        in numpy >= 2.0) still get thousands separators and float precision.
        """
        if pd.isna(val):
            return ""
        # bool is a subclass of int; render as "True"/"False" instead of "1"/"0".
        if isinstance(val, bool):
            return str(val)
        if isinstance(val, numbers.Integral):
            return f"{int(val):,}"
        if isinstance(val, numbers.Real):
            return f"{float(val):,.2f}"
        return h(str(val))


def generate_report(
    report: Report,
    output_format: str,
    parameters: dict[str, Any],
    db: Session,
    preview_only: bool = False,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Generate a report and optionally save to file."""
    # Get data source
    data_source = db.query(DataSource).filter(DataSource.id == report.data_source_id).first()
    if not data_source:
        raise ReportGeneratorError("Data source not found for report")

    results = {}
    output_dir = Path(__file__).parent.parent.parent / "generated_reports"
    output_dir.mkdir(exist_ok=True)

    with ReportGenerator(data_source) as generator:
        for item in report.items:
            if item.item_type == "text":
                # Text items don't need data
                results[item.name] = pd.DataFrame()
                continue

            query, params = generator.build_query(item, parameters)
            try:
                df = generator.execute_query(query, params)
                results[item.name] = df
            except ReportGeneratorError:
                # If a query fails, continue with empty data
                results[item.name] = pd.DataFrame()

        if preview_only or output_format == "html":
            html_content = generator.render_html(results, report, base_url=base_url)
            if preview_only:
                return {"preview_data": html_content}
            else:
                # Save HTML file
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = output_dir / f"{_safe_filename(report.name)}_{timestamp}.html"
                filename.write_text(html_content, encoding="utf-8")
                return {"file_path": str(filename)}

        elif output_format == "excel":
            # Create Excel file with multiple sheets
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = output_dir / f"{_safe_filename(report.name)}_{timestamp}.xlsx"

            with pd.ExcelWriter(filename, engine="openpyxl") as writer:
                # Summary sheet
                summary_df = pd.DataFrame([
                    {"Report": report.name},
                    {"Description": report.description or ""},
                    {"Generated At": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                ])
                summary_df.to_excel(writer, sheet_name="Summary", index=False)

                # Data sheets
                for item_name, df in results.items():
                    if not df.empty:
                        # Clean sheet name
                        sheet_name = item_name[:31].replace("/", "_").replace("*", "")
                        df.to_excel(writer, sheet_name=sheet_name, index=False)

            return {"file_path": str(filename)}

    return {}

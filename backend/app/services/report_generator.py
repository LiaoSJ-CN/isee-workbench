"""Service for generating business analysis reports."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.data_source import DataSource
from app.models.report import Report, ReportItem
from app.services.connection import build_connection_url


class ReportGeneratorError(Exception):
    """Raised when report generation fails."""


class ReportGenerator:
    """Generates reports from configured report definitions."""

    def __init__(self, data_source: DataSource):
        """Initialize the generator with a data source."""
        self.data_source = data_source
        self.url = build_connection_url(data_source)

    def __enter__(self):
        """Create database engine."""
        if self.data_source.db_type == "sqlite":
            self.engine = create_engine(self.url)
        else:
            self.engine = create_engine(self.url, connect_args={"connect_timeout": 30})
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Dispose database engine."""
        if hasattr(self, "engine"):
            self.engine.dispose()

    def build_query(self, item: ReportItem, parameters: dict[str, Any]) -> str:
        """Build SQL query from report item configuration."""
        if item.custom_sql:
            # Substitute parameters in custom SQL
            sql = item.custom_sql
            for key, value in parameters.items():
                sql = sql.replace(f"{{{key}}}", str(value))
            return sql

        # Auto-generate query from configuration
        table_name = item.table_name
        if not table_name:
            raise ReportGeneratorError(f"Report item '{item.name}' has no table_name defined")

        # Build SELECT clause
        fields = item.fields if item.fields else ["*"]
        select_clause = ", ".join(fields)

        # Build WHERE clause
        where_parts = []
        for cond in (item.where_conditions or []):
            field = cond.get("field") if isinstance(cond, dict) else cond.field
            operator = cond.get("operator") if isinstance(cond, dict) else cond.operator
            value = cond.get("value") if isinstance(cond, dict) else cond.value

            # Handle parameter substitution
            if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
                param_key = value[1:-1]
                value = parameters.get(param_key, value)

            if operator == "IN" and isinstance(value, list):
                values_str = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in value)
                where_parts.append(f"{field} IN ({values_str})")
            elif operator == "IS NULL":
                where_parts.append(f"{field} IS NULL")
            elif operator == "IS NOT NULL":
                where_parts.append(f"{field} IS NOT NULL")
            elif operator == "LIKE":
                where_parts.append(f"{field} LIKE '{value}'")
            elif isinstance(value, str):
                where_parts.append(f"{field} {operator} '{value}'")
            elif value is None:
                where_parts.append(f"{field} {operator} NULL")
            else:
                where_parts.append(f"{field} {operator} {value}")

        # Build GROUP BY clause
        group_by_clause = ""
        if item.group_by:
            group_by_clause = f" GROUP BY {', '.join(item.group_by)}"

        # Build ORDER BY clause
        order_by_parts = []
        for ob in (item.order_by or []):
            field = ob.get("field") if isinstance(ob, dict) else ob.field
            direction = ob.get("direction", "ASC") if isinstance(ob, dict) else ob.direction
            order_by_parts.append(f"{field} {direction}")

        order_by_clause = f" ORDER BY {', '.join(order_by_parts)}" if order_by_parts else ""

        # Build LIMIT clause
        limit_clause = f" LIMIT {item.limit}" if item.limit else ""

        # Assemble query
        query = f"SELECT {select_clause} FROM {table_name}"
        if where_parts:
            query += " WHERE " + " AND ".join(where_parts)
        query += group_by_clause + order_by_clause + limit_clause

        return query

    def execute_query(self, query: str) -> pd.DataFrame:
        """Execute a SQL query and return results as DataFrame."""
        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(text(query), conn)
            return df
        except SQLAlchemyError as exc:
            raise ReportGeneratorError(f"Query execution failed: {exc}") from exc

    def render_html(self, data: dict[str, pd.DataFrame], report: Report) -> str:
        """Render report data as HTML."""
        html_parts = [
            "<!DOCTYPE html>",
            "<html>",
            "<head>",
            "<meta charset='utf-8'>",
            f"<title>{report.name}</title>",
            "<style>",
            "body { font-family: -apple-system, BlinkMacSystemFont, "
            "'Segoe UI', Roboto, sans-serif; padding: 20px; }",
            "h1 { color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 10px; }",
            "h2 { color: #555; margin-top: 30px; }",
            "table { border-collapse: collapse; width: 100%; margin: 20px 0; }",
            "th { background-color: #0066cc; color: white; padding: 12px; text-align: left; }",
            "td { padding: 10px; border-bottom: 1px solid #ddd; }",
            "tr:hover { background-color: #f5f5f5; }",
            ".metric { display: inline-block; padding: 20px; margin: 10px; "
            "background: #f0f8ff; border-radius: 8px; }",
            ".metric-value { font-size: 2em; font-weight: bold; color: #0066cc; }",
            ".metric-label { color: #666; }",
            ".timestamp { color: #999; font-size: 0.9em; margin-top: 20px; }",
            "</style>",
            "</head>",
            "<body>",
            f"<h1>{report.name}</h1>",
        ]

        if report.description:
            html_parts.append(f"<p>{report.description}</p>")

        # Render each item
        for item in report.items:
            item_data = data.get(item.name)

            if item.item_type == "metric" and item_data is not None and not item_data.empty:
                # Render as metric cards
                if "value" in item_data.columns and "label" in item_data.columns:
                    html_parts.append("<div>")
                    for _, row in item_data.iterrows():
                        html_parts.append("<div class='metric'>")
                        html_parts.append(f"<div class='metric-value'>{row['value']}</div>")
                        html_parts.append(f"<div class='metric-label'>{row.get('label', '')}</div>")
                        html_parts.append("</div>")
                    html_parts.append("</div>")

            elif item.item_type == "chart" and item_data is not None:
                # Render as simple HTML table with chart hint
                # For real charts, you'd use a library like Chart.js
                title = item.display_config.get("title") if item.display_config else item.name
                html_parts.append(f"<h2>{title}</h2>")
                html_parts.append(self._df_to_html_table(item_data))

            elif item.item_type in ("table", "chart") and item_data is not None:
                title = item.display_config.get("title") if item.display_config else item.name
                html_parts.append(f"<h2>{title}</h2>")
                html_parts.append(self._df_to_html_table(item_data))

            elif item.item_type == "text":
                # Static text content
                content = item.display_config.get("content", "") if item.display_config else ""
                html_parts.append(f"<div class='text-block'>{content}</div>")

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
            html.append(f"<th>{col}</th>")
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
        """Format a value for display."""
        if pd.isna(val):
            return ""
        if isinstance(val, float):
            return f"{val:,.2f}"
        if isinstance(val, int):
            return f"{val:,}"
        return str(val)


def generate_report(
    report: Report,
    output_format: str,
    parameters: dict[str, Any],
    db: Session,
    preview_only: bool = False,
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

            query = generator.build_query(item, parameters)
            try:
                df = generator.execute_query(query)
                results[item.name] = df
            except ReportGeneratorError:
                # If a query fails, continue with empty data
                results[item.name] = pd.DataFrame()

        if preview_only or output_format == "html":
            html_content = generator.render_html(results, report)
            if preview_only:
                return {"preview_data": html_content}
            else:
                # Save HTML file
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = output_dir / f"{report.name}_{timestamp}.html"
                filename.write_text(html_content, encoding="utf-8")
                return {"file_path": str(filename)}

        elif output_format == "excel":
            # Create Excel file with multiple sheets
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = output_dir / f"{report.name}_{timestamp}.xlsx"

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

"""Report generator package (批 5.2).

Originally a 628-line single-file module. Split into:

* :mod:`.engine`           — SQLAlchemy engine cache + filename helper
* :mod:`.query_builder`    — pure SQL builder + query executor
* :mod:`.errors`           — :class:`ReportGeneratorError`
* :mod:`.renderers.html`   — HTML preview renderer
* :mod:`.renderers.excel`  — Excel output writer
* :mod:`.renderers._shared` — default palette + cell formatter + HTML
                              table fragment builder

This module re-exports the public surface so callers (routers,
scheduler, tests) keep their existing import paths:

    from app.services.report_generator import (
        ReportGenerator,           # class — context manager + delegation
        generate_report,           # function — top-level orchestration
        ReportGeneratorError,      # class — exception type
        _get_or_create_engine,     # alias for engine.get_or_create_engine
        _engine_cache,             # dict — tests reset this
        evict_engine,              # function — drop + dispose cache entry
        _safe_filename,            # alias for engine.safe_filename
    )
"""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd
from sqlalchemy.orm import Session

from app.config import settings
from app.models.data_source import DataSource
from app.models.report import Report, ReportItem
from app.services.report_generator.engine import (
    _engine_cache,
    _get_or_create_engine,
    _safe_filename,
    evict_engine,
    get_or_create_engine,
    safe_filename,
)
from app.services.report_generator.errors import ReportGeneratorError
from app.services.report_generator.query_builder import build_query, execute_query
from app.services.report_generator.renderers import render_excel, render_html

__all__ = [
    "ReportGenerator",
    "ReportGeneratorError",
    "build_query",
    "evict_engine",
    "execute_query",
    "generate_report",
    "get_or_create_engine",
    "render_excel",
    "render_html",
    "safe_filename",
    # Underscore-prefixed aliases preserved for backwards-compatible
    # imports in routers/explorer.py and tests/conftest.py.
    "_engine_cache",
    "_get_or_create_engine",
    "_safe_filename",
]


class ReportGenerator:
    """Generates reports from configured report definitions.

    Thin context-manager wrapper that holds a cached engine and
    delegates :meth:`build_query`, :meth:`execute_query`, and
    :meth:`render_html` to the module-level helpers in
    :mod:`.query_builder` and :mod:`.renderers`. Kept as a class so
    the public ``with ReportGenerator(ds) as gen:`` idiom continues
    to work — the scheduler and tests rely on it.
    """

    def __init__(self, data_source: DataSource) -> None:
        self.data_source = data_source

    def __enter__(self) -> "ReportGenerator":
        """Get (or create) the cached database engine for the data source."""
        self.engine = get_or_create_engine(self.data_source)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Intentionally do not dispose — engine is cached for reuse.

        Connections stay in the pool for the next caller that hits
        the same DataSource. Call ``evict_engine(ds_id)`` when the
        underlying DataSource config changes.
        """

    def build_query(
        self, item: ReportItem, parameters: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """See :func:`app.services.report_generator.query_builder.build_query`."""
        return build_query(item, parameters)

    def execute_query(self, query: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
        """See :func:`app.services.report_generator.query_builder.execute_query`."""
        return execute_query(self.engine, query, params)

    def render_html(
        self,
        data: dict[str, pd.DataFrame],
        report: Report,
        base_url: str | None = None,
        errors: dict[str, str] | None = None,
    ) -> str:
        """See :func:`app.services.report_generator.renderers.html.render_html`."""
        return render_html(data, report, base_url=base_url, errors=errors)


def generate_report(
    report: Report,
    output_format: str,
    parameters: dict[str, Any],
    db: Session,
    preview_only: bool = False,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Generate a report and optionally save to file.

    Public signature is identical to the pre-split module — routers
    and the scheduler call this with no changes.
    """
    data_source = (
        db.query(DataSource).filter(DataSource.id == report.data_source_id).first()
    )
    if not data_source:
        raise ReportGeneratorError("Data source not found for report")

    results: dict[str, pd.DataFrame] = {}
    output_dir: Path = settings.generated_reports_dir
    output_dir.mkdir(exist_ok=True)

    with ReportGenerator(data_source) as generator:
        errors: dict[str, str] = {}
        for item in report.items:
            if item.item_type == "text":
                # Text items don't need data
                results[cast(str, item.name)] = pd.DataFrame()
                continue

            query, params = generator.build_query(item, parameters)
            try:
                df = generator.execute_query(query, params)
                results[cast(str, item.name)] = df
            except ReportGeneratorError as exc:
                # Record the error so the renderer / API can surface
                # it. Empty DataFrame keeps the rest of the pipeline
                # (HTML layout, Excel sheets) running for the other
                # items.
                errors[cast(str, item.name)] = str(exc)
                results[cast(str, item.name)] = pd.DataFrame()

        if preview_only or output_format == "html":
            html_content = generator.render_html(
                results, report, base_url=base_url, errors=errors or None
            )
            if preview_only:
                return {"preview_data": html_content, "errors": errors}

            # Save HTML file with random suffix to prevent enumeration
            # (SEC-19).
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            rand = secrets.token_hex(4)
            filename = (
                output_dir
                / f"{_safe_filename(str(report.name))}_{timestamp}_{rand}.html"
            )
            try:
                filename.write_text(html_content, encoding="utf-8")
            except OSError as exc:
                raise ReportGeneratorError(
                    f"Failed to write HTML report: {exc}"
                ) from exc
            return {"file_path": str(filename), "errors": errors}

        if output_format == "excel":
            # Random suffix also prevents enumeration of stored files.
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            rand = secrets.token_hex(4)
            filename = (
                output_dir
                / f"{_safe_filename(str(report.name))}_{timestamp}_{rand}.xlsx"
            )
            render_excel(filename, report, results)
            return {"file_path": str(filename), "errors": errors}

    raise ReportGeneratorError(f"Unsupported output format: {output_format}")

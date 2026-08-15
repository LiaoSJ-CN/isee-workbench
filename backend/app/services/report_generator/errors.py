"""Errors raised by the report generator package (批 5.2).

Single error class kept in its own module so renderers and query
builders can raise it without pulling in the full ReportGenerator
class (and the engine cache, the renderer chain, etc.).

``UnsafeSQLError`` instances raised by the SQL validator are
re-raised as ``ReportGeneratorError`` in
:func:`app.services.report_generator.query_builder.build_query` so
the existing router / test contract — ``except ReportGeneratorError``
— keeps working unchanged after the split.
"""

from __future__ import annotations


class ReportGeneratorError(Exception):
    """Raised when report generation fails."""


__all__ = ["ReportGeneratorError"]

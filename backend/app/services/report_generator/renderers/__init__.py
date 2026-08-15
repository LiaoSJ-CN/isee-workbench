"""Renderer subpackage (批 5.2).

Re-exports the public render functions so callers can do::

    from app.services.report_generator.renderers import render_html, render_excel

…without caring which sub-module each lives in.
"""

from __future__ import annotations

from app.services.report_generator.renderers.excel import render_excel
from app.services.report_generator.renderers.html import render_html

__all__ = ["render_html", "render_excel"]

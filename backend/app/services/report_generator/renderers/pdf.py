"""PDF renderer for report data (批 8.1).

Wraps :func:`app.services.report_generator.renderers.html.render_html`
through :class:`weasyprint.HTML` to turn the same self-contained
HTML preview into a paginated PDF. Re-using the HTML renderer keeps
the report layout single-sourced — if the HTML preview changes, the
PDF output tracks it without a second renderer to maintain.

``weasyprint`` is imported lazily. Servers that don't have the
package or its native dependencies (libpango, libcairo,
libgdk-pixbuf, fonts-noto-cjk) installed get a clear
:class:`ReportGeneratorError` instructing them to install
``weasyprint`` + the system libraries; we don't crash the import
chain on the web process.

Chart fidelity caveat: Chart.js renders to a ``<canvas>`` in HTML —
weasyprint cannot paint a canvas, so chart items appear as empty
placeholders in the PDF. Tables, text items, and metric cards render
faithfully (text + CSS). This matches ``xhtml2pdf``-class libraries
and is acceptable for the "snapshot / archive" use case; revisit if
customers ask for vector charts in exports.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from app.models.report import Report
from app.services.report_generator.errors import ReportGeneratorError
from app.services.report_generator.renderers.html import render_html

logger = logging.getLogger(__name__)


def _load_weasyprint() -> Any:
    """Import weasyprint lazily so an absent install doesn't break
    the web process at module-load time.

    Always raises :class:`ReportGeneratorError` on failure so the
    API layer surfaces an actionable message — "weasyprint is not
    installed; run ``pip install weasyprint`` and install the
    system libraries documented in ``backend/Dockerfile``".
    """
    try:
        import weasyprint  # type: ignore[import-not-found]  # noqa: WPS433 — intentional lazy import
    except ImportError as exc:
        raise ReportGeneratorError(
            "PDF rendering requires weasyprint. Install with "
            "`pip install weasyprint` and the system libraries "
            "(libpango, libcairo, libgdk-pixbuf, fonts-noto-cjk) "
            "documented in backend/Dockerfile."
        ) from exc
    except OSError as exc:
        # weasyprint raises OSError when native libs are missing
        # (e.g. libgobject-2.0-0). Treat the same as a missing install.
        raise ReportGeneratorError(
            "PDF rendering requires native libraries for weasyprint. "
            "Install libpango, libcairo, libgdk-pixbuf, and "
            "fonts-noto-cjk. See backend/Dockerfile for the "
            "Debian package list."
        ) from exc
    return weasyprint


def render_pdf(
    data: dict[str, pd.DataFrame],
    report: Report,
    base_url: str | None = None,
    errors: dict[str, str] | None = None,
) -> bytes:
    """Render *report* results to PDF via the HTML preview pipeline.

    Args:
        data: Map from item name to its query result DataFrame.
        report: The :class:`~app.models.report.Report` being exported.
        base_url: Optional base URL used to resolve Chart.js script
            references in the HTML (PDF strips scripts, so this is
            mostly inert for PDF — kept for signature parity with
            :func:`render_html`).
        errors: Per-item error messages from query execution.

    Returns:
        The rendered PDF as bytes (so the caller decides whether to
        write to disk or stream). The caller is expected to write
        with ``.pdf`` extension; :mod:`weasyprint` does not validate.

    Raises:
        ReportGeneratorError: when weasyprint is not installed, the
            native libraries are missing, or PDF rendering fails.
    """
    weasyprint = _load_weasyprint()

    html_content = render_html(data, report, base_url=base_url, errors=errors)
    try:
        pdf_bytes = weasyprint.HTML(string=html_content).write_pdf()
    except (ValueError, OSError) as exc:
        raise ReportGeneratorError(f"Failed to render PDF: {exc}") from exc
    return bytes(pdf_bytes)

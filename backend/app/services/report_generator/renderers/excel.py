"""Excel renderer for report data (批 5.2).

Writes a multi-sheet ``.xlsx`` file with a ``Summary`` sheet on top
(report metadata + generation timestamp) and one sheet per
non-empty item result. Sheet names are sanitized to fit Excel's
``[\\/*?:[]]`` prohibition and 31-character cap.

Lifted out of the inline block that used to live in
``generate_report`` so the report-orchestration function stays
focused on plumbing (build, execute, dispatch) and the Excel-specific
sanitization lives in one place.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.models.report import Report
from app.services.report_generator.errors import ReportGeneratorError

logger = logging.getLogger(__name__)

# Excel forbids these characters in sheet names; replace with underscore.
_SHEET_NAME_INVALID_RE = re.compile(r"[\\/*?:\[\]]")
_SHEET_NAME_MAX_LEN = 31


def render_excel(
    output_path: Path,
    report: Report,
    results: dict[str, pd.DataFrame],
) -> None:
    """Write *report* results to *output_path* as an ``.xlsx`` file.

    Raises ``ReportGeneratorError`` wrapping any openpyxl / pandas /
    OS error so the caller can surface a clean message via the API
    layer.

    Sheets with empty DataFrames are skipped — keeps the workbook
    useful for downstream consumers (BI tools, email attachments)
    and avoids ``No data available`` noise that already lives in
    the HTML preview path.
    """
    try:
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            summary_df = pd.DataFrame(
                [
                    {"Report": report.name},
                    {"Description": report.description or ""},
                    {"Generated At": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                ]
            )
            summary_df.to_excel(writer, sheet_name="Summary", index=False)

            for item_name, df in results.items():
                if df.empty:
                    continue
                sheet_name = _SHEET_NAME_INVALID_RE.sub("_", item_name[:_SHEET_NAME_MAX_LEN])
                df.to_excel(writer, sheet_name=sheet_name, index=False)
    except (ValueError, KeyError, OSError) as exc:
        raise ReportGeneratorError(f"Failed to write Excel report: {exc}") from exc

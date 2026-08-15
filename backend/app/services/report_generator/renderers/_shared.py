"""Helpers shared between the HTML and Excel renderers (批 5.2).

Both renderers need to:
    - format cell values the same way (numbers with thousands
      separators, floats with 2 decimals, NaN as empty string)
    - render a pandas DataFrame to an HTML table for the preview
    - pick a default color palette when the user-configured one is
      too short

Centralizing them here means the visual contract stays consistent
across output formats and one bug fix lands in both places.
"""

from __future__ import annotations

import numbers
from html import escape as h
from typing import Any

import pandas as pd

# Default palette for charts. Matches the Ant Design "blue/green/gold/
# red/purple/cyan/orange/pink/geek-blue/cyan" family — looks
# reasonable on a light background and survives being printed.
DEFAULT_COLORS: list[str] = [
    "#0066cc",
    "#52c41a",
    "#faad14",
    "#f5222d",
    "#722ed1",
    "#13c2c2",
    "#fa8c16",
    "#eb2f96",
    "#2f54eb",
    "#24bdbd",
]


def format_value(val: Any) -> str:
    """Format a value for display in HTML / Excel cells.

    Uses ``numbers.Integral`` / ``numbers.Real`` ABCs instead of
    ``int``/``float`` so ``np.int64`` and ``np.float64`` (which lose
    their built-in inheritance in numpy >= 2.0) still get thousands
    separators and float precision.
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


def df_to_html_table(df: pd.DataFrame, max_rows: int = 100) -> str:
    """Convert a DataFrame to an HTML table fragment.

    Returns the fragment only (no ``<html>`` wrapper) so the renderer
    can slot it into the page layout. Cells are HTML-escaped via
    :func:`format_value`; column headers go through ``html.escape``
    since they're raw column labels.

    Rows are clipped to ``max_rows``; the caller is expected to
    provide a chart / spreadsheet alternative for longer datasets.
    """
    if df.empty:
        return "<p>No data available.</p>"

    display_df = df.head(max_rows)

    parts: list[str] = ["<table>"]

    # Header
    parts.append("<thead><tr>")
    for col in display_df.columns:
        parts.append(f"<th>{h(col)}</th>")
    parts.append("</tr></thead>")

    # Body
    parts.append("<tbody>")
    for _, row in display_df.iterrows():
        parts.append("<tr>")
        for val in row:
            parts.append(f"<td>{format_value(val)}</td>")
        parts.append("</tr>")
    parts.append("</tbody>")

    parts.append("</table>")

    if len(df) > max_rows:
        parts.append(
            f"<p style='color:#666;'>Showing {max_rows} of {len(df)} rows</p>"
        )

    return "".join(parts)

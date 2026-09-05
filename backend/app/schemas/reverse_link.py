"""Lightweight reference shapes for cross-entity reverse listings (D 双向 link).

The three ``/reports/{id}/dashboards``-style endpoints return only the
``(id, name, …)`` surface needed to render a deep-link — not the full
entity payload. Keeping these in a single module avoids the circular
import you'd hit if each router pulled the other's response schema
just to embed a sub-list.

The visibility literals are imported from the canonical schemas
(:mod:`app.schemas.report` and :mod:`app.schemas.dashboard`) so a
change in the visibility union only has to land in one place.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.dashboard import DashboardVisibility
from app.schemas.report import ReportVisibility


class RefSummary(BaseModel):
    """Smallest common shape: ``(id, name)``."""

    id: int
    name: str


class ReportRef(RefSummary):
    """Reference to a :class:`app.models.report.Report` row.

    ``visibility`` lets the UI render the same lock badges the report
    list shows; ``is_active`` lets it gray out paused reports without a
    second round-trip.
    """

    visibility: ReportVisibility
    is_active: bool | None = None


class DataSourceRef(RefSummary):
    """Reference to a :class:`app.models.data_source.DataSource` row."""

    db_type: str


class DashboardRef(RefSummary):
    """Reference to a :class:`app.models.dashboard.Dashboard` row.

    ``item_count`` is the count of items that reference the parent
    entity (Report or DataSource) this listing is keyed on. It is
    optional because the dashboard-level listing at
    ``GET /data-sources/{id}/dashboards`` only counts distinct
    referencing dashboards, not items per dashboard.
    """

    visibility: DashboardVisibility
    item_count: int | None = None


__all__ = [
    "DashboardRef",
    "DataSourceRef",
    "RefSummary",
    "ReportRef",
]

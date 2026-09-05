"""Global command-palette search (批 A — 联合搜索).

One ``GET /search?q=&limit_per_kind=`` endpoint fans out to the three
ACL helpers and returns three grouped result lists. Drives the
top-bar command palette; intended as the single round-trip per
keystroke.

ACL ordering: ACL first (via ``list_accessible_*``), then the ``q``
substring match. This is the same defense-in-depth the existing
``/data-sources`` / ``/reports`` / ``/dashboards`` list endpoints
use so an unauthorized caller can't probe via filter combinations.

Why not extend ``list_accessible_data_sources`` to take ``q``: that
service deliberately returns the full unfiltered list (router
slices on top). Mirroring the existing pattern keeps behavior
byte-equivalent between ``GET /search`` and ``GET /data-sources?q=``.
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.reverse_link import (
    DashboardRef,
    DataSourceRef,
    ReportRef,
    SearchResponse,
)
from app.services.dashboard import list_accessible_dashboards
from app.services.data_source import list_accessible_data_sources
from app.services.report import list_accessible_reports

router = APIRouter(
    prefix="/search",
    tags=["search"],
    dependencies=[Depends(get_current_user)],
)


@router.get("", response_model=SearchResponse)
def global_search(
    q: str | None = Query(default=None, max_length=255),
    limit_per_kind: int = Query(default=8, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SearchResponse:
    """Search reports, dashboards, and data sources in one round-trip.

    Empty / missing ``q`` returns three empty lists without hitting
    the DB — the palette short-circuits client-side too, but the
    no-op on the server keeps the contract symmetric.

    Each kind is independently capped by ``limit_per_kind`` so a
    single noisy kind (e.g. a common substring on the data-source
    name) cannot squeeze the other two off the wire. Per-kind totals
    are returned inline as the list length; the palette does not
    page.

    Reports + dashboards run a server-side ILIKE inside the service
    (already supported); data sources do a Python ``.casefold()``
    substring match on the post-ACL list, matching the
    ``routers/data_source.list_data_sources`` precedent.
    """
    if not q:
        return SearchResponse(reports=[], dashboards=[], data_sources=[])

    # Reports + dashboards — services already accept ``q`` and apply
    # ``name.ilike`` after the ACL filter. Sliding past both filters
    # is impossible from the outside: ``list_accessible_*`` returns
    # only the rows the user can see, then trims by ``q``.
    #
    # Explicit field assembly (vs ``model_validate(orm_obj)``) — the
    # ``*Ref`` schemas don't set ``from_attributes=True`` (batch D
    # ships the rows in plain dict shape). ``cast(int, …)`` because
    # SQLAlchemy's ``Mapped[int | None]`` columns don't narrow for
    # mypy until we coerce.
    report_rows = list_accessible_reports(db, user, q=q)
    reports = [
        ReportRef(
            id=cast(int, r.id),
            name=str(r.name),
            visibility=r.visibility,  # type: ignore[arg-type]
            is_active=r.is_active,
        )
        for r in report_rows[:limit_per_kind]
    ]

    dashboard_rows = list_accessible_dashboards(db, user, q=q)
    dashboards = [
        DashboardRef(
            id=cast(int, d.id),
            name=str(d.name),
            visibility=d.visibility,  # type: ignore[arg-type]
        )
        for d in dashboard_rows[:limit_per_kind]
    ]

    # Data sources — service does NOT take ``q``. Apply the same
    # case-insensitive substring match the existing
    # ``GET /data-sources?q=`` endpoint uses so callers get a
    # predictable, uniform behavior across endpoints.
    source_rows = list_accessible_data_sources(db, user)
    needle = q.casefold()
    source_rows = [s for s in source_rows if s.name and needle in s.name.casefold()]
    data_sources = [
        DataSourceRef(
            id=cast(int, s.id),
            name=str(s.name),
            db_type=str(s.db_type),
        )
        for s in source_rows[:limit_per_kind]
    ]

    return SearchResponse(
        reports=reports,
        dashboards=dashboards,
        data_sources=data_sources,
    )


__all__ = ["router"]

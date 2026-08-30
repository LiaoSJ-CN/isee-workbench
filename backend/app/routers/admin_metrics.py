"""Admin-only DataSource connection-pool metrics endpoint (批 12).

Single GET — ``GET /admin/metrics`` — that aggregates the in-memory +
Prometheus-backed pool stats maintained by
:mod:`app.services.connection_metrics` into a JSON envelope for the
admin monitoring page.

Security model
--------------
The endpoint is gated by :data:`app.deps.admin_required`, which raises
401 for unauthenticated callers and 403 for non-admin authenticated
users. Pool stats can leak connection URL metadata (database type,
hostname via name), so the endpoint never serves non-admins.

Response shape
--------------
See :class:`app.schemas.admin_metrics.AdminMetricsResponse`. A small
``health_summary`` block (``green`` / ``yellow`` / ``red`` / ``total``)
lets the UI render status cards without iterating the full pool list.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.deps import admin_required
from app.models.user import User
from app.schemas.admin_metrics import (
    AdminMetricsResponse,
    DataSourcePoolStats,
    HealthSummary,
)
from app.services.connection_metrics import get_all_stats

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get(
    "/metrics",
    response_model=AdminMetricsResponse,
    summary="Per-DataSource connection-pool metrics (admin only)",
)
def admin_metrics(
    _user: User = Depends(admin_required),
) -> AdminMetricsResponse:
    """Return live pool metrics for every registered DataSource.

    Iterates :func:`app.services.connection_metrics.get_all_stats` —
    the in-memory store maintained by the SQLAlchemy pool event
    listeners wired into ``get_or_create_engine``.

    Cheap enough to call on every page load (the store is in-process);
    if a future batch needs historical replay we can add a window
    parameter and let Prometheus be the long-term store instead.
    """
    stats = get_all_stats()
    counts = {"green": 0, "yellow": 0, "red": 0}
    pools: list[DataSourcePoolStats] = []
    for s in stats:
        counts[s.health] += 1
        # ``s`` is a frozen :class:`PoolStats` whose ``history`` field
        # holds a list of :class:`BucketStats` dataclass instances.
        # Pydantic v2's ``model_validate`` with ``from_attributes=True``
        # does NOT recurse into list elements — the top-level fields
        # are populated but ``history.0`` would fail with
        # "Input should be a valid dictionary or instance of HistoryBucket".
        # ``asdict`` recursively turns every nested dataclass into a
        # plain dict, which Pydantic accepts.
        pools.append(DataSourcePoolStats.model_validate(asdict(s)))
    return AdminMetricsResponse(
        pools=pools,
        health_summary=HealthSummary(
            green=counts["green"],
            yellow=counts["yellow"],
            red=counts["red"],
            total=len(pools),
        ),
        generated_at=datetime.now(timezone.utc),
    )


__all__ = ["router"]

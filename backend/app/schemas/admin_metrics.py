"""Pydantic schemas for the admin pool-metrics endpoint (批 12).

Three models:

* :class:`HistoryBucket` — one 5-minute bucket of checkout / checkin /
  invalidation counts (the chart's data points).
* :class:`DataSourcePoolStats` — live snapshot for one DataSource's
  connection pool. ``model_config = ConfigDict(from_attributes=True)``
  so the router can hand back a :class:`app.services.connection_metrics.PoolStats`
  dataclass instance directly.
* :class:`HealthSummary` — aggregate counts of green / yellow / red
  pools across the fleet, surfaced as cards at the top of the admin page.
* :class:`AdminMetricsResponse` — top-level envelope returned by
  ``GET /admin/metrics``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

Health = Literal["green", "yellow", "red"]


class HistoryBucket(BaseModel):
    """One 5-minute bucket of pool activity.

    ``bucket_ts`` is unix seconds at the bucket start (the frontend
    multiplies by 1000 to render with Chart.js's millisecond axes).
    """

    bucket_ts: int
    checkouts: int
    checkins: int
    invalidations: int


class DataSourcePoolStats(BaseModel):
    """Live pool metrics for a single DataSource.

    ``from_attributes=True`` so the router can pass a frozen
    :class:`~app.services.connection_metrics.PoolStats` directly.
    """

    model_config = ConfigDict(from_attributes=True)

    data_source_id: int
    name: str
    db_type: str
    active: int
    pool_size: int
    checkouts_total: int
    checkins_total: int
    invalidations_total: int
    timeouts_total: int
    avg_held_ms: float
    timeout_rate: float
    health: Health
    history: list[HistoryBucket]


class HealthSummary(BaseModel):
    """Aggregate counts across every registered DataSource."""

    green: int
    yellow: int
    red: int
    total: int


class AdminMetricsResponse(BaseModel):
    """Top-level response for ``GET /admin/metrics``."""

    pools: list[DataSourcePoolStats]
    health_summary: HealthSummary
    generated_at: datetime


__all__ = [
    "AdminMetricsResponse",
    "DataSourcePoolStats",
    "Health",
    "HealthSummary",
    "HistoryBucket",
]

"""SQLAlchemy model for per-user dashboard subscriptions (批 14).

A :class:`DashboardSubscription` binds an *owner* (a :class:`User`) to
a :class:`Dashboard` plus a 6-field cron expression plus an optional
notification config. When the cron triggers, the subscription renders
the dashboard and dispatches the notification to the owner.

Distinct from :class:`ReportSubscription` so that:

* **Dashboard-level trigger** — a single cron entry drives the whole
  grid render (instead of N report-level cron entries for the same
  delivery cadence). The dispatch service handles incremental
  deduplication across items, so subscribers don't get pinged when
  nothing changed since the last run.
* **Multi-recipient delivery** — one dashboard, many subscribers,
  each gets their own notification destination without copy-pasting
  the cron across subscriptions.
* **Decoupled lifecycle** — pausing a subscription doesn't touch the
  dashboard definition or its underlying reports.

Mirrors :class:`ReportSubscription` shape so the sidecar scheduler can
reuse the same APScheduler instance — job IDs are namespaced
(``dsub_<id>`` vs ``sub_<id>``) so the two streams don't collide.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    from app.models.dashboard import Dashboard


class DashboardSubscription(Base):
    """A user's subscription to a dashboard on a cron schedule.

    Soft-delete-style status — ``is_active`` flips between paused and
    running without losing history. ``last_run_at`` / ``next_run_at``
    are bookkeeping for the UI; the source of truth for "when did
    this actually fire" is APScheduler's job state.
    """

    __tablename__ = "dashboard_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    owner_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dashboard_id = Column(
        Integer,
        ForeignKey("dashboards.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cron_expression = Column(String(100), nullable=False)
    parameters = Column(JSON, nullable=True, default=dict)
    notification_config = Column(JSON, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    # Incremental-dedup fingerprint (批 14.4): hex MD5 of per-item
    # tokens at the previous tick. NULL means "first run, always
    # send". The dispatcher compares against the freshly-computed
    # fingerprint; equal → skip notification, just bump ``last_run_at``.
    last_fingerprint = Column(String(64), nullable=True)

    __table_args__ = (
        # Owner-scoped history listing: "show me my subscriptions"
        # is a common page; composite index keeps it indexed.
        Index(
            "ix_dashboard_subscriptions_owner_active",
            "owner_user_id",
            "is_active",
        ),
    )

    dashboard: Mapped["Dashboard"] = relationship(
        "Dashboard", back_populates="subscriptions"
    )

    def __repr__(self) -> str:
        return (
            f"<DashboardSubscription(id={self.id}, owner_user_id={self.owner_user_id}, "
            f"dashboard_id={self.dashboard_id}, cron='{self.cron_expression}', "
            f"active={self.is_active})>"
        )

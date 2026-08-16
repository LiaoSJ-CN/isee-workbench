"""SQLAlchemy model for per-user report subscriptions (批 8.3).

A :class:`ReportSubscription` binds an *owner* (a :class:`User`) to a
:class:`Report` plus a 6-field cron expression plus an optional
notification config. When the cron triggers, the subscription runs
the report and dispatches the notification to the owner — independent
of the report's own ``is_scheduled`` flag (operators may keep a
"system" scheduling knob on a public report while users subscribe
on their own cadence).

Distinct from :class:`Report`'s built-in scheduling so that:

* **Multi-recipient delivery** — one report, many subscribers, each
  gets their own notification destination without copy-pasting the
  cron across ``Report.cron_expression`` rows.
* **Future RBAC isolation** — even before 批 9 lands, subscription
  ownership is per-user (queries are filtered by ``owner_user_id``).
* **Decoupled lifecycle** — pausing a subscription doesn't touch the
  report definition.

Caveats:

* Subscriptions reuse the same :class:`APScheduler` instance as
  ``Report.is_scheduled`` jobs. Job IDs are namespaced
  (``report_<id>`` vs ``sub_<id>``) so the two streams don't
  collide and ``sync_with_database`` can ignore the other.
* Per-user rate-limiting on subscription creation lives in the router
  (``/reports/{id}/subscriptions`` reuses the existing
  ``reports_generate_rate_limit`` budget — operators can bump it
  once subscriptions become popular).
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
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    pass


class ReportSubscription(Base):
    """A user's subscription to a report on a cron schedule.

    Soft-delete-style status — ``is_active`` flips between paused and
    running without losing history. ``last_run_at`` / ``next_run_at``
    are bookkeeping for the UI; the source of truth for "when did
    this actually fire" is APScheduler's job state.
    """

    __tablename__ = "report_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    owner_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    report_id = Column(
        Integer,
        ForeignKey("reports.id", ondelete="CASCADE"),
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

    __table_args__ = (
        # Owner-scoped history listing: "show me my subscriptions"
        # is a common page; composite index keeps it indexed.
        Index(
            "ix_report_subscriptions_owner_active",
            "owner_user_id",
            "is_active",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ReportSubscription(id={self.id}, owner_user_id={self.owner_user_id}, "
            f"report_id={self.report_id}, cron='{self.cron_expression}', "
            f"active={self.is_active})>"
        )

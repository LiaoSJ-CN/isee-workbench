"""Append-only audit log writer (批 9.5).

Every mutating router calls :func:`log` after a successful business
``commit()`` to record who did what to which resource. The function is
**fire-and-forget** — a failed audit insert must never break the
business endpoint, so :func:`log` swallows exceptions and logs them
via ``logger.exception``.

Three public surfaces:

1. **Action constants** (``ACTION_*``) — the canonical set of auditable
   events. Add a new action by appending here; the router imports the
   constant by name rather than passing a string literal.

2. **Target-type constants** (``TARGET_TYPE_*``) — what kind of
   resource the action was on. A single action may have multiple
   target types (e.g. ``ACTION_REPORT_GENERATE`` always targets a
   report, but ``ACTION_REPORT_ITEM_CREATE`` targets a
   ``ReportItem``).

3. **:func:`log`** — the writer. Takes the same ``db: Session`` as
   the calling router so it shares the connection / transaction
   context; commits in its own transaction so audit failure can't
   roll back business data (the business commit has already happened
   by the time :func:`log` runs).

The :func:`_snapshot` helper turns ORM rows into JSON-safe dicts via
the matching Pydantic ``*Response`` schema. Preferring the schema over
``__dict__`` keeps SQLAlchemy internal state (``_sa_instance_state``
etc.) out of the audit row.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.middleware.request_id import get_request_id
from app.models.audit_log import AuditLog
from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.report import Report, ReportItem
from app.models.report_access import ReportAccess
from app.models.report_job import ReportJob
from app.models.report_parameter import ReportParameter
from app.models.report_subscription import ReportSubscription
from app.schemas.data_source import DataSourceResponse, GrantResponse
from app.schemas.job import ReportJobResponse
from app.schemas.report import ReportDetailResponse, ReportItemResponse, ReportShareResponse
from app.schemas.report_parameter import ReportParameterResponse
from app.schemas.report_subscription import ReportSubscriptionResponse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action constants
# ---------------------------------------------------------------------------
ACTION_LOGIN = "login"
ACTION_LOGOUT = "logout"
ACTION_TOKEN_REFRESH = "token_refresh"

ACTION_DATA_SOURCE_CREATE = "data_source.create"
ACTION_DATA_SOURCE_UPDATE = "data_source.update"
ACTION_DATA_SOURCE_DELETE = "data_source.delete"
ACTION_DATA_SOURCE_GRANT = "data_source.grant"
ACTION_DATA_SOURCE_REVOKE = "data_source.revoke"
# 批 10.3: clone a DataSource — ``before`` is the source row,
# ``after`` is the new row (different id / owner). Reuses
# ``TARGET_TYPE_DATA_SOURCE``; the action disambiguates.
ACTION_DATA_SOURCE_CLONE = "data_source.clone"

ACTION_REPORT_CREATE = "report.create"
ACTION_REPORT_UPDATE = "report.update"
ACTION_REPORT_DELETE = "report.delete"
# 批 10.3: duplicate a Report — copies items + parameters, resets
# scheduler fields, drops shares. ``before`` = source report,
# ``after`` = new report.
ACTION_REPORT_DUPLICATE = "report.duplicate"
ACTION_REPORT_ITEM_CREATE = "report.item.create"
ACTION_REPORT_ITEM_UPDATE = "report.item.update"
ACTION_REPORT_ITEM_DELETE = "report.item.delete"
ACTION_REPORT_ITEM_REORDER = "report.item.reorder"
ACTION_REPORT_PARAM_CREATE = "report.parameter.create"
ACTION_REPORT_PARAM_UPDATE = "report.parameter.update"
ACTION_REPORT_PARAM_DELETE = "report.parameter.delete"
ACTION_REPORT_SHARE = "report.share"
ACTION_REPORT_REVOKE = "report.revoke"
ACTION_REPORT_GENERATE = "report.generate"

ACTION_JOB_ENQUEUE = "job.enqueue"

ACTION_SUBSCRIPTION_CREATE = "subscription.create"
ACTION_SUBSCRIPTION_UPDATE = "subscription.update"
ACTION_SUBSCRIPTION_DELETE = "subscription.delete"
ACTION_SUBSCRIPTION_PAUSE = "subscription.pause"
ACTION_SUBSCRIPTION_RESUME = "subscription.resume"

ACTION_SCHEDULER_JOB_CREATE = "scheduler.job.create"
ACTION_SCHEDULER_JOB_DELETE = "scheduler.job.delete"
ACTION_SCHEDULER_SYNC = "scheduler.sync"

ACTION_EXPLORER_QUERY = "explorer.query"

ALL_ACTIONS: tuple[str, ...] = (
    ACTION_LOGIN,
    ACTION_LOGOUT,
    ACTION_TOKEN_REFRESH,
    ACTION_DATA_SOURCE_CREATE,
    ACTION_DATA_SOURCE_UPDATE,
    ACTION_DATA_SOURCE_DELETE,
    ACTION_DATA_SOURCE_GRANT,
    ACTION_DATA_SOURCE_REVOKE,
    ACTION_REPORT_CREATE,
    ACTION_REPORT_UPDATE,
    ACTION_REPORT_DELETE,
    ACTION_REPORT_ITEM_CREATE,
    ACTION_REPORT_ITEM_UPDATE,
    ACTION_REPORT_ITEM_DELETE,
    ACTION_REPORT_ITEM_REORDER,
    ACTION_REPORT_PARAM_CREATE,
    ACTION_REPORT_PARAM_UPDATE,
    ACTION_REPORT_PARAM_DELETE,
    ACTION_REPORT_SHARE,
    ACTION_REPORT_REVOKE,
    ACTION_REPORT_GENERATE,
    ACTION_JOB_ENQUEUE,
    ACTION_SUBSCRIPTION_CREATE,
    ACTION_SUBSCRIPTION_UPDATE,
    ACTION_SUBSCRIPTION_DELETE,
    ACTION_SUBSCRIPTION_PAUSE,
    ACTION_SUBSCRIPTION_RESUME,
    ACTION_SCHEDULER_JOB_CREATE,
    ACTION_SCHEDULER_JOB_DELETE,
    ACTION_SCHEDULER_SYNC,
    ACTION_EXPLORER_QUERY,
)


# ---------------------------------------------------------------------------
# Target type constants
# ---------------------------------------------------------------------------
TARGET_TYPE_SESSION = "session"
TARGET_TYPE_DATA_SOURCE = "data_source"
TARGET_TYPE_DATA_SOURCE_GRANT = "data_source_grant"
TARGET_TYPE_REPORT = "report"
TARGET_TYPE_REPORT_ITEM = "report_item"
TARGET_TYPE_REPORT_PARAM = "report_parameter"
TARGET_TYPE_REPORT_SHARE = "report_share"
TARGET_TYPE_REPORT_JOB = "report_job"
TARGET_TYPE_REPORT_SUBSCRIPTION = "report_subscription"
TARGET_TYPE_SCHEDULER = "scheduler"
TARGET_TYPE_EXPLORER_QUERY = "explorer_query"

ALL_TARGET_TYPES: tuple[str, ...] = (
    TARGET_TYPE_SESSION,
    TARGET_TYPE_DATA_SOURCE,
    TARGET_TYPE_DATA_SOURCE_GRANT,
    TARGET_TYPE_REPORT,
    TARGET_TYPE_REPORT_ITEM,
    TARGET_TYPE_REPORT_PARAM,
    TARGET_TYPE_REPORT_SHARE,
    TARGET_TYPE_REPORT_JOB,
    TARGET_TYPE_REPORT_SUBSCRIPTION,
    TARGET_TYPE_SCHEDULER,
    TARGET_TYPE_EXPLORER_QUERY,
)


# ---------------------------------------------------------------------------
# Snapshot helper
# ---------------------------------------------------------------------------
_SENSITIVE_FIELDS: frozenset[str] = frozenset({"password", "password_hash"})
_MAX_STRING_LEN = 4096
_MAX_USER_AGENT_LEN = 512

# ORM class → Pydantic response schema. ``User`` is intentionally absent —
# callers that need to snapshot a user construct a minimal dict by hand so
# ``password_hash`` never lands in the audit row.
_SCHEMA_FOR_TYPE: dict[type, type[BaseModel]] = {
    DataSource: DataSourceResponse,
    DataSourceAccess: GrantResponse,
    Report: ReportDetailResponse,
    ReportItem: ReportItemResponse,
    ReportParameter: ReportParameterResponse,
    ReportAccess: ReportShareResponse,
    ReportJob: ReportJobResponse,
    ReportSubscription: ReportSubscriptionResponse,
}


def _redact(d: Mapping[str, Any]) -> dict[str, Any]:
    """Replace values for sensitive keys with a sentinel before persistence."""
    return {
        k: ("***REDACTED***" if k in _SENSITIVE_FIELDS else v)
        for k, v in d.items()
    }


def _truncate(value: Any, max_len: int = _MAX_STRING_LEN) -> Any:
    """Recursively clamp long strings so a single big JSON column can't bloat."""
    if isinstance(value, str):
        if len(value) > max_len:
            return value[:max_len] + "…(truncated)"
        return value
    if isinstance(value, dict):
        return {k: _truncate(v, max_len) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(item, max_len) for item in value]
    return value


def _truncate_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Narrow return type for the dict branches used by :func:`_snapshot`."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        out[k] = _truncate(v)
    return out


def _snapshot(obj: Any) -> dict[str, Any] | None:
    """Convert an ORM row (or pre-built dict) into a JSON-safe audit snapshot.

    ``None`` passes through (so callers can pass ``before=None`` for
    create events without branching). For ORM rows we look up the
    matching Pydantic ``*Response`` schema in :data:`_SCHEMA_FOR_TYPE`
    and use ``model_dump(mode='json')`` — that handles datetime /
    JSON column conversion and skips SQLAlchemy internal state.

    Falls back to a shallow dict for unknown types (used for the
    manual ``User`` snapshots and ad-hoc dicts passed by router code
    e.g. for ``reorder`` and ``explorer.query``).
    """
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return _truncate_dict(_redact(obj))
    schema_cls = _SCHEMA_FOR_TYPE.get(type(obj))
    if schema_cls is None:
        # Last-resort fallback. Strips SQLAlchemy state via __dict__ copy
        # filtered to primitive values; logged as a warning so we notice
        # if a new ORM model sneaks in without being registered.
        data: dict[str, Any] = {
            k: v
            for k, v in obj.__dict__.items()
            if not k.startswith("_sa_")
        }
        logger.warning(
            "audit: no schema registered for %s; falling back to __dict__",
            type(obj).__name__,
        )
        return _truncate_dict(_redact(data))
    data = schema_cls.model_validate(obj).model_dump(mode="json")
    return _truncate_dict(_redact(data))


# ---------------------------------------------------------------------------
# Public writer
# ---------------------------------------------------------------------------
def log(
    db: Session,
    *,
    actor_user_id: int | None,
    action: str,
    target_type: str,
    target_id: int | None = None,
    before: Any = None,
    after: Any = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> int | None:
    """Append one audit-log row.

    Returns the new row's ``id`` on success, ``None`` if the insert
    failed and the exception was swallowed (see below). Callers
    generally fire-and-forget; tests use the return value to assert
    the row landed.

    **Never raises.** The business endpoint has already committed by
    the time this runs; an audit failure must not propagate. We catch
    ``Exception`` (not just SQLAlchemy errors) because the JSON
    snapshot / Pydantic validation can also raise on a schema
    mismatch.

    The caller passes the same ``Session`` as the business endpoint;
    the commit below is in a fresh transaction from SQLAlchemy's
    standpoint because the previous commit closed the previous
    transaction. If this commit itself raises, only the audit insert
    is rolled back — the business row is already safe.
    """
    try:
        effective_request_id = request_id or get_request_id() or "-"
        if isinstance(user_agent, str) and len(user_agent) > _MAX_USER_AGENT_LEN:
            user_agent = user_agent[:_MAX_USER_AGENT_LEN]
        row = AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=_snapshot(before),
            after=_snapshot(after),
            request_id=effective_request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(row)
        db.commit()
        return row.id
    except Exception:
        # Roll back the audit insert only — the business commit
        # already landed, so this rollback is a no-op for the
        # business row but saves the session from a poisoned state.
        try:
            db.rollback()
        except Exception:  # pragma: no cover - defensive
            pass
        logger.exception(
            "audit log write failed: action=%s target_type=%s target_id=%s",
            action,
            target_type,
            target_id,
        )
        return None


__all__ = [
    # Action constants
    "ACTION_LOGIN",
    "ACTION_LOGOUT",
    "ACTION_TOKEN_REFRESH",
    "ACTION_DATA_SOURCE_CREATE",
    "ACTION_DATA_SOURCE_UPDATE",
    "ACTION_DATA_SOURCE_DELETE",
    "ACTION_DATA_SOURCE_GRANT",
    "ACTION_DATA_SOURCE_REVOKE",
    "ACTION_REPORT_CREATE",
    "ACTION_REPORT_UPDATE",
    "ACTION_REPORT_DELETE",
    "ACTION_REPORT_ITEM_CREATE",
    "ACTION_REPORT_ITEM_UPDATE",
    "ACTION_REPORT_ITEM_DELETE",
    "ACTION_REPORT_ITEM_REORDER",
    "ACTION_REPORT_PARAM_CREATE",
    "ACTION_REPORT_PARAM_UPDATE",
    "ACTION_REPORT_PARAM_DELETE",
    "ACTION_REPORT_SHARE",
    "ACTION_REPORT_REVOKE",
    "ACTION_REPORT_GENERATE",
    "ACTION_JOB_ENQUEUE",
    "ACTION_SUBSCRIPTION_CREATE",
    "ACTION_SUBSCRIPTION_UPDATE",
    "ACTION_SUBSCRIPTION_DELETE",
    "ACTION_SUBSCRIPTION_PAUSE",
    "ACTION_SUBSCRIPTION_RESUME",
    "ACTION_SCHEDULER_JOB_CREATE",
    "ACTION_SCHEDULER_JOB_DELETE",
    "ACTION_SCHEDULER_SYNC",
    "ACTION_EXPLORER_QUERY",
    "ALL_ACTIONS",
    # Target type constants
    "TARGET_TYPE_SESSION",
    "TARGET_TYPE_DATA_SOURCE",
    "TARGET_TYPE_DATA_SOURCE_GRANT",
    "TARGET_TYPE_REPORT",
    "TARGET_TYPE_REPORT_ITEM",
    "TARGET_TYPE_REPORT_PARAM",
    "TARGET_TYPE_REPORT_SHARE",
    "TARGET_TYPE_REPORT_JOB",
    "TARGET_TYPE_REPORT_SUBSCRIPTION",
    "TARGET_TYPE_SCHEDULER",
    "TARGET_TYPE_EXPLORER_QUERY",
    "ALL_TARGET_TYPES",
    # Writer
    "log",
]

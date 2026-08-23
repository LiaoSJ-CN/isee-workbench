"""API routes for data exploration (SQL query execution)."""

import logging
from datetime import datetime as dt
from decimal import Decimal
from typing import Any, cast

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.user import User
from app.services import audit as audit_service
from app.services.connection import ConnectionError
from app.services.data_source import get_data_source_for_user
from app.services.report_generator import _get_or_create_engine
from app.services.sql_validator import UnsafeSQLError, validate_select_only

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/explorer",
    tags=["explorer"],
    dependencies=[Depends(get_current_user)],
)


# Per-IP rate limit on the SQL exploration endpoint. 30/min/IP keeps a
# single analyst comfortable while making abuse (or a runaway script)
# visible to the operator — ``X-Too-Many-Requests`` is a clear signal.
_explorer_query_limiter = RateLimiter(
    max_requests=settings.explorer_query_rate_limit,
    window_seconds=60,
)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class QueryRequest(BaseModel):
    """SQL query request."""

    data_source_id: int
    sql: str


class QueryResponse(BaseModel):
    """SQL query response."""

    success: bool
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    error: str | None = None


@router.post("/query", response_model=QueryResponse)
def execute_query(
    payload: QueryRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QueryResponse:
    """Execute a SELECT SQL query against a data source.

    批 9.3: data source is owner/grant-gated via
    :func:`app.services.data_source.get_data_source_for_user`. A user
    with no access sees the same 404 as a missing data source — never
    a permission error that would leak existence.
    """
    # Rate-limit by IP *before* any DB work — the limit protects against
    # runaway scripts that hammer the endpoint without ever being authed
    # to a meaningful user. Namespace the key so the budget is independent
    # from ``/reports/generate`` and ``/reports/{id}/jobs`` (both of which
    # also use client IP as their key material).
    if _explorer_query_limiter.is_rate_limited(f"explorer_query:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many explorer queries. Limit: "
                f"{settings.explorer_query_rate_limit}/min/IP."
            ),
            headers={"Retry-After": "60"},
        )

    # Get data source via ACL — None when missing OR no read access.
    data_source = get_data_source_for_user(db, payload.data_source_id, user)
    if data_source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Data source not found",
        )

    # Security check — all validation lives in sql_validator now.
    # We keep returning 200 + success=False (not 422) so the existing
    # frontend explorer code path is unchanged.
    try:
        validate_select_only(payload.sql)
    except UnsafeSQLError as exc:
        # 批 9.5: audit unsafe-SQL attempts. The validator rejected
        # the query — the attempt itself is auditable ("who probed
        # the validator with what"). ``sql`` is captured so we can
        # see which statements were blocked.
        audit_service.log(
            db,
            actor_user_id=cast(int, user.id),
            action=audit_service.ACTION_EXPLORER_QUERY,
            target_type=audit_service.TARGET_TYPE_EXPLORER_QUERY,
            target_id=None,
            before=None,
            after={
                "data_source_id": payload.data_source_id,
                "sql": payload.sql,
                "row_count": 0,
                "success": False,
                "error": str(exc),
            },
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
        return QueryResponse(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            error=f"Only SELECT queries are allowed: {exc}",
        )

    # Build connection and execute using pandas
    try:
        engine = _get_or_create_engine(data_source)

        # Statement timeout for PostgreSQL-based backends (PY-2).
        # SQLite doesn't support SET — skip silently.
        timeout = max(0, settings.explorer_statement_timeout)
        if timeout > 0 and getattr(engine, "name", "") in ("postgresql",):
            with engine.connect() as conn:
                conn.execute(text(f"SET LOCAL statement_timeout = {timeout * 1000}"))
                conn.commit()

        # Row cap: wrap user SQL in a subquery so we never pull unlimited
        # rows into memory, even if the user forgets a LIMIT clause.
        max_rows = max(1, settings.explorer_max_rows)
        capped_sql = (
            f"SELECT * FROM ({payload.sql}) AS _explorer_sub "
            f"LIMIT {max_rows}"
        )

        df = pd.read_sql(text(capped_sql), engine)

        columns = df.columns.tolist()
        rows = df.to_dict("records")
        row_count = len(rows)

        # Convert types for JSON serialization — pandas/numpy types plus
        # datetime, Decimal, and bytes which json.dumps can't serialize.
        cleaned_rows: list[dict[str, Any]] = []
        for row in cast(list[dict[str, Any]], rows):
            cleaned_row: dict[str, Any] = {}
            for k, v in row.items():
                if pd.isna(v) or v is None:
                    cleaned_row[k] = None
                elif isinstance(v, (np.integer, np.floating)):
                    cleaned_row[k] = v.item()
                elif isinstance(v, dt):
                    cleaned_row[k] = v.isoformat()
                elif isinstance(v, Decimal):
                    cleaned_row[k] = float(v)
                elif isinstance(v, bytes):
                    cleaned_row[k] = v.decode("utf-8", errors="replace")
                else:
                    cleaned_row[k] = v
            cleaned_rows.append(cleaned_row)

        # 批 9.5: audit successful explorer query. ``after`` is
        # hand-built — the row content itself is too large to dump
        # (the whole point of the row cap above is to keep results
        # bounded), but ``sql`` + ``row_count`` are what an admin
        # needs for the audit ("who ran which SQL on which DS, and
        # how many rows came back").
        audit_service.log(
            db,
            actor_user_id=cast(int, user.id),
            action=audit_service.ACTION_EXPLORER_QUERY,
            target_type=audit_service.TARGET_TYPE_EXPLORER_QUERY,
            target_id=None,
            before=None,
            after={
                "data_source_id": payload.data_source_id,
                "sql": payload.sql,
                "row_count": row_count,
                "success": True,
            },
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
        return QueryResponse(
            success=True,
            columns=columns,
            rows=cleaned_rows,
            row_count=row_count,
        )

    except ConnectionError as exc:
        # 批 9.5: audit connection failures so an operator can see
        # whether DS downtime correlates with specific queries.
        audit_service.log(
            db,
            actor_user_id=cast(int, user.id),
            action=audit_service.ACTION_EXPLORER_QUERY,
            target_type=audit_service.TARGET_TYPE_EXPLORER_QUERY,
            target_id=None,
            before=None,
            after={
                "data_source_id": payload.data_source_id,
                "sql": payload.sql,
                "row_count": 0,
                "success": False,
                "error": str(exc),
            },
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
        return QueryResponse(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            error=f"Connection error: {exc}",
        )
    except Exception:
        # 批 9.5: audit unexpected failures too. ``sql`` lets the
        # operator reproduce the trace after the fact.
        logger.exception(
            "Unexpected error during query execution for data source %s",
            payload.data_source_id,
        )
        audit_service.log(
            db,
            actor_user_id=cast(int, user.id),
            action=audit_service.ACTION_EXPLORER_QUERY,
            target_type=audit_service.TARGET_TYPE_EXPLORER_QUERY,
            target_id=None,
            before=None,
            after={
                "data_source_id": payload.data_source_id,
                "sql": payload.sql,
                "row_count": 0,
                "success": False,
                "error": "unexpected_error",
            },
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
        return QueryResponse(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            error="An unexpected error occurred. Please check the server logs for details.",
        )

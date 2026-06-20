"""API routes for data exploration (SQL query execution)."""

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.data_source import DataSource
from app.services.connection import ConnectionError, build_connection_url

router = APIRouter(
    prefix="/explorer",
    tags=["explorer"],
    dependencies=[Depends(get_current_user)],
)


class QueryRequest(BaseModel):
    """SQL query request."""

    data_source_id: int
    sql: str


class QueryResponse(BaseModel):
    """SQL query response."""

    success: bool
    columns: list[str]
    rows: list[dict]
    row_count: int
    error: str | None = None


# Dangerous SQL keywords that should not be allowed
FORBIDDEN_KEYWORDS = [
    "DROP",
    "DELETE",
    "TRUNCATE",
    "ALTER",
    "CREATE",
    "INSERT",
    "UPDATE",
    "GRANT",
    "REVOKE",
]


def is_safe_sql(sql: str) -> bool:
    """Check if SQL appears safe (SELECT only)."""
    import re
    upper_sql = sql.upper().strip()
    # Check it starts with SELECT
    if not upper_sql.startswith("SELECT"):
        return False
    # Check no dangerous keywords appear as whole words
    for keyword in FORBIDDEN_KEYWORDS:
        # Use word boundary to avoid false matches like CREATE in created_date
        if re.search(r'\b' + keyword + r'\b', upper_sql):
            return False
    return True


@router.post("/query", response_model=QueryResponse)
def execute_query(request: QueryRequest, db: Session = Depends(get_db)) -> QueryResponse:
    """Execute a SELECT SQL query against a data source."""
    # Get data source
    data_source = db.query(DataSource).filter(DataSource.id == request.data_source_id).first()
    if not data_source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Data source {request.data_source_id} not found",
        )

    # Security check
    if not is_safe_sql(request.sql):
        return QueryResponse(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            error="Only SELECT queries are allowed for security reasons",
        )

    # Build connection and execute using pandas
    try:
        url = build_connection_url(data_source)
        if data_source.db_type == "sqlite":
            engine = create_engine(url)
        else:
            engine = create_engine(url, connect_args={"connect_timeout": 30})

        df = pd.read_sql(text(request.sql), engine)
        engine.dispose()

        columns = df.columns.tolist()
        rows = df.to_dict("records")
        row_count = len(rows)

        # Convert types for JSON serialization
        import numpy as np
        cleaned_rows = []
        for row in rows:
            cleaned_row = {}
            for k, v in row.items():
                if pd.isna(v) or v is None:
                    cleaned_row[k] = None
                elif isinstance(v, (np.integer, np.floating)):
                    cleaned_row[k] = v.item()
                else:
                    cleaned_row[k] = v
            cleaned_rows.append(cleaned_row)

        return QueryResponse(
            success=True,
            columns=columns,
            rows=cleaned_rows,
            row_count=row_count,
        )

    except ConnectionError as exc:
        return QueryResponse(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            error=f"Connection error: {exc}",
        )
    except Exception as exc:
        return QueryResponse(
            success=False,
            columns=[],
            rows=[],
            row_count=0,
            error=str(exc),
        )

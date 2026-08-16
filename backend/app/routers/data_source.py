"""API routes for data source management (批 9.3 adds per-user ACL)."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.crypto import encrypt as crypto_encrypt
from app.database import get_db
from app.deps import get_current_user
from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.user import User
from app.schemas.data_source import (
    DataSourceCreate,
    DataSourceResponse,
    DataSourceSchemaResponse,
    DataSourceUpdate,
    GrantCreate,
    GrantResponse,
)
from app.services.connection import ConnectionError, test_connection
from app.services.data_source import (
    PERMISSION_WRITE,
    can_share,
    get_data_source_for_user,
    is_admin,
    is_owner,
    list_accessible_data_sources,
    list_grants_for_data_source,
    revoke_grant,
    upsert_grant,
)
from app.services.report_generator import evict_engine
from app.services.schema_introspection import (
    SchemaIntrospectionError,
    introspect_schema,
)

router = APIRouter(
    prefix="/data-sources",
    tags=["data-sources"],
    dependencies=[Depends(get_current_user)],
)


def _not_found() -> HTTPException:
    """Uniform 404 — used for both "row missing" and "no access" so
    an unauthorized caller can't probe for the existence of someone
    else's data source."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Data source not found",
    )


@router.get("", response_model=list[DataSourceResponse])
def list_data_sources(
    response: Response,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DataSource]:
    """List data sources the caller can see, with pagination.

    ACL: admin sees all; owner sees their own; others see the union
    of "owner=me" and "I have any grant". Total accessible count is
    reported in ``X-Total-Count`` so the frontend can drive a pager.
    """
    sources = list_accessible_data_sources(db, user)
    response.headers["X-Total-Count"] = str(len(sources))
    # Stable order so offset+limit produces consistent pages.
    return sources[offset : offset + limit]


@router.post("", response_model=DataSourceResponse, status_code=status.HTTP_201_CREATED)
def create_data_source(
    payload: DataSourceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DataSource:
    """Create a new data source, owned by the caller."""
    existing = db.query(DataSource).filter(DataSource.name == payload.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Data source named '{payload.name}' already exists",
        )

    data = payload.model_dump()
    if data.get("password"):
        data["password"] = crypto_encrypt(data["password"])
    data["owner_user_id"] = user.id
    source = DataSource(**data)
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.get("/{source_id}", response_model=DataSourceResponse)
def get_data_source(
    source_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DataSource:
    """Get a single data source by ID (read ACL)."""
    ds = get_data_source_for_user(db, source_id, user)
    if ds is None:
        raise _not_found()
    return ds


@router.put("/{source_id}", response_model=DataSourceResponse)
def update_data_source(
    source_id: int,
    payload: DataSourceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DataSource:
    """Update an existing data source (write ACL).

    Returns 404 — not 403 — when the caller lacks write access. See
    :func:`_not_found` for the rationale.
    """
    ds = get_data_source_for_user(db, source_id, user, level=PERMISSION_WRITE)
    if ds is None:
        raise _not_found()

    update_data = payload.model_dump(exclude_unset=True)
    if "password" in update_data and update_data["password"] is not None:
        update_data["password"] = crypto_encrypt(update_data["password"])
    for field, value in update_data.items():
        setattr(ds, field, value)

    db.commit()
    db.refresh(ds)
    # Connection URL may have changed (host/port/user/password/db) — drop the
    # cached engine so the next call rebuilds it against the new config.
    evict_engine(source_id)
    return ds


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_data_source(
    source_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a data source. Owner-or-admin only — even a write
    grant does not authorize delete."""
    ds = get_data_source_for_user(db, source_id, user)
    if ds is None or not (is_admin(user) or is_owner(user, ds)):
        raise _not_found()
    db.delete(ds)
    db.commit()
    # Free any pooled connections that were bound to the now-deleted source.
    evict_engine(source_id)
    return None


@router.post("/{source_id}/test", response_model=dict)
def test_data_source(
    source_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, str | bool]:
    """Test connectivity to a data source (read ACL)."""
    ds = get_data_source_for_user(db, source_id, user)
    if ds is None:
        raise _not_found()

    try:
        return test_connection(ds)
    except ConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/{source_id}/schema", response_model=DataSourceSchemaResponse)
def get_data_source_schema(
    source_id: int,
    schema: str | None = Query(default=None, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DataSourceSchemaResponse:
    """Introspect the data source's schema and return its tables + columns.

    The frontend schema tree calls this when the user picks a data
    source. Pass ``?schema=foo`` to override the configured schema;
    otherwise the data source's ``schema_name`` is used (``"public"``
    for Postgres-family, ``"main"`` for SQLite by default).

    Read ACL — anyone who can list/get the data source can introspect it.
    """
    ds = get_data_source_for_user(db, source_id, user)
    if ds is None:
        raise _not_found()

    try:
        tables = introspect_schema(ds, schema_name=schema)
    except SchemaIntrospectionError as exc:
        # Upstream DB unreachable / permission denied / schema missing —
        # surface as 502 because we're a proxy to it.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    return DataSourceSchemaResponse(tables=tables)


# ---------------------------------------------------------------------------
# Grants (批 9.3)
# ---------------------------------------------------------------------------


@router.post(
    "/{source_id}/grants",
    response_model=GrantResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_grant_endpoint(
    source_id: int,
    payload: GrantCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DataSourceAccess:
    """Grant ``user_id`` read/write on this data source. Owner-or-admin
    only — see :func:`app.services.data_source.can_share`.

    Upserts: re-POSTing with the same ``user_id`` updates the
    permission level (and refreshes ``granted_by``) rather than
    hitting the unique constraint.
    """
    ds = get_data_source_for_user(db, source_id, user)
    if ds is None or not can_share(db, user, ds):
        raise _not_found()

    target = db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    grant = upsert_grant(
        db,
        data_source_id=source_id,
        target_user_id=payload.user_id,
        permission=payload.permission,
        granted_by=cast(int, user.id),
    )
    return grant


@router.get(
    "/{source_id}/grants",
    response_model=list[GrantResponse],
)
def list_grants_endpoint(
    source_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DataSourceAccess]:
    """List every grant on this data source. Owner-or-admin only — a
    read grant on the source itself does not let the recipient see
    *who else* has access."""
    ds = get_data_source_for_user(db, source_id, user)
    if ds is None or not (is_admin(user) or is_owner(user, ds)):
        raise _not_found()
    return list_grants_for_data_source(db, source_id)


@router.delete(
    "/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_grant_endpoint(
    grant_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Revoke a grant by id. Owner-or-admin on the underlying data
    source only.

    The path uses ``/grants/{grant_id}`` rather than
    ``/{source_id}/grants/{grant_id}`` so an unauthorized caller
    can't probe for the existence of a grant_id they don't own —
    the lookup happens by id, then ACL is checked on the parent
    data source.
    """
    grant = db.get(DataSourceAccess, grant_id)
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Grant not found",
        )
    ds = get_data_source_for_user(db, grant.data_source_id, user)
    if ds is None or not (is_admin(user) or is_owner(user, ds)):
        raise _not_found()
    revoke_grant(db, grant)
    return None

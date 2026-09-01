"""API routes for data source management (批 9.3 adds per-user ACL)."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.crypto import encrypt as crypto_encrypt
from app.database import get_db
from app.deps import get_current_user
from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.report import Report
from app.models.user import User
from app.schemas.data_source import (
    DataSourceCloneRequest,
    DataSourceCreate,
    DataSourceResponse,
    DataSourceSchemaResponse,
    DataSourceUpdate,
    GrantCreate,
    GrantResponse,
)
from app.services import audit as audit_service
from app.services.connection import ConnectionError, test_connection
from app.services.data_source import (
    PERMISSION_WRITE,
    can_share,
    clone_data_source,
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

# How many blocking report names to name in the 409 detail before
# collapsing the rest into an "(and N more)" tail.
_DELETE_BLOCKER_SAMPLE = 5


def _not_found() -> HTTPException:
    """Uniform 404 — used for both "row missing" and "no access" so
    an unauthorized caller can't probe for the existence of someone
    else's data source."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Data source not found",
    )


def _client_ip(request: Request) -> str:
    """Peer IP for the audit log. ``ProxyHeadersMiddleware`` has
    already rewritten ``request.client.host`` when the request came
    through a trusted proxy, so this is the real client IP."""
    return request.client.host if request.client else "unknown"


@router.get("", response_model=list[DataSourceResponse])
def list_data_sources(
    response: Response,
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DataSource]:
    """List data sources the caller can see, with pagination.

    ACL: admin sees all; owner sees their own; others see the union
    of "owner=me" and "I have any grant". ``q`` is a case-insensitive
    substring match on ``name`` applied AFTER the ACL filter so an
    unauthorized caller can't probe via filter combinations
    (mirrors :func:`app.routers.dashboard.list_dashboards`).
    Total accessible count is reported in ``X-Total-Count`` so the
    frontend can drive a pager; it reflects the post-ACL AND
    post-``q`` total.
    """
    sources = list_accessible_data_sources(db, user)
    if q:
        needle = q.casefold()
        sources = [s for s in sources if s.name and needle in s.name.casefold()]
    response.headers["X-Total-Count"] = str(len(sources))
    # Stable order so offset+limit produces consistent pages.
    return sources[offset : offset + limit]


@router.post("", response_model=DataSourceResponse, status_code=status.HTTP_201_CREATED)
def create_data_source(
    payload: DataSourceCreate,
    request: Request,
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
    # 批 9.5: audit successful create. ``before`` is None (no pre-image).
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DATA_SOURCE_CREATE,
        target_type=audit_service.TARGET_TYPE_DATA_SOURCE,
        target_id=cast(int, source.id),
        before=None,
        after=source,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return source


@router.post(
    "/{source_id}/clone",
    response_model=DataSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
def clone_data_source_endpoint(
    source_id: int,
    request: Request,
    payload: DataSourceCloneRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DataSource:
    """Duplicate a DataSource — read ACL is sufficient.

    Copies connection details (host/port/db/user/password ciphertext
    round-trips under the same Fernet key). The clone's owner is the
    caller, so original ACL does not transfer. Grants / refresh on
    the original row are left untouched.

    Body is optional. If the caller passes a name that collides
    with an existing row, the endpoint returns 409.
    """
    body = payload or DataSourceCloneRequest()
    try:
        original, clone = clone_data_source(db, source_id, user, new_name=body.name)
    except LookupError:
        raise _not_found()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    db.commit()
    db.refresh(clone)
    # 批 9.5: audit the clone. ``before`` = original (with password
    # redacted by _snapshot), ``after`` = clone.
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DATA_SOURCE_CLONE,
        target_type=audit_service.TARGET_TYPE_DATA_SOURCE,
        target_id=cast(int, clone.id),
        before=original,
        after=clone,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    db.commit()
    return clone


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
    request: Request,
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

    # 批 9.5: snapshot before mutation so the audit row carries a
    # before/after diff. ``ds.password`` is already ciphertext from the
    # DB, but _redact() in the service layer still blanks it.
    before_snapshot = audit_service._snapshot(ds)

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
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DATA_SOURCE_UPDATE,
        target_type=audit_service.TARGET_TYPE_DATA_SOURCE,
        target_id=cast(int, ds.id),
        before=before_snapshot,
        after=ds,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return ds


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_data_source(
    source_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a data source. Owner-or-admin only — even a write
    grant does not authorize delete."""
    ds = get_data_source_for_user(db, source_id, user)
    if ds is None or not (is_admin(user) or is_owner(user, ds)):
        raise _not_found()
    # ``reports.data_source_id`` is NOT NULL and the relationship has no
    # cascade, so SQLAlchemy would try to NULL it out on delete and the
    # request would surface as a 500 IntegrityError. Refuse up front and
    # name the blockers so the caller knows what to delete first.
    blocking = (
        db.query(Report.name)
        .filter(Report.data_source_id == source_id)
        .order_by(Report.id.asc())
        .limit(_DELETE_BLOCKER_SAMPLE)
        .all()
    )
    if blocking:
        total = db.query(Report).filter(Report.data_source_id == source_id).count()
        names = ", ".join(repr(row[0]) for row in blocking)
        suffix = f" (and {total - len(blocking)} more)" if total > len(blocking) else ""
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Data source is still used by {total} report(s): {names}{suffix}. "
                "Delete or repoint them first."
            ),
        )
    # 批 9.5: capture the row before delete so we know what was removed.
    before_snapshot = audit_service._snapshot(ds)
    db.delete(ds)
    db.commit()
    # Free any pooled connections that were bound to the now-deleted source.
    evict_engine(source_id)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DATA_SOURCE_DELETE,
        target_type=audit_service.TARGET_TYPE_DATA_SOURCE,
        target_id=source_id,
        before=before_snapshot,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
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
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

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
    request: Request,
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
    # 批 9.5: target_type=data_source_grant so the admin UI can filter
    # "show every grant action" by target_type. before=None because
    # upsert either created a fresh row or refreshed an existing one
    # (the service doesn't return the previous permission).
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DATA_SOURCE_GRANT,
        target_type=audit_service.TARGET_TYPE_DATA_SOURCE_GRANT,
        target_id=cast(int, grant.id),
        before=None,
        after=grant,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
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
    request: Request,
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
    before_snapshot = audit_service._snapshot(grant)
    revoke_grant(db, grant)
    # 批 9.5: capture the grant row before revoke so the audit trail
    # shows who lost access.
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DATA_SOURCE_REVOKE,
        target_type=audit_service.TARGET_TYPE_DATA_SOURCE_GRANT,
        target_id=grant_id,
        before=before_snapshot,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return None

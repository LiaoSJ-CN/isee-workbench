"""HTTP routes for dashboard management (批 14).

Mirrors :mod:`app.routers.report` — same ACL primitives
(``get_dashboard_for_user``, ``can_share_dashboard``, ``ensure_dashboard_visible``),
same uniform 404 for "missing" + "no access", same audit log shape.
The new surface bits are:

* :func:`render_dashboard_html` — server-side aggregate of every
  underlying item into a single HTML page so the frontend iframe
  doesn't have to issue N cross-origin subrequests with admin tokens.
* :func:`batch_update_layout` — one PATCH path for the
  ``react-grid-layout`` ``onLayoutChange`` callback.
* :func:`create_dashboard` / :func:`update_dashboard` — accept an
  optional ``items`` payload so the editor can persist in one round
  trip when seeding a brand-new grid.

DS gate is enforced inside the service-layer helpers; this router
just maps service results to HTTP responses.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.dashboard import Dashboard, DashboardItem
from app.models.dashboard_access import DashboardAccess
from app.models.user import User
from app.schemas.dashboard import (
    DashboardCreate,
    DashboardDetailResponse,
    DashboardDuplicateRequest,
    DashboardItemCreate,
    DashboardItemLayoutRequest,
    DashboardItemResponse,
    DashboardItemUpdate,
    DashboardResponse,
    DashboardShareCreate,
    DashboardShareResponse,
    DashboardUpdate,
)
from app.services import audit as audit_service
from app.services.dashboard import (
    PERMISSION_WRITE,
    can_share_dashboard,
    duplicate_dashboard,
    ensure_dashboard_visible,
    get_dashboard_for_user,
    is_owner_or_admin,
    list_accessible_dashboards,
    list_shares_for_dashboard,
    render_dashboard_html,
    revoke_share,
    upsert_share,
)

router = APIRouter(
    prefix="/dashboards",
    tags=["dashboards"],
    dependencies=[Depends(get_current_user)],
)
def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _dashboard_not_found() -> HTTPException:
    """Uniform 404 — used for both "row missing" and "no access" so
    an unauthorized caller can't probe for the existence of someone
    else's dashboard. Mirrors :func:`app.routers.report._report_not_found`."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Dashboard not found",
    )


# ---------------------------------------------------------------------------
# Dashboard CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=list[DashboardResponse])
def list_dashboards(
    response: Response,
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Dashboard]:
    """List dashboards the caller can see. ACL via
    :func:`app.services.dashboard.list_accessible_dashboards` — admin
    sees all; owner / public / org / grant-holders see the union.
    ``q`` is applied AFTER the ACL filter so an unauthorized caller
    can't probe via filter combinations. ``X-Total-Count`` reports the
    post-ACL total so the frontend can drive a pager.
    """
    rows = list_accessible_dashboards(db, user, q=q)
    response.headers["X-Total-Count"] = str(len(rows))
    return rows[offset : offset + limit]


@router.post(
    "",
    response_model=DashboardDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_dashboard(
    payload: DashboardCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dashboard:
    """Create a new dashboard with optional initial items.

    Mirrors :func:`app.routers.report.create_report` minus the
    data-source gate (a dashboard is a shell until the first
    report/chart item is added — the per-item ACL check happens on
    item create / render).
    """
    existing = (
        db.query(Dashboard).filter(Dashboard.name == payload.name).first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Dashboard named '{payload.name}' already exists",
        )

    dashboard_data = payload.model_dump(exclude={"items"})
    # 批 14: caller becomes the owner; new dashboards default to
    # ``visibility=private`` per the schema default.
    dashboard_data["owner_user_id"] = user.id

    dashboard = Dashboard(**dashboard_data)
    db.add(dashboard)
    db.flush()  # populate id so item FKs can resolve

    for item_data in payload.items:
        item = DashboardItem(dashboard_id=dashboard.id, **item_data.model_dump())
        db.add(item)

    db.commit()
    db.refresh(dashboard)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DASHBOARD_CREATE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD,
        target_id=cast(int, dashboard.id),
        before=None,
        after=dashboard,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return dashboard


@router.get("/{dashboard_id}", response_model=DashboardDetailResponse)
def get_dashboard(
    dashboard_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dashboard:
    """Get a single dashboard by ID with all items. Read ACL — admin,
    owner, public, org, or grant-holder."""
    dashboard = get_dashboard_for_user(db, dashboard_id, user)
    if dashboard is None:
        raise _dashboard_not_found()
    return dashboard


@router.post(
    "/{dashboard_id}/duplicate",
    response_model=DashboardDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_dashboard_endpoint(
    dashboard_id: int,
    request: Request,
    payload: DashboardDuplicateRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dashboard:
    """Duplicate a Dashboard — read ACL is sufficient. The duplicate
    is owned by the caller, starts private, and shares / subscriptions
    are NOT transferred. Items are deep-copied (JSON columns
    included) so post-duplicate edits stay independent.
    """
    body = payload or DashboardDuplicateRequest()
    try:
        original, clone = duplicate_dashboard(
            db,
            dashboard_id,
            user,
            new_name=body.name,
        )
    except LookupError:
        raise _dashboard_not_found()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    db.commit()
    db.refresh(clone)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DASHBOARD_DUPLICATE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD,
        target_id=cast(int, clone.id),
        before=original,
        after=clone,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    db.commit()
    return clone


@router.put("/{dashboard_id}", response_model=DashboardDetailResponse)
def update_dashboard(
    dashboard_id: int,
    payload: DashboardUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Dashboard:
    """Update an existing dashboard. Write ACL — owner or
    write-grantee. Read-only access (public / org / read-grant) gets
    the same uniform 404 so a caller can't probe for write access
    via the PUT endpoint."""
    dashboard = get_dashboard_for_user(
        db, dashboard_id, user, level=PERMISSION_WRITE
    )
    if dashboard is None:
        raise _dashboard_not_found()

    before_snapshot = audit_service._snapshot(dashboard)
    update_data = payload.model_dump(exclude_unset=True)

    if "name" in update_data and update_data["name"] != dashboard.name:
        existing = (
            db.query(Dashboard)
            .filter(Dashboard.name == update_data["name"])
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Dashboard named '{update_data['name']}' already exists",
            )

    for field, value in update_data.items():
        setattr(dashboard, field, value)

    db.commit()
    db.refresh(dashboard)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DASHBOARD_UPDATE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD,
        target_id=cast(int, dashboard.id),
        before=before_snapshot,
        after=dashboard,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return dashboard


@router.delete(
    "/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_dashboard(
    dashboard_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a dashboard and all its items. Owner-or-admin only —
    even a write-grantee cannot delete."""
    dashboard = get_dashboard_for_user(db, dashboard_id, user)
    if dashboard is None or not is_owner_or_admin(user, dashboard):
        raise _dashboard_not_found()

    before_snapshot = audit_service._snapshot(dashboard)
    db.delete(dashboard)
    db.commit()
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DASHBOARD_DELETE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD,
        target_id=dashboard_id,
        before=before_snapshot,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return None


# ---------------------------------------------------------------------------
# Dashboard items
# ---------------------------------------------------------------------------


@router.post(
    "/{dashboard_id}/items",
    response_model=DashboardItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_dashboard_item(
    dashboard_id: int,
    payload: DashboardItemCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DashboardItem:
    """Add a new item to a dashboard. Write ACL on the parent
    dashboard."""
    dashboard = get_dashboard_for_user(
        db, dashboard_id, user, level=PERMISSION_WRITE
    )
    if dashboard is None:
        raise _dashboard_not_found()

    item = DashboardItem(dashboard_id=dashboard_id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DASHBOARD_ITEM_CREATE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD_ITEM,
        target_id=cast(int, item.id),
        before=None,
        after=item,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return item


@router.put(
    "/{dashboard_id}/items/{item_id}",
    response_model=DashboardItemResponse,
)
def update_dashboard_item(
    dashboard_id: int,
    item_id: int,
    payload: DashboardItemUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DashboardItem:
    """Update an existing dashboard item. Write ACL on the parent
    dashboard."""
    dashboard = get_dashboard_for_user(
        db, dashboard_id, user, level=PERMISSION_WRITE
    )
    if dashboard is None:
        raise _dashboard_not_found()

    item = (
        db.query(DashboardItem)
        .filter(
            DashboardItem.id == item_id,
            DashboardItem.dashboard_id == dashboard_id,
        )
        .first()
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard item not found",
        )

    before_snapshot = audit_service._snapshot(item)
    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DASHBOARD_ITEM_UPDATE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD_ITEM,
        target_id=cast(int, item.id),
        before=before_snapshot,
        after=item,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return item


@router.delete(
    "/{dashboard_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_dashboard_item(
    dashboard_id: int,
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a dashboard item. Write ACL on the parent dashboard."""
    dashboard = get_dashboard_for_user(
        db, dashboard_id, user, level=PERMISSION_WRITE
    )
    if dashboard is None:
        raise _dashboard_not_found()

    item = (
        db.query(DashboardItem)
        .filter(
            DashboardItem.id == item_id,
            DashboardItem.dashboard_id == dashboard_id,
        )
        .first()
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dashboard item not found",
        )

    before_snapshot = audit_service._snapshot(item)
    db.delete(item)
    db.commit()
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DASHBOARD_ITEM_DELETE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD_ITEM,
        target_id=item_id,
        before=before_snapshot,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return None


@router.patch("/{dashboard_id}/items/layout")
def batch_update_layout(
    dashboard_id: int,
    payload: DashboardItemLayoutRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Atomically update ``x/y/w/h`` (and optional ``order_index``) for
    a dashboard's items. Write ACL.

    Used by the ``react-grid-layout`` ``onLayoutChange`` callback —
    dragging an item fires one PATCH instead of N PUTs. All
    ``item_id`` values must belong to ``dashboard_id``; any mismatch
    returns 422 so the caller can roll back the optimistic UI update.
    """
    dashboard = get_dashboard_for_user(
        db, dashboard_id, user, level=PERMISSION_WRITE
    )
    if dashboard is None:
        raise _dashboard_not_found()

    item_ids = [e.item_id for e in payload.items]
    rows = (
        db.query(DashboardItem)
        .filter(
            DashboardItem.id.in_(item_ids),
            DashboardItem.dashboard_id == dashboard_id,
        )
        .all()
    )
    if len(rows) != len(set(item_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="All item_ids must belong to this dashboard",
        )

    before_layout = sorted(
        (
            cast(int, row.id),
            cast(int, row.x),
            cast(int, row.y),
            cast(int, row.w),
            cast(int, row.h),
        )
        for row in rows
    )

    by_id = {e.item_id: e for e in payload.items}
    for row in rows:
        entry = by_id[cast(int, row.id)]
        row.x = entry.x
        row.y = entry.y
        row.w = entry.w
        row.h = entry.h
        if entry.order_index is not None:
            row.order_index = entry.order_index

    db.commit()
    after_layout = sorted(
        (
            cast(int, row.id),
            cast(int, row.x),
            cast(int, row.y),
            cast(int, row.w),
            cast(int, row.h),
        )
        for row in rows
    )
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DASHBOARD_ITEM_REORDER,
        target_type=audit_service.TARGET_TYPE_DASHBOARD_ITEM,
        target_id=dashboard_id,
        before={"layout": before_layout},
        after={"layout": after_layout},
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return {"updated": len(rows)}


# ---------------------------------------------------------------------------
# Dashboard preview — server-side aggregate (render lives in services/dashboard.py)
# ---------------------------------------------------------------------------


@router.post("/{dashboard_id}/preview", response_class=HTMLResponse)
def render_dashboard_html_endpoint(
    dashboard_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Server-side aggregate of every item into a single HTML page so
    the frontend iframe can load it directly.

    Read ACL via :func:`ensure_dashboard_visible` — admin / owner /
    public / org / grant-holder. Per-item rendering failures are
    surfaced as inline error placeholders so a partial dashboard
    still renders.

    The render itself lives in :func:`app.services.dashboard.render_dashboard_html`
    so the dispatcher (批 14.4) can reuse the same pipeline without
    importing the router module.
    """
    dashboard = ensure_dashboard_visible(db, user, dashboard_id)
    rendered = render_dashboard_html(db, dashboard, user)
    return HTMLResponse(content=rendered["html"])


# ---------------------------------------------------------------------------
# Shares (mirror routers/report.py)
# ---------------------------------------------------------------------------


@router.post(
    "/{dashboard_id}/shares",
    response_model=DashboardShareResponse,
    status_code=status.HTTP_201_CREATED,
)
def upsert_dashboard_share(
    dashboard_id: int,
    payload: DashboardShareCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DashboardAccess:
    """Grant ``user_id`` read/write on this dashboard. Owner-or-admin
    OR write-grantee — see :func:`app.services.dashboard.can_share_dashboard`.

    Upserts: re-POSTing with the same ``user_id`` updates the
    permission level rather than hitting the unique constraint.
    """
    dashboard = get_dashboard_for_user(db, dashboard_id, user)
    if dashboard is None or not can_share_dashboard(db, user, dashboard):
        raise _dashboard_not_found()

    target = db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    share = upsert_share(
        db,
        dashboard_id=dashboard_id,
        target_user_id=payload.user_id,
        permission=payload.permission,
        granted_by=cast(int, user.id),
    )
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DASHBOARD_SHARE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD_SHARE,
        target_id=cast(int, share.id),
        before=None,
        after=share,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return share


@router.get(
    "/{dashboard_id}/shares",
    response_model=list[DashboardShareResponse],
)
def list_dashboard_shares(
    dashboard_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DashboardAccess]:
    """List every share on this dashboard. Owner-or-admin only — a
    read grantee cannot see who else has access."""
    dashboard = get_dashboard_for_user(db, dashboard_id, user)
    if dashboard is None or not is_owner_or_admin(user, dashboard):
        raise _dashboard_not_found()
    return list_shares_for_dashboard(db, dashboard_id)


@router.delete(
    "/{dashboard_id}/shares/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_dashboard_share(
    dashboard_id: int,
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Revoke a share by user_id. Owner-or-admin on the parent
    dashboard.

    Uses the ``/shares/{user_id}`` path so an unauthorized caller
    can't probe for share rows they don't own — the lookup is keyed
    on the dashboard ACL check first.
    """
    dashboard = get_dashboard_for_user(db, dashboard_id, user)
    if dashboard is None or not can_share_dashboard(db, user, dashboard):
        raise _dashboard_not_found()

    share = (
        db.query(DashboardAccess)
        .filter(
            DashboardAccess.dashboard_id == dashboard_id,
            DashboardAccess.user_id == user_id,
        )
        .first()
    )
    if share is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found",
        )
    before_snapshot = audit_service._snapshot(share)
    revoke_share(db, share)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DASHBOARD_REVOKE,
        target_type=audit_service.TARGET_TYPE_DASHBOARD_SHARE,
        target_id=cast(int, share.id),
        before=before_snapshot,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return None

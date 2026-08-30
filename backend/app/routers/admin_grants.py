"""Centralised grant / revoke endpoints (batch ``user-management``
Stage 2).

Surface:

- ``GET    /admin/grants?resource_type=&resource_id=`` — every grant
  pointing at one resource (admin only).
- ``POST   /admin/grants`` — grant a user access to any resource
  type from the admin page (idempotent; same body shape regardless
  of resource type).
- ``DELETE /admin/grants/{resource_type}/{grant_id}`` — revoke any
  grant by its underlying row id.

All endpoints sit behind :data:`app.deps.admin_required` — the
admin role is the *only* path that can grant on behalf of someone
else or revoke someone else's grant. The per-resource share
endpoints (``POST /data-sources/{id}/grants``,
``POST /reports/{id}/shares``, ``POST /dashboards/{id}/shares``)
still work for the owner / write-grantee path; this router is a
sibling that lets the admin page bypass the per-resource ACL check
to grant/revoke at the system boundary.

Audit row conventions: the centralised router emits the **same**
``data_source.grant`` / ``report.share`` / ``dashboard.share``
actions as the per-resource endpoints so the audit-page filter
("show every share event this week") keeps working across both
entry points. ``actor_user_id`` is the admin — operators reading
the audit log can filter on that for "what did the admin do"
without scanning the ``after`` payloads.

URL shape consistency: the existing per-resource revoke endpoints
use ``/shares/{share_id}`` for DS/Report and
``/dashboards/{id}/shares/{user_id}`` for Dashboard. This router
uses a uniform ``/admin/grants/{resource_type}/{grant_id}`` shape
so a single client-side handler drives all three resource types.
The per-resource URL inconsistency is flagged for a future
cleanup batch (see plan §"Out of scope").
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_required
from app.models.user import User
from app.schemas.user import (
    RESOURCE_TYPE_DASHBOARD,
    RESOURCE_TYPE_DATA_SOURCE,
    RESOURCE_TYPE_REPORT,
    AdminGrantCreate,
    GrantSummaryItem,
)
from app.services import audit as audit_service
from app.services import user_admin

router = APIRouter(prefix="/admin/grants", tags=["admin"])


def _client_ip(request: Request) -> str:
    """Peer IP for the audit log. ``ProxyHeadersMiddleware`` has
    already rewritten ``request.client.host`` when the request came
    through a trusted proxy, so this is the real client IP."""
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Read — list grants on one resource
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[GrantSummaryItem],
    summary="List every grant on one resource (admin only)",
)
def list_grants_admin(
    resource_type: str = Query(
        ...,
        description="One of data_source / report / dashboard",
    ),
    resource_id: int = Query(..., ge=1),
    db: Session = Depends(get_db),
    _admin: User = Depends(admin_required),
) -> list[GrantSummaryItem]:
    """Return the union of grants on *resource_id* of *resource_type*.

    The query is intentionally keyed by ``resource_type`` +
    ``resource_id`` (not by the access-row id) so the admin UI can
    render the share list inline when picking a resource in the
    ``+集中授权`` modal. Returns an empty list for a missing
    resource — matches :func:`list_resource_grants` so the API
    never 404s for a typo'd resource id.
    """
    if resource_type not in (
        RESOURCE_TYPE_DATA_SOURCE,
        RESOURCE_TYPE_REPORT,
        RESOURCE_TYPE_DASHBOARD,
    ):
        # 422-shaped rejection — keeps the admin page's filter UI
        # honest when the user types a value not in the dropdown.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"resource_type must be one of "
                f"{RESOURCE_TYPE_DATA_SOURCE!r}, "
                f"{RESOURCE_TYPE_REPORT!r}, "
                f"{RESOURCE_TYPE_DASHBOARD!r}; got {resource_type!r}"
            ),
        )
    return user_admin.list_resource_grants(
        db, resource_type=resource_type, resource_id=resource_id
    )


# ---------------------------------------------------------------------------
# Grant (create-or-update, idempotent)
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=GrantSummaryItem,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a user access to a resource (admin only)",
)
def create_grant_admin(
    payload: AdminGrantCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_required),
) -> GrantSummaryItem:
    """Centralised grant — admin grants *target_user_id* access to
    *resource_id* of *resource_type*.

    Idempotent: re-POSTing the same ``(resource_type, resource_id,
    target_user_id)`` updates the permission level (mirrors the
    per-resource :func:`app.services.data_source.upsert_grant`).

    Audit action: inherited from the per-resource endpoints — the
    admin path emits the same ``data_source.grant`` /
    ``report.share`` / ``dashboard.share`` so the audit-page
    filter doesn't need a separate "centralised" entry.
    """
    try:
        item = user_admin.centralized_grant(
            db,
            actor=admin,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            target_user_id=payload.target_user_id,
            permission=payload.permission,
        )
    except LookupError as exc:
        # Missing user or missing resource — the per-resource share
        # endpoints answer 404 for these; mirror that shape here.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        # ``upsert_grant`` raises ValueError on an unknown permission
        # (defence-in-depth — the Pydantic Literal already rejects
        # bad strings). Translate to 422 so the client gets a clean
        # error envelope.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    audit_service.log(
        db,
        actor_user_id=cast(int, admin.id),
        action=_grant_action_for(payload.resource_type),
        target_type=_grant_target_type_for(payload.resource_type),
        target_id=item.grant_id,
        before=None,
        after=item.model_dump(mode="json"),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return item


# ---------------------------------------------------------------------------
# Revoke (hard delete)
# ---------------------------------------------------------------------------


@router.delete(
    "/{resource_type}/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a grant by id (admin only)",
)
def revoke_grant_admin(
    resource_type: str,
    grant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_required),
) -> None:
    """Centralised revoke — admin revokes any grant by its underlying
    access-row primary key. Mirrors the per-resource
    :func:`app.services.data_source.revoke_grant` etc. so the
    admin path doesn't need its own delete implementation.

    Audit action: ``data_source.revoke`` / ``report.revoke`` /
    ``dashboard.revoke`` — same as the per-resource endpoints. The
    ``before`` snapshot comes from the row pre-delete (returned by
    :func:`centralized_revoke`) so the audit trail captures the
    permission level that was revoked.
    """
    if resource_type not in (
        RESOURCE_TYPE_DATA_SOURCE,
        RESOURCE_TYPE_REPORT,
        RESOURCE_TYPE_DASHBOARD,
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown resource_type {resource_type!r}",
        )

    try:
        before_snapshot = user_admin.centralized_revoke(
            db, resource_type=resource_type, grant_id=grant_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    audit_service.log(
        db,
        actor_user_id=cast(int, admin.id),
        action=_revoke_action_for(resource_type),
        target_type=_grant_target_type_for(resource_type),
        target_id=grant_id,
        before=before_snapshot.model_dump(mode="json"),
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return None


# ---------------------------------------------------------------------------
# Audit action / target_type dispatch helpers
# ---------------------------------------------------------------------------


def _grant_action_for(resource_type: str) -> str:
    """Return the per-resource audit action used by ``POST
    /admin/grants`` — keeps the centralised endpoint's audit rows
    indistinguishable from the per-resource share endpoints so the
    audit-page filter doesn't need a separate "centralised" entry.
    """
    if resource_type == RESOURCE_TYPE_DATA_SOURCE:
        return audit_service.ACTION_DATA_SOURCE_GRANT
    if resource_type == RESOURCE_TYPE_REPORT:
        return audit_service.ACTION_REPORT_SHARE
    if resource_type == RESOURCE_TYPE_DASHBOARD:
        return audit_service.ACTION_DASHBOARD_SHARE
    raise ValueError(f"unknown resource_type {resource_type!r}")


def _revoke_action_for(resource_type: str) -> str:
    """Return the per-resource audit action used by ``DELETE
    /admin/grants/{resource_type}/{grant_id}``.
    """
    if resource_type == RESOURCE_TYPE_DATA_SOURCE:
        return audit_service.ACTION_DATA_SOURCE_REVOKE
    if resource_type == RESOURCE_TYPE_REPORT:
        return audit_service.ACTION_REPORT_REVOKE
    if resource_type == RESOURCE_TYPE_DASHBOARD:
        return audit_service.ACTION_DASHBOARD_REVOKE
    raise ValueError(f"unknown resource_type {resource_type!r}")


def _grant_target_type_for(resource_type: str) -> str:
    """Return the per-resource audit target type for a grant /
    revoke row."""
    if resource_type == RESOURCE_TYPE_DATA_SOURCE:
        return audit_service.TARGET_TYPE_DATA_SOURCE_GRANT
    if resource_type == RESOURCE_TYPE_REPORT:
        return audit_service.TARGET_TYPE_REPORT_SHARE
    if resource_type == RESOURCE_TYPE_DASHBOARD:
        return audit_service.TARGET_TYPE_DASHBOARD_SHARE
    raise ValueError(f"unknown resource_type {resource_type!r}")


__all__ = ["router"]

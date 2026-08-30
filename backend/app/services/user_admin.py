"""User admin service helpers (batch ``user-management``).

Stage 1 added five user-CRUD operations backing ``/admin/users``:

- :func:`create_user` — admin creates a new user, hashing the password
  via :func:`app.services.password.hash_password`. Duplicate username
  raises ``ValueError`` (router → 409).
- :func:`list_users` — admin lists users with ``role`` / ``disabled``
  / ``q`` (username substring) filters and ``limit`` / ``offset``
  pagination. Filter order is ``disabled → role → q`` so a query that
  asks "active editors named alice" is cheap (the disabled index
  narrows first).
- :func:`update_user` — admin PATCHes ``role`` and/or ``disabled``.
  Enforces **self-protection**: an admin cannot demote or disable
  themselves (raises ``PermissionError`` → router → 403).
- :func:`disable_user` — convenience wrapper for ``update_user``
  that flips ``disabled=True``. Same self-protection rule; explicit
  endpoint so the router's audit action
  (``ACTION_USER_DISABLE``) is unambiguous.
- :func:`reset_password` — two-mode password reset
  (admin-supplied / server-generated). Mirrors the DataSource rotation
  flow at ``routers/admin_data_sources.py:67`` so admins use the same
  mental model across resources. Returns the plaintext for the
  server-generated case (caller echoes it ONCE in the response; never
  persisted).

Stage 2 added ACL aggregation + centralised grant/revoke helpers:

- :func:`list_user_grants` — every grant the user holds across
  DataSource + Report + Dashboard, joined to the parent resource for
  ``resource_name`` and to ``User`` for ``granted_by_username``.
- :func:`list_resource_grants` — symmetric helper: every grant
  pointing at one resource (used by ``GET /admin/grants``).
- :func:`centralized_grant` — admin grants a user access to any
  resource type. Switches over the per-resource ``upsert_grant`` /
  ``upsert_share`` so ACL invariants (idempotency, ``granted_by``
  refresh, audit action) stay in one place per resource.
- :func:`centralized_revoke` — admin revokes any grant by its
  underlying row id.

The ``actor`` parameter is propagated to ``granted_by`` on grant and
recorded in the audit row by the router (matching the per-resource
share endpoints at ``routers/data_source.py`` /
``routers/report.py`` / ``routers/dashboard.py``).
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from typing import cast

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.dashboard import Dashboard
from app.models.dashboard_access import DashboardAccess
from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.report import Report
from app.models.report_access import ReportAccess
from app.models.user import ROLE_ADMIN, User
from app.schemas.user import GrantSummaryItem
from app.services import dashboard as dashboard_service
from app.services import data_source as data_source_service
from app.services import report as report_service
from app.services.password import hash_password

# ---------------------------------------------------------------------------
# Self-protection (batch user-management Stage 1)
# ---------------------------------------------------------------------------

# Last-admin check: ``count_admin_users(db) <= 1`` means the actor is
# the only admin left — disabling or demoting them would lock the
# system out. The router surfaces this as 403 ("Insufficient
# privileges for this action") so the API client knows it's an
# authorization failure, not a bad request.


def _count_admins(db: Session) -> int:
    """Return the number of currently-active admin rows.

    Used by the self-protection rule below. Counts only
    ``disabled=False`` so a disabled admin doesn't keep the count
    above 1 once they're soft-deleted.
    """
    return (
        db.query(func.count(User.id))
        .filter(User.role == ROLE_ADMIN, User.disabled.is_(False))
        .scalar()
        or 0
    )


def _check_self_protection(
    db: Session, *, actor: User, target: User, role: str | None, disabled: bool | None
) -> None:
    """Raise ``PermissionError`` if *actor* is locking themselves out.

    Two ways an admin can lock themselves out:

    1. Demoting themselves from ``admin`` to a non-admin role.
    2. Disabling themselves (``disabled=True``).

    Both are rejected when ``actor.id == target.id`` AND
    ``_count_admins(db) == 1`` (the actor is the only admin). If
    there's another active admin, the change is allowed — admins
    shouldn't be permanently locked to their own role.
    """
    if actor.id != target.id:
        return
    if _count_admins(db) > 1:
        return
    demoting = role is not None and role != ROLE_ADMIN
    disabling = disabled is True
    if demoting or disabling:
        raise PermissionError(
            "cannot demote or disable the last remaining admin"
        )


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def create_user(db: Session, *, username: str, password: str, role: str) -> User:
    """Create a new user row with a bcrypt-hashed password.

    Raises ``ValueError`` if the username is already taken — the DB's
    unique constraint is the safety net, but checking first lets the
    router return a clean 409 instead of relying on the
    ``IntegrityError`` translation path. ``org_id`` is left ``NULL``;
    the admin can stamp it later via a one-off migration if multi-
    tenant ever ships.
    """
    existing = db.query(User).filter(User.username == username).first()
    if existing is not None:
        raise ValueError(f"username {username!r} already exists")

    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def list_users(
    db: Session,
    *,
    role: str | None = None,
    disabled: bool | None = None,
    q: str | None = None,
    limit: int,
    offset: int,
) -> tuple[Sequence[User], int]:
    """Return ``(rows, total)`` for the admin user list.

    Filter ordering matters: ``disabled → role → q``. The ``disabled``
    predicate is the most selective in practice (admins care about
    "active vs disabled" first), so it narrows first; the username
    ``LIKE`` is last because it can't use a regular index (substring
    match).

    ``q`` matches both ``username`` and ``role`` substrings so the
    admin can quickly filter "all viewers" via the role column without
    needing a separate ``role`` dropdown. (The explicit ``role``
    filter is kept for symmetry with the existing list endpoints and
    for UI dropdown-driven filtering.)
    """
    query = db.query(User)

    if disabled is not None:
        query = query.filter(User.disabled.is_(disabled))

    if role is not None:
        query = query.filter(User.role == role)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(User.username.ilike(like), User.role.ilike(like))
        )

    total = query.count()
    rows = (
        query.order_by(User.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows, total


def update_user(
    db: Session,
    *,
    actor: User,
    target: User,
    role: str | None,
    disabled: bool | None,
) -> User:
    """Apply ``role`` and/or ``disabled`` patches to *target*.

    Raises ``PermissionError`` if the change would lock the actor out
    (see :func:`_check_self_protection`). The caller (router) commits
    after the audit row lands — matches the project convention.
    """
    _check_self_protection(db, actor=actor, target=target, role=role, disabled=disabled)
    if role is not None:
        target.role = role
    if disabled is not None:
        target.disabled = disabled
    db.commit()
    db.refresh(target)
    return target


def disable_user(db: Session, *, actor: User, target: User) -> User:
    """Soft-delete via ``disabled=True``.

    Same self-protection rule as :func:`update_user`. Hard-delete is
    intentionally not exposed: ``audit_log.actor_user_id`` uses
    ``ondelete=SET NULL``, so a hard-delete would silently drop the
    audit trail's FK readability for actions performed by that user.
    """
    if not target.disabled:
        _check_self_protection(
            db, actor=actor, target=target, role=None, disabled=True
        )
        target.disabled = True
        db.commit()
        db.refresh(target)
    return target


def reset_password(
    db: Session,
    *,
    target: User,
    new_password: str | None,
) -> tuple[User, str, str]:
    """Reset *target*'s password; return ``(user, plaintext, method)``.

    Two modes (mirrors :func:`app.routers.admin_data_sources.
    rotate_data_source_password`):

    - ``admin_supplied`` — caller passed a non-empty ``new_password``.
      Persisted verbatim (after bcrypt hashing). The plaintext is
      **not** returned in the response — the admin already knows it.
    - ``server_generated`` — caller passed ``None`` or empty. Server
      generates a 24-char URL-safe random password
      (:func:`secrets.token_urlsafe`). Plaintext returned ONCE; the
      admin must copy it immediately because the server only stores
      the hash.
    """
    supplied = (new_password or "").strip()
    if supplied:
        plaintext = supplied
        method = "admin_supplied"
    else:
        plaintext = secrets.token_urlsafe(18)
        method = "server_generated"

    target.password_hash = hash_password(plaintext)
    db.commit()
    db.refresh(target)
    return target, plaintext, method


# ---------------------------------------------------------------------------
# ACL aggregation + centralised grant/revoke (batch user-management Stage 2)
# ---------------------------------------------------------------------------

# Resource-type → (parent-resource-id-column-on-access-row) — used by
# :func:`_build_summary_for_resource_grant` to build a uniform
# :class:`GrantSummaryItem` from the per-type access row. Mirrors the
# three ``upsert_*`` / ``revoke_*`` helpers dispatched by
# :func:`centralized_grant` / :func:`centralized_revoke`.
#
# Keep this dict in sync with :data:`app.schemas.user.ALL_RESOURCE_TYPES` —
# the admin grant router validates ``resource_type`` against that
# Literal before dispatching here, so an unknown key shouldn't be
# reachable in practice.


def _user_by_id(db: Session, user_id: int | None) -> str | None:
    """Return *user_id*'s username or ``None``.

    Used to project ``granted_by`` (an integer FK) into the human-
    friendly ``granted_by_username`` column on
    :class:`GrantSummaryItem`. Returns ``None`` for both
    "no FK set" (granted_by is NULL on legacy rows) and "FK set but
    row missing" (deleted granter) — the audit trail treats both as
    "unknown actor".
    """
    if user_id is None:
        return None
    user = db.get(User, user_id)
    return user.username if user is not None else None


def _ds_summary(db: Session, row: DataSourceAccess) -> GrantSummaryItem:
    """Project a :class:`DataSourceAccess` row into a summary item."""
    ds = db.get(DataSource, row.data_source_id)
    return GrantSummaryItem(
        resource_type="data_source",
        resource_id=cast(int, row.data_source_id),
        resource_name=ds.name if ds is not None else None,
        grant_id=cast(int, row.id),
        permission=row.permission,  # type: ignore[arg-type]
        granted_by=row.granted_by,
        granted_by_username=_user_by_id(db, row.granted_by),
        created_at=row.created_at,
    )


def _report_summary(db: Session, row: ReportAccess) -> GrantSummaryItem:
    """Project a :class:`ReportAccess` row into a summary item."""
    report = db.get(Report, row.report_id)
    return GrantSummaryItem(
        resource_type="report",
        resource_id=cast(int, row.report_id),
        resource_name=report.name if report is not None else None,
        grant_id=cast(int, row.id),
        permission=row.permission,  # type: ignore[arg-type]
        granted_by=row.granted_by,
        granted_by_username=_user_by_id(db, row.granted_by),
        created_at=row.created_at,
    )


def _dashboard_summary(db: Session, row: DashboardAccess) -> GrantSummaryItem:
    """Project a :class:`DashboardAccess` row into a summary item."""
    dashboard = db.get(Dashboard, row.dashboard_id)
    return GrantSummaryItem(
        resource_type="dashboard",
        resource_id=cast(int, row.dashboard_id),
        resource_name=dashboard.name if dashboard is not None else None,
        grant_id=cast(int, row.id),
        permission=row.permission,  # type: ignore[arg-type]
        granted_by=row.granted_by,
        granted_by_username=_user_by_id(db, row.granted_by),
        created_at=row.created_at,
    )


def list_user_grants(db: Session, user_id: int) -> list[GrantSummaryItem]:
    """Every grant pointing at *user_id* across all three resource types.

    Three indexed lookups (``UniqueConstraint(resource_id, user_id)``
    plus the per-user reverse index — both added in 批 9.3 / 9.4 /
    14) so this is fast even when the system has thousands of grants
    on unrelated resources. The results are concatenated in stable
    order (data_source, report, dashboard) so the admin UI can group
    by tab without an extra client-side sort.
    """
    if db.get(User, user_id) is None:
        return []

    ds_rows: list[DataSourceAccess] = (
        db.query(DataSourceAccess)
        .filter(DataSourceAccess.user_id == user_id)
        .order_by(DataSourceAccess.id)
        .all()
    )
    report_rows: list[ReportAccess] = (
        db.query(ReportAccess)
        .filter(ReportAccess.user_id == user_id)
        .order_by(ReportAccess.id)
        .all()
    )
    dashboard_rows: list[DashboardAccess] = (
        db.query(DashboardAccess)
        .filter(DashboardAccess.user_id == user_id)
        .order_by(DashboardAccess.id)
        .all()
    )

    return [
        _ds_summary(db, row) for row in ds_rows
    ] + [
        _report_summary(db, row) for row in report_rows
    ] + [
        _dashboard_summary(db, row) for row in dashboard_rows
    ]


def list_resource_grants(
    db: Session,
    *,
    resource_type: str,
    resource_id: int,
) -> list[GrantSummaryItem]:
    """Every grant pointing at *resource_id* of *resource_type*.

    Thin switch over the per-resource list helpers in
    :mod:`app.services.data_source` / :mod:`app.services.report` /
    :mod:`app.services.dashboard`. ``resource_type`` is validated by
    the router's Pydantic Literal before this helper sees it; an
    unknown value falls through to ``[]`` so the API returns a clean
    empty list rather than a 500.
    """
    if resource_type == "data_source":
        ds_rows = data_source_service.list_grants_for_data_source(db, resource_id)
        return [_ds_summary(db, row) for row in ds_rows]
    if resource_type == "report":
        report_rows = report_service.list_shares_for_report(db, resource_id)
        return [_report_summary(db, row) for row in report_rows]
    if resource_type == "dashboard":
        dashboard_rows = dashboard_service.list_shares_for_dashboard(db, resource_id)
        return [_dashboard_summary(db, row) for row in dashboard_rows]
    return []


def _require_user_or_404(db: Session, user_id: int) -> User:
    """Return the user row or raise ``LookupError`` (router → 404).

    Used by :func:`centralized_grant` so the admin endpoint mirrors
    the per-resource share endpoints' 404 behaviour for a missing
    target user — without this the FK insert would surface as a 500
    via ``IntegrityError``.
    """
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")
    return user


def _require_resource_or_404(
    db: Session, *, resource_type: str, resource_id: int
) -> None:
    """Raise ``LookupError`` if the parent resource row is missing.

    Mirrors the 404-on-missing-resource behaviour of the per-resource
    share endpoints so the centralised endpoint doesn't reveal a
    missing target via a 500 from a stale ``upsert_grant`` call.
    """
    if resource_type == "data_source" and db.get(DataSource, resource_id) is None:
        raise LookupError(f"DataSource {resource_id} not found")
    if resource_type == "report" and db.get(Report, resource_id) is None:
        raise LookupError(f"Report {resource_id} not found")
    if resource_type == "dashboard" and db.get(Dashboard, resource_id) is None:
        raise LookupError(f"Dashboard {resource_id} not found")


def centralized_grant(
    db: Session,
    *,
    actor: User,
    resource_type: str,
    resource_id: int,
    target_user_id: int,
    permission: str,
) -> GrantSummaryItem:
    """Grant *target_user_id* access to *resource_id* of *resource_type*.

    Switches over the three ``upsert_*`` helpers so the ACL invariants
    (idempotency on ``(resource_id, user_id)`` unique constraint,
    refresh of ``granted_by``, permission validation) live in one
    place per resource — :func:`centralized_grant` is a dispatch
    layer, not a second source of truth.

    Returns the post-upsert access row projected into a
    :class:`GrantSummaryItem` so the caller can both render the row
    and snapshot it for the audit log without a second SELECT.
    """
    _require_resource_or_404(db, resource_type=resource_type, resource_id=resource_id)
    target = _require_user_or_404(db, target_user_id)
    actor_id = cast(int, actor.id)
    target_id = cast(int, target.id)

    if resource_type == "data_source":
        ds_access_row = data_source_service.upsert_grant(
            db,
            data_source_id=resource_id,
            target_user_id=target_id,
            permission=permission,
            granted_by=actor_id,
        )
        return _ds_summary(db, ds_access_row)
    if resource_type == "report":
        report_access_row = report_service.upsert_share(
            db,
            report_id=resource_id,
            target_user_id=target_id,
            permission=permission,
            granted_by=actor_id,
        )
        return _report_summary(db, report_access_row)
    if resource_type == "dashboard":
        dashboard_access_row = dashboard_service.upsert_share(
            db,
            dashboard_id=resource_id,
            target_user_id=target_id,
            permission=permission,
            granted_by=actor_id,
        )
        return _dashboard_summary(db, dashboard_access_row)
    raise LookupError(f"unknown resource_type {resource_type!r}")


def centralized_revoke(
    db: Session,
    *,
    resource_type: str,
    grant_id: int,
) -> GrantSummaryItem:
    """Revoke the access row whose primary key is *grant_id*.

    Looks up the row by id in the access table that matches
    *resource_type*, snapshots it (for the audit row's ``before``)
    and hard-deletes via the per-resource ``revoke_*`` helper so the
    DELETE path stays in one place per resource.

    Raises ``LookupError`` (router → 404) for both an unknown
    *resource_type* and a missing *grant_id* so the centralised
    endpoint mirrors the per-resource revoke endpoints' 404 shape.
    """
    if resource_type == "data_source":
        ds_access_row = db.get(DataSourceAccess, grant_id)
        if ds_access_row is None:
            raise LookupError(f"DataSourceAccess {grant_id} not found")
        snapshot = _ds_summary(db, ds_access_row)
        data_source_service.revoke_grant(db, ds_access_row)
        return snapshot
    if resource_type == "report":
        report_access_row = db.get(ReportAccess, grant_id)
        if report_access_row is None:
            raise LookupError(f"ReportAccess {grant_id} not found")
        snapshot = _report_summary(db, report_access_row)
        report_service.revoke_share(db, report_access_row)
        return snapshot
    if resource_type == "dashboard":
        dashboard_access_row = db.get(DashboardAccess, grant_id)
        if dashboard_access_row is None:
            raise LookupError(f"DashboardAccess {grant_id} not found")
        snapshot = _dashboard_summary(db, dashboard_access_row)
        dashboard_service.revoke_share(db, dashboard_access_row)
        return snapshot
    raise LookupError(f"unknown resource_type {resource_type!r}")


__all__ = [
    "centralized_grant",
    "centralized_revoke",
    "create_user",
    "disable_user",
    "list_resource_grants",
    "list_user_grants",
    "list_users",
    "reset_password",
    "update_user",
]

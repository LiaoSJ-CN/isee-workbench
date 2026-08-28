"""Per-user Dashboard ACL helpers (批 14).

Mirrors :mod:`app.services.report` ACL primitives — same 404-isolation
semantics, same ownership + visibility + grant model. The new wrinkle
is the **DS gate**: a dashboard can reference both Reports (whose
``data_source_id`` is a transitive dependency) and DataSources
directly (chart items). :func:`_check_data_source_gate` walks the
dashboard's items and confirms the caller can access every referenced
DS — admin / dashboard-owner short-circuits; non-admin must pass
every check.

Chart items reuse :class:`ReportItem`'s SQL builder via
:func:`execute_dashboard_chart` — we assemble a transient
:class:`ReportItem` (not committed) and pass it to
:func:`app.services.report_generator.query_builder.build_query`, so
the SQL validator chain stays single-sourced.
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy.orm import Session

from app.models.dashboard import Dashboard, DashboardItem
from app.models.dashboard_access import DashboardAccess
from app.models.report import (
    VISIBILITY_ORG,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    Report,
    ReportItem,
)
from app.models.user import ROLE_ADMIN, User
from app.services.data_source import (
    PERMISSION_READ,
    PERMISSION_WRITE,
    get_data_source_for_user,
    is_admin,
)

__all__ = [
    "ALL_VISIBILITIES",
    "PERMISSION_READ",
    "PERMISSION_WRITE",
    "VISIBILITY_ORG",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_PUBLIC",
    "can_share_dashboard",
    "duplicate_dashboard",
    "ensure_dashboard_visible",
    "get_dashboard_for_user",
    "is_dashboard_visible_to_user",
    "is_owner",
    "is_owner_or_admin",
    "list_accessible_dashboards",
    "list_shares_for_dashboard",
    "revoke_share",
    "upsert_share",
]


ALL_VISIBILITIES: tuple[str, ...] = (
    VISIBILITY_PUBLIC,
    VISIBILITY_PRIVATE,
    VISIBILITY_ORG,
)


def is_owner(user: User, dashboard: Dashboard) -> bool:
    """True when ``dashboard.owner_user_id`` matches the user. ``False``
    for ``dashboard.owner_user_id IS NULL`` (orphan-row case — the
    dashboard isn't owned by anyone yet, e.g. legacy import)."""
    return (
        dashboard.owner_user_id is not None
        and dashboard.owner_user_id == user.id
    )


def is_dashboard_visible_to_user(dashboard: Dashboard, user: User) -> bool:
    """Pure visibility ACL for a dashboard — no DS gate layered.

    Used by :func:`get_dashboard_for_user` after the DS gate has
    passed. Mirrors :func:`app.services.report._is_template_visible_to_user`.

    Rules (admin short-circuits in the caller; this is non-admin):

    * ``public`` — anyone can see.
    * ``org`` — both ``dashboard.org_id`` and ``user.org_id`` must be
      non-null and equal. NULL on either side is a cross-tenant
      mismatch.
    * ``private`` — only the dashboard owner.

    Admin is checked by the caller via :func:`is_admin`.
    """
    if dashboard.visibility == VISIBILITY_PUBLIC:
        return True
    if dashboard.visibility == VISIBILITY_ORG:
        return (
            dashboard.org_id is not None
            and user.org_id is not None
            and dashboard.org_id == user.org_id
        )
    # ``private`` (and any unknown value — defensive default).
    return dashboard.owner_user_id == user.id


def _grant_for(
    db: Session, dashboard_id: int, user_id: int
) -> DashboardAccess | None:
    """Single-shot lookup of one user's grant on one dashboard."""
    return (
        db.query(DashboardAccess)
        .filter(
            DashboardAccess.dashboard_id == dashboard_id,
            DashboardAccess.user_id == user_id,
        )
        .first()
    )


def _check_data_source_gate(
    db: Session, dashboard: Dashboard, user: User
) -> bool:
    """DS gate — every DS referenced by the dashboard must be visible
    to ``user`` (admin / dashboard-owner passes; non-admin non-owner
    must pass every check).

    Walks the dashboard's items, collecting unique
    ``data_source_id`` values:

    * ``item_type='report'`` → resolve ``report.data_source_id``,
    * ``item_type='chart'`` → use ``item.data_source_id`` directly,
    * ``item_type='text'`` → no DS dependency.

    Returns ``True`` when admin / owner or every check passes. False
    on the first failure.
    """
    if is_admin(user):
        return True
    if is_owner(user, dashboard):
        return True
    checked: set[int] = set()
    for item in dashboard.items:
        ds_id: int | None = None
        if item.item_type == "report" and item.report_id is not None:
            report = db.get(Report, item.report_id)
            if report is not None:
                ds_id = report.data_source_id
        elif item.item_type == "chart" and item.data_source_id is not None:
            ds_id = item.data_source_id
        # text items have no DS dependency.
        if ds_id is None or ds_id in checked:
            continue
        checked.add(ds_id)
        if get_data_source_for_user(db, ds_id, user) is None:
            return False
    return True


def get_dashboard_for_user(
    db: Session,
    dashboard_id: int | None,
    user: User,
    *,
    level: str = PERMISSION_READ,
) -> Dashboard | None:
    """Single-dashboard lookup, ACL-gated.

    Returns the :class:`Dashboard` row when *all* of these hold:

    1. The dashboard exists.
    2. Every referenced data source is visible to ``user`` (DS gate).
    3. The caller has dashboard-level access:

       * admin → everything,
       * owner → anything,
       * explicit :class:`DashboardAccess` grant at ``>= level``,
       * public visibility + read request,
       * org-tier visibility + ``org_id`` match + read request.

    Returns ``None`` for both "row missing" and "no access" so the
    caller answers a uniform 404 — same cross-user isolation as the
    Report helper.
    """
    if dashboard_id is None:
        return None
    dashboard = db.get(Dashboard, dashboard_id)
    if dashboard is None:
        return None

    # DS gate — admin / owner short-circuit inside the helper.
    if not _check_data_source_gate(db, dashboard, user):
        return None

    if is_admin(user):
        return dashboard
    if is_owner(user, dashboard):
        return dashboard

    # Read access: public OR org-tier (with org match) OR explicit grant.
    if level == PERMISSION_READ:
        if is_dashboard_visible_to_user(dashboard, user):
            return dashboard
        assert dashboard.id is not None  # freshly loaded from DB
        assert user.id is not None
        grant = _grant_for(db, dashboard.id, user.id)
        if grant is not None:
            return dashboard
        return None

    # Write access requires an explicit write grant — public visibility
    # never grants write.
    assert dashboard.id is not None  # freshly loaded from DB
    assert user.id is not None
    grant = _grant_for(db, dashboard.id, user.id)
    if grant is not None and grant.permission == PERMISSION_WRITE:
        return dashboard
    return None


def list_accessible_dashboards(
    db: Session,
    user: User,
    *,
    q: str | None = None,
) -> list[Dashboard]:
    """All dashboards the user can see.

    Admin sees everything. Non-admin sees the union of:

    * ``owner_user_id = me``,
    * ``visibility = public`` (read-only by definition),
    * ``visibility = org`` AND ``org_id`` matches the caller's (NULL on
      either side is a cross-tenant mismatch, treated as no-access),
    * any :class:`DashboardAccess` row pointing at me.

    The optional ``q`` filter is a name ILIKE match; applied after the
    ACL filter so an unauthorized caller can't use it as a probe.

    Note: this helper does NOT apply the DS gate. The dashboard list
    endpoint either (a) omits the DS gate (acceptable for a list
    view that shows names + metadata but blocks per-item rendering),
    or (b) filters post-hoc. Sub-batch 2 picks the policy — for now
    we keep the helper consistent with ``list_accessible_reports`` and
    apply the gate inside the read endpoint instead.
    """
    if is_admin(user):
        base = db.query(Dashboard)
    else:
        owner_q = db.query(Dashboard.id).filter(Dashboard.owner_user_id == user.id)
        granted_q = db.query(DashboardAccess.dashboard_id).filter(
            DashboardAccess.user_id == user.id
        )
        public_q = db.query(Dashboard.id).filter(
            Dashboard.visibility == VISIBILITY_PUBLIC
        )
        if user.org_id is not None:
            org_q = db.query(Dashboard.id).filter(
                Dashboard.visibility == VISIBILITY_ORG,
                Dashboard.org_id.isnot(None),
                Dashboard.org_id == user.org_id,
            )
        else:
            # Always-empty subquery — org dashboards never match a
            # user without an org id. ``id == -1`` is a cheap no-op
            # predicate since ``dashboards.id`` is always positive.
            org_q = db.query(Dashboard.id).filter(Dashboard.id == -1)
        ids = (
            {row[0] for row in owner_q.all()}
            | {row[0] for row in granted_q.all()}
            | {row[0] for row in public_q.all()}
            | {row[0] for row in org_q.all()}
        )
        if not ids:
            return []
        base = db.query(Dashboard).filter(Dashboard.id.in_(ids))

    if q:
        base = base.filter(Dashboard.name.ilike(f"%{q}%"))
    return base.order_by(Dashboard.id).all()


def upsert_share(
    db: Session,
    *,
    dashboard_id: int,
    target_user_id: int,
    permission: str,
    granted_by: int,
) -> DashboardAccess:
    """Create-or-update the grant for ``(dashboard_id, user_id)``.

    Same idempotent semantics as :func:`app.services.report.upsert_share`.
    Caller is responsible for confirming ownership / write-permission
    on the underlying dashboard.
    """
    if permission not in (PERMISSION_READ, PERMISSION_WRITE):
        raise ValueError(
            f"permission must be one of ({PERMISSION_READ!r}, "
            f"{PERMISSION_WRITE!r}), got {permission!r}"
        )
    existing = _grant_for(db, dashboard_id, target_user_id)
    if existing is not None:
        existing.permission = permission
        existing.granted_by = granted_by
        db.commit()
        db.refresh(existing)
        return existing

    share = DashboardAccess(
        dashboard_id=dashboard_id,
        user_id=target_user_id,
        permission=permission,
        granted_by=granted_by,
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def revoke_share(db: Session, share: DashboardAccess) -> None:
    """Hard-delete a share row. Caller is responsible for confirming
    the caller is owner-or-admin on the underlying dashboard."""
    db.delete(share)
    db.commit()


def list_shares_for_dashboard(
    db: Session, dashboard_id: int
) -> list[DashboardAccess]:
    """All share rows for one dashboard. Caller must gate on
    owner-or-admin — the helper itself does not check."""
    return (
        db.query(DashboardAccess)
        .filter(DashboardAccess.dashboard_id == dashboard_id)
        .order_by(DashboardAccess.id)
        .all()
    )


def can_share_dashboard(db: Session, user: User, dashboard: Dashboard) -> bool:
    """True when ``user`` may create / revoke shares on ``dashboard``.

    Owner / admin / write-grantee can share — write permission
    includes the right to propagate sharing further (same transitive
    capability as :func:`app.services.report.can_share_report`).
    """
    if is_admin(user):
        return True
    if is_owner(user, dashboard):
        return True
    assert dashboard.id is not None
    assert user.id is not None
    grant = _grant_for(db, dashboard.id, user.id)
    return grant is not None and grant.permission == PERMISSION_WRITE


# ---- Chart execution (delegates to ReportItem's SQL builder) ----


def execute_dashboard_chart(
    db: Session,
    item: DashboardItem,
    user: User,
) -> dict[str, Any]:
    """Execute a dashboard chart item.

    Resolves the item's data source (admin / owner / has-grant),
    assembles a transient :class:`ReportItem` (not committed) with the
    dashboard's chart fields, and runs the same
    :func:`app.services.report_generator.query_builder.build_query` +
    SQL execution pipeline as a normal report.

    Returns a dict with ``columns``, ``rows``, ``row_count`` ready for
    the dashboard renderer to hand to Chart.js. Raises
    :class:`fastapi.HTTPException` 404 on DS gate failure (admin
    passes; others must have DS access), 422 on SQL builder /
    execution errors.
    """
    if item.item_type != "chart":
        raise ValueError(
            f"execute_dashboard_chart called on item_type={item.item_type!r} "
            "(only 'chart' is supported)"
        )
    if item.data_source_id is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Chart item has no data_source_id",
        )
    ds = get_data_source_for_user(db, item.data_source_id, user)
    if ds is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Data source not found or inaccessible",
        )

    # Lazy imports so the service module stays light when only ACL is
    # exercised (router tests, audit hooks).
    from app.services.report_generator.engine import get_or_create_engine
    from app.services.report_generator.query_builder import build_query, execute_query

    proxy = ReportItem(
        name=item.title or "dashboard chart",
        item_type="chart",
        table_name=item.table_name,
        fields=list(item.fields or []),
        where_conditions=list(item.where_conditions or []),
        group_by=list(item.group_by or []),
        order_by=list(item.order_by or []),
        limit=item.limit,
        display_config=item.display_config,
        custom_sql=item.custom_sql,
    )
    sql, params = build_query(proxy, item.parameters or {})

    engine = get_or_create_engine(ds)
    df = execute_query(engine, sql, params)
    return {
        "columns": [str(c) for c in df.columns],
        "rows": df.astype(object).where(df.notna(), None).values.tolist(),
        "row_count": int(len(df)),
    }


# ---- Duplicate (批 14) ----


# Scalar fields on ``Dashboard`` that are intentionally NOT copied when
# duplicating. Everything else (description) is fair game. JSON-shaped
# fields are deep-copied at the item level so mutations on the original
# don't bleed into the clone.
_EXCLUDE_DASHBOARD_FIELDS = frozenset(
    {
        "id",
        "name",  # caller resolves (or we generate a "(副本)" suffix)
        "owner_user_id",  # the duplicator becomes the new owner
        "visibility",  # resets to private — see below
        "created_at",
        "updated_at",
    }
)


def _next_duplicate_dashboard_name(db: Session, base: str) -> str:
    """Pick a unique duplicate name with ``(副本)`` / ``(副本 2)`` /
    ``(副本 3)`` suffix progression. Mirrors
    :func:`app.services.report._next_duplicate_name` but scopes the
    collision check to ``dashboards.name`` (Dashboard and Report both
    have UNIQUE ``name`` columns in different tables)."""
    candidate = f"{base} (副本)"
    n = 1
    while n <= 1000:
        exists = db.query(Dashboard.id).filter(Dashboard.name == candidate).first()
        if not exists:
            return candidate
        n += 1
        candidate = f"{base} (副本 {n})"
    raise RuntimeError(
        f"could not find a free duplicate dashboard name after 1000 attempts "
        f"for base {base!r}"
    )


def duplicate_dashboard(
    db: Session,
    dashboard_id: int,
    user: User,
    *,
    new_name: str | None = None,
) -> tuple[Dashboard, Dashboard]:
    """Duplicate ``dashboard_id`` into a new Dashboard owned by ``user``.

    Read ACL is sufficient (mirroring
    :func:`app.services.report.duplicate_report`). The new row is
    created private + without subscriptions; the caller can opt-in via
    the regular update + subscription endpoints. Items are deep-copied
    (JSON columns included) so later edits stay independent.

    Returns ``(original, duplicate)`` for the audit log. Raises
    ``LookupError`` if the source is missing / inaccessible (uniform
    404). Raises ``ValueError`` on name collision.
    """
    original = get_dashboard_for_user(db, dashboard_id, user)
    if original is None:
        raise LookupError(f"Dashboard {dashboard_id} not found or inaccessible")

    # ``name`` is NOT NULL in the schema so the narrowing is safe.
    assert original.name is not None
    chosen = (
        new_name if new_name else _next_duplicate_dashboard_name(db, original.name)
    )
    if chosen == original.name:
        raise ValueError("duplicate name must differ from the source name")
    collision = db.query(Dashboard.id).filter(Dashboard.name == chosen).first()
    if collision:
        raise ValueError(f"Dashboard named {chosen!r} already exists")

    clone = Dashboard(
        **{
            col: getattr(original, col)
            for col in [c.key for c in Dashboard.__table__.columns]
            if col not in _EXCLUDE_DASHBOARD_FIELDS
        },
        name=chosen,
        owner_user_id=user.id,
        visibility=VISIBILITY_PRIVATE,
    )
    db.add(clone)
    db.flush()  # populate clone.id so item FKs can resolve

    # Deep-copy items. ``copy.deepcopy`` handles the JSON columns
    # (fields, where_conditions, group_by, order_by, display_config,
    # parameters) so post-duplicate edits don't bleed across. List-typed
    # JSON columns are normalised to ``[]`` / ``{}`` instead of
    # ``None`` — SQLite stores both interchangeably for our purposes
    # and the schema defaults are lists/dicts.
    for src_item in original.items:
        db.add(
            DashboardItem(
                dashboard_id=clone.id,
                item_type=src_item.item_type,
                title=src_item.title,
                order_index=src_item.order_index,
                x=src_item.x,
                y=src_item.y,
                w=src_item.w,
                h=src_item.h,
                report_id=src_item.report_id,
                data_source_id=src_item.data_source_id,
                table_name=src_item.table_name,
                fields=copy.deepcopy(src_item.fields) or [],
                where_conditions=copy.deepcopy(src_item.where_conditions) or [],
                group_by=copy.deepcopy(src_item.group_by) or [],
                order_by=copy.deepcopy(src_item.order_by) or [],
                limit=src_item.limit,
                display_config=copy.deepcopy(src_item.display_config)
                if src_item.display_config is not None
                else None,
                custom_sql=src_item.custom_sql,
                text_content=src_item.text_content,
                parameters=copy.deepcopy(src_item.parameters) or {},
            )
        )

    db.flush()
    return original, clone


# ---- Visibility helpers ----


def ensure_dashboard_visible(
    db: Session,
    user: User,
    dashboard_id: int,
    *,
    level: str = PERMISSION_READ,
) -> Dashboard:
    """Load a Dashboard and raise 404 if missing OR inaccessible (uniform 404).

    Thin wrapper over :func:`get_dashboard_for_user` so all ACL checks
    (DS gate + dashboard layer + read/write split) are reused rather
    than re-implemented. ``level=PERMISSION_WRITE`` requires an explicit
    write grant — public visibility never grants write.
    """
    dashboard = get_dashboard_for_user(db, dashboard_id, user, level=level)
    if dashboard is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Dashboard not found",
        )
    return dashboard


def is_owner_or_admin(user: User, dashboard: Dashboard) -> bool:
    """True for admin role or dashboard owner."""
    if user.role == ROLE_ADMIN:
        return True
    return dashboard.owner_user_id == user.id

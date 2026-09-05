"""API routes for report management."""

from datetime import datetime
from pathlib import Path as FilePath
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.middleware.rate_limit import RateLimiter
from app.models.dashboard import Dashboard, DashboardItem
from app.models.report import Report, ReportItem
from app.models.report_access import ReportAccess
from app.models.report_parameter import ReportParameter
from app.models.user import User
from app.schemas.report import (
    ForkFromTemplateRequest,
    ReportCreate,
    ReportDetailResponse,
    ReportDuplicateRequest,
    ReportGenerateRequest,
    ReportGenerateResponse,
    ReportItemCreate,
    ReportItemReorderRequest,
    ReportItemResponse,
    ReportItemUpdate,
    ReportShareCreate,
    ReportShareResponse,
    ReportUpdate,
    SaveAsTemplateRequest,
    VersionConflict,
)
from app.schemas.report_parameter import (
    ReportParameterCreate,
    ReportParameterResponse,
    ReportParameterUpdate,
)
from app.schemas.reverse_link import DashboardRef
from app.services import audit as audit_service
from app.services.dashboard import get_dashboard_for_user
from app.services.data_source import (
    get_data_source_for_user,
    is_admin,
)
from app.services.etag import compute_etag, etag_matches, parse_if_match
from app.services.parameter_validator import ParameterValidationError, validate_parameters
from app.services.report import (
    PERMISSION_WRITE,
    _is_template_visible_to_user,
    can_share_report,
    duplicate_report,
    fork_from_template,
    get_report_for_user,
    is_owner,
    list_accessible_reports,
    list_shares_for_report,
    revoke_share,
    save_as_template,
    upsert_share,
)
from app.services.report_generator import ReportGeneratorError, generate_report

router = APIRouter(
    prefix="/reports",
    tags=["reports"],
    dependencies=[Depends(get_current_user)],
)

# D 双向 link: when a Report is being deleted, surface the first N
# blocking DashboardItem rows in the 409 detail so the operator knows
# what to clean up. Matches the existing pattern in
# ``app.routers.data_source.delete_data_source``.
_DELETE_BLOCKER_SAMPLE = 10


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _report_not_found() -> HTTPException:
    """Uniform 404 — used for both "row missing" and "no access" so
    an unauthorized caller can't probe for the existence of someone
    else's report. Mirrors :func:`app.routers.data_source._not_found`."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Report not found",
    )


# Per-IP rate limit on synchronous report generation. Report renders can
# each take seconds; the 10/min/IP cap prevents one analyst from
# monopolising a worker thread and slowing everyone else.
_generate_report_limiter = RateLimiter(
    max_requests=settings.reports_generate_rate_limit,
    window_seconds=60,
)


# ---- Report Item Endpoints ----


@router.post(
    "/{report_id}/items",
    response_model=ReportItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_report_item(
    report_id: int,
    payload: ReportItemCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportItem:
    """Add a new item to a report.

    批 9.4: write ACL on the parent report — owner or write-grantee
    (or admin). Missing / no-access both 404.
    """
    report = get_report_for_user(db, report_id, user, level=PERMISSION_WRITE)
    if report is None:
        raise _report_not_found()

    item = ReportItem(report_id=report_id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    # 批 9.5: audit successful create. target_type=report_item so the
    # admin UI can filter "show every item event" by target_type.
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_ITEM_CREATE,
        target_type=audit_service.TARGET_TYPE_REPORT_ITEM,
        target_id=cast(int, item.id),
        before=None,
        after=item,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return item


@router.put("/{report_id}/items/{item_id}", response_model=ReportItemResponse)
def update_report_item(
    report_id: int,
    item_id: int,
    payload: ReportItemUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportItem:
    """Update an existing report item. Write ACL on the parent report."""
    report = get_report_for_user(db, report_id, user, level=PERMISSION_WRITE)
    if report is None:
        raise _report_not_found()

    item = (
        db.query(ReportItem)
        .filter(ReportItem.id == item_id, ReportItem.report_id == report_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report item not found")

    before_snapshot = audit_service._snapshot(item)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(item, field, value)

    db.commit()
    db.refresh(item)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_ITEM_UPDATE,
        target_type=audit_service.TARGET_TYPE_REPORT_ITEM,
        target_id=cast(int, item.id),
        before=before_snapshot,
        after=item,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return item


@router.delete("/{report_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report_item(
    report_id: int,
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a report item. Write ACL on the parent report."""
    report = get_report_for_user(db, report_id, user, level=PERMISSION_WRITE)
    if report is None:
        raise _report_not_found()

    item = (
        db.query(ReportItem)
        .filter(ReportItem.id == item_id, ReportItem.report_id == report_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report item not found")

    before_snapshot = audit_service._snapshot(item)
    db.delete(item)
    db.commit()
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_ITEM_DELETE,
        target_type=audit_service.TARGET_TYPE_REPORT_ITEM,
        target_id=item_id,
        before=before_snapshot,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return None


@router.patch("/{report_id}/items/order")
def reorder_report_items(
    report_id: int,
    payload: ReportItemReorderRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, int]:
    """Atomically update ``order_index`` for a report's items. Write ACL.

    Used by the drag-reorder UI to replace N parallel PUTs with one
    transactional call. All ``item_id`` values must belong to
    ``report_id``; any mismatch returns 422 so the caller can roll
    back the optimistic UI update.
    """
    report = get_report_for_user(db, report_id, user, level=PERMISSION_WRITE)
    if report is None:
        raise _report_not_found()

    item_ids = [e.item_id for e in payload.items]

    # Reject duplicate order_index values — the caller must assign unique
    # positions (the frontend's arrayMove-based reorder already does this).
    order_values = [e.order_index for e in payload.items]
    if len(order_values) != len(set(order_values)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="order_index values must be unique",
        )

    rows = (
        db.query(ReportItem)
        .filter(ReportItem.id.in_(item_ids), ReportItem.report_id == report_id)
        .all()
    )
    if len(rows) != len(set(item_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="All item_ids must belong to this report",
        )

    # 批 9.5: snapshot the report-level reorder state (item_ids +
    # new order_index) so the audit row captures the full reordering,
    # not per-row changes. ``target_type`` is the report (not item)
    # because the resource being acted on is the report's order.
    before_order = sorted((cast(int, row.id), cast(int, row.order_index)) for row in rows)

    index_by_id = {e.item_id: e.order_index for e in payload.items}
    for row in rows:
        row.order_index = index_by_id[cast(int, row.id)]
    db.commit()
    after_order = sorted((cast(int, row.id), cast(int, row.order_index)) for row in rows)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_ITEM_REORDER,
        target_type=audit_service.TARGET_TYPE_REPORT,
        target_id=report_id,
        before={"order": before_order},
        after={"order": after_order},
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return {"updated": len(rows)}


# ---- Report CRUD Endpoints ----


@router.get("", response_model=list[ReportDetailResponse])
def list_reports(
    response: Response,
    is_active: bool | None = Query(default=None),
    data_source_id: int | None = Query(default=None),
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Report]:
    """List reports the caller can see, with optional filtering.

    批 9.4: ACL via :func:`app.services.report.list_accessible_reports`
    — admin sees all; owner / public / grant-holders see the union.
    Filter values (``is_active``, ``data_source_id``, ``q``) are
    applied AFTER the ACL filter so an unauthorized caller can't
    probe via filter combinations (mirrors
    :func:`app.routers.dashboard.list_dashboards` for ``q``).
    ``X-Total-Count`` reports the post-ACL AND post-``q`` total so
    the frontend can drive a pager.
    """
    rows = list_accessible_reports(db, user, is_active=is_active, data_source_id=data_source_id)
    if q:
        needle = q.casefold()
        rows = [r for r in rows if r.name and needle in r.name.casefold()]
    response.headers["X-Total-Count"] = str(len(rows))
    # Stable order so offset+limit produces consistent pages.
    return rows[offset : offset + limit]


@router.post("", response_model=ReportDetailResponse, status_code=status.HTTP_201_CREATED)
def create_report(
    payload: ReportCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Report:
    """Create a new report with optional initial items.

    批 9.4: caller becomes the owner; new reports default to
    ``visibility=private`` (the schema default). The data source
    still needs read ACL — building a report over a source you can't
    read makes it unusable at render time.
    """
    # Check if report name already exists
    existing = db.query(Report).filter(Report.name == payload.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Report named '{payload.name}' already exists",
        )

    # 批 9.3: ACL-gate the data source — uniform 404 for "missing"
    # and "no access" so an unauthorized caller can't probe existence.
    data_source = get_data_source_for_user(db, payload.data_source_id, user)
    if data_source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Data source not found",
        )

    # Extract items before creating report
    items_data = payload.model_dump().get("items", [])
    report_data = {k: v for k, v in payload.model_dump().items() if k != "items"}
    # 批 9.4: caller becomes owner. ``visibility`` comes from the
    # payload (default ``private`` per the schema).
    report_data["owner_user_id"] = user.id

    report = Report(**report_data)
    db.add(report)
    db.flush()  # Get the report ID

    # Create report items
    for item_data in items_data:
        item = ReportItem(report_id=report.id, **item_data)
        db.add(item)

    db.commit()
    db.refresh(report)
    # 批 3: publish the initial ETag so the caller can immediately
    # follow up with a conditional PUT without a re-GET. Brand-new rows
    # always have ``version=1`` (column default), so the ETag is
    # always present here.
    etag = compute_etag(cast(int, report.version))
    if etag is not None:
        response.headers["ETag"] = etag
    # 批 9.5: audit successful create. ``before`` is None (no pre-image).
    # Snapshot includes nested items because ``refresh`` populates the
    # relationship collection.
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_CREATE,
        target_type=audit_service.TARGET_TYPE_REPORT,
        target_id=cast(int, report.id),
        before=None,
        after=report,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return report


@router.get("/templates", response_model=list[ReportDetailResponse])
def list_templates_endpoint(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    category: str | None = Query(default=None, max_length=64),
    data_source_id: int | None = Query(default=None, ge=1),
    visibility: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=255),
) -> list[Report]:
    """Browse the template gallery.

    Returns templates the caller can see based on visibility:

    * admin → all templates (visibility ACL skipped),
    * everyone else → ``public`` + ``org`` (matching ``org_id``)
      + ``private``-but-owned.

    Filters (``category``, ``data_source_id``, ``q``) are applied
    after the ACL pass so an unauthorized caller can't probe for
    the existence of a hidden template via filter combinations.
    ``visibility`` query param narrows further (e.g. show only
    public templates); pass ``visibility='public'`` to make the
    public-only filter explicit.
    """
    # Pull every template first (single ACL gate), then filter. The
    # ``list_accessible_reports`` ACL pass is the cheaper approach
    # than per-row ``_is_template_visible_to_user`` because
    # ``list_accessible_reports`` runs the visibility subqueries once
    # and uses an IN-set; forking a Python loop over each row would
    # be N round-trips.
    templates = list_accessible_reports(
        db,
        user,
        is_template=True,
        template_category=category,
        data_source_id=data_source_id,
        q=q,
    )
    if visibility is not None:
        templates = [t for t in templates if t.visibility == visibility]
    if not is_admin(user):
        # Belt-and-braces: ``list_accessible_reports`` already
        # enforces visibility at the SQL level (public + owner +
        # org-match + grants), but ``_is_template_visible_to_user``
        # is the single source of truth — apply it as a final
        # guard so a regression in either layer can't leak.
        templates = [t for t in templates if _is_template_visible_to_user(t, user)]
    return templates


@router.get("/{report_id}", response_model=ReportDetailResponse)
def get_report(
    report_id: int,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Report:
    """Get a single report by ID with all items. Read ACL.

    批 3: emits an ``ETag`` header (weak, derived from
    ``updated_at``) so callers can do optimistic-concurrency PUTs
    without a separate round-trip to discover the version.
    """
    report = get_report_for_user(db, report_id, user)
    if report is None:
        raise _report_not_found()
    etag = compute_etag(cast(int, report.version))
    if etag is not None:
        response.headers["ETag"] = etag
    return report


@router.get(
    "/{report_id}/dashboards",
    response_model=list[DashboardRef],
)
def list_dashboards_for_report(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[DashboardRef]:
    """Reverse-link (D): dashboards whose items reference this report.

    Returns one ``DashboardRef`` per referencing dashboard, deduplicated
    (a single dashboard may have multiple items pointing at the same
    report). Each candidate is filtered through
    :func:`get_dashboard_for_user` so the dashboard's own ACL — and the
    data-source gate it inherits via items — is honored; private
    dashboards of other users are silently omitted rather than leaked.

    The parent report itself must be visible to the caller; otherwise
    the endpoint returns 404 with no body, matching :func:`get_report`.
    """
    report = get_report_for_user(db, report_id, user)
    if report is None:
        raise _report_not_found()

    # One row per (item.dashboard_id) where item.report_id matches;
    # multiple items on the same dashboard collapse via DISTINCT.
    rows = (
        db.query(Dashboard.id)
        .join(DashboardItem, DashboardItem.dashboard_id == Dashboard.id)
        .filter(DashboardItem.report_id == report_id)
        .distinct()
        .order_by(Dashboard.id.asc())
        .all()
    )

    refs: list[DashboardRef] = []
    for (dashboard_id,) in rows:
        dashboard = get_dashboard_for_user(db, dashboard_id, user)
        if dashboard is None:
            continue
        item_count = (
            db.query(DashboardItem)
            .filter(
                DashboardItem.dashboard_id == dashboard_id,
                DashboardItem.report_id == report_id,
            )
            .count()
        )
        refs.append(
            DashboardRef(
                id=cast(int, dashboard.id),
                name=str(dashboard.name),
                visibility=dashboard.visibility,  # type: ignore[arg-type]
                item_count=item_count,
            )
        )
    return refs


@router.post(
    "/{report_id}/duplicate",
    response_model=ReportDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_report_endpoint(
    report_id: int,
    request: Request,
    payload: ReportDuplicateRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Report:
    """Duplicate a Report — copies items + parameters, resets scheduler.

    Read ACL is sufficient. The duplicate is owned by the caller
    (not the original owner), defaults to private visibility, and
    starts unscheduled with no notification config. Shares on the
    original are NOT transferred — the duplicate is a fresh
    workspace-owned report. Items / parameters / display_config /
    SQL are deep-copied so post-duplicate edits stay independent.
    """
    body = payload or ReportDuplicateRequest()
    try:
        original, clone = duplicate_report(db, report_id, user, new_name=body.name)
    except LookupError:
        raise _report_not_found()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    db.commit()
    db.refresh(clone)
    # 批 9.5: audit the duplicate. ``before`` = original (a fresh
    # snapshot is required because the session may have already
    # mutated related rows during the duplicate); ``after`` = clone.
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_DUPLICATE,
        target_type=audit_service.TARGET_TYPE_REPORT,
        target_id=cast(int, clone.id),
        before=original,
        after=clone,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    db.commit()
    return clone


# ---- Template marketplace endpoints (批 13) ----


@router.post(
    "/{report_id}/save-as-template",
    response_model=ReportDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def save_as_template_endpoint(
    report_id: int,
    payload: SaveAsTemplateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Report:
    """Publish a Report into the template pool.

    Owner-or-admin only — non-owners can't republish someone else's
    report as a template (they can clone it via the regular duplicate
    endpoint instead). The original row is untouched; the response
    is the new template row with ``is_template=true`` and the
    operator-supplied ``visibility``/``category``. If
    ``visibility=='org'`` the template is stamped with the caller's
    ``org_id`` (caller without an org id + org visibility is rejected
    — a NULL template can't match a NULL viewer).

    Audit: ``report.save_as_template``, ``before`` = source,
    ``after`` = new template.
    """
    try:
        source, template = save_as_template(
            db,
            report_id,
            user,
            visibility=payload.visibility,
            category=payload.category,
        )
    except LookupError:
        raise _report_not_found()
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    if payload.visibility == "org" and (user.org_id is None):
        # Save-as-template succeeded (with org_id=None on the template,
        # which is correct), but the operator chose a tier that needs
        # a real org — flag it so the UI can re-prompt. We don't
        # roll back the row because the caller might also be fine
        # with the resulting NULL-org template (it just won't be
        # discoverable via ``org`` visibility).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "visibility='org' requires the caller to have an "
                "org_id; set DEFAULT_ORG_ID on the admin user or "
                "pick a different visibility tier"
            ),
        )
    db.commit()
    db.refresh(template)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_SAVE_AS_TEMPLATE,
        target_type=audit_service.TARGET_TYPE_REPORT,
        target_id=cast(int, template.id),
        before=source,
        after=template,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    db.commit()
    return template


@router.post(
    "/{report_id}/from-template",
    response_model=ReportDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def fork_template_endpoint(
    report_id: int,
    request: Request,
    payload: ForkFromTemplateRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Report:
    """Fork a template into a personal Report.

    Read ACL on the template is sufficient — anyone who can browse
    the gallery can fork (visibility ACL + grants). The fork is a
    fresh private report owned by the caller, with
    ``is_template=False`` and ``template_source_id=<template_id>``
    for lineage. Items + parameters + display_config are deep-copied
    via the same ``duplicate_report`` machinery so post-fork edits
    stay independent.

    Audit: ``report.fork``, ``before`` = template, ``after`` = fork.
    """
    body = payload or ForkFromTemplateRequest()
    try:
        template, fork = fork_from_template(
            db, report_id, user, new_name=body.name
        )
    except LookupError:
        raise _report_not_found()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    db.commit()
    db.refresh(fork)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_FORK,
        target_type=audit_service.TARGET_TYPE_REPORT,
        target_id=cast(int, fork.id),
        before=template,
        after=fork,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    db.commit()
    return fork


@router.put("/{report_id}", response_model=ReportDetailResponse)
def update_report(
    report_id: int,
    payload: ReportUpdate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Report:
    """Update an existing report. Write ACL — owner or write-grantee.

    批 3: optimistic concurrency. If the caller supplies ``If-Match``,
    the ETag must match the server-side ``version`` counter — otherwise
    412 with the current state in the body. Missing ``If-Match`` is
    accepted (backward-compatible — pre-批 3 clients keep working).
    The response always carries an ``ETag`` header so the next PUT
    has something to compare against.

    The actual UPDATE goes through a single ``UPDATE reports SET ...
    version = version + 1 WHERE id = ? AND version = ?`` statement so
    the version precondition is enforced atomically by the DB — if a
    concurrent writer bumps the row between our read and the write,
    ``rowcount == 0`` and we 412 (this catches a TOCTOU window that
    a Python-side read-then-write check alone can't).
    """
    report = get_report_for_user(db, report_id, user, level=PERMISSION_WRITE)
    if report is None:
        # 404 even when the row exists but the caller lacks write —
        # uniform with the rest of the surface so an attacker can't
        # probe for read-only rows they could otherwise PUT against.
        raise _report_not_found()

    # 批 3: optimistic concurrency — read-time check. We compare
    # BEFORE the mutation so the returned ``current`` reflects the
    # state the caller's PUT would clobber, not a state that includes
    # their own changes. The DB-level WHERE-version precondition
    # below catches the narrower TOCTOU window where a concurrent
    # writer slips in between this check and our UPDATE.
    current_version = cast(int, report.version)
    if_match = parse_if_match(request.headers.get("If-Match"))
    if if_match is not None and not etag_matches(if_match, current_version):
        conflict = VersionConflict(
            message="Report was modified by someone else since you last fetched it.",
            current=ReportDetailResponse.model_validate(
                report, from_attributes=True
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=conflict.model_dump(mode="json"),
        )

    # 批 9.5: snapshot before mutation so the audit row carries a
    # before/after diff. ``get_report_for_user`` already returned the
    # ORM row — capture it now before we mutate.
    before_snapshot = audit_service._snapshot(report)

    update_data = payload.model_dump(exclude_unset=True)

    # Check name uniqueness if name is being updated
    if "name" in update_data and update_data["name"] != report.name:
        existing = db.query(Report).filter(Report.name == update_data["name"]).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Report named '{update_data['name']}' already exists",
            )

    # 批 3: single-shot UPDATE with WHERE version = current_version.
    # We bypass the ORM ``setattr`` path because it would emit an
    # UPDATE that doesn't enforce the precondition (no version column
    # in the WHERE clause), and ``version_id_col`` interacts poorly
    # with cascade-delete / relationship-refresh housekeeping. The
    # atomicity guarantee lives in the WHERE clause.
    update_values = {**update_data, "version": current_version + 1}
    result = db.execute(
        update(Report)
        .where(Report.id == report_id, Report.version == current_version)
        .values(**update_values)
    )
    # ``db.execute`` for a core UPDATE returns ``CursorResult`` (a
    # ``Result`` subtype) which exposes ``rowcount``; mypy only sees
    # the ``Result`` superclass. ``type: ignore[attr-defined]`` is the
    # narrowest escape hatch — alternative is an explicit
    # ``CursorResult`` cast which is uglier and breaks the type
    # relationship with the session.
    if result.rowcount == 0:  # type: ignore[attr-defined]
        # Someone else slipped in between our read and our write.
        # Roll back the half-applied transaction and surface the
        # current state so the caller can decide what to do.
        db.rollback()
        refreshed = db.get(Report, report_id)
        if refreshed is None:
            # Row was deleted entirely — surface as 404.
            raise _report_not_found()
        conflict = VersionConflict(
            message="Report was modified by someone else since you last fetched it.",
            current=ReportDetailResponse.model_validate(
                refreshed, from_attributes=True
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=conflict.model_dump(mode="json"),
        )

    db.commit()
    db.refresh(report)

    # 批 3: publish the new ETag so the caller's next PUT has a
    # comparison point.
    new_etag = compute_etag(cast(int, report.version))
    if new_etag is not None:
        response.headers["ETag"] = new_etag
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_UPDATE,
        target_type=audit_service.TARGET_TYPE_REPORT,
        target_id=cast(int, report.id),
        before=before_snapshot,
        after=report,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return report


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(
    report_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a report and all its items. Owner-or-admin only —
    even a write-grantee cannot delete."""
    report = get_report_for_user(db, report_id, user)
    if report is None or not (is_admin(user) or is_owner(user, report)):
        raise _report_not_found()

    # D 双向 link: ``dashboard_items.report_id`` is nullable with
    # ``ON DELETE SET NULL`` — without this guard the request would
    # silently orphan the referencing item rows. Surface the first N
    # blocking items (parent dashboard name + item id) so the caller
    # knows which dashboards to repoint first.
    blocking_items = (
        db.query(DashboardItem.id, Dashboard.name)
        .join(Dashboard, Dashboard.id == DashboardItem.dashboard_id)
        .filter(DashboardItem.report_id == report_id)
        .order_by(DashboardItem.id.asc())
        .limit(_DELETE_BLOCKER_SAMPLE)
        .all()
    )
    if blocking_items:
        total = (
            db.query(DashboardItem)
            .filter(DashboardItem.report_id == report_id)
            .count()
        )
        sample = ", ".join(
            f"#{row.id} in {row.name!r}" for row in blocking_items
        )
        suffix = (
            f" (and {total - len(blocking_items)} more)"
            if total > len(blocking_items)
            else ""
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Report is still referenced by {total} dashboard item(s): "
                f"{sample}{suffix}. Remove or repoint them first."
            ),
        )

    # 批 9.5: capture the report row before delete so we know what
    # was removed (and which items / params / shares / subscriptions
    # were cascade-deleted along with it).
    before_snapshot = audit_service._snapshot(report)
    db.delete(report)
    db.commit()
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_DELETE,
        target_type=audit_service.TARGET_TYPE_REPORT,
        target_id=report_id,
        before=before_snapshot,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return None


# ---- Report Generation Endpoints ----


@router.post("/generate", response_model=ReportGenerateResponse)
def generate_report_endpoint(
    payload: ReportGenerateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportGenerateResponse:
    """Generate a report and return the output file or preview data.

    批 9.4: read ACL on the report itself — layered on top of the
    data-source ACL by :func:`get_report_for_user`. Public reports
    are reachable by any authenticated user; private ones require
    ownership or an explicit grant.
    """
    # Rate-limit by IP before any DB / query work. Report renders can
    # take seconds, so this cap protects the worker pool from a single
    # runaway client. Namespace the key so the budget is independent from
    # ``/explorer/query`` and ``/reports/{id}/jobs``.
    if _generate_report_limiter.is_rate_limited(f"reports_generate:{_client_ip(request)}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many report generations. Limit: "
                f"{settings.reports_generate_rate_limit}/min/IP."
            ),
            headers={"Retry-After": "60"},
        )

    report = get_report_for_user(db, payload.report_id, user)
    if report is None:
        raise _report_not_found()

    # Validate caller-supplied parameters against the report's declared
    # parameter spec before kicking off the SQL pipeline. Unknown keys,
    # missing required values, type mismatches, and out-of-range enums all
    # surface here as a 400 with a precise message — cheaper than letting
    # them reach the SQL validator and fail in an opaque way.
    try:
        validated_params = validate_parameters(
            spec=list(report.parameters), values=payload.parameters
        )
    except ParameterValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    try:
        result = generate_report(
            report=report,
            output_format=payload.output_format,
            parameters=validated_params,
            db=db,
        )
        # result may include an `errors` dict; pull it out so it goes into
        # the typed `item_errors` field rather than as an arbitrary kwarg.
        item_errors = result.pop("errors", {})
        # 批 9.5: audit successful generate. ``after`` is hand-built
        # rather than the ORM row because the generated file is a side
        # effect outside the DB; we just want the parameters + result
        # envelope. ``item_errors`` captures partial failures (some
        # items rendered, others did not).
        audit_service.log(
            db,
            actor_user_id=cast(int, user.id),
            action=audit_service.ACTION_REPORT_GENERATE,
            target_type=audit_service.TARGET_TYPE_REPORT,
            target_id=cast(int, report.id),
            before=None,
            after={
                "report_id": cast(int, report.id),
                "output_format": payload.output_format,
                "success": True,
                "item_errors": item_errors,
            },
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
        return ReportGenerateResponse(
            success=True,
            report_id=cast(int, report.id),
            report_name=cast(str, report.name),
            output_format=payload.output_format,
            item_errors=item_errors,
            **result,
        )
    except ReportGeneratorError as exc:
        # 批 9.5: audit generation failures too — even when the report
        # didn't render, the attempt itself is auditable ("who probed
        # report X with format Y at time T"). Re-raise after logging.
        audit_service.log(
            db,
            actor_user_id=cast(int, user.id),
            action=audit_service.ACTION_REPORT_GENERATE,
            target_type=audit_service.TARGET_TYPE_REPORT,
            target_id=cast(int, report.id),
            before=None,
            after={
                "report_id": cast(int, report.id),
                "output_format": payload.output_format,
                "success": False,
                "error": str(exc),
            },
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/{report_id}/preview", response_class=HTMLResponse)
def preview_report(
    report_id: int,
    request: Request,
    format: str = Query(default="html", pattern="^html$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> HTMLResponse:
    """Preview a report without generating a file. Returns raw HTML so the
    frontend iframe can load it directly via <iframe src=...> and scripts
    (Chart.js) execute without being stripped by DOMPurify.

    批 9.4: read ACL on the report itself (layered on DS ACL).
    """
    report = get_report_for_user(db, report_id, user)
    if report is None:
        raise _report_not_found()

    try:
        result = generate_report(
            report=report,
            output_format=format,
            parameters={},
            db=db,
            preview_only=True,
            base_url=str(request.base_url),
        )
        return HTMLResponse(content=result["preview_data"])
    except ReportGeneratorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get("/{report_id}/export/{format}", response_class=FileResponse)
def export_report(
    report_id: int,
    format: str = Path(..., pattern="^(excel|html|pdf)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    """Export a generated report file.

    批 9.4: read ACL on the report itself.
    批 8.1: ``pdf`` joins ``excel`` and ``html`` as a sync export
    format — the async worker path (POST /reports/{id}/jobs) also
    accepts ``output_format="pdf"`` and routes through the same
    :func:`generate_report` pipeline, so both entry points use the
    same renderer.
    """
    report = get_report_for_user(db, report_id, user)
    if report is None:
        raise _report_not_found()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{report.name}_{timestamp}"

    try:
        result = generate_report(
            report=report,
            output_format=format,
            parameters={},
            db=db,
        )
        file_path = result.get("file_path")
        if not file_path or not FilePath(file_path).exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Generated file not found",
            )

        # Keep the MIME map exhaustive — a missing branch on a brand-
        # new format would 500 here. See JobOutputFormat for the
        # canonical list; the async /jobs/{id}/download route uses
        # the same dict via _media_type_for() in routers/jobs.py.
        media_type = {
            "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "html": "text/html",
            "pdf": "application/pdf",
        }[format]
        return FileResponse(
            path=file_path,
            filename=f"{filename}.{format}",
            media_type=media_type,
        )
    except ReportGeneratorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


# ---- Report Parameter Endpoints ----


@router.post(
    "/{report_id}/parameters",
    response_model=ReportParameterResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_report_parameter(
    report_id: int,
    payload: ReportParameterCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportParameter:
    """Add a typed parameter declaration to a report. Write ACL.

    ``order_index`` is auto-assigned to ``last + 1`` if omitted, so the
    common "append a parameter" UI flow doesn't have to know about
    existing positions.
    """
    report = get_report_for_user(db, report_id, user, level=PERMISSION_WRITE)
    if report is None:
        raise _report_not_found()

    data = payload.model_dump()
    if data.get("order_index", 0) == 0:
        last = (
            db.query(func.max(ReportParameter.order_index))
            .filter(ReportParameter.report_id == report_id)
            .scalar()
        )
        data["order_index"] = (last or 0) + 1

    param = ReportParameter(report_id=report_id, **data)
    db.add(param)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Parameter {data['name']!r} already exists for this report",
        ) from exc
    db.refresh(param)
    # 批 9.5: audit successful parameter create. ``before`` is None
    # (no pre-image). ``target_type`` is the param (not report) so the
    # admin UI can filter "show every parameter event" by target_type.
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_PARAM_CREATE,
        target_type=audit_service.TARGET_TYPE_REPORT_PARAM,
        target_id=cast(int, param.id),
        before=None,
        after=param,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return param


@router.get(
    "/{report_id}/parameters",
    response_model=list[ReportParameterResponse],
)
def list_report_parameters(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReportParameter]:
    """List a report's parameter declarations, ordered by ``order_index``.

    Used by the frontend (batch 4b) to render the parameter input form
    before calling ``POST /reports/generate``. Read ACL.
    """
    report = get_report_for_user(db, report_id, user)
    if report is None:
        raise _report_not_found()

    return (
        db.query(ReportParameter)
        .filter(ReportParameter.report_id == report_id)
        .order_by(ReportParameter.order_index)
        .all()
    )


@router.put(
    "/{report_id}/parameters/{param_id}",
    response_model=ReportParameterResponse,
)
def update_report_parameter(
    report_id: int,
    param_id: int,
    payload: ReportParameterUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportParameter:
    """Update a report parameter. All fields are optional; the existing
    ``type`` is preserved unless explicitly changed. Write ACL."""
    report = get_report_for_user(db, report_id, user, level=PERMISSION_WRITE)
    if report is None:
        raise _report_not_found()

    param = (
        db.query(ReportParameter)
        .filter(
            ReportParameter.id == param_id,
            ReportParameter.report_id == report_id,
        )
        .first()
    )
    if not param:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report parameter not found"
        )

    # 批 9.5: snapshot before mutation so the audit row carries a
    # before/after diff. Important here because the param ``type``
    # change is a SQL-impacting diff (string vs number vs date).
    before_snapshot = audit_service._snapshot(param)

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(param, field, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Parameter name conflicts with an existing parameter on this report",
        ) from exc
    db.refresh(param)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_PARAM_UPDATE,
        target_type=audit_service.TARGET_TYPE_REPORT_PARAM,
        target_id=cast(int, param.id),
        before=before_snapshot,
        after=param,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return param


@router.delete(
    "/{report_id}/parameters/{param_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_report_parameter(
    report_id: int,
    param_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a report parameter declaration. Write ACL."""
    report = get_report_for_user(db, report_id, user, level=PERMISSION_WRITE)
    if report is None:
        raise _report_not_found()

    param = (
        db.query(ReportParameter)
        .filter(
            ReportParameter.id == param_id,
            ReportParameter.report_id == report_id,
        )
        .first()
    )
    if not param:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report parameter not found"
        )

    before_snapshot = audit_service._snapshot(param)
    db.delete(param)
    db.commit()
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_PARAM_DELETE,
        target_type=audit_service.TARGET_TYPE_REPORT_PARAM,
        target_id=param_id,
        before=before_snapshot,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return None


# ---------------------------------------------------------------------------
# Report shares (批 9.4)
# ---------------------------------------------------------------------------


@router.post(
    "/{report_id}/shares",
    response_model=ReportShareResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_share_endpoint(
    report_id: int,
    payload: ReportShareCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportAccess:
    """Grant ``user_id`` read/write on this report. Owner-or-admin OR
    write-grantee — see :func:`app.services.report.can_share_report`.

    Upserts: re-POSTing with the same ``user_id`` updates the
    permission level rather than hitting the unique constraint.
    """
    report = get_report_for_user(db, report_id, user)
    if report is None or not can_share_report(db, user, report):
        raise _report_not_found()

    target = db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    share = upsert_share(
        db,
        report_id=report_id,
        target_user_id=payload.user_id,
        permission=payload.permission,
        granted_by=cast(int, user.id),
    )
    # 批 9.5: target_type=report_share so the admin UI can filter
    # "show every share event" by target_type. ``before`` is None
    # because upsert either created a fresh row or refreshed an
    # existing one (the service doesn't return the previous permission).
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_SHARE,
        target_type=audit_service.TARGET_TYPE_REPORT_SHARE,
        target_id=cast(int, share.id),
        before=None,
        after=share,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return share


@router.get(
    "/{report_id}/shares",
    response_model=list[ReportShareResponse],
)
def list_shares_endpoint(
    report_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReportAccess]:
    """List every share on this report. Owner-or-admin only — a
    read grantee cannot see who else has access (same isolation as
    the data_source grant list)."""
    report = get_report_for_user(db, report_id, user)
    if report is None or not (is_admin(user) or is_owner(user, report)):
        raise _report_not_found()
    return list_shares_for_report(db, report_id)


@router.delete(
    "/shares/{share_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_share_endpoint(
    share_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Revoke a share by id. Owner-or-admin on the parent report.

    Uses the ``/shares/{id}`` path so an unauthorized caller can't
    probe for share_ids they don't own — lookup is by id, then the
    parent report's ACL is checked.
    """
    share = db.get(ReportAccess, share_id)
    if share is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Share not found",
        )
    report = get_report_for_user(db, share.report_id, user)
    if report is None or not (is_admin(user) or is_owner(user, report)):
        raise _report_not_found()
    # 批 9.5: capture the share row before revoke so the audit trail
    # shows who lost access and at which permission level.
    before_snapshot = audit_service._snapshot(share)
    revoke_share(db, share)
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_REPORT_REVOKE,
        target_type=audit_service.TARGET_TYPE_REPORT_SHARE,
        target_id=share_id,
        before=before_snapshot,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return None

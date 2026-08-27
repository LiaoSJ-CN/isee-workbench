"""Per-user Report ACL helpers (批 9.4).

Mirrors :mod:`app.services.data_source` — same primitives, same
404-isolation semantics:

* :func:`get_report_for_user` — single-row lookup with read/write gate.
* :func:`list_accessible_reports` — admin sees all, owner sees own,
  anyone with a grant sees the report; everyone sees ``public``
  reports (the coarse gate).
* :func:`upsert_share` / :func:`revoke_share` — mutators for the share
  endpoints.

ACL layering: :func:`get_report_for_user` also calls
:func:`get_data_source_for_user` first, so the report's data source
ACL is enforced automatically — a report whose DS was just revoked
becomes inaccessible through this report-level helper even without an
explicit grant.
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy.orm import Session

from app.models.data_source_access import DataSourceAccess  # noqa: F401  # noqa
from app.models.report import (
    ALL_VISIBILITIES,
    VISIBILITY_ORG,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    Report,
    ReportItem,
)
from app.models.report_access import ReportAccess
from app.models.report_parameter import ReportParameter
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
    "VISIBILITY_PRIVATE",
    "VISIBILITY_PUBLIC",
    "can_share_report",
    "fork_from_template",
    "get_report_for_user",
    "is_owner",
    "list_accessible_reports",
    "list_shares_for_report",
    "revoke_share",
    "save_as_template",
    "upsert_share",
]


def is_owner(user: User, report: Report) -> bool:
    """True when ``report.owner_user_id`` matches the user. ``False``
    for ``report.owner_user_id IS NULL`` (orphan-row case — the
    migration backfilled admin but legacy rows from a clean import
    may still be NULL)."""
    return report.owner_user_id is not None and report.owner_user_id == user.id


def _is_template_visible_to_user(template: Report, user: User) -> bool:
    """Pure visibility ACL for template gallery browsing.

    Returns True iff the user is allowed to see ``template`` based on
    its ``visibility`` setting — data-source ACL is layered separately
    by the endpoint so admin can still see every template regardless
    of which DS it points at (the gallery is for browsing, not
    executing).

    Rules (admin short-circuits in the caller; this is non-admin):

    * ``public`` — anyone can browse.
    * ``org`` — both ``template.org_id`` and ``user.org_id`` must be
      non-null and equal. NULL on either side is a cross-tenant
      mismatch — opting into the ``org`` tier requires setting
      ``DEFAULT_ORG_ID`` (the ``org_id`` column is otherwise always
      NULL on a single-tenant deployment).
    * ``private`` — only the template owner.

    Admin is checked by the caller via :func:`is_admin`. We don't
    accept ``user`` as ``User | None`` because the gallery endpoint
    requires authentication; the helper inherits that contract.
    """
    if template.visibility == VISIBILITY_PUBLIC:
        return True
    if template.visibility == VISIBILITY_ORG:
        return (
            template.org_id is not None
            and user.org_id is not None
            and template.org_id == user.org_id
        )
    # ``private`` (and any unknown value — defensive default).
    return template.owner_user_id == user.id


def get_report_for_user(
    db: Session,
    report_id: int | None,
    user: User,
    *,
    level: str = PERMISSION_READ,
) -> Report | None:
    """Single-report lookup, ACL-gated.

    Returns the :class:`Report` row when *all* of these hold:

    1. The caller passes the report's data-source ACL (admin / owner /
       has-grant). The DS gate is layered in first so a freshly
       revoked DS collapses the report's visibility too — without
       this, a report over a forbidden DS would still be listable.
    2. The caller has report-level access:

       * admin → everything,
       * owner → anything,
       * explicit :class:`ReportAccess` grant at ``>= level``,
       * public visibility + read request (public reports are
         read-only by default — sharing is required to mutate).

    Returns ``None`` for both "row missing" and "no access" so the
    caller answers a uniform 404 — same cross-user isolation as the
    DS helper.

    ``report_id`` accepts ``int | None`` so callers can pass through
    raw SQLAlchemy values without casting at every call site.
    """
    if report_id is None:
        return None

    report = db.get(Report, report_id)
    if report is None:
        return None

    # Layer 1 — data source ACL. Reuse the 9.3 helper so the gate is
    # consistent across endpoints. Admin / owner / grant-holders all
    # pass through here before report-level checks.
    if get_data_source_for_user(db, report.data_source_id, user) is None:
        return None

    # Layer 2 — report-level ACL.
    if is_admin(user):
        return report
    if is_owner(user, report):
        return report

    # Read access: public OR explicit read/write grant.
    if level == PERMISSION_READ:
        if report.visibility == VISIBILITY_PUBLIC:
            return report
        assert report.id is not None  # freshly loaded from DB
        assert user.id is not None
        grant = _grant_for(db, report.id, user.id)
        if grant is not None:
            return report
        return None

    # Write access requires an explicit grant (write) — public
    # visibility never grants write.
    assert report.id is not None  # freshly loaded from DB
    assert user.id is not None
    grant = _grant_for(db, report.id, user.id)
    if grant is not None and grant.permission == PERMISSION_WRITE:
        return report
    return None


def _grant_for(db: Session, report_id: int, user_id: int) -> ReportAccess | None:
    """Single-shot lookup of one user's grant on one report."""
    return (
        db.query(ReportAccess)
        .filter(
            ReportAccess.report_id == report_id,
            ReportAccess.user_id == user_id,
        )
        .first()
    )


def list_accessible_reports(
    db: Session,
    user: User,
    *,
    is_active: bool | None = None,
    data_source_id: int | None = None,
    is_template: bool | None = None,
    template_category: str | None = None,
    q: str | None = None,
) -> list[Report]:
    """All reports the user can see.

    Admin sees everything. Owner sees their own + public + grants.
    Everyone else sees the union of:

    * ``visibility = public`` (read-only by definition),
    * ``owner_user_id = me``,
    * any :class:`ReportAccess` row pointing at me.

    The optional ``is_active`` and ``data_source_id`` filters are
    applied after the ACL filter so an unauthorized caller can't use
    them as a probe.

    批 13 — ``is_template``, ``template_category``, and ``q`` (name
    ILIKE match) extend the same ACL-first pattern. ``is_template``
    filters templates vs ordinary reports; ``template_category`` is an
    admin-supplied bucket so the gallery can group cards. The
    visibility gate remains in ``list_accessible_reports`` itself for
    non-admins — that's the coarse filter; ``org``-tier templates need
    the matching ``org_id`` AND a non-null ``user.org_id`` (NULL on
    either side is treated as a cross-tenant mismatch).
    """
    if is_admin(user):
        base = db.query(Report)
    else:
        owner_q = db.query(Report.id).filter(Report.owner_user_id == user.id)
        granted_q = db.query(ReportAccess.report_id).filter(ReportAccess.user_id == user.id)
        # ``public_q`` is a literal — no join needed. SQLite handles
        # the IN-subquery efficiently; the report_id set is small.
        public_q = db.query(Report.id).filter(Report.visibility == VISIBILITY_PUBLIC)
        # ``org``-tier templates are visible only when the caller's
        # ``org_id`` matches the template's and both are non-null.
        # NULL on either side is a cross-tenant mismatch (single-
        # tenant deployment default — operators opt in via
        # ``DEFAULT_ORG_ID``). Branch on the Python value so the
        # SQLAlchemy expression stays column-only (mypy-safe).
        if user.org_id is not None:
            org_q = db.query(Report.id).filter(
                Report.visibility == VISIBILITY_ORG,
                Report.org_id.isnot(None),
                Report.org_id == user.org_id,
            )
        else:
            # Always-empty subquery — org templates never match a
            # user without an org id. ``id == -1`` is a cheap no-op
            # predicate since ``reports.id`` is always positive.
            org_q = db.query(Report.id).filter(Report.id == -1)
        ids = (
            {row[0] for row in owner_q.all()}
            | {row[0] for row in granted_q.all()}
            | {row[0] for row in public_q.all()}
            | {row[0] for row in org_q.all()}
        )
        if not ids:
            return []
        base = db.query(Report).filter(Report.id.in_(ids))

    if is_active is not None:
        base = base.filter(Report.is_active == is_active)
    if data_source_id is not None:
        base = base.filter(Report.data_source_id == data_source_id)
    if is_template is not None:
        base = base.filter(Report.is_template == is_template)
    if template_category is not None:
        base = base.filter(Report.template_category == template_category)
    if q:
        # ``Report.name`` is NOT NULL; ``contains`` translates to
        # ``LIKE %q%`` on SQLite/Postgres. Case-insensitive on both —
        # SQLite handles ``LIKE`` case-insensitively for ASCII by
        # default; Postgres needs ``ILIKE``. SQLAlchemy abstracts both
        # behind ``.ilike()``.
        base = base.filter(Report.name.ilike(f"%{q}%"))
    return base.order_by(Report.id).all()


def upsert_share(
    db: Session,
    *,
    report_id: int,
    target_user_id: int,
    permission: str,
    granted_by: int,
) -> ReportAccess:
    """Create-or-update the grant for ``(report_id, user_id)``.

    Same idempotent semantics as :func:`data_source.upsert_grant`.
    Caller is responsible for confirming ownership / write-permission
    on the underlying report.
    """
    if permission not in (PERMISSION_READ, PERMISSION_WRITE):
        raise ValueError(
            f"permission must be one of ({PERMISSION_READ!r}, "
            f"{PERMISSION_WRITE!r}), got {permission!r}"
        )
    existing = _grant_for(db, report_id, target_user_id)
    if existing is not None:
        existing.permission = permission
        existing.granted_by = granted_by
        db.commit()
        db.refresh(existing)
        return existing

    share = ReportAccess(
        report_id=report_id,
        user_id=target_user_id,
        permission=permission,
        granted_by=granted_by,
    )
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def revoke_share(db: Session, share: ReportAccess) -> None:
    """Hard-delete a share row. Caller is responsible for confirming
    the caller is owner-or-admin on the underlying report."""
    db.delete(share)
    db.commit()


def list_shares_for_report(db: Session, report_id: int) -> list[ReportAccess]:
    """All share rows for one report. Caller must gate on
    owner-or-admin — the helper itself does not check."""
    return (
        db.query(ReportAccess)
        .filter(ReportAccess.report_id == report_id)
        .order_by(ReportAccess.id)
        .all()
    )


def can_share_report(db: Session, user: User, report: Report) -> bool:
    """True when ``user`` may create / revoke shares on ``report``.

    Owner / admin / write-grantee can share — write permission
    includes the right to propagate sharing further (same transitive
    capability as :func:`data_source.can_share`).
    """
    if is_admin(user):
        return True
    if is_owner(user, report):
        return True
    assert report.id is not None
    assert user.id is not None
    grant = _grant_for(db, report.id, user.id)
    return grant is not None and grant.permission == PERMISSION_WRITE


# ---- Duplicate (批 10.3) ----

# Scalar fields on ``Report`` that are intentionally NOT copied when
# duplicating. Everything else (description / layout_config /
# output_formats) is fair game. JSON-shaped fields are deep-copied so
# mutations on the original don't bleed into the clone.
_EXCLUDE_REPORT_FIELDS = frozenset(
    {
        "id",
        "name",  # caller resolves (or we generate a "(副本)" suffix)
        "owner_user_id",  # the duplicator becomes the new owner
        "visibility",  # resets to private — see below
        "is_demo",  # clones are never demo scaffolding
        "is_scheduled",  # user explicitly opts back in if they want
        "cron_expression",
        "schedule_description",
        "notification_config",  # may reference external webhook URLs
        # 批 13 — template marketplace fields. Excluded so
        # ``save_as_template`` / ``fork_from_template`` can flip them
        # via ``extra_overrides`` without colliding with the
        # column-iteration dict (Python's ``**`` expansion rejects
        # duplicate kwargs).
        "is_template",
        "template_category",
        "template_source_id",
        # ``save_as_template`` may override ``org_id`` (templates take
        # the publishing user's org); ``duplicate_report`` shouldn't
        # leak the source's org into the clone or the override
        # collides on the ``**`` spread.
        "org_id",
        "created_at",
        "updated_at",
    }
)


def _next_duplicate_name(db: Session, base: str) -> str:
    """Pick a unique duplicate name with ``(副本)`` / ``(副本 2)`` /
    ``(副本 3)`` suffix progression. Same pattern as
    ``data_source._next_clone_name`` — kept separate so each side
    scopes its collision check to its own table (DataSource.name and
    Report.name are both UNIQUE but in different tables).
    """
    candidate = f"{base} (副本)"
    n = 1
    while n <= 1000:
        exists = db.query(Report.id).filter(Report.name == candidate).first()
        if not exists:
            return candidate
        n += 1
        candidate = f"{base} (副本 {n})"
    raise RuntimeError(
        f"could not find a free duplicate name after 1000 attempts for base {base!r}"
    )


def duplicate_report(
    db: Session,
    report_id: int,
    user: User,
    *,
    new_name: str | None = None,
    extra_overrides: dict[str, Any] | None = None,
) -> tuple[Report, Report]:
    """Duplicate ``report_id`` into a new Report owned by ``user``.

    Read ACL is sufficient (mirroring ``clone_data_source``). The new
    row is created private + unscheduled + without notification
    config; the caller can opt-in via the regular update endpoint.
    Items + parameters are deep-copied (JSON columns included) so
    later edits to either side stay independent.

    批 13 — ``extra_overrides`` is the seam for save-as-template and
    fork-from-template: those callers flip ``is_template`` and
    ``template_source_id`` (and friends) on the clone. ``extra_overrides``
    wins over the defaults baked in here (e.g. ``visibility=private``)
    so a template-save can pass ``visibility="org"`` to get the
    org-tier template row. Caller is responsible for the values —
    validation lives in the router.

    Returns ``(original, duplicate)`` for the audit log.
    Raises ``LookupError`` if the source is missing / inaccessible
    (uniform 404). Raises ``ValueError`` on name collision.
    """
    original = get_report_for_user(db, report_id, user)
    if original is None:
        raise LookupError(f"Report {report_id} not found or inaccessible")

    # ``name`` is NOT NULL in the schema so the narrowing is safe.
    assert original.name is not None
    chosen = new_name if new_name else _next_duplicate_name(db, original.name)
    if chosen == original.name:
        raise ValueError("duplicate name must differ from the source name")
    collision = db.query(Report.id).filter(Report.name == chosen).first()
    if collision:
        raise ValueError(f"Report named {chosen!r} already exists")

    # Build the per-call defaults. ``extra_overrides`` lets
    # save-as-template (is_template / visibility / org_id / category)
    # and fork-from-template (template_source_id) flip fields the
    # standard duplicate wouldn't touch. Inline at the ``Report(**...)``
    # call so mypy can unify the column types from the surrounding
    # constructor signature — extracting to a named variable makes
    # mypy see ``dict[str, Any]`` and reject the spread.
    defaults: dict[str, Any] = dict(
        name=chosen,
        owner_user_id=user.id,
        visibility=VISIBILITY_PRIVATE,
        is_demo=False,
        is_scheduled=False,
        cron_expression=None,
        schedule_description=None,
        notification_config=None,
    )
    if extra_overrides:
        defaults.update(extra_overrides)
    clone = Report(
        **{
            col: getattr(original, col)
            for col in [c.key for c in Report.__table__.columns]
            if col not in _EXCLUDE_REPORT_FIELDS
        },
        **defaults,
    )
    db.add(clone)
    db.flush()  # populate clone.id so item / parameter FKs can resolve

    # Deep-copy items. ``copy.deepcopy`` handles the JSON columns
    # (fields, where_conditions, group_by, order_by, display_config)
    # so post-duplicate edits don't bleed across. List-typed JSON
    # columns are normalised to ``[]`` instead of ``None`` — the
    # Pydantic response schema rejects ``None`` for these fields
    # (they have ``default_factory=list``), and SQLite stores ``[]``
    # and ``None`` interchangeably for our purposes.
    for src_item in original.items:
        db.add(
            ReportItem(
                report_id=clone.id,
                name=src_item.name,
                item_type=src_item.item_type,
                order_index=src_item.order_index,
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
            )
        )

    # Deep-copy parameters. ``default`` / ``options`` are JSON; rest
    # are scalars. ``default`` is nullable by design (an unset
    # optional param), ``options`` is only present on ``enum``.
    for src_param in original.parameters:
        db.add(
            ReportParameter(
                report_id=clone.id,
                name=src_param.name,
                label=src_param.label,
                type=src_param.type,
                required=src_param.required,
                default=copy.deepcopy(src_param.default) if src_param.default is not None else None,
                options=copy.deepcopy(src_param.options) if src_param.options is not None else None,
                order_index=src_param.order_index,
            )
        )

    db.flush()
    return original, clone


# ---- Template marketplace (批 13) ----


def save_as_template(
    db: Session,
    source_id: int,
    user: User,
    *,
    visibility: str,
    category: str | None,
) -> tuple[Report, Report]:
    """Clone ``source_id`` into a new Report marked as a template.

    Caller must be admin OR the source's owner — non-owners can't
    publish someone else's report as a template (they can clone it
    via the regular duplicate endpoint). The original row is left
    untouched; the template is a fresh row with:

    * ``is_template=True``,
    * ``owner_user_id=user.id``,
    * ``visibility`` set to the operator-supplied value (validated by
      the router before this call),
    * ``org_id=user.org_id`` if ``visibility=='org'`` else ``None``
      (templates only participate in the org tier when the owning
      user is in an org),
    * ``template_category`` set to the admin-supplied free-text bucket
      (or ``None`` if left blank),
    * scheduler + notification stripped (templates are dormant
      definitions; the forker can wire those up after the copy),
    * ``is_demo=False`` (templates and demo scaffolding are separate
      concepts — admin uses one or the other, not both).

    Returns ``(source, template)`` for the audit log. Raises
    ``PermissionError`` if the caller isn't admin/owner. Raises
    ``LookupError`` if the source is missing (uniform 404).
    """
    source = get_report_for_user(db, source_id, user)
    if source is None:
        raise LookupError(f"Report {source_id} not found or inaccessible")
    if not (is_admin(user) or is_owner(user, source)):
        raise PermissionError(
            "Only the report owner or an admin can publish it as a template"
        )

    overrides: dict[str, Any] = dict(
        is_template=True,
        visibility=visibility,
        template_category=category,
        template_source_id=None,
        org_id=user.org_id if visibility == VISIBILITY_ORG else None,
    )
    return duplicate_report(
        db,
        source_id,
        user,
        new_name=f"{source.name} [模板]",
        extra_overrides=overrides,
    )


def fork_from_template(
    db: Session,
    template_id: int,
    user: User,
    *,
    new_name: str | None = None,
) -> tuple[Report, Report]:
    """Clone ``template_id`` into a new Report owned by ``user``.

    Read ACL on the *template* row is sufficient — ``get_report_for_user``
    grants admin + template owner + visibility ACL + grants, and any
    of those callers should be able to fork. The resulting fork is a
    fresh private report (mirrors ``duplicate_report`` defaults), with
    ``is_template=False`` (the fork is a regular report, not itself
    a template) and ``template_source_id=template_id`` for lineage.
    Items + parameters are deep-copied via the same machinery so
    later edits stay independent.

    Returns ``(template, fork)`` for the audit log. Raises
    ``LookupError`` if the template is missing/inaccessible (uniform
    404). Raises ``ValueError`` on name collision.
    """
    # Load the template first so we can read ``template_category`` into
    # the override dict — the forker inherits the bucket so the
    # gallery can still group the fork. ``get_report_for_user``
    # enforces read ACL (uniform 404 on miss / no-access).
    template = get_report_for_user(db, template_id, user)
    if template is None:
        raise LookupError(f"Report {template_id} not found or inaccessible")
    _, fork = duplicate_report(
        db,
        template_id,
        user,
        new_name=new_name,
        extra_overrides=dict(
            is_template=False,
            template_source_id=template_id,
            template_category=template.template_category,
        ),
    )
    return template, fork


# ---- Versioning helpers (批 versioning Task 4) ----


def ensure_report_visible(
    db: Session,
    user: User,
    report_id: int,
    *,
    level: str = PERMISSION_READ,
) -> Report:
    """Load a Report and raise 404 if missing OR inaccessible (uniform 404).

    Thin wrapper over :func:`get_report_for_user` so all ACL checks
    (data-source layer + report layer + read/write split) are reused
    rather than re-implemented. ``level=PERMISSION_WRITE`` requires an
    explicit write grant — public visibility never grants write.
    """
    report = get_report_for_user(db, report_id, user, level=level)
    if report is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail="Report not found",
        )
    return report


def is_owner_or_admin(user: User, report: Report) -> bool:
    """True for admin role or report owner."""
    if user.role == ROLE_ADMIN:
        return True
    return report.owner_user_id == user.id

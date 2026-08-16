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

from sqlalchemy.orm import Session

from app.models.data_source_access import DataSourceAccess  # noqa: F401  # noqa
from app.models.report import (
    ALL_VISIBILITIES,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    Report,
)
from app.models.report_access import ReportAccess
from app.models.user import User
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
    "get_report_for_user",
    "is_owner",
    "list_accessible_reports",
    "list_shares_for_report",
    "revoke_share",
    "upsert_share",
]


def is_owner(user: User, report: Report) -> bool:
    """True when ``report.owner_user_id`` matches the user. ``False``
    for ``report.owner_user_id IS NULL`` (orphan-row case — the
    migration backfilled admin but legacy rows from a clean import
    may still be NULL)."""
    return report.owner_user_id is not None and report.owner_user_id == user.id


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
    """
    if is_admin(user):
        q = db.query(Report)
    else:

        owner_q = db.query(Report.id).filter(Report.owner_user_id == user.id)
        granted_q = db.query(ReportAccess.report_id).filter(
            ReportAccess.user_id == user.id
        )
        # ``public_q`` is a literal — no join needed. SQLite handles
        # the IN-subquery efficiently; the report_id set is small.
        public_q = db.query(Report.id).filter(Report.visibility == VISIBILITY_PUBLIC)
        ids = {row[0] for row in owner_q.all()} | {
            row[0] for row in granted_q.all()
        } | {row[0] for row in public_q.all()}
        if not ids:
            return []
        q = db.query(Report).filter(Report.id.in_(ids))

    if is_active is not None:
        q = q.filter(Report.is_active == is_active)
    if data_source_id is not None:
        q = q.filter(Report.data_source_id == data_source_id)
    return q.order_by(Report.id).all()


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

"""ACL tests for the report-versioning helpers.

The helpers exist before the HTTP router — these tests gate the helper
behavior independently of routing.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import VISIBILITY_PRIVATE, VISIBILITY_PUBLIC, Report
from app.models.report_access import ReportAccess
from app.models.user import ROLE_ADMIN, ROLE_EDITOR, User
from app.services.report import ensure_report_visible, is_owner_or_admin


@pytest.fixture
def db():
    """Yield a SQLAlchemy session bound to the dev metadata DB.

    Tests create and discard their own User / Report / ReportAccess rows
    via the local helpers; cleanup is the test's responsibility (each
    helper commits then returns the row, but the dev ``app.db`` is
    intentionally not truncated so we can debug from the UI afterwards).
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _make_user(db, role=ROLE_EDITOR, username="alice"):
    u = User(username=_unique(username), role=role, disabled=False, password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_report(db, owner=None, visibility=VISIBILITY_PUBLIC):
    ds = DataSource(
        name=f"ds-{owner.username if owner else 'x'}",
        db_type="sqlite",
        database=":memory:",
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    r = Report(
        name=f"r-{owner.username if owner else 'x'}",
        data_source_id=ds.id,
        owner_user_id=owner.id if owner else None,
        visibility=visibility,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_ensure_report_visible_admin_sees_private(db):
    owner = _make_user(db, username="owner")
    admin = _make_user(db, role=ROLE_ADMIN, username="admin")
    report = _make_report(db, owner=owner, visibility=VISIBILITY_PRIVATE)
    assert ensure_report_visible(db, admin, report.id).id == report.id


def test_ensure_report_visible_owner_sees_own(db):
    owner = _make_user(db, username="owner")
    report = _make_report(db, owner=owner, visibility=VISIBILITY_PRIVATE)
    assert ensure_report_visible(db, owner, report.id).id == report.id


def test_ensure_report_visible_public_seen_by_stranger(db):
    owner = _make_user(db, username="owner")
    stranger = _make_user(db, username="stranger")
    report = _make_report(db, owner=owner, visibility=VISIBILITY_PUBLIC)
    assert ensure_report_visible(db, stranger, report.id).id == report.id


def test_ensure_report_visible_grantee_sees(db):
    owner = _make_user(db, username="owner")
    grantee = _make_user(db, username="grantee")
    report = _make_report(db, owner=owner, visibility=VISIBILITY_PRIVATE)
    db.add(ReportAccess(report_id=report.id, user_id=grantee.id, permission="read"))
    db.commit()
    assert ensure_report_visible(db, grantee, report.id).id == report.id


def test_ensure_report_visible_private_403(db):
    owner = _make_user(db, username="owner")
    stranger = _make_user(db, username="stranger")
    report = _make_report(db, owner=owner, visibility=VISIBILITY_PRIVATE)
    with pytest.raises(HTTPException) as exc:
        ensure_report_visible(db, stranger, report.id)
    assert exc.value.status_code == 403


def test_ensure_report_visible_missing_404(db):
    user = _make_user(db, username="u")
    with pytest.raises(HTTPException) as exc:
        ensure_report_visible(db, user, 99999)
    assert exc.value.status_code == 404


def test_is_owner_or_admin_admin_true(db):
    admin = _make_user(db, role=ROLE_ADMIN, username="admin")
    owner = _make_user(db, username="owner")
    report = _make_report(db, owner=owner)
    assert is_owner_or_admin(admin, report) is True


def test_is_owner_or_admin_owner_true(db):
    owner = _make_user(db, username="owner")
    report = _make_report(db, owner=owner)
    assert is_owner_or_admin(owner, report) is True


def test_is_owner_or_admin_grantee_false(db):
    grantee = _make_user(db, username="grantee")
    owner = _make_user(db, username="owner")
    report = _make_report(db, owner=owner)
    assert is_owner_or_admin(grantee, report) is False
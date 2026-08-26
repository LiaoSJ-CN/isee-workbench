"""End-to-end router tests via FastAPI TestClient."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.report import VISIBILITY_PRIVATE, Report, ReportItem
from app.models.report_access import ReportAccess
from app.models.report_version import ReportVersion
from app.models.user import ROLE_ADMIN, ROLE_EDITOR, User
from app.services.jwt_auth import create_access_token
from app.services.report_version import _lock_normalize


@pytest.fixture
def db():
    """Yield a SQLAlchemy session bound to the dev metadata DB.

    Mirrors the fixture in ``test_report_version_acl.py``: tests create
    and discard their own User / Report rows via the local fixtures;
    cleanup is the test's responsibility.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def admin_user(db):
    u = User(
        username=_unique("admin"),
        role=ROLE_ADMIN,
        disabled=False,
        password_hash="x",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture
def other_user(db):
    u = User(
        username=_unique("bob"),
        role=ROLE_EDITOR,
        disabled=False,
        password_hash="x",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture
def rv_seed_report(db, admin_user):
    ds = DataSource(name=_unique("ds"), db_type="sqlite", database=":memory:")
    db.add(ds)
    db.commit()
    db.refresh(ds)
    r = Report(
        name=_unique("r"),
        data_source_id=ds.id,
        owner_user_id=admin_user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    db.add(
        ReportItem(
            report_id=r.id,
            name="sales",
            item_type="table",
            order_index=0,
            table_name="orders",
        )
    )
    db.commit()
    return r


def _auth(user):
    token = create_access_token(user.username)
    return {"Authorization": f"Bearer {token}"}


def test_create_and_list_versions(client, db, admin_user, rv_seed_report):
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={"label": "v1"},
        headers=_auth(admin_user),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version_number"] == 1
    assert body["label"] == "v1"

    r = client.get(f"/reports/{rv_seed_report.id}/versions", headers=_auth(admin_user))
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_create_version_invisible_returns_404(client, db, admin_user, other_user, rv_seed_report):
    """Layered ACL collapses private-with-no-grant into a uniform 404.

    Matches the convention used by every other endpoint in the project
    (P3 / SEC-3) — don't leak whether the resource exists when the
    caller isn't allowed to see it.
    """
    rv_seed_report.visibility = VISIBILITY_PRIVATE
    db.commit()
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={},
        headers=_auth(other_user),
    )
    assert r.status_code == 404


def test_create_version_non_owner_403(client, db, admin_user, other_user, rv_seed_report):
    """Reader (sees report via DataSource + Report grants) cannot snapshot.

    A read grant lets ``ensure_report_visible`` pass; the new owner-or-admin
    gate below it returns 403 — mirroring the restore/delete behavior so
    readers can't poison the version history.
    """
    db.add(
        DataSourceAccess(
            data_source_id=rv_seed_report.data_source_id,
            user_id=other_user.id,
            permission="read",
        )
    )
    rv_seed_report.visibility = VISIBILITY_PRIVATE
    db.add(
        ReportAccess(
            report_id=rv_seed_report.id,
            user_id=other_user.id,
            permission="read",
        )
    )
    db.commit()
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={"label": "reader-snapshot"},
        headers=_auth(other_user),
    )
    assert r.status_code == 403


def test_restore_owner_allowed(client, db, admin_user, rv_seed_report):
    original_name = rv_seed_report.name
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={"label": "v1"},
        headers=_auth(admin_user),
    )
    version_id = r.json()["id"]
    rv_seed_report.name = _unique("mutated")
    db.commit()
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions/{version_id}/restore",
        headers=_auth(admin_user),
    )
    assert r.status_code == 200, r.text
    db.refresh(rv_seed_report)
    assert rv_seed_report.name == original_name


def test_restore_non_owner_403(client, db, admin_user, other_user, rv_seed_report):
    """Editor with read-only ReportAccess can see the report but cannot
    restore — only owner / admin can (matches ACL convention)."""
    # Layered ACL: other_user needs DS access + a read grant to pass
    # ensure_report_visible; the 403 we assert comes from
    # is_owner_or_admin below, not the visibility gate.
    db.add(
        DataSourceAccess(
            data_source_id=rv_seed_report.data_source_id,
            user_id=other_user.id,
            permission="read",
        )
    )
    rv_seed_report.visibility = VISIBILITY_PRIVATE
    db.add(
        ReportAccess(
            report_id=rv_seed_report.id,
            user_id=other_user.id,
            permission="read",
        )
    )
    db.commit()
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={},
        headers=_auth(admin_user),
    )
    version_id = r.json()["id"]
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions/{version_id}/restore",
        headers=_auth(other_user),
    )
    assert r.status_code == 403


def test_delete_pinned_version_409(client, db, admin_user, rv_seed_report):
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={},
        headers=_auth(admin_user),
    )
    version_id = r.json()["id"]
    db.query(ReportVersion).filter(ReportVersion.id == version_id).update({"is_pinned": True})
    db.commit()
    r = client.delete(
        f"/reports/{rv_seed_report.id}/versions/{version_id}",
        headers=_auth(admin_user),
    )
    assert r.status_code == 409


def test_diff_endpoint_returns_changes(client, db, admin_user, rv_seed_report):
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={},
        headers=_auth(admin_user),
    )
    version_id = r.json()["id"]
    rv_seed_report.name = _unique("mutated")
    db.commit()
    r = client.get(
        f"/reports/{rv_seed_report.id}/versions/{version_id}/diff?against=current",
        headers=_auth(admin_user),
    )
    assert r.status_code == 200
    body = r.json()
    assert any(c["field"] == "name" for c in body["report_changes"])


def test_create_version_writes_audit_log(client, db, admin_user, rv_seed_report):
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={"label": "v1"},
        headers=_auth(admin_user),
    )
    assert r.status_code == 201
    # Scope by actor_user_id (unique per test run) so accumulated
    # create_version rows from previous runs of this same test don't
    # fail the count.
    logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "create_version",
            AuditLog.actor_user_id == admin_user.id,
            AuditLog.target_id == rv_seed_report.id,
        )
        .all()
    )
    assert len(logs) == 1
    assert logs[0].target_id == rv_seed_report.id


def test_restore_writes_audit_log(client, db, admin_user, rv_seed_report):
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={},
        headers=_auth(admin_user),
    )
    version_id = r.json()["id"]
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions/{version_id}/restore",
        headers=_auth(admin_user),
    )
    assert r.status_code == 200
    logs = (
        db.query(AuditLog)
        .filter(
            AuditLog.action == "restore_version",
            AuditLog.actor_user_id == admin_user.id,
            AuditLog.target_id == rv_seed_report.id,
        )
        .all()
    )
    assert len(logs) == 1


# ---------------------------------------------------------------------------
# A5 — optimistic lock on restore
# ---------------------------------------------------------------------------


def _force_updated_at(db, report: Report, when: datetime) -> datetime:
    """Bump ``report.updated_at`` to ``when`` (assignable for tests).

    The model uses ``onupdate=func.now()`` which fires on UPDATE; we
    also need to touch another column so the UPDATE is non-empty, and
    we explicitly assign ``updated_at`` to override the onupdate
    trigger so the test value is reproducible.
    """
    report.name = _unique("locked")
    report.updated_at = when
    db.commit()
    db.refresh(report)
    return report.updated_at


def test_restore_with_no_body_backward_compat(client, db, admin_user, rv_seed_report):
    """Omitting the body preserves the v1 ``trust the client`` behavior."""
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={},
        headers=_auth(admin_user),
    )
    version_id = r.json()["id"]
    # Mutate the live report so updated_at advances.
    _force_updated_at(db, rv_seed_report, datetime.now(timezone.utc))
    # No body at all — restores anyway.
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions/{version_id}/restore",
        headers=_auth(admin_user),
    )
    assert r.status_code == 200, r.text


def test_restore_with_explicit_null_expected_updated_at_skips_check(
    client, db, admin_user, rv_seed_report,
):
    """``expected_updated_at: null`` is the explicit opt-out for the check."""
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={},
        headers=_auth(admin_user),
    )
    version_id = r.json()["id"]
    _force_updated_at(db, rv_seed_report, datetime.now(timezone.utc))
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions/{version_id}/restore",
        json={"expected_updated_at": None},
        headers=_auth(admin_user),
    )
    assert r.status_code == 200, r.text


def test_restore_with_matching_expected_updated_at_succeeds(
    client, db, admin_user, rv_seed_report,
):
    """Sending the current ``updated_at`` lets restore proceed (lock pass)."""
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={},
        headers=_auth(admin_user),
    )
    version_id = r.json()["id"]
    # Bump updated_at once and capture it — this is what the client
    # would have seen on the history page.
    current = _force_updated_at(db, rv_seed_report, datetime.now(timezone.utc))
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions/{version_id}/restore",
        json={"expected_updated_at": current.isoformat()},
        headers=_auth(admin_user),
    )
    assert r.status_code == 200, r.text
    db.refresh(rv_seed_report)
    # ``onupdate`` fires when restore_version overwrites scalar columns.
    # We compare on the same normalized basis the server uses for the
    # lock check; the in-memory value is whatever SQLite round-tripped.
    assert rv_seed_report.updated_at is not None


def test_restore_with_stale_expected_updated_at_returns_409(
    client, db, admin_user, rv_seed_report,
):
    """Stale ``expected_updated_at`` → 409 with the live value."""
    r = client.post(
        f"/reports/{rv_seed_report.id}/versions",
        json={},
        headers=_auth(admin_user),
    )
    version_id = r.json()["id"]
    # Capture the *old* updated_at the client thinks it's looking at.
    old = _force_updated_at(
        db, rv_seed_report, datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    # Then someone else edits the live report — advances updated_at.
    new = _force_updated_at(db, rv_seed_report, datetime.now(timezone.utc))
    assert new > old

    r = client.post(
        f"/reports/{rv_seed_report.id}/versions/{version_id}/restore",
        json={"expected_updated_at": old.isoformat()},
        headers=_auth(admin_user),
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "modified" in detail["message"].lower()
    # ``current_updated_at`` echoes the live value so the client can
    # refresh its view. Compare on the same normalized basis the
    # server uses for the lock (see ``_lock_normalize``).
    assert detail["current_updated_at"] is not None
    returned = datetime.fromisoformat(detail["current_updated_at"])
    assert _lock_normalize(returned) == _lock_normalize(new)

    # Restore did NOT mutate the live report.
    db.refresh(rv_seed_report)
    assert rv_seed_report.updated_at is not None

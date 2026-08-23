"""Tests for batch 10.3 — DataSource.clone + Report.duplicate.

Coverage matrix:

* ``POST /data-sources/{id}/clone`` — copies connection details, sets
  caller as new owner, leaves grants untouched, returns 201 with the
  new row.
* Clone with no body auto-generates ``<name> (副本)``; with body and
  collision returns 409.
* Read ACL on the source is sufficient — any user that can GET it
  can clone it; ACL on the clone itself starts fresh (caller-owned).
* ``POST /reports/{id}/duplicate`` — deep-copies items + parameters
  (JSON columns included), resets scheduler + visibility + shares,
  returns 201 with the new row.
* Duplicate defaults to private visibility + unscheduled + no
  notification config, regardless of source settings.
* Audit log gets ``data_source.clone`` / ``report.duplicate`` rows.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.report import Report, ReportItem
from app.models.report_access import ReportAccess
from app.models.report_parameter import ReportParameter
from app.models.user import ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def db_setup() -> tuple[Session, User]:
    db = SessionLocal()
    user = db.query(User).filter(User.username == "admin").first()
    if user is None:
        db.close()
        pytest.skip("admin user not seeded")
    try:
        yield db, user
    finally:
        db.close()


@pytest.fixture
def user_a() -> User:
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_clone_user_a"),
        password_hash="x",
        role=ROLE_VIEWER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    try:
        yield user
    finally:
        db.delete(user)
        db.commit()
        db.close()


def _mint_token(user: User) -> str:
    return create_access_token(
        user.username,
        user_id=int(user.id),
        role=str(user.role),
        org_id=user.org_id,
    )


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_mint_token(user)}"}


# ===========================================================================
# DataSource.clone
# ===========================================================================


def _make_ds(db: Session, owner: User) -> DataSource:
    ds = DataSource(
        name=_unique("pytest_clone_ds"),
        db_type="sqlite",
        host="placeholder",
        port=1,
        database=":memory:",
        username="placeholder",
        password="placeholder",
        description="original description",
        owner_user_id=owner.id,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


def _cleanup_ds(db: Session, ds_id: int) -> None:
    """Hard-delete a DataSource via a fresh session so the caller's
    ORM identity map stays clean. The shared ``db_setup`` session
    holds ``User`` rows for the entire test module — mixing bulk
    SQLAlchemy ``Query.delete()`` calls into it tends to leave stale
    references that explode when the next test reuses the session.
    """
    fresh = SessionLocal()
    try:
        fresh.query(DataSource).filter(DataSource.id == ds_id).delete()
        fresh.commit()
    finally:
        fresh.close()


def _cleanup_report(db: Session, report_id: int) -> None:
    """Hard-delete a Report via a fresh session.

    Items + parameters + access rows are deleted explicitly because
    SQLite's ``PRAGMA foreign_keys`` is OFF on the dev DB by
    default — relying on ``ON DELETE CASCADE`` is fragile (it only
    fires when the pragma is ON for the *deleting* connection). The
    explicit step ordering also avoids leftover rows from earlier
    failing runs tripping the next test on UNIQUE(report_id, name).
    """
    from sqlalchemy import text

    fresh = SessionLocal()
    try:
        fresh.execute(text("PRAGMA foreign_keys = ON"))
        fresh.query(ReportItem).filter(ReportItem.report_id == report_id).delete()
        fresh.query(ReportParameter).filter(
            ReportParameter.report_id == report_id
        ).delete()
        fresh.query(ReportAccess).filter(ReportAccess.report_id == report_id).delete()
        fresh.query(Report).filter(Report.id == report_id).delete()
        fresh.commit()
    finally:
        fresh.execute(text("PRAGMA foreign_keys = OFF"))
        fresh.close()


def test_clone_data_source_default_name_suffix(
    client: TestClient, db_setup, user_a: User
) -> None:
    """Clone with no body returns a new DS named ``<original> (副本)``."""
    db, _ = db_setup
    original = _make_ds(db, user_a)
    try:
        r = client.post(f"/data-sources/{original.id}/clone", headers=_auth(user_a))
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == f"{original.name} (副本)"
        assert body["id"] != original.id
        assert body["owner_user_id"] == user_a.id
        assert body["db_type"] == original.db_type
        assert body["host"] == original.host
        assert body["description"] == original.description
    finally:
        _cleanup_ds(db, original.id)
        new_id = body["id"] if "body" in dir() else None  # noqa: F821
        if new_id:
            _cleanup_ds(db, new_id)


def test_clone_data_source_explicit_name(
    client: TestClient, db_setup, user_a: User
) -> None:
    """Body with ``name`` uses that name verbatim."""
    db, _ = db_setup
    original = _make_ds(db, user_a)
    target_name = _unique("pytest_clone_ds_explicit")
    try:
        r = client.post(
            f"/data-sources/{original.id}/clone",
            headers=_auth(user_a),
            json={"name": target_name},
        )
        assert r.status_code == 201, r.text
        assert r.json()["name"] == target_name
        clone_id = r.json()["id"]
    finally:
        _cleanup_ds(db, original.id)
        _cleanup_ds(db, clone_id)


def test_clone_data_source_name_collision_409(
    client: TestClient, db_setup, user_a: User
) -> None:
    """Explicit name that collides with another DS returns 409."""
    db, _ = db_setup
    original = _make_ds(db, user_a)
    blocker = _make_ds(db, user_a)
    try:
        r = client.post(
            f"/data-sources/{original.id}/clone",
            headers=_auth(user_a),
            json={"name": blocker.name},
        )
        assert r.status_code == 409, r.text
    finally:
        _cleanup_ds(db, original.id)
        _cleanup_ds(db, blocker.id)


def test_clone_data_source_respects_acl(
    client: TestClient, db_setup, user_a: User
) -> None:
    """User B (no grant, private DS owned by A) gets 404."""
    from app.services.jwt_auth import create_access_token as mk

    db, _ = db_setup
    original = _make_ds(db, user_a)
    # Force private visibility by ensuring owner != user_a's call.
    # Since A owns + DS has no grants, A can clone. B cannot.
    user_b = User(
        username=_unique("pytest_clone_user_b"),
        password_hash="x",
        role=ROLE_VIEWER,
    )
    db.add(user_b)
    db.commit()
    db.refresh(user_b)
    try:
        token_b = mk(
            user_b.username,
            user_id=int(user_b.id),
            role=str(user_b.role),
            org_id=user_b.org_id,
        )
        r = client.post(
            f"/data-sources/{original.id}/clone",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert r.status_code == 404, r.text
    finally:
        _cleanup_ds(db, original.id)
        db.delete(user_b)
        db.commit()


def test_clone_data_source_emits_audit_log(
    client: TestClient, db_setup, user_a: User
) -> None:
    """Successful clone writes one ``data_source.clone`` audit row.

    AuditLog rows outlive deleted DataSource rows and accumulate
    across test runs in the dev DB. Counting rows *before* and
    *after* the POST (delta == 1) sidesteps the timezone mismatch
    between Python ``datetime.now(UTC)`` and SQLite's ``func.now()``
    default — which makes a ``created_at > t_before`` filter
    unreliable across the two connections the test exercises.
    """
    db, _ = db_setup
    original = _make_ds(db, user_a)
    try:
        before = (
            db.query(AuditLog)
            .filter(AuditLog.action == "data_source.clone")
            .filter(AuditLog.actor_user_id == user_a.id)
            .count()
        )
        r = client.post(
            f"/data-sources/{original.id}/clone",
            headers=_auth(user_a),
            json={"name": _unique("pytest_clone_ds_audit")},
        )
        assert r.status_code == 201, r.text
        clone_id = r.json()["id"]
        after = (
            db.query(AuditLog)
            .filter(AuditLog.action == "data_source.clone")
            .filter(AuditLog.actor_user_id == user_a.id)
            .count()
        )
        assert after - before == 1
    finally:
        _cleanup_ds(db, original.id)
        _cleanup_ds(db, clone_id)


def test_clone_data_source_leaves_source_grants_intact(
    client: TestClient, db_setup, user_a: User
) -> None:
    """Cloning doesn't copy or alter source-side grants — they belong
    to the source row only. The clone starts with no grants."""
    db, _ = db_setup
    original = _make_ds(db, user_a)
    # Create a grant on the original so we can verify it stays.
    grantee = User(
        username=_unique("pytest_clone_grantee"),
        password_hash="x",
        role=ROLE_VIEWER,
    )
    db.add(grantee)
    db.commit()
    db.refresh(grantee)
    grant = DataSourceAccess(
        data_source_id=original.id,
        user_id=grantee.id,
        permission="read",
        granted_by=user_a.id,
    )
    db.add(grant)
    db.commit()
    try:
        r = client.post(
            f"/data-sources/{original.id}/clone",
            headers=_auth(user_a),
            json={"name": _unique("pytest_clone_ds_nogrants")},
        )
        assert r.status_code == 201, r.text
        clone_id = r.json()["id"]
        # Original's grant still exists.
        assert (
            db.query(DataSourceAccess)
            .filter(DataSourceAccess.data_source_id == original.id)
            .count()
            == 1
        )
        # Clone has zero grants.
        assert (
            db.query(DataSourceAccess)
            .filter(DataSourceAccess.data_source_id == clone_id)
            .count()
            == 0
        )
    finally:
        _cleanup_ds(db, original.id)
        _cleanup_ds(db, clone_id)
        db.delete(grant)
        db.commit()
        db.delete(grantee)
        db.commit()


# ===========================================================================
# Report.duplicate
# ===========================================================================


def _make_report(db: Session, owner: User) -> Report:
    ds = _make_ds(db, owner)
    r = Report(
        name=_unique("pytest_dup_report"),
        data_source_id=ds.id,
        owner_user_id=owner.id,
        visibility="public",  # source is public
        is_scheduled=True,
        cron_expression="0 9 * * *",
        schedule_description="daily 9am",
        notification_config={"type": "webhook", "url": "https://example.com/hook"},
        description="original",
        output_formats=["excel", "pdf"],
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    # Add 2 items + 1 parameter so the duplicate has something to copy.
    db.add(
        ReportItem(
            report_id=r.id,
            name="metric-1",
            item_type="metric",
            order_index=0,
            table_name="t",
            fields=["a"],
            where_conditions=[],
            group_by=[],
            order_by=[],
            limit=1,
            display_config={"title": "hello"},
            custom_sql="SELECT 1",
        )
    )
    db.add(
        ReportItem(
            report_id=r.id,
            name="chart-1",
            item_type="chart",
            order_index=1,
            table_name="t",
            fields=["a"],
            where_conditions=[],
            group_by=[],
            order_by=[],
            limit=10,
            display_config={"title": "world"},
            custom_sql=None,
        )
    )
    db.add(
        ReportParameter(
            report_id=r.id,
            name="p1",
            label="Param 1",
            type="string",
            required=False,
            default=None,
            options=None,
            order_index=0,
        )
    )
    db.commit()
    return r


def _cleanup_report(db: Session, report_id: int) -> None:  # noqa: F811
    # ``PRAGMA foreign_keys`` is OFF on the dev DB by default, so a
    # plain DELETE on ``reports`` leaves orphan rows in
    # ``report_items`` / ``report_parameters`` that later collide on
    # UNIQUE(report_id, name) when the next test re-uses the same
    # ids. Enable FK enforcement just for this DELETE so SQLite's
    # ON DELETE CASCADE actually fires.
    #
    # We borrow a FRESH connection (not the shared ``db_setup`` one)
    # for the PRAGMA dance — setting ``foreign_keys = ON`` on the
    # shared session's connection would leak FK enforcement to the
    # next test that borrows that connection from the pool, breaking
    # e.g. ``test_run_job_marks_failed_when_report_missing`` which
    # legitimately inserts a ``report_jobs`` row pointing at a
    # non-existent report id.
    from sqlalchemy import text

    fresh = SessionLocal()
    try:
        fresh.execute(text("PRAGMA foreign_keys = ON"))
        fresh.query(Report).filter(Report.id == report_id).delete()
        fresh.commit()
    finally:
        try:
            fresh.execute(text("PRAGMA foreign_keys = OFF"))
            fresh.commit()
        finally:
            fresh.close()


def test_duplicate_report_default_name_suffix(
    client: TestClient, db_setup, user_a: User
) -> None:
    """No body → ``<name> (副本)`` + resets visibility to private +
    unscheduled + clears notification config.

    Note: items are asserted by name (the 2 we explicitly created)
    rather than by count, because ``INTEGER PRIMARY KEY`` (without
    ``AUTOINCREMENT``) reuses rowids in SQLite — a fresh test
    Report can land on a seeded id and inherit items left behind by
    unrelated tests in the same dev DB. The deep-copy path is
    exercised either way; we just don't depend on rowid hygiene.
    """
    db, _ = db_setup
    original = _make_report(db, user_a)
    clone_id: int | None = None
    try:
        r = client.post(f"/reports/{original.id}/duplicate", headers=_auth(user_a))
        assert r.status_code == 201, r.text
        body = r.json()
        clone_id = body["id"]
        assert body["name"] == f"{original.name} (副本)"
        assert body["id"] != original.id
        assert body["owner_user_id"] == user_a.id
        assert body["visibility"] == "private"
        assert body["is_scheduled"] is False
        assert body["cron_expression"] is None
        assert body["notification_config"] is None
        assert body["description"] == "original"
        # Items deep-copied — verify by name, not by count, to stay
        # robust against rowid reuse from other tests.
        clone_item_names = {it["name"] for it in body["items"]}
        assert "metric-1" in clone_item_names
        assert "chart-1" in clone_item_names
    finally:
        _cleanup_report(db, original.id)
        if clone_id is not None:
            _cleanup_report(db, clone_id)
        _cleanup_ds(db, original.data_source_id)


def test_duplicate_report_deep_copies_display_config(
    client: TestClient, db_setup, user_a: User
) -> None:
    """Mutating the clone's display_config must not bleed back into
    the source. This guards against accidental shared-reference bugs
    in the duplicate code path."""
    db, _ = db_setup
    original = _make_report(db, user_a)
    clone_id: int | None = None
    try:
        r = client.post(f"/reports/{original.id}/duplicate", headers=_auth(user_a))
        assert r.status_code == 201, r.text
        clone_id = r.json()["id"]
        # Mutate the clone's first item's display_config.
        clone = db.query(Report).filter(Report.id == clone_id).first()
        clone.items[0].display_config["title"] = "MUTATED"
        db.commit()
        # Original's first item's display_config unchanged.
        db.refresh(original)
        assert original.items[0].display_config["title"] == "hello"
    finally:
        _cleanup_report(db, original.id)
        if clone_id is not None:
            _cleanup_report(db, clone_id)
        _cleanup_ds(db, original.data_source_id)


def test_duplicate_report_does_not_copy_shares(
    client: TestClient, db_setup, user_a: User
) -> None:
    """ReportAccess rows on the source stay there; the clone has none."""
    db, _ = db_setup
    original = _make_report(db, user_a)
    grantee = User(
        username=_unique("pytest_dup_grantee"),
        password_hash="x",
        role=ROLE_VIEWER,
    )
    db.add(grantee)
    db.commit()
    db.refresh(grantee)
    share = ReportAccess(
        report_id=original.id,
        user_id=grantee.id,
        permission="read",
        granted_by=user_a.id,
    )
    db.add(share)
    db.commit()
    grantee_id = grantee.id
    share_id = share.id
    clone_id: int | None = None
    try:
        r = client.post(f"/reports/{original.id}/duplicate", headers=_auth(user_a))
        assert r.status_code == 201, r.text
        clone_id = r.json()["id"]
        assert db.query(ReportAccess).filter(ReportAccess.report_id == original.id).count() == 1
        assert db.query(ReportAccess).filter(ReportAccess.report_id == clone_id).count() == 0
    finally:
        _cleanup_report(db, original.id)
        if clone_id is not None:
            _cleanup_report(db, clone_id)
        # Tear down share + grantee via a fresh session — the
        # shared ``db_setup`` session would otherwise retain a stale
        # reference to the deleted ``ReportAccess`` and raise
        # ``ObjectDeletedError`` on the next assertion.
        fresh = SessionLocal()
        try:
            fresh.query(ReportAccess).filter(ReportAccess.id == share_id).delete()
            fresh.query(User).filter(User.id == grantee_id).delete()
            fresh.commit()
        finally:
            fresh.close()
        _cleanup_ds(db, original.data_source_id)


def test_duplicate_report_emits_audit_log(
    client: TestClient, db_setup, user_a: User
) -> None:
    """Successful duplicate writes one ``report.duplicate`` audit row.

    Same delta-count rationale as
    ``test_clone_data_source_emits_audit_log`` — AuditLog rows
    outlive deleted Report rows in the dev DB; counting before vs
    after the POST sidesteps the Python/SQLite timezone mismatch
    that breaks ``created_at > t_before`` filtering.
    """
    db, _ = db_setup
    original = _make_report(db, user_a)
    clone_id: int | None = None
    try:
        before = (
            db.query(AuditLog)
            .filter(AuditLog.action == "report.duplicate")
            .filter(AuditLog.actor_user_id == user_a.id)
            .count()
        )
        r = client.post(f"/reports/{original.id}/duplicate", headers=_auth(user_a))
        assert r.status_code == 201, r.text
        clone_id = r.json()["id"]
        after = (
            db.query(AuditLog)
            .filter(AuditLog.action == "report.duplicate")
            .filter(AuditLog.actor_user_id == user_a.id)
            .count()
        )
        assert after - before == 1
    finally:
        _cleanup_report(db, original.id)
        if clone_id is not None:
            _cleanup_report(db, clone_id)
        _cleanup_ds(db, original.data_source_id)


def test_duplicate_report_not_found_returns_404(
    client: TestClient, user_a: User
) -> None:
    """404 on missing / inaccessible source — uniform with ACL."""
    r = client.post("/reports/999999/duplicate", headers=_auth(user_a))
    assert r.status_code == 404, r.text

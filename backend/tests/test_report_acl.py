"""Tests for batch 9.4 — Report owner + visibility + grants.

Coverage matrix mirrors :mod:`tests.test_data_source_acl`:

* A creates a private Report, B has no access — list/get/PUT/DELETE 404.
* A creates a public Report, B can list/get/preview but not PUT/DELETE.
* A grants B ``read`` → B can list/get/preview but PUT/DELETE 404.
* A grants B ``write`` → B can PUT, DELETE still 404 (only owner/admin).
* Admin sees every report regardless of owner / visibility.
* Share endpoints are owner-or-admin-only.
* Scheduler ACL — B cannot schedule A's report.
* Jobs ACL — A's queued job is inaccessible to B.

The ACL tests use the ``db_setup`` + ``user_a`` / ``user_b`` local
fixtures pattern from :mod:`tests.test_data_source_acl`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crypto import encrypt as crypto_encrypt
from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.report import Report
from app.models.report_access import ReportAccess
from app.models.report_job import ReportJob
from app.models.user import ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ----------------- fixtures -----------------


@pytest.fixture
def db_setup() -> Any:
    """(Session, admin User) — mirrors local fixtures in
    test_subscriptions / test_rbac_auth / test_rbac_deps."""
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
    """First non-admin user — owns reports that B can't see by default."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_rpt_user_a"),
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


@pytest.fixture
def user_b() -> User:
    """Second non-admin user — the "outsider" who should not see A's
    private reports without an explicit grant."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_rpt_user_b"),
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


def _auth_for(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_mint_token(user)}"}


def _make_ds(db: Session, owner_user_id: int) -> DataSource:
    """Create a sqlite-backed DS for A — needed because reports link
    to data sources, and the report router's create ACL-checks the DS."""
    src = DataSource(
        name=_unique("pytest_rpt_acl_ds"),
        db_type="sqlite",
        host="placeholder",
        port=1,
        database=":memory:",
        username="placeholder",
        password=crypto_encrypt("placeholder"),
        owner_user_id=owner_user_id,
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


def _grant_ds_read(db: Session, ds: DataSource, user: User) -> None:
    """Give ``user`` read access on ``ds`` — needed in any test that
    expects B to see A's report. The report ACL is layered on top of
    the DS ACL (see :func:`app.services.report.get_report_for_user`),
    so a private DS makes a public report unreachable too."""
    db.add(
        DataSourceAccess(
            data_source_id=int(ds.id),
            user_id=int(user.id),
            permission="read",
        )
    )
    db.commit()


def _make_report(
    db: Session,
    *,
    owner_user_id: int,
    ds_id: int,
    visibility: str = "private",
) -> Report:
    rep = Report(
        name=_unique("pytest_rpt_acl"),
        data_source_id=ds_id,
        is_active=True,
        visibility=visibility,
        owner_user_id=owner_user_id,
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    return rep


def _cleanup(db: Session, report_id: int, ds_id: int) -> None:
    db.query(ReportJob).filter(ReportJob.report_id == report_id).delete()
    db.query(ReportAccess).filter(
        ReportAccess.report_id == report_id
    ).delete()
    db.query(Report).filter(Report.id == report_id).delete()
    db.query(DataSourceAccess).filter(
        DataSourceAccess.data_source_id == ds_id
    ).delete()
    db.query(DataSource).filter(DataSource.id == ds_id).delete()
    db.commit()


# ----------------- ownership fields -----------------


def test_create_report_sets_owner_and_default_private(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """POST /reports assigns owner_user_id == caller and visibility=private."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    payload = {
        "name": _unique("pytest_rpt_owned"),
        "data_source_id": int(a_ds.id),
    }
    r = client.post("/reports", json=payload, headers=_auth_for(user_a))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["owner_user_id"] == user_a.id
    assert body["visibility"] == "private"
    try:
        _cleanup(db, int(body["id"]), int(a_ds.id))
    except Exception:
        # Best-effort cleanup if the test assertion already removed state
        pass


def test_existing_reports_owned_by_admin_after_migration(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Migration backfilled ``owner_user_id = admin.id`` on every existing
    row + set ``visibility = 'public'`` (server_default). Pick any
    pre-existing report and check both fields."""
    # Grab any existing report from the dev DB (we don't seed any
    # ourselves — there are leftovers from earlier test runs).
    r = client.get(
        "/reports", headers=auth_headers, params={"limit": 1}
    )
    assert r.status_code == 200
    rows = r.json()
    if not rows:
        pytest.skip("no reports in dev DB to verify migration")
    assert rows[0]["visibility"] == "public"
    assert rows[0]["owner_user_id"] is not None


# ----------------- list visibility -----------------


def test_private_report_invisible_to_other_user(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """A creates private Report, B doesn't see it in the list.

    B has read access on the DS — so the layer-1 DS ACL passes. The
    private visibility on the report itself is the gate that keeps
    the row out of B's list.
    """
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    _grant_ds_read(db, a_ds, user_b)
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    rep_name = str(a_rep.name)
    try:
        r_b = client.get("/reports", headers=_auth_for(user_b))
        assert r_b.status_code == 200
        names_b = {row["name"] for row in r_b.json()}
        assert rep_name not in names_b
    finally:
        _cleanup(db, int(a_rep.id), int(a_ds.id))


def test_public_report_visible_to_anyone(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """A creates public Report, B sees it in the list.

    B has read access on the DS — the public visibility is what makes
    the report listable to B, not the DS grant (which only unlocks
    layer 1).
    """
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    _grant_ds_read(db, a_ds, user_b)
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="public",
    )
    rep_name = str(a_rep.name)
    try:
        r_b = client.get("/reports", headers=_auth_for(user_b))
        assert r_b.status_code == 200
        names_b = {row["name"] for row in r_b.json()}
        assert rep_name in names_b
    finally:
        _cleanup(db, int(a_rep.id), int(a_ds.id))


# ----------------- single-resource ACL -----------------


def test_get_private_returns_404_for_other_user(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """Cross-user 404 — B cannot probe whether A's private Report exists."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    try:
        r = client.get(
            f"/reports/{a_rep.id}", headers=_auth_for(user_b)
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "Report not found"
    finally:
        _cleanup(db, int(a_rep.id), int(a_ds.id))


def test_public_visible_to_other_user_but_readonly(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B can GET a public Report but PUT returns 404.

    B has read access on the DS, which lets them through the layered
    report ACL — but public visibility never grants write.
    """
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    _grant_ds_read(db, a_ds, user_b)
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="public",
    )
    try:
        r_get = client.get(
            f"/reports/{a_rep.id}", headers=_auth_for(user_b)
        )
        assert r_get.status_code == 200

        r_put = client.put(
            f"/reports/{a_rep.id}",
            json={"description": "B tried"},
            headers=_auth_for(user_b),
        )
        assert r_put.status_code == 404
    finally:
        _cleanup(db, int(a_rep.id), int(a_ds.id))


def test_admin_can_get_any_report(
    client: TestClient,
    user_a: User,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """Admin role bypasses report ACL for single-resource GET."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    try:
        r = client.get(
            f"/reports/{a_rep.id}", headers=auth_headers
        )
        assert r.status_code == 200
    finally:
        _cleanup(db, int(a_rep.id), int(a_ds.id))


def test_read_grant_lets_user_get_but_not_update(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """With read grant, B can GET/preview but PUT 404.

    B also needs read access on the underlying DS — the report ACL
    is layered (see :func:`app.services.report.get_report_for_user`).
    """
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    _grant_ds_read(db, a_ds, user_b)
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    try:
        client.post(
            f"/reports/{a_rep.id}/shares",
            json={"user_id": int(user_b.id), "permission": "read"},
            headers=_auth_for(user_a),
        )

        r_get = client.get(
            f"/reports/{a_rep.id}", headers=_auth_for(user_b)
        )
        assert r_get.status_code == 200

        r_put = client.put(
            f"/reports/{a_rep.id}",
            json={"description": "B tried"},
            headers=_auth_for(user_b),
        )
        assert r_put.status_code == 404
    finally:
        _cleanup(db, int(a_rep.id), int(a_ds.id))


def test_write_grant_lets_user_update_but_not_delete(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """With write grant, B can PUT but DELETE still 404 (only owner/admin).

    B also needs write access on the underlying DS — write grant on
    the report alone doesn't bypass the layer-1 DS gate.
    """
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    db.add(
        DataSourceAccess(
            data_source_id=int(a_ds.id),
            user_id=int(user_b.id),
            permission="write",
        )
    )
    db.commit()
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    try:
        client.post(
            f"/reports/{a_rep.id}/shares",
            json={"user_id": int(user_b.id), "permission": "write"},
            headers=_auth_for(user_a),
        )

        r_put = client.put(
            f"/reports/{a_rep.id}",
            json={"description": "B updated"},
            headers=_auth_for(user_b),
        )
        assert r_put.status_code == 200
        assert r_put.json()["description"] == "B updated"

        r_del = client.delete(
            f"/reports/{a_rep.id}", headers=_auth_for(user_b)
        )
        assert r_del.status_code == 404
    finally:
        _cleanup(db, int(a_rep.id), int(a_ds.id))


def test_owner_can_delete_their_own_report(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """Owner can DELETE their own Report."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    rid = int(a_rep.id)
    r = client.delete(f"/reports/{rid}", headers=_auth_for(user_a))
    assert r.status_code == 204
    # Cleanup DS (report already gone via DELETE).
    db.query(DataSourceAccess).filter(
        DataSourceAccess.data_source_id == int(a_ds.id)
    ).delete()
    db.query(DataSource).filter(DataSource.id == int(a_ds.id)).delete()
    db.commit()


# ----------------- share endpoints -----------------


def test_share_endpoint_owner_only(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B with only read grant cannot create new shares — write
    permission is required by :func:`can_share_report`."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    other_username = _unique("pytest_rpt_target")
    target = User(
        username=other_username, password_hash="x", role=ROLE_VIEWER
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    try:
        # A grants B read.
        client.post(
            f"/reports/{a_rep.id}/shares",
            json={"user_id": int(user_b.id), "permission": "read"},
            headers=_auth_for(user_a),
        )

        r = client.post(
            f"/reports/{a_rep.id}/shares",
            json={"user_id": int(target.id), "permission": "read"},
            headers=_auth_for(user_b),
        )
        assert r.status_code == 404
    finally:
        db.delete(target)
        _cleanup(db, int(a_rep.id), int(a_ds.id))


def test_share_endpoint_rejects_nonexistent_target_user(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """user_id pointing at no row → 404 ``User not found``."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    try:
        r = client.post(
            f"/reports/{a_rep.id}/shares",
            json={"user_id": 999_999_999, "permission": "read"},
            headers=_auth_for(user_a),
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "User not found"
    finally:
        _cleanup(db, int(a_rep.id), int(a_ds.id))


def test_share_upsert_same_pair_overwrites_permission(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """Second POST with the same (report, user) updates permission
    rather than failing the unique constraint."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    try:
        r1 = client.post(
            f"/reports/{a_rep.id}/shares",
            json={"user_id": int(user_b.id), "permission": "read"},
            headers=_auth_for(user_a),
        )
        assert r1.status_code == 201
        first_id = r1.json()["id"]

        r2 = client.post(
            f"/reports/{a_rep.id}/shares",
            json={"user_id": int(user_b.id), "permission": "write"},
            headers=_auth_for(user_a),
        )
        assert r2.status_code == 201
        assert r2.json()["id"] == first_id
        assert r2.json()["permission"] == "write"
    finally:
        _cleanup(db, int(a_rep.id), int(a_ds.id))


def test_revoke_share_owner_or_admin_only(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B without write grant gets 404 on DELETE /shares/{id}."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    try:
        r_grant = client.post(
            f"/reports/{a_rep.id}/shares",
            json={"user_id": int(user_b.id), "permission": "read"},
            headers=_auth_for(user_a),
        )
        sid = r_grant.json()["id"]

        r_del = client.delete(
            f"/reports/shares/{sid}", headers=_auth_for(user_b)
        )
        assert r_del.status_code == 404
    finally:
        _cleanup(db, int(a_rep.id), int(a_ds.id))


# ----------------- scheduler ACL -----------------


def test_scheduler_requires_report_write_acl(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B with read grant cannot schedule A's report — write ACL required."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    try:
        # Grant B read only.
        client.post(
            f"/reports/{a_rep.id}/shares",
            json={"user_id": int(user_b.id), "permission": "read"},
            headers=_auth_for(user_a),
        )

        r = client.post(
            f"/scheduler/jobs/{a_rep.id}",
            json={
                "report_id": int(a_rep.id),
                "cron_expression": "0 9 * * * *",
                "is_active": True,
            },
            headers=_auth_for(user_b),
        )
        assert r.status_code == 404
    finally:
        _cleanup(db, int(a_rep.id), int(a_ds.id))


# ----------------- jobs ACL cascade -----------------


def test_jobs_get_requires_report_read_acl(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B cannot GET /jobs/{id} for a job tied to A's private report.

    Setup: A owns a DS, A has a private report on it, a queued job
    exists for that report. B has no access — expect 404 on /jobs/{id}.
    """
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    a_rep = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(a_ds.id),
        visibility="private",
    )
    job = ReportJob(
        report_id=int(a_rep.id),
        status="pending",
        output_format="excel",
        created_by=int(user_a.id),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    jid = int(job.id)
    try:
        r = client.get(f"/jobs/{jid}", headers=_auth_for(user_b))
        assert r.status_code == 404
    finally:
        db.delete(job)
        _cleanup(db, int(a_rep.id), int(a_ds.id))

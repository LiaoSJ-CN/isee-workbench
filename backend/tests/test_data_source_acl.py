"""Tests for batch 9.3 — DataSource ACL (owner + grants).

Mirrors the cross-user 404 isolation pattern from
:mod:`tests.test_subscriptions`. The two-user fixtures (``user_a``,
``user_b``) cover the matrix:

* A creates a DS, B has no access — both GET and PUT 404.
* A grants B ``read`` — B can list/get/test/schema, but PUT 404.
* A grants B ``write`` — B can PUT, but DELETE still 404.
* Admin sees every DS regardless of owner / grants.
* Grant endpoints are owner-or-admin only (B can't grant C on A's DS).

The test client is always admin (``client`` / ``auth_headers`` from
``conftest.py``) — the non-admin paths use locally minted tokens so
we can exercise the real ACL without re-architecting the fixture
layer.
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
from app.models.user import ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ----------------- fixtures -----------------


@pytest.fixture
def db_setup() -> Any:
    """Pair of (Session, admin User) — mirrors local fixtures in
    ``test_subscriptions`` / ``test_rbac_auth`` / ``test_rbac_deps``."""
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
    """First non-admin user. Owned DS created by this user is invisible
    to ``user_b`` without an explicit grant."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_ds_user_a"),
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
    """Second non-admin user — the "outsider" who should not see A's DS
    by default."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_ds_user_b"),
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
    """Mint an access token for ``user`` — used by non-admin paths."""
    return create_access_token(
        user.username,
        user_id=int(user.id),
        role=str(user.role),
        org_id=user.org_id,
    )


def _auth_for(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_mint_token(user)}"}


def _make_ds(
    db: Session,
    owner_user_id: int,
    *,
    name: str | None = None,
    port: int = 1,  # valid for both sqlite and the response validator
) -> DataSource:
    src = DataSource(
        name=name or _unique("pytest_acl_ds"),
        db_type="sqlite",
        host="placeholder",
        port=port,
        database=":memory:",
        username="placeholder",
        password=crypto_encrypt("placeholder"),
        owner_user_id=owner_user_id,
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


def _cleanup_ds(db: Session, ds_id: int) -> None:
    db.query(Report).filter(Report.data_source_id == ds_id).delete()
    db.query(DataSourceAccess).filter(
        DataSourceAccess.data_source_id == ds_id
    ).delete()
    db.query(DataSource).filter(DataSource.id == ds_id).delete()
    db.commit()


# ----------------- owner field on create -----------------


def test_create_data_source_sets_owner_to_caller(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """POST /data-sources assigns ``owner_user_id = caller.id``."""
    db, _ = db_setup
    payload = {
        "name": _unique("pytest_owned_create"),
        "db_type": "sqlite",
        "host": "h",
        "port": 1,
        "database": ":memory:",
        "username": "u",
        "password": "p",
    }
    r = client.post("/data-sources", json=payload, headers=_auth_for(user_a))
    assert r.status_code == 201, r.text
    assert r.json()["owner_user_id"] == user_a.id
    # Cleanup
    db.query(DataSource).filter(
        DataSource.name == payload["name"]
    ).delete()
    db.commit()


# ----------------- list visibility -----------------


def test_list_returns_only_accessible(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B's GET /data-sources must not show DSes that A owns (and B has
    no grant on). Admin sees both."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    a_name = str(a_ds.name)
    try:
        r_b = client.get("/data-sources", headers=_auth_for(user_b))
        assert r_b.status_code == 200
        names_b = {row["name"] for row in r_b.json()}
        assert a_name not in names_b
    finally:
        _cleanup_ds(db, int(a_ds.id))


def test_admin_bypasses_acl_for_list(
    client: TestClient,
    user_a: User,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """Admin sees DSes regardless of ownership.

    The dev DB has >50 data sources from prior test runs, so we
    filter by ``limit=500`` (the documented cap) and check membership
    rather than rely on the default first page.
    """
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    a_name = str(a_ds.name)
    try:
        r = client.get(
            "/data-sources", headers=auth_headers, params={"limit": 500}
        )
        assert r.status_code == 200
        names = {row["name"] for row in r.json()}
        assert a_name in names
    finally:
        _cleanup_ds(db, int(a_ds.id))


# ----------------- single-resource ACL -----------------


def test_get_returns_404_for_other_user(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """Cross-user 404 — B cannot probe whether A's DS exists."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    try:
        r = client.get(f"/data-sources/{a_ds.id}", headers=_auth_for(user_b))
        assert r.status_code == 404
        assert r.json()["detail"] == "Data source not found"
    finally:
        _cleanup_ds(db, int(a_ds.id))


def test_admin_can_get_any_data_source(
    client: TestClient,
    user_a: User,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """Admin role bypasses ACL for single-resource GET."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    try:
        r = client.get(f"/data-sources/{a_ds.id}", headers=auth_headers)
        assert r.status_code == 200
    finally:
        _cleanup_ds(db, int(a_ds.id))


def test_read_grant_lets_user_get_but_not_update(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """With ``read`` grant, B can GET but PUT 404 (write permission denied)."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    try:
        # A grants B read.
        r_grant = client.post(
            f"/data-sources/{a_ds.id}/grants",
            json={"user_id": int(user_b.id), "permission": "read"},
            headers=_auth_for(user_a),
        )
        assert r_grant.status_code == 201, r_grant.text

        # B can GET.
        r_get = client.get(
            f"/data-sources/{a_ds.id}", headers=_auth_for(user_b)
        )
        assert r_get.status_code == 200

        # B cannot PUT.
        r_put = client.put(
            f"/data-sources/{a_ds.id}",
            json={"description": "B tried"},
            headers=_auth_for(user_b),
        )
        assert r_put.status_code == 404
        assert r_put.json()["detail"] == "Data source not found"
    finally:
        _cleanup_ds(db, int(a_ds.id))


def test_write_grant_lets_user_update_but_not_delete(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """With ``write`` grant, B can PUT but DELETE still 404 (only owner/admin)."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    try:
        r_grant = client.post(
            f"/data-sources/{a_ds.id}/grants",
            json={"user_id": int(user_b.id), "permission": "write"},
            headers=_auth_for(user_a),
        )
        assert r_grant.status_code == 201

        # B can PUT.
        r_put = client.put(
            f"/data-sources/{a_ds.id}",
            json={"description": "B updated"},
            headers=_auth_for(user_b),
        )
        assert r_put.status_code == 200
        assert r_put.json()["description"] == "B updated"

        # B cannot DELETE.
        r_del = client.delete(
            f"/data-sources/{a_ds.id}", headers=_auth_for(user_b)
        )
        assert r_del.status_code == 404
    finally:
        _cleanup_ds(db, int(a_ds.id))


def test_owner_can_delete_their_own_data_source(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """Owner can DELETE their own DS (sanity check — paired with the
    write-grantee DELETE 404 above)."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    ds_id = int(a_ds.id)
    r = client.delete(f"/data-sources/{ds_id}", headers=_auth_for(user_a))
    assert r.status_code == 204
    # Cleanup already happened via the DELETE; drop residual grants
    # from any earlier test runs that left orphans.
    db.query(DataSourceAccess).filter(
        DataSourceAccess.data_source_id == ds_id
    ).delete()
    db.commit()


def test_non_owner_without_grant_cannot_delete(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B without any grant gets 404 on DELETE — uniform with the read
    path (no leak)."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    try:
        r = client.delete(
            f"/data-sources/{a_ds.id}", headers=_auth_for(user_b)
        )
        assert r.status_code == 404
    finally:
        _cleanup_ds(db, int(a_ds.id))


# ----------------- grant endpoints -----------------


def test_grant_endpoint_owner_only(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B without write grant (only read access) cannot create a new
    grant — :func:`can_share` requires write. The 404 message is
    uniform regardless of the failure mode."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    other_username = _unique("pytest_acl_target")
    target = User(
        username=other_username, password_hash="x", role=ROLE_VIEWER
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    try:
        # A grants B write — gives B the ability to *test* can_share.
        client.post(
            f"/data-sources/{a_ds.id}/grants",
            json={"user_id": int(user_b.id), "permission": "write"},
            headers=_auth_for(user_a),
        )

        # A grants B read explicitly (downgrades) so can_share should
        # now reject B's attempt to share.
        client.post(
            f"/data-sources/{a_ds.id}/grants",
            json={"user_id": int(user_b.id), "permission": "read"},
            headers=_auth_for(user_a),
        )

        r = client.post(
            f"/data-sources/{a_ds.id}/grants",
            json={"user_id": int(target.id), "permission": "read"},
            headers=_auth_for(user_b),
        )
        assert r.status_code == 404
    finally:
        db.delete(target)
        _cleanup_ds(db, int(a_ds.id))


def test_grant_endpoint_rejects_nonexistent_target_user(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """user_id pointing at no row → 404 ``User not found``."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    try:
        r = client.post(
            f"/data-sources/{a_ds.id}/grants",
            json={"user_id": 999_999_999, "permission": "read"},
            headers=_auth_for(user_a),
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "User not found"
    finally:
        _cleanup_ds(db, int(a_ds.id))


def test_grant_upsert_same_pair_overwrites_permission(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """Second POST with the same (ds, user) updates permission rather
    than failing the unique constraint."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    try:
        # First grant: read.
        r1 = client.post(
            f"/data-sources/{a_ds.id}/grants",
            json={"user_id": int(user_b.id), "permission": "read"},
            headers=_auth_for(user_a),
        )
        assert r1.status_code == 201
        first_id = r1.json()["id"]

        # Second grant: write (same user) — should upsert.
        r2 = client.post(
            f"/data-sources/{a_ds.id}/grants",
            json={"user_id": int(user_b.id), "permission": "write"},
            headers=_auth_for(user_a),
        )
        assert r2.status_code == 201
        assert r2.json()["id"] == first_id
        assert r2.json()["permission"] == "write"
    finally:
        _cleanup_ds(db, int(a_ds.id))


def test_revoke_grant_endpoint_owner_or_admin_only(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B (no write grant, no owner) gets 404 on DELETE /grants/{id}."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    try:
        # A grants B read so a grant row exists.
        r_grant = client.post(
            f"/data-sources/{a_ds.id}/grants",
            json={"user_id": int(user_b.id), "permission": "read"},
            headers=_auth_for(user_a),
        )
        gid = r_grant.json()["id"]

        r_del = client.delete(
            f"/data-sources/grants/{gid}", headers=_auth_for(user_b)
        )
        assert r_del.status_code == 404
    finally:
        _cleanup_ds(db, int(a_ds.id))


def test_revoke_grant_owner_succeeds(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """Owner can revoke their own grant."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    try:
        r_grant = client.post(
            f"/data-sources/{a_ds.id}/grants",
            json={"user_id": int(user_b.id), "permission": "read"},
            headers=_auth_for(user_a),
        )
        gid = r_grant.json()["id"]

        r_del = client.delete(
            f"/data-sources/grants/{gid}", headers=_auth_for(user_a)
        )
        assert r_del.status_code == 204

        # B no longer has access.
        r_get = client.get(
            f"/data-sources/{a_ds.id}", headers=_auth_for(user_b)
        )
        assert r_get.status_code == 404
    finally:
        _cleanup_ds(db, int(a_ds.id))


# ----------------- explorer / jobs ACL cascade -----------------


def test_explorer_query_requires_ds_read_acl(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B without DS read grant cannot run SQL via /explorer/query."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    try:
        r = client.post(
            "/explorer/query",
            json={"data_source_id": int(a_ds.id), "sql": "SELECT 1"},
            headers=_auth_for(user_b),
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "Data source not found"
    finally:
        _cleanup_ds(db, int(a_ds.id))


def test_jobs_get_requires_ds_read_acl(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B cannot GET /jobs/{id} for a job tied to A's DS.

    Setup: A owns a DS, A has a report on it, a queued job exists for
    that report. B has no access — expect 404 on /jobs/{id}.
    """
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    rep = Report(
        name=_unique("pytest_acl_job_report"),
        data_source_id=int(a_ds.id),
        is_active=True,
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)

    from app.models.report_job import ReportJob

    job = ReportJob(
        report_id=int(rep.id),
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
        db.delete(rep)
        _cleanup_ds(db, int(a_ds.id))

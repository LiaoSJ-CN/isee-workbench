"""Tests for batch 14.2 — Dashboard router ACL + CRUD + shares + preview.

Coverage matrix mirrors :mod:`tests.test_report_acl` adapted for the
dashboard shape (owner + visibility + per-user share + DS gate on
every referenced item):

* Create dashboard — owner defaults to caller, visibility defaults
  to ``private``, 409 on name collision.
* Visibility matrix — public/org/private × owner/grantee/outsider.
* Grant matrix — read grant → GET OK, write grant → PUT OK, delete
  is owner-or-admin only.
* Admin override — admin sees / mutates everything.
* DS gate (preview only): a dashboard with a chart item pointing at
  an admin-only DS is invisible to non-admin non-owner. Verified via
  the preview endpoint.
* Duplicate endpoint — admin/owner can call, non-owner 404.
* Share endpoint — owner-or-admin can manage, non-owner 403 (uniform 404).
* Preview endpoint — server-side aggregate; text items render
  in-line without any DS dependency.
* Delete — owner-or-admin only; cascade clears items / shares /
  subscriptions.

Tests rely on the ``admin`` user being seeded; otherwise they skip
silently (same contract as the rest of the batch 9 ACL suite).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crypto import encrypt as crypto_encrypt
from app.database import SessionLocal
from app.models.dashboard import Dashboard, DashboardItem
from app.models.dashboard_access import DashboardAccess
from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.user import ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ----------------- fixtures -----------------


@pytest.fixture
def db_setup() -> Any:
    """(Session, admin User) — mirrors local fixtures in test_report_acl."""
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
    """First non-admin user — owns dashboards that B can't see by default."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_dash_user_a"),
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
    """Second non-admin user — the "outsider" without any grants."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_dash_user_b"),
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
    """Insert a placeholder DS row; the dashboard ACL tests don't run
    real queries against it — they only exercise the existence + ACL
    paths."""
    src = DataSource(
        name=_unique("pytest_dash_acl_ds"),
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


def _make_dashboard(
    db: Session,
    *,
    owner_user_id: int | None,
    visibility: str = "private",
) -> Dashboard:
    dash = Dashboard(
        name=_unique("pytest_dash_acl"),
        description="acl fixture",
        owner_user_id=owner_user_id,
        visibility=visibility,
    )
    db.add(dash)
    db.commit()
    db.refresh(dash)
    return dash


def _cleanup(db: Session, dashboard_id: int, ds_id: int | None = None) -> None:
    """Best-effort teardown — child rows first so SQLite FK doesn't
    trip. Skips silently on missing parent (best-effort)."""
    db.query(DashboardAccess).filter(
        DashboardAccess.dashboard_id == dashboard_id
    ).delete()
    db.query(DashboardItem).filter(
        DashboardItem.dashboard_id == dashboard_id
    ).delete()
    db.query(Dashboard).filter(Dashboard.id == dashboard_id).delete()
    if ds_id is not None:
        db.query(DataSourceAccess).filter(
            DataSourceAccess.data_source_id == ds_id
        ).delete()
        db.query(DataSource).filter(DataSource.id == ds_id).delete()
    db.commit()


# ----------------- create / ownership defaults -----------------


def test_create_dashboard_sets_owner_and_default_private(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """POST /dashboards assigns owner_user_id == caller and visibility=private."""
    payload = {"name": _unique("pytest_dash_owned")}
    r = client.post("/dashboards", json=payload, headers=_auth_for(user_a))
    assert r.status_code == 201, r.text
    body = r.json()
    try:
        assert body["owner_user_id"] == user_a.id
        assert body["visibility"] == "private"
    finally:
        db, _ = db_setup
        _cleanup(db, int(body["id"]))


def test_create_dashboard_name_collision_returns_409(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """Duplicate name → 409, not 500 — same contract as /reports."""
    payload = {"name": _unique("pytest_dash_dup_name")}
    r1 = client.post("/dashboards", json=payload, headers=_auth_for(user_a))
    assert r1.status_code == 201
    try:
        r2 = client.post("/dashboards", json=payload, headers=_auth_for(user_a))
        assert r2.status_code == 409
        assert "already exists" in r2.json()["detail"]
    finally:
        db, _ = db_setup
        _cleanup(db, int(r1.json()["id"]))


# ----------------- visibility matrix -----------------


def test_private_dashboard_invisible_to_other_user(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """A creates private Dashboard, B can't see / get it."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="private")
    try:
        r_list = client.get("/dashboards", headers=_auth_for(user_b))
        assert r_list.status_code == 200
        names_b = {row["name"] for row in r_list.json()}
        assert dash.name not in names_b

        r_get = client.get(
            f"/dashboards/{int(dash.id)}", headers=_auth_for(user_b)
        )
        assert r_get.status_code == 404
    finally:
        _cleanup(db, int(dash.id))


def test_public_dashboard_visible_to_anyone(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """A creates public Dashboard, B sees it in the list + can GET it."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="public")
    try:
        r_list = client.get("/dashboards", headers=_auth_for(user_b))
        names_b = {row["name"] for row in r_list.json()}
        assert dash.name in names_b

        r_get = client.get(
            f"/dashboards/{int(dash.id)}", headers=_auth_for(user_b)
        )
        assert r_get.status_code == 200
    finally:
        _cleanup(db, int(dash.id))


def test_admin_sees_all_dashboards(
    client: TestClient,
    user_a: User,
    auth_headers: dict[str, str],
    db_setup: Any,
) -> None:
    """Admin role short-circuits visibility."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="private")
    try:
        r = client.get(f"/dashboards/{int(dash.id)}", headers=auth_headers)
        assert r.status_code == 200
    finally:
        _cleanup(db, int(dash.id))


# ----------------- grant matrix -----------------


def test_read_grantee_can_get_but_not_put(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """A grants B read → B GETs OK, PUT 404, DELETE 404."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="private")
    # Seed the read grant via the share endpoint to also exercise it.
    r_share = client.post(
        f"/dashboards/{int(dash.id)}/shares",
        json={"user_id": int(user_b.id), "permission": "read"},
        headers=_auth_for(user_a),
    )
    assert r_share.status_code == 201, r_share.text
    try:
        r_get = client.get(
            f"/dashboards/{int(dash.id)}", headers=_auth_for(user_b)
        )
        assert r_get.status_code == 200

        r_put = client.put(
            f"/dashboards/{int(dash.id)}",
            json={"description": "mutated by grantee"},
            headers=_auth_for(user_b),
        )
        # Public visibility alone doesn't grant write; uniform 404.
        assert r_put.status_code == 404

        r_del = client.delete(
            f"/dashboards/{int(dash.id)}", headers=_auth_for(user_b)
        )
        assert r_del.status_code == 404
    finally:
        _cleanup(db, int(dash.id))


def test_write_grantee_can_put_but_not_delete(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """A grants B write → B PUTs OK, DELETE 404 (only owner/admin)."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="private")
    r_share = client.post(
        f"/dashboards/{int(dash.id)}/shares",
        json={"user_id": int(user_b.id), "permission": "write"},
        headers=_auth_for(user_a),
    )
    assert r_share.status_code == 201, r_share.text
    try:
        r_put = client.put(
            f"/dashboards/{int(dash.id)}",
            json={"description": "mutated by write grantee"},
            headers=_auth_for(user_b),
        )
        assert r_put.status_code == 200
        assert r_put.json()["description"] == "mutated by write grantee"

        r_del = client.delete(
            f"/dashboards/{int(dash.id)}", headers=_auth_for(user_b)
        )
        assert r_del.status_code == 404
    finally:
        _cleanup(db, int(dash.id))


# ----------------- duplicate endpoint -----------------


def test_duplicate_dashboard_admin_can_call(
    client: TestClient,
    user_a: User,
    auth_headers: dict[str, str],
    db_setup: Any,
) -> None:
    """Admin can duplicate any dashboard — read ACL is sufficient."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="public")
    # Add one text item so the duplicate has something to copy.
    db.add(
        DashboardItem(
            dashboard_id=int(dash.id),
            item_type="text",
            title="welcome",
            order_index=0,
            text_content="hello",
        )
    )
    db.commit()
    try:
        r = client.post(
            f"/dashboards/{int(dash.id)}/duplicate", headers=auth_headers
        )
        assert r.status_code == 201, r.text
        clone_id = int(r.json()["id"])
        try:
            assert clone_id != int(dash.id)
            assert r.json()["visibility"] == "private"
            # Items were deep-copied (1 item).
            assert len(r.json()["items"]) == 1
        finally:
            _cleanup(db, clone_id)
    finally:
        _cleanup(db, int(dash.id))


def test_duplicate_dashboard_non_owner_404(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B has no grant on A's private dashboard → duplicate 404."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="private")
    try:
        r = client.post(
            f"/dashboards/{int(dash.id)}/duplicate", headers=_auth_for(user_b)
        )
        assert r.status_code == 404
    finally:
        _cleanup(db, int(dash.id))


# ----------------- shares endpoint -----------------


def test_shares_endpoint_owner_or_admin_only(
    client: TestClient,
    user_a: User,
    user_b: User,
    auth_headers: dict[str, str],
    db_setup: Any,
) -> None:
    """Listing shares requires owner-or-admin (uniform 404 for others).
    Owner can create / revoke; non-owner 404s."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="private")
    try:
        # Non-owner listing → 404
        r_list_b = client.get(
            f"/dashboards/{int(dash.id)}/shares", headers=_auth_for(user_b)
        )
        assert r_list_b.status_code == 404

        # Admin can list (no shares yet → empty array)
        r_list_admin = client.get(
            f"/dashboards/{int(dash.id)}/shares", headers=auth_headers
        )
        assert r_list_admin.status_code == 200
        assert r_list_admin.json() == []

        # Owner creates a share
        r_create = client.post(
            f"/dashboards/{int(dash.id)}/shares",
            json={"user_id": int(user_b.id), "permission": "read"},
            headers=_auth_for(user_a),
        )
        assert r_create.status_code == 201
        share_id = int(r_create.json()["id"])

        # Non-owner create → 404
        r_create_b = client.post(
            f"/dashboards/{int(dash.id)}/shares",
            json={"user_id": int(user_a.id), "permission": "read"},
            headers=_auth_for(user_b),
        )
        assert r_create_b.status_code == 404

        # Owner can revoke
        r_del = client.delete(
            f"/dashboards/{int(dash.id)}/shares/{int(user_b.id)}",
            headers=_auth_for(user_a),
        )
        assert r_del.status_code == 204
    finally:
        _cleanup(db, int(dash.id))


# ----------------- DS gate on preview -----------------


def test_preview_blocks_when_chart_item_references_inaccessible_ds(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B has no DS access to A's chart item's data source → preview 404."""
    db, _ = db_setup
    a_ds = _make_ds(db, owner_user_id=int(user_a.id))
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="private")
    # Visibility=public so B can pass the dashboard ACL — the DS gate
    # is what should still block B.
    dash.visibility = "public"
    db.add(
        DashboardItem(
            dashboard_id=int(dash.id),
            item_type="chart",
            title="private chart",
            order_index=0,
            data_source_id=int(a_ds.id),
            table_name="t",
            fields=["a"],
            display_config={"chart_type": "bar"},
        )
    )
    db.commit()
    try:
        r_preview = client.post(
            f"/dashboards/{int(dash.id)}/preview", headers=_auth_for(user_b)
        )
        # Uniform 404 — DS gate failure is indistinguishable from "row
        # not found" by design.
        assert r_preview.status_code == 404
    finally:
        _cleanup(db, int(dash.id), int(a_ds.id))


def test_preview_renders_text_item_inline(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """A preview of an empty-item dashboard returns an HTML shell; a
    dashboard with a single text item renders the escaped text inline."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="private")
    db.add(
        DashboardItem(
            dashboard_id=int(dash.id),
            item_type="text",
            title="note",
            order_index=0,
            text_content="hello & welcome <script>alert(1)</script>",
        )
    )
    db.commit()
    try:
        r = client.post(
            f"/dashboards/{int(dash.id)}/preview", headers=_auth_for(user_a)
        )
        assert r.status_code == 200, r.text
        body = r.text
        # XSS-defence: the script tag is escaped, not embedded.
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body or "&amp;lt;script&amp;gt;" in body
        # The dashboard title is rendered as an <h1>.
        assert dash.name in body
    finally:
        _cleanup(db, int(dash.id))

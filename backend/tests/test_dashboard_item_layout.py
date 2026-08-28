"""Tests for batch 14.2 — Dashboard item CRUD + layout batch PATCH.

Coverage:

* POST /dashboards/{id}/items — write ACL, item persists with the
  expected fields.
* PUT /dashboards/{id}/items/{item_id} — write ACL, partial update.
* DELETE /dashboards/{id}/items/{item_id} — write ACL, cascade.
* PATCH /dashboards/{id}/items/layout — batch x/y/w/h + optional
  order_index, write ACL, 422 when an item_id belongs to a different
  dashboard.
* Non-owner without write grant → 404 across every mutation.

The suite mirrors :mod:`tests.test_dashboard_acl` for the
non-admin fixture pattern.
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
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_dash_item_user_a"),
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
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_dash_item_user_b"),
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


def _make_dashboard(
    db: Session,
    *,
    owner_user_id: int | None,
    visibility: str = "private",
) -> Dashboard:
    dash = Dashboard(
        name=_unique("pytest_dash_item"),
        description="item fixture",
        owner_user_id=owner_user_id,
        visibility=visibility,
    )
    db.add(dash)
    db.commit()
    db.refresh(dash)
    return dash


def _make_ds(db: Session, owner_user_id: int) -> DataSource:
    src = DataSource(
        name=_unique("pytest_dash_item_ds"),
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


def _cleanup(db: Session, dashboard_id: int, ds_id: int | None = None) -> None:
    db.query(DashboardAccess).filter(
        DashboardAccess.dashboard_id == dashboard_id
    ).delete()
    db.query(DashboardItem).filter(
        DashboardItem.dashboard_id == dashboard_id
    ).delete()
    db.query(Dashboard).filter(Dashboard.id == dashboard_id).delete()
    if ds_id is not None:
        db.query(DataSource).filter(DataSource.id == ds_id).delete()
    db.commit()


# ----------------- create item -----------------


def test_create_dashboard_item_text(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """POST creates a text item and returns 201 with the persisted row."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id))
    try:
        r = client.post(
            f"/dashboards/{int(dash.id)}/items",
            json={
                "item_type": "text",
                "title": "header",
                "text_content": "Hello world",
                "order_index": 0,
            },
            headers=_auth_for(user_a),
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["item_type"] == "text"
        assert body["text_content"] == "Hello world"
        assert body["dashboard_id"] == int(dash.id)
    finally:
        _cleanup(db, int(dash.id))


def test_create_dashboard_item_non_owner_404(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B has no grant → POST 404 (uniform with the read path)."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="private")
    try:
        r = client.post(
            f"/dashboards/{int(dash.id)}/items",
            json={"item_type": "text", "title": "x"},
            headers=_auth_for(user_b),
        )
        assert r.status_code == 404
    finally:
        _cleanup(db, int(dash.id))


# ----------------- update + delete -----------------


def test_update_and_delete_dashboard_item(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """PUT updates in place; DELETE removes the item (parent dashboard
    stays)."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id))
    db.add(
        DashboardItem(
            dashboard_id=int(dash.id),
            item_type="text",
            title="original",
            order_index=0,
            text_content="orig",
        )
    )
    db.commit()
    db.refresh(dash)
    item_id = int(dash.items[0].id)
    try:
        r_put = client.put(
            f"/dashboards/{int(dash.id)}/items/{item_id}",
            json={"title": "renamed", "text_content": "fresh"},
            headers=_auth_for(user_a),
        )
        assert r_put.status_code == 200
        assert r_put.json()["title"] == "renamed"
        assert r_put.json()["text_content"] == "fresh"

        r_del = client.delete(
            f"/dashboards/{int(dash.id)}/items/{item_id}",
            headers=_auth_for(user_a),
        )
        assert r_del.status_code == 204

        # Verify it's gone.
        r_get = client.get(
            f"/dashboards/{int(dash.id)}", headers=_auth_for(user_a)
        )
        assert r_get.status_code == 200
        assert all(
            int(it["id"]) != item_id for it in r_get.json()["items"]
        )
    finally:
        _cleanup(db, int(dash.id))


def test_update_item_wrong_dashboard_404(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """An item belonging to a different dashboard cannot be PUT via
    this dashboard's path — matches the FK scoping in the query."""
    db, _ = db_setup
    dash_a = _make_dashboard(db, owner_user_id=int(user_a.id))
    dash_b = _make_dashboard(db, owner_user_id=int(user_a.id))
    db.add(
        DashboardItem(
            dashboard_id=int(dash_a.id),
            item_type="text",
            title="on dash A",
            order_index=0,
        )
    )
    db.commit()
    db.refresh(dash_a)
    item_id = int(dash_a.items[0].id)
    try:
        r = client.put(
            f"/dashboards/{int(dash_b.id)}/items/{item_id}",
            json={"title": "should not work"},
            headers=_auth_for(user_a),
        )
        assert r.status_code == 404
    finally:
        _cleanup(db, int(dash_a.id))
        _cleanup(db, int(dash_b.id))


# ----------------- layout batch PATCH -----------------


def test_batch_layout_patch_updates_all_items(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """One PATCH sets x/y/w/h for the whole grid; the order_index is
    optional and falls back to no-op when omitted."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id))
    for i in range(3):
        db.add(
            DashboardItem(
                dashboard_id=int(dash.id),
                item_type="text",
                title=f"item-{i}",
                order_index=i,
                x=0,
                y=i,
                w=4,
                h=2,
            )
        )
    db.commit()
    db.refresh(dash)
    layout = [
        {
            "item_id": int(it.id),
            "x": (i * 4) % 12,
            "y": i,
            "w": 4,
            "h": 2,
            "order_index": i,
        }
        for i, it in enumerate(sorted(dash.items, key=lambda x: int(x.id)))
    ]
    try:
        r = client.patch(
            f"/dashboards/{int(dash.id)}/items/layout",
            json={"items": layout},
            headers=_auth_for(user_a),
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"updated": len(layout)}

        # Verify positions in the GET response.
        r_get = client.get(
            f"/dashboards/{int(dash.id)}", headers=_auth_for(user_a)
        )
        positions = {
            int(it["id"]): (
                int(it["x"]),
                int(it["y"]),
                int(it["w"]),
                int(it["h"]),
            )
            for it in r_get.json()["items"]
        }
        for i, entry in enumerate(layout):
            assert positions[entry["item_id"]] == (
                entry["x"],
                entry["y"],
                entry["w"],
                entry["h"],
            )
    finally:
        _cleanup(db, int(dash.id))


def test_batch_layout_patch_rejects_foreign_item(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """An item_id that belongs to a different dashboard triggers 422
    so the optimistic UI update can roll back."""
    db, _ = db_setup
    dash_a = _make_dashboard(db, owner_user_id=int(user_a.id))
    dash_b = _make_dashboard(db, owner_user_id=int(user_a.id))
    db.add(
        DashboardItem(
            dashboard_id=int(dash_a.id),
            item_type="text",
            title="on dash A",
            order_index=0,
        )
    )
    db.add(
        DashboardItem(
            dashboard_id=int(dash_b.id),
            item_type="text",
            title="on dash B",
            order_index=0,
        )
    )
    db.commit()
    db.refresh(dash_a)
    db.refresh(dash_b)
    try:
        # Try to PATCH dash B's layout with one of dash A's item_ids
        # mixed in.
        layout = [
            {"item_id": int(dash_b.items[0].id), "x": 0, "y": 0, "w": 4, "h": 2},
            {"item_id": int(dash_a.items[0].id), "x": 4, "y": 0, "w": 4, "h": 2},
        ]
        r = client.patch(
            f"/dashboards/{int(dash_b.id)}/items/layout",
            json={"items": layout},
            headers=_auth_for(user_a),
        )
        assert r.status_code == 422
        assert "must belong to this dashboard" in r.json()["detail"]
    finally:
        _cleanup(db, int(dash_a.id))
        _cleanup(db, int(dash_b.id))


def test_batch_layout_patch_non_owner_404(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """B has no write grant → PATCH 404."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="private")
    db.add(
        DashboardItem(
            dashboard_id=int(dash.id),
            item_type="text",
            title="only",
            order_index=0,
        )
    )
    db.commit()
    db.refresh(dash)
    try:
        layout = [
            {"item_id": int(it.id), "x": 0, "y": 0, "w": 4, "h": 2}
            for it in dash.items
        ]
        r = client.patch(
            f"/dashboards/{int(dash.id)}/items/layout",
            json={"items": layout},
            headers=_auth_for(user_b),
        )
        assert r.status_code == 404
    finally:
        _cleanup(db, int(dash.id))

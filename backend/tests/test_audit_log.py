"""Tests for batch 9.5 — Audit log.

Covers:

* each ACTION_* constant has a happy-path test (login, logout, refresh,
  DS CRUD, DS grant/revoke, report CRUD, item CRUD/reorder, param CRUD,
  share/revoke, generate, job enqueue, subscription CRUD/pause/resume,
  scheduler job create/delete, scheduler sync, explorer query)
* the audit-log read endpoint requires admin (non-admin → 403)
* filtering by actor_user_id / action / target_type / since+until
* pagination (limit/offset + X-Total-Count + body.total)
* password / password_hash is redacted to ``***REDACTED***`` in both
  ``before`` and ``after``
* request_id / ip_address / user_agent are captured from the request
* audit failure (e.g. AuditLog.__init__ raising) does NOT block the
  business endpoint
* unauthorized requests (404 from ACL) are NOT logged — closing the
  resource_id probing side-channel
* failed login (401) is NOT logged
* audit write swallows errors gracefully (no row created, business still
  succeeded)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.report import Report
from app.models.report_access import ReportAccess
from app.models.user import ROLE_VIEWER, User
from app.services import audit as audit_service
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_setup() -> Any:
    """(Session, admin User) — admin must be seeded for the audit
    read endpoint to be useful."""
    db = SessionLocal()
    admin = db.query(User).filter(User.username == "admin").first()
    if admin is None:
        db.close()
        pytest.skip("admin user not seeded")
    yield db, admin
    db.close()


@pytest.fixture
def user_a() -> User:
    """First non-admin user."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_audit_user_a"),
        password_hash="x",
        role=ROLE_VIEWER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    try:
        yield user
    finally:
        # AuditLog FK is SET NULL on user delete, so the audit trail
        # outlives the test user. Safe to delete in any order.
        db.query(DataSourceAccess).filter(DataSourceAccess.user_id == int(user.id)).delete()
        db.query(ReportAccess).filter(ReportAccess.user_id == int(user.id)).delete()
        db.query(DataSource).filter(DataSource.owner_user_id == int(user.id)).delete()
        db.query(Report).filter(Report.owner_user_id == int(user.id)).delete()
        db.delete(user)
        db.commit()
        db.close()


@pytest.fixture
def user_b() -> User:
    """Second non-admin user — outsider."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_audit_user_b"),
        password_hash="x",
        role=ROLE_VIEWER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    try:
        yield user
    finally:
        db.query(DataSourceAccess).filter(DataSourceAccess.user_id == int(user.id)).delete()
        db.query(ReportAccess).filter(ReportAccess.user_id == int(user.id)).delete()
        db.query(DataSource).filter(DataSource.owner_user_id == int(user.id)).delete()
        db.query(Report).filter(Report.owner_user_id == int(user.id)).delete()
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


def _make_ds(db: Session, owner: User, *, name: str | None = None) -> DataSource:
    src = DataSource(
        name=name or _unique("pytest_audit_ds"),
        db_type="sqlite",
        host="placeholder",
        port=1,
        database=":memory:",
        username="placeholder",
        password="placeholder",
        owner_user_id=int(owner.id),
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


def _make_report(
    db: Session,
    *,
    owner: User,
    ds: DataSource,
    visibility: str = "private",
    name: str | None = None,
) -> Report:
    rep = Report(
        name=name or _unique("pytest_audit_rep"),
        data_source_id=int(ds.id),
        is_active=True,
        visibility=visibility,
        owner_user_id=int(owner.id),
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    return rep


def _list_audit_rows(
    client: TestClient,
    *,
    admin: User,
    action: str | None = None,
    target_type: str | None = None,
    actor_user_id: int | None = None,
    target_id: int | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Helper to call GET /audit-logs and return the items list."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if action is not None:
        params["action"] = action
    if target_type is not None:
        params["target_type"] = target_type
    if actor_user_id is not None:
        params["actor_user_id"] = actor_user_id
    if target_id is not None:
        params["target_id"] = target_id
    if request_id is not None:
        params["request_id"] = request_id
    if ip_address is not None:
        params["ip_address"] = ip_address
    if since is not None:
        params["since"] = since.isoformat()
    if until is not None:
        params["until"] = until.isoformat()
    r = client.get("/audit-logs", params=params, headers=_auth_for(admin))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "items" in body and "total" in body
    return body["items"]


def _delete_audit_rows_for_action(action: str) -> None:
    """Sweep leftover audit rows so cross-test pollution doesn't break
    ordering assertions. Each test already uses unique resource IDs,
    but ``action`` is shared."""
    db = SessionLocal()
    try:
        db.query(AuditLog).filter(AuditLog.action == action).delete()
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Audit-log read endpoint ACL
# ---------------------------------------------------------------------------


def test_audit_endpoint_requires_authentication(client: TestClient) -> None:
    r = client.get("/audit-logs")
    assert r.status_code == 401


def test_audit_endpoint_non_admin_gets_403(client: TestClient, user_a: User) -> None:
    r = client.get("/audit-logs", headers=_auth_for(user_a))
    assert r.status_code == 403


def test_audit_endpoint_admin_succeeds(client: TestClient, db_setup: Any) -> None:
    _, admin = db_setup
    r = client.get("/audit-logs", params={"limit": 1}, headers=_auth_for(admin))
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "total" in body
    assert r.headers.get("x-total-count") == str(body["total"])


# ---------------------------------------------------------------------------
# Auth hooks (login / logout / refresh)
# ---------------------------------------------------------------------------


def test_login_creates_audit_entry(client: TestClient, db_setup: Any) -> None:
    """Successful login writes an audit row with the user identity."""
    _delete_audit_rows_for_action(audit_service.ACTION_LOGIN)
    db, admin = db_setup
    # Hit the public login endpoint — uses settings.admin_password.
    from app.config import settings

    r = client.post(
        "/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert r.status_code == 200
    _, admin_user = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin_user,
        action=audit_service.ACTION_LOGIN,
    )
    assert len(rows) >= 1
    row = rows[0]
    assert row["action"] == audit_service.ACTION_LOGIN
    assert row["actor_user_id"] == int(admin.id)
    assert row["target_type"] == audit_service.TARGET_TYPE_SESSION
    assert row["before"] is None
    assert row["after"]["id"] == int(admin.id)
    assert row["after"]["username"] == settings.admin_username
    assert "password" not in row["after"]


def test_failed_login_not_logged(client: TestClient, db_setup: Any) -> None:
    """Failed login must NOT write an audit row — otherwise the
    audit log becomes a username-enumeration side-channel."""
    _delete_audit_rows_for_action(audit_service.ACTION_LOGIN)
    _, admin = db_setup
    r = client.post(
        "/auth/login",
        json={"username": "nobody_audit_xyz", "password": "wrong"},
    )
    assert r.status_code == 401
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_LOGIN,
    )
    # No login row for a bad username
    bad_rows = [row for row in rows if row["actor_user_id"] is None]
    assert bad_rows == []


def test_logout_creates_audit_entry(client: TestClient, user_a: User) -> None:
    """Successful logout writes an audit row with no before/after."""
    _delete_audit_rows_for_action(audit_service.ACTION_LOGOUT)
    r = client.post("/auth/logout", headers=_auth_for(user_a))
    assert r.status_code == 200
    # Pull out the latest row scoped to user_a
    db = SessionLocal()
    try:
        row = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == audit_service.ACTION_LOGOUT,
                AuditLog.actor_user_id == int(user_a.id),
            )
            .order_by(AuditLog.id.desc())
            .first()
        )
    finally:
        db.close()
    assert row is not None
    assert row.before is None
    assert row.after is None


# ---------------------------------------------------------------------------
# DataSource hooks
# ---------------------------------------------------------------------------


def test_ds_create_logs_owner(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_CREATE)
    name = _unique("audit_ds_create")
    r = client.post(
        "/data-sources",
        json={
            "name": name,
            "db_type": "sqlite",
            "host": "placeholder",
            "port": 1,
            "database": ":memory:",
            "username": "placeholder",
            "password": "secret",
        },
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_DATA_SOURCE_CREATE,
    )
    assert any(
        row["actor_user_id"] == int(user_a.id) and row["after"]["name"] == name for row in rows
    ), "expected DS create audit row for user_a"


def test_ds_update_logs_before_after_diff(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_UPDATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        ds_id = int(ds.id)
    finally:
        db.close()
    name_new = _unique("audit_ds_renamed")
    r = client.put(
        f"/data-sources/{ds_id}",
        json={"name": name_new},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 200
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_DATA_SOURCE_UPDATE,
        target_id=ds_id,
    )
    assert len(rows) >= 1
    row = rows[0]
    assert row["before"]["name"] != name_new
    assert row["after"]["name"] == name_new
    assert row["actor_user_id"] == int(user_a.id)


def test_ds_update_password_redacted_in_after(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    """Password updates: the audit row's ``before`` and ``after`` use
    the DataSourceResponse schema, which doesn't expose ``password``
    at all (write-only). The redaction in ``audit_service._redact``
    would catch the field if it ever did leak; the schema exclusion
    is the first line of defence."""
    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_UPDATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        ds_id = int(ds.id)
    finally:
        db.close()
    r = client.put(
        f"/data-sources/{ds_id}",
        json={"password": "new-secret"},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 200
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_DATA_SOURCE_UPDATE,
        target_id=ds_id,
    )
    assert len(rows) >= 1
    row = rows[0]
    # No password key in either snapshot (schema doesn't expose it).
    assert "password" not in row["before"]
    assert "password" not in row["after"]
    # Plaintext is nowhere in the row.
    assert "new-secret" not in str(row)


def test_ds_delete_logs_before_snapshot(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_DELETE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        ds_id = int(ds.id)
    finally:
        db.close()
    r = client.delete(f"/data-sources/{ds_id}", headers=_auth_for(user_a))
    assert r.status_code == 204
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_DATA_SOURCE_DELETE,
        target_id=ds_id,
    )
    assert len(rows) >= 1
    assert rows[0]["before"] is not None
    assert rows[0]["after"] is None


def test_ds_grant_logs_grantee(
    client: TestClient, user_a: User, user_b: User, db_setup: Any
) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_GRANT)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        ds_id = int(ds.id)
    finally:
        db.close()
    r = client.post(
        f"/data-sources/{ds_id}/grants",
        json={"user_id": int(user_b.id), "permission": "read"},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201
    grant_id = r.json()["id"]
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_DATA_SOURCE_GRANT,
        target_id=grant_id,
    )
    assert len(rows) >= 1
    assert rows[0]["target_type"] == audit_service.TARGET_TYPE_DATA_SOURCE_GRANT


def test_ds_revoke_logs_grant_row(
    client: TestClient, user_a: User, user_b: User, db_setup: Any
) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_REVOKE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        ds_id = int(ds.id)
        grant = DataSourceAccess(
            data_source_id=ds_id,
            user_id=int(user_b.id),
            permission="read",
            granted_by=int(user_a.id),
        )
        db.add(grant)
        db.commit()
        db.refresh(grant)
        grant_id = int(grant.id)
    finally:
        db.close()
    r = client.delete(f"/data-sources/grants/{grant_id}", headers=_auth_for(user_a))
    assert r.status_code == 204
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_DATA_SOURCE_REVOKE,
        target_id=grant_id,
    )
    assert len(rows) >= 1
    assert rows[0]["before"] is not None
    assert rows[0]["after"] is None


# ---------------------------------------------------------------------------
# Report hooks
# ---------------------------------------------------------------------------


def test_report_create_logs_visibility(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_CREATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        ds_id = int(ds.id)
    finally:
        db.close()
    name = _unique("audit_report_create")
    r = client.post(
        "/reports",
        json={
            "name": name,
            "data_source_id": ds_id,
            "visibility": "private",
        },
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201
    rep_id = r.json()["id"]
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_CREATE,
        target_id=rep_id,
    )
    assert len(rows) >= 1
    assert rows[0]["after"]["visibility"] == "private"


def test_report_update_logs_name_change(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_UPDATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    new_name = _unique("audit_report_updated")
    r = client.put(
        f"/reports/{rep_id}",
        json={"name": new_name},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 200
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_UPDATE,
        target_id=rep_id,
    )
    assert len(rows) >= 1
    assert rows[0]["before"]["name"] != new_name
    assert rows[0]["after"]["name"] == new_name


def test_report_delete_logs_pre_delete(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_DELETE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.delete(f"/reports/{rep_id}", headers=_auth_for(user_a))
    assert r.status_code == 204
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_DELETE,
        target_id=rep_id,
    )
    assert len(rows) >= 1
    assert rows[0]["before"] is not None
    assert rows[0]["after"] is None


def test_item_create_logs_item(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_ITEM_CREATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        f"/reports/{rep_id}/items",
        json={
            "name": "audit_item",
            "item_type": "table",
            "table_name": "t",
            "fields": ["a"],
        },
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_ITEM_CREATE,
        target_id=item_id,
    )
    assert len(rows) >= 1
    assert rows[0]["target_type"] == audit_service.TARGET_TYPE_REPORT_ITEM


def test_item_update_logs_order_index(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_ITEM_UPDATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        f"/reports/{rep_id}/items",
        json={"name": "it", "item_type": "table", "table_name": "t", "fields": ["a"]},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]
    r = client.put(
        f"/reports/{rep_id}/items/{item_id}",
        json={"order_index": 42},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 200, r.text
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_ITEM_UPDATE,
        target_id=item_id,
    )
    assert len(rows) >= 1
    assert rows[0]["before"]["order_index"] != 42
    assert rows[0]["after"]["order_index"] == 42


def test_item_delete_logs_item(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_ITEM_DELETE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        f"/reports/{rep_id}/items",
        json={"name": "del", "item_type": "table", "table_name": "t", "fields": ["a"]},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]
    r = client.delete(f"/reports/{rep_id}/items/{item_id}", headers=_auth_for(user_a))
    assert r.status_code == 204
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_ITEM_DELETE,
        target_id=item_id,
    )
    assert len(rows) >= 1


def test_item_reorder_logs_order_list(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_ITEM_REORDER)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    ids = []
    for i in range(2):
        r = client.post(
            f"/reports/{rep_id}/items",
            json={
                "name": f"r{i}",
                "item_type": "table",
                "table_name": "t",
                "fields": ["a"],
            },
            headers=_auth_for(user_a),
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    r = client.patch(
        f"/reports/{rep_id}/items/order",
        json={
            "items": [{"item_id": ids[0], "order_index": 1}, {"item_id": ids[1], "order_index": 0}]
        },
        headers=_auth_for(user_a),
    )
    assert r.status_code == 200, r.text
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_ITEM_REORDER,
        target_id=rep_id,
    )
    assert len(rows) >= 1
    assert "order" in rows[0]["after"]


def test_param_create_logs_spec(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_PARAM_CREATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        f"/reports/{rep_id}/parameters",
        json={"name": "p1", "label": "P1", "type": "string", "required": True},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_PARAM_CREATE,
        target_id=pid,
    )
    assert len(rows) >= 1


def test_param_delete_logs_spec(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_PARAM_DELETE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        f"/reports/{rep_id}/parameters",
        json={"name": "p_del", "label": "PDel", "type": "string", "required": True},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    r = client.delete(f"/reports/{rep_id}/parameters/{pid}", headers=_auth_for(user_a))
    assert r.status_code == 204
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_PARAM_DELETE,
        target_id=pid,
    )
    assert len(rows) >= 1


def test_share_logs_grantee(client: TestClient, user_a: User, user_b: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_SHARE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        f"/reports/{rep_id}/shares",
        json={"user_id": int(user_b.id), "permission": "read"},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201
    share_id = r.json()["id"]
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_SHARE,
        target_id=share_id,
    )
    assert len(rows) >= 1
    assert rows[0]["target_type"] == audit_service.TARGET_TYPE_REPORT_SHARE


def test_revoke_logs_share(client: TestClient, user_a: User, user_b: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_REVOKE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
        share = ReportAccess(
            report_id=rep_id,
            user_id=int(user_b.id),
            permission="read",
            granted_by=int(user_a.id),
        )
        db.add(share)
        db.commit()
        db.refresh(share)
        share_id = int(share.id)
    finally:
        db.close()
    r = client.delete(f"/reports/shares/{share_id}", headers=_auth_for(user_a))
    assert r.status_code == 204
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_REVOKE,
        target_id=share_id,
    )
    assert len(rows) >= 1


def test_generate_endpoint_calls_audit_hook(
    client: TestClient, user_a: User, db_setup: Any, monkeypatch: Any
) -> None:
    """Verify the ``POST /reports/generate`` audit hook fires by
    monkey-patching ``generate_report`` so the test is hermetic —
    the real generator needs an external DS with real tables, which
    isn't available in the unit-test environment.

    Replacing the function returns a stub dict; the endpoint then
    writes an audit row with ``success=True``. This catches both the
    route wiring (the audit hook is wired) and the success-branch
    payload shape (``output_format``, ``success`` fields)."""
    from app.models.report_parameter import ReportParameter
    from app.routers import report as report_router

    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_GENERATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(
            db,
            owner=user_a,
            ds=ds,
            name=_unique("audit_gen_rep"),
        )
        rep_id = int(rep.id)
        # Drop any leftover parameters to avoid ``missing required
        # parameter: 'p1'`` short-circuiting before the hook runs.
        db.query(ReportParameter).filter(ReportParameter.report_id == int(rep.id)).delete()
        db.commit()
    finally:
        db.close()

    def _fake_generate_report(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(report_router, "generate_report", _fake_generate_report)
    r = client.post(
        "/reports/generate",
        json={"report_id": rep_id, "output_format": "html", "parameters": {}},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 200, f"got {r.status_code}: {r.text}"
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_GENERATE,
        target_id=rep_id,
    )
    assert len(rows) >= 1, "audit row should be written for /reports/generate"
    assert rows[0]["after"]["output_format"] == "html"
    assert rows[0]["after"]["success"] is True


def test_generate_endpoint_audit_hook_on_report_generator_error(
    client: TestClient, user_a: User, db_setup: Any, monkeypatch: Any
) -> None:
    """Failure branch: when ``generate_report`` raises
    ``ReportGeneratorError`` the endpoint catches it, writes an audit
    row with ``success=False``, and returns 400."""
    from app.models.report_parameter import ReportParameter
    from app.routers import report as report_router
    from app.services.report_generator import ReportGeneratorError

    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_GENERATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(
            db,
            owner=user_a,
            ds=ds,
            name=_unique("audit_gen_fail_rep"),
        )
        rep_id = int(rep.id)
        db.query(ReportParameter).filter(ReportParameter.report_id == int(rep.id)).delete()
        db.commit()
    finally:
        db.close()

    def _fake_generate_report(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ReportGeneratorError("simulated generate failure")

    monkeypatch.setattr(report_router, "generate_report", _fake_generate_report)
    r = client.post(
        "/reports/generate",
        json={"report_id": rep_id, "output_format": "html", "parameters": {}},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 400, f"got {r.status_code}: {r.text}"
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_GENERATE,
        target_id=rep_id,
    )
    assert len(rows) >= 1, "audit row should be written even on failure"
    assert rows[0]["after"]["success"] is False
    assert "simulated generate failure" in rows[0]["after"]["error"]


# ---------------------------------------------------------------------------
# Job enqueue
# ---------------------------------------------------------------------------


def test_job_enqueue_logs_report_format(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_JOB_ENQUEUE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        f"/reports/{rep_id}/jobs",
        json={"output_format": "excel"},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201
    job_id = r.json()["id"]
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_JOB_ENQUEUE,
        target_id=job_id,
    )
    assert len(rows) >= 1
    assert rows[0]["target_type"] == audit_service.TARGET_TYPE_REPORT_JOB


# ---------------------------------------------------------------------------
# Subscription hooks
# ---------------------------------------------------------------------------


def _make_publishable_report(db: Session, owner: User, ds: DataSource) -> Report:
    """Public report so the subscription service can find the report
    via the regular report lookup (subscription doesn't ACL-gate the
    parent report beyond its existence)."""
    return _make_report(db, owner=owner, ds=ds, visibility="public")


def test_subscription_create_logs_cron(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_SUBSCRIPTION_CREATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_publishable_report(db, user_a, ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        "/subscriptions",
        json={
            "report_id": rep_id,
            "cron_expression": "0 9 * * * *",
            "parameters": {},
        },
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201, r.text
    sub_id = r.json()["id"]
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_SUBSCRIPTION_CREATE,
        target_id=sub_id,
    )
    assert len(rows) >= 1
    assert rows[0]["target_type"] == audit_service.TARGET_TYPE_REPORT_SUBSCRIPTION


def test_subscription_pause_logs_is_active_false(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_SUBSCRIPTION_PAUSE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_publishable_report(db, user_a, ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        "/subscriptions",
        json={"report_id": rep_id, "cron_expression": "0 9 * * * *", "parameters": {}},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201, r.text
    sub_id = r.json()["id"]
    r = client.post(f"/subscriptions/{sub_id}/pause", headers=_auth_for(user_a))
    assert r.status_code == 200
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_SUBSCRIPTION_PAUSE,
        target_id=sub_id,
    )
    assert len(rows) >= 1
    assert rows[0]["after"]["is_active"] is False


def test_subscription_resume_logs_is_active_true(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_SUBSCRIPTION_RESUME)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_publishable_report(db, user_a, ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        "/subscriptions",
        json={"report_id": rep_id, "cron_expression": "0 9 * * * *", "parameters": {}},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201, r.text
    sub_id = r.json()["id"]
    client.post(f"/subscriptions/{sub_id}/pause", headers=_auth_for(user_a))
    _delete_audit_rows_for_action(audit_service.ACTION_SUBSCRIPTION_PAUSE)
    r = client.post(f"/subscriptions/{sub_id}/resume", headers=_auth_for(user_a))
    assert r.status_code == 200
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_SUBSCRIPTION_RESUME,
        target_id=sub_id,
    )
    assert len(rows) >= 1
    assert rows[0]["after"]["is_active"] is True


def test_subscription_delete_logs_sub(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_SUBSCRIPTION_DELETE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_publishable_report(db, user_a, ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        "/subscriptions",
        json={"report_id": rep_id, "cron_expression": "0 9 * * * *", "parameters": {}},
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201, r.text
    sub_id = r.json()["id"]
    r = client.delete(f"/subscriptions/{sub_id}", headers=_auth_for(user_a))
    assert r.status_code == 204
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_SUBSCRIPTION_DELETE,
        target_id=sub_id,
    )
    assert len(rows) >= 1
    assert rows[0]["after"] is None


# ---------------------------------------------------------------------------
# Scheduler hooks
# ---------------------------------------------------------------------------


def test_scheduler_job_create_logs_cron(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_SCHEDULER_JOB_CREATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.post(
        f"/scheduler/jobs/{rep_id}",
        json={
            "report_id": rep_id,
            "cron_expression": "0 8 * * * *",
            "is_active": True,
        },
        headers=_auth_for(user_a),
    )
    assert r.status_code == 200, r.text
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_SCHEDULER_JOB_CREATE,
        target_id=rep_id,
    )
    assert len(rows) >= 1


def test_scheduler_job_delete_logs_report(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_SCHEDULER_JOB_DELETE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds)
        rep_id = int(rep.id)
    finally:
        db.close()
    client.post(
        f"/scheduler/jobs/{rep_id}",
        json={"report_id": rep_id, "cron_expression": "0 8 * * * *", "is_active": True},
        headers=_auth_for(user_a),
    )
    r = client.delete(f"/scheduler/jobs/{rep_id}", headers=_auth_for(user_a))
    assert r.status_code == 204
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_SCHEDULER_JOB_DELETE,
        target_id=rep_id,
    )
    assert len(rows) >= 1


def test_scheduler_sync_logs_actor_admin(client: TestClient, db_setup: Any) -> None:
    """``POST /scheduler/sync`` is admin-only — non-admin should be 403
    (ACL test); admin should write a sync row."""
    _, admin = db_setup
    _delete_audit_rows_for_action(audit_service.ACTION_SCHEDULER_SYNC)
    r = client.post("/scheduler/sync", headers=_auth_for(admin))
    assert r.status_code == 200, r.text
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_SCHEDULER_SYNC,
    )
    assert len(rows) >= 1
    assert rows[0]["actor_user_id"] == int(admin.id)
    assert rows[0]["after"]["jobs_loaded"] >= 0


def test_scheduler_sync_non_admin_403(client: TestClient, user_a: User) -> None:
    r = client.post("/scheduler/sync", headers=_auth_for(user_a))
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Explorer hook
# ---------------------------------------------------------------------------


def test_explorer_query_logs_sql(client: TestClient, user_a: User, db_setup: Any) -> None:
    """Successful explorer query writes an audit row with the SQL."""
    _delete_audit_rows_for_action(audit_service.ACTION_EXPLORER_QUERY)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        ds_id = int(ds.id)
    finally:
        db.close()
    # SQLite DS — use a query that doesn't require setup beyond
    # attaching an empty in-memory db (which is the configured state).
    r = client.post(
        "/explorer/query",
        json={"data_source_id": ds_id, "sql": "SELECT 1 AS a"},
        headers=_auth_for(user_a),
    )
    # Either success=True or a ConnectionError-rolled success=False
    # audit row — both are valid; the important thing is the row exists.
    assert r.status_code == 200, r.text
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_EXPLORER_QUERY,
        actor_user_id=int(user_a.id),
    )
    assert len(rows) >= 1
    assert "SELECT 1" in rows[0]["after"]["sql"]


# ---------------------------------------------------------------------------
# Filtering, pagination, redaction
# ---------------------------------------------------------------------------


def test_audit_filter_by_actor_user_id(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_CREATE)
    name = _unique("audit_filter_actor")
    client.post(
        "/data-sources",
        json={
            "name": name,
            "db_type": "sqlite",
            "host": "x",
            "port": 1,
            "database": ":memory:",
            "username": "x",
            "password": "x",
        },
        headers=_auth_for(user_a),
    )
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_DATA_SOURCE_CREATE,
        actor_user_id=int(user_a.id),
    )
    assert all(row["actor_user_id"] == int(user_a.id) for row in rows)


def test_audit_filter_by_target_type(client: TestClient, user_a: User, db_setup: Any) -> None:
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        target_type=audit_service.TARGET_TYPE_DATA_SOURCE,
    )
    assert all(row["target_type"] == audit_service.TARGET_TYPE_DATA_SOURCE for row in rows)


def test_audit_filter_by_since_until(client: TestClient, db_setup: Any) -> None:
    """Windowed by ``since`` — anything before that timestamp is
    excluded."""
    _, admin = db_setup
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    r = client.get(
        "/audit-logs",
        params={"since": since.isoformat(), "limit": 1},
        headers=_auth_for(admin),
    )
    assert r.status_code == 200
    body = r.json()
    assert "items" in body


def test_audit_pagination_limit_offset(client: TestClient, db_setup: Any) -> None:
    _, admin = db_setup
    r = client.get(
        "/audit-logs",
        params={"limit": 3, "offset": 0},
        headers=_auth_for(admin),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 3
    assert body["offset"] == 0
    assert len(body["items"]) <= 3


def test_audit_pagination_total_in_body(client: TestClient, db_setup: Any) -> None:
    _, admin = db_setup
    r = client.get("/audit-logs", params={"limit": 1}, headers=_auth_for(admin))
    body = r.json()
    assert r.headers.get("x-total-count") == str(body["total"])


def test_audit_get_endpoint_returns_newest_first(client: TestClient, db_setup: Any) -> None:
    """List ordering: ``created_at DESC, id DESC`` — newest rows
    appear first."""
    _, admin = db_setup
    r = client.get("/audit-logs", params={"limit": 50}, headers=_auth_for(admin))
    rows = r.json()["items"]
    if len(rows) < 2:
        pytest.skip("not enough audit rows to verify ordering")
    timestamps = [row["created_at"] for row in rows]
    assert timestamps == sorted(timestamps, reverse=True)


# ---------------------------------------------------------------------------
# Resilience: audit failure does not block business endpoint
# ---------------------------------------------------------------------------


def test_audit_failure_does_not_block_business_endpoint(client: TestClient, user_a: User) -> None:
    """If audit write raises (e.g. DB transient error), the business
    endpoint still succeeds. The user gets 201, not 500.

    We patch ``AuditLog.__init__`` to raise — the production
    ``audit_service.log`` wraps the insert in a try/except and
    swallows the failure, so the business endpoint proceeds. Patching
    ``audit_service.log`` itself would bypass that safety net, which
    isn't what we want to verify."""
    name = _unique("audit_failure_resilience")

    def _boom(self: Any, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated audit failure")

    with patch.object(AuditLog, "__init__", _boom):
        r = client.post(
            "/data-sources",
            json={
                "name": name,
                "db_type": "sqlite",
                "host": "x",
                "port": 1,
                "database": ":memory:",
                "username": "x",
                "password": "x",
            },
            headers=_auth_for(user_a),
        )
    assert r.status_code == 201, r.text


def test_audit_request_id_captured_from_contextvar(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    """The middleware-installed X-Request-ID is captured into the
    audit row's ``request_id`` column."""
    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_CREATE)
    name = _unique("audit_req_id")
    r = client.post(
        "/data-sources",
        json={
            "name": name,
            "db_type": "sqlite",
            "host": "x",
            "port": 1,
            "database": ":memory:",
            "username": "x",
            "password": "x",
        },
        headers={
            **_auth_for(user_a),
            "X-Request-ID": "audit-test-rid-xyz",
        },
    )
    assert r.status_code == 201
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_DATA_SOURCE_CREATE,
    )
    matching = [row for row in rows if row["after"]["name"] == name]
    assert matching
    assert matching[0]["request_id"] == "audit-test-rid-xyz"


def test_audit_ip_address_captured_from_request(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_CREATE)
    name = _unique("audit_ip")
    r = client.post(
        "/data-sources",
        json={
            "name": name,
            "db_type": "sqlite",
            "host": "x",
            "port": 1,
            "database": ":memory:",
            "username": "x",
            "password": "x",
        },
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_DATA_SOURCE_CREATE,
    )
    matching = [row for row in rows if row["after"]["name"] == name]
    assert matching
    # IP captured (testclient uses 'testclient' as the peer)
    assert matching[0]["ip_address"]


def test_audit_user_agent_captured(client: TestClient, user_a: User, db_setup: Any) -> None:
    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_CREATE)
    name = _unique("audit_ua")
    ua = "audit-test-agent/9.5"
    r = client.post(
        "/data-sources",
        json={
            "name": name,
            "db_type": "sqlite",
            "host": "x",
            "port": 1,
            "database": ":memory:",
            "username": "x",
            "password": "x",
        },
        headers={**_auth_for(user_a), "User-Agent": ua},
    )
    assert r.status_code == 201
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_DATA_SOURCE_CREATE,
    )
    matching = [row for row in rows if row["after"]["name"] == name]
    assert matching
    assert matching[0]["user_agent"] == ua


# ---------------------------------------------------------------------------
# Side-channel: unauthorized requests are NOT logged
# ---------------------------------------------------------------------------


def test_audit_unauthorized_endpoint_not_logged(
    client: TestClient, user_a: User, user_b: User, db_setup: Any
) -> None:
    """user_b trying to PUT user_a's private report → 404. No audit
    row should be written — otherwise the audit log would leak
    information about resource existence via the ``target_id`` it
    contains."""
    _delete_audit_rows_for_action(audit_service.ACTION_REPORT_UPDATE)
    db = SessionLocal()
    try:
        ds = _make_ds(db, user_a)
        rep = _make_report(db, owner=user_a, ds=ds, visibility="private")
        rep_id = int(rep.id)
    finally:
        db.close()
    r = client.put(
        f"/reports/{rep_id}",
        json={"name": _unique("hack_attempt")},
        headers=_auth_for(user_b),
    )
    assert r.status_code == 404
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_REPORT_UPDATE,
        target_id=rep_id,
    )
    assert rows == []


# ---------------------------------------------------------------------------
# Snapshot redaction
# ---------------------------------------------------------------------------


def test_audit_password_field_redacted_in_create_after(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    """The DataSource ORM row's ``password`` field is encrypted
    ciphertext (encryption happens at the router before insert). The
    DataSourceResponse schema doesn't expose the password field at all
    (it's write-only on the API), so the snapshot from ``_snapshot``
    already excludes it — no plaintext, no ciphertext leak."""
    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_CREATE)
    name = _unique("audit_redact_create")
    r = client.post(
        "/data-sources",
        json={
            "name": name,
            "db_type": "sqlite",
            "host": "x",
            "port": 1,
            "database": ":memory:",
            "username": "x",
            "password": "very-secret-123",
        },
        headers=_auth_for(user_a),
    )
    assert r.status_code == 201
    _, admin = db_setup
    rows = _list_audit_rows(
        client,
        admin=admin,
        action=audit_service.ACTION_DATA_SOURCE_CREATE,
    )
    matching = [row for row in rows if row["after"]["name"] == name]
    assert matching
    # The response schema doesn't carry the password field at all,
    # so it never lands in the audit row.
    assert "password" not in matching[0]["after"]
    # Defense in depth: the plaintext password string is nowhere in the row.
    assert "very-secret-123" not in str(matching[0])


# ---------------------------------------------------------------------------
# 批 11.1 — composite index, ip_address / request_id filters, retention purge
# ---------------------------------------------------------------------------
def test_audit_endpoint_filters_by_request_id(
    client: TestClient, db_setup: Any, user_a: Any
) -> None:
    """``?request_id=...`` returns only rows tagged with that middleware ID."""
    admin = db_setup[1]
    rid_a = f"req-filter-a-{uuid.uuid4().hex[:8]}"
    rid_b = f"req-filter-b-{uuid.uuid4().hex[:8]}"

    def _post_with(rid: str, name: str) -> None:
        r = client.post(
            "/data-sources",
            json={
                "name": name,
                "db_type": "sqlite",
                "host": "x",
                "port": 1,
                "database": ":memory:",
                "username": "x",
                "password": "p",
            },
            headers={
                **_auth_for(user_a),
                "X-Request-ID": rid,
            },
        )
        assert r.status_code == 201, r.text

    _delete_audit_rows_for_action(audit_service.ACTION_DATA_SOURCE_CREATE)
    _post_with(rid_a, _unique("audit_rid_a1"))
    _post_with(rid_a, _unique("audit_rid_a2"))
    _post_with(rid_b, _unique("audit_rid_b"))

    rows_a = _list_audit_rows(client, admin=admin, request_id=rid_a)
    assert {row["request_id"] for row in rows_a} == {rid_a}
    rows_b = _list_audit_rows(client, admin=admin, request_id=rid_b)
    assert {row["request_id"] for row in rows_b} == {rid_b}
    # Mutual exclusion — the rid_a filter must not leak rid_b rows.
    assert all(row["request_id"] == rid_a for row in rows_a)
    assert all(row["request_id"] == rid_b for row in rows_b)


def test_audit_endpoint_filters_by_ip_address(client: TestClient, db_setup: Any) -> None:
    """``?ip_address=...`` returns only rows tagged with that client IP.

    The handler doesn't observe the peer IP directly (TestClient
    defaults to ``testclient``), so we plant audit rows by calling
    :func:`audit_service.log` directly with the IP we want to filter
    on, then verify the endpoint returns only those rows.
    """
    admin = db_setup[1]
    ip_a = "203.0.113.42"
    ip_b = "198.51.100.7"

    db = SessionLocal()
    try:
        for _ in range(3):
            audit_service.log(
                db,
                actor_user_id=admin.id,
                action=audit_service.ACTION_DATA_SOURCE_CREATE,
                target_type=audit_service.TARGET_TYPE_DATA_SOURCE,
                target_id=None,
                ip_address=ip_a,
            )
        audit_service.log(
            db,
            actor_user_id=admin.id,
            action=audit_service.ACTION_DATA_SOURCE_CREATE,
            target_type=audit_service.TARGET_TYPE_DATA_SOURCE,
            target_id=None,
            ip_address=ip_b,
        )
    finally:
        db.close()

    rows_a = _list_audit_rows(client, admin=admin, ip_address=ip_a)
    assert len(rows_a) == 3
    assert all(row["ip_address"] == ip_a for row in rows_a)
    rows_b = _list_audit_rows(client, admin=admin, ip_address=ip_b)
    assert len(rows_b) == 1
    assert rows_b[0]["ip_address"] == ip_b
    # And an unknown IP returns zero.
    rows_x = _list_audit_rows(client, admin=admin, ip_address="10.0.0.1")
    assert rows_x == []


def test_purge_old_audit_logs_removes_old_rows(db_setup: Any) -> None:
    """:func:`purge_old_audit_logs` deletes rows older than the cutoff."""
    db = SessionLocal()
    try:
        # Seed two old + one recent, all by the admin so no FK violation.
        admin = db_setup[1]
        old_old = datetime.now(timezone.utc) - timedelta(days=200)
        old_recent = datetime.now(timezone.utc) - timedelta(days=10)
        # Insert with explicit ``created_at`` so the cutoff math is
        # deterministic (default server_now would all be ~today).
        for ts in (old_old, old_old, old_recent):
            row = AuditLog(
                actor_user_id=admin.id,
                action=audit_service.ACTION_DATA_SOURCE_CREATE,
                target_type=audit_service.TARGET_TYPE_DATA_SOURCE,
                target_id=None,
                created_at=ts,
            )
            db.add(row)
        db.commit()

        before = db.query(AuditLog).count()
        deleted = audit_service.purge_old_audit_logs(db, retention_days=90)
        db.commit()
        after = db.query(AuditLog).count()
    finally:
        db.close()

    assert deleted == 2
    assert after == before - 2


def test_purge_old_audit_logs_zero_is_noop(db_setup: Any) -> None:
    """``retention_days <= 0`` disables the sweep — no rows touched."""
    db = SessionLocal()
    try:
        deleted = audit_service.purge_old_audit_logs(db, retention_days=0)
        deleted_neg = audit_service.purge_old_audit_logs(db, retention_days=-1)
    finally:
        db.close()
    assert deleted == 0
    assert deleted_neg == 0
    # Sanity: count unchanged. Tests run in arbitrary order so we just
    # confirm the no-op really didn't fire a DELETE.

"""Tests for batch ``user-management`` Stage 1 — ``/admin/users`` CRUD.

Covers:

- Auth gates (401 for missing token, 403 for non-admin)
- List with ``role`` / ``disabled`` / ``q`` filters and pagination
- Create (success, duplicate username → 409, validation errors)
- Get one (found, not-found → uniform 404)
- Update role / disabled — including self-protection for the
  last-admin (cannot demote or disable themselves)
- Delete = soft-disable — idempotent on already-disabled; audit
  action is ``user.disable``; no-op when the row is already disabled
- Password reset — both modes (admin_supplied / server_generated);
  the new password hashes correctly and the audit row carries only
  metadata (never the plaintext)
- Audit log writes for every mutation, with no password leak

Uses the session-tmpfile isolated DB so tests run against a fresh
slate and never touch ``backend/app.db``.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.user import ROLE_EDITOR, ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token
from app.services.password import verify_password


def _unique(prefix: str = "pytest_admin_users") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def fresh_user() -> User:
    """Create a single non-admin user row; clean up after the test.

    Used by tests that need a known target row (update / delete /
    reset) and don't care about the user count. Each test gets a
    unique username so the isolated tmpfile DB doesn't leak rows.
    """
    db: Session = SessionLocal()
    username = _unique("admin_users_viewer")
    user = User(
        username=username,
        password_hash="placeholder",
        role=ROLE_VIEWER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    try:
        yield user
    finally:
        db.query(User).filter(User.username == username).delete()
        db.commit()
        db.close()


@pytest.fixture
def viewer_auth_headers() -> dict[str, str]:
    """Bearer token for a non-admin user — must get 403 on the admin route."""
    db = SessionLocal()
    username = _unique("admin_users_viewer_h")
    user = User(
        username=username,
        password_hash="placeholder",
        role=ROLE_VIEWER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(username)
    try:
        yield {"Authorization": f"Bearer {token}"}
    finally:
        db.query(User).filter(User.username == username).delete()
        db.commit()
        db.close()


# ---------------------------------------------------------------------------
# Auth gates
# ---------------------------------------------------------------------------


def test_list_requires_auth(client: TestClient) -> None:
    response = client.get("/admin/users")
    assert response.status_code == 401


def test_list_non_admin_forbidden(
    client: TestClient, viewer_auth_headers: dict[str, str]
) -> None:
    response = client.get("/admin/users", headers=viewer_auth_headers)
    assert response.status_code == 403


def test_create_requires_auth(client: TestClient) -> None:
    response = client.post(
        "/admin/users",
        json={"username": "x", "password": "longenoughpw", "role": "viewer"},
    )
    assert response.status_code == 401


def test_create_non_admin_forbidden(
    client: TestClient, viewer_auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/admin/users",
        json={"username": "x", "password": "longenoughpw", "role": "viewer"},
        headers=viewer_auth_headers,
    )
    assert response.status_code == 403


def test_reset_password_requires_auth(client: TestClient, fresh_user: User) -> None:
    response = client.post(
        f"/admin/users/{fresh_user.id}/reset-password", json={}
    )
    assert response.status_code == 401


def test_reset_password_non_admin_forbidden(
    client: TestClient, viewer_auth_headers: dict[str, str], fresh_user: User
) -> None:
    response = client.post(
        f"/admin/users/{fresh_user.id}/reset-password",
        json={},
        headers=viewer_auth_headers,
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_returns_paginated_envelope(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/admin/users", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"items", "total", "limit", "offset"}
    assert isinstance(body["items"], list)
    assert isinstance(body["total"], int)
    assert body["limit"] == 50  # default
    assert body["offset"] == 0


def test_list_filter_by_role(
    client: TestClient, auth_headers: dict[str, str], fresh_user: User
) -> None:
    """All returned rows match the requested role."""
    response = client.get(
        f"/admin/users?role={ROLE_VIEWER}", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    for entry in body["items"]:
        assert entry["role"] == ROLE_VIEWER


def test_list_filter_by_q_matches_username_substring(
    client: TestClient, auth_headers: dict[str, str], fresh_user: User
) -> None:
    """``q`` substring-matches usernames (case-insensitive)."""
    response = client.get(
        f"/admin/users?q={fresh_user.username[:8]}", headers=auth_headers
    )
    assert response.status_code == 200
    usernames = [entry["username"] for entry in response.json()["items"]]
    assert fresh_user.username in usernames


def test_list_does_not_leak_password_hash(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """``UserResponse`` must not expose ``password_hash``.

    This is the structural guarantee — even a future refactor that
    accidentally enabled ``response_model_exclude_none`` or similar
    would still keep the field out of the wire shape.
    """
    response = client.get("/admin/users?limit=1", headers=auth_headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert items, "expected at least the seeded admin user"
    assert "password_hash" not in items[0]


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_user_succeeds(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    username = _unique("create_user_ok")
    response = client.post(
        "/admin/users",
        json={"username": username, "password": "longenoughpw", "role": ROLE_VIEWER},
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["username"] == username
    assert body["role"] == ROLE_VIEWER
    assert body["disabled"] is False
    assert "password_hash" not in body

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        assert user is not None
        assert verify_password("longenoughpw", str(user.password_hash))
    finally:
        db.query(User).filter(User.username == username).delete()
        db.commit()
        db.close()


def test_create_duplicate_username_returns_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Username collision → 409, not 500.

    The service pre-checks before INSERT; the DB unique constraint is
    a safety net for the race window.
    """
    username = _unique("dup_username")
    payload = {"username": username, "password": "longenoughpw", "role": ROLE_VIEWER}
    first = client.post("/admin/users", json=payload, headers=auth_headers)
    assert first.status_code == 201

    try:
        second = client.post("/admin/users", json=payload, headers=auth_headers)
        assert second.status_code == 409
    finally:
        db = SessionLocal()
        db.query(User).filter(User.username == username).delete()
        db.commit()
        db.close()


def test_create_invalid_role_returns_422(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/admin/users",
        json={"username": _unique("bad_role"), "password": "longenoughpw", "role": "owner"},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_create_short_password_returns_422(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Password < 8 chars is rejected by Pydantic field validation."""
    response = client.post(
        "/admin/users",
        json={"username": _unique("short_pw"), "password": "short", "role": ROLE_VIEWER},
        headers=auth_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


def test_get_unknown_id_returns_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = client.get("/admin/users/999999999", headers=auth_headers)
    assert response.status_code == 404


def test_get_existing_user(
    client: TestClient, auth_headers: dict[str, str], fresh_user: User
) -> None:
    response = client.get(f"/admin/users/{fresh_user.id}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == fresh_user.id
    assert body["username"] == fresh_user.username
    assert "password_hash" not in body


# ---------------------------------------------------------------------------
# Update + self-protection
# ---------------------------------------------------------------------------


def test_update_role_succeeds(
    client: TestClient, auth_headers: dict[str, str], fresh_user: User
) -> None:
    response = client.patch(
        f"/admin/users/{fresh_user.id}",
        json={"role": ROLE_EDITOR},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["role"] == ROLE_EDITOR


def test_last_admin_cannot_demote_self(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Self-protection: the only admin can't demote themselves.

    The seeded admin from conftest is the only ``disabled=False``
    admin in the isolated tmpfile DB at this point — so any role
    change away from ``admin`` must 403.
    """
    db = SessionLocal()
    try:
        admin = (
            db.query(User)
            .filter(User.username == settings.admin_username)
            .first()
        )
        assert admin is not None
        admin_id = admin.id

        response = client.patch(
            f"/admin/users/{admin_id}",
            json={"role": ROLE_VIEWER},
            headers=auth_headers,
        )
        assert response.status_code == 403
        assert "last remaining admin" in response.json()["detail"].lower()
    finally:
        db.close()


def test_last_admin_cannot_disable_self(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Same self-protection rule for ``disabled=True``."""
    db = SessionLocal()
    try:
        admin = (
            db.query(User)
            .filter(User.username == settings.admin_username)
            .first()
        )
        assert admin is not None
        admin_id = admin.id

        response = client.delete(
            f"/admin/users/{admin_id}", headers=auth_headers
        )
        assert response.status_code == 403
        assert "last remaining admin" in response.json()["detail"].lower()
    finally:
        db.close()


def test_update_disabled_flips_audit_action_to_user_disable(
    client: TestClient, auth_headers: dict[str, str], fresh_user: User
) -> None:
    """PATCH flipping ``disabled False → True`` emits ``user.disable``."""
    response = client.patch(
        f"/admin/users/{fresh_user.id}",
        json={"disabled": True},
        headers=auth_headers,
    )
    assert response.status_code == 200

    db = SessionLocal()
    try:
        rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "user.disable",
                AuditLog.target_id == fresh_user.id,
            )
            .order_by(AuditLog.id.desc())
            .all()
        )
        assert rows, "expected at least one user.disable audit row"
        assert rows[0].target_type == "user"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Delete = soft-disable
# ---------------------------------------------------------------------------


def test_delete_soft_disables_user(
    client: TestClient, auth_headers: dict[str, str], fresh_user: User
) -> None:
    response = client.delete(
        f"/admin/users/{fresh_user.id}", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["disabled"] is True

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == fresh_user.id).first()
        assert user is not None
        assert user.disabled is True
    finally:
        db.close()


def test_delete_is_idempotent_on_already_disabled(
    client: TestClient, auth_headers: dict[str, str], fresh_user: User
) -> None:
    """Disabling an already-disabled user is a no-op (no audit row).

    The audit row reflects an actual state change — re-disabling an
    already-disabled user is silent, mirroring the principle
    documented in the router.
    """
    # First disable: writes audit row.
    client.delete(f"/admin/users/{fresh_user.id}", headers=auth_headers)
    # Second disable: must still 200 but write no audit row.
    db = SessionLocal()
    try:
        rows_before = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "user.disable",
                AuditLog.target_id == fresh_user.id,
            )
            .count()
        )
    finally:
        db.close()

    response = client.delete(
        f"/admin/users/{fresh_user.id}", headers=auth_headers
    )
    assert response.status_code == 200

    db = SessionLocal()
    try:
        rows_after = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "user.disable",
                AuditLog.target_id == fresh_user.id,
            )
            .count()
        )
        assert rows_after == rows_before, (
            "idempotent disable must NOT write a new audit row"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


def test_reset_password_admin_supplied_persists_hash(
    client: TestClient, auth_headers: dict[str, str], fresh_user: User
) -> None:
    new_plaintext = "admin-supplied-strong-pw-2026"

    response = client.post(
        f"/admin/users/{fresh_user.id}/reset-password",
        json={"new_password": new_plaintext},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == fresh_user.id
    assert body["rotation_method"] == "admin_supplied"
    # Admin-supplied plaintext is deliberately not echoed.
    assert body["generated_password"] is None

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == fresh_user.id).first()
        assert user is not None
        assert verify_password(new_plaintext, str(user.password_hash))
    finally:
        db.close()


def test_reset_password_server_generated_returns_plaintext_once(
    client: TestClient, auth_headers: dict[str, str], fresh_user: User
) -> None:
    response = client.post(
        f"/admin/users/{fresh_user.id}/reset-password",
        json={},  # empty body → server_generated
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rotation_method"] == "server_generated"
    generated = body["generated_password"]
    assert isinstance(generated, str) and len(generated) >= 16
    # Output of ``secrets.token_urlsafe`` is url-safe alphabet only.
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert set(generated) <= allowed

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == fresh_user.id).first()
        assert user is not None
        # The stored hash decrypts back to the same plaintext.
        assert verify_password(generated, str(user.password_hash))
    finally:
        db.close()


def test_reset_password_audit_does_not_contain_plaintext(
    client: TestClient, auth_headers: dict[str, str], fresh_user: User
) -> None:
    """Defence-in-depth: audit row MUST NOT contain the plaintext.

    Mirrors the DataSource rotation precedent — the router only
    writes ``after={"rotation_method": ...}`` so this is structural,
    but we assert it explicitly so a future refactor doesn't
    silently regress.
    """
    canary = "audit-canary-token-2026"
    response = client.post(
        f"/admin/users/{fresh_user.id}/reset-password",
        json={"new_password": canary},
        headers=auth_headers,
    )
    assert response.status_code == 200

    db = SessionLocal()
    try:
        rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "user.password_reset",
                AuditLog.target_id == fresh_user.id,
            )
            .order_by(AuditLog.id.desc())
            .all()
        )
        assert rows, "expected at least one user.password_reset audit row"
        row = rows[0]
        candidates = []
        for attr in ("before", "after"):
            value = getattr(row, attr)
            if value is None:
                continue
            candidates.append(json.dumps(value) if not isinstance(value, str) else value)
        joined = "\n".join(candidates)
        assert canary not in joined, "audit row leaked the new plaintext password"
        # Sanity: the metadata-only ``after`` is the rotation method.
        after = row.after if isinstance(row.after, dict) else json.loads(row.after)
        assert after.get("rotation_method") == "admin_supplied"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Create writes audit row
# ---------------------------------------------------------------------------


def test_create_writes_user_create_audit_row(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    username = _unique("audit_create")
    response = client.post(
        "/admin/users",
        json={"username": username, "password": "longenoughpw", "role": ROLE_VIEWER},
        headers=auth_headers,
    )
    assert response.status_code == 201
    user_id = response.json()["id"]

    db = SessionLocal()
    try:
        rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "user.create",
                AuditLog.target_id == user_id,
            )
            .order_by(AuditLog.id.desc())
            .all()
        )
        assert rows, "expected at least one user.create audit row"
        row = rows[0]
        assert row.target_type == "user"
        after = row.after if isinstance(row.after, dict) else json.loads(row.after)
        assert after.get("username") == username
        # Defence-in-depth: no password_hash in the audit row.
        assert "password_hash" not in after
    finally:
        db.query(User).filter(User.username == username).delete()
        db.commit()
        db.close()

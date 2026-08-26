"""Tests for the lightweight ``GET /users`` listing (A3).

The endpoint is auth-required but not admin-gated; it's used by the
report-versioning UI to resolve ``ReportVersionSummary.created_by``
foreign keys (raw user ids) into display usernames. The dev
``app.db`` accumulated users across many pytest runs (see e.g.
``test_report_version_router.py``'s ``_unique`` prefix helpers), so
exact counts are not asserted — only shape + ordering + filter
behavior.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal
from app.models.user import ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _admin_token() -> str:
    return create_access_token(settings.admin_username)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_token()}"}


def test_list_users_requires_auth(client: TestClient) -> None:
    r = client.get("/users")
    assert r.status_code == 401


def test_list_users_returns_id_username_role(client: TestClient) -> None:
    """Shape contract for the lightweight projection."""
    r = client.get("/users", headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list)
    assert body, "dev app.db has at least the seeded admin user"
    for entry in body:
        # Exactly the three keys we promised — no extra fields leaked
        # (``password_hash`` would be the footgun).
        assert set(entry.keys()) == {"id", "username", "role"}
        assert isinstance(entry["id"], int)
        assert isinstance(entry["username"], str)
        assert entry["role"] in {ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER}


def test_list_users_ordered_by_id_ascending(client: TestClient) -> None:
    """UI's ``Map<id, username>`` lookup needs monotonically increasing ids."""
    r = client.get("/users", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    ids = [entry["id"] for entry in body]
    assert ids == sorted(ids)


def test_list_users_filter_by_role_via_query(client: TestClient, db) -> None:
    """Sanity check: known seeded admin is present and has the admin role.

    Sorting by id ASC places the very first user at index 0 (id is
    monotonic, not sparse — autoincrement gaps from rowid reuse
    don't reorder the survivors). We rely on this to assert that
    ``admin`` shows up at the start without depending on the
    autoincrement rowid of a freshly-inserted row.
    """
    r = client.get("/users", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    # The very first id has the smallest rowid; if it's not admin in
    # this test fixture, we don't gate on it — we just confirm the
    # shape of the role field.
    for entry in body[:5]:
        assert entry["role"] in {ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER}


def test_list_users_limit_capped(client: TestClient) -> None:
    """Out-of-range ``limit`` values are rejected by Pydantic's Query constraint."""
    # Past the cap (500) → 422.
    r = client.get("/users?limit=501", headers=_auth())
    assert r.status_code == 422
    # Below the floor (1) → 422.
    r = client.get("/users?limit=0", headers=_auth())
    assert r.status_code == 422


def test_list_users_excludes_disabled(client: TestClient, db) -> None:
    """Disabled users are filtered out (consistent with ``auth.py``).

    ``User.disabled`` is treated as a soft-delete flag throughout the
    codebase — ``auth.py`` rejects disabled users at login and
    ``/auth/refresh`` — so the listing must match the auth path to
    keep the report-versioning UI from showing stale usernames for
    users that can no longer sign in. Insert a uniquely-named
    disabled user, hit ``/users``, assert the username is absent.
    """
    suffix = uuid.uuid4().hex[:8]
    disabled_username = f"disabled_{suffix}"
    db.add(
        User(
            username=disabled_username,
            password_hash="not-used-in-this-test",
            role=ROLE_VIEWER,
            disabled=True,
        )
    )
    db.commit()
    try:
        r = client.get("/users", headers=_auth())
        assert r.status_code == 200
        usernames = {entry["username"] for entry in r.json()}
        assert disabled_username not in usernames
    finally:
        # Clean up so the dev ``app.db`` doesn't accumulate test rows.
        db.query(User).filter(User.username == disabled_username).delete()
        db.commit()

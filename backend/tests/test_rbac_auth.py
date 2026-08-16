"""Tests for batch 9.1 — RBAC identity shape.

Covers the new ``User.role`` / ``User.org_id`` columns and the JWT
identity claims they enable. Resource-level ACL (DataSource / Report)
is exercised by ``tests/test_data_source_acl.py`` and
``tests/test_report_acl.py`` in 批 9.3 / 9.4 — this file only checks
the auth layer itself.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.user import ROLE_ADMIN, ROLE_VIEWER, User
from app.services.jwt_auth import decode_token

# ----------------- helpers -----------------


def _unique(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def viewer_user() -> User:
    """A non-admin test user with ``role='viewer'`` and no org_id.

    Cleanup happens via CASCADE on foreign keys (none in this fixture);
    a direct DELETE in finally keeps the test isolation explicit.
    """
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_rbac_viewer"),
        password_hash="x",
        role=ROLE_VIEWER,
        org_id=None,
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
def db_setup() -> Any:
    """Pair of (Session, admin User) so each test gets a clean handle.

    Mirrors the local fixture in :mod:`tests.test_subscriptions` and
    :mod:`tests.test_job_queue` — not promoted to conftest because
    only a handful of test files need it.
    """
    db = SessionLocal()
    user = db.query(User).filter(User.username == "admin").first()
    if user is None:
        db.close()
        pytest.skip("admin user not seeded")
    try:
        yield db, user
    finally:
        db.close()


# ----------------- model layer -----------------


def test_user_model_has_role_and_org_id_columns() -> None:
    """Smoke-check the schema columns exist on the mapped class.

    Alembic migration ``371bcac5fa32`` is the source of truth for
    server-side defaults; this test guards against someone dropping
    the columns while refactoring the model.
    """
    from sqlalchemy import inspect

    from app.database import engine

    inspector = inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("users")}
    assert "role" in cols
    assert "org_id" in cols


def test_user_defaults_role_to_admin(db_setup: Any) -> None:
    """New users inserted without ``role`` fall back to 'admin'.

    Mirrors the historical bootstrap behaviour: every user is an admin
    until an explicit role is set. 批 9 flips this for new users at
    the API surface, but keeps the schema default as a safety net so
    legacy inserts (seed scripts, raw SQL migrations) keep working.
    """
    db, _ = db_setup
    user = User(
        username=_unique("pytest_default_role"),
        password_hash="x",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    try:
        assert user.role == ROLE_ADMIN
        assert user.org_id is None
    finally:
        db.delete(user)
        db.commit()


def test_existing_admin_user_has_admin_role(db_setup: Any) -> None:
    """The bootstrap admin seeded by lifespan gets role='admin' too.

    Belt-and-braces check: the migration default is ``'admin'``, but
    if someone ran ``UPDATE users SET role = 'viewer' WHERE 1=1`` in
    production this test would fail loudly.
    """
    db, user = db_setup
    assert user.role == ROLE_ADMIN


def test_viewer_user_round_trips(db_setup: Any, viewer_user: User) -> None:
    """Non-admin role persists through a refresh."""
    db, _ = db_setup
    fetched = db.get(User, viewer_user.id)
    assert fetched is not None
    assert fetched.role == ROLE_VIEWER
    assert fetched.org_id is None


# ----------------- /auth/me endpoint -----------------


def test_me_returns_identity_claims(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    r = client.get("/auth/me", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "admin"
    assert body["role"] == ROLE_ADMIN
    assert isinstance(body["user_id"], int)
    assert body["org_id"] is None


def test_me_for_viewer_user_returns_viewer_role(
    client: TestClient,
    viewer_user: User,
) -> None:
    """Login as a freshly inserted viewer and confirm /auth/me surfaces
    the right role — the JWT round-trip is the part most likely to
    silently drop the new claim."""
    from app.services.jwt_auth import create_access_token

    token = create_access_token(
        viewer_user.username,
        user_id=int(viewer_user.id),
        role=str(viewer_user.role),
        org_id=viewer_user.org_id,
    )
    r = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == viewer_user.username
    assert body["role"] == ROLE_VIEWER
    assert body["user_id"] == viewer_user.id


# ----------------- JWT claim shape -----------------


def test_access_token_carries_identity_claims(
    viewer_user: User,
) -> None:
    """Round-trip a freshly minted access token and confirm uid/role/oid
    claims survive encode → decode. Refresh tokens intentionally omit
    these — covered separately in :func:`test_refresh_token_omits_claims`."""
    from app.services.jwt_auth import create_access_token

    token = create_access_token(
        viewer_user.username,
        user_id=int(viewer_user.id),
        role=str(viewer_user.role),
        org_id=viewer_user.org_id,
    )
    payload = decode_token(token, expected_type="access")
    assert payload is not None
    assert payload["sub"] == viewer_user.username
    assert payload["uid"] == viewer_user.id
    assert payload["role"] == ROLE_VIEWER
    # ``org_id`` is None for a single-org user — ``_encode`` skips
    # the claim when the value is None to keep the token compact.
    # Future multi-tenant deployment will start emitting it as an int.
    assert "oid" not in payload


def test_refresh_token_omits_claims() -> None:
    """Refresh tokens intentionally drop identity claims so a
    mid-session role change actually re-loads from the DB on
    /auth/refresh. Without this guard, a stale role claim would
    survive the rotation."""
    from app.services.jwt_auth import create_refresh_token

    token = create_refresh_token("any-user")
    payload = decode_token(token, expected_type="refresh")
    assert payload is not None
    assert payload["sub"] == "any-user"
    assert "uid" not in payload
    assert "role" not in payload
    assert "oid" not in payload


def test_login_token_carries_current_role(
    client: TestClient,
    viewer_user: User,
) -> None:
    """End-to-end: login as the viewer, decode the issued access
    token, confirm role='viewer' is in the claims."""
    # We can't log in via /auth/login without the bcrypt-hashed
    # password the seed would have produced, so mint the token
    # directly via the public helper (the same call /auth/login uses).
    from app.services.jwt_auth import create_access_token

    token = create_access_token(
        viewer_user.username,
        user_id=int(viewer_user.id),
        role=str(viewer_user.role),
        org_id=viewer_user.org_id,
    )
    payload = decode_token(token, expected_type="access")
    assert payload is not None
    assert payload["role"] == ROLE_VIEWER


def test_get_current_user_caches_role_on_request_state(
    auth_headers: dict[str, str],
) -> None:
    """``request.state.current_user`` is the seam that 9.2's
    ``require_role`` builds on. Confirm the cached object carries the
    role string."""
    from app.database import SessionLocal
    from app.deps import get_current_user

    # Borrow the same _build_request_with_bearer helper the auth test
    # file uses — it's a local fixture there but the logic is generic.
    from tests.test_auth import _build_request_with_bearer

    request = _build_request_with_bearer(auth_headers["Authorization"])
    db = SessionLocal()
    try:
        user = get_current_user(request=request, db=db)
        assert user.role == ROLE_ADMIN
        # Same dependency call again hits the request.state cache.
        again = get_current_user(request=request, db=db)
        assert again is user
    finally:
        db.close()

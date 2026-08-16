"""Tests for batch 9.2 — ``require_role`` dependency factory.

These tests stand up a tiny FastAPI app per scenario so the
``require_role`` dependency is exercised exactly as it would be in a
production router. The factory itself is a closure over ``allowed``,
so a small per-app mounting is the cleanest way to assert "viewer
gets 403, editor gets through, admin always passes" without
spilling auth-test logic into every router's test file.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import admin_required, editor_required, get_current_user, require_role
from app.models.user import ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ----------------- helpers -----------------


def _mint_header(user: User) -> dict[str, str]:
    """Mint an Authorization header for ``user``."""
    token = create_access_token(
        user.username,
        user_id=int(user.id),
        role=str(user.role),
        org_id=user.org_id,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def editor_user() -> User:
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_editor"),
        password_hash="x",
        role=ROLE_EDITOR,
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
def viewer_user() -> User:
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_viewer"),
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
def admin_user(db_setup: Any) -> User:
    """Admin user row from the bootstrap seed."""
    _db, user = db_setup
    return user


@pytest.fixture
def db_setup() -> Any:
    """Pair of (Session, admin User) so each test gets a clean handle.

    Mirrors the local fixture in :mod:`tests.test_subscriptions` /
    :mod:`tests.test_rbac_auth` — kept local because only a handful
    of test files need raw DB access plus a real admin row.
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


def _build_test_app(*deps: Any) -> TestClient:
    """Mount a single GET ``/who`` endpoint on a throwaway app and
    return a TestClient. The endpoint echoes the role string of the
    resolved user so tests can confirm the dependency ran end to end.
    """
    app = FastAPI()
    router = APIRouter()

    @router.get("/who")
    def who(user: User = Depends(get_current_user)) -> dict[str, Any]:
        return {"role": user.role, "username": user.username}

    for dep in deps:

        @router.get("/gate")
        def gate(user: User = Depends(dep)) -> dict[str, Any]:
            return {"role": user.role}

    app.include_router(router)
    return TestClient(app)


# ----------------- admin_required preset -----------------


def test_admin_required_lets_admin_through(admin_user: User) -> None:
    client = _build_test_app(admin_required)
    r = client.get("/gate", headers=_mint_header(admin_user))
    assert r.status_code == 200
    assert r.json() == {"role": ROLE_ADMIN}


def test_admin_required_blocks_editor(editor_user: User) -> None:
    client = _build_test_app(admin_required)
    r = client.get("/gate", headers=_mint_header(editor_user))
    assert r.status_code == 403
    assert "Insufficient role" in r.json()["detail"]


def test_admin_required_blocks_viewer(viewer_user: User) -> None:
    client = _build_test_app(admin_required)
    r = client.get("/gate", headers=_mint_header(viewer_user))
    assert r.status_code == 403


def test_admin_required_without_token_returns_401() -> None:
    client = _build_test_app(admin_required)
    r = client.get("/gate")
    assert r.status_code == 401


# ----------------- editor_required preset -----------------


def test_editor_required_lets_admin_through(admin_user: User) -> None:
    """Admin always passes — even when the gate listed only editor."""
    client = _build_test_app(editor_required)
    r = client.get("/gate", headers=_mint_header(admin_user))
    assert r.status_code == 200
    assert r.json() == {"role": ROLE_ADMIN}


def test_editor_required_lets_editor_through(editor_user: User) -> None:
    client = _build_test_app(editor_required)
    r = client.get("/gate", headers=_mint_header(editor_user))
    assert r.status_code == 200
    assert r.json() == {"role": ROLE_EDITOR}


def test_editor_required_blocks_viewer(viewer_user: User) -> None:
    client = _build_test_app(editor_required)
    r = client.get("/gate", headers=_mint_header(viewer_user))
    assert r.status_code == 403


# ----------------- require_role factory -----------------


def test_require_role_accepts_single_role(admin_user: User) -> None:
    client = _build_test_app(require_role(ROLE_EDITOR))
    r = client.get("/gate", headers=_mint_header(admin_user))
    assert r.status_code == 200


def test_require_role_with_no_listed_roles_blocks_everyone_but_admin(
    admin_user: User,
    editor_user: User,
    viewer_user: User,
) -> None:
    """``require_role()`` with no args rejects everyone except admin."""
    gate = require_role()  # no roles listed
    client = _build_test_app(gate)
    assert client.get("/gate", headers=_mint_header(admin_user)).status_code == 200
    assert client.get("/gate", headers=_mint_header(editor_user)).status_code == 403
    assert client.get("/gate", headers=_mint_header(viewer_user)).status_code == 403


def test_require_role_does_not_leak_user_role_in_error(
    viewer_user: User,
) -> None:
    """The 403 message must not echo ``viewer`` — that's a small but
    useful fingerprint for an attacker enumerating valid roles.
    """
    gate = require_role(ROLE_ADMIN, ROLE_EDITOR)
    client = _build_test_app(gate)
    r = client.get("/gate", headers=_mint_header(viewer_user))
    assert r.status_code == 403
    assert ROLE_VIEWER not in r.json()["detail"]


# ----------------- dependency composition -----------------


def test_get_current_user_still_works_independently(admin_user: User) -> None:
    """The new factory doesn't break the existing ``get_current_user``
    dependency — both can co-exist on the same app."""
    client = _build_test_app(admin_required)
    r = client.get("/who", headers=_mint_header(admin_user))
    assert r.status_code == 200
    assert r.json()["username"] == admin_user.username

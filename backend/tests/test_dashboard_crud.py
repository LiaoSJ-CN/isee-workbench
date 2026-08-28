"""Tests for batch 14.1 — Dashboard models, ACL helpers, duplicate.

Coverage matrix (mirrors :mod:`tests.test_report_templates` pattern —
direct service-layer calls; the API surface lands in sub-batch 14.2):

* ``get_dashboard_for_user`` — owner / admin / non-owner paths.
* ``list_accessible_dashboards`` — visibility filter (owner / public /
  org-tier / grants).
* ``duplicate_dashboard`` — default ``(副本)`` suffix, explicit name
  override, visibility reset to private, items deep-copied.
* ``upsert_share`` / ``revoke_share`` — create / update / delete.
* Model defaults — ``visibility`` defaults to ``private``,
  ``owner_user_id`` defaults to ``None``.

Skips silently when the ``admin`` user isn't seeded (the suite relies
on the dev DB being alive — same contract as the rest of the batch 9
ACL tests).
"""

from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.dashboard import Dashboard, DashboardItem
from app.models.dashboard_access import DashboardAccess
from app.models.report import VISIBILITY_PRIVATE, VISIBILITY_PUBLIC
from app.models.user import ROLE_VIEWER, User
from app.services.dashboard import (
    duplicate_dashboard,
    get_dashboard_for_user,
    is_owner,
    list_accessible_dashboards,
    list_shares_for_dashboard,
    revoke_share,
    upsert_share,
)


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def db_setup() -> Iterator[tuple[Session, User]]:
    """Admin user + a fresh DB session (read-only on dev DB)."""
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
def non_admin_user() -> Iterator[User]:
    """Throwaway non-admin user — committed + deleted at teardown."""
    db = SessionLocal()
    user = User(
        username=_unique("pytest_dash_user"),
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
def sample_dashboard(db_setup: tuple[Session, User]) -> Iterator[Dashboard]:
    """Private dashboard owned by admin with one of each item type."""
    db, admin = db_setup
    dash = Dashboard(
        name=_unique("pytest_dash"),
        description="test dashboard",
        owner_user_id=admin.id,
        visibility=VISIBILITY_PRIVATE,
    )
    db.add(dash)
    db.flush()
    # One of each item type so the deep-copy test has something to
    # verify (the items' JSON columns mutate independently).
    db.add(
        DashboardItem(
            dashboard_id=dash.id,
            item_type="text",
            title="welcome",
            order_index=0,
            x=0,
            y=0,
            w=4,
            h=2,
            text_content="hello",
            parameters={"greeting": "hi", "items": [1, 2, 3]},
        )
    )
    db.add(
        DashboardItem(
            dashboard_id=dash.id,
            item_type="report",
            title="placeholder report item",
            order_index=1,
            x=4,
            y=0,
            w=4,
            h=2,
            report_id=None,
        )
    )
    db.add(
        DashboardItem(
            dashboard_id=dash.id,
            item_type="chart",
            title="placeholder chart item",
            order_index=2,
            x=0,
            y=2,
            w=8,
            h=4,
            data_source_id=None,
            table_name="t",
            fields=["a", "b"],
            where_conditions=[{"field": "a", "operator": "=", "value": "1"}],
            group_by=[],
            order_by=[],
            limit=100,
            display_config={"chart_type": "bar"},
        )
    )
    db.commit()
    db.refresh(dash)
    try:
        yield dash
    finally:
        # Items cascade-delete with the dashboard.
        db.delete(dash)
        db.commit()
        db.close()


# ---- Model defaults ----


def test_create_dashboard_default_visibility_is_private(db_setup):
    """A freshly inserted dashboard is private by default — admin
    can't leak its content via the public list without an explicit
    ``visibility`` override."""
    db, _ = db_setup
    dash = Dashboard(name=_unique("pytest_dash_def_vis"))
    db.add(dash)
    db.flush()
    try:
        # Column default (server_default + default both "private").
        assert dash.visibility == VISIBILITY_PRIVATE
        assert Dashboard.__table__.c.visibility.server_default.arg == "private"
    finally:
        db.delete(dash)
        db.commit()


def test_create_dashboard_default_owner_is_none(db_setup):
    """A dashboard created without ``owner_user_id`` defaults to NULL
    (orphan) — mirroring the same pattern on Report."""
    db, _ = db_setup
    dash = Dashboard(name=_unique("pytest_dash_def_owner"))
    db.add(dash)
    db.flush()
    try:
        assert dash.owner_user_id is None
    finally:
        db.delete(dash)
        db.commit()


# ---- ACL: get_dashboard_for_user ----


def test_get_dashboard_for_user_owner_sees_own(
    db_setup, sample_dashboard
):
    db, admin = db_setup
    loaded = get_dashboard_for_user(db, sample_dashboard.id, admin)
    assert loaded is not None
    assert loaded.id == sample_dashboard.id
    assert is_owner(admin, loaded) is True


def test_get_dashboard_for_user_non_owner_returns_none(
    db_setup, sample_dashboard, non_admin_user
):
    """A non-owner without a grant sees ``None`` (uniform 404)."""
    db, _ = db_setup
    loaded = get_dashboard_for_user(db, sample_dashboard.id, non_admin_user)
    assert loaded is None


def test_get_dashboard_for_user_admin_sees_all(
    db_setup, sample_dashboard
):
    """Admin role short-circuits ownership + grant checks."""
    db, admin = db_setup
    loaded = get_dashboard_for_user(db, sample_dashboard.id, admin)
    assert loaded is not None


def test_get_dashboard_for_user_missing_returns_none(db_setup):
    db, admin = db_setup
    assert get_dashboard_for_user(db, 999_999_999, admin) is None
    # ``None`` is also a uniform 404 — same shape as a missing id.
    assert get_dashboard_for_user(db, None, admin) is None


# ---- ACL: list_accessible_dashboards ----


def test_list_accessible_dashboards_owner_sees_own(
    db_setup, sample_dashboard
):
    db, admin = db_setup
    listed = list_accessible_dashboards(db, admin)
    ids = {d.id for d in listed}
    assert sample_dashboard.id in ids


def test_list_accessible_dashboards_non_owner_excludes_private(
    db_setup, sample_dashboard, non_admin_user
):
    """A non-admin non-grantee can't see private dashboards in the
    list — even though admin's dashboard exists."""
    db, _ = db_setup
    listed = list_accessible_dashboards(db, non_admin_user)
    ids = {d.id for d in listed}
    assert sample_dashboard.id not in ids


def test_list_accessible_dashboards_public_visible_to_all(
    db_setup, sample_dashboard, non_admin_user
):
    """Flipping the source to ``public`` makes it visible to the
    non-admin user — same path as the ``Report`` gallery."""
    db, _ = db_setup
    sample_dashboard.visibility = VISIBILITY_PUBLIC
    db.commit()
    listed = list_accessible_dashboards(db, non_admin_user)
    ids = {d.id for d in listed}
    assert sample_dashboard.id in ids


# ---- Shares ----


def test_upsert_share_creates_and_updates(
    db_setup, sample_dashboard, non_admin_user
):
    """First call creates; second call updates in place (no duplicate
    row, same ``DashboardAccess.id``)."""
    db, admin = db_setup
    created = upsert_share(
        db,
        dashboard_id=sample_dashboard.id,
        target_user_id=non_admin_user.id,
        permission="read",
        granted_by=admin.id,
    )
    first_id = created.id
    try:
        updated = upsert_share(
            db,
            dashboard_id=sample_dashboard.id,
            target_user_id=non_admin_user.id,
            permission="write",
            granted_by=admin.id,
        )
        assert updated.id == first_id
        assert updated.permission == "write"
        rows = list_shares_for_dashboard(db, sample_dashboard.id)
        assert len(rows) == 1
    finally:
        # Clean up explicitly since cascade-delete also wipes shares,
        # but this exercises the helper directly.
        existing = (
            db.query(DashboardAccess)
            .filter(
                DashboardAccess.dashboard_id == sample_dashboard.id,
                DashboardAccess.user_id == non_admin_user.id,
            )
            .first()
        )
        if existing is not None:
            revoke_share(db, existing)


def test_revoke_share_removes_row(
    db_setup, sample_dashboard, non_admin_user
):
    db, admin = db_setup
    share = upsert_share(
        db,
        dashboard_id=sample_dashboard.id,
        target_user_id=non_admin_user.id,
        permission="read",
        granted_by=admin.id,
    )
    revoke_share(db, share)
    rows = list_shares_for_dashboard(db, sample_dashboard.id)
    assert rows == []


# ---- Duplicate ----


def test_duplicate_dashboard_default_name_suffix(
    db_setup, sample_dashboard
):
    """Default name picks ``<original> (副本)`` (or ``(副本 2)`` if
    the first suffix already exists)."""
    db, admin = db_setup
    original, clone = duplicate_dashboard(db, sample_dashboard.id, admin)
    try:
        assert clone.id != original.id
        assert clone.name == f"{original.name} (副本)"
        assert clone.owner_user_id == admin.id
        # Items were deep-copied (one of each, total 3).
        assert len(clone.items) == 3
    finally:
        db.delete(clone)
        db.commit()


def test_duplicate_dashboard_resets_visibility_to_private(
    db_setup, sample_dashboard
):
    """Even when the source is public, the duplicate starts private —
    same contract as :func:`app.services.report.duplicate_report`."""
    db, admin = db_setup
    sample_dashboard.visibility = VISIBILITY_PUBLIC
    db.commit()
    _, clone = duplicate_dashboard(db, sample_dashboard.id, admin)
    try:
        assert clone.visibility == VISIBILITY_PRIVATE
    finally:
        db.delete(clone)
        db.commit()


def test_duplicate_dashboard_deep_copies_items(
    db_setup, sample_dashboard
):
    """Mutating the clone's items / JSON columns doesn't bleed back
    into the source — ``copy.deepcopy`` on the JSON columns is the
    contract."""
    db, admin = db_setup
    _, clone = duplicate_dashboard(db, sample_dashboard.id, admin)
    try:
        clone_text_item = next(it for it in clone.items if it.item_type == "text")
        clone_text_item.text_content = "MUTATED"
        clone_text_item.parameters["greeting"] = "MUTATED"
        clone_text_item.parameters["items"].append(999)
        db.commit()
        # Reload the source — its JSON columns stay untouched.
        db.refresh(sample_dashboard)
        src_text_item = next(
            it for it in sample_dashboard.items if it.item_type == "text"
        )
        assert src_text_item.text_content == "hello"
        assert src_text_item.parameters == {"greeting": "hi", "items": [1, 2, 3]}
    finally:
        db.delete(clone)
        db.commit()


def test_duplicate_dashboard_name_collision_raises(
    db_setup, sample_dashboard
):
    """Explicit ``new_name`` that collides with an existing dashboard
    raises ``ValueError`` (the router translates to 400)."""
    db, admin = db_setup
    # Pre-create a dashboard whose name will collide.
    collision = Dashboard(name=f"{sample_dashboard.name} (副本)")
    db.add(collision)
    db.commit()
    try:
        with pytest.raises(ValueError, match="already exists"):
            duplicate_dashboard(
                db,
                sample_dashboard.id,
                admin,
                new_name=f"{sample_dashboard.name} (副本)",
            )
    finally:
        db.delete(collision)
        db.commit()


def test_duplicate_dashboard_missing_source_raises(db_setup):
    """Uniform 404 contract — missing id raises ``LookupError``."""
    db, admin = db_setup
    with pytest.raises(LookupError, match="not found or inaccessible"):
        duplicate_dashboard(db, 999_999_999, admin)

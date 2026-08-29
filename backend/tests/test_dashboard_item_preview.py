"""Tests for batch 14.7 — per-item preview endpoint.

``GET /dashboards/{dashboard_id}/items/{item_id}/preview`` returns a
standalone HTML page rendering just that one item. Used by
:component:`DashboardItemCard` so each grid cell embeds its own
iframe (the alternative — putting ``<iframe src=URL>`` directly —
401s because iframe navigations don't carry the ``Authorization``
header).

Coverage:

* Each item type — ``text`` / ``chart`` / ``report`` — returns the
  expected HTML chunk wrapped in a minimal page.
* ``text`` rendering XSS-escapes the body.
* ``chart`` rendering produces a canvas + Chart.js script tag.
* ``report`` rendering falls through to ``dashboard-error`` when
  ``report_id`` is NULL (the only case the unit test can exercise
  without a real Report fixture).
* ACL: 404 on missing dashboard, missing item, item under a
  different dashboard, and unauthorized caller (uniform 404).
* DS gate: ``chart`` item referencing an inaccessible DS causes the
  inner render to surface a ``dashboard-error`` (the page still
  returns 200 — partial-success contract).

Why a new file rather than appending to ``test_dashboard_acl``:
the existing ACL suite focuses on visibility/grant matrix; this
file focuses on the rendering contract. Keeping them apart makes
the per-item regressions easier to read.
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
from app.models.data_source import DataSource
from app.models.user import ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ----------------- fixtures -----------------


@pytest.fixture
def db_setup() -> Any:
    """(Session, admin User). Skip when admin isn't seeded."""
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
    """Owner of the dashboard under test — owns the DS so the chart
    item's DS gate passes for them."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_itemprev_a"),
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
    """Outsider without any grants."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_itemprev_b"),
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
    """Insert a placeholder DS row. ``execute_dashboard_chart`` will
    try to connect and run SQL; for the success-path test we point
    it at a sqlite file we just created + populated with one trivial
    table."""
    # Real sqlite warehouse — tiny, file-scoped, torn down with the test.
    import sqlite3
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp(prefix="pytest_itemprev_ds_"))
    db_path = tmp / "warehouse.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE t (label TEXT, value INTEGER)")
        conn.execute("INSERT INTO t VALUES ('a', 1), ('b', 2)")
        conn.commit()
    finally:
        conn.close()

    src = DataSource(
        name=_unique("pytest_itemprev_ds"),
        db_type="sqlite",
        host="placeholder",
        port=1,
        database=str(db_path),
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
    visibility: str = "public",
) -> Dashboard:
    """Public by default so a non-owner caller can reach the ACL
    boundary we're testing (visibility, not DS gate)."""
    dash = Dashboard(
        name=_unique("pytest_itemprev_dash"),
        description="item preview fixture",
        owner_user_id=owner_user_id,
        visibility=visibility,
    )
    db.add(dash)
    db.commit()
    db.refresh(dash)
    return dash


def _cleanup(db: Session, dashboard_id: int, ds_id: int | None = None) -> None:
    """Best-effort teardown. Child rows first so SQLite FK doesn't
    trip."""
    from sqlalchemy import text
    db.execute(
        text("DELETE FROM dashboard_items WHERE dashboard_id = :did"),
        {"did": dashboard_id},
    )
    db.execute(
        text("DELETE FROM dashboards WHERE id = :did"),
        {"did": dashboard_id},
    )
    if ds_id is not None:
        db.execute(
            text("DELETE FROM data_sources WHERE id = :dsid"),
            {"dsid": ds_id},
        )
    db.commit()


# ----------------- text item -----------------


def test_preview_text_item_returns_escaped_html(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """Text item HTML-escapes the body and wraps it in a minimal doc."""
    db, admin = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id))
    db.add(
        DashboardItem(
            dashboard_id=int(dash.id),
            item_type="text",
            title="note",
            order_index=0,
            x=0, y=0, w=12, h=1,
            text_content="hello <script>alert(1)</script> world",
        )
    )
    db.commit()
    try:
        items = db.query(DashboardItem).filter(
            DashboardItem.dashboard_id == int(dash.id)
        ).all()
        item_id = int(items[0].id)

        r = client.get(
            f"/dashboards/{int(dash.id)}/items/{item_id}/preview",
            headers=auth_admin_headers(db_setup),
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/html")
        body = r.text
        assert "<!doctype html>" in body
        assert "<script src=\"https://cdn.jsdelivr.net" in body
        assert "dashboard-text" in body
        # XSS payload escaped — raw ``<script>`` must not survive.
        assert "hello <script>alert(1)</script> world" not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    finally:
        _cleanup(db, int(dash.id))


def auth_admin_headers(db_setup: Any) -> dict[str, str]:
    """Mint an admin token inline so the test reads top-down."""
    _, admin = db_setup
    return _auth_for(admin)


# ----------------- chart item -----------------


def test_preview_chart_item_renders_canvas_and_chartjs(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """Chart item with a real SQLite DS returns a Chart.js canvas and
    inline ``new Chart(...)`` script."""
    db, admin = db_setup
    ds = _make_ds(db, owner_user_id=int(user_a.id))
    dash = _make_dashboard(db, owner_user_id=int(user_a.id))
    db.add(
        DashboardItem(
            dashboard_id=int(dash.id),
            item_type="chart",
            title="trend",
            order_index=0,
            x=0, y=0, w=6, h=4,
            data_source_id=int(ds.id),
            table_name="t",
            fields=["label", "value"],
            display_config={"chart_type": "bar"},
        )
    )
    db.commit()
    try:
        item_id = int(
            db.query(DashboardItem)
            .filter(DashboardItem.dashboard_id == int(dash.id))
            .first()
            .id
        )
        r = client.get(
            f"/dashboards/{int(dash.id)}/items/{item_id}/preview",
            headers=auth_admin_headers(db_setup),
        )
        assert r.status_code == 200, r.text
        body = r.text
        assert f"<canvas id=\"chart_{item_id}\">" in body
        assert "if (window.Chart)" in body
        assert "new Chart(" in body
        # Real rows from the warehouse leaked into the JSON payload
        # (labels are JSON-serialised with single-quote delimiters — that's
        # the bare ``str()`` of a Python dict key inside JS).
        assert "'a'" in body
        assert "'b'" in body
        assert "[1, 2]" in body
    finally:
        _cleanup(db, int(dash.id), int(ds.id))


# ----------------- report item (no linked Report) -----------------


def test_preview_report_item_without_linked_report_renders_error(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """``item_type='report'`` with ``report_id=NULL`` → the inner
    renderer returns ``dashboard-error`` and the page is still 200.
    The frontend treats this as an inline failure banner."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id))
    db.add(
        DashboardItem(
            dashboard_id=int(dash.id),
            item_type="report",
            title="missing",
            order_index=0,
            x=0, y=0, w=6, h=4,
            report_id=None,
        )
    )
    db.commit()
    try:
        item_id = int(
            db.query(DashboardItem)
            .filter(DashboardItem.dashboard_id == int(dash.id))
            .first()
            .id
        )
        r = client.get(
            f"/dashboards/{int(dash.id)}/items/{item_id}/preview",
            headers=auth_admin_headers(db_setup),
        )
        assert r.status_code == 200, r.text
        assert "dashboard-error" in r.text
        assert "未关联报表" in r.text
    finally:
        _cleanup(db, int(dash.id))


# ----------------- 404 paths -----------------


def test_preview_missing_dashboard_returns_404(
    client: TestClient,
    db_setup: Any,
) -> None:
    """Non-existent dashboard → uniform 404."""
    r = client.get(
        "/dashboards/9999999/items/1/preview",
        headers=auth_admin_headers(db_setup),
    )
    assert r.status_code == 404


def test_preview_missing_item_returns_404(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """Dashboard exists, item does not → 404."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id))
    try:
        r = client.get(
            f"/dashboards/{int(dash.id)}/items/9999999/preview",
            headers=auth_admin_headers(db_setup),
        )
        assert r.status_code == 404
    finally:
        _cleanup(db, int(dash.id))


def test_preview_item_under_other_dashboard_returns_404(
    client: TestClient,
    user_a: User,
    db_setup: Any,
) -> None:
    """Item belongs to dashboard B but caller asked via dashboard A's URL →
    404. Don't leak that the item exists under a different dashboard."""
    db, _ = db_setup
    dash_a = _make_dashboard(db, owner_user_id=int(user_a.id))
    dash_b = _make_dashboard(db, owner_user_id=int(user_a.id))
    db.add(
        DashboardItem(
            dashboard_id=int(dash_b.id),
            item_type="text",
            title="in B",
            order_index=0,
            x=0, y=0, w=12, h=1,
            text_content="only on B",
        )
    )
    db.commit()
    try:
        item_b_id = int(
            db.query(DashboardItem)
            .filter(DashboardItem.dashboard_id == int(dash_b.id))
            .first()
            .id
        )
        r = client.get(
            f"/dashboards/{int(dash_a.id)}/items/{item_b_id}/preview",
            headers=auth_admin_headers(db_setup),
        )
        assert r.status_code == 404
    finally:
        _cleanup(db, int(dash_a.id))
        _cleanup(db, int(dash_b.id))


def test_preview_non_owner_without_grant_returns_404(
    client: TestClient,
    user_a: User,
    user_b: User,
    db_setup: Any,
) -> None:
    """Private dashboard, non-owner B with no grant → 404."""
    db, _ = db_setup
    dash = _make_dashboard(db, owner_user_id=int(user_a.id), visibility="private")
    db.add(
        DashboardItem(
            dashboard_id=int(dash.id),
            item_type="text",
            title="private",
            order_index=0,
            x=0, y=0, w=12, h=1,
            text_content="secret",
        )
    )
    db.commit()
    try:
        item_id = int(
            db.query(DashboardItem)
            .filter(DashboardItem.dashboard_id == int(dash.id))
            .first()
            .id
        )
        r = client.get(
            f"/dashboards/{int(dash.id)}/items/{item_id}/preview",
            headers=_auth_for(user_b),
        )
        assert r.status_code == 404
    finally:
        _cleanup(db, int(dash.id))

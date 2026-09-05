"""Tests for the D 双向 link reverse-listing on the Report side.

Covers :http:get:`/reports/{report_id}/dashboards` and the
DashboardItem-ref 409 that ``DELETE /reports/{report_id}`` now raises.

The ACL matrix mirrors :mod:`tests.test_report_acl`:

* Owner of a public report sees all referencing dashboards (deduplicated).
* Owner of a private report sees only the dashboards they themselves
  can reach.
* Non-owner with no grants sees nothing — the parent report 404s
  first, which is the uniform behaviour the rest of the API uses.
* ``DELETE`` returns 409 when any ``DashboardItem.report_id`` references
  the row; clean delete (204) when no references exist.
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
from app.models.data_source_access import DataSourceAccess
from app.models.report import Report
from app.models.report_access import ReportAccess
from app.models.report_job import ReportJob
from app.models.user import ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ----------------- fixtures -----------------


@pytest.fixture
def db_setup() -> Any:
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
        username=_unique("pytest_rl_user_a"),
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
        username=_unique("pytest_rl_user_b"),
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
    src = DataSource(
        name=_unique("pytest_rl_ds"),
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


def _grant_ds_read(db: Session, ds: DataSource, user: User) -> None:
    db.add(
        DataSourceAccess(
            data_source_id=int(ds.id),
            user_id=int(user.id),
            permission="read",
        )
    )
    db.commit()


def _make_report(
    db: Session, *, owner_user_id: int, ds_id: int, visibility: str = "private"
) -> Report:
    rep = Report(
        name=_unique("pytest_rl_rep"),
        data_source_id=ds_id,
        is_active=True,
        visibility=visibility,
        owner_user_id=owner_user_id,
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    return rep


def _make_dashboard(
    db: Session, *, owner_user_id: int | None, visibility: str = "public"
) -> Dashboard:
    dash = Dashboard(
        name=_unique("pytest_rl_dash"),
        owner_user_id=owner_user_id,
        visibility=visibility,
    )
    db.add(dash)
    db.commit()
    db.refresh(dash)
    return dash


def _make_report_item(
    db: Session, *, dashboard_id: int, report_id: int | None = None,
    data_source_id: int | None = None,
) -> DashboardItem:
    item = DashboardItem(
        dashboard_id=dashboard_id,
        item_type="report" if report_id is not None else "chart",
        report_id=report_id,
        data_source_id=data_source_id,
        x=0,
        y=0,
        order_index=0,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _cleanup(db: Session, *ids: int) -> None:
    """Best-effort teardown by id. Items first to dodge FK constraints."""
    if not ids:
        return
    db.query(DashboardItem).filter(DashboardItem.dashboard_id.in_(ids)).delete(
        synchronize_session=False
    )
    db.query(Dashboard).filter(Dashboard.id.in_(ids)).delete(
        synchronize_session=False
    )
    db.commit()


# ----------------- reverse listing -----------------


def test_get_dashboards_for_report_returns_referencing_dashboards(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    """Happy path: A's report is referenced by two dashboards → both come
    back, deduplicated."""
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))
    report = _make_report(db, owner_user_id=int(user_a.id), ds_id=int(ds.id))
    dash1 = _make_dashboard(db, owner_user_id=int(user_a.id))
    dash2 = _make_dashboard(db, owner_user_id=int(user_a.id))
    _make_report_item(db, dashboard_id=int(dash1.id), report_id=int(report.id))
    _make_report_item(db, dashboard_id=int(dash2.id), report_id=int(report.id))
    # Same dashboard referenced twice via two items — should collapse.
    _make_report_item(db, dashboard_id=int(dash1.id), report_id=int(report.id))

    response = client.get(
        f"/reports/{report.id}/dashboards", headers=_auth_for(user_a)
    )
    assert response.status_code == 200
    payload = response.json()
    assert {row["id"] for row in payload} == {int(dash1.id), int(dash2.id)}
    # Per-dashboard count includes both items on dash1.
    counts = {row["id"]: row["item_count"] for row in payload}
    assert counts[int(dash1.id)] == 2
    assert counts[int(dash2.id)] == 1

    _cleanup(db, int(dash1.id), int(dash2.id))
    db.query(ReportAccess).filter(ReportAccess.report_id == report.id).delete()
    db.query(ReportJob).filter(ReportJob.report_id == report.id).delete()
    db.query(Report).filter(Report.id == report.id).delete()
    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()


def test_get_dashboards_for_report_hides_dashboards_behind_ds_gate(
    client: TestClient, user_a: User, user_b: User, db_setup: Any
) -> None:
    """B can see the parent report (public + DS grant) but the
    referencing dashboard is A's private row. The dashboard's
    ``get_dashboard_for_user`` returns None for B → the row is
    omitted from B's listing, not surfaced as a leak."""
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))
    report = _make_report(
        db, owner_user_id=int(user_a.id), ds_id=int(ds.id), visibility="public"
    )
    # B needs DS read so the layered DS gate in ``get_report_for_user``
    # doesn't 404 the parent report before we even reach the
    # dashboard filter.
    _grant_ds_read(db, ds, user_b)
    dash = _make_dashboard(
        db, owner_user_id=int(user_a.id), visibility="private"
    )
    _make_report_item(db, dashboard_id=int(dash.id), report_id=int(report.id))

    response = client.get(
        f"/reports/{report.id}/dashboards", headers=_auth_for(user_b)
    )
    assert response.status_code == 200
    assert response.json() == []

    # And owner A still sees their own dashboard.
    owner_response = client.get(
        f"/reports/{report.id}/dashboards", headers=_auth_for(user_a)
    )
    assert owner_response.status_code == 200
    assert {row["id"] for row in owner_response.json()} == {int(dash.id)}

    _cleanup(db, int(dash.id))
    db.query(DataSourceAccess).filter(
        DataSourceAccess.data_source_id == int(ds.id)
    ).delete()
    db.query(ReportAccess).filter(ReportAccess.report_id == report.id).delete()
    db.query(ReportJob).filter(ReportJob.report_id == report.id).delete()
    db.query(Report).filter(Report.id == report.id).delete()
    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()


def test_get_dashboards_for_report_404_for_inaccessible_parent(
    client: TestClient, user_a: User, user_b: User, db_setup: Any
) -> None:
    """B has no access to A's private report → endpoint returns 404
    uniformly (same wall as :func:`get_report`)."""
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))
    report = _make_report(db, owner_user_id=int(user_a.id), ds_id=int(ds.id))

    response = client.get(
        f"/reports/{report.id}/dashboards", headers=_auth_for(user_b)
    )
    assert response.status_code == 404

    db.query(ReportAccess).filter(ReportAccess.report_id == report.id).delete()
    db.query(ReportJob).filter(ReportJob.report_id == report.id).delete()
    db.query(Report).filter(Report.id == report.id).delete()
    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()


# ----------------- DELETE 409 -----------------


def test_delete_report_with_dashboard_item_returns_409(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    """A dashboard item references the report → DELETE 409s with a
    detail naming the offending dashboard(s)."""
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))
    report = _make_report(db, owner_user_id=int(user_a.id), ds_id=int(ds.id))
    dash = _make_dashboard(db, owner_user_id=int(user_a.id))
    _make_report_item(db, dashboard_id=int(dash.id), report_id=int(report.id))

    response = client.delete(
        f"/reports/{report.id}", headers=_auth_for(user_a)
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "1 dashboard item" in detail
    assert str(dash.name) in detail

    _cleanup(db, int(dash.id))
    db.query(ReportAccess).filter(ReportAccess.report_id == report.id).delete()
    db.query(ReportJob).filter(ReportJob.report_id == report.id).delete()
    db.query(Report).filter(Report.id == report.id).delete()
    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()


def test_delete_report_without_dashboard_item_returns_204(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    """No references → DELETE proceeds normally. Regression guard
    against the new 409 branch blocking the ordinary happy path."""
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))
    report = _make_report(db, owner_user_id=int(user_a.id), ds_id=int(ds.id))

    response = client.delete(
        f"/reports/{report.id}", headers=_auth_for(user_a)
    )
    assert response.status_code == 204

    db.query(ReportAccess).filter(ReportAccess.report_id == report.id).delete()
    db.query(ReportJob).filter(ReportJob.report_id == report.id).delete()
    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()

"""Tests for the D 双向 link reverse-listing on the DataSource side.

Covers:

* :http:get:`/data-sources/{id}/reports`
* :http:get:`/data-sources/{id}/dashboards`
* The DashboardItem-ref 409 that ``DELETE /data-sources/{id}`` now
  raises (in addition to the existing Report-ref 409).

Both reverse endpoints run a child-ACL filter: reports through
``list_accessible_reports`` (visibility + DS gate), dashboards
through ``get_dashboard_for_user`` (owner / grant / visibility +
DS gate).
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
        username=_unique("pytest_ds_rl_user_a"),
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
        username=_unique("pytest_ds_rl_user_b"),
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


def _make_ds(db: Session, owner_user_id: int | None = None) -> DataSource:
    src = DataSource(
        name=_unique("pytest_ds_rl_ds"),
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
    db: Session,
    *,
    owner_user_id: int,
    ds_id: int,
    visibility: str = "private",
) -> Report:
    rep = Report(
        name=_unique("pytest_ds_rl_rep"),
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
        name=_unique("pytest_ds_rl_dash"),
        owner_user_id=owner_user_id,
        visibility=visibility,
    )
    db.add(dash)
    db.commit()
    db.refresh(dash)
    return dash


def _make_item(
    db: Session,
    *,
    dashboard_id: int,
    report_id: int | None = None,
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


def _purge_report(db: Session, *ids: int) -> None:
    for rid in ids:
        db.query(ReportAccess).filter(ReportAccess.report_id == rid).delete()
        db.query(ReportJob).filter(ReportJob.report_id == rid).delete()
        db.query(Report).filter(Report.id == rid).delete()
    db.commit()


def _purge_dash(db: Session, *ids: int) -> None:
    for did in ids:
        db.query(DashboardItem).filter(DashboardItem.dashboard_id == did).delete(
            synchronize_session=False
        )
        db.query(Dashboard).filter(Dashboard.id == did).delete(
            synchronize_session=False
        )
    db.commit()


# ----------------- /reports reverse listing -----------------


def test_get_reports_for_data_source_returns_referencing_reports(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    """Owner A sees all reports attached to the DS."""
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))
    rep1 = _make_report(db, owner_user_id=int(user_a.id), ds_id=int(ds.id))
    rep2 = _make_report(db, owner_user_id=int(user_a.id), ds_id=int(ds.id))

    response = client.get(
        f"/data-sources/{ds.id}/reports", headers=_auth_for(user_a)
    )
    assert response.status_code == 200
    payload = response.json()
    assert {row["id"] for row in payload} == {int(rep1.id), int(rep2.id)}
    for row in payload:
        assert row["visibility"] == "private"
        assert row["is_active"] is True

    _purge_report(db, int(rep1.id), int(rep2.id))
    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()


def test_get_reports_for_data_source_hides_private_reports_of_others(
    client: TestClient, user_a: User, user_b: User, db_setup: Any
) -> None:
    """B has DS access but A's reports are private → B's listing is empty
    even though the DS is shared."""
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))
    rep = _make_report(db, owner_user_id=int(user_a.id), ds_id=int(ds.id))
    _grant_ds_read(db, ds, user_b)

    response = client.get(
        f"/data-sources/{ds.id}/reports", headers=_auth_for(user_b)
    )
    assert response.status_code == 200
    assert response.json() == []

    db.query(DataSourceAccess).filter(
        DataSourceAccess.data_source_id == int(ds.id)
    ).delete()
    _purge_report(db, int(rep.id))
    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()


def test_get_reports_for_data_source_404_for_inaccessible_ds(
    client: TestClient, user_a: User, user_b: User, db_setup: Any
) -> None:
    """B has no DS grant → endpoint returns 404 uniformly with the rest
    of the DS API surface."""
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))

    response = client.get(
        f"/data-sources/{ds.id}/reports", headers=_auth_for(user_b)
    )
    assert response.status_code == 404

    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()


# ----------------- /dashboards reverse listing -----------------


def test_get_dashboards_for_data_source_returns_distinct_dashboards(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    """Three referencing items across two dashboards collapse to two
    dashboard rows; per-dashboard count is the number of items that
    touch this DS (direct chart items + transitive via report)."""
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))
    rep = _make_report(db, owner_user_id=int(user_a.id), ds_id=int(ds.id))
    dash1 = _make_dashboard(db, owner_user_id=int(user_a.id))
    dash2 = _make_dashboard(db, owner_user_id=int(user_a.id))
    # dash1: one chart item + one report item both touch DS
    _make_item(db, dashboard_id=int(dash1.id), data_source_id=int(ds.id))
    _make_item(db, dashboard_id=int(dash1.id), report_id=int(rep.id))
    # dash2: only chart item
    _make_item(db, dashboard_id=int(dash2.id), data_source_id=int(ds.id))

    response = client.get(
        f"/data-sources/{ds.id}/dashboards", headers=_auth_for(user_a)
    )
    assert response.status_code == 200
    payload = response.json()
    assert {row["id"] for row in payload} == {int(dash1.id), int(dash2.id)}
    counts = {row["id"]: row["item_count"] for row in payload}
    assert counts[int(dash1.id)] == 2
    assert counts[int(dash2.id)] == 1

    _purge_dash(db, int(dash1.id), int(dash2.id))
    _purge_report(db, int(rep.id))
    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()


def test_get_dashboards_for_data_source_hides_dashboards_behind_dashboard_acl(
    client: TestClient, user_a: User, user_b: User, db_setup: Any
) -> None:
    """B has DS access (so the parent DS isn't 404) but the dashboard
    is A's private row. The dashboard's ``get_dashboard_for_user``
    returns None for B → silently omitted."""
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))
    dash = _make_dashboard(
        db, owner_user_id=int(user_a.id), visibility="private"
    )
    _make_item(db, dashboard_id=int(dash.id), data_source_id=int(ds.id))
    _grant_ds_read(db, ds, user_b)

    response = client.get(
        f"/data-sources/{ds.id}/dashboards", headers=_auth_for(user_b)
    )
    assert response.status_code == 200
    assert response.json() == []

    owner_response = client.get(
        f"/data-sources/{ds.id}/dashboards", headers=_auth_for(user_a)
    )
    assert owner_response.status_code == 200
    assert {row["id"] for row in owner_response.json()} == {int(dash.id)}

    _purge_dash(db, int(dash.id))
    db.query(DataSourceAccess).filter(
        DataSourceAccess.data_source_id == int(ds.id)
    ).delete()
    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()


# ----------------- DELETE 409 -----------------


def test_delete_data_source_with_dashboard_item_returns_409(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    """A dashboard item references the DS → DELETE 409s with a
    detail naming the offending item(s). Mirrors the existing
    ``test_delete_data_source_with_reports_returns_409``.
    """
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))
    dash = _make_dashboard(db, owner_user_id=int(user_a.id))
    _make_item(db, dashboard_id=int(dash.id), data_source_id=int(ds.id))

    response = client.delete(
        f"/data-sources/{ds.id}", headers=_auth_for(user_a)
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "1 dashboard item" in detail
    assert str(dash.name) in detail

    _purge_dash(db, int(dash.id))
    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()


def test_delete_data_source_with_dashboard_item_fires_after_report_check(
    client: TestClient, user_a: User, db_setup: Any
) -> None:
    """Existing Report-ref guard still fires first when both kinds of
    references exist — that was the only 409 before D, and we don't
    want to silently change the message operators are used to seeing."""
    db, _ = db_setup
    ds = _make_ds(db, int(user_a.id))
    rep = _make_report(db, owner_user_id=int(user_a.id), ds_id=int(ds.id))
    dash = _make_dashboard(db, owner_user_id=int(user_a.id))
    _make_item(db, dashboard_id=int(dash.id), data_source_id=int(ds.id))

    response = client.delete(
        f"/data-sources/{ds.id}", headers=_auth_for(user_a)
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    # The first guard wins — operators still see the report-side
    # detail they were trained on.
    assert "report(s)" in detail
    assert "dashboard item" not in detail

    _purge_dash(db, int(dash.id))
    _purge_report(db, int(rep.id))
    db.query(DataSource).filter(DataSource.id == ds.id).delete()
    db.commit()

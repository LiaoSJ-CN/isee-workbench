"""Tests for batch ``user-management`` Stage 2 — centralised grants +
ACL aggregation.

Covers:

- ``GET    /admin/users/{id}/grants`` — aggregates the user's grants
  across DataSource + Report + Dashboard.
- ``GET    /admin/grants?resource_type=&resource_id=`` — list grants
  on one resource.
- ``POST   /admin/grants`` — admin grants on behalf of any user
  (idempotent on ``(resource_type, resource_id, target_user_id)``).
- ``DELETE /admin/grants/{resource_type}/{grant_id}`` — admin revokes
  by underlying access-row id.
- ``q`` param on ``GET /data-sources`` / ``GET /reports`` for the
  resource-picker search in the centralised grant modal.

Uses the session-tmpfile isolated DB so tests run against a fresh
slate and never touch ``backend/app.db``.
"""

from __future__ import annotations

import uuid
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.dashboard import Dashboard
from app.models.data_source import DataSource
from app.models.report import Report
from app.models.user import ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token


def _unique(prefix: str = "admin_grants") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def target_user() -> Iterator[User]:
    """A non-admin user that the admin will grant / revoke to.

    Owned by this test (cleanup wipes the row by username).
    """
    db: Session = SessionLocal()
    username = _unique("target_viewer")
    user = User(username=username, password_hash="placeholder", role=ROLE_VIEWER)
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
def fresh_ds() -> Iterator[DataSource]:
    """A standalone DataSource row owned by admin (so grants can hang
    off it). Cleaned up after the test.
    """
    db = SessionLocal()
    name = _unique("ds")
    ds = DataSource(
        name=name,
        db_type="sqlite",
        host="placeholder",
        port=1,
        database="placeholder",
        username="placeholder",
        owner_user_id=_admin_id(db),
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    try:
        yield ds
    finally:
        db.delete(ds)
        db.commit()
        db.close()


@pytest.fixture
def fresh_report() -> Iterator[Report]:
    """A standalone Report row owned by admin. The DS dependency is
    satisfied via a paired ``fresh_ds`` in the same test, so we just
    need any existing data_source_id — using ``fresh_ds.id`` requires
    the test to depend on both fixtures.
    """
    db = SessionLocal()
    # Pick the demo DS seeded by conftest — guaranteed to exist.
    from app.models.data_source import DataSource as DsModel

    ds = db.query(DsModel).first()
    assert ds is not None, "demo DS must be seeded by conftest"
    name = _unique("report")
    report = Report(
        name=name,
        description="admin_grants test fixture",
        data_source_id=ds.id,
        owner_user_id=_admin_id(db),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    try:
        yield report
    finally:
        db.delete(report)
        db.commit()
        db.close()


@pytest.fixture
def fresh_dashboard() -> Iterator[Dashboard]:
    """A standalone Dashboard row owned by admin."""
    db = SessionLocal()
    name = _unique("dashboard")
    dashboard = Dashboard(
        name=name,
        description="admin_grants test fixture",
        owner_user_id=_admin_id(db),
    )
    db.add(dashboard)
    db.commit()
    db.refresh(dashboard)
    try:
        yield dashboard
    finally:
        db.delete(dashboard)
        db.commit()
        db.close()


def _admin_id(db: Session) -> int:
    """Resolve the bootstrap admin's id so fixtures can set owner_user_id."""
    admin = db.query(User).filter(User.username == settings.admin_username).first()
    assert admin is not None, "admin user must be seeded by conftest"
    return int(admin.id)


@pytest.fixture
def non_admin_auth_headers() -> Iterator[dict[str, str]]:
    """Bearer token for a non-admin user — admin routes must 403."""
    db = SessionLocal()
    username = _unique("viewer_h")
    user = User(username=username, password_hash="placeholder", role=ROLE_VIEWER)
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


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Bearer token for the bootstrap admin user."""
    return {"Authorization": f"Bearer {create_access_token(settings.admin_username)}"}


# ---------------------------------------------------------------------------
# Auth gates
# ---------------------------------------------------------------------------


def test_grants_list_requires_auth(
    client: TestClient, fresh_ds: DataSource
) -> None:
    response = client.get(
        "/admin/grants",
        params={"resource_type": "data_source", "resource_id": fresh_ds.id},
    )
    assert response.status_code == 401


def test_grants_post_requires_auth(
    client: TestClient, fresh_ds: DataSource, target_user: User
) -> None:
    response = client.post(
        "/admin/grants",
        json={
            "resource_type": "data_source",
            "resource_id": fresh_ds.id,
            "target_user_id": target_user.id,
            "permission": "read",
        },
    )
    assert response.status_code == 401


def test_grants_delete_requires_auth(
    client: TestClient,
) -> None:
    response = client.delete("/admin/grants/data_source/999999")
    assert response.status_code == 401


def test_grants_non_admin_forbidden(
    client: TestClient,
    non_admin_auth_headers: dict[str, str],
    fresh_ds: DataSource,
    target_user: User,
) -> None:
    response = client.post(
        "/admin/grants",
        headers=non_admin_auth_headers,
        json={
            "resource_type": "data_source",
            "resource_id": fresh_ds.id,
            "target_user_id": target_user.id,
            "permission": "read",
        },
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /admin/grants — centralised grant across all three resource types
# ---------------------------------------------------------------------------


def test_centralized_grant_data_source(
    client: TestClient,
    auth_headers: dict[str, str],
    fresh_ds: DataSource,
    target_user: User,
) -> None:
    """Admin grants *target_user* read on a DS — returns the summary
    and writes a ``data_source.grant`` audit row.
    """
    response = client.post(
        "/admin/grants",
        headers=auth_headers,
        json={
            "resource_type": "data_source",
            "resource_id": fresh_ds.id,
            "target_user_id": target_user.id,
            "permission": "read",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["resource_type"] == "data_source"
    assert body["resource_id"] == fresh_ds.id
    assert body["resource_name"] == fresh_ds.name
    assert body["permission"] == "read"
    assert body["grant_id"] >= 1
    assert body["granted_by_username"] == settings.admin_username

    # Audit row written under the per-resource action.
    db = SessionLocal()
    try:
        from app.models.audit_log import AuditLog

        rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "data_source.grant",
                AuditLog.target_id == body["grant_id"],
            )
            .all()
        )
        assert len(rows) == 1
        assert rows[0].actor_user_id == _admin_id(db)
    finally:
        db.close()


def test_centralized_grant_report(
    client: TestClient,
    auth_headers: dict[str, str],
    fresh_report: Report,
    target_user: User,
) -> None:
    response = client.post(
        "/admin/grants",
        headers=auth_headers,
        json={
            "resource_type": "report",
            "resource_id": fresh_report.id,
            "target_user_id": target_user.id,
            "permission": "write",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["resource_type"] == "report"
    assert body["resource_id"] == fresh_report.id
    assert body["permission"] == "write"


def test_centralized_grant_dashboard(
    client: TestClient,
    auth_headers: dict[str, str],
    fresh_dashboard: Dashboard,
    target_user: User,
) -> None:
    response = client.post(
        "/admin/grants",
        headers=auth_headers,
        json={
            "resource_type": "dashboard",
            "resource_id": fresh_dashboard.id,
            "target_user_id": target_user.id,
            "permission": "read",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["resource_type"] == "dashboard"
    assert body["resource_id"] == fresh_dashboard.id


def test_centralized_grant_is_idempotent(
    client: TestClient,
    auth_headers: dict[str, str],
    fresh_ds: DataSource,
    target_user: User,
) -> None:
    """Re-POSTing the same payload upgrades the permission level
    rather than failing the unique constraint.
    """
    payload = {
        "resource_type": "data_source",
        "resource_id": fresh_ds.id,
        "target_user_id": target_user.id,
        "permission": "read",
    }
    first = client.post("/admin/grants", headers=auth_headers, json=payload)
    assert first.status_code == 201
    second = client.post(
        "/admin/grants",
        headers=auth_headers,
        json={**payload, "permission": "write"},
    )
    assert second.status_code == 201
    # Same grant_id (idempotent upsert), upgraded permission.
    assert first.json()["grant_id"] == second.json()["grant_id"]
    assert second.json()["permission"] == "write"


def test_centralized_grant_missing_resource_404(
    client: TestClient,
    auth_headers: dict[str, str],
    target_user: User,
) -> None:
    response = client.post(
        "/admin/grants",
        headers=auth_headers,
        json={
            "resource_type": "data_source",
            "resource_id": 999999,
            "target_user_id": target_user.id,
            "permission": "read",
        },
    )
    assert response.status_code == 404
    assert "DataSource" in response.json()["detail"]


def test_centralized_grant_missing_user_404(
    client: TestClient,
    auth_headers: dict[str, str],
    fresh_ds: DataSource,
) -> None:
    response = client.post(
        "/admin/grants",
        headers=auth_headers,
        json={
            "resource_type": "data_source",
            "resource_id": fresh_ds.id,
            "target_user_id": 999999,
            "permission": "read",
        },
    )
    assert response.status_code == 404
    assert "User" in response.json()["detail"]


def test_centralized_grant_invalid_permission_422(
    client: TestClient,
    auth_headers: dict[str, str],
    fresh_ds: DataSource,
    target_user: User,
) -> None:
    response = client.post(
        "/admin/grants",
        headers=auth_headers,
        json={
            "resource_type": "data_source",
            "resource_id": fresh_ds.id,
            "target_user_id": target_user.id,
            "permission": "admin",  # not in {read, write}
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /admin/grants/{resource_type}/{grant_id}
# ---------------------------------------------------------------------------


def test_centralized_revoke_data_source(
    client: TestClient,
    auth_headers: dict[str, str],
    fresh_ds: DataSource,
    target_user: User,
) -> None:
    """Grant then revoke — both audited, the underlying row gone."""
    create = client.post(
        "/admin/grants",
        headers=auth_headers,
        json={
            "resource_type": "data_source",
            "resource_id": fresh_ds.id,
            "target_user_id": target_user.id,
            "permission": "read",
        },
    )
    grant_id = create.json()["grant_id"]

    revoke = client.delete(
        f"/admin/grants/data_source/{grant_id}",
        headers=auth_headers,
    )
    assert revoke.status_code == 204
    assert revoke.content == b""

    # Re-listing the same resource shows zero grants for this user.
    listing = client.get(
        "/admin/grants",
        headers=auth_headers,
        params={"resource_type": "data_source", "resource_id": fresh_ds.id},
    )
    assert listing.status_code == 200
    assert listing.json() == []

    # Revoke audit row written.
    db = SessionLocal()
    try:
        from app.models.audit_log import AuditLog

        rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "data_source.revoke",
                AuditLog.target_id == grant_id,
            )
            .all()
        )
        assert len(rows) == 1
        assert rows[0].before is not None
        assert rows[0].after is None
    finally:
        db.close()


def test_centralized_revoke_missing_grant_404(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.delete(
        "/admin/grants/data_source/999999",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_centralized_revoke_invalid_resource_type_404(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.delete(
        "/admin/grants/not_a_resource/1",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_centralized_revoke_cross_type_lookup_404(
    client: TestClient,
    auth_headers: dict[str, str],
    fresh_ds: DataSource,
    target_user: User,
) -> None:
    """A DS grant (id=X) is not visible under ``/admin/grants/report/X``
    even if the id collides — the resource_type path segment scopes
    the lookup to the matching access table.
    """
    create = client.post(
        "/admin/grants",
        headers=auth_headers,
        json={
            "resource_type": "data_source",
            "resource_id": fresh_ds.id,
            "target_user_id": target_user.id,
            "permission": "read",
        },
    )
    grant_id = create.json()["grant_id"]

    response = client.delete(
        f"/admin/grants/report/{grant_id}",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /admin/users/{id}/grants — ACL aggregation
# ---------------------------------------------------------------------------


def test_list_user_grants_aggregates_all_three_types(
    client: TestClient,
    auth_headers: dict[str, str],
    target_user: User,
    fresh_ds: DataSource,
    fresh_report: Report,
    fresh_dashboard: Dashboard,
) -> None:
    """Grant the user access across all three resource types — the
    aggregate endpoint returns three summary items, ordered by
    resource_type (data_source, report, dashboard).
    """
    for resource_type, resource_id in (
        ("data_source", fresh_ds.id),
        ("report", fresh_report.id),
        ("dashboard", fresh_dashboard.id),
    ):
        response = client.post(
            "/admin/grants",
            headers=auth_headers,
            json={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "target_user_id": target_user.id,
                "permission": "read",
            },
        )
        assert response.status_code == 201, response.text

    response = client.get(
        f"/admin/users/{target_user.id}/grants",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["subject_type"] == "user"
    assert body["subject_id"] == target_user.id
    assert len(body["grants"]) == 3
    types = [g["resource_type"] for g in body["grants"]]
    assert types == ["data_source", "report", "dashboard"]
    # Each item carries the parent resource's name and the grantor's username.
    for grant in body["grants"]:
        assert grant["resource_name"]
        assert grant["granted_by_username"] == settings.admin_username


def test_list_user_grants_empty_when_no_grants(
    client: TestClient,
    auth_headers: dict[str, str],
    target_user: User,
) -> None:
    response = client.get(
        f"/admin/users/{target_user.id}/grants",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["grants"] == []


def test_list_user_grants_missing_user_404(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.get("/admin/users/999999/grants", headers=auth_headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /admin/grants?resource_type=&resource_id= — per-resource listing
# ---------------------------------------------------------------------------


def test_list_resource_grants_filters_by_resource(
    client: TestClient,
    auth_headers: dict[str, str],
    fresh_ds: DataSource,
    fresh_report: Report,
    target_user: User,
) -> None:
    """Listing grants on the DS returns only the DS grant, not the
    Report grant (cross-resource isolation).
    """
    client.post(
        "/admin/grants",
        headers=auth_headers,
        json={
            "resource_type": "data_source",
            "resource_id": fresh_ds.id,
            "target_user_id": target_user.id,
            "permission": "read",
        },
    )
    client.post(
        "/admin/grants",
        headers=auth_headers,
        json={
            "resource_type": "report",
            "resource_id": fresh_report.id,
            "target_user_id": target_user.id,
            "permission": "read",
        },
    )

    response = client.get(
        "/admin/grants",
        headers=auth_headers,
        params={"resource_type": "data_source", "resource_id": fresh_ds.id},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["resource_type"] == "data_source"
    assert body[0]["resource_id"] == fresh_ds.id


def test_list_resource_grants_invalid_type_422(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.get(
        "/admin/grants",
        headers=auth_headers,
        params={"resource_type": "not_a_resource", "resource_id": 1},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# q param on /data-sources and /reports list endpoints
# ---------------------------------------------------------------------------


def test_list_data_sources_q_filters_by_name_substring(
    client: TestClient,
    auth_headers: dict[str, str],
    fresh_ds: DataSource,
) -> None:
    """A substring of the freshly-created DS name returns it; a
    non-matching query returns no items.
    """
    response = client.get(
        f"/data-sources?q={fresh_ds.name[4:12]}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    names = [s["name"] for s in response.json()]
    assert fresh_ds.name in names

    response = client.get(
        "/data-sources?q=zzz_no_match_zzz",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json() == []


def test_list_reports_q_filters_by_name_substring(
    client: TestClient,
    auth_headers: dict[str, str],
    fresh_report: Report,
) -> None:
    response = client.get(
        f"/reports?q={fresh_report.name[4:12]}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    names = [r["name"] for r in response.json()]
    assert fresh_report.name in names

    response = client.get(
        "/reports?q=zzz_no_match_zzz",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json() == []


def test_list_q_does_not_leak_unauthorized_resources(
    client: TestClient,
    fresh_ds: DataSource,
) -> None:
    """Without auth the q param must not reveal a row's existence —
    the unauthenticated branch short-circuits before q is applied.
    """
    response = client.get(f"/data-sources?q={fresh_ds.name}")
    assert response.status_code == 401

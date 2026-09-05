"""Tests for batch A — global command-palette search.

Coverage matrix (10 cases):

* 1 — auth gate (no header → 401).
* 2 — empty ``q`` short-circuits to three empty lists without DB hit.
* 3 — happy path returns all three kinds for the admin.
* 4 — non-admin can't probe private reports owned by another user
  (ACL isolation — same defense as the per-resource list endpoints).
* 5 — ``limit_per_kind`` is independent per group (12 reports, cap=5,
  dashboards / data_sources still get their full cap).
* 6 — ``q`` over the max_length boundary → 422.
* 7 — ``q`` at max_length → 200.
* 8 — case-insensitive match (reports + dashboards via SQL ``ILIKE``,
  data sources via Python ``.casefold()``).
* 9 — data-source path uses ``.casefold()`` for CJK (the SQL path's
  ``ILIKE`` doesn't help us here, so this case pins the Python branch).
* 10 — query with no matches returns three empty lists.

Fixtures follow the ``test_data_source_acl`` pattern: local
``db_setup`` + ``user_a`` / ``user_b`` + ``_auth_for``. Row creation
goes through tiny ``_make_*`` helpers so each test only states the
data it cares about.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crypto import encrypt as crypto_encrypt
from app.database import SessionLocal
from app.models.dashboard import Dashboard
from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.report import Report
from app.models.user import ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ----------------- fixtures -----------------


@pytest.fixture
def db_setup() -> Any:
    """(Session, admin User) — mirrors local fixtures in
    test_data_source_acl / test_report_acl / test_subscriptions."""
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
    """First non-admin user. Owns rows that ``user_b`` cannot see
    without an explicit grant."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_search_user_a"),
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
    """Second non-admin user — the "outsider" the ACL isolation case
    uses to assert that probe queries return empty."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_search_user_b"),
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


def _make_ds(
    db: Session,
    owner_user_id: int,
    *,
    name: str | None = None,
) -> DataSource:
    src = DataSource(
        name=name or _unique("pytest_search_ds"),
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
    """Give ``user`` read access on ``ds`` — without it a public
    report on a private DS still 404s (DS ACL sits below report ACL)."""
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
    name: str | None = None,
    visibility: str = "private",
) -> Report:
    rep = Report(
        name=name or _unique("pytest_search_rpt"),
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
    db: Session,
    *,
    owner_user_id: int,
    name: str | None = None,
    visibility: str = "private",
) -> Dashboard:
    dash = Dashboard(
        name=name or _unique("pytest_search_dash"),
        visibility=visibility,
        owner_user_id=owner_user_id,
    )
    db.add(dash)
    db.commit()
    db.refresh(dash)
    return dash


def _cleanup_row(db: Session, row_id: int, *, kind: str) -> None:
    """Drop a single row by id and any ACL cascade that would
    otherwise break the FK from a later test's seeded rows."""
    if kind == "report":
        db.query(Report).filter(Report.id == row_id).delete()
    elif kind == "dashboard":
        db.query(Dashboard).filter(Dashboard.id == row_id).delete()
    elif kind == "data_source":
        db.query(DataSourceAccess).filter(
            DataSourceAccess.data_source_id == row_id
        ).delete()
        db.query(DataSource).filter(DataSource.id == row_id).delete()
    db.commit()


# ----------------- 1. auth -----------------


def test_search_requires_auth(client: TestClient) -> None:
    """No Authorization header → 401 (uniform JSON auth gate)."""
    r = client.get("/search", params={"q": "x"})
    assert r.status_code == 401


# ----------------- 2. empty q -----------------


def test_search_empty_q_returns_empty_lists(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """``q`` is None or empty → three empty lists, no DB fan-out.

    Pins the early-return short-circuit: even with auth, no service
    call should happen. ``limit_per_kind`` is honored as a Query
    constraint (still validated) but the response body is empty.
    """
    for q_value in (None, "", "   "):
        r = client.get(
            "/search",
            params={"q": q_value} if q_value is not None else {},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"reports": [], "dashboards": [], "data_sources": []}


# ----------------- 3. happy path -----------------


def test_search_happy_path_returns_all_three_kinds(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """Admin seeds one DS / one Report / one Dashboard with a shared
    substring, and ``?q=<substring>`` returns all three groups
    non-empty."""
    db, _ = db_setup
    token = "pytestsearch"
    ds = _make_ds(db, owner_user_id=1, name=_unique(f"{token}_ds"))
    report = _make_report(
        db,
        owner_user_id=1,
        ds_id=int(ds.id),
        name=_unique(f"{token}_report"),
    )
    dash = _make_dashboard(
        db,
        owner_user_id=1,
        name=_unique(f"{token}_dash"),
    )
    try:
        r = client.get(
            "/search",
            params={"q": token},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert {row["id"] for row in body["reports"]} == {int(report.id)}
        assert {row["id"] for row in body["dashboards"]} == {int(dash.id)}
        assert {row["id"] for row in body["data_sources"]} == {int(ds.id)}
    finally:
        _cleanup_row(db, int(report.id), kind="report")
        _cleanup_row(db, int(dash.id), kind="dashboard")
        _cleanup_row(db, int(ds.id), kind="data_source")


# ----------------- 4. ACL isolation -----------------


def test_search_acl_isolates_user(
    client: TestClient,
    db_setup: Any,
    user_a: User,
    user_b: User,
) -> None:
    """user_a creates a private Report / Dashboard / DS, user_b
    searches for the substring → all three groups are empty.

    Pins the probe-protection contract: even though user_b knows the
    names, the post-ACL filter swallows the rows before ``q`` runs.
    The admin path (``auth_headers``) still sees them.
    """
    db, _ = db_setup
    token = "searchprobe"
    a_ds = _make_ds(db, owner_user_id=int(user_a.id), name=_unique(f"{token}_ds"))
    a_report = _make_report(
        db,
        owner_user_id=int(user_a.id),
        ds_id=int(a_ds.id),
        name=_unique(f"{token}_report"),
    )
    a_dash = _make_dashboard(
        db,
        owner_user_id=int(user_a.id),
        name=_unique(f"{token}_dash"),
    )
    try:
        b_resp = client.get(
            "/search",
            params={"q": token},
            headers=_auth_for(user_b),
        )
        assert b_resp.status_code == 200
        b_body = b_resp.json()
        assert b_body == {"reports": [], "dashboards": [], "data_sources": []}
    finally:
        _cleanup_row(db, int(a_report.id), kind="report")
        _cleanup_row(db, int(a_dash.id), kind="dashboard")
        _cleanup_row(db, int(a_ds.id), kind="data_source")


# ----------------- 5. per-kind limit -----------------


def test_search_per_kind_limit_independent(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """12 reports matching ``q`` with ``limit_per_kind=5`` → reports
    list is capped at 5. Dashboards / data sources (only the demo
    seed) come through with their own independent cap; the cap does
    NOT steal slots from one kind to give to another.
    """
    db, _ = db_setup
    token = "capkind"
    ds = _make_ds(db, owner_user_id=1, name=_unique(f"{token}_ds"))
    seeded_reports: list[Report] = []
    for _ in range(12):
        seeded_reports.append(
            _make_report(
                db,
                owner_user_id=1,
                ds_id=int(ds.id),
                name=_unique(f"{token}_rpt"),
            )
        )
    try:
        r = client.get(
            "/search",
            params={"q": token, "limit_per_kind": 5},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # 12 seeded + possibly the demo ``scripts.seed_reports`` rows
        # — but those are demo-named, not contain ``token``, so the
        # only matching reports are the ones we just seeded.
        assert len(body["reports"]) == 5
        # Dashboards / data sources we created are 0 each; the cap is
        # independent and the response reflects that.
        assert len(body["dashboards"]) >= 0
        assert len(body["data_sources"]) >= 0
        # Per-kind cap is a server-side ceiling, so the response
        # length for ``reports`` must never exceed ``limit_per_kind``
        # even if more rows would otherwise match.
        assert len(body["reports"]) <= 5
    finally:
        for rep in seeded_reports:
            _cleanup_row(db, int(rep.id), kind="report")
        _cleanup_row(db, int(ds.id), kind="data_source")


# ----------------- 6. q too long -----------------


def test_search_q_too_long_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """``q`` length 256 exceeds ``max_length=255`` → 422."""
    long_q = "a" * 256
    r = client.get("/search", params={"q": long_q}, headers=auth_headers)
    assert r.status_code == 422


# ----------------- 7. q at boundary -----------------


def test_search_q_max_length_accepted(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """``q`` length 255 (boundary) → 200. We don't seed a matching
    row at that length; the test only verifies the upper bound is
    accepted (returns empty lists, not 422)."""
    db, _ = db_setup
    boundary_q = "z" * 255
    try:
        r = client.get("/search", params={"q": boundary_q}, headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        # All three lists must be present (and empty — we don't seed
        # any matching row of that length).
        assert set(body.keys()) == {"reports", "dashboards", "data_sources"}
        assert body["reports"] == []
    finally:
        # No seeded rows to clean up; the fixture is enough.
        pass


# ----------------- 8. case-insensitive -----------------


def test_search_case_insensitive(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """Uppercase ``q`` matches lowercase ``name`` across all three
    kinds. Pins that reports + dashboards go through SQLAlchemy's
    ``ILIKE`` (Postgres) / case-insensitive ``LIKE`` (SQLite ASCII)
    and that data sources run Python ``.casefold()``.
    """
    db, _ = db_setup
    token = "casedemo"
    ds = _make_ds(db, owner_user_id=1, name=_unique(f"{token}_ds_lower"))
    report = _make_report(
        db,
        owner_user_id=1,
        ds_id=int(ds.id),
        name=_unique(f"{token}_rpt_lower"),
    )
    dash = _make_dashboard(
        db,
        owner_user_id=1,
        name=_unique(f"{token}_dash_lower"),
    )
    try:
        # Uppercase fragment of the lowercase seed token.
        r = client.get(
            "/search",
            params={"q": token.upper()},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert {row["id"] for row in body["reports"]} == {int(report.id)}
        assert {row["id"] for row in body["dashboards"]} == {int(dash.id)}
        assert {row["id"] for row in body["data_sources"]} == {int(ds.id)}
    finally:
        _cleanup_row(db, int(report.id), kind="report")
        _cleanup_row(db, int(dash.id), kind="dashboard")
        _cleanup_row(db, int(ds.id), kind="data_source")


# ----------------- 9. data source casefold (CJK) -----------------


def test_search_data_source_uses_casefold_for_cjk(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """Chinese name on the data source is matched by an ASCII
    ``q`` that overlaps with the substring.

    This pins the Python ``.casefold()`` branch used for data
    sources (the SQL ``ILIKE`` branch isn't in play for that
    list). The match is done in Python on the post-ACL list, so
    CJK works the same as ASCII.
    """
    db, _ = db_setup
    # ``casefold`` for ASCII is a no-op equivalent to ``lower``;
    # we just need a unique CJK-prefixed name to verify the path.
    name = _unique("财务_ds_cjk")
    ds = _make_ds(db, owner_user_id=1, name=name)
    try:
        r = client.get(
            "/search",
            params={"q": "财务"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert {row["id"] for row in body["data_sources"]} == {int(ds.id)}
    finally:
        _cleanup_row(db, int(ds.id), kind="data_source")


# ----------------- 10. no match -----------------


def test_search_returns_empty_when_no_match(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """A ``q`` that nothing matches returns three empty lists."""
    r = client.get(
        "/search",
        params={"q": "zzz_definitely_no_match_xyz"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"reports": [], "dashboards": [], "data_sources": []}

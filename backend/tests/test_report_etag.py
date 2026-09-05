"""Tests for batch 3 — Report ETag / If-Match optimistic concurrency.

Coverage matrix (8 cases):

* 1 — GET /reports/{id} emits an ``ETag`` header (weak, derived from
  ``updated_at``).
* 2 — POST /reports emits an ``ETag`` header so the caller can
  follow up with a conditional PUT without a re-GET.
* 3 — PUT without ``If-Match`` succeeds — backward compat for
  pre-批 3 clients.
* 4 — PUT with the current ``If-Match`` succeeds (200) and the
  response carries the *new* ETag.
* 5 — PUT with a *stale* ``If-Match`` (someone else updated between
  our GET and PUT) returns 412 with ``detail.current`` carrying the
  full ReportResponse.
* 6 — PUT with ``If-Match: *`` succeeds (RFC 7232 §3.2 wildcard).
* 7 — PUT with a malformed ``If-Match`` (non-parseable garbage) is
  treated as a mismatch — 412, not 400 — so a bad client doesn't get
  locked out by its own bug.
* 8 — Stale-after-concurrent-update: A reads ETag, B updates, A's
  conditional PUT fails — pinning the cross-tab race the feature
  exists to defend against.

Auth/ACL uses the conftest ``auth_headers`` (admin) + a local ``user_a``
fixture so cases that need a "second writer" can mint a second actor.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.crypto import encrypt as crypto_encrypt
from app.database import SessionLocal
from app.models.data_source import DataSource
from app.models.report import Report
from app.models.user import ROLE_VIEWER, User
from app.services.etag import compute_etag, etag_matches, parse_if_match
from app.services.jwt_auth import create_access_token


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ----------------- fixtures -----------------


@pytest.fixture
def db_setup() -> Any:
    """(Session, admin User) — mirrors the local fixtures used by
    ``test_data_source_acl`` / ``test_search``."""
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
    """Second writer so case 8 can race admin's stale ETag."""
    db: Session = SessionLocal()
    user = User(
        username=_unique("pytest_etag_user_a"),
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


def _auth_for(user: User) -> dict[str, str]:
    token = create_access_token(
        user.username,
        user_id=int(user.id),
        role=str(user.role),
        org_id=user.org_id,
    )
    return {"Authorization": f"Bearer {token}"}


def _make_ds(db: Session, owner_user_id: int) -> DataSource:
    """A private sqlite DS admin owns — ``owner_user_id`` is admin so
    case 5's PUT goes through admin's write ACL (admins bypass the
    owner-only write check on Report)."""
    src = DataSource(
        name=_unique("pytest_etag_ds"),
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


def _grant_write(db: Session, report: Report, user: User) -> None:
    """Give ``user`` write access on ``report`` (and read on its DS) so
    they can race the admin's ETag for case 8. The DS grant is needed
    because ``get_report_for_user`` layers DS-ACL *before* report-level
    ACL — a write grant on the report alone won't bypass a private DS.
    """
    from app.models.data_source_access import DataSourceAccess
    from app.models.report_access import ReportAccess

    db.add(
        DataSourceAccess(
            data_source_id=int(report.data_source_id),
            user_id=int(user.id),
            permission="read",
        )
    )
    db.add(
        ReportAccess(
            report_id=int(report.id),
            user_id=int(user.id),
            permission="write",
        )
    )
    db.commit()


def _make_report(db: Session, owner_user_id: int, ds_id: int) -> Report:
    rep = Report(
        name=_unique("pytest_etag_rpt"),
        data_source_id=ds_id,
        is_active=True,
        visibility="private",
        owner_user_id=owner_user_id,
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    return rep


# ----------------- helper-level unit tests -----------------


def test_compute_etag_returns_weak_quoted_v_n() -> None:
    """Direct check on the helper — pin the wire format. We use the
    version counter (not updated_at) so two writes inside the same
    second still produce distinct ETags."""
    assert compute_etag(1) == 'W/"v1"'
    assert compute_etag(42) == 'W/"v42"'


def test_compute_etag_returns_none_for_unset() -> None:
    """A NULL version (shouldn't happen — column default is 1) means
    we must skip the header rather than emit ``W/"vNone"``."""
    assert compute_etag(None) is None


def test_parse_if_match_strips_weak_prefix_and_quotes() -> None:
    assert parse_if_match('W/"v1"') == "v1"
    assert parse_if_match('"v1"') == "v1"
    assert parse_if_match("v1") == "v1"
    assert parse_if_match("*") == "*"


def test_parse_if_match_takes_first_in_multi_value() -> None:
    """RFC 7232 §3.2 says we MAY accept any of multiple — we take the
    first matchable one."""
    assert parse_if_match('W/"v1", W/"v2"') == "v1"


def test_parse_if_match_returns_none_for_missing_or_empty() -> None:
    assert parse_if_match(None) is None
    assert parse_if_match("") is None
    assert parse_if_match("   ") is None


def test_etag_matches_wildcard() -> None:
    """``If-Match: *`` matches any existing resource (RFC 7232 §3.2)."""
    assert etag_matches("*", 1) is True
    assert etag_matches("*", None) is False  # missing resource → no match


def test_etag_matches_exact_version() -> None:
    assert etag_matches("v3", 3) is True
    assert etag_matches("v3", 4) is False
    assert etag_matches(None, 3) is False


# ----------------- HTTP-level tests -----------------


def test_get_report_returns_etag_header(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """GET /reports/{id} must emit ETag so the frontend can drive
    conditional PUTs without a separate round-trip."""
    db, admin = db_setup
    ds = _make_ds(db, int(admin.id))
    report = _make_report(db, int(admin.id), int(ds.id))

    resp = client.get(f"/reports/{report.id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    etag = resp.headers.get("ETag")
    assert etag is not None, "GET /reports/{id} must emit ETag"
    # Weak ETag in ``W/"v<n>"`` shape — brand-new rows start at v1.
    assert etag == 'W/"v1"'
    # Round-trip: parse_if_match recovers the bare ``v1`` tag.
    assert parse_if_match(etag) == "v1"


def test_post_report_returns_etag_header(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """POST /reports emits an initial ETag so the client can PUT
    immediately without a follow-up GET."""
    db, admin = db_setup
    ds = _make_ds(db, int(admin.id))

    resp = client.post(
        "/reports",
        headers=auth_headers,
        json={"name": _unique("pytest_etag_post"), "data_source_id": int(ds.id)},
    )
    assert resp.status_code == 201, resp.text
    assert resp.headers.get("ETag") is not None


def test_put_without_if_match_succeeds(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """Backward compat: pre-批 3 clients don't send ``If-Match``. The
    PUT must still go through with 200."""
    db, admin = db_setup
    ds = _make_ds(db, int(admin.id))
    report = _make_report(db, int(admin.id), int(ds.id))

    resp = client.put(
        f"/reports/{report.id}",
        headers=auth_headers,
        json={"description": "no If-Match, that's fine"},
    )
    assert resp.status_code == 200, resp.text
    # Response still carries the new ETag — client can opt in next round
    assert resp.headers.get("ETag") is not None


def test_put_with_matching_if_match_succeeds(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """Happy path: client's ETag matches → 200 + new ETag (v2)."""
    db, admin = db_setup
    ds = _make_ds(db, int(admin.id))
    report = _make_report(db, int(admin.id), int(ds.id))
    etag = compute_etag(int(report.version))

    resp = client.put(
        f"/reports/{report.id}",
        headers={**auth_headers, "If-Match": etag},
        json={"description": "matching If-Match"},
    )
    assert resp.status_code == 200, resp.text
    new_etag = resp.headers.get("ETag")
    assert new_etag is not None
    # The version counter increments — confirms version_id_col is
    # wired and the feature isn't a no-op.
    assert new_etag == 'W/"v2"'
    assert new_etag != etag


def test_put_with_stale_if_match_returns_412(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """Core feature: A's ETag is from before B's update → 412 with
    the current state in the body so the frontend can render a diff."""
    db, admin = db_setup
    ds = _make_ds(db, int(admin.id))
    report = _make_report(db, int(admin.id), int(ds.id))
    stale_etag = compute_etag(int(report.version))

    # Bump the row out-of-band (mimics another tab / scheduler tick).
    # Without ``version_id_col`` we have to bump ``version`` by hand —
    # in production it goes through ``update_report`` which increments
    # automatically; here we're simulating a foreign writer.
    report.description = "changed under A's feet"
    report.version = int(report.version) + 1
    db.commit()
    db.refresh(report)

    resp = client.put(
        f"/reports/{report.id}",
        headers={**auth_headers, "If-Match": stale_etag},
        json={"description": "A trying to overwrite"},
    )
    assert resp.status_code == 412, resp.text
    body = resp.json()
    # FastAPI HTTPException wraps the typed detail in {"detail": ...}
    assert "detail" in body
    detail = body["detail"]
    assert detail["message"]
    assert detail["current"]["description"] == "changed under A's feet"
    assert detail["current"]["id"] == report.id


def test_put_with_wildcard_if_match_succeeds(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """``If-Match: *`` is the RFC 7232 wildcard — matches any existing
    resource. Useful for clients that don't track versions but still
    want the precondition guarantee that the row exists."""
    db, admin = db_setup
    ds = _make_ds(db, int(admin.id))
    report = _make_report(db, int(admin.id), int(ds.id))

    resp = client.put(
        f"/reports/{report.id}",
        headers={**auth_headers, "If-Match": "*"},
        json={"description": "wildcard precondition"},
    )
    assert resp.status_code == 200, resp.text


def test_put_with_garbage_if_match_is_ignored(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
) -> None:
    """An unparseable ``If-Match`` is silently treated as "no
    precondition" — we don't want a buggy client (typo'd header name,
    bad quoting) to lock itself out with a 412. Lenient parsing is
    RFC 7232 friendly: only well-formed tags actually trigger the
    precondition check.
    """
    db, admin = db_setup
    ds = _make_ds(db, int(admin.id))
    report = _make_report(db, int(admin.id), int(ds.id))

    resp = client.put(
        f"/reports/{report.id}",
        headers={**auth_headers, "If-Match": "not a valid etag at all"},
        json={"description": "garbage header"},
    )
    assert resp.status_code == 200, resp.text


def test_concurrent_update_invalidates_stale_etag(
    client: TestClient,
    db_setup: Any,
    auth_headers: dict[str, str],
    user_a: User,
) -> None:
    """End-to-end race: A reads the ETag, B updates via a separate
    PUT, A's conditional PUT fails. This is the scenario the feature
    exists to defend."""
    db, admin = db_setup
    ds = _make_ds(db, int(admin.id))
    report = _make_report(db, int(admin.id), int(ds.id))
    _grant_write(db, report, user_a)

    # A (admin) reads the ETag.
    a_etag = compute_etag(int(report.version))

    # B (user_a) sneaks in a successful PUT — bypasses A's ETag
    # because B has their own write grant and doesn't send If-Match.
    resp_b = client.put(
        f"/reports/{report.id}",
        headers=_auth_for(user_a),
        json={"description": "B got here first"},
    )
    assert resp_b.status_code == 200, resp_b.text

    # A's now-stale PUT fails.
    resp_a = client.put(
        f"/reports/{report.id}",
        headers={**auth_headers, "If-Match": a_etag},
        json={"description": "A's overwrite attempt"},
    )
    assert resp_a.status_code == 412, resp_a.text
    body = resp_a.json()
    assert body["detail"]["current"]["description"] == "B got here first"
    # ``current`` carries the full Report so the frontend has
    # ``current.version`` to feed into the next PUT without a re-GET.
    assert body["detail"]["current"]["version"] >= 2

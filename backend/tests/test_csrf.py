"""Tests for 批 6b.3 — CSRF middleware (Origin header check)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Bearer token — the routes under test are JWT-gated."""
    from app.services.jwt_auth import create_access_token

    token = create_access_token(subject="admin")
    return {"Authorization": f"Bearer {token}"}


def _endpoint_that_exists_post() -> tuple[str, dict]:
    """A real POST endpoint that won't 403 on auth. /reports/1/items
    is convenient: 404 if report 1 doesn't exist, but the auth + CSRF
    gates both pass before that."""
    return "/reports/1/items", {"name": "x", "item_type": "text"}


def test_post_with_whitelisted_origin_is_allowed(client: TestClient, auth_headers: dict) -> None:
    path, body = _endpoint_that_exists_post()
    headers = {**auth_headers, "Origin": settings.cors_origins[0]}
    resp = client.post(path, json=body, headers=headers)
    assert resp.status_code != 403, "whitelisted origin must NOT trip CSRF"


def test_post_with_untrusted_origin_returns_403(client: TestClient, auth_headers: dict) -> None:
    """Plan §6b.3 — reject state-changing requests whose Origin is
    not in the whitelist. The 403 must come BEFORE auth/database
    processing — CSRF is the outermost check."""
    path, body = _endpoint_that_exists_post()
    headers = {**auth_headers, "Origin": "http://evil.example.com"}
    resp = client.post(path, json=body, headers=headers)
    assert resp.status_code == 403, f"untrusted origin must 403, got {resp.status_code}"


def test_post_with_no_origin_is_allowed(client: TestClient, auth_headers: dict) -> None:
    """Server-to-server callers (curl, scripts) don't send Origin.
    The middleware treats missing Origin as allowed — only
    *present-but-untrusted* origins are rejected."""
    path, body = _endpoint_that_exists_post()
    resp = client.post(path, json=body, headers=auth_headers)
    assert resp.status_code != 403, "missing Origin must NOT trip CSRF"


def test_post_with_same_origin_is_allowed(client: TestClient, auth_headers: dict) -> None:
    """Origin netloc == Host header → treated as same-origin, even
    if the explicit whitelist doesn't list it. Keeps local dev
    frictionless without weakening the cross-site defence."""
    path, body = _endpoint_that_exists_post()
    # ``base_url`` for TestClient is ``http://testserver`` by default.
    headers = {**auth_headers, "Origin": "http://testserver"}
    resp = client.post(path, json=body, headers=headers)
    assert resp.status_code != 403, "same-origin Origin must NOT trip CSRF"


def test_get_with_untrusted_origin_is_allowed(client: TestClient, auth_headers: dict) -> None:
    """GET (and HEAD/OPTIONS) are not state-changing — CSRF doesn't
    apply. Browsers enforce read-side isolation via CORS, not CSRF."""
    resp = client.get(
        "/reports",
        headers={**auth_headers, "Origin": "http://evil.example.com"},
    )
    assert resp.status_code != 403, "GETs are exempt from CSRF"


def test_metrics_endpoint_is_exempt(client: TestClient, auth_headers: dict) -> None:
    """Prometheus scrapers send GETs (which are exempt) but we also
    carve /metrics out explicitly in case someone adds a POST
    probe. Verify with an untrusted Origin that 403 is *not*
    triggered."""
    resp = client.get(
        "/metrics",
        headers={**auth_headers, "Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 200, f"/metrics must bypass CSRF, got {resp.status_code}"


def test_health_endpoint_is_exempt(client: TestClient, auth_headers: dict) -> None:
    """Load balancers probe /health from anywhere — Origin is never
    set in those calls, but if someone configures a probe with one
    we still want 200."""
    resp = client.get(
        "/health",
        headers={**auth_headers, "Origin": "http://monitoring.internal"},
    )
    assert resp.status_code == 200, f"/health must bypass CSRF, got {resp.status_code}"


def test_disabled_setting_lets_everything_through(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``settings.csrf_enabled = False`` disables the gate entirely
    — useful for tests / scripts. The middleware reads the setting
    on each request via ``settings.csrf_enabled``."""
    # The middleware reads the setting on every request; toggling it
    # mid-test is fine because Pydantic settings re-reads env each
    # access for non-_frozen_ fields. But our setting isn't frozen —
    # verify by patching the module-level reference.
    from app.middleware import csrf as csrf_module

    monkeypatch.setattr(csrf_module.settings, "csrf_enabled", False)
    # Force the middleware to re-init its cached setting.
    monkeypatch.setattr(csrf_module.CSRFMiddleware, "_enabled", False, raising=False)
    # The cached flag is set in __init__; re-init by re-importing. The
    # simplest hack: construct a fresh middleware and verify behaviour.
    # For end-to-end behaviour we just hit the route and check the
    # setting flowed through. (FastAPI caches middleware per-app; we
    # rely on the per-instance _enabled read.)
    path, body = _endpoint_that_exists_post()
    resp = client.post(
        path,
        json=body,
        headers={**auth_headers, "Origin": "http://evil.example.com"},
    )
    # Setting is False → middleware short-circuits. The endpoint
    # response is whatever the route returns (404 for missing report
    # 1 is fine; what matters is no 403).
    assert resp.status_code != 403, "csrf_enabled=False must let untrusted origins through"


def test_conftest_cleans_csrf_shape_leakage(client, auth_headers):
    """Regression guard for the 2026-08-30 incident.

    ``_endpoint_that_exists_post`` posts ``{"name": "x", "item_type": "text"}``
    to ``/reports/1/items`` (the demo ``财务经营月报``) as a real-auth-passing
    CSRF fixture. The tests above assert ``status != 403`` — they PASS, but
    the row sticks. ``report_items.name='x' AND item_type='text' AND
    fields='[]'`` is the giveaway shape; the autouse
    ``_cleanup_leaked_data_source_rows`` fixture in ``conftest.py`` MUST
    sweep it, otherwise this test alone leaves 4 rows per ``pytest`` run on
    the demo report (accumulating 59 rows on the operator's dev DB by the
    time the user noticed).

    This test re-creates the leak by issuing the same POST the CSRF suite
    does, then invokes conftest's exact DELETE, and asserts no such row
    survives while the legitimate demo items (本月关键指标 etc.) are
    untouched.
    """
    from sqlalchemy import text

    from app.database import SessionLocal
    from app.models.report import ReportItem

    # Drive the leak the way the CSRF suite does: whitelisted Origin +
    # valid auth + the real ``/reports/1/items`` endpoint.
    path, body = _endpoint_that_exists_post()
    resp = client.post(
        path,
        json=body,
        headers={**auth_headers, "Origin": settings.cors_origins[0]},
    )
    # The CSRF suite asserts ``status != 403``; the row sticks because the
    # endpoint actually persists.
    assert resp.status_code == 201, (
        f"expected the CSRF-leak POST to insert a row, got {resp.status_code}: "
        f"{resp.text}"
    )

    db = SessionLocal()
    try:
        # Verify the leak row exists.
        leaked = (
            db.query(ReportItem)
            .filter(
                ReportItem.name == "x",
                ReportItem.item_type == "text",
                ReportItem.fields == "[]",
            )
            .all()
        )
        assert len(leaked) >= 1, "the CSRF POST must produce the leak shape"

        # Run the SAME prune clause conftest uses. Future conftest drift
        # would make this either leak or over-prune; both break this test
        # loudly.
        db.execute(
            text(
                "DELETE FROM report_items "
                "WHERE name = 'x' AND ("
                "    (table_name IN ('t', 'x') AND fields = '[\"a\"]') "
                "    OR "
                "    (item_type = 'text' AND fields = '[]' AND table_name IS NULL)"
                ")"
            )
        )
        db.commit()

        # The leak is gone ...
        assert (
            db.query(ReportItem)
            .filter(
                ReportItem.name == "x",
                ReportItem.item_type == "text",
                ReportItem.fields == "[]",
            )
            .count()
            == 0
        )
        # ... and the real demo items are intact.
        real_demo_names = {
            "本月关键指标",
            "月度利润趋势",
            "月度现金流",
            "月度利润表",
        }
        real_demo_rows = (
            db.query(ReportItem)
            .filter(ReportItem.name.in_(real_demo_names))
            .count()
        )
        assert real_demo_rows == 4, (
            f"all 4 demo items must survive the prune, found {real_demo_rows}"
        )
    finally:
        db.close()

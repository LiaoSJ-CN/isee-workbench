"""Tests for ``POST /admin/data-sources/{id}/rotate-password`` (批 E).

Covers:
- Auth gates (401, 403)
- Unknown-id behaviour (uniform 404 — admin-only operations don't
  reveal existence)
- Both payload modes (admin_supplied, server_generated)
- Persistence: stored ciphertext round-trips back to the new plaintext
  via ``crypto.decrypt``
- Engine cache eviction (the rotation MUST drop the cached SQLAlchemy
  engine so subsequent connections use the new credential)
- Audit log row written with action
  ``data_source.password_rotated`` and no plaintext leak
- Crypto round-trip fills a long-standing gap (no test previously
  asserted ``decrypt(encrypt(x)) == x``)
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.crypto import decrypt as crypto_decrypt
from app.crypto import encrypt as crypto_encrypt
from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.data_source import DataSource
from app.models.user import ROLE_VIEWER, User
from app.services.jwt_auth import create_access_token
from app.services.report_generator import (
    _engine_cache,
    _get_or_create_engine,
    evict_engine,
)


def _unique_name(prefix: str = "pytest_rotate") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_data_source_with_engine():
    """Create a sqlite data source, prime the engine cache, yield its id,
    then delete + evict.

    Engine cache priming is necessary for the evict-after-rotation test —
    otherwise there's nothing to evict and the assertion is vacuous.
    """
    db: Session = SessionLocal()
    name = _unique_name()
    src = DataSource(
        name=name,
        db_type="sqlite",
        host="placeholder",
        port=0,
        database=":memory:",
        username="placeholder",
        # Pre-encrypt so the fixture matches what real rows look like.
        # crypto_encrypt handles Fernet tokens — if the value is plaintext
        # the encrypt call still wraps it.
        password=crypto_encrypt("old-placeholder-password"),
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    sid = src.id
    # Prime the cache so the evict-after-rotation assertion is meaningful.
    _get_or_create_engine(src)
    assert sid in _engine_cache, "fixture setup: cache should have the engine"
    try:
        yield sid, name
    finally:
        db.delete(src)
        db.commit()
        # Belt-and-suspenders: ensure no cached engine survives into
        # the next test (autouse engine_cache_cleanup covers this
        # too, but being explicit here documents the dependency).
        evict_engine(sid)
        db.close()


@pytest.fixture
def viewer_auth_headers() -> dict[str, str]:
    """Bearer token for a non-admin user — must get 403 on the admin route."""
    db = SessionLocal()
    try:
        user = User(
            username=_unique_name("pytest_rotate_viewer"),
            password_hash="x",
            role=ROLE_VIEWER,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = create_access_token(user.username)
        yield {"Authorization": f"Bearer {token}"}
    finally:
        # Best-effort cleanup; viewer rows aren't reused across tests.
        try:
            user = db.query(User).filter(User.username.like("pytest_rotate_viewer_%")).first()
            if user is not None:
                db.delete(user)
                db.commit()
        except Exception:
            pass
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


def test_rotate_password_requires_auth(
    client: TestClient,
    temp_data_source_with_engine,
) -> None:
    """No token → 401."""
    sid, _name = temp_data_source_with_engine
    response = client.post(
        f"/admin/data-sources/{sid}/rotate-password",
        json={},
    )
    assert response.status_code == 401


def test_rotate_password_non_admin_forbidden(
    client: TestClient,
    viewer_auth_headers,
    temp_data_source_with_engine,
) -> None:
    """Authenticated viewer → 403."""
    sid, _name = temp_data_source_with_engine
    response = client.post(
        f"/admin/data-sources/{sid}/rotate-password",
        json={},
        headers=viewer_auth_headers,
    )
    assert response.status_code == 403


def test_rotate_password_unknown_id_returns_404(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Uniform 404 — admin-only operations don't reveal existence.

    The chosen id is far above any real id so even after a noisy
    concurrent test run the lookup genuinely misses.
    """
    response = client.post(
        "/admin/data-sources/999999999/rotate-password",
        json={"new_password": "irrelevant"},
        headers=auth_headers,
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Behaviour tests
# ---------------------------------------------------------------------------


def test_rotate_password_with_admin_supplied_value(
    client: TestClient,
    auth_headers: dict[str, str],
    temp_data_source_with_engine,
) -> None:
    """Admin-supplied plaintext is encrypted and persisted; not echoed back."""
    sid, _name = temp_data_source_with_engine
    new_plaintext = "rotated-by-admin-2026"

    response = client.post(
        f"/admin/data-sources/{sid}/rotate-password",
        json={"new_password": new_plaintext},
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data_source_id"] == sid
    assert body["rotation_method"] == "admin_supplied"
    # Admin-supplied plaintext is deliberately not echoed — admin knows it.
    assert body["generated_password"] is None
    assert "rotated_at" in body

    # Persistence: stored ciphertext decrypts back to the new plaintext.
    db = SessionLocal()
    try:
        ds = db.query(DataSource).filter(DataSource.id == sid).first()
        assert ds is not None
        assert crypto_decrypt(str(ds.password)) == new_plaintext
    finally:
        db.close()


def test_rotate_password_with_server_generated_value(
    client: TestClient,
    auth_headers: dict[str, str],
    temp_data_source_with_engine,
) -> None:
    """Empty body → server generates a random password, returns plaintext once,
    and persists it."""
    sid, _name = temp_data_source_with_engine

    response = client.post(
        f"/admin/data-sources/{sid}/rotate-password",
        json={},  # empty body → server_generated
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rotation_method"] == "server_generated"
    assert body["data_source_id"] == sid
    generated = body["generated_password"]
    assert isinstance(generated, str) and len(generated) >= 16
    # Server-generated password must be url-safe (it's the literal
    # output of secrets.token_urlsafe) — sanity check the alphabet.
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert set(generated) <= allowed

    # Persistence: the stored ciphertext decrypts to the same plaintext.
    db = SessionLocal()
    try:
        ds = db.query(DataSource).filter(DataSource.id == sid).first()
        assert crypto_decrypt(str(ds.password)) == generated
    finally:
        db.close()


def test_rotate_password_evicts_cached_engine(
    client: TestClient,
    auth_headers: dict[str, str],
    temp_data_source_with_engine,
) -> None:
    """Rotation MUST drop the cached engine so the next query rebuilds it
    with the new credentials. Otherwise the next test/admin operation
    would still authenticate with the stale password.

    The fixture pre-primes the cache; the assertion is on its absence
    after the call.
    """
    sid, _name = temp_data_source_with_engine
    assert sid in _engine_cache, "fixture invariant: engine should be cached"

    response = client.post(
        f"/admin/data-sources/{sid}/rotate-password",
        json={"new_password": "x"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert sid not in _engine_cache, "rotation should have evicted the cached engine"


# ---------------------------------------------------------------------------
# Audit log tests
# ---------------------------------------------------------------------------


def test_rotate_password_writes_audit_log(
    client: TestClient,
    auth_headers: dict[str, str],
    temp_data_source_with_engine,
) -> None:
    """A dedicated ``data_source.password_rotated`` row is written.

    Filter by action + target_id rather than reading the whole log so
    parallel-run noise doesn't cause flaky failures.
    """
    sid, _name = temp_data_source_with_engine
    response = client.post(
        f"/admin/data-sources/{sid}/rotate-password",
        json={"new_password": "irrelevant"},
        headers=auth_headers,
    )
    assert response.status_code == 200

    db = SessionLocal()
    try:
        rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "data_source.password_rotated",
                AuditLog.target_id == sid,
            )
            .order_by(AuditLog.id.desc())
            .all()
        )
        # SQLite reuses rowids after a row is deleted, so the fixture
        # can yield the same ``sid`` across multiple tests. Assert
        # "at least one row for this sid, with the latest being ours"
        # rather than "exactly one row" — the latter is what bit us
        # the first time around.
        assert len(rows) >= 1, f"expected ≥1 audit row for sid={sid}"
        row = rows[0]
        # target_type is the existing constant — cross-checked to make
        # sure the audit writer routed through the right code path.
        assert row.target_type == "data_source"
        # actor_user_id is the admin (settings.admin_username). We
        # don't compare against the int id directly because the admin
        # user is seeded once per app start and the id may have been
        # incremented by other tests; verify by username instead.
        actor = db.query(User).filter(User.id == row.actor_user_id).first()
        assert actor is not None
        assert actor.username == settings.admin_username
        # Sanity check: the latest row's ``after`` payload matches
        # the rotation method we requested. Defends against the
        # possibility of an unrelated ``password_rotated`` row being
        # the latest (e.g. an admin_supplied one from a prior test
        # when we just sent a server_generated request).
        assert row.after is not None
        after = row.after if isinstance(row.after, dict) else json.loads(row.after)
        assert after.get("rotation_method") == "admin_supplied"
    finally:
        db.close()


def test_rotate_password_audit_does_not_contain_plaintext(
    client: TestClient,
    auth_headers: dict[str, str],
    temp_data_source_with_engine,
) -> None:
    """Defence-in-depth: the audit row MUST NOT carry the new password
    anywhere in its JSON payload (before/after/extra).

    The router only writes ``after={"rotation_method": ...}`` so this
    is structural — but we assert it explicitly so a future refactor
    that switches to ``audit_service._snapshot(ds)`` (which would
    also be redacted by ``_SENSITIVE_FIELDS``) doesn't silently
    regress if the redaction list is ever loosened.
    """
    sid, _name = temp_data_source_with_engine
    new_plaintext = "defence-in-depth-canary-token-2026"

    response = client.post(
        f"/admin/data-sources/{sid}/rotate-password",
        json={"new_password": new_plaintext},
        headers=auth_headers,
    )
    assert response.status_code == 200

    db = SessionLocal()
    try:
        rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.action == "data_source.password_rotated",
                AuditLog.target_id == sid,
            )
            .order_by(AuditLog.id.desc())
            .all()
        )
        # Same SQLite rowid-reuse caveat as the sibling test: the
        # fixture can yield the same sid across tests, so we operate
        # on the latest row only.
        assert len(rows) >= 1
        row = rows[0]
        # Defensive: the latest row should match our just-completed
        # call. If not, we may be scanning an unrelated audit row
        # and the assertion below would be misleading.
        assert row.after is not None
        after = row.after if isinstance(row.after, dict) else json.loads(row.after)
        assert after.get("rotation_method") == "admin_supplied"

        # Scan every JSON-shaped field the audit row carries.
        candidates = []
        for attr in ("before", "after"):
            value = getattr(row, attr)
            if value is None:
                continue
            # Some implementations store JSON as dict, others as str.
            candidates.append(json.dumps(value) if not isinstance(value, str) else value)

        joined = "\n".join(candidates)
        assert new_plaintext not in joined, (
            "audit row leaked the new plaintext password — "
            "check that 'after' is metadata-only, not a row snapshot"
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Crypto round-trip (fills a long-standing coverage gap)
# ---------------------------------------------------------------------------


def test_crypto_encrypt_decrypt_roundtrip() -> None:
    """``decrypt(encrypt(x)) == x`` for a range of inputs.

    ``encrypt`` is called from the data_source router on every create
    / update, and ``decrypt`` is called from the connection builder on
    every engine checkout. A bug in either direction silently breaks
    the entire app — this test would have caught the now-fixed Fernet
    key-mismatch bug at unit-test speed instead of via the connection
    probe.
    """
    samples = [
        "hello",
        "a-typical-password-with-!@#$%^&*()",
        # CJK password — useful guard against an encode/decode bug
        # that wouldn't show up on ASCII-only inputs.
        "测试密码-2026",
        # Single character — minimum edge case.
        "x",
        # 255-char max that the DataSource column allows.
        "a" * 255,
    ]
    for plaintext in samples:
        token = crypto_encrypt(plaintext)
        # Fernet tokens are URL-safe base64 with a version prefix.
        # The leading "gAAAAA" is the marker that ``decrypt`` uses to
        # distinguish a token from legacy plaintext — asserted here
        # so a future change to the cipher format breaks loudly.
        assert token.startswith("gAAAAA")
        assert crypto_decrypt(token) == plaintext


def test_crypto_decrypt_returns_plaintext_legacy_fallback() -> None:
    """Legacy plaintext rows (pre-Fernet rollout) must still decrypt.

    ``crypto.decrypt`` falls back to returning the raw stored string
    when the input does NOT start with the Fernet ``gAAAAA`` marker.
    This is the backward-compat path that lets existing data sources
    keep working after encryption is enabled.
    """
    legacy = "this-was-stored-as-plaintext-in-2025"
    assert crypto_decrypt(legacy) == legacy

"""JWT helpers for the auth router.

Single shared secret (HS256). Tokens carry a ``type`` claim so access
and refresh tokens can't be used interchangeably, plus a unique ``jti``
claim (P3 / PY-25) so individual tokens can be revoked via the
``revoked_jti`` table.

批 9 (RBAC): tokens additionally carry ``uid`` (user id), ``role``
(role string) and ``oid`` (org id, nullable). These claims mirror the
``User`` row so clients and edge services can read coarse-grained
permissions without a DB round-trip. The DB row is still the source of
truth — ``get_current_user`` re-reads the user on every request and
rejects disabled accounts mid-session, so a stale claim can't grant
access to a removed user.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt

from app.config import settings

TokenType = Literal["access", "refresh"]

# JWT claim keys. Kept short to keep the token small.
_CLAIM_UID = "uid"
_CLAIM_ROLE = "role"
_CLAIM_OID = "oid"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(
    subject: str,
    token_type: TokenType,
    expires_in: timedelta,
    *,
    user_id: int | None = None,
    role: str | None = None,
    org_id: int | None = None,
) -> str:
    now = _now()
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + expires_in,
        "type": token_type,
        "jti": uuid.uuid4().hex,
    }
    # Identity claims (批 9). Optional so test-only call sites and the
    # refresh-path call (which re-derives from the DB) can omit them.
    if user_id is not None:
        payload[_CLAIM_UID] = user_id
    if role is not None:
        payload[_CLAIM_ROLE] = role
    if org_id is not None:
        payload[_CLAIM_OID] = org_id
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(
    subject: str,
    *,
    user_id: int | None = None,
    role: str | None = None,
    org_id: int | None = None,
) -> str:
    """Mint a short-lived access token for API requests.

    The ``user_id`` / ``role`` / ``org_id`` kwargs let the auth router
    embed identity claims without a second DB read; pass them through
    from the freshly loaded ``User`` row.
    """
    return _encode(
        subject,
        "access",
        timedelta(minutes=settings.access_token_minutes),
        user_id=user_id,
        role=role,
        org_id=org_id,
    )


def create_refresh_token(subject: str) -> str:
    """Mint a longer-lived refresh token used only at /auth/refresh.

    Identity claims are intentionally omitted from refresh tokens —
    /auth/refresh re-loads the user from the DB and mints fresh access
    claims. Stale claims in a refresh token would defeat the point of
    mid-session revocation (disabled accounts).
    """
    return _encode(subject, "refresh", timedelta(days=settings.refresh_token_days))


def decode_token(token: str, expected_type: TokenType = "access") -> dict[str, Any] | None:
    """Decode and validate a JWT. Returns the payload or ``None`` on any
    failure (bad signature, expired, wrong type, malformed).

    Revocation is intentionally **not** checked here — the caller decides
    whether to consult the deny-list. Keeps this function pure and
    trivial to unit-test without a database.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != expected_type:
        return None
    return payload

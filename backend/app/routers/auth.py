"""Authentication endpoints.

P3 (SEC-18): credentials are looked up against the ``users`` table;
passwords are stored as bcrypt hashes. The bootstrap admin user is
seeded from ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD`` in ``backend/.env``
on first startup. The plaintext env var remains as a one-time bootstrap
mechanism — it is **not** consulted at login time.

P3 (PY-25): every token carries a unique ``jti`` claim. Logout inserts
that jti into the ``revoked_jti`` deny-list so a stolen token cannot be
reused after the legitimate user signs out. ``/auth/refresh`` rotates
refresh tokens — the old refresh jti is revoked and a new pair is
minted, so refresh tokens are single-use.

P3 (SEC-6): on success, login and refresh set HttpOnly+Secure+SameSite
cookies for both the access and refresh tokens. The response body
also carries the tokens (so CLI / curl callers that don't handle
cookies can still use the response directly). The ``Authorization:
Bearer`` header is honored as a fallback for non-cookie clients.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_token, get_current_user, get_refresh_token_from_request
from app.middleware.rate_limit import RateLimiter
from app.models.user import User
from app.services import audit as audit_service
from app.services.auth_state import is_jti_revoked, revoke_jti
from app.services.jwt_auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.services.password import verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Per-IP login rate limiter: default 10 attempts / minute.
_login_limiter = RateLimiter(
    max_requests=settings.login_rate_limit,
    window_seconds=60,
)


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    """Optional body. With cookies enabled (P3 / SEC-6 default), the
    refresh token lives in the HttpOnly cookie and no body is needed.
    Body fallback is kept for CLI / curl."""

    refresh_token: str | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    """Attach HttpOnly+SameSite cookies for both tokens.

    ``Max-Age`` matches the JWT lifetime so the browser discards the
    cookie at the same time the server-side token would expire. The
    ``Path`` is ``/`` so the cookie is sent on every API call, not
    just ``/auth/*`` (which would block ``/explorer`` etc).
    """
    if not settings.cookie_auth_enabled:
        return
    common: dict[str, Any] = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": "/",
    }
    response.set_cookie(
        key=settings.access_cookie_name,
        value=access,
        max_age=settings.access_token_minutes * 60,
        **common,
    )
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh,
        max_age=settings.refresh_token_days * 86400,
        **common,
    )


def _clear_auth_cookies(response: Response) -> None:
    common: dict[str, Any] = {
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path": "/",
    }
    response.delete_cookie(settings.access_cookie_name, **common)
    response.delete_cookie(settings.refresh_cookie_name, **common)


@router.post("/login", response_model=TokenPair)
def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> TokenPair:
    """Validate credentials against the users table and mint a fresh token pair.

    On success, both tokens are returned in the body AND set as
    HttpOnly cookies (when ``cookie_auth_enabled`` is True).
    """
    if _login_limiter.is_rate_limited(_client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": "60"},
        )

    user = db.query(User).filter(User.username == req.username).first()
    # Unified error message — don't leak whether the username exists.
    invalid_creds = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
    )
    if user is None or user.disabled:
        raise invalid_creds

    try:
        ok = verify_password(req.password, cast(str, user.password_hash))
    except ValueError:
        # Stored hash is malformed (should not happen post-seed). Treat as
        # a 500 — the row is in an unrecoverable state, and a 401 here
        # would mask a real ops issue.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored password hash is invalid; contact administrator",
        ) from None

    if not ok:
        raise invalid_creds

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    access = create_access_token(
        cast(str, user.username),
        user_id=cast(int, user.id),
        role=cast(str, user.role),
        org_id=user.org_id,
    )
    refresh = create_refresh_token(cast(str, user.username))
    _set_auth_cookies(response, access, refresh)
    # 批 9.5: audit successful login (failed logins aren't audited to
    # avoid a side-channel on usernames). User snapshot is hand-built
    # rather than going through the ORM schema because User has no
    # *Response schema and ``user.password_hash`` must never land here.
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_LOGIN,
        target_type=audit_service.TARGET_TYPE_SESSION,
        target_id=None,
        before=None,
        after={
            "id": cast(int, user.id),
            "username": cast(str, user.username),
            "role": cast(str, user.role),
        },
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return TokenPair(access_token=access, refresh_token=refresh, token_type="bearer")


@router.post("/refresh", response_model=TokenPair)
def refresh(
    request: Request,
    response: Response,
    req: Annotated[RefreshRequest, ...] = RefreshRequest(),
    db: Session = Depends(get_db),
) -> TokenPair:
    """Exchange a valid refresh token for a new token pair (rotation).

    Reads the refresh token from the HttpOnly cookie first; falls back
    to the request body for CLI callers. The old refresh jti is added
    to the deny-list; the new pair has fresh jti claims. This is the
    refresh side of PY-25: refresh tokens are single-use, so a stolen
    refresh token can be observed and invalidated the next time the
    legitimate user refreshes.
    """
    refresh_token = get_refresh_token_from_request(request, req.refresh_token if req else None)
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing refresh token (cookie or body)",
        )
    payload = decode_token(refresh_token, expected_type="refresh")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    old_jti = payload.get("jti")
    if old_jti and is_jti_revoked(db, old_jti):
        # Old refresh was already used (or explicitly revoked). Rotation
        # turns refresh into a single-use token; replay is a 401.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
        )
    if old_jti and payload.get("exp") is not None:
        revoke_jti(db, old_jti, cast(int, payload["exp"]))

    subject = cast(str, payload["sub"])
    # Re-load the user so the freshly minted access token carries the
    # current role / org_id, not the (possibly stale) claims from the
    # refresh token. The refresh token itself doesn't carry identity
    # claims, so this is the only place to look.
    user = db.query(User).filter(User.username == subject).first()
    if user is None or user.disabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is no longer active",
        )
    access = create_access_token(
        subject,
        user_id=cast(int, user.id),
        role=cast(str, user.role),
        org_id=user.org_id,
    )
    new_refresh = create_refresh_token(subject)
    _set_auth_cookies(response, access, new_refresh)
    # 批 9.5: audit successful token rotation. Same hand-built user
    # snapshot as login — never dump the full ORM row.
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_TOKEN_REFRESH,
        target_type=audit_service.TARGET_TYPE_SESSION,
        target_id=None,
        before=None,
        after={
            "id": cast(int, user.id),
            "username": cast(str, user.username),
            "role": cast(str, user.role),
        },
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return TokenPair(access_token=access, refresh_token=new_refresh, token_type="bearer")


@router.post("/logout")
def logout(
    token: Annotated[str, Depends(get_current_token)],
    user: Annotated[User, Depends(get_current_user)],
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    """Invalidate the current access token via the jti deny-list AND
    clear the auth cookies. The client should also drop any cached
    username state. The next request that presents the same token (via
    cookie or header) will get 401 ``Token has been revoked``."""
    payload = decode_token(token, expected_type="access")
    if payload and payload.get("jti") is not None and payload.get("exp") is not None:
        revoke_jti(db, cast(str, payload["jti"]), cast(int, payload["exp"]))
    _clear_auth_cookies(response)
    # 批 9.5: audit logout so we can answer "who signed out when".
    # ``user`` is added as an explicit dep (the endpoint used to rely
    # only on the token) so the audit row has an actor_user_id.
    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_LOGOUT,
        target_type=audit_service.TARGET_TYPE_SESSION,
        target_id=None,
        before=None,
        after=None,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return {"ok": True}


@router.get("/me")
def me(user: Annotated[User, Depends(get_current_user)]) -> dict[str, Any]:
    """Return the currently logged-in user plus identity claims (批 9).

    ``user.username`` is ``str | None`` per SQLAlchemy's column typing
    even though the column is ``NOT NULL`` in the schema; cast to
    match the endpoint's contract. The DB lookup in
    ``get_current_user`` already verified the row exists.
    """
    return {
        "username": cast(str, user.username),
        "user_id": cast(int, user.id),
        "role": cast(str, user.role),
        "org_id": user.org_id,
    }

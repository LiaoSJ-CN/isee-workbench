"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import ROLE_ADMIN, ROLE_EDITOR, User
from app.services.auth_state import is_jti_revoked
from app.services.jwt_auth import decode_token

# auto_error=False so a missing Authorization header doesn't itself 401;
# the call site raises the final 401 with a clear message.
_bearer = HTTPBearer(auto_error=False)


def _credentials_from_request(request: Request) -> HTTPAuthorizationCredentials | None:
    """Read the bearer token from the cookie (P3 / SEC-6) or the
    ``Authorization`` header (CLI / curl fallback).

    Order: cookie first, then header. The frontend sends the cookie
    automatically; ``Authorization: Bearer`` is only useful for direct
    API calls.
    """
    cookie = request.cookies.get(settings.access_cookie_name)
    if cookie:
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=cookie)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=auth[7:])
    return None


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """Return the authenticated :class:`~app.models.user.User` from the
    cookie or the ``Authorization`` header.

    Before 批 5.4 this returned just the username (``str``); the
    change to ``User`` lets route handlers read fields other than
    ``username`` (e.g. ``disabled``, ``last_login_at``) without an
    extra DB round-trip. The cache on ``request.state.user`` makes
    multiple ``Depends(get_current_user)`` invocations within one
    request share one DB lookup.

    Raises 401 when:
    - the cookie / header is missing,
    - the token signature is invalid or expired,
    - the token's jti is in the ``revoked_jti`` deny-list (P3 / PY-25),
    - the username in the token no longer exists in the users table,
    - the matched user has ``disabled=True``.

    The previous ``?token=`` query-param fallback was removed when
    ReportPreview switched to fetching HTML via the Authorization
    header and pointing the iframe at a blob: URL.
    """
    creds = _credentials_from_request(request)
    if not creds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    payload = decode_token(creds.credentials, expected_type="access")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    jti = payload.get("jti")
    if jti and is_jti_revoked(db, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = db.query(User).filter(User.username == str(username)).first()
    if user is None or user.disabled:
        # Same status for "user gone" and "user disabled" — don't leak
        # which one. The token is otherwise valid (sig + exp + jti
        # all passed), so this means somebody deleted/disabled the
        # account mid-session.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is no longer active",
        )

    # Cache so a second Depends(get_current_user) in the same request
    # doesn't re-query. Mostly future-proofing — only ``/auth/me``
    # currently consumes the return value, but if RBAC is added later
    # the cache keeps ``Depends(get_current_user)`` + ``require_role(...)``
    # at one DB hit per request.
    request.state.current_user = user
    return user


def get_current_token(request: Request) -> str:
    """Return the raw bearer token from the cookie or the ``Authorization`` header.

    Used by ``/auth/logout`` to read the token's jti for revocation.
    Raises 401 if neither is present — logout requires auth, since
    there's no token to revoke without one.
    """
    creds = _credentials_from_request(request)
    if not creds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return creds.credentials


def get_refresh_token_from_request(request: Request, body_token: str | None = None) -> str | None:
    """Read the refresh token from the request body first, then the cookie.

    Body takes precedence so a caller that explicitly POSTs
    ``{"refresh_token": "..."}`` (CLI / curl, body-only tests) always
    gets the token they asked for — even if the browser happens to
    also have a (possibly stale) cookie on the same request. When no
    body is provided, fall back to the HttpOnly cookie the SPA
    receives on login.

    Returns None if neither is present so the caller can return 400
    instead of 401 (the client is structurally wrong, not unauth'd).
    """
    if body_token:
        return body_token
    return request.cookies.get(settings.refresh_cookie_name)


# ---------------------------------------------------------------------------
# Role gating (批 9.2)
# ---------------------------------------------------------------------------
#
# Coarse-grained role checks for endpoints that are not bound to a
# specific resource — e.g. scheduler management, audit log access, or
# future admin-only configuration. Resource-level ACL (DataSource /
# Report) is enforced inside the corresponding service helpers in
# 批 9.3 / 9.4, NOT here.
#
# Usage:
#
#     @router.delete("/foo", dependencies=[Depends(admin_required)])
#     def delete_foo(...): ...
#
#     @router.get("/bar")
#     def get_bar(user: User = Depends(editor_required)): ...
#
# Admin always passes — even if the caller only listed ``"editor"``,
# an admin user is granted every role (escape hatch for ops). The
# returned object is the same :class:`User` instance that
# ``get_current_user`` cached, so a subsequent
# ``Depends(get_current_user)`` in the same handler hits the cache
# rather than re-querying the DB.


def require_role(*allowed: str) -> Callable[..., User]:
    """Build a FastAPI dependency that accepts only the listed roles.

    ``admin`` is always permitted (escape hatch). The returned object
    is the cached :class:`User` so handlers can read additional
    fields without a second DB lookup.
    """

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role == ROLE_ADMIN:
            return user
        if user.role not in allowed:
            # Don't echo the user's role back — the client knows who
            # they are; an error like "Role 'viewer' not allowed" is
            # both enough for a UI to render a 403 page and silent
            # enough to not leak which roles exist.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this action",
            )
        return user

    return _dep


# Convenience presets — keep call sites short and consistent.
admin_required = require_role(ROLE_ADMIN)
editor_required = require_role(ROLE_ADMIN, ROLE_EDITOR)

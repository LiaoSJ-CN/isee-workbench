"""Shared FastAPI dependencies."""

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.jwt_auth import decode_token


# auto_error=False so a missing Authorization header doesn't itself 401;
# the query-param fallback below gives iframe-loaded URLs a chance.
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Return the authenticated username from ``Authorization`` header or
    ``?token=`` query param (the latter is for iframe-loaded URLs like
    the report preview, where the browser cannot attach a custom header).

    Raises 401 if neither source is present or the token is invalid.
    """
    token = creds.credentials if creds else None
    if not token:
        token = request.query_params.get("token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    payload = decode_token(token, expected_type="access")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return payload["sub"]
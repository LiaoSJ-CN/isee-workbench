"""Shared FastAPI dependencies."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.jwt_auth import decode_token

# auto_error=False so a missing Authorization header doesn't itself 401;
# the call site raises the final 401 with a clear message.
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Return the authenticated username from the ``Authorization`` header.

    Raises 401 if the header is missing or the token is invalid.
    The previous ``?token=`` query-param fallback (kept for the old
    iframe-loaded preview) was removed when ReportPreview switched to
    fetching the HTML via Authorization header and pointing the iframe
    at a blob: URL.
    """
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
    return payload["sub"]

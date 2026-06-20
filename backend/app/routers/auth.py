"""Authentication endpoints.

Single shared admin user. Password is sourced from ``ADMIN_PASSWORD`` in
``backend/.env`` (defaults to ``admin`` for local dev). On successful
login the response carries a short-lived access token (used as
``Authorization: Bearer <token>`` on every API call) and a longer-lived
refresh token (used only at ``/auth/refresh``).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.config import settings
from app.deps import get_current_user
from app.services.jwt_auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
)


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessOnly(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenPair)
def login(req: LoginRequest) -> TokenPair:
    """Validate credentials and mint a fresh token pair."""
    if req.username != settings.admin_username or req.password != settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    return TokenPair(
        access_token=create_access_token(req.username),
        refresh_token=create_refresh_token(req.username),
    )


@router.post("/refresh", response_model=AccessOnly)
def refresh(req: RefreshRequest) -> AccessOnly:
    """Exchange a valid refresh token for a new access token."""
    payload = decode_token(req.refresh_token, expected_type="refresh")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    return AccessOnly(access_token=create_access_token(payload["sub"]))


@router.post("/logout")
def logout() -> dict:
    """Stateless JWT — clients just discard the tokens."""
    return {"ok": True}


@router.get("/me")
def me(user: str = Depends(get_current_user)) -> dict:
    """Return the currently logged-in user."""
    return {"username": user}
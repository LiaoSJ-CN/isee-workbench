"""Admin-only DataSource mutation endpoints (批 E).

Single endpoint:

- ``POST /admin/data-sources/{source_id}/rotate-password`` — replace
  the stored Fernet-encrypted password with a new one, evict the
  cached SQLAlchemy engine so subsequent connections use the new
  credentials, and write a dedicated ``data_source.password_rotated``
  audit row.

Why a dedicated endpoint instead of reusing ``PUT /data-sources/{id}``:

- The regular update endpoint is owner-write ACL — a regular user with
  write access on their own data source could rotate it. Rotation is
  an ops/security action and must be admin-only.
- The regular update uses the generic ``data_source.update`` audit
  action — operators cannot filter the audit log to "show me only
  password rotations", which is exactly what they need during incident
  response.
- The regular update does not support server-side password
  generation. The rotation endpoint lets admins rotate without ever
  learning (or choosing) the new plaintext — best practice when the
  old password is suspected leaked.

Both endpoints coexist: ``PUT`` for general owner-level edits
(host/port/db/user/etc.), this endpoint for the security-sensitive
"rotate the credential" operation.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.crypto import encrypt as crypto_encrypt
from app.database import get_db
from app.deps import admin_required
from app.models.data_source import DataSource
from app.models.user import User
from app.schemas.admin_data_source import (
    RotatePasswordRequest,
    RotatePasswordResponse,
)
from app.services import audit as audit_service
from app.services.report_generator import evict_engine

router = APIRouter(prefix="/admin/data-sources", tags=["admin"])


# ``secrets.token_urlsafe(18)`` returns ~24 base64-urlsafe characters
# (~144 bits of entropy — same order as a typical password manager
# master). 18 bytes because token_urlsafe pads to a multiple of 3 so
# 18 → 24 chars exactly. Length is fixed; callers don't need to
# configure it.
def _generate_strong_password() -> str:
    """Return a fresh URL-safe random password.

    Centralised so the policy (length, alphabet) lives in one place —
    if we ever need to add length-prefix metadata or bias the alphabet
    (e.g. require at least one digit), this is the single point of
    change.
    """
    return secrets.token_urlsafe(18)


def _client_ip(request: Request) -> str:
    """Peer IP for the audit log. ``ProxyHeadersMiddleware`` has
    already rewritten ``request.client.host`` when the request came
    through a trusted proxy, so this is the real client IP."""
    return request.client.host if request.client else "unknown"


@router.post(
    "/{source_id}/rotate-password",
    response_model=RotatePasswordResponse,
    summary="Rotate a DataSource's connection password (admin only)",
)
def rotate_data_source_password(
    source_id: int,
    payload: RotatePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(admin_required),
) -> RotatePasswordResponse:
    """Admin-only password rotation.

    Two modes (see :class:`RotatePasswordRequest` for the request
    shape and :class:`RotatePasswordResponse` for the response):

    - **admin_supplied**: payload carries ``new_password``. Persisted
      verbatim after Fernet encryption; not echoed in the response.
    - **server_generated**: payload carries no password (or empty).
      Server generates a fresh 24-char random password, persists it,
      returns the plaintext ONCE.

    Side effects (in this order):

    1. ``DataSource.password`` is updated to the Fernet ciphertext
       of the new plaintext.
    2. The cached SQLAlchemy engine for this DataSource is evicted
       (the connection URL is now stale; the next query rebuilds it
       with the new credentials).
    3. An ``audit_log`` row is written with action
       ``data_source.password_rotated``, ``before=None``, and
       ``after={"rotation_method": "..."}``. The new password is
       intentionally not part of the snapshot — even bypassing the
       ``_SENSITIVE_FIELDS`` redaction guard, there's nothing in the
       row that could leak the new credential.

    Errors: returns **404** (not 403) for a non-existent DataSource.
    Same uniform-not-found pattern as the existing DELETE / grants
    endpoints so an attacker cannot probe which ids exist.
    """
    ds = db.query(DataSource).filter(DataSource.id == source_id).first()
    if ds is None:
        # Uniform 404 — admin-only operations don't reveal existence.
        # Matches the DELETE / grants precedent in data_source.py.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DataSource {source_id} not found",
        )

    supplied = (payload.new_password or "").strip()
    if supplied:
        plaintext = supplied
        rotation_method = "admin_supplied"
    else:
        plaintext = _generate_strong_password()
        rotation_method = "server_generated"

    ds.password = crypto_encrypt(plaintext)
    db.commit()
    db.refresh(ds)

    # Drop the cached engine so the next connection rebuilds with the
    # new credential. Idempotent if nothing was cached. Mirrors the
    # evict call at the end of update_data_source (line 220) for the
    # same reason.
    evict_engine(source_id)

    audit_service.log(
        db,
        actor_user_id=cast(int, user.id),
        action=audit_service.ACTION_DATA_SOURCE_PASSWORD_ROTATED,
        target_type=audit_service.TARGET_TYPE_DATA_SOURCE,
        target_id=source_id,
        before=None,
        # Explicit metadata only — never the full row. Defence-in-depth:
        # even if the _redact/_SENSITIVE_FIELDS guard is bypassed by a
        # future refactor, the plaintext can't be in this dict because
        # we never put it here.
        after={"rotation_method": rotation_method},
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )

    return RotatePasswordResponse(
        data_source_id=source_id,
        rotation_method=rotation_method,
        rotated_at=datetime.now(timezone.utc),
        generated_password=(
            plaintext if rotation_method == "server_generated" else None
        ),
    )


__all__ = ["router"]

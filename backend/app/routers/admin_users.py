"""Admin-only User CRUD + password reset endpoints
(batch ``user-management`` Stage 1).

Surface:

- ``GET    /admin/users``             — list with filters
- ``POST   /admin/users``             — create
- ``GET    /admin/users/{id}``        — fetch one
- ``PATCH  /admin/users/{id}``        — update role / disabled
- ``DELETE /admin/users/{id}``        — soft-disable
- ``POST   /admin/users/{id}/reset-password`` — two-mode reset

All endpoints sit behind :data:`app.deps.admin_required`. Stage 2 adds
``GET /admin/users/{id}/grants`` here (URL is user-keyed; the
``/admin/grants`` prefix is reserved for resource-keyed operations).

Why a separate router from the existing ``routers/users.py``:

- ``GET /users`` is intentionally permissive (any authenticated user)
  so share-modal foreign-key resolution works without a 403.
- The admin CRUD endpoints here are admin-only and model fields the
  non-admin endpoint deliberately omits (``disabled``, ``last_login_at``).
- Mixing admin CRUD into ``routers/users.py`` would muddy that
  access model and force file-level imports that pull the admin
  password-reset flow into every reader.

Audit row conventions (matches the DataSource rotate-password
precedent at ``routers/admin_data_sources.py``):

- ``password_hash`` is **never** in the audit row — neither via the
  router's hand-built dict nor via the ``_snapshot`` path. Defence-in-
  depth: ``audit._SENSITIVE_FIELDS`` would redact it anyway, but the
  structural guarantee is stronger.
- The reset-password ``after`` payload is ``{"rotation_method": ...}``
  only — never the plaintext.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_required
from app.models.user import User
from app.schemas.user import (
    PasswordResetRequest,
    PasswordResetResponse,
    UserAclView,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from app.services import audit as audit_service
from app.services import user_admin

router = APIRouter(prefix="/admin/users", tags=["admin"])


def _client_ip(request: Request) -> str:
    """Peer IP for the audit log. ``ProxyHeadersMiddleware`` has
    already rewritten ``request.client.host`` when the request came
    through a trusted proxy, so this is the real client IP."""
    return request.client.host if request.client else "unknown"


def _user_snapshot(user: User) -> dict[str, str | int | bool | None]:
    """Build a minimal audit-row dict for a ``User``.

    Mirrors the DataSource rotation endpoint's "metadata only,
    never the full row" rule. ``password_hash`` is intentionally
    absent — it must never reach the audit log even via the
    ``_snapshot`` path (the audit module's ``_SENSITIVE_FIELDS`` is
    a defence-in-depth backstop, not the primary guard).
    """
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "disabled": user.disabled,
    }


def _require_user(db: Session, user_id: int) -> User:
    """Look up a user row or raise 404.

    Uniform 404 for admin-only operations — matches the DELETE /
    grants / DataSource-rotation precedent so an attacker cannot
    probe which ids exist.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )
    return user


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=UserListResponse,
    summary="List users (admin only)",
)
def list_users_admin(
    role: str | None = Query(default=None, max_length=16),
    disabled: bool | None = Query(default=None),
    q: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(admin_required),
) -> UserListResponse:
    """Return ``(items, total)`` matching the active filters.

    Filter order in :func:`app.services.user_admin.list_users` is
    ``disabled → role → q`` so the most selective predicate runs
    first. ``q`` matches ``username`` and ``role`` substrings via
    ``ILIKE`` (case-insensitive on SQLite/Postgres).
    """
    rows, total = user_admin.list_users(
        db,
        role=role,
        disabled=disabled,
        q=q,
        limit=limit,
        offset=offset,
    )
    return UserListResponse(
        items=[UserResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user (admin only)",
)
def create_user_admin(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_required),
) -> UserResponse:
    """Create a new user row with a bcrypt-hashed password.

    Returns **409** if the username is already taken (the DB's
    unique constraint is the safety net; the service pre-checks to
    give a clean 409 instead of an ``IntegrityError`` translation).
    """
    try:
        user = user_admin.create_user(
            db, username=payload.username, password=payload.password, role=payload.role
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except IntegrityError as exc:
        # Belt-and-suspenders for the race window between the pre-check
        # and the INSERT — another request could have created the
        # same username in between.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"username {payload.username!r} already exists",
        ) from exc

    audit_service.log(
        db,
        actor_user_id=cast(int, admin.id),
        action=audit_service.ACTION_USER_CREATE,
        target_type=audit_service.TARGET_TYPE_USER,
        target_id=user.id,
        before=None,
        after=_user_snapshot(user),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return UserResponse.model_validate(user)


# ---------------------------------------------------------------------------
# Get one
# ---------------------------------------------------------------------------


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get a single user (admin only)",
)
def get_user_admin(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(admin_required),
) -> UserResponse:
    user = _require_user(db, user_id)
    return UserResponse.model_validate(user)


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update a user's role / disabled flag (admin only)",
)
def update_user_admin(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_required),
) -> UserResponse:
    """Apply ``role`` and/or ``disabled`` patches.

    Emits one of:

    - ``user.update`` — at least one field changed.
    - ``user.disable`` — the patch flipped ``disabled`` ``False → True``.
    - ``user.enable`` — the patch flipped ``disabled`` ``True → False``.

    The action split lets the audit-page filter answer "who did I
    suspend this week" without scanning ``after`` payloads. Self-
    protection is enforced in the service helper — see
    :func:`app.services.user_admin._check_self_protection`.
    """
    target = _require_user(db, user_id)
    before_snapshot = _user_snapshot(target)
    disabled_before = target.disabled

    try:
        target = user_admin.update_user(
            db,
            actor=admin,
            target=target,
            role=payload.role,
            disabled=payload.disabled,
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    # Pick the most specific action. ``user.update`` is the catch-all;
    # the enable/disable variants only fire when the patch actually
    # flipped the bit (no-op patches still log ``user.update`` so the
    # trail shows the operator touched the row).
    action = audit_service.ACTION_USER_UPDATE
    if payload.disabled is not None and payload.disabled is True and not disabled_before:
        action = audit_service.ACTION_USER_DISABLE
    elif payload.disabled is not None and payload.disabled is False and disabled_before:
        action = audit_service.ACTION_USER_ENABLE

    audit_service.log(
        db,
        actor_user_id=cast(int, admin.id),
        action=action,
        target_type=audit_service.TARGET_TYPE_USER,
        target_id=target.id,
        before=before_snapshot,
        after=_user_snapshot(target),
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return UserResponse.model_validate(target)


# ---------------------------------------------------------------------------
# Soft-delete (disable)
# ---------------------------------------------------------------------------


@router.delete(
    "/{user_id}",
    response_model=UserResponse,
    summary="Soft-disable a user (admin only)",
)
def delete_user_admin(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_required),
) -> UserResponse:
    """Disable a user (``User.disabled = True``).

    DELETE is overloaded as "soft-delete" here because the audit FK
    (``actor_user_id``) uses ``ondelete=SET NULL`` — a hard-delete
    would silently drop the audit trail's FK readability for actions
    performed by that user. Re-enable is done via
    ``PATCH /admin/users/{id}`` with ``{"disabled": false}``.
    """
    target = _require_user(db, user_id)
    before_snapshot = _user_snapshot(target)
    try:
        target = user_admin.disable_user(db, actor=admin, target=target)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc

    # Idempotent: if the user was already disabled, ``disable_user``
    # is a no-op — no audit row. This matches the principle "the
    # audit row reflects an actual state change".
    if target.disabled and not before_snapshot["disabled"]:
        audit_service.log(
            db,
            actor_user_id=cast(int, admin.id),
            action=audit_service.ACTION_USER_DISABLE,
            target_type=audit_service.TARGET_TYPE_USER,
            target_id=target.id,
            before=before_snapshot,
            after=_user_snapshot(target),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent", ""),
        )
    return UserResponse.model_validate(target)


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


@router.post(
    "/{user_id}/reset-password",
    response_model=PasswordResetResponse,
    summary="Reset a user's password (admin only)",
)
def reset_user_password_admin(
    user_id: int,
    payload: PasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(admin_required),
) -> PasswordResetResponse:
    """Two-mode password reset (mirrors the DataSource rotation).

    - ``admin_supplied`` — payload carries a non-empty
      ``new_password``. Persisted verbatim (after bcrypt hashing).
      Plaintext **not** echoed in the response.
    - ``server_generated`` — payload is empty / ``None``. Server
      generates a 24-char URL-safe random password and returns the
      plaintext ONCE. The admin must copy it immediately.
    """
    target = _require_user(db, user_id)
    target, plaintext, method = user_admin.reset_password(
        db, target=target, new_password=payload.new_password
    )

    audit_service.log(
        db,
        actor_user_id=cast(int, admin.id),
        action=audit_service.ACTION_USER_PASSWORD_RESET,
        target_type=audit_service.TARGET_TYPE_USER,
        target_id=target.id,
        before=None,
        # Explicit metadata only — never the full row, never the
        # plaintext. Defence-in-depth: even if the redaction guard
        # is bypassed by a future refactor, the plaintext can't be
        # in this dict because we never put it here.
        after={"rotation_method": method},
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )

    return PasswordResetResponse(
        user_id=cast(int, target.id),
        rotation_method=method,
        reset_at=datetime.now(timezone.utc),
        generated_password=plaintext if method == "server_generated" else None,
    )


# ---------------------------------------------------------------------------
# ACL aggregation (batch user-management Stage 2)
# ---------------------------------------------------------------------------


@router.get(
    "/{user_id}/grants",
    response_model=UserAclView,
    summary="List every grant a user holds across all resources (admin only)",
)
def get_user_grants_admin(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: User = Depends(admin_required),
) -> UserAclView:
    """Aggregate the user's grants across DataSource / Report / Dashboard.

    Returns a :class:`UserAclView` envelope with ``subject_type =
    "user"`` and ``subject_id = user_id`` — same shape as the
    forward-compatible per-resource counterpart so the admin UI can
    drive both endpoints with one client-side handler.

    The endpoint 404s when *user_id* doesn't exist (uniform with
    the other ``/admin/users/{id}`` endpoints). An existing user
    with no grants returns an empty ``grants`` list — not 404 —
    because "this user exists, just doesn't have access" is a
    legitimate answer to the admin's question.
    """
    _require_user(db, user_id)
    grants = user_admin.list_user_grants(db, user_id)
    return UserAclView(
        subject_type="user",
        subject_id=user_id,
        grants=grants,
    )


__all__ = ["router"]

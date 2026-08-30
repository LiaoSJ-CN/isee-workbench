"""Pydantic schemas for user-facing user endpoints.

Used by:

- ``GET /users`` — lightweight listing so the report-versioning UI can
  render a human-friendly ``created_by`` instead of a raw user id
  (A3, post-批-report-versioning).
- ``POST/PATCH/GET/DELETE /admin/users`` — admin CRUD, password reset,
  and disable/reactivate. Admin-only; gated by
  :data:`app.deps.admin_required`. (Batch ``user-management`` Stage 1.)
- ``GET /admin/users/{id}/grants`` — aggregate every grant the user
  holds across DataSource / Report / Dashboard. (Batch
  ``user-management`` Stage 2.)
- ``GET / POST / DELETE /admin/grants`` — centralized grant / revoke /
  list-by-resource. (Batch ``user-management`` Stage 2.)

The lightweight ``UserSummary`` (id + username + role) is intentionally
kept separate from the richer ``UserResponse`` — ``UserResponse``
exposes fields (``disabled``, ``last_login_at``) that the share-modal
foreign-key resolution does not need and that a non-admin user should
not see.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.user import ALL_ROLES

# Subject / resource type constants — kept as module-level strings so
# the centralized grant endpoint, the per-user aggregation endpoint,
# and the future per-resource grant list can all speak the same
# vocabulary. Mirrors the Literal below — update both together.
RESOURCE_TYPE_DATA_SOURCE = "data_source"
RESOURCE_TYPE_REPORT = "report"
RESOURCE_TYPE_DASHBOARD = "dashboard"
ALL_RESOURCE_TYPES: tuple[str, ...] = (
    RESOURCE_TYPE_DATA_SOURCE,
    RESOURCE_TYPE_REPORT,
    RESOURCE_TYPE_DASHBOARD,
)

# Subject type tags for :class:`UserAclView` — only ``"user"`` is wired
# today (``GET /admin/users/{id}/grants``); the other three are
# forward-compatible so a future ``GET /admin/grants?resource_type=...``
# endpoint can return the same shape.
SUBJECT_TYPE_USER = "user"
SUBJECT_TYPE_DATA_SOURCE = "data_source"
SUBJECT_TYPE_REPORT = "report"
SUBJECT_TYPE_DASHBOARD = "dashboard"


class UserSummary(BaseModel):
    """Lightweight user projection for ``GET /users``.

    Returns just enough for the frontend to resolve ``created_by``
    foreign keys into display names. Org-bound context
    (``org_id``) and authentication material (``password_hash``,
    ``disabled``) are deliberately omitted — they're not part of the
    user-listing use case and exposing them widens the data the
    endpoint reveals.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str


# ---------------------------------------------------------------------------
# Admin CRUD (batch user-management Stage 1)
# ---------------------------------------------------------------------------

# Method label on the password-reset request/response so the audit row
# and the API caller agree on which path was taken. Strings (not enum)
# to match the existing audit action convention — easier to grep
# across code, logs, and SQL filter clauses.
PasswordResetMethod = Literal["admin_supplied", "server_generated"]


class UserCreate(BaseModel):
    """Body for ``POST /admin/users``.

    Username must be unique — the DB has a unique constraint on
    ``users.username`` and the service raises ``ValueError`` on
    collision (router → 409). Password length is bounded at 8..255;
    the lower bound rejects trivially weak passwords without forcing
    a complexity policy (admin discretion — they can issue longer
    passphrases through the server-generated path).
    """

    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=255)
    role: str = Field(..., description="One of admin / editor / viewer")

    @field_validator("role")
    @classmethod
    def _role_must_be_known(cls, value: str) -> str:
        if value not in ALL_ROLES:
            raise ValueError(
                f"role must be one of {sorted(ALL_ROLES)}, got {value!r}"
            )
        return value


class UserUpdate(BaseModel):
    """Body for ``PATCH /admin/users/{id}``.

    Username is **immutable** — changing it would break the audit FK
    readability (``actor_user_id`` resolves to the username at the
    time of the event, and operators reading the audit log expect the
    name to be stable). If a username change is ever needed, do it
    via a one-off SQL update plus an audit explanation row.
    """

    role: str | None = Field(default=None)
    disabled: bool | None = Field(default=None)

    @field_validator("role")
    @classmethod
    def _role_must_be_known(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value not in ALL_ROLES:
            raise ValueError(
                f"role must be one of {sorted(ALL_ROLES)}, got {value!r}"
            )
        return value


class UserResponse(BaseModel):
    """Row returned by every admin user endpoint.

    ``password_hash`` is deliberately absent — the admin endpoints
    must never echo or leak it (it would also be redacted by
    ``audit._SENSITIVE_FIELDS`` defence-in-depth, but the cleaner
    guarantee is to never model it on the response shape at all).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    disabled: bool
    org_id: int | None = None
    created_at: datetime | None = None
    last_login_at: datetime | None = None


class PasswordResetRequest(BaseModel):
    """Body for ``POST /admin/users/{id}/reset-password``.

    Two payload shapes:

    - ``new_password`` set to a non-empty string → admin-supplied.
      Persisted verbatim (after bcrypt hashing).
    - ``new_password`` is ``None`` or empty → server generates a
      24-char URL-safe random password (~144 bits entropy). The
      plaintext is returned ONCE in the response; the admin must
      copy it immediately because the server only stores the hash
      and cannot recover the plaintext later.
    """

    new_password: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "New plaintext password. Empty/null triggers server-side "
            "random generation; the plaintext is returned in the "
            "response and not stored anywhere recoverable."
        ),
    )


class PasswordResetResponse(BaseModel):
    """Response from a successful password reset.

    ``generated_password`` is non-null **only** when the server
    generated the password — for admin-supplied values we deliberately
    do not echo it back (admin already knows it).
    """

    user_id: int
    rotation_method: PasswordResetMethod
    reset_at: datetime
    generated_password: str | None = None


class UserListResponse(BaseModel):
    """Paginated list envelope for ``GET /admin/users``.

    ``total`` is the count of rows matching the active filter
    (without ``limit`` / ``offset`` applied) so the admin UI can show
    a pager. Mirrors :class:`AuditLogListResponse`.
    """

    items: list[UserResponse]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# ACL aggregation + centralized grants (batch user-management Stage 2)
# ---------------------------------------------------------------------------


class GrantSummaryItem(BaseModel):
    """One grant row, normalised across DataSource / Report / Dashboard.

    ``grant_id`` is the primary key of the underlying access row
    (``data_source_access.id`` / ``report_access.id`` /
    ``dashboard_access.id``). It's exposed so the admin UI can drive
    the centralised ``DELETE /admin/grants/{resource_type}/{grant_id}``
    endpoint without re-resolving ``(resource_type, resource_id,
    user_id)`` — which is awkward because the Dashboard endpoint uses
    ``user_id`` in its path while DS / Report use ``grant_id``.

    ``resource_type`` matches one of :data:`ALL_RESOURCE_TYPES`.
    ``granted_by_username`` is the lookup-friendlier projection of
    ``granted_by`` so the admin UI doesn't need to issue a separate
    user fetch per row.
    """

    resource_type: Literal["data_source", "report", "dashboard"]
    resource_id: int
    resource_name: str | None = None
    grant_id: int
    permission: Literal["read", "write"]
    granted_by: int | None = None
    granted_by_username: str | None = None
    created_at: datetime | None = None


class UserAclView(BaseModel):
    """Response envelope for ``GET /admin/users/{id}/grants`` (and the
    future per-resource counterpart).

    ``subject_type`` always resolves to ``"user"`` from the
    user-keyed endpoint today; the other literals are reserved for
    the resource-keyed counterpart so both endpoints return the same
    envelope.
    """

    subject_type: Literal["user", "data_source", "report", "dashboard"]
    subject_id: int
    grants: list[GrantSummaryItem]


class AdminGrantCreate(BaseModel):
    """Body for ``POST /admin/grants`` (centralised grant).

    ``resource_type`` selects which access table and which
    ``upsert_grant`` / ``upsert_share`` helper the service dispatches
    to. ``permission`` is the same ``read|write`` binary used by the
    per-resource share endpoints so the centralised path doesn't
    introduce a new capability tier — admin just grants the same
    things owners / write-grantees can grant themselves.

    The endpoint is **idempotent** (mirrors
    :func:`app.services.data_source.upsert_grant`) — re-POSTing with
    the same ``(resource_type, resource_id, target_user_id)`` updates
    the permission level rather than surfacing a unique-constraint
    error.
    """

    resource_type: Literal["data_source", "report", "dashboard"]
    resource_id: int = Field(..., ge=1)
    target_user_id: int = Field(..., ge=1)
    permission: Literal["read", "write"]


__all__ = [
    "ALL_RESOURCE_TYPES",
    "AdminGrantCreate",
    "GrantSummaryItem",
    "PasswordResetMethod",
    "PasswordResetRequest",
    "PasswordResetResponse",
    "RESOURCE_TYPE_DASHBOARD",
    "RESOURCE_TYPE_DATA_SOURCE",
    "RESOURCE_TYPE_REPORT",
    "SUBJECT_TYPE_DASHBOARD",
    "SUBJECT_TYPE_DATA_SOURCE",
    "SUBJECT_TYPE_REPORT",
    "SUBJECT_TYPE_USER",
    "UserAclView",
    "UserCreate",
    "UserListResponse",
    "UserResponse",
    "UserSummary",
    "UserUpdate",
]

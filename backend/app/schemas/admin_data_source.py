"""Admin-only DataSource management schemas (批 E).

Schemas for endpoints under ``/admin/data-sources`` — operations
reserved for the admin role, not exposed through the regular
``/data-sources`` ACL surface.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Method label on both the request and the response so the audit row
# and the API caller agree on which path was taken. Strings (not enum)
# to match the existing audit action convention — easier to grep
# across code, logs, and SQL filter clauses.
RotationMethod = Literal["admin_supplied", "server_generated"]


class RotatePasswordRequest(BaseModel):
    """Body for ``POST /admin/data-sources/{id}/rotate-password``.

    Two payload shapes supported:

    - ``new_password`` set to a non-empty string → admin-supplied.
      Persisted verbatim (after Fernet encryption).
    - ``new_password`` is ``None`` or empty → server generates a
      24-char URL-safe random password (~144 bits entropy). The
      plaintext is returned ONCE in the response; the admin must
      copy it immediately because the server only stores ciphertext
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


class RotatePasswordResponse(BaseModel):
    """Response from a successful rotation.

    ``generated_password`` is non-null **only** when the server
    generated the password — for admin-supplied values we deliberately
    do not echo it back (admin already knows it).
    """

    data_source_id: int
    rotation_method: RotationMethod
    rotated_at: datetime
    generated_password: str | None = None


__all__ = [
    "RotationMethod",
    "RotatePasswordRequest",
    "RotatePasswordResponse",
]

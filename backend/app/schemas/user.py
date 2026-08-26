"""Pydantic schemas for user listings.

Used by ``GET /users`` so the report-versioning UI can render a
human-friendly ``created_by`` instead of a raw user id
(final-review leftover, A3 in the post-批-report-versioning backlog).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class UserSummary(BaseModel):
    """Lightweight user projection for ``GET /users``.

    Returns just enough for the frontend to resolve ``created_by``
    foreign keys into display names. Org-bound context
    (``org_id``) and authentication material (``password_hash``,
    ``disabled``) are deliberately omitted — they're not part of
    the user-listing use case and exposing them widens the data
    the endpoint reveals.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str


__all__ = ["UserSummary"]

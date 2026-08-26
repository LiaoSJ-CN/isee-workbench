"""User listing endpoint (A3, post-批-report-versioning).

``GET /users`` returns a lightweight list of every user in the
metadata database (``id`` / ``username`` / ``role``). It exists so the
report-versioning UI can resolve the ``created_by`` foreign key on
each ``ReportVersionSummary`` to a human-readable username instead of
a raw id (e.g. ``5`` → ``alice``).

Access control is intentionally permissive: any authenticated user can
list users. Usernames are already visible on the login screen and on
``GET /auth/me``, so the listing endpoint reveals nothing that's not
already exposed by a less convenient code path. Admin-only access
would force a 403 on otherwise-valid ``GET /reports/:id/versions``
page loads whenever a reader — who can already see ``created_by`` in
the response body — couldn't resolve it client-side.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.user import UserSummary

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "",
    response_model=list[UserSummary],
    summary="List active users (id + username + role) for foreign-key resolution",
)
def list_users(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = Query(default=500, ge=1, le=500),
) -> list[UserSummary]:
    """Return every **active** user, ordered by id ascending.

    ``User.disabled`` is treated as a soft-delete flag throughout the
    codebase (``auth.py`` rejects disabled users at login). Filtering
    it here too keeps the listing consistent with the auth path and
    prevents the ``created_by`` resolution UI from showing stale
    usernames for users that can no longer sign in.

    Capped at 500 so a runaway ``SELECT *`` cannot sweep the whole
    table if someone bumps the count somewhere; in practice the
    population is dozens.
    """
    rows = (
        db.query(User)
        .filter(User.disabled.is_(False))
        .order_by(User.id.asc())
        .limit(limit)
        .all()
    )
    return [UserSummary.model_validate(row) for row in rows]


__all__ = ["list_users", "router"]

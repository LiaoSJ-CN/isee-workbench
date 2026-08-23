"""Per-user DataSource ACL helpers (批 9.3).

Three primitives the routers all consume:

* :func:`get_data_source_for_user` — owner-scoped single lookup. Returns
  ``None`` for both "not found" and "no access" so callers can answer
  a uniform 404 without leaking which case applies.
* :func:`list_accessible_data_sources` — admin sees all, owners see
  their own, others see owner=mine OR has-grant=mine.
* :func:`upsert_grant` / :func:`revoke_grant` — mutators used by the
  grant endpoints.

The admin role bypasses ACL entirely (``user.role == 'admin'``). The
shape mirrors :mod:`app.services.subscription` so the per-resource
ownership pattern is consistent across the codebase.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.data_source import DataSource
from app.models.data_source_access import DataSourceAccess
from app.models.user import ROLE_ADMIN, User

# Permission string constants — keep in sync with the regex on
# ``GrantCreate.permission`` in ``schemas.data_source``.
PERMISSION_READ = "read"
PERMISSION_WRITE = "write"
ALL_PERMISSIONS: tuple[str, ...] = (PERMISSION_READ, PERMISSION_WRITE)


def is_admin(user: User) -> bool:
    """``admin`` role bypasses all resource ACL checks."""
    return user.role == ROLE_ADMIN


def is_owner(user: User, ds: DataSource) -> bool:
    """True when ``ds.owner_user_id`` matches the user. ``False`` for
    ``ds.owner_user_id IS NULL`` (the orphan-row case)."""
    return ds.owner_user_id is not None and ds.owner_user_id == user.id


def get_data_source_for_user(
    db: Session,
    ds_id: int | None,
    user: User,
    *,
    level: str = PERMISSION_READ,
) -> DataSource | None:
    """Owner-scoped single lookup.

    Returns the :class:`DataSource` row when:

    * the user is admin (always permitted), or
    * the user is the owner (any level — owner can do anything but
      transfer ownership, which isn't a 9.3 concern), or
    * a :class:`DataSourceAccess` row grants at least the requested
      ``level``.

    Returns ``None`` for both "row missing" and "row present but no
    access" — the caller turns either into the same 404 so an
    unauthorized user can't probe for the existence of someone else's
    data source.

    ``ds_id`` accepts ``int | None`` so callers can pass through the
    raw ``report.data_source_id`` SQLAlchemy value (typed as
    ``int | None`` even though the FK is NOT NULL) without a cast at
    every call site. ``None`` falls through to a 404-equivalent return.
    """
    if ds_id is None:
        return None
    if is_admin(user):
        return db.get(DataSource, ds_id)

    ds = db.get(DataSource, ds_id)
    if ds is None:
        return None
    if is_owner(user, ds):
        return ds

    # Single-shot lookup for the user's grant row on this resource.
    grant = (
        db.query(DataSourceAccess.permission)
        .filter(
            DataSourceAccess.data_source_id == ds_id,
            DataSourceAccess.user_id == user.id,
        )
        .first()
    )
    if grant is None:
        return None
    granted = grant[0]
    if level == PERMISSION_READ:
        # Either level satisfies a read request — write implies read.
        return ds
    if granted == PERMISSION_WRITE:
        return ds
    return None


def list_accessible_data_sources(db: Session, user: User) -> list[DataSource]:
    """All data sources the user can see.

    Admins see everything; everyone else sees the union of
    ``owner_user_id = me`` and "any grant pointing at me". Ordered by
    id so callers can drive a stable pager.

    Returns the full unfiltered list — pagination is the router's
    job (it slices on top of this).
    """
    if is_admin(user):
        return db.query(DataSource).order_by(DataSource.id).all()

    owned_ids = {
        row[0]
        for row in db.query(DataSource.id)
        .filter(DataSource.owner_user_id == user.id)
        .all()
    }
    granted_ids = {
        row[0]
        for row in db.query(DataSourceAccess.data_source_id)
        .filter(DataSourceAccess.user_id == user.id)
        .all()
    }
    accessible = owned_ids | granted_ids
    if not accessible:
        return []
    return (
        db.query(DataSource)
        .filter(DataSource.id.in_(accessible))
        .order_by(DataSource.id)
        .all()
    )


def upsert_grant(
    db: Session,
    *,
    data_source_id: int,
    target_user_id: int,
    permission: str,
    granted_by: int,
) -> DataSourceAccess:
    """Create-or-update the grant for ``(data_source_id, user_id)``.

    Idempotent — calling twice with the same ``user_id`` overwrites
    ``permission`` (and refreshes ``granted_by``) instead of
    surfacing a unique-constraint error. Returns the live row.
    """
    if permission not in ALL_PERMISSIONS:
        raise ValueError(
            f"permission must be one of {ALL_PERMISSIONS}, got {permission!r}"
        )
    existing = (
        db.query(DataSourceAccess)
        .filter(
            DataSourceAccess.data_source_id == data_source_id,
            DataSourceAccess.user_id == target_user_id,
        )
        .first()
    )
    if existing is not None:
        existing.permission = permission
        existing.granted_by = granted_by
        db.commit()
        db.refresh(existing)
        return existing

    grant = DataSourceAccess(
        data_source_id=data_source_id,
        user_id=target_user_id,
        permission=permission,
        granted_by=granted_by,
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)
    return grant


def revoke_grant(db: Session, grant: DataSourceAccess) -> None:
    """Hard-delete a grant row. Caller is responsible for confirming
    the caller is owner-or-admin on the underlying data source."""
    db.delete(grant)
    db.commit()


def list_grants_for_data_source(
    db: Session,
    data_source_id: int,
) -> list[DataSourceAccess]:
    """All grant rows for one data source. Caller must gate on
    owner-or-admin — the helper itself does not check."""
    return (
        db.query(DataSourceAccess)
        .filter(DataSourceAccess.data_source_id == data_source_id)
        .order_by(DataSourceAccess.id)
        .all()
    )


def can_share(db: Session, user: User, ds: DataSource) -> bool:
    """True when the user is allowed to create/revoke grants on
    ``ds``. Owner / admin / write-grantee — write permission
    includes the right to share further (批 9.3 design decision:
    write is a transitive capability)."""
    if is_admin(user):
        return True
    if is_owner(user, ds):
        return True
    perm = (
        db.query(DataSourceAccess.permission)
        .filter(
            DataSourceAccess.data_source_id == ds.id,
            DataSourceAccess.user_id == user.id,
        )
        .first()
    )
    return perm is not None and perm[0] == PERMISSION_WRITE


# ---- Clone (批 10.3) ----

# Fields that are intentionally NOT copied when cloning a DataSource.
# Everything else on the row is fair game (host/port/db/user/password
# encrypted with the same Fernet key, so the ciphertext round-trips).
_EXCLUDE_ON_CLONE = frozenset(
    {
        "id",
        "name",  # caller resolves (or we generate a "(副本)" suffix)
        "owner_user_id",  # the cloner becomes the new owner
        "created_at",
        "updated_at",
    }
)


def _next_clone_name(db: Session, base: str) -> str:
    """Generate a unique clone name by appending ``(副本)`` /
    ``(副本 2)`` / ``(副本 3)`` … until no existing DataSource matches.

    Tries the suffixes in numeric order so the user sees a stable
    progression if they clone the same source multiple times. Stops
    at 1000 iterations as a safety net against pathological cases.
    """
    candidate = f"{base} (副本)"
    n = 1
    while n <= 1000:
        exists = (
            db.query(DataSource.id).filter(DataSource.name == candidate).first()
        )
        if not exists:
            return candidate
        n += 1
        candidate = f"{base} (副本 {n})"
    raise RuntimeError(
        f"could not find a free clone name after 1000 attempts for base {base!r}"
    )


def clone_data_source(
    db: Session,
    source_id: int,
    user: User,
    *,
    new_name: str | None = None,
) -> tuple[DataSource, DataSource]:
    """Clone ``source_id`` into a new DataSource owned by ``user``.

    Read ACL is sufficient — anyone who can list the source can clone
    its connection details. The new row's ``owner_user_id`` is the
    cloner, so ACL is reset (the original owner loses visibility if
    the clone target is private to them, which matches user
    expectation when they "copy it to my workspace").

    Returns ``(original, clone)`` so the caller can audit both rows.
    Raises ``LookupError`` if the source doesn't exist or the caller
    can't read it (uniform 404 — caller doesn't know which case).
    Raises ``ValueError`` if ``new_name`` collides with an existing
    DataSource name (caller's contract — they picked it).
    """
    original = get_data_source_for_user(db, source_id, user)
    if original is None:
        raise LookupError(f"DataSource {source_id} not found or inaccessible")

    # Resolve name. If caller passed one, validate it's free; otherwise
    # auto-generate with the "(副本)" suffix progression. ``name`` is
    # NOT NULL in the schema so the ``str | None`` narrowing is safe.
    assert original.name is not None
    chosen = new_name if new_name else _next_clone_name(db, original.name)
    if chosen == original.name:
        raise ValueError("clone name must differ from the source name")
    collision = (
        db.query(DataSource.id).filter(DataSource.name == chosen).first()
    )
    if collision:
        raise ValueError(f"Data source named {chosen!r} already exists")

    clone = DataSource(
        **{
            col: getattr(original, col)
            for col in _column_names(DataSource)
            if col not in _EXCLUDE_ON_CLONE
        },
        name=chosen,
        owner_user_id=user.id,
    )
    db.add(clone)
    db.flush()  # populate clone.id so the audit ``after`` row resolves
    return original, clone


def _column_names(model: type[DataSource]) -> list[str]:
    """Return all column attribute names on a SQLAlchemy declarative
    class. Used by ``clone_data_source`` to iterate every field
    without hard-coding the schema in two places.
    """
    return [c.key for c in model.__table__.columns]


__all__ = [
    "ALL_PERMISSIONS",
    "PERMISSION_READ",
    "PERMISSION_WRITE",
    "can_share",
    "get_data_source_for_user",
    "is_admin",
    "is_owner",
    "list_accessible_data_sources",
    "list_grants_for_data_source",
    "revoke_grant",
    "upsert_grant",
]

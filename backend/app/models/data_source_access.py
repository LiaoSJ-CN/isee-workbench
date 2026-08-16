"""SQLAlchemy model for per-user DataSource grants (批 9.3).

A :class:`DataSourceAccess` row grants one :class:`User` either
``read`` or ``write`` access to one :class:`DataSource`. Owner-level
control (delete, ownership transfer) lives on ``DataSource.owner_user_id``
itself — the grants table only covers "who else can use this resource
and at what level". The admin role bypasses ACL entirely so admin rows
do not need to exist in this table.

Mirrors :class:`ReportSubscription`'s shape — per-resource, per-user,
FK cascades on the owning-resource side (``ondelete=CASCADE`` on
``data_source_id`` / ``user_id``) so deleting either the source or
the user cleans up its grants. ``granted_by`` uses ``SET NULL`` so
removing the granting user doesn't blow away the row itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, relationship
from sqlalchemy.sql import func

from app.database import Base

if TYPE_CHECKING:
    from app.models.data_source import DataSource


class DataSourceAccess(Base):
    """A user's explicit grant to a data source.

    ``permission`` is ``"read"`` (list / get / test / schema /
    explorer-query / preview / export) or ``"write"`` (everything
    ``read`` does plus ``PUT`` / share / revoke). Delete is reserved
    for the owner or an admin even at the ``write`` level — see
    ``services.data_source.is_owner``.
    """

    __tablename__ = "data_source_access"

    id = Column(Integer, primary_key=True, index=True)
    data_source_id = Column(
        Integer,
        ForeignKey("data_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    permission = Column(String(16), nullable=False)  # "read" | "write"
    # SET NULL on grantor removal so a deleted granter doesn't delete
    # the grant (the recipient still has the access they were given).
    granted_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    data_source: Mapped["DataSource"] = relationship(
        "DataSource", back_populates="grants"
    )

    __table_args__ = (
        # One grant per (resource, user) — second POST upserts the
        # permission level rather than creating a duplicate row.
        UniqueConstraint(
            "data_source_id",
            "user_id",
            name="uq_ds_access_ds_user",
        ),
        # Reverse lookup: "what does this user have access to?"
        Index("ix_ds_access_user", "user_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging
        return (
            f"<DataSourceAccess(id={self.id}, ds_id={self.data_source_id}, "
            f"user_id={self.user_id}, permission='{self.permission}')>"
        )

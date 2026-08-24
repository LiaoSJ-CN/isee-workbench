"""add audit log query indexes + retention support (批 11.1)

Revision ID: a51e9a14f8c7
Revises: dff25a24e6b4
Create Date: 2026-08-24 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a51e9a14f8c7"
down_revision: Union[str, Sequence[str], None] = "dff25a24e6b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add query-optimisation indexes for the admin ``/audit-logs`` filters.

    The two new indexes target the hot read paths of the admin endpoint
    (see :mod:`app.routers.audit`):

    - ``(actor_user_id, created_at)`` — backs
      ``WHERE actor_user_id = X ORDER BY created_at DESC`` so the
      planner returns rows in chronological order without a separate
      sort. Critical once a single user accumulates > 100k actions.
    - ``ip_address`` — backs the new ``?ip_address=...`` filter
      ("everything from this client IP") added in the same batch.

    The single-column ``actor_user_id`` index from 批 9.5 is kept —
    the composite doesn't strictly subsume it (queries that filter
    only by actor without sorting can still use either). The marginal
    write cost of one extra b-tree is worth the read flexibility.

    Indexes are NOT added on ``request_id`` (high cardinality / UUID,
    rare cross-reference lookup) or ``before``/``after`` (JSON column,
    index would balloon). Compliance ``?request_id=...`` lookups are
    infrequent and tolerate a sequential scan on the small result set.
    """
    op.create_index(
        op.f("ix_audit_log_actor_user_id_created_at"),
        "audit_log",
        ["actor_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_log_ip_address"),
        "audit_log",
        ["ip_address"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the new indexes; the original 批 9.5 indexes stay intact."""
    op.drop_index(op.f("ix_audit_log_ip_address"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_actor_user_id_created_at"), table_name="audit_log")

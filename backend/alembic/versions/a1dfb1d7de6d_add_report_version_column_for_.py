"""add report version column for optimistic concurrency (批 3)

Revision ID: a1dfb1d7de6d
Revises: e4f1b2c3a5d6
Create Date: 2026-09-05 19:02:48.213858

Adds ``reports.version`` — a strictly-monotonic integer counter
auto-incremented by SQLAlchemy's ``version_id_col`` mapper on every
ORM UPDATE. Used as the source of truth for the optimistic-concurrency
``If-Match`` / ``ETag`` check on ``PUT /reports/{id}`` (412
Precondition Failed on stale ETags).

Why not derive ETag from ``updated_at``? SQLite's
``CURRENT_TIMESTAMP`` collapses to second precision — two writes
inside the same second would collide on the ETag and the lock would
silently pass. An integer counter sidesteps that. See the long-form
note in ``app/models/report.py`` next to the ``version`` column.

The column is ``NOT NULL`` with ``server_default='1'`` so existing
rows backfill to ``v1`` cleanly — any incoming conditional PUT that
arrives with ``If-Match: W/"v1"`` will match.

Other auto-detected drift (missing indexes on data_source_access /
report_version_items / report_version_parameters / report_versions)
was deliberately removed from this migration — those changes are
unrelated to 批 3 and should be handled in a separate housekeeping
PR if they turn out to be real drift rather than dev/test env
divergence.

Original autogenerate also emitted ``op.create_index`` for those four
indexes; pruned to keep this migration surgical.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1dfb1d7de6d"
down_revision: Union[str, Sequence[str], None] = "e4f1b2c3a5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "reports",
        sa.Column(
            "version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("reports", "version")
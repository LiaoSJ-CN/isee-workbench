"""add dashboard subscription fingerprint (batch 14.4)

Revision ID: e4f1b2c3a5d6
Revises: 3b8e4f7c2a91
Create Date: 2026-08-29 16:00:00.000000

批 14.4 — incremental dispatch dedup needs a per-subscription
``last_fingerprint`` so the dispatcher can short-circuit a cron
tick when no dashboard item changed since the last run. The
column is hex MD5 (32 chars) so ``String(64)`` leaves headroom
for a future SHA-256 bump without a migration.

Why ``String(64)`` and not the obvious ``String(32)``:
* The dispatcher computes ``hashlib.md5(...)`` today, but the
  column length is a contract — bumping the hash algorithm
  shouldn't force a schema change.
* Indexing is unnecessary — we read ``last_fingerprint``
  together with the row primary key, never as a query filter.

Caveat:

* Existing rows have ``last_fingerprint=NULL`` — the dispatcher
  treats NULL as "first run, always send" so legacy
  subscriptions don't silently lose their first delivery.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e4f1b2c3a5d6"
down_revision: Union[str, Sequence[str], None] = "3b8e4f7c2a91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add ``last_fingerprint`` to ``dashboard_subscriptions``."""
    op.add_column(
        "dashboard_subscriptions",
        sa.Column("last_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Drop ``last_fingerprint`` from ``dashboard_subscriptions``."""
    op.drop_column("dashboard_subscriptions", "last_fingerprint")
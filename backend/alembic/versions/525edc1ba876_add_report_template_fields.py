"""add report template fields (batch 13)

Revision ID: 525edc1ba876
Revises: ce6c152ead31
Create Date: 2026-08-27 22:28:29.459176

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '525edc1ba876'
down_revision: Union[str, Sequence[str], None] = 'ce6c152ead31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 批 13 — template marketplace. ``is_template`` mirrors the existing
    # ``is_demo`` flag (Boolean NOT NULL with server_default "0"). The
    # composite index on ``(is_template, template_category)`` is what
    # the gallery endpoint filters on (``WHERE is_template = 1 AND
    # template_category = ?``). The FK on ``template_source_id`` uses
    # ON DELETE SET NULL so a future "delete template" cleanup can't
    # cascade-wipe user forks.
    op.add_column(
        'reports',
        sa.Column('is_template', sa.Boolean(), server_default='0', nullable=False),
    )
    op.add_column(
        'reports',
        sa.Column('template_category', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'reports',
        sa.Column('template_source_id', sa.Integer(), nullable=True),
    )
    # SQLite can't ALTER a table to add a foreign-key constraint — the
    # column has to exist (which it now does), but the FK definition
    # requires rebuilding the table via batch_alter_table. On Postgres
    # the batch wrapper is a no-op alias for plain ALTER TABLE.
    with op.batch_alter_table('reports') as batch_op:
        batch_op.create_foreign_key(
            'fk_reports_template_source_id',
            'reports',
            ['template_source_id'],
            ['id'],
            ondelete='SET NULL',
        )
        batch_op.create_index(
            'ix_reports_template_source_id',
            ['template_source_id'],
            unique=False,
        )
    op.create_index(
        'ix_reports_template_category',
        'reports',
        ['is_template', 'template_category'],
        unique=False,
    )

    # Data backfill: existing demo rows (seeded by ``seed_reports.py``)
    # become templates. Anything else is left at the default
    # ``is_template = 0``. This is idempotent on replay — re-running
    # the migration after the backfill would no-op since
    # ``is_template = 1 WHERE is_demo = 1`` evaluates identically.
    op.execute("UPDATE reports SET is_template = 1 WHERE is_demo = 1")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_reports_template_category', table_name='reports')
    with op.batch_alter_table('reports') as batch_op:
        batch_op.drop_constraint(
            'fk_reports_template_source_id', type_='foreignkey'
        )
        batch_op.drop_index('ix_reports_template_source_id')
    op.drop_column('reports', 'template_source_id')
    op.drop_column('reports', 'template_category')
    op.drop_column('reports', 'is_template')
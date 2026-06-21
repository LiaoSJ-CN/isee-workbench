"""C1: make DataSource host/port/username/password nullable for SQLite

Revision ID: 4527f122f4de
Revises: dbf1518d2113
Create Date: 2026-06-21 14:49:19.125173

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4527f122f4de'
down_revision: Union[str, Sequence[str], None] = 'dbf1518d2113'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('data_sources') as batch_op:
        batch_op.alter_column('host',
                              existing_type=sa.VARCHAR(length=255),
                              nullable=True)
        batch_op.alter_column('port',
                              existing_type=sa.INTEGER(),
                              nullable=True)
        batch_op.alter_column('username',
                              existing_type=sa.VARCHAR(length=255),
                              nullable=True)
        batch_op.alter_column('password',
                              existing_type=sa.VARCHAR(length=255),
                              nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('data_sources') as batch_op:
        batch_op.alter_column('password',
                              existing_type=sa.VARCHAR(length=255),
                              nullable=False)
        batch_op.alter_column('username',
                              existing_type=sa.VARCHAR(length=255),
                              nullable=False)
        batch_op.alter_column('port',
                              existing_type=sa.INTEGER(),
                              nullable=False)
        batch_op.alter_column('host',
                              existing_type=sa.VARCHAR(length=255),
                              nullable=False)

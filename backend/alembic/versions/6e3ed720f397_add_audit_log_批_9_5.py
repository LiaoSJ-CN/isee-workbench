"""add audit log (批 9.5)

Revision ID: 6e3ed720f397
Revises: 921b7fe787b0
Create Date: 2026-08-17 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e3ed720f397'
down_revision: Union[str, Sequence[str], None] = '921b7fe787b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the ``audit_log`` append-only table.

    Design notes (see :class:`app.models.audit_log.AuditLog`):

    1. **No CHECK constraints on ``action`` / ``target_type``.** The
       set of valid values lives in ``app.services.audit`` so adding
       a new action means editing one Python module, not a database
       migration. SQLite and Postgres also differ in CHECK syntax,
       so the Python layer is the single source of truth.

    2. **``actor_user_id`` FK uses ``ON DELETE SET NULL`` (not
       CASCADE).** Audit must outlive user deletion — a CASCADE FK
       would erase the user's history when an admin removes them,
       which is exactly the wrong default for a compliance log.

    3. **Composite index ``(target_type, target_id)``** is the key
       path for "show every change to resource X" queries. A single-
       column ``target_id`` index would be useless (most IDs collide
       across resource types), so we leave it off and rely on the
       composite.

    4. **JSON columns ``before`` / ``after``** are nullable. Create
       events have ``before = NULL``; delete events have
       ``after = NULL``. Login / logout have both NULL.
    """
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('target_type', sa.String(length=64), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=True),
        sa.Column('before', sa.JSON(), nullable=True),
        sa.Column('after', sa.JSON(), nullable=True),
        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=512), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['actor_user_id'], ['users.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_audit_log_id'), 'audit_log', ['id'], unique=False
    )
    op.create_index(
        op.f('ix_audit_log_actor_user_id'),
        'audit_log',
        ['actor_user_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_audit_log_action'),
        'audit_log',
        ['action'],
        unique=False,
    )
    op.create_index(
        op.f('ix_audit_log_target_type'),
        'audit_log',
        ['target_type'],
        unique=False,
    )
    op.create_index(
        op.f('ix_audit_log_target_type_target_id'),
        'audit_log',
        ['target_type', 'target_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_audit_log_created_at'),
        'audit_log',
        ['created_at'],
        unique=False,
    )


def downgrade() -> None:
    """Reverse the migration. Drop indexes (composite first) then table."""
    op.drop_index(
        op.f('ix_audit_log_created_at'), table_name='audit_log'
    )
    op.drop_index(
        op.f('ix_audit_log_target_type_target_id'), table_name='audit_log'
    )
    op.drop_index(
        op.f('ix_audit_log_target_type'), table_name='audit_log'
    )
    op.drop_index(
        op.f('ix_audit_log_action'), table_name='audit_log'
    )
    op.drop_index(
        op.f('ix_audit_log_actor_user_id'), table_name='audit_log'
    )
    op.drop_index(op.f('ix_audit_log_id'), table_name='audit_log')
    op.drop_table('audit_log')

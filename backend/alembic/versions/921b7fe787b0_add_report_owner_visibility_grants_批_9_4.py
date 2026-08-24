"""add report owner + visibility + grants (批 9.4)

Revision ID: 921b7fe787b0
Revises: 01a6b1ebae29
Create Date: 2026-08-17 00:05:47.745307

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "921b7fe787b0"
down_revision: Union[str, Sequence[str], None] = "01a6b1ebae29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Three changes:

    1. ``reports.visibility`` (NOT NULL, default ``public``). Existing
       rows are therefore public after the migration — back-compat
       with the pre-9.4 single-operator workflow where admin already
       saw every report. Newly-created reports default ``private``
       (set by the router, not the DB) so the grant table is the
       only path for cross-user access going forward.

    2. ``reports.owner_user_id`` (FK users.id, ondelete SET NULL).
       Nullable so reports stay alive when their owner is removed.
       Backfilled to admin so existing rows are non-NULL — same
       rationale as 批 9.3 on data sources.

    3. New ``report_access`` table — UNIQUE(report_id, user_id) for
       upsert semantics; mirrors :class:`data_source_access`.
    """
    # Plain ``op.add_column`` is fine for nullable columns with no
    # default constraint — SQLite handles it without the batch dance.
    op.add_column(
        "reports",
        sa.Column(
            "visibility",
            sa.String(length=16),
            server_default="public",
            nullable=False,
        ),
    )
    op.add_column("reports", sa.Column("org_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_reports_org_id"), "reports", ["org_id"], unique=False)

    # ``owner_user_id`` needs a FK to ``users.id``, which SQLite cannot
    # add with a plain ALTER TABLE — wrap the column + index + FK in
    # ``batch_alter_table`` (copy → drop → rename, no-op on Postgres).
    with op.batch_alter_table("reports") as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            op.f("ix_reports_owner_user_id"),
            ["owner_user_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_reports_owner_user_id_users",
            "users",
            ["owner_user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Backfill ownership so every existing row has a non-NULL owner.
    # Pre-9.4 the system ran as a single operator (admin), so claiming
    # all rows is the least-surprising behaviour. A no-op on a fresh
    # database where the admin seed hasn't happened yet — those rows
    # stay NULL until ownership is set on first create.
    op.execute(
        "UPDATE reports SET owner_user_id = "
        "(SELECT id FROM users WHERE username = 'admin' LIMIT 1) "
        "WHERE owner_user_id IS NULL"
    )

    # ``report_access`` table — UNIQUE on (report_id, user_id) so the
    # service layer can upsert idempotently.
    op.create_table(
        "report_access",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("permission", sa.String(length=16), nullable=False),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "user_id", name="uq_report_access_report_user"),
    )
    op.create_index(op.f("ix_report_access_id"), "report_access", ["id"], unique=False)
    op.create_index(
        op.f("ix_report_access_report_id"),
        "report_access",
        ["report_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_report_access_user_id"),
        "report_access",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_report_access_user_id"), table_name="report_access")
    op.drop_index(op.f("ix_report_access_report_id"), table_name="report_access")
    op.drop_index(op.f("ix_report_access_id"), table_name="report_access")
    op.drop_table("report_access")

    # Drop FK + index + column via batch mode (same SQLite reason as
    # the upgrade). Opposite ordering is intentional: drop FK first
    # so the column drop doesn't carry a dangling constraint.
    with op.batch_alter_table("reports") as batch_op:
        batch_op.drop_constraint("fk_reports_owner_user_id_users", type_="foreignkey")
        batch_op.drop_index(op.f("ix_reports_owner_user_id"))
        batch_op.drop_column("owner_user_id")

    op.drop_index(op.f("ix_reports_org_id"), table_name="reports")
    op.drop_column("reports", "org_id")
    op.drop_column("reports", "visibility")

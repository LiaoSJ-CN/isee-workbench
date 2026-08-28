"""add dashboard tables (batch 14)

Revision ID: 3b8e4f7c2a91
Revises: 525edc1ba876
Create Date: 2026-08-29 10:30:00.000000

批 14 — Dashboard (grid 拼装 + 订阅聚合). Four new tables:

* ``dashboards`` — the grid container. Mirrors ``reports`` ACL columns
  (``visibility`` / ``owner_user_id`` / ``org_id``); no
  ``data_source_id`` (each item carries its own).
* ``dashboard_items`` — single table for all three item types
  (``report`` / ``text`` / ``chart``). Layout coordinates
  (``x``/``y``/``w``/``h``) plus ``order_index`` for stable
  reordering, item-type-specific columns nullable so a single row
  covers all three shapes.
* ``dashboard_access`` — per-user read/write grants, mirrors
  ``report_access``.
* ``dashboard_subscriptions`` — per-user cron subscriptions, mirrors
  ``report_subscriptions``.

Schema notes:

* FK on ``dashboards.owner_user_id`` is ``ON DELETE SET NULL`` so a
  user purge doesn't cascade-wipe the dashboard — the dashboard
  becomes orphan-owned but its shares / items / subscriptions still
  exist (and the audit log keeps its records).
* FK on ``dashboard_items.dashboard_id`` is ``ON DELETE CASCADE`` —
  dropping a dashboard removes its items atomically.
* ``is_active`` on subscriptions defaults to True at the ORM level
  (mirroring ``report_subscriptions``); the migration doesn't add a
  server_default since the Boolean + ORM default pair works
  identically on SQLite and Postgres.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "3b8e4f7c2a91"
down_revision: Union[str, Sequence[str], None] = "525edc1ba876"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ---- dashboards ----
    op.create_table(
        "dashboards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="private",
        ),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("org_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_dashboards_name"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_dashboards_id", "dashboards", ["id"], unique=False)
    op.create_index(
        "ix_dashboards_owner_user_id", "dashboards", ["owner_user_id"]
    )
    op.create_index("ix_dashboards_org_id", "dashboards", ["org_id"])
    op.create_index(
        "ix_dashboards_owner_visibility",
        "dashboards",
        ["owner_user_id", "visibility"],
    )

    # ---- dashboard_items ----
    op.create_table(
        "dashboard_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dashboard_id", sa.Integer(), nullable=False),
        sa.Column("x", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("y", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("w", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("h", sa.Integer(), nullable=False, server_default="4"),
        sa.Column(
            "order_index", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("item_type", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("report_id", sa.Integer(), nullable=True),
        sa.Column("data_source_id", sa.Integer(), nullable=True),
        sa.Column("table_name", sa.String(length=255), nullable=True),
        sa.Column("fields", sa.JSON(), nullable=True),
        sa.Column("where_conditions", sa.JSON(), nullable=True),
        sa.Column("group_by", sa.JSON(), nullable=True),
        sa.Column("order_by", sa.JSON(), nullable=True),
        sa.Column("limit", sa.Integer(), nullable=True),
        sa.Column("display_config", sa.JSON(), nullable=True),
        sa.Column("custom_sql", sa.Text(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["dashboard_id"], ["dashboards.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["report_id"], ["reports.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["data_source_id"], ["data_sources.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_dashboard_items_id", "dashboard_items", ["id"], unique=False
    )
    op.create_index(
        "ix_dashboard_items_dashboard_id",
        "dashboard_items",
        ["dashboard_id"],
    )
    op.create_index(
        "ix_dashboard_items_report_id", "dashboard_items", ["report_id"]
    )
    op.create_index(
        "ix_dashboard_items_data_source_id",
        "dashboard_items",
        ["data_source_id"],
    )

    # ---- dashboard_access ----
    op.create_table(
        "dashboard_access",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dashboard_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("permission", sa.String(length=16), nullable=False),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dashboard_id", "user_id", name="uq_dashboard_access_dashboard_user"
        ),
        sa.ForeignKeyConstraint(
            ["dashboard_id"], ["dashboards.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_dashboard_access_id", "dashboard_access", ["id"], unique=False
    )
    op.create_index(
        "ix_dashboard_access_dashboard_id",
        "dashboard_access",
        ["dashboard_id"],
    )
    op.create_index(
        "ix_dashboard_access_user_id", "dashboard_access", ["user_id"]
    )

    # ---- dashboard_subscriptions ----
    op.create_table(
        "dashboard_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("dashboard_id", sa.Integer(), nullable=False),
        sa.Column("cron_expression", sa.String(length=100), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=True),
        sa.Column("notification_config", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["dashboard_id"], ["dashboards.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_dashboard_subscriptions_id",
        "dashboard_subscriptions",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_dashboard_subscriptions_owner_user_id",
        "dashboard_subscriptions",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_dashboard_subscriptions_dashboard_id",
        "dashboard_subscriptions",
        ["dashboard_id"],
    )
    op.create_index(
        "ix_dashboard_subscriptions_owner_active",
        "dashboard_subscriptions",
        ["owner_user_id", "is_active"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("dashboard_subscriptions")
    op.drop_table("dashboard_access")
    op.drop_table("dashboard_items")
    op.drop_table("dashboards")

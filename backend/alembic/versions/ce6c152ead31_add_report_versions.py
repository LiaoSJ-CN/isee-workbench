"""add report versions

Manual snapshot + restore + diff for Reports. Three tables mirror the
live Report / ReportItem / ReportParameter schemas; restore atomically
overwrites the live state from a chosen snapshot.

Revision ID: ce6c152ead31
Revises: a51e9a14f8c7
Create Date: 2026-08-25 23:23:45.916122

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ce6c152ead31"
down_revision = "a51e9a14f8c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.Integer(), sa.ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),

        # Mirrored Report columns
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("data_source_id", sa.Integer(), sa.ForeignKey("data_sources.id"), nullable=False),
        sa.Column("layout_config", sa.JSON(), nullable=True),
        sa.Column("is_scheduled", sa.Boolean(), nullable=True, server_default=sa.text("0")),
        sa.Column("cron_expression", sa.String(length=100), nullable=True),
        sa.Column("schedule_description", sa.String(length=255), nullable=True),
        sa.Column("notification_config", sa.JSON(), nullable=True),
        sa.Column("output_formats", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("1")),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("visibility", sa.String(length=16), nullable=False, server_default="public"),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("org_id", sa.Integer(), nullable=True),

        sa.UniqueConstraint("report_id", "version_number", name="uq_report_versions_report_version"),
    )
    op.create_index("ix_report_versions_report_id", "report_versions", ["report_id"])

    op.create_table(
        "report_version_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("report_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("item_type", sa.String(length=50), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=True, server_default=sa.text("0")),
        sa.Column("table_name", sa.String(length=255), nullable=True),
        sa.Column("fields", sa.JSON(), nullable=True),
        sa.Column("where_conditions", sa.JSON(), nullable=True),
        sa.Column("group_by", sa.JSON(), nullable=True),
        sa.Column("order_by", sa.JSON(), nullable=True),
        sa.Column("limit", sa.Integer(), nullable=True),
        sa.Column("display_config", sa.JSON(), nullable=True),
        sa.Column("custom_sql", sa.Text(), nullable=True),
        sa.Column("original_item_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_report_version_items_version_id", "report_version_items", ["version_id"])

    op.create_table(
        "report_version_parameters",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("report_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("default", sa.JSON(), nullable=True),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("original_parameter_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_report_version_parameters_version_id", "report_version_parameters", ["version_id"])


def downgrade() -> None:
    op.drop_index("ix_report_version_parameters_version_id", table_name="report_version_parameters")
    op.drop_table("report_version_parameters")
    op.drop_index("ix_report_version_items_version_id", table_name="report_version_items")
    op.drop_table("report_version_items")
    op.drop_index("ix_report_versions_report_id", table_name="report_versions")
    op.drop_table("report_versions")
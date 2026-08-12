"""add openclaw capability item catalog

Revision ID: b4c5d6e7f8a9
Revises: a2b3c4d5e6f7
Create Date: 2026-03-27 13:00:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "b4c5d6e7f8a9"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "openclaw_capability_item",
        sa.Column("capability_key", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("system_capability_key", sa.String(length=128), nullable=True),
        sa.Column("source_tool_name", sa.String(length=128), nullable=True),
        sa.Column("tool_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_system_preset", sa.Boolean(), nullable=False),
        sa.Column("input_schema_json", sa.JSON(), nullable=False),
        sa.Column("output_schema_json", sa.JSON(), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=False),
        sa.Column("output_summary", sa.Text(), nullable=False),
        sa.Column("tool_response_mode", sa.String(length=32), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('system_adapter','tool','workflow','agent')",
            name="ck_openclaw_capability_item_source_type",
        ),
        sa.CheckConstraint(
            "tool_response_mode IN ('json_schema','text_field')",
            name="ck_openclaw_capability_item_tool_response_mode",
        ),
        sa.CheckConstraint(
            "("
            "source_type = 'system_adapter' "
            "AND system_capability_key IS NOT NULL "
            "AND source_tool_name IS NULL "
            "AND tool_id IS NULL "
            "AND workflow_id IS NULL "
            "AND agent_profile_id IS NULL"
            ") OR ("
            "source_type = 'tool' "
            "AND system_capability_key IS NULL "
            "AND workflow_id IS NULL "
            "AND agent_profile_id IS NULL "
            "AND (tool_id IS NOT NULL OR source_tool_name IS NOT NULL)"
            ") OR ("
            "source_type = 'workflow' "
            "AND system_capability_key IS NULL "
            "AND source_tool_name IS NULL "
            "AND tool_id IS NULL "
            "AND workflow_id IS NOT NULL "
            "AND agent_profile_id IS NULL"
            ") OR ("
            "source_type = 'agent' "
            "AND system_capability_key IS NULL "
            "AND source_tool_name IS NULL "
            "AND tool_id IS NULL "
            "AND workflow_id IS NULL "
            "AND agent_profile_id IS NOT NULL"
            ")",
            name="ck_openclaw_capability_item_single_source",
        ),
        sa.ForeignKeyConstraint(["tool_id"], ["assistant_tool.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_id"], ["assistant_workflow.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["assistant_agent_profile.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_openclaw_capability_item_capability_key",
        "openclaw_capability_item",
        ["capability_key"],
        unique=True,
    )
    op.create_index(
        "ix_openclaw_capability_item_tool_name",
        "openclaw_capability_item",
        ["tool_name"],
        unique=True,
    )
    op.create_index(
        "ix_openclaw_capability_item_system_capability_key",
        "openclaw_capability_item",
        ["system_capability_key"],
        unique=False,
    )
    op.create_index(
        "ix_openclaw_capability_item_tool_id",
        "openclaw_capability_item",
        ["tool_id"],
        unique=False,
    )
    op.create_index(
        "ix_openclaw_capability_item_workflow_id",
        "openclaw_capability_item",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        "ix_openclaw_capability_item_agent_profile_id",
        "openclaw_capability_item",
        ["agent_profile_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_openclaw_capability_item_agent_profile_id",
        table_name="openclaw_capability_item",
    )
    op.drop_index(
        "ix_openclaw_capability_item_workflow_id",
        table_name="openclaw_capability_item",
    )
    op.drop_index(
        "ix_openclaw_capability_item_tool_id",
        table_name="openclaw_capability_item",
    )
    op.drop_index(
        "ix_openclaw_capability_item_system_capability_key",
        table_name="openclaw_capability_item",
    )
    op.drop_index(
        "ix_openclaw_capability_item_tool_name",
        table_name="openclaw_capability_item",
    )
    op.drop_index(
        "ix_openclaw_capability_item_capability_key",
        table_name="openclaw_capability_item",
    )
    op.drop_table("openclaw_capability_item")

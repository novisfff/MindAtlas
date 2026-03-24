"""add system ai behavior bindings

Revision ID: f1b2c3d4e5f7
Revises: e1a2b3c4d5e6
Create Date: 2026-03-23 18:10:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "f1b2c3d4e5f7"
down_revision = "e1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_system_behavior_binding",
        sa.Column("behavior_key", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "target_type IN ('workflow','agent')",
            name="ck_assistant_system_behavior_binding_target_type",
        ),
        sa.CheckConstraint(
            "(workflow_id IS NOT NULL AND agent_profile_id IS NULL AND target_type = 'workflow') OR "
            "(workflow_id IS NULL AND agent_profile_id IS NOT NULL AND target_type = 'agent')",
            name="ck_assistant_system_behavior_binding_single_target",
        ),
        sa.ForeignKeyConstraint(
            ["agent_profile_id"],
            ["assistant_agent_profile.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["assistant_workflow.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_system_behavior_binding_behavior_key",
        "assistant_system_behavior_binding",
        ["behavior_key"],
        unique=True,
    )
    op.create_index(
        "ix_assistant_system_behavior_binding_workflow_id",
        "assistant_system_behavior_binding",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        "ix_assistant_system_behavior_binding_agent_profile_id",
        "assistant_system_behavior_binding",
        ["agent_profile_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assistant_system_behavior_binding_agent_profile_id",
        table_name="assistant_system_behavior_binding",
    )
    op.drop_index(
        "ix_assistant_system_behavior_binding_workflow_id",
        table_name="assistant_system_behavior_binding",
    )
    op.drop_index(
        "ix_assistant_system_behavior_binding_behavior_key",
        table_name="assistant_system_behavior_binding",
    )
    op.drop_table("assistant_system_behavior_binding")

"""add_human_in_loop_approvals

Revision ID: a8b9c0d1e2f3
Revises: f6a7b8c9d0e1
Create Date: 2026-02-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "a8b9c0d1e2f3"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_human_approval",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_id", UUID(as_uuid=True), nullable=True),
        sa.Column("skill_id", UUID(as_uuid=True), nullable=True),
        sa.Column("node_id", sa.String(length=128), nullable=False),
        sa.Column("node_label", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("field_schema", sa.JSON(), nullable=False),
        sa.Column("initial_values", sa.JSON(), nullable=False),
        sa.Column("submitted_values", sa.JSON(), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["assistant_conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["assistant_message.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_id"], ["assistant_workflow.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["skill_id"], ["assistant_skill.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','cancelled')",
            name="ck_assistant_human_approval_status",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('approved','rejected')",
            name="ck_assistant_human_approval_decision",
        ),
    )

    op.create_index("ix_assistant_human_approval_run_id", "assistant_human_approval", ["run_id"], unique=False)
    op.create_index("ix_assistant_human_approval_channel_type", "assistant_human_approval", ["channel_type"], unique=False)
    op.create_index("ix_assistant_human_approval_conversation_id", "assistant_human_approval", ["conversation_id"], unique=False)
    op.create_index("ix_assistant_human_approval_message_id", "assistant_human_approval", ["message_id"], unique=False)
    op.create_index("ix_assistant_human_approval_workflow_id", "assistant_human_approval", ["workflow_id"], unique=False)
    op.create_index("ix_assistant_human_approval_skill_id", "assistant_human_approval", ["skill_id"], unique=False)
    op.create_index("ix_assistant_human_approval_status", "assistant_human_approval", ["status"], unique=False)
    op.create_index("ix_assistant_human_approval_run_status", "assistant_human_approval", ["run_id", "status"], unique=False)
    op.create_index("ix_assistant_human_approval_conversation_status", "assistant_human_approval", ["conversation_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_assistant_human_approval_conversation_status", table_name="assistant_human_approval")
    op.drop_index("ix_assistant_human_approval_run_status", table_name="assistant_human_approval")
    op.drop_index("ix_assistant_human_approval_status", table_name="assistant_human_approval")
    op.drop_index("ix_assistant_human_approval_skill_id", table_name="assistant_human_approval")
    op.drop_index("ix_assistant_human_approval_workflow_id", table_name="assistant_human_approval")
    op.drop_index("ix_assistant_human_approval_message_id", table_name="assistant_human_approval")
    op.drop_index("ix_assistant_human_approval_conversation_id", table_name="assistant_human_approval")
    op.drop_index("ix_assistant_human_approval_channel_type", table_name="assistant_human_approval")
    op.drop_index("ix_assistant_human_approval_run_id", table_name="assistant_human_approval")
    op.drop_table("assistant_human_approval")

"""add workflow call child memory table

Revision ID: a3b4c5d6e7f8
Revises: d2e3f4a5b6c7
Create Date: 2026-04-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "a3b4c5d6e7f8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_conversation_workflow_call_memory",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_workflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_node_scope", sa.String(length=512), nullable=False),
        sa.Column("target_workflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["assistant_conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_workflow_id"], ["assistant_workflow.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_workflow_id"], ["assistant_workflow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_workflow_call_memory_scope",
        "assistant_conversation_workflow_call_memory",
        ["conversation_id", "source_workflow_id", "source_node_scope", "target_workflow_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assistant_workflow_call_memory_scope",
        table_name="assistant_conversation_workflow_call_memory",
    )
    op.drop_table("assistant_conversation_workflow_call_memory")

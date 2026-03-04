"""add assistant l1 memory table

Revision ID: c8d9e0f1a2b3
Revises: b8c9d0e1f2a3
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "c8d9e0f1a2b3"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_conversation_l1_memory",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["assistant_conversation.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assistant_l1_memory_conversation_id",
        "assistant_conversation_l1_memory",
        ["conversation_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_l1_memory_conversation_id", table_name="assistant_conversation_l1_memory")
    op.drop_table("assistant_conversation_l1_memory")

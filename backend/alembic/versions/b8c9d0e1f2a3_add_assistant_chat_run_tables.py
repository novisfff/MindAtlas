"""add assistant chat run tables

Revision ID: b8c9d0e1f2a3
Revises: a8b9c0d1e2f3
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "b8c9d0e1f2a3"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_chat_run",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("user_message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("assistant_message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checkpoint_seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["assistant_conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_message_id"], ["assistant_message.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["assistant_message.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('queued','running','waiting_approval','cancelling','completed','failed','cancelled')",
            name="ck_assistant_chat_run_status",
        ),
    )
    op.create_index("ix_assistant_chat_run_conversation_id", "assistant_chat_run", ["conversation_id"])
    op.create_index("ix_assistant_chat_run_user_message_id", "assistant_chat_run", ["user_message_id"])
    op.create_index("ix_assistant_chat_run_assistant_message_id", "assistant_chat_run", ["assistant_message_id"])
    op.create_index("ix_assistant_chat_run_status", "assistant_chat_run", ["status"])
    op.create_index(
        "ix_assistant_chat_run_conversation_status",
        "assistant_chat_run",
        ["conversation_id", "status"],
    )

    op.create_table(
        "assistant_chat_run_event",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_name", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_chat_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_chat_run_event_run_id", "assistant_chat_run_event", ["run_id"])
    op.create_index(
        "ix_assistant_chat_run_event_run_seq",
        "assistant_chat_run_event",
        ["run_id", "seq"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_chat_run_event_run_seq", table_name="assistant_chat_run_event")
    op.drop_index("ix_assistant_chat_run_event_run_id", table_name="assistant_chat_run_event")
    op.drop_table("assistant_chat_run_event")

    op.drop_index("ix_assistant_chat_run_conversation_status", table_name="assistant_chat_run")
    op.drop_index("ix_assistant_chat_run_status", table_name="assistant_chat_run")
    op.drop_index("ix_assistant_chat_run_assistant_message_id", table_name="assistant_chat_run")
    op.drop_index("ix_assistant_chat_run_user_message_id", table_name="assistant_chat_run")
    op.drop_index("ix_assistant_chat_run_conversation_id", table_name="assistant_chat_run")
    op.drop_table("assistant_chat_run")

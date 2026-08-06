"""add assistant_config tables

Revision ID: 9c0b8c3a1d2e
Revises: 5b511c2b737b
Create Date: 2026-01-14

"""

from alembic import op
import sqlalchemy as sa


revision = "9c0b8c3a1d2e"
down_revision = "5b511c2b737b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # assistant_tool 表
    op.create_table(
        "assistant_tool",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("endpoint_url", sa.String(2048), nullable=True),
        sa.Column("http_method", sa.String(10), nullable=True),
        sa.Column("headers", sa.JSON(), nullable=True),
        sa.Column("auth_header_name", sa.String(128), nullable=True),
        sa.Column("auth_scheme", sa.String(32), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_key_hint", sa.String(64), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("payload_wrapper", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_tool_name", "assistant_tool", ["name"], unique=True)

    # assistant_skill 表
    op.create_table(
        "assistant_skill",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512), nullable=False),
        sa.Column("intent_examples", sa.JSON(), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_skill_name", "assistant_skill", ["name"], unique=True)

    # assistant_skill_step 表
    op.create_table(
        "assistant_skill_step",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("skill_id", sa.UUID(), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=True),
        sa.Column("args_from", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["skill_id"], ["assistant_skill.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_assistant_skill_step_skill_id", "assistant_skill_step", ["skill_id"])
    op.create_index(
        "uq_assistant_skill_step_skill_order",
        "assistant_skill_step",
        ["skill_id", "step_order"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_assistant_skill_step_skill_order", table_name="assistant_skill_step")
    op.drop_index("ix_assistant_skill_step_skill_id", table_name="assistant_skill_step")
    op.drop_table("assistant_skill_step")

    op.drop_index("ix_assistant_skill_name", table_name="assistant_skill")
    op.drop_table("assistant_skill")

    op.drop_index("ix_assistant_tool_name", table_name="assistant_tool")
    op.drop_table("assistant_tool")

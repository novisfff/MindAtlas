"""add_workflow_dag_tables

Revision ID: d4e5f6a7b8c9
Revises: f3a4b5c6d7e8
Create Date: 2026-02-11

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "d4e5f6a7b8c9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to assistant_skill
    op.add_column(
        "assistant_skill",
        sa.Column("workflow_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "assistant_skill",
        sa.Column("workflow_viewport", sa.JSON(), nullable=True),
    )

    # Create assistant_skill_node table
    op.create_table(
        "assistant_skill_node",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("skill_id", UUID(as_uuid=True), sa.ForeignKey("assistant_skill.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("label", sa.String(256), nullable=False, server_default=""),
        sa.Column("position_x", sa.Float(), nullable=False, server_default="0"),
        sa.Column("position_y", sa.Float(), nullable=False, server_default="0"),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_skill_node_skill_id", "assistant_skill_node", ["skill_id"])
    op.create_index("uq_skill_node_id", "assistant_skill_node", ["skill_id", "node_id"], unique=True)

    # Create assistant_skill_edge table
    op.create_table(
        "assistant_skill_edge",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("skill_id", UUID(as_uuid=True), sa.ForeignKey("assistant_skill.id", ondelete="CASCADE"), nullable=False),
        sa.Column("edge_id", sa.String(128), nullable=False),
        sa.Column("source_node_id", sa.String(128), nullable=False),
        sa.Column("target_node_id", sa.String(128), nullable=False),
        sa.Column("source_handle", sa.String(64), nullable=False, server_default="output"),
        sa.Column("target_handle", sa.String(64), nullable=False, server_default="input"),
        sa.Column("condition_type", sa.String(32), nullable=True),
        sa.Column("condition_expr", sa.JSON(), nullable=True),
        sa.Column("label", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_skill_edge_skill_id", "assistant_skill_edge", ["skill_id"])
    op.create_index(
        "uq_skill_edge",
        "assistant_skill_edge",
        ["skill_id", "source_node_id", "source_handle", "target_node_id", "target_handle"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("assistant_skill_edge")
    op.drop_table("assistant_skill_node")
    op.drop_column("assistant_skill", "workflow_viewport")
    op.drop_column("assistant_skill", "workflow_version")

"""drop_legacy_workflow_graph_tables

Revision ID: c9d0e1f2a3b4
Revises: b0c1d2e3f4a5
Create Date: 2026-04-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "c9d0e1f2a3b4"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    if not _has_table(table_name):
        return
    inspector = sa.inspect(op.get_bind())
    if any(index.get("name") == index_name for index in inspector.get_indexes(table_name)):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    for table_name, indexes in (
        ("assistant_workflow_edge", ("uq_workflow_edge", "ix_assistant_workflow_edge_workflow_id")),
        ("assistant_workflow_node", ("uq_workflow_node_id", "ix_assistant_workflow_node_workflow_id")),
        ("assistant_skill_edge", ("uq_skill_edge", "ix_assistant_skill_edge_skill_id")),
        ("assistant_skill_node", ("uq_skill_node_id", "ix_assistant_skill_node_skill_id")),
    ):
        if not _has_table(table_name):
            continue
        for index_name in indexes:
            _drop_index_if_exists(table_name, index_name)
        op.drop_table(table_name)


def downgrade() -> None:
    if not _has_table("assistant_skill_node"):
        op.create_table(
            "assistant_skill_node",
            sa.Column("id", UUID(as_uuid=True), nullable=False),
            sa.Column("skill_id", UUID(as_uuid=True), nullable=False),
            sa.Column("node_id", sa.String(length=128), nullable=False),
            sa.Column("node_type", sa.String(length=32), nullable=False),
            sa.Column("label", sa.String(length=256), nullable=False),
            sa.Column("position_x", sa.Float(), nullable=False),
            sa.Column("position_y", sa.Float(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["skill_id"], ["assistant_skill.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_assistant_skill_node_skill_id", "assistant_skill_node", ["skill_id"])
        op.create_index("uq_skill_node_id", "assistant_skill_node", ["skill_id", "node_id"], unique=True)

    if not _has_table("assistant_skill_edge"):
        op.create_table(
            "assistant_skill_edge",
            sa.Column("id", UUID(as_uuid=True), nullable=False),
            sa.Column("skill_id", UUID(as_uuid=True), nullable=False),
            sa.Column("edge_id", sa.String(length=128), nullable=False),
            sa.Column("source_node_id", sa.String(length=128), nullable=False),
            sa.Column("target_node_id", sa.String(length=128), nullable=False),
            sa.Column("source_handle", sa.String(length=64), nullable=False),
            sa.Column("target_handle", sa.String(length=64), nullable=False),
            sa.Column("condition_type", sa.String(length=32), nullable=True),
            sa.Column("condition_expr", sa.JSON(), nullable=True),
            sa.Column("label", sa.String(length=256), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["skill_id"], ["assistant_skill.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_assistant_skill_edge_skill_id", "assistant_skill_edge", ["skill_id"])
        op.create_index(
            "uq_skill_edge",
            "assistant_skill_edge",
            ["skill_id", "source_node_id", "source_handle", "target_node_id", "target_handle"],
            unique=True,
        )

    if not _has_table("assistant_workflow_node"):
        op.create_table(
            "assistant_workflow_node",
            sa.Column("id", UUID(as_uuid=True), nullable=False),
            sa.Column("workflow_id", UUID(as_uuid=True), nullable=False),
            sa.Column("node_id", sa.String(length=128), nullable=False),
            sa.Column("node_type", sa.String(length=32), nullable=False),
            sa.Column("label", sa.String(length=256), nullable=False),
            sa.Column("position_x", sa.Float(), nullable=False),
            sa.Column("position_y", sa.Float(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workflow_id"], ["assistant_workflow.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_assistant_workflow_node_workflow_id", "assistant_workflow_node", ["workflow_id"])
        op.create_index("uq_workflow_node_id", "assistant_workflow_node", ["workflow_id", "node_id"], unique=True)

    if not _has_table("assistant_workflow_edge"):
        op.create_table(
            "assistant_workflow_edge",
            sa.Column("id", UUID(as_uuid=True), nullable=False),
            sa.Column("workflow_id", UUID(as_uuid=True), nullable=False),
            sa.Column("edge_id", sa.String(length=128), nullable=False),
            sa.Column("source_node_id", sa.String(length=128), nullable=False),
            sa.Column("target_node_id", sa.String(length=128), nullable=False),
            sa.Column("source_handle", sa.String(length=64), nullable=False),
            sa.Column("target_handle", sa.String(length=64), nullable=False),
            sa.Column("condition_type", sa.String(length=32), nullable=True),
            sa.Column("condition_expr", sa.JSON(), nullable=True),
            sa.Column("label", sa.String(length=256), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["workflow_id"], ["assistant_workflow.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_assistant_workflow_edge_workflow_id", "assistant_workflow_edge", ["workflow_id"])
        op.create_index(
            "uq_workflow_edge",
            "assistant_workflow_edge",
            ["workflow_id", "source_node_id", "source_handle", "target_node_id", "target_handle"],
            unique=True,
        )

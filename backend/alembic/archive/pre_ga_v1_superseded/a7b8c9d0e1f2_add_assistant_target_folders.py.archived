"""add assistant target folders

Revision ID: a7b8c9d0e1f2
Revises: c9d0e1f2a3b4
Create Date: 2026-04-19
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "a7b8c9d0e1f2"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def upgrade() -> None:
    op.create_table(
        "assistant_target_folder",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("parent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("color_token", sa.String(length=32), nullable=False, server_default="slate"),
        sa.Column("icon_key", sa.String(length=32), nullable=False, server_default="folder"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["assistant_target_folder.id"],
            name="fk_assistant_target_folder_parent_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_target_folder_name", "assistant_target_folder", ["name"], unique=False)
    op.create_index("ix_assistant_target_folder_parent_id", "assistant_target_folder", ["parent_id"], unique=False)
    op.create_index(
        "ix_assistant_target_folder_parent_name",
        "assistant_target_folder",
        ["parent_id", "name"],
        unique=False,
    )

    op.add_column("assistant_workflow", sa.Column("folder_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_assistant_workflow_folder_id", "assistant_workflow", ["folder_id"], unique=False)
    op.create_foreign_key(
        "fk_assistant_workflow_folder_id",
        "assistant_workflow",
        "assistant_target_folder",
        ["folder_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("assistant_agent_profile", sa.Column("folder_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_assistant_agent_profile_folder_id", "assistant_agent_profile", ["folder_id"], unique=False)
    op.create_foreign_key(
        "fk_assistant_agent_profile_folder_id",
        "assistant_agent_profile",
        "assistant_target_folder",
        ["folder_id"],
        ["id"],
        ondelete="SET NULL",
    )

    bind = op.get_bind()
    has_system_workflow = bind.execute(
        sa.text("SELECT id FROM assistant_workflow WHERE is_system = true LIMIT 1")
    ).first()
    has_system_agent = bind.execute(
        sa.text("SELECT id FROM assistant_agent_profile WHERE is_system = true LIMIT 1")
    ).first()
    if has_system_workflow or has_system_agent:
        folder_id = uuid.uuid4()
        now = _utcnow()
        bind.execute(
            sa.text(
                "INSERT INTO assistant_target_folder "
                "(id, name, description, parent_id, color_token, icon_key, created_at, updated_at) "
                "VALUES (:id, :name, :description, :parent_id, :color_token, :icon_key, :created_at, :updated_at)"
            ),
            {
                "id": folder_id,
                "name": "系统内置",
                "description": "",
                "parent_id": None,
                "color_token": "amber",
                "icon_key": "folder",
                "created_at": now,
                "updated_at": now,
            },
        )
        bind.execute(
            sa.text("UPDATE assistant_workflow SET folder_id = :folder_id WHERE is_system = true"),
            {"folder_id": folder_id},
        )
        bind.execute(
            sa.text("UPDATE assistant_agent_profile SET folder_id = :folder_id WHERE is_system = true"),
            {"folder_id": folder_id},
        )


def downgrade() -> None:
    op.drop_constraint("fk_assistant_agent_profile_folder_id", "assistant_agent_profile", type_="foreignkey")
    op.drop_index("ix_assistant_agent_profile_folder_id", table_name="assistant_agent_profile")
    op.drop_column("assistant_agent_profile", "folder_id")

    op.drop_constraint("fk_assistant_workflow_folder_id", "assistant_workflow", type_="foreignkey")
    op.drop_index("ix_assistant_workflow_folder_id", table_name="assistant_workflow")
    op.drop_column("assistant_workflow", "folder_id")

    op.drop_index("ix_assistant_target_folder_parent_name", table_name="assistant_target_folder")
    op.drop_index("ix_assistant_target_folder_parent_id", table_name="assistant_target_folder")
    op.drop_index("ix_assistant_target_folder_name", table_name="assistant_target_folder")
    op.drop_table("assistant_target_folder")

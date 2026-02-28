"""separate_skill_workflow_agent

Revision ID: e5f6a7b8c9d0
Revises: c4d5e6f7a8b9
Create Date: 2026-02-19
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "e5f6a7b8c9d0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_param(value):
    if value is None:
        return None
    # psycopg2 cannot bind raw dict/list to JSON columns through untyped text() params.
    return json.dumps(value, ensure_ascii=False)


def upgrade() -> None:
    op.create_table(
        "assistant_workflow",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("workflow_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("workflow_viewport", sa.JSON(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_workflow_name", "assistant_workflow", ["name"], unique=True)

    op.create_table(
        "assistant_workflow_node",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(length=128), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False, server_default=""),
        sa.Column("position_x", sa.Float(), nullable=False, server_default="0"),
        sa.Column("position_y", sa.Float(), nullable=False, server_default="0"),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["assistant_workflow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_workflow_node_workflow_id", "assistant_workflow_node", ["workflow_id"])
    op.create_index("uq_workflow_node_id", "assistant_workflow_node", ["workflow_id", "node_id"], unique=True)

    op.create_table(
        "assistant_workflow_edge",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("edge_id", sa.String(length=128), nullable=False),
        sa.Column("source_node_id", sa.String(length=128), nullable=False),
        sa.Column("target_node_id", sa.String(length=128), nullable=False),
        sa.Column("source_handle", sa.String(length=64), nullable=False, server_default="output"),
        sa.Column("target_handle", sa.String(length=64), nullable=False, server_default="input"),
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

    op.create_table(
        "assistant_agent_profile",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("kb_config", sa.JSON(), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assistant_agent_profile_name", "assistant_agent_profile", ["name"], unique=True)

    op.add_column(
        "assistant_skill",
        sa.Column(
            "workflow_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assistant_workflow.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "assistant_skill",
        sa.Column(
            "agent_profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assistant_agent_profile.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index("ix_assistant_skill_workflow_id", "assistant_skill", ["workflow_id"])
    op.create_index("ix_assistant_skill_agent_profile_id", "assistant_skill", ["agent_profile_id"])

    bind = op.get_bind()
    session = sa.orm.Session(bind=bind)

    try:
        skills = session.execute(
            sa.text(
                "SELECT id, name, description, tools, langgraph_pattern, system_prompt, kb_config, "
                "workflow_viewport, workflow_version, is_system, enabled, created_at, updated_at "
                "FROM assistant_skill"
            )
        ).fetchall()

        for row in skills:
            sid = row.id
            name = str(row.name or "").strip() or f"skill_{sid}"
            description = row.description or ""
            tools = row.tools if isinstance(row.tools, list) else []
            pattern = str(row.langgraph_pattern or "agent_loop").strip().lower()
            created_at = row.created_at or _utcnow()
            updated_at = row.updated_at or created_at

            if pattern == "workflow_dag":
                wid = uuid.uuid4()
                workflow_name = f"{name}__workflow"
                session.execute(
                    sa.text(
                        "INSERT INTO assistant_workflow "
                        "(id, name, description, workflow_version, workflow_viewport, is_system, enabled, created_at, updated_at) "
                        "VALUES (:id, :name, :description, :workflow_version, :workflow_viewport, :is_system, :enabled, :created_at, :updated_at)"
                    ),
                    {
                        "id": wid,
                        "name": workflow_name,
                        "description": description,
                        "workflow_version": row.workflow_version or 1,
                        "workflow_viewport": _json_param(row.workflow_viewport),
                        "is_system": bool(row.is_system),
                        "enabled": bool(row.enabled),
                        "created_at": created_at,
                        "updated_at": updated_at,
                    },
                )

                nodes = session.execute(
                    sa.text(
                        "SELECT node_id, node_type, label, position_x, position_y, config, created_at, updated_at "
                        "FROM assistant_skill_node WHERE skill_id = :sid"
                    ),
                    {"sid": sid},
                ).fetchall()
                for node in nodes:
                    session.execute(
                        sa.text(
                            "INSERT INTO assistant_workflow_node "
                            "(id, workflow_id, node_id, node_type, label, position_x, position_y, config, created_at, updated_at) "
                            "VALUES (:id, :workflow_id, :node_id, :node_type, :label, :position_x, :position_y, :config, :created_at, :updated_at)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "workflow_id": wid,
                            "node_id": node.node_id,
                            "node_type": node.node_type,
                            "label": node.label or "",
                            "position_x": node.position_x or 0.0,
                            "position_y": node.position_y or 0.0,
                            "config": _json_param(node.config),
                            "created_at": node.created_at or created_at,
                            "updated_at": node.updated_at or updated_at,
                        },
                    )

                edges = session.execute(
                    sa.text(
                        "SELECT edge_id, source_node_id, target_node_id, source_handle, target_handle, "
                        "condition_type, condition_expr, label, created_at, updated_at "
                        "FROM assistant_skill_edge WHERE skill_id = :sid"
                    ),
                    {"sid": sid},
                ).fetchall()
                for edge in edges:
                    session.execute(
                        sa.text(
                            "INSERT INTO assistant_workflow_edge "
                            "(id, workflow_id, edge_id, source_node_id, target_node_id, source_handle, target_handle, "
                            "condition_type, condition_expr, label, created_at, updated_at) "
                            "VALUES (:id, :workflow_id, :edge_id, :source_node_id, :target_node_id, :source_handle, :target_handle, "
                            ":condition_type, :condition_expr, :label, :created_at, :updated_at)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "workflow_id": wid,
                            "edge_id": edge.edge_id,
                            "source_node_id": edge.source_node_id,
                            "target_node_id": edge.target_node_id,
                            "source_handle": edge.source_handle or "output",
                            "target_handle": edge.target_handle or "input",
                            "condition_type": edge.condition_type,
                            "condition_expr": _json_param(edge.condition_expr),
                            "label": edge.label,
                            "created_at": edge.created_at or created_at,
                            "updated_at": edge.updated_at or updated_at,
                        },
                    )

                session.execute(
                    sa.text(
                        "UPDATE assistant_skill SET workflow_id = :workflow_id, agent_profile_id = NULL WHERE id = :sid"
                    ),
                    {"workflow_id": wid, "sid": sid},
                )
            else:
                aid = uuid.uuid4()
                agent_name = f"{name}__agent"
                system_prompt = (row.system_prompt or "").strip()
                if not system_prompt:
                    system_prompt = "你是 MindAtlas 的 AI 助手，友好地回复用户。"
                session.execute(
                    sa.text(
                        "INSERT INTO assistant_agent_profile "
                        "(id, name, description, system_prompt, kb_config, tools, is_system, enabled, created_at, updated_at) "
                        "VALUES (:id, :name, :description, :system_prompt, :kb_config, :tools, :is_system, :enabled, :created_at, :updated_at)"
                    ),
                    {
                        "id": aid,
                        "name": agent_name,
                        "description": description,
                        "system_prompt": system_prompt,
                        "kb_config": _json_param(row.kb_config if isinstance(row.kb_config, dict) else {"enabled": False}),
                        "tools": _json_param(tools),
                        "is_system": bool(row.is_system),
                        "enabled": bool(row.enabled),
                        "created_at": created_at,
                        "updated_at": updated_at,
                    },
                )
                session.execute(
                    sa.text(
                        "UPDATE assistant_skill SET workflow_id = NULL, agent_profile_id = :agent_profile_id WHERE id = :sid"
                    ),
                    {"agent_profile_id": aid, "sid": sid},
                )

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    op.create_check_constraint(
        "ck_assistant_skill_single_target_binding",
        "assistant_skill",
        "(workflow_id IS NOT NULL AND agent_profile_id IS NULL) OR "
        "(workflow_id IS NULL AND agent_profile_id IS NOT NULL)",
    )

    op.alter_column("assistant_workflow", "description", server_default=None)
    op.alter_column("assistant_workflow", "workflow_version", server_default=None)
    op.alter_column("assistant_workflow_node", "label", server_default=None)
    op.alter_column("assistant_workflow_node", "position_x", server_default=None)
    op.alter_column("assistant_workflow_node", "position_y", server_default=None)
    op.alter_column("assistant_workflow_edge", "source_handle", server_default=None)
    op.alter_column("assistant_workflow_edge", "target_handle", server_default=None)
    op.alter_column("assistant_agent_profile", "description", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_assistant_skill_single_target_binding", "assistant_skill", type_="check")
    op.drop_index("ix_assistant_skill_agent_profile_id", table_name="assistant_skill")
    op.drop_index("ix_assistant_skill_workflow_id", table_name="assistant_skill")
    op.drop_column("assistant_skill", "agent_profile_id")
    op.drop_column("assistant_skill", "workflow_id")

    op.drop_index("ix_assistant_agent_profile_name", table_name="assistant_agent_profile")
    op.drop_table("assistant_agent_profile")

    op.drop_index("uq_workflow_edge", table_name="assistant_workflow_edge")
    op.drop_index("ix_assistant_workflow_edge_workflow_id", table_name="assistant_workflow_edge")
    op.drop_table("assistant_workflow_edge")

    op.drop_index("uq_workflow_node_id", table_name="assistant_workflow_node")
    op.drop_index("ix_assistant_workflow_node_workflow_id", table_name="assistant_workflow_node")
    op.drop_table("assistant_workflow_node")

    op.drop_index("ix_assistant_workflow_name", table_name="assistant_workflow")
    op.drop_table("assistant_workflow")

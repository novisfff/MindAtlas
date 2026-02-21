"""add_target_versioning

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-02-20
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_version_name(dt: datetime | None) -> str:
    base = dt.astimezone(timezone.utc) if isinstance(dt, datetime) else _utcnow()
    return base.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_agent_snapshot(*, system_prompt: str | None, tools, kb_config) -> dict:
    kb = kb_config if isinstance(kb_config, dict) else {"enabled": False}

    source_raw = kb.get("model_source", kb.get("modelSource", "default"))
    source = str(source_raw or "default").strip().lower()
    if source not in {"default", "custom"}:
        source = "default"

    model_id_raw = kb.get("model_id", kb.get("modelId"))
    model_id = None
    if model_id_raw is not None:
        text = str(model_id_raw).strip()
        if text:
            model_id = text

    if source == "default":
        model_id = None

    return {
        "system_prompt": str(system_prompt or ""),
        "tools": [str(item) for item in (tools or []) if str(item).strip()],
        "kb_config": kb,
        "model_source": source,
        "model_id": model_id,
    }


def upgrade() -> None:
    op.create_table(
        "assistant_workflow_version",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("version_name", sa.String(length=255), nullable=False),
        sa.Column("version_source", sa.String(length=32), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["assistant_workflow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("version_source IN ('save','publish')", name="ck_assistant_workflow_version_source"),
    )
    op.create_index("uq_assistant_workflow_version_seq", "assistant_workflow_version", ["workflow_id", "sequence_no"], unique=True)
    op.create_index(
        "ix_assistant_workflow_version_workflow_created",
        "assistant_workflow_version",
        ["workflow_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "assistant_agent_profile_version",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_profile_id", UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("version_name", sa.String(length=255), nullable=False),
        sa.Column("version_source", sa.String(length=32), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_profile_id"], ["assistant_agent_profile.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("version_source IN ('save','publish')", name="ck_assistant_agent_profile_version_source"),
    )
    op.create_index("uq_assistant_agent_profile_version_seq", "assistant_agent_profile_version", ["agent_profile_id", "sequence_no"], unique=True)
    op.create_index(
        "ix_assistant_agent_profile_version_agent_created",
        "assistant_agent_profile_version",
        ["agent_profile_id", "created_at"],
        unique=False,
    )

    op.add_column("assistant_workflow", sa.Column("draft_version_id", UUID(as_uuid=True), nullable=True))
    op.add_column("assistant_workflow", sa.Column("published_version_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_assistant_workflow_draft_version_id", "assistant_workflow", ["draft_version_id"])
    op.create_index("ix_assistant_workflow_published_version_id", "assistant_workflow", ["published_version_id"])
    op.create_foreign_key(
        "fk_assistant_workflow_draft_version",
        "assistant_workflow",
        "assistant_workflow_version",
        ["draft_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_assistant_workflow_published_version",
        "assistant_workflow",
        "assistant_workflow_version",
        ["published_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("assistant_agent_profile", sa.Column("draft_version_id", UUID(as_uuid=True), nullable=True))
    op.add_column("assistant_agent_profile", sa.Column("published_version_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_assistant_agent_profile_draft_version_id", "assistant_agent_profile", ["draft_version_id"])
    op.create_index("ix_assistant_agent_profile_published_version_id", "assistant_agent_profile", ["published_version_id"])
    op.create_foreign_key(
        "fk_assistant_agent_profile_draft_version",
        "assistant_agent_profile",
        "assistant_agent_profile_version",
        ["draft_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_assistant_agent_profile_published_version",
        "assistant_agent_profile",
        "assistant_agent_profile_version",
        ["published_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    bind = op.get_bind()
    session = sa.orm.Session(bind=bind)

    workflow_tbl = sa.table(
        "assistant_workflow",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("workflow_viewport", sa.JSON()),
        sa.column("draft_version_id", UUID(as_uuid=True)),
        sa.column("published_version_id", UUID(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    workflow_node_tbl = sa.table(
        "assistant_workflow_node",
        sa.column("workflow_id", UUID(as_uuid=True)),
        sa.column("node_id", sa.String()),
        sa.column("node_type", sa.String()),
        sa.column("label", sa.String()),
        sa.column("position_x", sa.Float()),
        sa.column("position_y", sa.Float()),
        sa.column("config", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    workflow_edge_tbl = sa.table(
        "assistant_workflow_edge",
        sa.column("workflow_id", UUID(as_uuid=True)),
        sa.column("edge_id", sa.String()),
        sa.column("source_node_id", sa.String()),
        sa.column("target_node_id", sa.String()),
        sa.column("source_handle", sa.String()),
        sa.column("target_handle", sa.String()),
        sa.column("condition_type", sa.String()),
        sa.column("condition_expr", sa.JSON()),
        sa.column("label", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    workflow_version_tbl = sa.table(
        "assistant_workflow_version",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("workflow_id", UUID(as_uuid=True)),
        sa.column("sequence_no", sa.Integer()),
        sa.column("version_name", sa.String()),
        sa.column("version_source", sa.String()),
        sa.column("snapshot", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    agent_tbl = sa.table(
        "assistant_agent_profile",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("system_prompt", sa.Text()),
        sa.column("tools", sa.JSON()),
        sa.column("kb_config", sa.JSON()),
        sa.column("draft_version_id", UUID(as_uuid=True)),
        sa.column("published_version_id", UUID(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    agent_version_tbl = sa.table(
        "assistant_agent_profile_version",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("agent_profile_id", UUID(as_uuid=True)),
        sa.column("sequence_no", sa.Integer()),
        sa.column("version_name", sa.String()),
        sa.column("version_source", sa.String()),
        sa.column("snapshot", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    try:
        workflows = session.execute(
            sa.select(
                workflow_tbl.c.id,
                workflow_tbl.c.workflow_viewport,
                workflow_tbl.c.created_at,
                workflow_tbl.c.updated_at,
            )
        ).mappings().all()

        for wf in workflows:
            created_at = wf["created_at"] or _utcnow()
            updated_at = wf["updated_at"] or created_at
            nodes = session.execute(
                sa.select(
                    workflow_node_tbl.c.node_id,
                    workflow_node_tbl.c.node_type,
                    workflow_node_tbl.c.label,
                    workflow_node_tbl.c.position_x,
                    workflow_node_tbl.c.position_y,
                    workflow_node_tbl.c.config,
                )
                .where(workflow_node_tbl.c.workflow_id == wf["id"])
                .order_by(workflow_node_tbl.c.created_at.asc(), workflow_node_tbl.c.node_id.asc())
            ).mappings().all()
            edges = session.execute(
                sa.select(
                    workflow_edge_tbl.c.edge_id,
                    workflow_edge_tbl.c.source_node_id,
                    workflow_edge_tbl.c.target_node_id,
                    workflow_edge_tbl.c.source_handle,
                    workflow_edge_tbl.c.target_handle,
                    workflow_edge_tbl.c.condition_type,
                    workflow_edge_tbl.c.condition_expr,
                    workflow_edge_tbl.c.label,
                )
                .where(workflow_edge_tbl.c.workflow_id == wf["id"])
                .order_by(workflow_edge_tbl.c.created_at.asc(), workflow_edge_tbl.c.edge_id.asc())
            ).mappings().all()

            snapshot = {
                "nodes": [dict(item) for item in nodes],
                "edges": [dict(item) for item in edges],
                "viewport": wf["workflow_viewport"],
            }

            version_id = uuid.uuid4()
            session.execute(
                workflow_version_tbl.insert().values(
                    id=version_id,
                    workflow_id=wf["id"],
                    sequence_no=1,
                    version_name=_default_version_name(updated_at),
                    version_source="publish",
                    snapshot=snapshot,
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
            session.execute(
                workflow_tbl.update()
                .where(workflow_tbl.c.id == wf["id"])
                .values(draft_version_id=version_id, published_version_id=version_id)
            )

        agents = session.execute(
            sa.select(
                agent_tbl.c.id,
                agent_tbl.c.system_prompt,
                agent_tbl.c.tools,
                agent_tbl.c.kb_config,
                agent_tbl.c.created_at,
                agent_tbl.c.updated_at,
            )
        ).mappings().all()

        for agent in agents:
            created_at = agent["created_at"] or _utcnow()
            updated_at = agent["updated_at"] or created_at
            snapshot = _normalize_agent_snapshot(
                system_prompt=agent["system_prompt"],
                tools=agent["tools"],
                kb_config=agent["kb_config"],
            )
            version_id = uuid.uuid4()
            session.execute(
                agent_version_tbl.insert().values(
                    id=version_id,
                    agent_profile_id=agent["id"],
                    sequence_no=1,
                    version_name=_default_version_name(updated_at),
                    version_source="publish",
                    snapshot=snapshot,
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
            session.execute(
                agent_tbl.update()
                .where(agent_tbl.c.id == agent["id"])
                .values(draft_version_id=version_id, published_version_id=version_id)
            )

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def downgrade() -> None:
    op.drop_constraint("fk_assistant_agent_profile_published_version", "assistant_agent_profile", type_="foreignkey")
    op.drop_constraint("fk_assistant_agent_profile_draft_version", "assistant_agent_profile", type_="foreignkey")
    op.drop_index("ix_assistant_agent_profile_published_version_id", table_name="assistant_agent_profile")
    op.drop_index("ix_assistant_agent_profile_draft_version_id", table_name="assistant_agent_profile")
    op.drop_column("assistant_agent_profile", "published_version_id")
    op.drop_column("assistant_agent_profile", "draft_version_id")

    op.drop_constraint("fk_assistant_workflow_published_version", "assistant_workflow", type_="foreignkey")
    op.drop_constraint("fk_assistant_workflow_draft_version", "assistant_workflow", type_="foreignkey")
    op.drop_index("ix_assistant_workflow_published_version_id", table_name="assistant_workflow")
    op.drop_index("ix_assistant_workflow_draft_version_id", table_name="assistant_workflow")
    op.drop_column("assistant_workflow", "published_version_id")
    op.drop_column("assistant_workflow", "draft_version_id")

    op.drop_index("ix_assistant_agent_profile_version_agent_created", table_name="assistant_agent_profile_version")
    op.drop_index("uq_assistant_agent_profile_version_seq", table_name="assistant_agent_profile_version")
    op.drop_table("assistant_agent_profile_version")

    op.drop_index("ix_assistant_workflow_version_workflow_created", table_name="assistant_workflow_version")
    op.drop_index("uq_assistant_workflow_version_seq", table_name="assistant_workflow_version")
    op.drop_table("assistant_workflow_version")

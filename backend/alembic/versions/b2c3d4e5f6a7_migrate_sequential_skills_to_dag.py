"""migrate_sequential_skills_to_dag

Revision ID: b2c3d4e5f6a7
Revises: d4e5f6a7b8c9
Create Date: 2026-02-11

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session
import uuid
from datetime import datetime, timezone


revision = "b2c3d4e5f6a7"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


# Step type -> DAG node type mapping
STEP_TYPE_MAP = {
    "analysis": "llm",
    "tool": "tool",
    "summary": "answer",
}

Y_SPACING = 120
START_X = 300


def _now():
    return datetime.now(timezone.utc)


def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)

    # Find all sequential langgraph skills that have steps
    skills = session.execute(
        sa.text(
            "SELECT id, name FROM assistant_skill "
            "WHERE mode = 'langgraph' AND langgraph_pattern = 'sequential'"
        )
    ).fetchall()

    for skill_id, skill_name in skills:
        steps = session.execute(
            sa.text(
                "SELECT id, step_order, type, instruction, tool_name, "
                "args_from, args_template, output_mode, output_fields, "
                "include_in_summary "
                "FROM assistant_skill_step "
                "WHERE skill_id = :sid ORDER BY step_order ASC"
            ),
            {"sid": skill_id},
        ).fetchall()

        if not steps:
            continue

        now = _now()
        node_ids = []

        # Create start node
        start_nid = "start_0"
        session.execute(
            sa.text(
                "INSERT INTO assistant_skill_node "
                "(id, skill_id, node_id, node_type, label, position_x, position_y, config, created_at, updated_at) "
                "VALUES (:id, :sid, :nid, 'start', 'Start', :px, :py, :cfg, :ca, :ua)"
            ),
            {
                "id": str(uuid.uuid4()), "sid": skill_id, "nid": start_nid,
                "px": START_X, "py": 0.0,
                "cfg": '{"input_schema": [{"name": "user_input", "type": "string", "required": true}]}',
                "ca": now, "ua": now,
            },
        )
        node_ids.append(start_nid)

        # Create nodes for each step
        for step in steps:
            s_order = step[1]
            s_type = step[2]
            node_type = STEP_TYPE_MAP.get(s_type, s_type)
            nid = f"{node_type}_{s_order}"

            config = _build_node_config(step, node_type)
            label = _build_label(step, node_type)

            session.execute(
                sa.text(
                    "INSERT INTO assistant_skill_node "
                    "(id, skill_id, node_id, node_type, label, position_x, position_y, config, created_at, updated_at) "
                    "VALUES (:id, :sid, :nid, :nt, :lbl, :px, :py, :cfg, :ca, :ua)"
                ),
                {
                    "id": str(uuid.uuid4()), "sid": skill_id, "nid": nid,
                    "nt": node_type, "lbl": label,
                    "px": START_X, "py": float(s_order) * Y_SPACING,
                    "cfg": config, "ca": now, "ua": now,
                },
            )
            node_ids.append(nid)

        # Create linear edges
        for i in range(len(node_ids) - 1):
            session.execute(
                sa.text(
                    "INSERT INTO assistant_skill_edge "
                    "(id, skill_id, edge_id, source_node_id, target_node_id, "
                    "source_handle, target_handle, created_at, updated_at) "
                    "VALUES (:id, :sid, :eid, :src, :tgt, 'output', 'input', :ca, :ua)"
                ),
                {
                    "id": str(uuid.uuid4()), "sid": skill_id,
                    "eid": f"edge_{i}",
                    "src": node_ids[i], "tgt": node_ids[i + 1],
                    "ca": now, "ua": now,
                },
            )

        # Update pattern to workflow_dag
        session.execute(
            sa.text(
                "UPDATE assistant_skill SET langgraph_pattern = 'workflow_dag', "
                "workflow_version = 2 WHERE id = :sid"
            ),
            {"sid": skill_id},
        )

    session.commit()


def _build_node_config(step, node_type: str) -> str:
    """Build JSON config string from step data."""
    import json

    s_instruction = step[3]
    s_tool_name = step[4]
    s_args_from = step[5]
    s_args_template = step[6]
    s_output_mode = step[7]
    s_output_fields = step[8]
    s_include_in_summary = step[9]

    if node_type == "tool":
        cfg = {"tool_name": s_tool_name}
        if s_args_from:
            cfg["args_from"] = s_args_from
        if s_args_template:
            cfg["args_template"] = s_args_template
        return json.dumps(cfg)

    if node_type == "llm":
        cfg = {"system_prompt": s_instruction or ""}
        if s_output_mode:
            cfg["output_mode"] = s_output_mode
        if s_output_fields:
            cfg["output_fields"] = s_output_fields
        return json.dumps(cfg)

    if node_type == "answer":
        cfg = {
            "answer_mode": "streaming",
            "instruction": s_instruction or "",
        }
        return json.dumps(cfg)

    return "{}"


def _build_label(step, node_type: str) -> str:
    s_tool_name = step[4]
    if node_type == "tool" and s_tool_name:
        return s_tool_name
    if node_type == "llm":
        return "LLM Analysis"
    if node_type == "answer":
        return "Answer"
    return node_type


def downgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)

    # Revert workflow_dag skills back to sequential
    session.execute(
        sa.text(
            "UPDATE assistant_skill SET langgraph_pattern = 'sequential', "
            "workflow_version = 1 "
            "WHERE langgraph_pattern = 'workflow_dag' AND mode = 'langgraph'"
        )
    )

    # Remove all nodes and edges (they were created by this migration)
    session.execute(sa.text("DELETE FROM assistant_skill_edge"))
    session.execute(sa.text("DELETE FROM assistant_skill_node"))

    session.commit()

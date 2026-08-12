"""remove_legacy_skill_modes_and_steps

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a7
Create Date: 2026-02-12

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c4d5e6f7a8b9"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None

_MODE_CHECK = "ck_assistant_skill_mode_langgraph_only"
_PATTERN_CHECK = "ck_assistant_skill_langgraph_pattern_valid"


def _drop_constraint_if_exists(table_name: str, constraint_name: str) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    checks = inspector.get_check_constraints(table_name)
    if any(c.get("name") == constraint_name for c in checks):
        op.drop_constraint(constraint_name, table_name, type_="check")


def upgrade() -> None:
    bind = op.get_bind()

    # 1) Remove legacy skills entirely (hard cutover)
    op.execute(sa.text("DELETE FROM assistant_skill WHERE mode IN ('steps', 'agent')"))

    # 2) Normalize surviving records
    op.execute(sa.text("UPDATE assistant_skill SET mode = 'langgraph'"))
    op.execute(
        sa.text(
            "UPDATE assistant_skill "
            "SET langgraph_pattern = 'agent_loop' "
            "WHERE langgraph_pattern IS NULL "
            "OR langgraph_pattern NOT IN ('agent_loop', 'workflow_dag')"
        )
    )

    # 3) DB-level constraints for the new invariant
    _drop_constraint_if_exists("assistant_skill", _MODE_CHECK)
    _drop_constraint_if_exists("assistant_skill", _PATTERN_CHECK)

    op.alter_column(
        "assistant_skill",
        "mode",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default="langgraph",
    )
    op.alter_column(
        "assistant_skill",
        "langgraph_pattern",
        existing_type=sa.String(length=32),
        nullable=False,
    )

    op.create_check_constraint(
        _MODE_CHECK,
        "assistant_skill",
        "mode = 'langgraph'",
    )
    op.create_check_constraint(
        _PATTERN_CHECK,
        "assistant_skill",
        "langgraph_pattern IN ('agent_loop', 'workflow_dag')",
    )

    # 4) Remove legacy step table
    if sa.inspect(bind).has_table("assistant_skill_step"):
        op.drop_table("assistant_skill_step")


def downgrade() -> None:
    bind = op.get_bind()

    # Recreate legacy step table shape (data is not recoverable)
    if not sa.inspect(bind).has_table("assistant_skill_step"):
        op.create_table(
            "assistant_skill_step",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("skill_id", sa.UUID(), nullable=False),
            sa.Column("step_order", sa.Integer(), nullable=False),
            sa.Column("type", sa.String(length=32), nullable=False),
            sa.Column("instruction", sa.Text(), nullable=True),
            sa.Column("tool_name", sa.String(length=128), nullable=True),
            sa.Column("args_from", sa.String(length=32), nullable=True),
            sa.Column("args_template", sa.Text(), nullable=True),
            sa.Column("output_mode", sa.String(length=16), nullable=True),
            sa.Column("output_fields", sa.JSON(), nullable=True),
            sa.Column("include_in_summary", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["skill_id"], ["assistant_skill.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_assistant_skill_step_skill_id", "assistant_skill_step", ["skill_id"])
        op.create_index(
            "uq_assistant_skill_step_skill_order",
            "assistant_skill_step",
            ["skill_id", "step_order"],
            unique=True,
        )

    _drop_constraint_if_exists("assistant_skill", _PATTERN_CHECK)
    _drop_constraint_if_exists("assistant_skill", _MODE_CHECK)

    op.alter_column(
        "assistant_skill",
        "langgraph_pattern",
        existing_type=sa.String(length=32),
        nullable=True,
    )
    op.alter_column(
        "assistant_skill",
        "mode",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default=None,
    )

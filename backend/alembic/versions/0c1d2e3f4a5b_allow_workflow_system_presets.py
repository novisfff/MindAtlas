"""allow workflow-backed openclaw system presets

Revision ID: 0c1d2e3f4a5b
Revises: b4c5d6e7f8a9
Create Date: 2026-03-28 10:30:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "0c1d2e3f4a5b"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


_TABLE_NAME = "openclaw_capability_item"
_CONSTRAINT_NAME = "ck_openclaw_capability_item_single_source"
_CONSTRAINT_SQL = (
    "("
    "source_type = 'system_adapter' "
    "AND system_capability_key IS NOT NULL "
    "AND source_tool_name IS NULL "
    "AND tool_id IS NULL "
    "AND workflow_id IS NULL "
    "AND agent_profile_id IS NULL "
    "AND is_system_preset = true"
    ") OR ("
    "source_type = 'tool' "
    "AND system_capability_key IS NULL "
    "AND workflow_id IS NULL "
    "AND agent_profile_id IS NULL "
    "AND is_system_preset = false "
    "AND (tool_id IS NOT NULL OR source_tool_name IS NOT NULL)"
    ") OR ("
    "source_type = 'workflow' "
    "AND source_tool_name IS NULL "
    "AND tool_id IS NULL "
    "AND workflow_id IS NOT NULL "
    "AND agent_profile_id IS NULL "
    "AND ("
    "(is_system_preset = true AND system_capability_key IS NOT NULL) "
    "OR (is_system_preset = false AND system_capability_key IS NULL)"
    ")"
    ") OR ("
    "source_type = 'agent' "
    "AND system_capability_key IS NULL "
    "AND source_tool_name IS NULL "
    "AND tool_id IS NULL "
    "AND workflow_id IS NULL "
    "AND agent_profile_id IS NOT NULL "
    "AND is_system_preset = false"
    ")"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, _TABLE_NAME, type_="check")
    op.create_check_constraint(_CONSTRAINT_NAME, _TABLE_NAME, _CONSTRAINT_SQL)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, _TABLE_NAME, type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        _TABLE_NAME,
        "("
        "source_type = 'system_adapter' "
        "AND system_capability_key IS NOT NULL "
        "AND source_tool_name IS NULL "
        "AND tool_id IS NULL "
        "AND workflow_id IS NULL "
        "AND agent_profile_id IS NULL"
        ") OR ("
        "source_type = 'tool' "
        "AND system_capability_key IS NULL "
        "AND workflow_id IS NULL "
        "AND agent_profile_id IS NULL "
        "AND (tool_id IS NOT NULL OR source_tool_name IS NOT NULL)"
        ") OR ("
        "source_type = 'workflow' "
        "AND system_capability_key IS NULL "
        "AND source_tool_name IS NULL "
        "AND tool_id IS NULL "
        "AND workflow_id IS NOT NULL "
        "AND agent_profile_id IS NULL"
        ") OR ("
        "source_type = 'agent' "
        "AND system_capability_key IS NULL "
        "AND source_tool_name IS NULL "
        "AND tool_id IS NULL "
        "AND workflow_id IS NULL "
        "AND agent_profile_id IS NOT NULL"
        ")",
    )

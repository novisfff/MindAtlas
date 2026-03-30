"""refactor openclaw system items to first-class bindings

Revision ID: c6d7e8f9a0b1
Revises: 0c1d2e3f4a5b
Create Date: 2026-03-30 14:30:00.000000

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "c6d7e8f9a0b1"
down_revision = "0c1d2e3f4a5b"
branch_labels = None
depends_on = None


_TABLE_NAME = "openclaw_capability_item"
_TOOL_SOURCE_BY_DEFAULT_KEY = {
    "capture_entry": "openclaw_capture_entry",
    "search_entries": "openclaw_search_entries",
    "get_entry": "openclaw_get_entry",
    "create_relation": "openclaw_create_relation",
    "query_knowledge_graph": "openclaw_query_knowledge_graph",
    "generate_weekly_report": "openclaw_generate_weekly_report",
    "generate_monthly_report": "openclaw_generate_monthly_report",
}

_NEW_SINGLE_SOURCE_CONSTRAINT = (
    "("
    "source_type = 'tool' "
    "AND workflow_id IS NULL "
    "AND agent_profile_id IS NULL "
    "AND (tool_id IS NOT NULL OR source_tool_name IS NOT NULL)"
    ") OR ("
    "source_type = 'workflow' "
    "AND source_tool_name IS NULL "
    "AND tool_id IS NULL "
    "AND workflow_id IS NOT NULL "
    "AND agent_profile_id IS NULL"
    ") OR ("
    "source_type = 'agent' "
    "AND source_tool_name IS NULL "
    "AND tool_id IS NULL "
    "AND workflow_id IS NULL "
    "AND agent_profile_id IS NOT NULL"
    ")"
)

_OLD_SINGLE_SOURCE_CONSTRAINT = (
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
    op.drop_constraint("ck_openclaw_capability_item_single_source", _TABLE_NAME, type_="check")
    op.drop_constraint("ck_openclaw_capability_item_source_type", _TABLE_NAME, type_="check")
    op.drop_index("ix_openclaw_capability_item_system_capability_key", table_name=_TABLE_NAME)

    op.alter_column(_TABLE_NAME, "system_capability_key", new_column_name="system_default_key")
    op.alter_column(_TABLE_NAME, "is_system_preset", new_column_name="is_system_item")

    case_sql = " ".join(
        f"WHEN '{key}' THEN '{source_tool}'"
        for key, source_tool in _TOOL_SOURCE_BY_DEFAULT_KEY.items()
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {_TABLE_NAME}
            SET
                source_type = 'tool',
                source_tool_name = CASE system_default_key {case_sql} ELSE source_tool_name END,
                tool_id = NULL,
                workflow_id = NULL,
                agent_profile_id = NULL
            WHERE source_type = 'system_adapter'
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {_TABLE_NAME}
            SET is_system_item = TRUE
            WHERE system_default_key IS NOT NULL
            """
        )
    )

    op.create_check_constraint(
        "ck_openclaw_capability_item_source_type",
        _TABLE_NAME,
        "source_type IN ('tool','workflow','agent')",
    )
    op.create_check_constraint(
        "ck_openclaw_capability_item_single_source",
        _TABLE_NAME,
        _NEW_SINGLE_SOURCE_CONSTRAINT,
    )
    op.create_check_constraint(
        "ck_openclaw_capability_item_system_default_key_requires_system_item",
        _TABLE_NAME,
        "(system_default_key IS NULL) OR (is_system_item = true)",
    )
    op.create_index(
        "ix_openclaw_capability_item_system_default_key",
        _TABLE_NAME,
        ["system_default_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_openclaw_capability_item_system_default_key", table_name=_TABLE_NAME)
    op.drop_constraint(
        "ck_openclaw_capability_item_system_default_key_requires_system_item",
        _TABLE_NAME,
        type_="check",
    )
    op.drop_constraint("ck_openclaw_capability_item_single_source", _TABLE_NAME, type_="check")
    op.drop_constraint("ck_openclaw_capability_item_source_type", _TABLE_NAME, type_="check")

    op.alter_column(_TABLE_NAME, "system_default_key", new_column_name="system_capability_key")
    op.alter_column(_TABLE_NAME, "is_system_item", new_column_name="is_system_preset")

    tool_names_sql = ", ".join(f"'{name}'" for name in _TOOL_SOURCE_BY_DEFAULT_KEY.values())
    op.execute(
        sa.text(
            f"""
            UPDATE {_TABLE_NAME}
            SET
                source_type = 'system_adapter',
                source_tool_name = NULL,
                tool_id = NULL,
                workflow_id = NULL,
                agent_profile_id = NULL
            WHERE
                source_type = 'tool'
                AND system_capability_key IS NOT NULL
                AND source_tool_name IN ({tool_names_sql})
            """
        )
    )

    op.create_check_constraint(
        "ck_openclaw_capability_item_source_type",
        _TABLE_NAME,
        "source_type IN ('system_adapter','tool','workflow','agent')",
    )
    op.create_check_constraint(
        "ck_openclaw_capability_item_single_source",
        _TABLE_NAME,
        _OLD_SINGLE_SOURCE_CONSTRAINT,
    )
    op.create_index(
        "ix_openclaw_capability_item_system_capability_key",
        _TABLE_NAME,
        ["system_capability_key"],
        unique=False,
    )

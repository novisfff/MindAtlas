"""drop assistant_skill table

Revision ID: 5cc5a70095f9
Revises: ca6f564ef4bd
Create Date: 2026-07-23

Plan 10 residual B2: drop legacy ``assistant_skill`` after packages/profile
own published targets. Detaches package/profile ``legacy_skill_id`` FKs while
retaining UUID provenance columns (no FK). Requires maintenance ack.
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "5cc5a70095f9"
down_revision = "ca6f564ef4bd"
branch_labels = None
depends_on = None

B2_MAINTENANCE_ACK_ENV = "MINDATLAS_PLAN10_B2_MAINTENANCE_ACK"
B2_TEST_OVERRIDE_ENV = "MINDATLAS_PLAN10_B2_TEST_OVERRIDE"
PREFLIGHT_BLOCKED_TOKEN = "MINDATLAS_PLAN10_B2_SKILL_DROP_BLOCKED"
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN10_B2_SKILL_DROP_DOWNGRADE_BLOCKED"
DOWNGRADE_ACK_ENV = "MINDATLAS_PLAN10_B2_SKILL_DROP_DOWNGRADE_ACK"


def _maintenance_ack_present() -> bool:
    ack = str(os.environ.get(B2_MAINTENANCE_ACK_ENV, "") or "").strip()
    test_override = str(os.environ.get(B2_TEST_OVERRIDE_ENV, "") or "").strip()
    return ack in {"1", "true", "TRUE", "yes", "YES"} or test_override in {
        "1",
        "true",
        "TRUE",
        "yes",
        "YES",
    }


def _table_exists(connection, name: str) -> bool:
    row = connection.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :name"
        ),
        {"name": name},
    ).fetchone()
    return row is not None


def _fk_exists(connection, table: str, name: str) -> bool:
    row = connection.execute(
        sa.text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE table_schema = current_schema() "
            "AND table_name = :table AND constraint_name = :name "
            "AND constraint_type = 'FOREIGN KEY'"
        ),
        {"table": table, "name": name},
    ).fetchone()
    return row is not None


def _index_exists(connection, name: str) -> bool:
    row = connection.execute(
        sa.text(
            "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() "
            "AND indexname = :name"
        ),
        {"name": name},
    ).fetchone()
    return row is not None


def _column_exists(connection, table: str, column: str) -> bool:
    row = connection.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).fetchone()
    return row is not None


def _drop_fk_if_exists(connection, table: str, name: str) -> None:
    if _fk_exists(connection, table, name):
        op.drop_constraint(name, table, type_="foreignkey")


def _preflight(connection) -> None:
    if not _maintenance_ack_present():
        raise RuntimeError(
            f"{PREFLIGHT_BLOCKED_TOKEN}: set {B2_MAINTENANCE_ACK_ENV}=1 "
            f"(or {B2_TEST_OVERRIDE_ENV}=1 for tests)"
        )
    if not _table_exists(connection, "assistant_skill"):
        return
    if _table_exists(connection, "assistant_human_approval"):
        raise RuntimeError(
            f"{PREFLIGHT_BLOCKED_TOKEN}: assistant_human_approval still present; "
            "apply ca6f564ef4bd first"
        )


def upgrade() -> None:
    conn = op.get_bind()
    _preflight(conn)

    if not _table_exists(conn, "assistant_skill"):
        return

    for table, fk_name in (
        ("assistant_skill_package", "assistant_skill_package_legacy_skill_id_fkey"),
        ("assistant_skill_package", "fk_assistant_skill_package_legacy_skill_id"),
        (
            "assistant_main_agent_profile",
            "assistant_main_agent_profile_legacy_skill_id_fkey",
        ),
        (
            "assistant_main_agent_profile",
            "fk_assistant_main_agent_profile_legacy_skill_id",
        ),
    ):
        if _table_exists(conn, table):
            _drop_fk_if_exists(conn, table, fk_name)

    fks = conn.execute(
        sa.text(
            """
            SELECT tc.table_name, tc.constraint_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = current_schema()
              AND ccu.table_name = 'assistant_skill'
            """
        )
    ).fetchall()
    for table_name, constraint_name in fks:
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")

    for idx in (
        "ix_assistant_skill_name",
        "ix_assistant_skill_workflow_id",
        "ix_assistant_skill_agent_profile_id",
    ):
        if _index_exists(conn, idx):
            op.drop_index(idx, table_name="assistant_skill")

    op.drop_table("assistant_skill")


def downgrade() -> None:
    """Recreate empty assistant_skill skeleton only — does not restore rows."""
    ack = str(os.environ.get(DOWNGRADE_ACK_ENV, "") or "").strip()
    if ack not in {"1", "true", "TRUE", "yes", "YES"}:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: set {DOWNGRADE_ACK_ENV}=1 to recreate "
            "empty assistant_skill structure (data not restored)"
        )

    conn = op.get_bind()
    if _table_exists(conn, "assistant_skill"):
        return

    op.create_table(
        "assistant_skill",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("intent_examples", sa.JSON(), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("langgraph_pattern", sa.String(length=32), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("kb_config", sa.JSON(), nullable=True),
        sa.Column("workflow_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("workflow_viewport", sa.JSON(), nullable=True),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_assistant_skill_name", "assistant_skill", ["name"], unique=True)

    if _table_exists(conn, "assistant_skill_package") and _column_exists(
        conn, "assistant_skill_package", "legacy_skill_id"
    ):
        op.create_foreign_key(
            "assistant_skill_package_legacy_skill_id_fkey",
            "assistant_skill_package",
            "assistant_skill",
            ["legacy_skill_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if _table_exists(conn, "assistant_main_agent_profile") and _column_exists(
        conn, "assistant_main_agent_profile", "legacy_skill_id"
    ):
        op.create_foreign_key(
            "assistant_main_agent_profile_legacy_skill_id_fkey",
            "assistant_main_agent_profile",
            "assistant_skill",
            ["legacy_skill_id"],
            ["id"],
            ondelete="SET NULL",
        )

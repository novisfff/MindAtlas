"""drop legacy_skill_id provenance columns

Revision ID: d3a9fcac15c7
Revises: 5cc5a70095f9
Create Date: 2026-07-23

Plan 10 residual: after assistant_skill table drop, remove leftover
package/profile ``legacy_skill_id`` UUID provenance columns.
Requires maintenance ack (same as other B2 destructive steps).
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d3a9fcac15c7"
down_revision = "5cc5a70095f9"
branch_labels = None
depends_on = None

B2_MAINTENANCE_ACK_ENV = "MINDATLAS_PLAN10_B2_MAINTENANCE_ACK"
B2_TEST_OVERRIDE_ENV = "MINDATLAS_PLAN10_B2_TEST_OVERRIDE"
PREFLIGHT_BLOCKED_TOKEN = "MINDATLAS_PLAN10_B2_LEGACY_ID_DROP_BLOCKED"
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN10_B2_LEGACY_ID_DROP_DOWNGRADE_BLOCKED"
DOWNGRADE_ACK_ENV = "MINDATLAS_PLAN10_B2_LEGACY_ID_DROP_DOWNGRADE_ACK"


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


def upgrade() -> None:
    if not _maintenance_ack_present():
        raise RuntimeError(
            f"{PREFLIGHT_BLOCKED_TOKEN}: set {B2_MAINTENANCE_ACK_ENV}=1 "
            f"(or {B2_TEST_OVERRIDE_ENV}=1 for tests)"
        )
    # Use IF EXISTS so unique constraints that own indexes drop cleanly.
    op.execute(
        sa.text(
            "ALTER TABLE IF EXISTS assistant_skill_package "
            "DROP CONSTRAINT IF EXISTS assistant_skill_package_legacy_skill_id_key"
        )
    )
    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS ix_assistant_skill_package_legacy_skill_id"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE IF EXISTS assistant_skill_package "
            "DROP COLUMN IF EXISTS legacy_skill_id"
        )
    )

    op.execute(
        sa.text(
            "ALTER TABLE IF EXISTS assistant_main_agent_profile "
            "DROP CONSTRAINT IF EXISTS assistant_main_agent_profile_legacy_skill_id_key"
        )
    )
    op.execute(
        sa.text(
            "DROP INDEX IF EXISTS ix_assistant_main_agent_profile_legacy_skill_id"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE IF EXISTS assistant_main_agent_profile "
            "DROP COLUMN IF EXISTS legacy_skill_id"
        )
    )


def downgrade() -> None:
    ack = str(os.environ.get(DOWNGRADE_ACK_ENV, "") or "").strip()
    if ack not in {"1", "true", "TRUE", "yes", "YES"}:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: set {DOWNGRADE_ACK_ENV}=1 to recreate "
            "empty legacy_skill_id columns (no data restored)"
        )
    conn = op.get_bind()

    def _column_exists(table: str, column: str) -> bool:
        row = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).fetchone()
        return row is not None

    def _table_exists(name: str) -> bool:
        row = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = :name"
            ),
            {"name": name},
        ).fetchone()
        return row is not None

    if _table_exists("assistant_skill_package") and not _column_exists(
        "assistant_skill_package", "legacy_skill_id"
    ):
        op.add_column(
            "assistant_skill_package",
            sa.Column("legacy_skill_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_index(
            "ix_assistant_skill_package_legacy_skill_id",
            "assistant_skill_package",
            ["legacy_skill_id"],
            unique=True,
        )
    if _table_exists("assistant_main_agent_profile") and not _column_exists(
        "assistant_main_agent_profile", "legacy_skill_id"
    ):
        op.add_column(
            "assistant_main_agent_profile",
            sa.Column("legacy_skill_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_index(
            "ix_assistant_main_agent_profile_legacy_skill_id",
            "assistant_main_agent_profile",
            ["legacy_skill_id"],
            unique=True,
        )

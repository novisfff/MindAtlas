"""drop legacy_source_digest provenance columns

Revision ID: 3bd7bc4257c9
Revises: d3a9fcac15c7
Create Date: 2026-07-27

Plan 10 residual: drop package/profile ``legacy_source_digest`` columns and
related SHA-256 check constraints. Digests may still be computed in memory for
migration item evidence without DB persistence.
Requires maintenance ack (same as other B2 destructive steps).
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa


revision = "3bd7bc4257c9"
down_revision = "d3a9fcac15c7"
branch_labels = None
depends_on = None

B2_MAINTENANCE_ACK_ENV = "MINDATLAS_PLAN10_B2_MAINTENANCE_ACK"
B2_TEST_OVERRIDE_ENV = "MINDATLAS_PLAN10_B2_TEST_OVERRIDE"
PREFLIGHT_BLOCKED_TOKEN = "MINDATLAS_PLAN10_B2_LEGACY_DIGEST_DROP_BLOCKED"
DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN10_B2_LEGACY_DIGEST_DROP_DOWNGRADE_BLOCKED"
DOWNGRADE_ACK_ENV = "MINDATLAS_PLAN10_B2_LEGACY_DIGEST_DROP_DOWNGRADE_ACK"

_SHA256 = r"^[0-9a-f]{64}$"


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

    op.execute(
        sa.text(
            "ALTER TABLE IF EXISTS assistant_skill_package "
            "DROP CONSTRAINT IF EXISTS ck_assistant_skill_package_legacy_source_digest"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE IF EXISTS assistant_skill_package "
            "DROP COLUMN IF EXISTS legacy_source_digest"
        )
    )

    op.execute(
        sa.text(
            "ALTER TABLE IF EXISTS assistant_main_agent_profile "
            "DROP CONSTRAINT IF EXISTS ck_assistant_main_agent_profile_legacy_source_digest"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE IF EXISTS assistant_main_agent_profile "
            "DROP COLUMN IF EXISTS legacy_source_digest"
        )
    )


def downgrade() -> None:
    ack = str(os.environ.get(DOWNGRADE_ACK_ENV, "") or "").strip()
    if ack not in {"1", "true", "TRUE", "yes", "YES"}:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: set {DOWNGRADE_ACK_ENV}=1 to recreate "
            "empty legacy_source_digest columns (no data restored)"
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
        "assistant_skill_package", "legacy_source_digest"
    ):
        op.add_column(
            "assistant_skill_package",
            sa.Column("legacy_source_digest", sa.String(length=64), nullable=True),
        )
        op.create_check_constraint(
            "ck_assistant_skill_package_legacy_source_digest",
            "assistant_skill_package",
            f"legacy_source_digest IS NULL OR legacy_source_digest ~ '{_SHA256}'",
        )
    if _table_exists("assistant_main_agent_profile") and not _column_exists(
        "assistant_main_agent_profile", "legacy_source_digest"
    ):
        op.add_column(
            "assistant_main_agent_profile",
            sa.Column("legacy_source_digest", sa.String(length=64), nullable=True),
        )
        op.create_check_constraint(
            "ck_assistant_main_agent_profile_legacy_source_digest",
            "assistant_main_agent_profile",
            f"legacy_source_digest IS NULL OR legacy_source_digest ~ '{_SHA256}'",
        )

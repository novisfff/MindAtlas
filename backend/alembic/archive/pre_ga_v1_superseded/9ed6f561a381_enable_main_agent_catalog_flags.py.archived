"""enable main agent catalog flags

Revision ID: 9ed6f561a381
Revises: b666b11a5faa
Create Date: 2026-07-14 11:47:29.686824

Plan 04 Task 1: drop Plan 01 disabled-only CHECK constraints so Skill package
catalog_enabled and Main Agent Profile runtime_enabled may later be set true.
Defaults remain false (NOT NULL + server_default). No data mutation.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9ed6f561a381"
down_revision = "b666b11a5faa"
branch_labels = None
depends_on = None

DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN04_DOWNGRADE_BLOCKED_ENABLED_AGGREGATES"

CATALOG_CHECK = "ck_assistant_skill_package_catalog_disabled"
RUNTIME_CHECK = "ck_assistant_main_agent_profile_runtime_disabled"


def upgrade() -> None:
    op.drop_constraint(
        CATALOG_CHECK,
        "assistant_skill_package",
        type_="check",
    )
    op.drop_constraint(
        RUNTIME_CHECK,
        "assistant_main_agent_profile",
        type_="check",
    )


def downgrade() -> None:
    conn = op.get_bind()
    enabled_packages = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM assistant_skill_package WHERE catalog_enabled = true"
        )
    ).scalar()
    enabled_profiles = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM assistant_main_agent_profile WHERE runtime_enabled = true"
        )
    ).scalar()
    enabled_packages = int(enabled_packages or 0)
    enabled_profiles = int(enabled_profiles or 0)
    if enabled_packages > 0 or enabled_profiles > 0:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: disable catalog_enabled / runtime_enabled "
            f"first (packages={enabled_packages}, profiles={enabled_profiles}); "
            "no aggregate data is deleted by this migration"
        )

    op.create_check_constraint(
        CATALOG_CHECK,
        "assistant_skill_package",
        "catalog_enabled = false",
    )
    op.create_check_constraint(
        RUNTIME_CHECK,
        "assistant_main_agent_profile",
        "runtime_enabled = false",
    )

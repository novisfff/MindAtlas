"""add skill package admin lifecycle

Revision ID: 403414a62e55
Revises: d7e8f9a0b1c3
Create Date: 2026-07-19 23:26:46.342434

Plan 09 Task 1: aggregate revision CAS, archive/catalog evidence columns on
``assistant_skill_package``, and soft-disable columns on
``assistant_skill_package_alias``. Additive/backfill/finalize only; 09A must
roll back independently of evaluation schema.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "403414a62e55"
down_revision = "d7e8f9a0b1c3"
branch_labels = None
depends_on = None


DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN09_DOWNGRADE_BLOCKED_ARCHIVED_OR_CATALOG_EVIDENCE"


def upgrade() -> None:
    # --- Phase 1: additive nullable / defaulted columns ---
    op.add_column(
        "assistant_skill_package",
        sa.Column(
            "aggregate_revision",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "assistant_skill_package",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assistant_skill_package",
        sa.Column("archived_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "assistant_skill_package",
        sa.Column("catalog_enabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assistant_skill_package",
        sa.Column("catalog_enabled_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "assistant_skill_package",
        sa.Column("last_admin_request_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "assistant_skill_package",
        sa.Column("last_admin_request_digest", sa.String(length=64), nullable=True),
    )

    op.add_column(
        "assistant_skill_package_alias",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assistant_skill_package_alias",
        sa.Column("disabled_by", sa.String(length=128), nullable=True),
    )

    # --- Phase 2: backfill existing rows ---
    op.execute(
        sa.text(
            "UPDATE assistant_skill_package "
            "SET aggregate_revision = 0 "
            "WHERE aggregate_revision IS NULL"
        )
    )

    # --- Phase 3: finalize NOT NULL + server_default + checks ---
    op.alter_column(
        "assistant_skill_package",
        "aggregate_revision",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )

    op.create_check_constraint(
        "ck_assistant_skill_package_aggregate_revision",
        "assistant_skill_package",
        "aggregate_revision >= 0",
    )
    op.create_check_constraint(
        "ck_assistant_skill_package_archived_shape",
        "assistant_skill_package",
        "(archived_at IS NULL AND archived_by IS NULL) OR (archived_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_assistant_skill_package_last_admin_request_digest",
        "assistant_skill_package",
        "last_admin_request_digest IS NULL OR length(last_admin_request_digest) = 64",
    )
    op.create_check_constraint(
        "ck_assistant_skill_package_alias_disabled_shape",
        "assistant_skill_package_alias",
        "(disabled_at IS NULL AND disabled_by IS NULL) OR "
        "(disabled_at IS NOT NULL AND alias_type = 'custom')",
    )


def downgrade() -> None:
    conn = op.get_bind()
    archived = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM assistant_skill_package WHERE archived_at IS NOT NULL"
        )
    ).scalar()
    catalog_evidence = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM assistant_skill_package "
            "WHERE catalog_enabled_at IS NOT NULL OR catalog_enabled_by IS NOT NULL"
        )
    ).scalar()
    disabled_aliases = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM assistant_skill_package_alias "
            "WHERE disabled_at IS NOT NULL"
        )
    ).scalar()
    archived = int(archived or 0)
    catalog_evidence = int(catalog_evidence or 0)
    disabled_aliases = int(disabled_aliases or 0)
    if archived > 0 or catalog_evidence > 0 or disabled_aliases > 0:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: clear archive/catalog/alias disable "
            f"evidence first (archived={archived}, catalog_evidence={catalog_evidence}, "
            f"disabled_aliases={disabled_aliases}); no aggregate data is deleted"
        )

    op.drop_constraint(
        "ck_assistant_skill_package_alias_disabled_shape",
        "assistant_skill_package_alias",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_skill_package_last_admin_request_digest",
        "assistant_skill_package",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_skill_package_archived_shape",
        "assistant_skill_package",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_skill_package_aggregate_revision",
        "assistant_skill_package",
        type_="check",
    )

    op.drop_column("assistant_skill_package_alias", "disabled_by")
    op.drop_column("assistant_skill_package_alias", "disabled_at")
    op.drop_column("assistant_skill_package", "last_admin_request_digest")
    op.drop_column("assistant_skill_package", "last_admin_request_id")
    op.drop_column("assistant_skill_package", "catalog_enabled_by")
    op.drop_column("assistant_skill_package", "catalog_enabled_at")
    op.drop_column("assistant_skill_package", "archived_by")
    op.drop_column("assistant_skill_package", "archived_at")
    op.drop_column("assistant_skill_package", "aggregate_revision")

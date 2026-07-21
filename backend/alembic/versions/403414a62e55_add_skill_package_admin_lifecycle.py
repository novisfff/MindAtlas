"""add skill package admin lifecycle

Revision ID: 403414a62e55
Revises: d7e8f9a0b1c3
Create Date: 2026-07-19 23:26:46.342434

Plan 09 Task 1 / remediation Task 2+3: aggregate revision CAS, archive/catalog
evidence columns on ``assistant_skill_package``, soft-disable columns on
``assistant_skill_package_alias``, matching Profile CAS columns on
``assistant_main_agent_profile``, and durable
``assistant_skill_import_preview`` storage for cross-worker import preview/apply.
Additive/backfill/finalize only; 09A must roll back independently of evaluation
schema.

Also installs the column-aware alias soft-disable UPDATE guard so 09A alone
can write ``disabled_at`` / ``disabled_by`` without depending on the 09B
evaluation workbench. DELETE remains fully rejected by Plan 01.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = "403414a62e55"
down_revision = "d7e8f9a0b1c3"
branch_labels = None
depends_on = None


DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN09_DOWNGRADE_BLOCKED_ARCHIVED_OR_CATALOG_EVIDENCE"

ALIAS_TABLE = "assistant_skill_package_alias"
ALIAS_SOFT_DISABLE_FN = "mindatlas_skill_package_alias_soft_disable_guard"
ALIAS_UPDATE_TRG = "trg_assistant_skill_package_alias_reject_update"

IMPORT_PREVIEW_TABLE = "assistant_skill_import_preview"


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
    # Restore provenance on the package aggregate (no FK — avoids circular
    # dependency with assistant_skill_version). Never written onto immutable
    # version rows when content-digest reuses an existing save row.
    op.add_column(
        "assistant_skill_package",
        sa.Column("last_restored_from_version_id", sa.UUID(), nullable=True),
    )

    op.add_column(
        "assistant_skill_package_alias",
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assistant_skill_package_alias",
        sa.Column("disabled_by", sa.String(length=128), nullable=True),
    )

    # Profile aggregate CAS columns (same shape as skill packages).
    op.add_column(
        "assistant_main_agent_profile",
        sa.Column("aggregate_revision", sa.Integer(), nullable=True),
    )
    op.add_column(
        "assistant_main_agent_profile",
        sa.Column("last_admin_request_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "assistant_main_agent_profile",
        sa.Column("last_admin_request_digest", sa.String(length=64), nullable=True),
    )

    # --- Phase 2: backfill existing rows ---
    op.execute(
        sa.text(
            "UPDATE assistant_skill_package "
            "SET aggregate_revision = 0 "
            "WHERE aggregate_revision IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE assistant_main_agent_profile "
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
    op.alter_column(
        "assistant_main_agent_profile",
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
    op.create_check_constraint(
        "ck_assistant_main_agent_profile_aggregate_revision",
        "assistant_main_agent_profile",
        "aggregate_revision >= 0",
    )
    op.create_check_constraint(
        "ck_assistant_main_agent_profile_last_admin_request_digest",
        "assistant_main_agent_profile",
        "last_admin_request_digest IS NULL OR length(last_admin_request_digest) = 64",
    )

    # Durable import preview store (cross-worker / restart-safe).
    # Drop any partial leftover from interrupted rewrite cycles, then recreate.
    op.execute(sa.text(f"DROP TABLE IF EXISTS {IMPORT_PREVIEW_TABLE} CASCADE"))
    op.create_table(
        IMPORT_PREVIEW_TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("principal_id", sa.String(length=128), nullable=False),
        sa.Column("principal_role", sa.String(length=32), nullable=False),
        sa.Column("actor_scope_digest", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("target_package_id", UUID(as_uuid=True), nullable=True),
        sa.Column("expected_aggregate_revision", sa.Integer(), nullable=True),
        sa.Column("candidate_canonical_name", sa.String(length=64), nullable=False),
        sa.Column("fork_canonical_name", sa.String(length=64), nullable=True),
        sa.Column("upload_digest", sa.String(length=64), nullable=False),
        sa.Column("candidate_content_digest", sa.String(length=64), nullable=False),
        sa.Column("preview_digest", sa.String(length=64), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("structural_diff", sa.JSON(), nullable=False),
        sa.Column("resource_index", sa.JSON(), nullable=False),
        sa.Column("capability_keys", sa.JSON(), nullable=False),
        sa.Column("archive_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "consumed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("applied_package_id", UUID(as_uuid=True), nullable=True),
        sa.Column("applied_request_id", sa.String(length=128), nullable=True),
        sa.Column("applied_request_digest", sa.String(length=64), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ('create', 'append_to_existing', 'fork_as_new')",
            name="ck_assistant_skill_import_preview_mode",
        ),
        sa.CheckConstraint(
            "principal_role IN ('operator', 'viewer')",
            name="ck_assistant_skill_import_preview_principal_role",
        ),
        sa.CheckConstraint(
            "length(actor_scope_digest) = 64",
            name="ck_assistant_skill_import_preview_actor_scope_digest",
        ),
        sa.CheckConstraint(
            "length(upload_digest) = 64",
            name="ck_assistant_skill_import_preview_upload_digest",
        ),
        sa.CheckConstraint(
            "length(candidate_content_digest) = 64",
            name="ck_assistant_skill_import_preview_content_digest",
        ),
        sa.CheckConstraint(
            "length(preview_digest) = 64",
            name="ck_assistant_skill_import_preview_preview_digest",
        ),
        sa.CheckConstraint(
            "applied_request_digest IS NULL OR length(applied_request_digest) = 64",
            name="ck_assistant_skill_import_preview_applied_request_digest",
        ),
        sa.CheckConstraint(
            "("
            "mode = 'create' AND target_package_id IS NULL "
            "AND expected_aggregate_revision IS NULL "
            "AND fork_canonical_name IS NULL"
            ") OR ("
            "mode = 'append_to_existing' AND target_package_id IS NOT NULL "
            "AND expected_aggregate_revision IS NOT NULL "
            "AND expected_aggregate_revision >= 0 "
            "AND fork_canonical_name IS NULL"
            ") OR ("
            "mode = 'fork_as_new' AND target_package_id IS NULL "
            "AND expected_aggregate_revision IS NULL "
            "AND fork_canonical_name IS NOT NULL"
            ")",
            name="ck_assistant_skill_import_preview_mode_target_shape",
        ),
        sa.CheckConstraint(
            "NOT (consumed AND archive_bytes IS NOT NULL)",
            name="ck_assistant_skill_import_preview_consumed_archive_xor",
        ),
        sa.CheckConstraint(
            "("
            "NOT consumed AND applied_package_id IS NULL "
            "AND applied_request_id IS NULL AND applied_request_digest IS NULL "
            "AND applied_at IS NULL"
            ") OR ("
            "consumed AND applied_package_id IS NOT NULL "
            "AND applied_request_id IS NOT NULL "
            "AND applied_request_digest IS NOT NULL "
            "AND applied_at IS NOT NULL"
            ")",
            name="ck_assistant_skill_import_preview_consumed_shape",
        ),
    )
    op.create_index(
        "ix_assistant_skill_import_preview_target_package_id",
        IMPORT_PREVIEW_TABLE,
        ["target_package_id"],
    )
    op.create_index(
        "ix_assistant_skill_import_preview_expires_at",
        IMPORT_PREVIEW_TABLE,
        ["expires_at"],
    )
    op.create_index(
        "ix_assistant_skill_import_preview_applied_package_id",
        IMPORT_PREVIEW_TABLE,
        ["applied_package_id"],
    )
    op.create_index(
        "ix_assistant_skill_import_preview_expires_archive",
        IMPORT_PREVIEW_TABLE,
        ["expires_at"],
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_assistant_skill_import_preview_applied_request_id "
            f"ON {IMPORT_PREVIEW_TABLE} (applied_request_id) "
            "WHERE applied_request_id IS NOT NULL"
        )
    )

    # Plan 01 marked assistant_skill_package_alias fully immutable, but soft
    # disable writes disabled_at / disabled_by. Replace the full-reject UPDATE
    # trigger on that table only with a column-aware guard. DELETE stays
    # fully rejected via Plan 01 mindatlas_reject_immutable_mutation().
    op.execute(f"DROP TRIGGER IF EXISTS {ALIAS_UPDATE_TRG} ON {ALIAS_TABLE}")
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {ALIAS_SOFT_DISABLE_FN}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            -- Identity / naming columns remain immutable.
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.skill_package_id IS DISTINCT FROM OLD.skill_package_id
               OR NEW.alias IS DISTINCT FROM OLD.alias
               OR NEW.normalized_alias IS DISTINCT FROM OLD.normalized_alias
               OR NEW.alias_type IS DISTINCT FROM OLD.alias_type
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN01_IMMUTABLE_ROW: UPDATE on % identity/name columns is not allowed',
                    TG_TABLE_NAME
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            -- Only disabled_at / disabled_by (soft-disable) may change.
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {ALIAS_UPDATE_TRG}
        BEFORE UPDATE ON {ALIAS_TABLE}
        FOR EACH ROW
        EXECUTE PROCEDURE {ALIAS_SOFT_DISABLE_FN}();
        """
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

    # Restore Plan 01 full-reject UPDATE trigger before dropping soft-disable
    # columns. The soft-disable function is no longer needed after rollback.
    op.execute(f"DROP TRIGGER IF EXISTS {ALIAS_UPDATE_TRG} ON {ALIAS_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {ALIAS_SOFT_DISABLE_FN}()")
    op.execute(
        f"""
        CREATE TRIGGER {ALIAS_UPDATE_TRG}
        BEFORE UPDATE ON {ALIAS_TABLE}
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_reject_immutable_mutation();
        """
    )

    op.drop_index(
        "uq_assistant_skill_import_preview_applied_request_id",
        table_name=IMPORT_PREVIEW_TABLE,
        if_exists=True,
    )
    op.drop_index(
        "ix_assistant_skill_import_preview_expires_archive",
        table_name=IMPORT_PREVIEW_TABLE,
        if_exists=True,
    )
    op.drop_index(
        "ix_assistant_skill_import_preview_applied_package_id",
        table_name=IMPORT_PREVIEW_TABLE,
        if_exists=True,
    )
    op.drop_index(
        "ix_assistant_skill_import_preview_expires_at",
        table_name=IMPORT_PREVIEW_TABLE,
        if_exists=True,
    )
    op.drop_index(
        "ix_assistant_skill_import_preview_target_package_id",
        table_name=IMPORT_PREVIEW_TABLE,
        if_exists=True,
    )
    op.drop_table(IMPORT_PREVIEW_TABLE, if_exists=True)

    # IF EXISTS: disposable DBs may be mid-rewrite with partial 09A objects.
    def _drop_check(table: str, name: str) -> None:
        op.execute(
            sa.text(
                f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}"
            )
        )

    def _drop_col(table: str, column: str) -> None:
        op.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}"))

    _drop_check(
        "assistant_main_agent_profile",
        "ck_assistant_main_agent_profile_last_admin_request_digest",
    )
    _drop_check(
        "assistant_main_agent_profile",
        "ck_assistant_main_agent_profile_aggregate_revision",
    )
    _drop_check(
        "assistant_skill_package_alias",
        "ck_assistant_skill_package_alias_disabled_shape",
    )
    _drop_check(
        "assistant_skill_package",
        "ck_assistant_skill_package_last_admin_request_digest",
    )
    _drop_check(
        "assistant_skill_package",
        "ck_assistant_skill_package_archived_shape",
    )
    _drop_check(
        "assistant_skill_package",
        "ck_assistant_skill_package_aggregate_revision",
    )

    _drop_col("assistant_main_agent_profile", "last_admin_request_digest")
    _drop_col("assistant_main_agent_profile", "last_admin_request_id")
    _drop_col("assistant_main_agent_profile", "aggregate_revision")
    _drop_col("assistant_skill_package_alias", "disabled_by")
    _drop_col("assistant_skill_package_alias", "disabled_at")
    _drop_col("assistant_skill_package", "last_restored_from_version_id")
    _drop_col("assistant_skill_package", "last_admin_request_digest")
    _drop_col("assistant_skill_package", "last_admin_request_id")
    _drop_col("assistant_skill_package", "catalog_enabled_by")
    _drop_col("assistant_skill_package", "catalog_enabled_at")
    _drop_col("assistant_skill_package", "archived_by")
    _drop_col("assistant_skill_package", "archived_at")
    _drop_col("assistant_skill_package", "aggregate_revision")

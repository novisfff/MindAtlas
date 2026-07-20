"""allow skill package alias soft-disable updates

Revision ID: 24f1e06fdd9e
Revises: 027869a00a47
Create Date: 2026-07-20 17:40:00.000000

Plan 09 residual: Plan 01 marked ``assistant_skill_package_alias`` fully
immutable, but Plan 09 admin soft-disable writes ``disabled_at`` /
``disabled_by``. Replace the full-reject UPDATE trigger on that table only
with a column-aware guard. DELETE remains fully rejected.
"""

from __future__ import annotations

from alembic import op


# revision identifiers, used by Alembic.
revision = "24f1e06fdd9e"
down_revision = "027869a00a47"
branch_labels = None
depends_on = None

ALIAS_TABLE = "assistant_skill_package_alias"
UPDATE_FN = "mindatlas_skill_package_alias_soft_disable_guard"
UPDATE_TRG = "trg_assistant_skill_package_alias_reject_update"
DELETE_TRG = "trg_assistant_skill_package_alias_reject_delete"


def upgrade() -> None:
    # Drop the Plan 01 full-reject UPDATE trigger on alias only.
    op.execute(f"DROP TRIGGER IF EXISTS {UPDATE_TRG} ON {ALIAS_TABLE}")

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {UPDATE_FN}()
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
        CREATE TRIGGER {UPDATE_TRG}
        BEFORE UPDATE ON {ALIAS_TABLE}
        FOR EACH ROW
        EXECUTE PROCEDURE {UPDATE_FN}();
        """
    )
    # DELETE trigger remains the Plan 01 full reject (unchanged).


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {UPDATE_TRG} ON {ALIAS_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {UPDATE_FN}()")
    # Restore Plan 01 full-reject UPDATE trigger (function still exists from Plan 01).
    op.execute(
        f"""
        CREATE TRIGGER {UPDATE_TRG}
        BEFORE UPDATE ON {ALIAS_TABLE}
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_reject_immutable_mutation();
        """
    )

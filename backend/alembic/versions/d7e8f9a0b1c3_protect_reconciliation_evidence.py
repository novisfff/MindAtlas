"""protect referenced reconciliation evidence Artifacts

Revision ID: d7e8f9a0b1c3
Revises: f2c3a4b5d6e7
Create Date: 2026-07-18 20:00:00.000000

Plan 08 follow-up: reconciliation verifies evidence while holding Artifact row
locks. Once a decision references an Artifact, PostgreSQL must reject any
subsequent UPDATE or DELETE of that evidence row.
"""

from __future__ import annotations

from alembic import op


revision = "d7e8f9a0b1c3"
down_revision = "f2c3a4b5d6e7"
branch_labels = None
depends_on = None


_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION mindatlas_reconciliation_evidence_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM assistant_capability_reconciliation AS reconciliation
        WHERE reconciliation.run_id = OLD.run_id
          AND COALESCE(
                reconciliation.evidence_artifact_ids::jsonb,
                '[]'::jsonb
              ) @> jsonb_build_array(OLD.id::text)
    ) THEN
        RAISE EXCEPTION
            'MINDATLAS_PLAN08_RECONCILIATION_EVIDENCE_IMMUTABLE: referenced evidence cannot be changed'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_FUNCTION_SQL)
    op.execute(
        """
        CREATE TRIGGER trg_assistant_run_artifact_reconciliation_evidence_immutable
        BEFORE UPDATE OR DELETE ON assistant_run_artifact
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_reconciliation_evidence_immutable();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS "
        "trg_assistant_run_artifact_reconciliation_evidence_immutable "
        "ON assistant_run_artifact"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mindatlas_reconciliation_evidence_immutable()"
    )

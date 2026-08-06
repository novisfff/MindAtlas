"""add ai model capability probes

Revision ID: b666b11a5faa
Revises: acf208493c87
Create Date: 2026-07-14 03:43:07.145806

Plan 03 Task 8: immutable model capability probe history + current pointer.
Does NOT re-add Plan 01 runtime_revision columns.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "b666b11a5faa"
down_revision = "acf208493c87"
branch_labels = None
depends_on = None

DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN03_DOWNGRADE_BLOCKED_PROBE_DATA"


def upgrade() -> None:
    op.create_table(
        "ai_model_capability_probe",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("probe_contract_version", sa.Integer(), nullable=False),
        sa.Column("adapter_key", sa.String(length=64), nullable=False),
        sa.Column("adapter_revision", sa.String(length=128), nullable=False),
        sa.Column("model_config_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("probe_digest", sa.String(length=64), nullable=False),
        sa.Column("safe_error_code", sa.String(length=64), nullable=True),
        sa.Column("safe_error_summary", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["ai_model.id"],
            name="fk_ai_model_capability_probe_model_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "probe_contract_version > 0",
            name="ck_ai_model_capability_probe_contract_version_positive",
        ),
        sa.CheckConstraint(
            "status IN ('passed','partial','failed')",
            name="ck_ai_model_capability_probe_status",
        ),
        sa.CheckConstraint(
            "model_config_digest ~ '^[0-9a-f]{64}$'",
            name="ck_ai_model_capability_probe_model_config_digest_hex",
        ),
        sa.CheckConstraint(
            "probe_digest ~ '^[0-9a-f]{64}$'",
            name="ck_ai_model_capability_probe_probe_digest_hex",
        ),
        sa.CheckConstraint(
            "length(adapter_key) >= 1 AND length(adapter_key) <= 64",
            name="ck_ai_model_capability_probe_adapter_key_len",
        ),
        sa.CheckConstraint(
            "length(adapter_revision) >= 1 AND length(adapter_revision) <= 128",
            name="ck_ai_model_capability_probe_adapter_revision_len",
        ),
        sa.CheckConstraint(
            "safe_error_code IS NULL OR (length(safe_error_code) >= 1 AND length(safe_error_code) <= 64)",
            name="ck_ai_model_capability_probe_safe_error_code_len",
        ),
        sa.CheckConstraint(
            "safe_error_summary IS NULL OR (length(safe_error_summary) >= 1 AND length(safe_error_summary) <= 200)",
            name="ck_ai_model_capability_probe_safe_error_summary_len",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(capabilities) = 'object'",
            name="ck_ai_model_capability_probe_capabilities_object",
        ),
    )
    op.create_index(
        "idx_ai_model_capability_probe_model_created_id",
        "ai_model_capability_probe",
        ["model_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "idx_ai_model_capability_probe_status",
        "ai_model_capability_probe",
        ["status"],
        unique=False,
    )
    op.create_index(
        "idx_ai_model_capability_probe_config_digest",
        "ai_model_capability_probe",
        ["model_config_digest"],
        unique=False,
    )
    op.create_index(
        "idx_ai_model_capability_probe_probe_digest",
        "ai_model_capability_probe",
        ["probe_digest"],
        unique=False,
    )
    # probe_digest is intentionally non-unique: repeated identical evidence is valid.

    op.add_column(
        "ai_model",
        sa.Column(
            "current_capability_probe_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_ai_model_current_capability_probe_id",
        "ai_model",
        ["current_capability_probe_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_ai_model_current_capability_probe_id",
        "ai_model",
        "ai_model_capability_probe",
        ["current_capability_probe_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Ownership: current pointer must reference a probe owned by the same model.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_ai_model_probe_pointer_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            owner_model_id uuid;
        BEGIN
            IF NEW.current_capability_probe_id IS NULL THEN
                RETURN NEW;
            END IF;
            SELECT model_id INTO owner_model_id
            FROM ai_model_capability_probe
            WHERE id = NEW.current_capability_probe_id;
            IF owner_model_id IS NULL THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN03_PROBE: current_capability_probe_id does not exist'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            IF owner_model_id IS DISTINCT FROM NEW.id THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN03_PROBE: current_capability_probe_id must belong to the same model'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_model_probe_pointer_guard
        BEFORE INSERT OR UPDATE OF current_capability_probe_id ON ai_model
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_ai_model_probe_pointer_guard();
        """
    )

    # Immutability: probe rows reject direct UPDATE.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_ai_model_capability_probe_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'MINDATLAS_PLAN03_PROBE: ai_model_capability_probe rows are immutable'
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ai_model_capability_probe_immutable
        BEFORE UPDATE ON ai_model_capability_probe
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_ai_model_capability_probe_immutable();
        """
    )


def downgrade() -> None:
    conn = op.get_bind()
    probe_count = conn.execute(
        sa.text("SELECT COUNT(*) FROM ai_model_capability_probe")
    ).scalar()
    if probe_count and int(probe_count) > 0:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: export/remove {probe_count} "
            "ai_model_capability_probe rows before downgrade"
        )

    op.execute("DROP TRIGGER IF EXISTS trg_ai_model_capability_probe_immutable ON ai_model_capability_probe")
    op.execute("DROP TRIGGER IF EXISTS trg_ai_model_probe_pointer_guard ON ai_model")
    op.execute("DROP FUNCTION IF EXISTS mindatlas_ai_model_capability_probe_immutable()")
    op.execute("DROP FUNCTION IF EXISTS mindatlas_ai_model_probe_pointer_guard()")

    op.drop_constraint(
        "fk_ai_model_current_capability_probe_id",
        "ai_model",
        type_="foreignkey",
    )
    op.drop_index("idx_ai_model_current_capability_probe_id", table_name="ai_model")
    op.drop_column("ai_model", "current_capability_probe_id")

    op.drop_index("idx_ai_model_capability_probe_probe_digest", table_name="ai_model_capability_probe")
    op.drop_index("idx_ai_model_capability_probe_config_digest", table_name="ai_model_capability_probe")
    op.drop_index("idx_ai_model_capability_probe_status", table_name="ai_model_capability_probe")
    op.drop_index("idx_ai_model_capability_probe_model_created_id", table_name="ai_model_capability_probe")
    op.drop_table("ai_model_capability_probe")

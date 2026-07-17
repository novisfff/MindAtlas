"""add capability call ledger

Revision ID: 984c07876856
Revises: 7a3dac0ac2a8
Create Date: 2026-07-17 16:47:00.000000

Plan 08 Task 1: CapabilityCall ledger tables, Run capability_ledger_mode,
call-owned Interrupt origin XOR profile, Entry source_capability_call_id,
immutable-identity / append-only triggers, and guarded downgrade.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "984c07876856"
down_revision = "7a3dac0ac2a8"
branch_labels = None
depends_on = None


DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN08_DOWNGRADE_BLOCKED_LEDGER_DATA"
DOWNGRADE_ACK_ENV = "MINDATLAS_PLAN08_DOWNGRADE_ACK_PURGE_LEDGER_DATA"

ACTIVE_OR_WAITING_STATUS_SQL = (
    "'queued','running','recovering','waiting_approval','waiting_input',"
    "'cancelling','needs_reconciliation'"
)

CALL_ACTIVE_STATUS_SQL = (
    "'proposed','awaiting_approval','authorized','executing',"
    "'unknown','needs_reconciliation'"
)


def upgrade() -> None:
    op.add_column(
        "assistant_chat_run",
        sa.Column("capability_ledger_mode", sa.String(length=32), nullable=True),
    )

    op.create_table(
        "assistant_capability_call",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("manifest_revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider_tool_call_id", sa.String(length=160), nullable=True),
        sa.Column("parent_call_id", UUID(as_uuid=True), nullable=True),
        sa.Column("logical_call_key", sa.String(length=160), nullable=False),
        sa.Column("owner_kind", sa.String(length=32), nullable=False),
        sa.Column("owner_id", UUID(as_uuid=True), nullable=True),
        sa.Column("owner_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("capability_type", sa.String(length=32), nullable=False),
        sa.Column("domain_key", sa.String(length=160), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=True),
        sa.Column("target_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("descriptor_digest", sa.String(length=64), nullable=False),
        sa.Column("authorization_digest", sa.String(length=64), nullable=False),
        sa.Column("approval_binding_digest", sa.String(length=64), nullable=True),
        sa.Column("input_artifact_id", UUID(as_uuid=True), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("side_effect_class", sa.String(length=32), nullable=False),
        sa.Column("execution_mode", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'proposed'"),
        ),
        sa.Column(
            "state_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("side_effect_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output_artifact_id", UUID(as_uuid=True), nullable=True),
        sa.Column("interrupt_id", UUID(as_uuid=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_chat_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["manifest_revision_id"],
            ["assistant_run_manifest_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_call_id"],
            ["assistant_capability_call.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["input_artifact_id"],
            ["assistant_run_artifact.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["output_artifact_id"],
            ["assistant_run_artifact.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "owner_kind IN ('main_agent','skill_version','capability_call')",
            name="ck_assistant_capability_call_owner_kind",
        ),
        sa.CheckConstraint(
            "side_effect_class IN ("
            "'none','compute','read','draft','write_local','write_external','unknown'"
            ")",
            name="ck_assistant_capability_call_side_effect",
        ),
        sa.CheckConstraint(
            "execution_mode IN ("
            "'pure_replayable','read_replayable','local_transactional',"
            "'external_idempotent','external_reconcilable','non_retriable','unsupported'"
            ")",
            name="ck_assistant_capability_call_execution_mode",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'proposed','denied','awaiting_approval','authorized','rejected',"
            "'cancelled','expired','executing','succeeded','failed','unknown',"
            "'needs_reconciliation','compensated'"
            ")",
            name="ck_assistant_capability_call_status",
        ),
        sa.CheckConstraint(
            "state_revision >= 0",
            name="ck_assistant_capability_call_state_revision",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_assistant_capability_call_attempt_count",
        ),
        sa.CheckConstraint(
            "("
            "  execution_mode <> 'local_transactional'"
            ") OR ("
            "  side_effect_started_at IS NULL"
            ") OR ("
            "  status = 'succeeded' AND side_effect_started_at IS NOT NULL"
            ")",
            name="ck_assistant_capability_call_local_effect_start",
        ),
        sa.CheckConstraint(
            "length(descriptor_digest) = 64",
            name="ck_assistant_capability_call_descriptor_digest",
        ),
        sa.CheckConstraint(
            "length(authorization_digest) = 64",
            name="ck_assistant_capability_call_authorization_digest",
        ),
        sa.CheckConstraint(
            "approval_binding_digest IS NULL OR length(approval_binding_digest) = 64",
            name="ck_assistant_capability_call_approval_binding_digest",
        ),
        sa.CheckConstraint(
            "length(input_digest) = 64",
            name="ck_assistant_capability_call_input_digest",
        ),
    )
    op.create_index(
        "ix_assistant_capability_call_run_id",
        "assistant_capability_call",
        ["run_id"],
    )
    op.create_index(
        "uq_assistant_capability_call_run_logical_key",
        "assistant_capability_call",
        ["run_id", "logical_call_key"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_capability_call_run_provider_tool_call",
        "assistant_capability_call",
        ["run_id", "provider_tool_call_id"],
        unique=True,
        postgresql_where=sa.text("provider_tool_call_id IS NOT NULL"),
        sqlite_where=sa.text("provider_tool_call_id IS NOT NULL"),
    )
    op.create_index(
        "ix_assistant_capability_call_run_status",
        "assistant_capability_call",
        ["run_id", "status"],
    )
    op.create_index(
        "ix_assistant_capability_call_idempotency_key",
        "assistant_capability_call",
        ["idempotency_key"],
    )

    op.create_table(
        "assistant_capability_call_attempt",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("call_id", UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("lease_generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_request_id", sa.String(length=255), nullable=True),
        sa.Column("external_idempotency_echo", sa.String(length=255), nullable=True),
        sa.Column("request_digest", sa.String(length=64), nullable=True),
        sa.Column("response_digest", sa.String(length=64), nullable=True),
        sa.Column("transport_status", sa.String(length=64), nullable=True),
        sa.Column(
            "side_effect_started",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("side_effect_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("retry_classification", sa.String(length=32), nullable=True),
        sa.Column("diagnostic_artifact_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["assistant_capability_call.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["diagnostic_artifact_id"],
            ["assistant_run_artifact.id"],
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "attempt_number > 0",
            name="ck_assistant_capability_call_attempt_number_positive",
        ),
        sa.CheckConstraint(
            "lease_generation >= 0",
            name="ck_assistant_capability_call_attempt_lease_generation",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'claimed','dispatched','response_received','committed',"
            "'failed','uncertain','abandoned'"
            ")",
            name="ck_assistant_capability_call_attempt_status",
        ),
        sa.CheckConstraint(
            "request_digest IS NULL OR length(request_digest) = 64",
            name="ck_assistant_capability_call_attempt_request_digest",
        ),
        sa.CheckConstraint(
            "response_digest IS NULL OR length(response_digest) = 64",
            name="ck_assistant_capability_call_attempt_response_digest",
        ),
    )
    op.create_index(
        "ix_assistant_capability_call_attempt_call_id",
        "assistant_capability_call_attempt",
        ["call_id"],
    )
    op.create_index(
        "uq_assistant_capability_call_attempt_number",
        "assistant_capability_call_attempt",
        ["call_id", "attempt_number"],
        unique=True,
    )

    op.create_table(
        "assistant_capability_reconciliation",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("call_id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("actor_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_admin_id", UUID(as_uuid=True), nullable=True),
        sa.Column("authorization_evidence", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("expected_call_revision", sa.Integer(), nullable=False),
        sa.Column("expected_run_revision", sa.Integer(), nullable=False),
        sa.Column("resulting_call_revision", sa.Integer(), nullable=True),
        sa.Column("resulting_run_revision", sa.Integer(), nullable=True),
        sa.Column("resolution_request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["assistant_capability_call.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["assistant_chat_run.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_assistant_capability_reconciliation_revision_positive",
        ),
        sa.CheckConstraint(
            "decision IN ("
            "'mark_succeeded','mark_failed','mark_compensated','retry_same_key'"
            ")",
            name="ck_assistant_capability_reconciliation_decision",
        ),
        sa.CheckConstraint(
            "expected_call_revision >= 0",
            name="ck_assistant_capability_reconciliation_expected_call_revision",
        ),
        sa.CheckConstraint(
            "expected_run_revision >= 0",
            name="ck_assistant_capability_reconciliation_expected_run_revision",
        ),
    )
    op.create_index(
        "ix_assistant_capability_reconciliation_call_id",
        "assistant_capability_reconciliation",
        ["call_id"],
    )
    op.create_index(
        "ix_assistant_capability_reconciliation_run_id",
        "assistant_capability_reconciliation",
        ["run_id"],
    )
    op.create_index(
        "uq_assistant_capability_reconciliation_call_revision",
        "assistant_capability_reconciliation",
        ["call_id", "revision"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_capability_reconciliation_resolution_request",
        "assistant_capability_reconciliation",
        ["run_id", "resolution_request_id"],
        unique=True,
    )

    op.add_column(
        "entry",
        sa.Column("source_capability_call_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "uq_entry_source_capability_call_id",
        "entry",
        ["source_capability_call_id"],
        unique=True,
        postgresql_where=sa.text("source_capability_call_id IS NOT NULL"),
        sqlite_where=sa.text("source_capability_call_id IS NOT NULL"),
    )
    op.create_foreign_key(
        "fk_entry_source_capability_call_id",
        "entry",
        "assistant_capability_call",
        ["source_capability_call_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_foreign_key(
        "fk_assistant_capability_call_interrupt_id",
        "assistant_capability_call",
        "assistant_run_interrupt",
        ["interrupt_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        """
        UPDATE assistant_chat_run
           SET capability_ledger_mode = 'legacy_read_only'
         WHERE runtime_kind = 'main_agent'
           AND capability_ledger_mode IS NULL
        """
    )

    op.add_column(
        "assistant_run_interrupt",
        sa.Column(
            "interrupt_origin",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'workflow_node'"),
        ),
    )
    op.execute(
        """
        UPDATE assistant_run_interrupt
           SET interrupt_origin = 'workflow_node'
         WHERE interrupt_origin IS NULL OR interrupt_origin = ''
        """
    )

    op.alter_column(
        "assistant_run_interrupt",
        "workflow_frame_id",
        existing_type=UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "assistant_run_interrupt",
        "node_id",
        existing_type=sa.String(length=128),
        nullable=True,
    )
    op.alter_column(
        "assistant_run_interrupt",
        "node_visit_id",
        existing_type=sa.String(length=160),
        nullable=True,
    )

    op.create_foreign_key(
        "fk_assistant_run_interrupt_capability_call_id",
        "assistant_run_interrupt",
        "assistant_capability_call",
        ["capability_call_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_assistant_chat_run_capability_ledger_mode",
        "assistant_chat_run",
        "capability_ledger_mode IS NULL OR capability_ledger_mode IN ("
        "'legacy_read_only','enforced')",
    )
    op.drop_constraint(
        "ck_assistant_chat_run_runtime_kind_shape",
        "assistant_chat_run",
        type_="check",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_runtime_kind_shape",
        "assistant_chat_run",
        "("
        "  runtime_kind = 'legacy'"
        "  AND runtime_contract_version IS NULL"
        "  AND capability_ledger_mode IS NULL"
        ") OR ("
        "  runtime_kind = 'main_agent'"
        "  AND runtime_contract_version IS NOT NULL"
        "  AND required_app_build_revision IS NOT NULL"
        "  AND capability_ledger_mode IN ('legacy_read_only','enforced')"
        ")",
    )

    op.create_check_constraint(
        "ck_assistant_run_interrupt_origin",
        "assistant_run_interrupt",
        "interrupt_origin IN ('workflow_node','capability_call')",
    )
    op.create_check_constraint(
        "ck_assistant_run_interrupt_origin_xor",
        "assistant_run_interrupt",
        "("
        "  interrupt_origin = 'workflow_node'"
        "  AND capability_call_id IS NULL"
        "  AND workflow_frame_id IS NOT NULL"
        "  AND node_id IS NOT NULL"
        "  AND node_visit_id IS NOT NULL"
        ") OR ("
        "  interrupt_origin = 'capability_call'"
        "  AND capability_call_id IS NOT NULL"
        "  AND workflow_frame_id IS NULL"
        "  AND node_id IS NULL"
        "  AND node_visit_id IS NULL"
        ")",
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_capability_call_immutable_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN08_CALL_IMMUTABLE: capability call rows cannot be deleted'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.run_id IS DISTINCT FROM OLD.run_id
               OR NEW.manifest_revision_id IS DISTINCT FROM OLD.manifest_revision_id
               OR NEW.logical_call_key IS DISTINCT FROM OLD.logical_call_key
               OR NEW.owner_kind IS DISTINCT FROM OLD.owner_kind
               OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
               OR NEW.owner_version_id IS DISTINCT FROM OLD.owner_version_id
               OR NEW.capability_type IS DISTINCT FROM OLD.capability_type
               OR NEW.domain_key IS DISTINCT FROM OLD.domain_key
               OR NEW.target_id IS DISTINCT FROM OLD.target_id
               OR NEW.target_version_id IS DISTINCT FROM OLD.target_version_id
               OR NEW.descriptor_digest IS DISTINCT FROM OLD.descriptor_digest
               OR NEW.authorization_digest IS DISTINCT FROM OLD.authorization_digest
               OR NEW.input_artifact_id IS DISTINCT FROM OLD.input_artifact_id
               OR NEW.input_digest IS DISTINCT FROM OLD.input_digest
               OR NEW.side_effect_class IS DISTINCT FROM OLD.side_effect_class
               OR NEW.execution_mode IS DISTINCT FROM OLD.execution_mode
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.parent_call_id IS DISTINCT FROM OLD.parent_call_id
               OR NEW.provider_tool_call_id IS DISTINCT FROM OLD.provider_tool_call_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
            THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN08_CALL_IMMUTABLE: identity/evidence fields are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF OLD.approval_binding_digest IS NOT NULL
               AND NEW.approval_binding_digest IS DISTINCT FROM OLD.approval_binding_digest
            THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN08_CALL_IMMUTABLE: approval_binding_digest is immutable once set'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF OLD.side_effect_started_at IS NOT NULL
               AND NEW.side_effect_started_at IS DISTINCT FROM OLD.side_effect_started_at
            THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN08_CALL_IMMUTABLE: side_effect_started_at is irreversible'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF NEW.state_revision < OLD.state_revision THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN08_CALL_REVISION: state_revision must be monotonic'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF NEW.attempt_count < OLD.attempt_count THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN08_CALL_REVISION: attempt_count must be monotonic'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_assistant_capability_call_immutable
        BEFORE UPDATE OR DELETE ON assistant_capability_call
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_capability_call_immutable_identity();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_capability_call_attempt_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'MINDATLAS_PLAN08_ATTEMPT_APPEND_ONLY: attempts are append-only'
                USING ERRCODE = 'integrity_constraint_violation';
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_assistant_capability_call_attempt_append_only
        BEFORE UPDATE OR DELETE ON assistant_capability_call_attempt
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_capability_call_attempt_append_only();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_capability_reconciliation_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'MINDATLAS_PLAN08_RECONCILIATION_APPEND_ONLY: reconciliation rows are append-only'
                USING ERRCODE = 'integrity_constraint_violation';
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_assistant_capability_reconciliation_append_only
        BEFORE UPDATE OR DELETE ON assistant_capability_reconciliation
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_capability_reconciliation_append_only();
        """
    )


def _has_blocking_ledger_data(conn) -> bool:
    active_runs = conn.execute(
        sa.text(
            f"""
            SELECT COUNT(*) FROM assistant_chat_run
            WHERE runtime_kind = 'main_agent'
              AND status IN ({ACTIVE_OR_WAITING_STATUS_SQL})
            """
        )
    ).scalar()
    if int(active_runs or 0) > 0:
        return True

    active_calls = conn.execute(
        sa.text(
            f"""
            SELECT COUNT(*) FROM assistant_capability_call
            WHERE status IN ({CALL_ACTIVE_STATUS_SQL})
            """
        )
    ).scalar()
    if int(active_calls or 0) > 0:
        return True

    recon = conn.execute(
        sa.text("SELECT COUNT(*) FROM assistant_capability_reconciliation")
    ).scalar()
    if int(recon or 0) > 0:
        return True

    calls = conn.execute(
        sa.text("SELECT COUNT(*) FROM assistant_capability_call")
    ).scalar()
    return int(calls or 0) > 0


def downgrade() -> None:
    import os

    conn = op.get_bind()
    blocking = _has_blocking_ledger_data(conn)
    ack = os.environ.get(DOWNGRADE_ACK_ENV, "").strip() == "1"
    if blocking and not ack:
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: active/waiting Main Agent Runs, "
            "nonterminal capability calls, reconciliation history, or call "
            f"history remain; set {DOWNGRADE_ACK_ENV}=1 only after verified export"
        )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_capability_reconciliation_append_only "
        "ON assistant_capability_reconciliation"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_capability_call_attempt_append_only "
        "ON assistant_capability_call_attempt"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_capability_call_immutable "
        "ON assistant_capability_call"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mindatlas_capability_reconciliation_append_only()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mindatlas_capability_call_attempt_append_only()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mindatlas_capability_call_immutable_identity()"
    )

    op.drop_constraint(
        "ck_assistant_run_interrupt_origin_xor",
        "assistant_run_interrupt",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_run_interrupt_origin",
        "assistant_run_interrupt",
        type_="check",
    )
    op.drop_constraint(
        "fk_assistant_run_interrupt_capability_call_id",
        "assistant_run_interrupt",
        type_="foreignkey",
    )
    op.execute(
        """
        UPDATE assistant_run_interrupt
           SET workflow_frame_id = '00000000-0000-0000-0000-000000000000'
         WHERE workflow_frame_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE assistant_run_interrupt
           SET node_id = 'unknown'
         WHERE node_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE assistant_run_interrupt
           SET node_visit_id = 'unknown'
         WHERE node_visit_id IS NULL
        """
    )
    op.alter_column(
        "assistant_run_interrupt",
        "workflow_frame_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "assistant_run_interrupt",
        "node_id",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.alter_column(
        "assistant_run_interrupt",
        "node_visit_id",
        existing_type=sa.String(length=160),
        nullable=False,
    )
    op.drop_column("assistant_run_interrupt", "interrupt_origin")

    op.drop_constraint(
        "ck_assistant_chat_run_runtime_kind_shape",
        "assistant_chat_run",
        type_="check",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_runtime_kind_shape",
        "assistant_chat_run",
        "("
        "  runtime_kind = 'legacy'"
        "  AND runtime_contract_version IS NULL"
        ") OR ("
        "  runtime_kind = 'main_agent'"
        "  AND runtime_contract_version IS NOT NULL"
        "  AND required_app_build_revision IS NOT NULL"
        ")",
    )
    op.drop_constraint(
        "ck_assistant_chat_run_capability_ledger_mode",
        "assistant_chat_run",
        type_="check",
    )

    op.drop_constraint(
        "fk_entry_source_capability_call_id",
        "entry",
        type_="foreignkey",
    )
    op.drop_index("uq_entry_source_capability_call_id", table_name="entry")
    op.drop_column("entry", "source_capability_call_id")

    op.drop_constraint(
        "fk_assistant_capability_call_interrupt_id",
        "assistant_capability_call",
        type_="foreignkey",
    )

    op.drop_index(
        "uq_assistant_capability_reconciliation_resolution_request",
        table_name="assistant_capability_reconciliation",
    )
    op.drop_index(
        "uq_assistant_capability_reconciliation_call_revision",
        table_name="assistant_capability_reconciliation",
    )
    op.drop_index(
        "ix_assistant_capability_reconciliation_run_id",
        table_name="assistant_capability_reconciliation",
    )
    op.drop_index(
        "ix_assistant_capability_reconciliation_call_id",
        table_name="assistant_capability_reconciliation",
    )
    op.drop_table("assistant_capability_reconciliation")

    op.drop_index(
        "uq_assistant_capability_call_attempt_number",
        table_name="assistant_capability_call_attempt",
    )
    op.drop_index(
        "ix_assistant_capability_call_attempt_call_id",
        table_name="assistant_capability_call_attempt",
    )
    op.drop_table("assistant_capability_call_attempt")

    op.drop_index(
        "ix_assistant_capability_call_idempotency_key",
        table_name="assistant_capability_call",
    )
    op.drop_index(
        "ix_assistant_capability_call_run_status",
        table_name="assistant_capability_call",
    )
    op.drop_index(
        "uq_assistant_capability_call_run_provider_tool_call",
        table_name="assistant_capability_call",
    )
    op.drop_index(
        "uq_assistant_capability_call_run_logical_key",
        table_name="assistant_capability_call",
    )
    op.drop_index(
        "ix_assistant_capability_call_run_id",
        table_name="assistant_capability_call",
    )
    op.drop_table("assistant_capability_call")

    op.drop_column("assistant_chat_run", "capability_ledger_mode")

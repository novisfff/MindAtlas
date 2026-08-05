"""main agent bootstrap readiness

Revision ID: b6e2d4f8a901
Revises: 9f3c1a7e2b40
Create Date: 2026-07-28

Plan 2 Task 2: immutable Main-Agent rollout revision/control/event tables and
Main-Agent-only Run shape. Pre-GA only — non-empty Run history is rejected.
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b6e2d4f8a901"
down_revision = "9f3c1a7e2b40"
branch_labels = None
depends_on = None

_SHA256 = r"^[0-9a-f]{64}$"

_ROLLOUT_ACTIONS = (
    "prepared",
    "activated",
    "superseded",
    "new_runs_enabled",
    "new_runs_disabled",
)
_ACTION_SQL = ", ".join(f"'{a}'" for a in _ROLLOUT_ACTIONS)

_REJECT_ROLLOUT_MUTATION_FN = """
CREATE OR REPLACE FUNCTION mindatlas_reject_rollout_revision_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'assistant rollout revision is immutable'
    USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
"""

_REJECT_BOOTSTRAP_GATE_USE_MUTATION_FN = """
CREATE OR REPLACE FUNCTION mindatlas_reject_bootstrap_gate_use_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'assistant runtime bootstrap gate use is immutable'
    USING ERRCODE = 'integrity_constraint_violation';
END;
$$;
"""

_RUN_RUNTIME_IDENTITY_FN = """
CREATE OR REPLACE FUNCTION mindatlas_reject_run_runtime_identity_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.runtime_kind IS DISTINCT FROM OLD.runtime_kind
     OR NEW.main_agent_rollout_revision_id IS DISTINCT FROM OLD.main_agent_rollout_revision_id
     OR NEW.main_agent_profile_version_id IS DISTINCT FROM OLD.main_agent_profile_version_id
     OR NEW.resolved_model_id IS DISTINCT FROM OLD.resolved_model_id
     OR NEW.runtime_closure_digest IS DISTINCT FROM OLD.runtime_closure_digest
     OR NEW.runtime_contract_version IS DISTINCT FROM OLD.runtime_contract_version
     OR NEW.required_checkpoint_codec_version IS DISTINCT FROM OLD.required_checkpoint_codec_version
     OR NEW.required_capability_feature_digest IS DISTINCT FROM OLD.required_capability_feature_digest
     OR NEW.required_app_build_revision IS DISTINCT FROM OLD.required_app_build_revision
     OR NEW.capability_ledger_mode IS DISTINCT FROM OLD.capability_ledger_mode
  THEN
    RAISE EXCEPTION 'assistant Run runtime identity is immutable'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END;
$$;
"""


def _sha256_check(column: str, *, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} ~ '{_SHA256}'",
        name=name,
    )


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # Preflight: pre-GA reset only. No legacy rows; no non-empty Run history.
    # ------------------------------------------------------------------
    legacy_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM assistant_chat_run "
            "WHERE runtime_kind <> 'main_agent'"
        )
    )
    if int(legacy_count or 0) > 0:
        raise RuntimeError(
            "legacy_upgrade_not_supported: reset this pre-GA database"
        )
    run_count = bind.scalar(sa.text("SELECT count(*) FROM assistant_chat_run"))
    if int(run_count or 0) > 0:
        raise RuntimeError(
            "schema_incompatible: non-empty Run history requires pre-GA reset"
        )

    # ------------------------------------------------------------------
    # Rollout tables
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_main_agent_rollout_revision",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("revision_label", sa.String(length=128), nullable=False),
        sa.Column(
            "profile_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_main_agent_profile_version.id"),
            nullable=False,
        ),
        sa.Column("profile_content_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "model_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_model.id"),
            nullable=False,
        ),
        sa.Column("model_identity_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "package_closure_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("package_closure_digest", sa.String(length=64), nullable=False),
        sa.Column("capability_closure_digest", sa.String(length=64), nullable=False),
        sa.Column("seed_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("build_revision", sa.String(length=128), nullable=False),
        sa.Column("runtime_contract_version", sa.Integer(), nullable=False),
        sa.Column("checkpoint_codec_version", sa.Integer(), nullable=False),
        sa.Column("capability_feature_digest", sa.String(length=64), nullable=False),
        sa.Column("revision_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "prepared_by_operator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operator_account.id"),
            nullable=True,
        ),
        sa.Column("prepared_reason", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "revision_label",
            name="uq_ma_rollout_revision_label",
        ),
        sa.UniqueConstraint(
            "revision_digest",
            name="uq_ma_rollout_revision_digest",
        ),
        sa.CheckConstraint(
            "runtime_contract_version > 0 AND checkpoint_codec_version > 0",
            name="ck_ma_rollout_revision_positive_contract",
        ),
        sa.CheckConstraint(
            "length(prepared_reason) >= 1 AND length(prepared_reason) <= 500",
            name="ck_ma_rollout_revision_reason_len",
        ),
        sa.CheckConstraint(
            "length(build_revision) >= 1 AND length(build_revision) <= 128",
            name="ck_ma_rollout_revision_build_len",
        ),
        _sha256_check(
            "profile_content_digest",
            name="ck_ma_rollout_revision_profile_content_digest",
        ),
        _sha256_check(
            "model_identity_digest",
            name="ck_ma_rollout_revision_model_identity_digest",
        ),
        _sha256_check(
            "package_closure_digest",
            name="ck_ma_rollout_revision_package_closure_digest",
        ),
        _sha256_check(
            "capability_closure_digest",
            name="ck_ma_rollout_revision_capability_closure_digest",
        ),
        _sha256_check(
            "seed_manifest_digest",
            name="ck_ma_rollout_revision_seed_manifest_digest",
        ),
        _sha256_check(
            "capability_feature_digest",
            name="ck_ma_rollout_revision_capability_feature_digest",
        ),
        _sha256_check(
            "revision_digest",
            name="ck_ma_rollout_revision_revision_digest",
        ),
    )

    # Trusted bootstrap authorization provenance.  This intentionally does not
    # reuse evaluation publish gates: the build-owned seed is not an evaluated
    # user publication, but activation must still verify a durable immutable
    # server-created gate-use row rather than trusting a rollout event label.
    op.create_table(
        "assistant_runtime_bootstrap_gate_use",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column(
            "rollout_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_main_agent_rollout_revision.id"),
            nullable=False,
        ),
        sa.Column("rollout_revision_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "profile_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_main_agent_profile_version.id"),
            nullable=False,
        ),
        sa.Column("profile_content_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "skill_package_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_skill_package.id"),
            nullable=False,
        ),
        sa.Column(
            "skill_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_skill_version.id"),
            nullable=False,
        ),
        sa.Column("skill_version_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "model_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_model.id"),
            nullable=False,
        ),
        sa.Column("model_identity_digest", sa.String(length=64), nullable=False),
        sa.Column("seed_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("seed_contract_digest", sa.String(length=64), nullable=False),
        sa.Column("package_closure_digest", sa.String(length=64), nullable=False),
        sa.Column("capability_closure_digest", sa.String(length=64), nullable=False),
        sa.Column("build_revision", sa.String(length=128), nullable=False),
        sa.Column("runtime_contract_version", sa.Integer(), nullable=False),
        sa.Column("checkpoint_codec_version", sa.Integer(), nullable=False),
        sa.Column("capability_feature_digest", sa.String(length=64), nullable=False),
        sa.Column("closure_digest", sa.String(length=64), nullable=False),
        sa.Column("bootstrap_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "operator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operator_account.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "rollout_revision_id",
            name="uq_runtime_bootstrap_gate_use_rollout_revision_id",
        ),
        sa.UniqueConstraint(
            "bootstrap_request_id",
            name="uq_runtime_bootstrap_gate_use_request_id",
        ),
        sa.CheckConstraint(
            "action = 'system_bootstrap'",
            name="ck_runtime_bootstrap_gate_use_action",
        ),
        sa.CheckConstraint(
            "runtime_contract_version > 0 AND checkpoint_codec_version > 0",
            name="ck_runtime_bootstrap_gate_use_positive_contract",
        ),
        sa.CheckConstraint(
            "length(build_revision) >= 1 AND length(build_revision) <= 128",
            name="ck_runtime_bootstrap_gate_use_build_len",
        ),
        _sha256_check(
            "rollout_revision_digest",
            name="ck_runtime_bootstrap_gate_use_rollout_revision_digest",
        ),
        _sha256_check(
            "profile_content_digest",
            name="ck_runtime_bootstrap_gate_use_profile_content_digest",
        ),
        _sha256_check(
            "skill_version_digest",
            name="ck_runtime_bootstrap_gate_use_skill_version_digest",
        ),
        _sha256_check(
            "model_identity_digest",
            name="ck_runtime_bootstrap_gate_use_model_identity_digest",
        ),
        _sha256_check(
            "seed_manifest_digest",
            name="ck_runtime_bootstrap_gate_use_seed_manifest_digest",
        ),
        _sha256_check(
            "seed_contract_digest",
            name="ck_runtime_bootstrap_gate_use_seed_contract_digest",
        ),
        _sha256_check(
            "package_closure_digest",
            name="ck_runtime_bootstrap_gate_use_package_closure_digest",
        ),
        _sha256_check(
            "capability_closure_digest",
            name="ck_runtime_bootstrap_gate_use_capability_closure_digest",
        ),
        _sha256_check(
            "capability_feature_digest",
            name="ck_runtime_bootstrap_gate_use_capability_feature_digest",
        ),
        _sha256_check(
            "closure_digest",
            name="ck_runtime_bootstrap_gate_use_closure_digest",
        ),
    )

    op.create_table(
        "assistant_main_agent_rollout_control",
        sa.Column("control_key", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column(
            "active_rollout_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_main_agent_rollout_revision.id"),
            nullable=True,
        ),
        sa.Column(
            "state_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "new_runs_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "control_key = 'main_agent'",
            name="ck_ma_rollout_control_key",
        ),
        sa.CheckConstraint(
            "state_revision >= 0",
            name="ck_ma_rollout_control_state_revision",
        ),
    )

    op.create_table(
        "assistant_main_agent_rollout_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "from_rollout_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_main_agent_rollout_revision.id"),
            nullable=True,
        ),
        sa.Column(
            "to_rollout_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assistant_main_agent_rollout_revision.id"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("control_revision", sa.Integer(), nullable=False),
        sa.Column(
            "request_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "operator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operator_account.id"),
            nullable=True,
        ),
        sa.Column(
            "operator_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operator_session.id"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "request_id",
            name="uq_ma_rollout_event_request_id",
        ),
        sa.CheckConstraint(
            f"action IN ({_ACTION_SQL})",
            name="ck_ma_rollout_event_action",
        ),
        sa.CheckConstraint(
            "control_revision >= 0",
            name="ck_ma_rollout_event_control_revision",
        ),
        sa.CheckConstraint(
            "length(reason) >= 1 AND length(reason) <= 500",
            name="ck_ma_rollout_event_reason_len",
        ),
        _sha256_check(
            "request_digest",
            name="ck_ma_rollout_event_request_digest",
        ),
        _sha256_check(
            "evidence_digest",
            name="ck_ma_rollout_event_evidence_digest",
        ),
    )

    # ------------------------------------------------------------------
    # Run shape: add Plan 2 frozen columns (table is empty after preflight).
    # ------------------------------------------------------------------
    op.add_column(
        "assistant_chat_run",
        sa.Column(
            "main_agent_rollout_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column(
            "main_agent_profile_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column(
            "resolved_model_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column("runtime_closure_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column(
            "required_checkpoint_codec_version",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "assistant_chat_run",
        sa.Column(
            "required_capability_feature_digest",
            sa.String(length=64),
            nullable=True,
        ),
    )

    # Shrink build revision to 128 and keep column present.
    op.alter_column(
        "assistant_chat_run",
        "required_app_build_revision",
        existing_type=sa.String(length=160),
        type_=sa.String(length=128),
        existing_nullable=True,
    )

    # Drop legacy-era runtime constraints before replacing shape.
    op.drop_constraint(
        "ck_assistant_chat_run_runtime_kind_shape",
        "assistant_chat_run",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_chat_run_runtime_kind",
        "assistant_chat_run",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_chat_run_runtime_contract_version",
        "assistant_chat_run",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_chat_run_capability_ledger_mode",
        "assistant_chat_run",
        type_="check",
    )

    # Make frozen columns non-null (empty table; safe after preflight).
    op.alter_column(
        "assistant_chat_run",
        "main_agent_rollout_revision_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "assistant_chat_run",
        "main_agent_profile_version_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "assistant_chat_run",
        "resolved_model_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "assistant_chat_run",
        "runtime_closure_digest",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column(
        "assistant_chat_run",
        "runtime_contract_version",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "assistant_chat_run",
        "required_checkpoint_codec_version",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "assistant_chat_run",
        "required_capability_feature_digest",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column(
        "assistant_chat_run",
        "required_app_build_revision",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.alter_column(
        "assistant_chat_run",
        "capability_ledger_mode",
        existing_type=sa.String(length=32),
        nullable=False,
    )

    # Default runtime_kind to main_agent going forward.
    op.alter_column(
        "assistant_chat_run",
        "runtime_kind",
        existing_type=sa.String(length=32),
        server_default=sa.text("'main_agent'"),
        existing_nullable=False,
    )
    op.alter_column(
        "assistant_chat_run",
        "memory_commit_status",
        existing_type=sa.String(length=32),
        server_default=sa.text("'pending'"),
        existing_nullable=False,
    )

    op.create_foreign_key(
        "fk_assistant_chat_run_main_agent_rollout_revision_id",
        "assistant_chat_run",
        "assistant_main_agent_rollout_revision",
        ["main_agent_rollout_revision_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_assistant_chat_run_main_agent_profile_version_id",
        "assistant_chat_run",
        "assistant_main_agent_profile_version",
        ["main_agent_profile_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_assistant_chat_run_resolved_model_id",
        "assistant_chat_run",
        "ai_model",
        ["resolved_model_id"],
        ["id"],
    )

    op.create_check_constraint(
        "ck_assistant_chat_run_main_agent_only",
        "assistant_chat_run",
        "runtime_kind = 'main_agent'",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_positive_runtime_contract",
        "assistant_chat_run",
        "runtime_contract_version > 0 AND required_checkpoint_codec_version > 0",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_runtime_digests",
        "assistant_chat_run",
        f"runtime_closure_digest ~ '{_SHA256}' "
        f"AND required_capability_feature_digest ~ '{_SHA256}'",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_capability_ledger_mode",
        "assistant_chat_run",
        "capability_ledger_mode IN ('legacy_read_only','enforced')",
    )

    # ------------------------------------------------------------------
    # Immutability triggers
    # ------------------------------------------------------------------
    op.execute(_REJECT_ROLLOUT_MUTATION_FN)
    op.execute(
        """
        CREATE TRIGGER trg_assistant_rollout_revision_immutable
        BEFORE UPDATE OR DELETE ON assistant_main_agent_rollout_revision
        FOR EACH ROW EXECUTE FUNCTION mindatlas_reject_rollout_revision_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_assistant_rollout_event_append_only
        BEFORE UPDATE OR DELETE ON assistant_main_agent_rollout_event
        FOR EACH ROW EXECUTE FUNCTION mindatlas_reject_rollout_revision_mutation();
        """
    )
    op.execute(_REJECT_BOOTSTRAP_GATE_USE_MUTATION_FN)
    op.execute(
        """
        CREATE TRIGGER trg_assistant_runtime_bootstrap_gate_use_immutable
        BEFORE UPDATE OR DELETE ON assistant_runtime_bootstrap_gate_use
        FOR EACH ROW EXECUTE FUNCTION mindatlas_reject_bootstrap_gate_use_mutation();
        """
    )

    op.execute(_RUN_RUNTIME_IDENTITY_FN)
    op.execute(
        """
        CREATE TRIGGER trg_assistant_chat_run_runtime_identity_immutable
        BEFORE UPDATE ON assistant_chat_run
        FOR EACH ROW EXECUTE FUNCTION mindatlas_reject_run_runtime_identity_mutation();
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    app_env = (os.environ.get("APP_ENV") or "").strip().lower()
    if app_env != "test":
        raise RuntimeError(
            "schema_incompatible: Plan 2 Main-Agent rollout downgrade is only "
            "allowed when APP_ENV=test"
        )

    for table in (
        "assistant_chat_run",
        "assistant_runtime_bootstrap_gate_use",
        "assistant_main_agent_rollout_event",
        "assistant_main_agent_rollout_control",
        "assistant_main_agent_rollout_revision",
    ):
        count = bind.scalar(sa.text(f"SELECT count(*) FROM {table}"))
        if int(count or 0) > 0:
            raise RuntimeError(
                f"schema_incompatible: cannot downgrade Plan 2 with non-empty {table}"
            )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_chat_run_runtime_identity_immutable "
        "ON assistant_chat_run"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mindatlas_reject_run_runtime_identity_mutation()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_rollout_event_append_only "
        "ON assistant_main_agent_rollout_event"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_rollout_revision_immutable "
        "ON assistant_main_agent_rollout_revision"
    )
    op.execute("DROP FUNCTION IF EXISTS mindatlas_reject_rollout_revision_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_runtime_bootstrap_gate_use_immutable "
        "ON assistant_runtime_bootstrap_gate_use"
    )
    op.execute("DROP FUNCTION IF EXISTS mindatlas_reject_bootstrap_gate_use_mutation()")

    op.drop_constraint(
        "ck_assistant_chat_run_capability_ledger_mode",
        "assistant_chat_run",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_chat_run_runtime_digests",
        "assistant_chat_run",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_chat_run_positive_runtime_contract",
        "assistant_chat_run",
        type_="check",
    )
    op.drop_constraint(
        "ck_assistant_chat_run_main_agent_only",
        "assistant_chat_run",
        type_="check",
    )
    op.drop_constraint(
        "fk_assistant_chat_run_resolved_model_id",
        "assistant_chat_run",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assistant_chat_run_main_agent_profile_version_id",
        "assistant_chat_run",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_assistant_chat_run_main_agent_rollout_revision_id",
        "assistant_chat_run",
        type_="foreignkey",
    )

    op.drop_column("assistant_chat_run", "required_capability_feature_digest")
    op.drop_column("assistant_chat_run", "required_checkpoint_codec_version")
    op.drop_column("assistant_chat_run", "runtime_closure_digest")
    op.drop_column("assistant_chat_run", "resolved_model_id")
    op.drop_column("assistant_chat_run", "main_agent_profile_version_id")
    op.drop_column("assistant_chat_run", "main_agent_rollout_revision_id")

    op.alter_column(
        "assistant_chat_run",
        "capability_ledger_mode",
        existing_type=sa.String(length=32),
        nullable=True,
    )
    op.alter_column(
        "assistant_chat_run",
        "runtime_contract_version",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.alter_column(
        "assistant_chat_run",
        "required_app_build_revision",
        existing_type=sa.String(length=128),
        type_=sa.String(length=160),
        nullable=True,
    )
    op.alter_column(
        "assistant_chat_run",
        "runtime_kind",
        existing_type=sa.String(length=32),
        server_default=sa.text("'legacy'"),
        existing_nullable=False,
    )
    op.alter_column(
        "assistant_chat_run",
        "memory_commit_status",
        existing_type=sa.String(length=32),
        server_default=sa.text("'not_applicable'"),
        existing_nullable=False,
    )

    # Restore Plan-1-era constraints (never reconstruct Legacy rows).
    op.create_check_constraint(
        "ck_assistant_chat_run_runtime_kind",
        "assistant_chat_run",
        "runtime_kind IN ('legacy','main_agent')",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_runtime_contract_version",
        "assistant_chat_run",
        "runtime_contract_version IS NULL OR runtime_contract_version > 0",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_capability_ledger_mode",
        "assistant_chat_run",
        "capability_ledger_mode IS NULL OR capability_ledger_mode IN ("
        "'legacy_read_only','enforced'"
        ")",
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

    op.drop_table("assistant_runtime_bootstrap_gate_use")
    op.drop_table("assistant_main_agent_rollout_event")
    op.drop_table("assistant_main_agent_rollout_control")
    op.drop_table("assistant_main_agent_rollout_revision")

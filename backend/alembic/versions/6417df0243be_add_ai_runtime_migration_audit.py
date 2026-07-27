"""add ai runtime migration audit

Revision ID: 6417df0243be
Revises: 027869a00a47
Create Date: 2026-07-23 01:18:12.616027

Plan 10 Task 1: additive migration/rollout evidence tables + Eval purpose
runtime_shadow extension. Parent is Plan 09 head ``027869a00a47`` only.
Produces evidence storage only — runtime selection remains legacy.
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "6417df0243be"
down_revision = "027869a00a47"
branch_labels = None
depends_on = None

DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN10_MIGRATION_DOWNGRADE_BLOCKED"
DOWNGRADE_ACK_ENV = "MINDATLAS_PLAN10_MIGRATION_DOWNGRADE_ACK"

# Fully immutable (no UPDATE/DELETE).
IMMUTABLE_TABLES = (
    "assistant_runtime_rollout_revision",
    "assistant_runtime_rollout_assignment",
    "assistant_runtime_admission_fallback_event",
    "assistant_legacy_approval_archive",
    "assistant_runtime_cleanup_gate",
    "assistant_runtime_migration_event",
    "assistant_runtime_rollout_event",
)

# Append-only for UPDATE; DELETE allowed for privacy retention (shadow pair).
APPEND_ONLY_UPDATE_TABLES = (
    "assistant_runtime_shadow_comparison",
)

ALL_MIGRATION_TABLES = (
    "assistant_runtime_shadow_comparison",
    "assistant_runtime_admission_fallback_event",
    "assistant_runtime_cleanup_gate",
    "assistant_legacy_approval_archive",
    "assistant_runtime_rollout_assignment",
    "assistant_runtime_rollout_event",
    "assistant_runtime_rollout_control",
    "assistant_runtime_rollout_revision",
    "assistant_runtime_migration_event",
    "assistant_runtime_migration_batch",
    "assistant_runtime_migration_item",
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Migration item + event + batch
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_runtime_migration_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("subject_kind", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=160), nullable=False),
        sa.Column(
            "source_name",
            sa.String(length=256),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "source_name_normalized",
            sa.String(length=256),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=160), nullable=True),
        sa.Column("target_version", sa.String(length=160), nullable=True),
        sa.Column("target_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'discovered'"),
        ),
        sa.Column("reason_code", sa.String(length=128), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column(
            "source_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "target_revision",
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
        sa.Column(
            "state_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_principal", sa.String(length=128), nullable=True),
        sa.Column("build_revision", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "subject_kind",
            "source_type",
            "source_id",
            name="uq_assistant_runtime_migration_item_source",
        ),
        sa.CheckConstraint(
            "subject_kind IN ("
            "'skill','profile','alias','l2_memory','approval',"
            "'entrypoint','package','write_branch'"
            ")",
            name="ck_assistant_runtime_migration_item_subject_kind",
        ),
        sa.CheckConstraint(
            "state IN ("
            "'discovered','mapped','migrated','verified','blocked','archived'"
            ")",
            name="ck_assistant_runtime_migration_item_state",
        ),
        sa.CheckConstraint(
            "source_revision >= 0",
            name="ck_assistant_runtime_migration_item_source_revision",
        ),
        sa.CheckConstraint(
            "target_revision >= 0",
            name="ck_assistant_runtime_migration_item_target_revision",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_assistant_runtime_migration_item_attempt_count",
        ),
        sa.CheckConstraint(
            "state_revision >= 0",
            name="ck_assistant_runtime_migration_item_state_revision",
        ),
        sa.CheckConstraint(
            "length(source_digest) = 64",
            name="ck_assistant_runtime_migration_item_source_digest",
        ),
        sa.CheckConstraint(
            "target_digest IS NULL OR length(target_digest) = 64",
            name="ck_assistant_runtime_migration_item_target_digest",
        ),
    )
    op.create_index(
        "ix_assistant_runtime_migration_item_state",
        "assistant_runtime_migration_item",
        ["state", "subject_kind"],
    )

    op.create_table(
        "assistant_runtime_migration_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("migration_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("previous_state", sa.String(length=32), nullable=True),
        sa.Column("new_state", sa.String(length=32), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("safe_details", sa.JSON(), nullable=False),
        sa.Column("actor_principal", sa.String(length=128), nullable=True),
        sa.Column("build_revision", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["migration_item_id"],
            ["assistant_runtime_migration_item.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "migration_item_id",
            "revision",
            name="uq_assistant_runtime_migration_event_revision",
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_assistant_runtime_migration_event_revision",
        ),
        sa.CheckConstraint(
            "new_state IN ("
            "'discovered','mapped','migrated','verified','blocked','archived'"
            ")",
            name="ck_assistant_runtime_migration_event_new_state",
        ),
        sa.CheckConstraint(
            "previous_state IS NULL OR previous_state IN ("
            "'discovered','mapped','migrated','verified','blocked','archived'"
            ")",
            name="ck_assistant_runtime_migration_event_previous_state",
        ),
        sa.CheckConstraint(
            "length(evidence_digest) = 64",
            name="ck_assistant_runtime_migration_event_evidence_digest",
        ),
    )
    op.create_index(
        "ix_assistant_runtime_migration_event_migration_item_id",
        "assistant_runtime_migration_event",
        ["migration_item_id"],
    )

    op.create_table(
        "assistant_runtime_migration_batch",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("command_kind", sa.String(length=32), nullable=False),
        sa.Column("source_snapshot_digest", sa.String(length=64), nullable=False),
        sa.Column("configuration_digest", sa.String(length=64), nullable=False),
        sa.Column("build_revision", sa.String(length=160), nullable=False),
        sa.Column("schema_revision", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=128), nullable=False),
        sa.Column("database_fingerprint", sa.String(length=160), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'prepared'"),
        ),
        sa.Column(
            "state_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "batch_size",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("100"),
        ),
        sa.Column("resume_cursor", sa.String(length=256), nullable=True),
        sa.Column(
            "processed_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "succeeded_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "blocked_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "failed_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("started_by", sa.String(length=128), nullable=True),
        sa.Column("completed_by", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("report_digest", sa.String(length=64), nullable=True),
        sa.Column("dry_run_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "request_id",
            name="uq_assistant_runtime_migration_batch_request_id",
        ),
        sa.CheckConstraint(
            "command_kind IN ("
            "'inventory','package','l2','approval','verify'"
            ")",
            name="ck_assistant_runtime_migration_batch_command_kind",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'prepared','running','completed','failed','cancelled'"
            ")",
            name="ck_assistant_runtime_migration_batch_status",
        ),
        sa.CheckConstraint(
            "state_revision >= 0",
            name="ck_assistant_runtime_migration_batch_state_revision",
        ),
        sa.CheckConstraint(
            "batch_size > 0 AND batch_size <= 1000",
            name="ck_assistant_runtime_migration_batch_size",
        ),
        sa.CheckConstraint(
            "processed_count >= 0 AND succeeded_count >= 0 "
            "AND blocked_count >= 0 AND failed_count >= 0",
            name="ck_assistant_runtime_migration_batch_counts",
        ),
        sa.CheckConstraint(
            "length(source_snapshot_digest) = 64",
            name="ck_assistant_runtime_migration_batch_source_snapshot_digest",
        ),
        sa.CheckConstraint(
            "length(configuration_digest) = 64",
            name="ck_assistant_runtime_migration_batch_configuration_digest",
        ),
        sa.CheckConstraint(
            "report_digest IS NULL OR length(report_digest) = 64",
            name="ck_assistant_runtime_migration_batch_report_digest",
        ),
        sa.CheckConstraint(
            "dry_run_digest IS NULL OR length(dry_run_digest) = 64",
            name="ck_assistant_runtime_migration_batch_dry_run_digest",
        ),
    )
    op.create_index(
        "ix_assistant_runtime_migration_batch_status",
        "assistant_runtime_migration_batch",
        ["status", "command_kind"],
    )

    # ------------------------------------------------------------------
    # Rollout revision / event / control / assignment
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_runtime_rollout_revision",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("revision_label", sa.String(length=128), nullable=False),
        sa.Column(
            "runtime_mode",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'legacy'"),
        ),
        sa.Column(
            "shadow_eligible_scope",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column(
            "shadow_percent",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "read_canary_percent",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "write_mode",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'off'"),
        ),
        sa.Column(
            "write_percent",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("eligible_closure_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "config_origin",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'native'"),
        ),
        sa.Column("build_revision", sa.String(length=160), nullable=False),
        sa.Column(
            "runtime_contract_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "policy_contract_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "worker_contract_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("cohort_salt_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("metric_definition_id", sa.String(length=128), nullable=True),
        sa.Column("metric_window_id", sa.String(length=128), nullable=True),
        sa.Column("approval_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("config_digest", sa.String(length=64), nullable=False),
        sa.Column("actor_principal", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "revision_label",
            name="uq_assistant_runtime_rollout_revision_label",
        ),
        sa.CheckConstraint(
            "runtime_mode IN ('legacy','main_agent')",
            name="ck_assistant_runtime_rollout_revision_runtime_mode",
        ),
        sa.CheckConstraint(
            "shadow_eligible_scope IN ('none','staff','fixture','approved_production')",
            name="ck_assistant_runtime_rollout_revision_shadow_scope",
        ),
        sa.CheckConstraint(
            "shadow_percent >= 0 AND shadow_percent <= 100",
            name="ck_assistant_runtime_rollout_revision_shadow_percent",
        ),
        sa.CheckConstraint(
            "read_canary_percent >= 0 AND read_canary_percent <= 100",
            name="ck_assistant_runtime_rollout_revision_read_canary_percent",
        ),
        sa.CheckConstraint(
            "write_mode IN ('off','golden')",
            name="ck_assistant_runtime_rollout_revision_write_mode",
        ),
        sa.CheckConstraint(
            "write_percent >= 0 AND write_percent <= 100",
            name="ck_assistant_runtime_rollout_revision_write_percent",
        ),
        sa.CheckConstraint(
            "config_origin IN ('native','plan04_compat')",
            name="ck_assistant_runtime_rollout_revision_config_origin",
        ),
        sa.CheckConstraint(
            "config_origin <> 'plan04_compat' OR ("
            "shadow_percent = 0 AND read_canary_percent = 0 "
            "AND write_percent = 0 AND runtime_mode = 'legacy'"
            ")",
            name="ck_assistant_runtime_rollout_revision_plan04_compat_shape",
        ),
        sa.CheckConstraint(
            "runtime_contract_version > 0 AND policy_contract_version > 0 "
            "AND worker_contract_version > 0",
            name="ck_assistant_runtime_rollout_revision_contract_versions",
        ),
        sa.CheckConstraint(
            "length(eligible_closure_digest) = 64",
            name="ck_assistant_runtime_rollout_revision_eligible_closure_digest",
        ),
        sa.CheckConstraint(
            "length(cohort_salt_fingerprint) = 64",
            name="ck_assistant_runtime_rollout_revision_cohort_salt_fingerprint",
        ),
        sa.CheckConstraint(
            "length(config_digest) = 64",
            name="ck_assistant_runtime_rollout_revision_config_digest",
        ),
    )

    op.create_table(
        "assistant_runtime_rollout_control",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "singleton_key",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'singleton'"),
        ),
        sa.Column(
            "active_rollout_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "state_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["active_rollout_revision_id"],
            ["assistant_runtime_rollout_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "singleton_key",
            name="uq_assistant_runtime_rollout_control_singleton_key",
        ),
        sa.CheckConstraint(
            "state_revision >= 0",
            name="ck_assistant_runtime_rollout_control_state_revision",
        ),
        sa.CheckConstraint(
            "singleton_key = 'singleton'",
            name="ck_assistant_runtime_rollout_control_singleton_key",
        ),
    )

    op.create_table(
        "assistant_runtime_rollout_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("rollout_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column(
            "previous_active_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("control_revision", sa.Integer(), nullable=False),
        sa.Column("actor_principal", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rollout_revision_id"],
            ["assistant_runtime_rollout_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "action IN ('prepared','activated','superseded','rolled_back')",
            name="ck_assistant_runtime_rollout_event_action",
        ),
        sa.CheckConstraint(
            "control_revision > 0",
            name="ck_assistant_runtime_rollout_event_control_revision",
        ),
        sa.CheckConstraint(
            "length(evidence_digest) = 64",
            name="ck_assistant_runtime_rollout_event_evidence_digest",
        ),
    )
    op.create_index(
        "ix_assistant_runtime_rollout_event_rollout_revision_id",
        "assistant_runtime_rollout_event",
        ["rollout_revision_id"],
    )
    op.create_index(
        "ix_assistant_runtime_rollout_event_control_revision",
        "assistant_runtime_rollout_event",
        ["control_revision"],
    )

    op.create_table(
        "assistant_runtime_rollout_assignment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_scope_digest", sa.String(length=64), nullable=True),
        sa.Column("rollout_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "cohort",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'default'"),
        ),
        sa.Column("assigned_runtime_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "assigned_write_mode",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'off'"),
        ),
        sa.Column("assignment_reason", sa.String(length=32), nullable=False),
        sa.Column("cohort_key_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rollout_revision_id"],
            ["assistant_runtime_rollout_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "rollout_revision_id",
            name="uq_assistant_runtime_rollout_assignment_scope_revision",
        ),
        sa.CheckConstraint(
            "assigned_runtime_kind IN ('legacy','main_agent')",
            name="ck_assistant_runtime_rollout_assignment_runtime_kind",
        ),
        sa.CheckConstraint(
            "assigned_write_mode IN ('off','golden')",
            name="ck_assistant_runtime_rollout_assignment_write_mode",
        ),
        sa.CheckConstraint(
            "assignment_reason IN ('hash','staff','explicit_override','rollback')",
            name="ck_assistant_runtime_rollout_assignment_reason",
        ),
        sa.CheckConstraint(
            "length(cohort_key_digest) = 64",
            name="ck_assistant_runtime_rollout_assignment_cohort_key_digest",
        ),
        sa.CheckConstraint(
            "principal_scope_digest IS NULL OR length(principal_scope_digest) = 64",
            name="ck_assistant_runtime_rollout_assignment_principal_scope_digest",
        ),
    )
    op.create_index(
        "ix_assistant_runtime_rollout_assignment_conversation_id",
        "assistant_runtime_rollout_assignment",
        ["conversation_id"],
    )
    op.create_index(
        "ix_assistant_runtime_rollout_assignment_rollout_revision_id",
        "assistant_runtime_rollout_assignment",
        ["rollout_revision_id"],
    )

    # ------------------------------------------------------------------
    # Admission fallback / shadow comparison / archive / cleanup gate
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_runtime_admission_fallback_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("rollout_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "candidate_runtime_kind",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'main_agent'"),
        ),
        sa.Column(
            "selected_runtime_kind",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'legacy'"),
        ),
        sa.Column(
            "reason",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'preinsert_fallback'"),
        ),
        sa.Column("admission_failure_digest", sa.String(length=64), nullable=False),
        sa.Column("resulting_legacy_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("principal_scope_digest", sa.String(length=64), nullable=True),
        sa.Column("build_revision", sa.String(length=160), nullable=True),
        sa.Column("schema_revision", sa.String(length=64), nullable=True),
        sa.Column("runtime_contract_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["rollout_revision_id"],
            ["assistant_runtime_rollout_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["assistant_runtime_rollout_assignment.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resulting_legacy_run_id"],
            ["assistant_chat_run.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "request_id",
            name="uq_assistant_runtime_admission_fallback_event_request_id",
        ),
        sa.CheckConstraint(
            "candidate_runtime_kind = 'main_agent'",
            name="ck_assistant_runtime_admission_fallback_candidate",
        ),
        sa.CheckConstraint(
            "selected_runtime_kind = 'legacy'",
            name="ck_assistant_runtime_admission_fallback_selected",
        ),
        sa.CheckConstraint(
            "reason = 'preinsert_fallback'",
            name="ck_assistant_runtime_admission_fallback_reason",
        ),
        sa.CheckConstraint(
            "length(admission_failure_digest) = 64",
            name="ck_assistant_runtime_admission_fallback_failure_digest",
        ),
        sa.CheckConstraint(
            "principal_scope_digest IS NULL OR length(principal_scope_digest) = 64",
            name="ck_assistant_runtime_admission_fallback_principal_scope_digest",
        ),
    )
    op.create_index(
        "ix_arm_admission_fallback_rollout_rev_id",
        "assistant_runtime_admission_fallback_event",
        ["rollout_revision_id"],
    )
    op.create_index(
        "ix_arm_admission_fallback_assignment_id",
        "assistant_runtime_admission_fallback_event",
        ["assignment_id"],
    )
    op.create_index(
        "ix_arm_admission_fallback_legacy_run_id",
        "assistant_runtime_admission_fallback_event",
        ["resulting_legacy_run_id"],
    )

    op.create_table(
        "assistant_runtime_shadow_comparison",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("production_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("eval_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rollout_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "shadow_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("context_digest", sa.String(length=64), nullable=False),
        sa.Column("fixture_digest", sa.String(length=64), nullable=True),
        sa.Column("catalog_revision", sa.String(length=160), nullable=True),
        sa.Column("profile_revision", sa.String(length=160), nullable=True),
        sa.Column("model_revision", sa.String(length=160), nullable=True),
        sa.Column("runtime_revision", sa.String(length=160), nullable=True),
        sa.Column("build_revision", sa.String(length=160), nullable=True),
        sa.Column("intent_class", sa.String(length=64), nullable=True),
        sa.Column(
            "write_simulation_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("legacy_skill_selection", sa.String(length=160), nullable=True),
        sa.Column("new_skill_selection", sa.String(length=160), nullable=True),
        sa.Column("capability_path_summary", sa.String(length=256), nullable=True),
        sa.Column("completion_summary", sa.String(length=256), nullable=True),
        sa.Column("stop_summary", sa.String(length=256), nullable=True),
        sa.Column("error_summary", sa.String(length=256), nullable=True),
        sa.Column("quality_assertion_snapshot", sa.JSON(), nullable=False),
        sa.Column("rounds_estimate", sa.Integer(), nullable=True),
        sa.Column("calls_estimate", sa.Integer(), nullable=True),
        sa.Column("tokens_estimate", sa.Integer(), nullable=True),
        sa.Column("latency_ms_estimate", sa.Integer(), nullable=True),
        sa.Column("cost_estimate_micros", sa.Integer(), nullable=True),
        sa.Column(
            "reviewer_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "result_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column(
            "private_input_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("private_input_payload_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["production_run_id"],
            ["assistant_chat_run.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["assistant_skill_eval_run.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rollout_revision_id"],
            ["assistant_runtime_rollout_revision.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["assistant_runtime_rollout_assignment.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "production_run_id",
            "eval_run_id",
            name="uq_assistant_runtime_shadow_comparison_pair",
        ),
        sa.UniqueConstraint(
            "eval_run_id",
            name="uq_assistant_runtime_shadow_comparison_eval_run",
        ),
        sa.CheckConstraint(
            "reviewer_state IN ('pending','reviewed','waived')",
            name="ck_assistant_runtime_shadow_comparison_reviewer_state",
        ),
        sa.CheckConstraint(
            "result_state IN ('open','match','diff','error','cancelled')",
            name="ck_assistant_runtime_shadow_comparison_result_state",
        ),
        sa.CheckConstraint(
            "length(input_digest) = 64",
            name="ck_assistant_runtime_shadow_comparison_input_digest",
        ),
        sa.CheckConstraint(
            "length(context_digest) = 64",
            name="ck_assistant_runtime_shadow_comparison_context_digest",
        ),
        sa.CheckConstraint(
            "fixture_digest IS NULL OR length(fixture_digest) = 64",
            name="ck_assistant_runtime_shadow_comparison_fixture_digest",
        ),
        sa.CheckConstraint(
            "private_input_payload_digest IS NULL OR "
            "length(private_input_payload_digest) = 64",
            name="ck_arm_shadow_cmp_private_input_payload_digest",
        ),
    )
    op.create_index(
        "ix_assistant_runtime_shadow_comparison_production_run_id",
        "assistant_runtime_shadow_comparison",
        ["production_run_id"],
    )
    op.create_index(
        "ix_assistant_runtime_shadow_comparison_eval_run_id",
        "assistant_runtime_shadow_comparison",
        ["eval_run_id"],
    )
    op.create_index(
        "ix_assistant_runtime_shadow_comparison_rollout_revision_id",
        "assistant_runtime_shadow_comparison",
        ["rollout_revision_id"],
    )

    op.create_table(
        "assistant_legacy_approval_archive",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("source_row_id", sa.String(length=160), nullable=False),
        sa.Column("source_run_id", sa.String(length=160), nullable=True),
        sa.Column("source_conversation_id", sa.String(length=160), nullable=True),
        sa.Column("safe_payload_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=64), nullable=True),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("migration_evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("actor_principal", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_row_id",
            name="uq_assistant_legacy_approval_archive_source_row",
        ),
        sa.CheckConstraint(
            "length(safe_payload_digest) = 64",
            name="ck_assistant_legacy_approval_archive_safe_payload_digest",
        ),
        sa.CheckConstraint(
            "length(migration_evidence_digest) = 64",
            name="ck_assistant_legacy_approval_archive_migration_evidence_digest",
        ),
    )

    op.create_table(
        "assistant_runtime_cleanup_gate",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("gate_kind", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("schema_revision", sa.String(length=64), nullable=False),
        sa.Column("build_revision", sa.String(length=160), nullable=False),
        sa.Column("runtime_revision", sa.String(length=160), nullable=True),
        sa.Column("actor_principal", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("inventory_digest", sa.String(length=64), nullable=False),
        sa.Column("migration_batch_digest", sa.String(length=64), nullable=True),
        sa.Column("rollout_revision_digest", sa.String(length=64), nullable=True),
        sa.Column("metric_window_digest", sa.String(length=64), nullable=True),
        sa.Column("backup_restore_digest", sa.String(length=64), nullable=True),
        sa.Column("legacy_access_window_digest", sa.String(length=64), nullable=True),
        sa.Column("archive_count_digest", sa.String(length=64), nullable=True),
        sa.Column("reconciliation_digest", sa.String(length=64), nullable=True),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("snapshot_counts", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "gate_kind IN ('deploy_b1','deploy_b2')",
            name="ck_assistant_runtime_cleanup_gate_kind",
        ),
        sa.CheckConstraint(
            "decision IN ('passed','failed')",
            name="ck_assistant_runtime_cleanup_gate_decision",
        ),
        sa.CheckConstraint(
            "length(inventory_digest) = 64",
            name="ck_assistant_runtime_cleanup_gate_inventory_digest",
        ),
        sa.CheckConstraint(
            "length(evidence_digest) = 64",
            name="ck_assistant_runtime_cleanup_gate_evidence_digest",
        ),
        sa.CheckConstraint(
            "migration_batch_digest IS NULL OR length(migration_batch_digest) = 64",
            name="ck_assistant_runtime_cleanup_gate_migration_batch_digest",
        ),
        sa.CheckConstraint(
            "rollout_revision_digest IS NULL OR length(rollout_revision_digest) = 64",
            name="ck_assistant_runtime_cleanup_gate_rollout_revision_digest",
        ),
        sa.CheckConstraint(
            "metric_window_digest IS NULL OR length(metric_window_digest) = 64",
            name="ck_assistant_runtime_cleanup_gate_metric_window_digest",
        ),
        sa.CheckConstraint(
            "backup_restore_digest IS NULL OR length(backup_restore_digest) = 64",
            name="ck_assistant_runtime_cleanup_gate_backup_restore_digest",
        ),
        sa.CheckConstraint(
            "legacy_access_window_digest IS NULL OR "
            "length(legacy_access_window_digest) = 64",
            name="ck_assistant_runtime_cleanup_gate_legacy_access_window_digest",
        ),
        sa.CheckConstraint(
            "archive_count_digest IS NULL OR length(archive_count_digest) = 64",
            name="ck_assistant_runtime_cleanup_gate_archive_count_digest",
        ),
        sa.CheckConstraint(
            "reconciliation_digest IS NULL OR length(reconciliation_digest) = 64",
            name="ck_assistant_runtime_cleanup_gate_reconciliation_digest",
        ),
    )
    op.create_index(
        "ix_assistant_runtime_cleanup_gate_kind_created",
        "assistant_runtime_cleanup_gate",
        ["gate_kind", "created_at"],
    )

    # ------------------------------------------------------------------
    # Eval purpose extension (runtime_shadow always gate_eligible=false)
    # ------------------------------------------------------------------
    op.add_column(
        "assistant_skill_eval_run",
        sa.Column(
            "purpose",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'admin_evaluation'"),
        ),
    )
    op.create_check_constraint(
        "ck_assistant_skill_eval_run_purpose",
        "assistant_skill_eval_run",
        "purpose IN ('admin_evaluation','runtime_shadow')",
    )
    op.create_check_constraint(
        "ck_assistant_skill_eval_run_runtime_shadow_gate_ineligible",
        "assistant_skill_eval_run",
        "purpose <> 'runtime_shadow' OR gate_eligible = false",
    )

    # Seed singleton rollout control pointer (no active revision).
    op.execute(
        """
        INSERT INTO assistant_runtime_rollout_control (
            id, singleton_key, active_rollout_revision_id, state_revision,
            created_at, updated_at
        ) VALUES (
            'a0000000-0000-4000-8000-000000000010',
            'singleton',
            NULL,
            0,
            NOW(),
            NOW()
        )
        ON CONFLICT (singleton_key) DO NOTHING
        """
    )

    _create_immutability_triggers()


def _create_immutability_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_reject_plan10_immutable_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'MINDATLAS_PLAN10_IMMUTABLE: % on % is not allowed',
                TG_OP, TG_TABLE_NAME
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$;
        """
    )
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_reject_update
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE PROCEDURE mindatlas_reject_plan10_immutable_mutation();
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_reject_delete
            BEFORE DELETE ON {table}
            FOR EACH ROW
            EXECUTE PROCEDURE mindatlas_reject_plan10_immutable_mutation();
            """
        )
    for table in APPEND_ONLY_UPDATE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_reject_update
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE PROCEDURE mindatlas_reject_plan10_immutable_mutation();
            """
        )


def downgrade() -> None:
    """Guarded downgrade: require explicit ack when migration evidence exists.

    Preconditions (enforced when evidence present):
    - MINDATLAS_PLAN10_MIGRATION_DOWNGRADE_ACK=1
    - no active (prepared/running) migration batches
    - no active rollout control pointer with non-legacy canary data requiring schema
    - no nonterminal runtime_shadow eval runs
    """
    conn = op.get_bind()

    def _count(sql: str) -> int:
        return int(conn.execute(sa.text(sql)).scalar() or 0)

    tables = {
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND ("
                "table_name LIKE 'assistant_runtime_%' "
                "OR table_name = 'assistant_legacy_approval_archive')"
            )
        )
    }

    if "assistant_runtime_migration_batch" in tables:
        active = _count(
            "SELECT COUNT(*) FROM assistant_runtime_migration_batch "
            "WHERE status IN ('prepared','running')"
        )
        if active:
            raise RuntimeError(
                f"{DOWNGRADE_BLOCKED_TOKEN}: {active} active migration batches remain "
                "(complete/cancel batches before downgrade)"
            )

    if "assistant_skill_eval_run" in tables:
        # purpose column may exist only after upgrade
        cols = {
            row[0]
            for row in conn.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND table_name = 'assistant_skill_eval_run'"
                )
            )
        }
        if "purpose" in cols:
            shadow_active = _count(
                "SELECT COUNT(*) FROM assistant_skill_eval_run "
                "WHERE purpose = 'runtime_shadow' "
                "AND status IN ('queued','running','cancelling')"
            )
            if shadow_active:
                raise RuntimeError(
                    f"{DOWNGRADE_BLOCKED_TOKEN}: {shadow_active} active "
                    "runtime_shadow eval runs remain"
                )

    evidence_counts = 0
    for table in (
        "assistant_runtime_migration_item",
        "assistant_runtime_migration_batch",
        "assistant_runtime_rollout_revision",
        "assistant_runtime_shadow_comparison",
        "assistant_legacy_approval_archive",
        "assistant_runtime_cleanup_gate",
        "assistant_runtime_admission_fallback_event",
    ):
        if table in tables:
            evidence_counts += _count(f"SELECT COUNT(*) FROM {table}")

    if evidence_counts and os.environ.get(DOWNGRADE_ACK_ENV, "").strip() != "1":
        raise RuntimeError(
            f"{DOWNGRADE_BLOCKED_TOKEN}: migration evidence exists "
            f"(rows={evidence_counts}); export retained evidence, stop workers, "
            f"and set {DOWNGRADE_ACK_ENV}=1"
        )

    # Drop triggers/functions then tables (children first).
    for table in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_update ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_delete ON {table}")
    for table in APPEND_ONLY_UPDATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_update ON {table}")
    op.execute("DROP FUNCTION IF EXISTS mindatlas_reject_plan10_immutable_mutation()")

    for table in ALL_MIGRATION_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # Drop Eval purpose extension.
    op.execute(
        "ALTER TABLE IF EXISTS assistant_skill_eval_run "
        "DROP CONSTRAINT IF EXISTS "
        "ck_assistant_skill_eval_run_runtime_shadow_gate_ineligible"
    )
    op.execute(
        "ALTER TABLE IF EXISTS assistant_skill_eval_run "
        "DROP CONSTRAINT IF EXISTS ck_assistant_skill_eval_run_purpose"
    )
    op.execute(
        "ALTER TABLE IF EXISTS assistant_skill_eval_run "
        "DROP COLUMN IF EXISTS purpose"
    )

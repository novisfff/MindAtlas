"""add skill evaluation workbench

Revision ID: 027869a00a47
Revises: 403414a62e55
Create Date: 2026-07-20 01:40:22.950366

Plan 09 Task 3: evaluation datasets/runs/results/capability-calls/events/
artifacts/publish-gates. Parent is Task 1 head ``403414a62e55`` only.
Produces persistence only — no runner/worker/gate enforcement.
"""

from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "027869a00a47"
down_revision = "403414a62e55"
branch_labels = None
depends_on = None

DOWNGRADE_BLOCKED_TOKEN = "MINDATLAS_PLAN09_EVAL_DOWNGRADE_BLOCKED"
DOWNGRADE_ACK_ENV = "MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK"

# Fully immutable (no UPDATE/DELETE). Events/artifacts are append-only for
# UPDATE but allow DELETE for reference-driven retention cleanup.
IMMUTABLE_TABLES = (
    "assistant_skill_eval_dataset_version",
    "assistant_skill_eval_case",
    "assistant_skill_eval_case_result",
    "assistant_skill_eval_capability_call",
    "assistant_skill_publish_gate",
    "assistant_skill_publish_gate_use",
)
APPEND_ONLY_UPDATE_TABLES = (
    "assistant_skill_eval_event",
    "assistant_skill_eval_artifact",
)

ALL_EVAL_TABLES = (
    "assistant_skill_publish_gate_use",
    "assistant_skill_publish_gate",
    "assistant_skill_eval_artifact",
    "assistant_skill_eval_event",
    "assistant_skill_eval_capability_call",
    "assistant_skill_eval_case_result",
    "assistant_skill_eval_run",
    "assistant_skill_eval_case",
    "assistant_skill_eval_dataset_draft",
    "assistant_skill_eval_dataset",
    "assistant_skill_eval_dataset_version",
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Dataset aggregate + draft + version + cases
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_skill_eval_dataset",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("stable_key", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column(
            "description",
            sa.String(length=2048),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "ownership",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'system'"),
        ),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "aggregate_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ownership IN ('system','custom')",
            name="ck_assistant_skill_eval_dataset_ownership",
        ),
        sa.CheckConstraint(
            "aggregate_revision >= 0",
            name="ck_assistant_skill_eval_dataset_aggregate_revision",
        ),
        sa.CheckConstraint(
            "(archived_at IS NULL AND archived_by IS NULL) OR (archived_at IS NOT NULL)",
            name="ck_assistant_skill_eval_dataset_archived_shape",
        ),
        sa.UniqueConstraint("stable_key", name="uq_assistant_skill_eval_dataset_stable_key"),
    )
    op.create_index(
        "ix_assistant_skill_eval_dataset_stable_key",
        "assistant_skill_eval_dataset",
        ["stable_key"],
    )
    op.create_index(
        "ix_assistant_skill_eval_dataset_current_version_id",
        "assistant_skill_eval_dataset",
        ["current_version_id"],
    )

    op.create_table(
        "assistant_skill_eval_dataset_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("version_name", sa.String(length=128), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("source_fixture_revision", sa.String(length=160), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["assistant_skill_eval_dataset.id"],
            name="fk_assistant_skill_eval_dataset_version_dataset_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "sequence",
            name="uq_assistant_skill_eval_dataset_version_sequence",
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "content_digest",
            name="uq_assistant_skill_eval_dataset_version_digest",
        ),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_assistant_skill_eval_dataset_version_sequence",
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_assistant_skill_eval_dataset_version_schema_version",
        ),
        sa.CheckConstraint(
            "length(content_digest) = 64",
            name="ck_assistant_skill_eval_dataset_version_content_digest",
        ),
    )
    op.create_index(
        "ix_assistant_skill_eval_dataset_version_dataset_id",
        "assistant_skill_eval_dataset_version",
        ["dataset_id"],
    )

    # Deferred FKs on dataset current_version_id (circular with version table).
    op.create_foreign_key(
        "fk_assistant_skill_eval_dataset_current_version_id",
        "assistant_skill_eval_dataset",
        "assistant_skill_eval_dataset_version",
        ["current_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "assistant_skill_eval_dataset_draft",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "draft_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("cases_snapshot", sa.JSON(), nullable=False),
        sa.Column("draft_digest", sa.String(length=64), nullable=False),
        sa.Column("base_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column("last_validation_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["assistant_skill_eval_dataset.id"],
            name="fk_assistant_skill_eval_dataset_draft_dataset_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["base_version_id"],
            ["assistant_skill_eval_dataset_version.id"],
            name="fk_assistant_skill_eval_dataset_draft_base_version_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "dataset_id",
            name="uq_assistant_skill_eval_dataset_draft_dataset_id",
        ),
        sa.CheckConstraint(
            "draft_revision >= 0",
            name="ck_assistant_skill_eval_dataset_draft_revision",
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_assistant_skill_eval_dataset_draft_schema_version",
        ),
        sa.CheckConstraint(
            "length(draft_digest) = 64",
            name="ck_assistant_skill_eval_dataset_draft_digest",
        ),
        sa.CheckConstraint(
            "last_validation_digest IS NULL OR length(last_validation_digest) = 64",
            name="ck_assistant_skill_eval_dataset_draft_validation_digest",
        ),
    )
    op.create_index(
        "ix_assistant_skill_eval_dataset_draft_dataset_id",
        "assistant_skill_eval_dataset_draft",
        ["dataset_id"],
    )

    op.create_table(
        "assistant_skill_eval_case",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("dataset_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_key", sa.String(length=128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "locale",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'en'"),
        ),
        sa.Column("input_messages", sa.JSON(), nullable=False),
        sa.Column("fixture_refs", sa.JSON(), nullable=False),
        sa.Column("expected_mode", sa.String(length=64), nullable=False),
        sa.Column("acceptable_skill_keys", sa.JSON(), nullable=False),
        sa.Column("forbidden_skill_keys", sa.JSON(), nullable=False),
        sa.Column("acceptable_capability_paths", sa.JSON(), nullable=False),
        sa.Column("forbidden_side_effect_classes", sa.JSON(), nullable=False),
        sa.Column(
            "expect_completion",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("assertion_json", sa.JSON(), nullable=False),
        sa.Column("ceilings_json", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column(
            "notes",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("case_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["assistant_skill_eval_dataset_version.id"],
            name="fk_assistant_skill_eval_case_dataset_version_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "case_key",
            name="uq_assistant_skill_eval_case_key",
        ),
        sa.UniqueConstraint(
            "dataset_version_id",
            "ordinal",
            name="uq_assistant_skill_eval_case_ordinal",
        ),
        sa.CheckConstraint(
            "ordinal >= 0",
            name="ck_assistant_skill_eval_case_ordinal",
        ),
        sa.CheckConstraint(
            "length(case_digest) = 64",
            name="ck_assistant_skill_eval_case_digest",
        ),
    )
    op.create_index(
        "ix_assistant_skill_eval_case_dataset_version_id",
        "assistant_skill_eval_case",
        ["dataset_version_id"],
    )

    # ------------------------------------------------------------------
    # Eval Run + results / calls / events / artifacts
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_skill_eval_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("subject_kind", sa.String(length=64), nullable=False),
        sa.Column("subject_aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_content_digest", sa.String(length=64), nullable=False),
        sa.Column("subject_binding_digest", sa.String(length=64), nullable=False),
        sa.Column("dataset_version_ids", sa.JSON(), nullable=False),
        sa.Column("threshold_policy_version", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("isolation_namespace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "owner_kind",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'test'"),
        ),
        sa.Column("runtime_contract_version", sa.Integer(), nullable=False),
        sa.Column("required_build_revision", sa.String(length=160), nullable=False),
        sa.Column(
            "runner_contract_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "state_revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column(
            "lease_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_cancel_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_event_seq",
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
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("isolation_digest", sa.String(length=64), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=True),
        sa.Column("runtime_digest", sa.String(length=64), nullable=True),
        sa.Column("provider_evidence_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "evidence_provenance",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'structural_synthetic'"),
        ),
        sa.Column("provider_fixture_revision", sa.String(length=160), nullable=True),
        sa.Column("provider_fixture_digest", sa.String(length=64), nullable=True),
        sa.Column("aggregate_metrics", sa.JSON(), nullable=False),
        sa.Column(
            "gate_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("actor_principal", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "subject_kind IN ("
            "'skill_draft','skill_version',"
            "'main_agent_profile_draft','main_agent_profile_version',"
            "'legacy_baseline'"
            ")",
            name="ck_assistant_skill_eval_run_subject_kind",
        ),
        sa.CheckConstraint(
            "mode IN ('interactive_scripted','dataset_scripted','dataset_live')",
            name="ck_assistant_skill_eval_run_mode",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'queued','running','cancelling','completed','failed','cancelled'"
            ")",
            name="ck_assistant_skill_eval_run_status",
        ),
        sa.CheckConstraint(
            "owner_kind = 'test'",
            name="ck_assistant_skill_eval_run_owner_kind",
        ),
        sa.CheckConstraint(
            "state_revision >= 0",
            name="ck_assistant_skill_eval_run_state_revision",
        ),
        sa.CheckConstraint(
            "lease_generation >= 0",
            name="ck_assistant_skill_eval_run_lease_generation",
        ),
        sa.CheckConstraint(
            "last_event_seq >= 0",
            name="ck_assistant_skill_eval_run_last_event_seq",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_assistant_skill_eval_run_attempt_count",
        ),
        sa.CheckConstraint(
            "runtime_contract_version > 0",
            name="ck_assistant_skill_eval_run_runtime_contract_version",
        ),
        sa.CheckConstraint(
            "runner_contract_version > 0",
            name="ck_assistant_skill_eval_run_runner_contract_version",
        ),
        sa.CheckConstraint(
            "length(subject_content_digest) = 64",
            name="ck_assistant_skill_eval_run_subject_content_digest",
        ),
        sa.CheckConstraint(
            "length(subject_binding_digest) = 64",
            name="ck_assistant_skill_eval_run_subject_binding_digest",
        ),
        sa.CheckConstraint(
            "length(isolation_digest) = 64",
            name="ck_assistant_skill_eval_run_isolation_digest",
        ),
        sa.CheckConstraint(
            "policy_digest IS NULL OR length(policy_digest) = 64",
            name="ck_assistant_skill_eval_run_policy_digest",
        ),
        sa.CheckConstraint(
            "runtime_digest IS NULL OR length(runtime_digest) = 64",
            name="ck_assistant_skill_eval_run_runtime_digest",
        ),
        sa.CheckConstraint(
            "provider_evidence_digest IS NULL OR length(provider_evidence_digest) = 64",
            name="ck_assistant_skill_eval_run_provider_evidence_digest",
        ),
        sa.CheckConstraint(
            "evidence_provenance IN ("
            "'real_orchestration','structural_synthetic','live_model'"
            ")",
            name="ck_assistant_skill_eval_run_evidence_provenance",
        ),
        sa.CheckConstraint(
            "provider_fixture_digest IS NULL OR length(provider_fixture_digest) = 64",
            name="ck_assistant_skill_eval_run_provider_fixture_digest",
        ),
        sa.CheckConstraint(
            "("
            "provider_fixture_revision IS NULL AND provider_fixture_digest IS NULL"
            ") OR ("
            "provider_fixture_revision IS NOT NULL "
            "AND length(provider_fixture_revision) > 0 "
            "AND length(provider_fixture_revision) <= 160 "
            "AND provider_fixture_digest IS NOT NULL "
            "AND length(provider_fixture_digest) = 64"
            ")",
            name="ck_assistant_skill_eval_run_provider_fixture_shape",
        ),
        sa.CheckConstraint(
            "evidence_provenance <> 'structural_synthetic' OR gate_eligible = false",
            name="ck_assistant_skill_eval_run_synthetic_gate_ineligible",
        ),
    )
    op.create_index(
        "ix_assistant_skill_eval_run_subject_aggregate_id",
        "assistant_skill_eval_run",
        ["subject_aggregate_id"],
    )
    op.create_index(
        "ix_assistant_skill_eval_run_subject_version_id",
        "assistant_skill_eval_run",
        ["subject_version_id"],
    )
    op.create_index(
        "ix_assistant_skill_eval_run_status_created",
        "assistant_skill_eval_run",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_assistant_skill_eval_run_lease_claim",
        "assistant_skill_eval_run",
        ["status", "lease_expires_at", "created_at"],
    )

    op.create_table(
        "assistant_skill_eval_case_result",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("eval_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("eval_case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_state", sa.String(length=32), nullable=False),
        sa.Column("assertion_details", sa.JSON(), nullable=False),
        sa.Column("actual_active_skills", sa.JSON(), nullable=False),
        sa.Column("visible_capability_aliases", sa.JSON(), nullable=False),
        sa.Column("call_trace", sa.JSON(), nullable=False),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("output_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("rounds", sa.Integer(), nullable=True),
        sa.Column("calls", sa.Integer(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("safe_error", sa.Text(), nullable=True),
        sa.Column("result_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["assistant_skill_eval_run.id"],
            name="fk_assistant_skill_eval_case_result_eval_run_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["eval_case_id"],
            ["assistant_skill_eval_case.id"],
            name="fk_assistant_skill_eval_case_result_eval_case_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "eval_run_id",
            "eval_case_id",
            name="uq_assistant_skill_eval_case_result_run_case",
        ),
        sa.CheckConstraint(
            "result_state IN ("
            "'passed','failed','indeterminate','error','skipped','cancelled'"
            ")",
            name="ck_assistant_skill_eval_case_result_state",
        ),
        sa.CheckConstraint(
            "rounds IS NULL OR rounds >= 0",
            name="ck_assistant_skill_eval_case_result_rounds",
        ),
        sa.CheckConstraint(
            "calls IS NULL OR calls >= 0",
            name="ck_assistant_skill_eval_case_result_calls",
        ),
        sa.CheckConstraint(
            "tokens IS NULL OR tokens >= 0",
            name="ck_assistant_skill_eval_case_result_tokens",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_assistant_skill_eval_case_result_latency_ms",
        ),
        sa.CheckConstraint(
            "length(result_digest) = 64",
            name="ck_assistant_skill_eval_case_result_digest",
        ),
    )
    op.create_index(
        "ix_assistant_skill_eval_case_result_eval_run_id",
        "assistant_skill_eval_case_result",
        ["eval_run_id"],
    )
    op.create_index(
        "ix_assistant_skill_eval_case_result_eval_case_id",
        "assistant_skill_eval_case_result",
        ["eval_case_id"],
    )

    # Eval CapabilityCall: synthetic IDs, NO production ledger FK.
    op.create_table(
        "assistant_skill_eval_capability_call",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("eval_call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("eval_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("eval_case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_call_key", sa.String(length=256), nullable=False),
        sa.Column("parent_ordinal", sa.Integer(), nullable=True),
        sa.Column(
            "child_ordinal",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "attempt",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "owner_kind",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'test'"),
        ),
        sa.Column("subject_kind", sa.String(length=64), nullable=False),
        sa.Column("subject_aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_owner_digest", sa.String(length=64), nullable=False),
        sa.Column("binding_digest", sa.String(length=64), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("descriptor_digest", sa.String(length=64), nullable=False),
        sa.Column("policy_digest", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("decision_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["assistant_skill_eval_run.id"],
            name="fk_assistant_skill_eval_capability_call_eval_run_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["eval_case_id"],
            ["assistant_skill_eval_case.id"],
            name="fk_assistant_skill_eval_capability_call_eval_case_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "eval_call_id",
            name="uq_assistant_skill_eval_capability_call_eval_call_id",
        ),
        sa.UniqueConstraint(
            "eval_run_id",
            "eval_case_id",
            "logical_call_key",
            "attempt",
            name="uq_assistant_skill_eval_capability_call_attempt",
        ),
        sa.CheckConstraint(
            "owner_kind = 'test'",
            name="ck_assistant_skill_eval_capability_call_owner_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded_isolated','simulated','denied','failed')",
            name="ck_assistant_skill_eval_capability_call_outcome",
        ),
        sa.CheckConstraint(
            "attempt > 0",
            name="ck_assistant_skill_eval_capability_call_attempt",
        ),
        sa.CheckConstraint(
            "child_ordinal >= 0",
            name="ck_assistant_skill_eval_capability_call_child_ordinal",
        ),
        sa.CheckConstraint(
            "parent_ordinal IS NULL OR parent_ordinal >= 0",
            name="ck_assistant_skill_eval_capability_call_parent_ordinal",
        ),
        sa.CheckConstraint(
            "length(subject_owner_digest) = 64",
            name="ck_assistant_skill_eval_capability_call_subject_owner_digest",
        ),
        sa.CheckConstraint(
            "length(binding_digest) = 64",
            name="ck_assistant_skill_eval_capability_call_binding_digest",
        ),
        sa.CheckConstraint(
            "length(input_digest) = 64",
            name="ck_assistant_skill_eval_capability_call_input_digest",
        ),
        sa.CheckConstraint(
            "length(descriptor_digest) = 64",
            name="ck_assistant_skill_eval_capability_call_descriptor_digest",
        ),
        sa.CheckConstraint(
            "length(policy_digest) = 64",
            name="ck_assistant_skill_eval_capability_call_policy_digest",
        ),
    )
    op.create_index(
        "ix_assistant_skill_eval_capability_call_eval_call_id",
        "assistant_skill_eval_capability_call",
        ["eval_call_id"],
    )
    op.create_index(
        "ix_assistant_skill_eval_capability_call_eval_run_id",
        "assistant_skill_eval_capability_call",
        ["eval_run_id"],
    )
    op.create_index(
        "ix_assistant_skill_eval_capability_call_eval_case_id",
        "assistant_skill_eval_capability_call",
        ["eval_case_id"],
    )

    op.create_table(
        "assistant_skill_eval_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("eval_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["assistant_skill_eval_run.id"],
            name="fk_assistant_skill_eval_event_eval_run_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "eval_run_id",
            "sequence",
            name="uq_assistant_skill_eval_event_sequence",
        ),
        sa.CheckConstraint(
            "sequence > 0",
            name="ck_assistant_skill_eval_event_sequence",
        ),
    )
    op.create_index(
        "ix_assistant_skill_eval_event_eval_run_id",
        "assistant_skill_eval_event",
        ["eval_run_id"],
    )

    op.create_table(
        "assistant_skill_eval_artifact",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("eval_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("storage_kind", sa.String(length=16), nullable=False),
        sa.Column("inline_payload", sa.LargeBinary(), nullable=True),
        sa.Column("object_key", sa.String(length=1024), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["assistant_skill_eval_run.id"],
            name="fk_assistant_skill_eval_artifact_eval_run_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "storage_kind IN ('inline','object')",
            name="ck_assistant_skill_eval_artifact_storage_kind",
        ),
        sa.CheckConstraint(
            "byte_size >= 0",
            name="ck_assistant_skill_eval_artifact_byte_size",
        ),
        sa.CheckConstraint(
            "("
            "  storage_kind = 'inline'"
            "  AND inline_payload IS NOT NULL"
            "  AND object_key IS NULL"
            ") OR ("
            "  storage_kind = 'object'"
            "  AND inline_payload IS NULL"
            "  AND object_key IS NOT NULL"
            ")",
            name="ck_assistant_skill_eval_artifact_storage_xor",
        ),
        sa.CheckConstraint(
            "length(content_digest) = 64",
            name="ck_assistant_skill_eval_artifact_content_digest",
        ),
        # object_key must be evaluation-namespace when present
        sa.CheckConstraint(
            "object_key IS NULL OR object_key LIKE 'skill-eval/%'",
            name="ck_assistant_skill_eval_artifact_object_key_namespace",
        ),
    )
    op.create_index(
        "ix_assistant_skill_eval_artifact_eval_run_id",
        "assistant_skill_eval_artifact",
        ["eval_run_id"],
    )
    op.create_index(
        "uq_assistant_skill_eval_artifact_content",
        "assistant_skill_eval_artifact",
        ["eval_run_id", "content_digest", "byte_size"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # Publish gate + gate use
    # ------------------------------------------------------------------
    op.create_table(
        "assistant_skill_publish_gate",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("subject_kind", sa.String(length=64), nullable=False),
        sa.Column("subject_aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_content_digest", sa.String(length=64), nullable=False),
        sa.Column("subject_binding_digest", sa.String(length=64), nullable=False),
        sa.Column("profile_digest", sa.String(length=64), nullable=False),
        sa.Column("catalog_digest", sa.String(length=64), nullable=False),
        sa.Column("dataset_version_ids", sa.JSON(), nullable=False),
        sa.Column("qualifying_eval_run_ids", sa.JSON(), nullable=False),
        sa.Column("runtime_contract_version", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("threshold_version", sa.String(length=64), nullable=False),
        sa.Column("build_revision", sa.String(length=160), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("assertion_snapshot", sa.JSON(), nullable=False),
        sa.Column("metric_snapshot", sa.JSON(), nullable=False),
        sa.Column("actor_principal", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("waiver_codes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "publication_pin_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "subject_kind IN ("
            "'skill_draft','skill_version',"
            "'main_agent_profile_draft','main_agent_profile_version',"
            "'legacy_baseline'"
            ")",
            name="ck_assistant_skill_publish_gate_subject_kind",
        ),
        sa.CheckConstraint(
            "decision IN ('passed','failed','waived_non_safety')",
            name="ck_assistant_skill_publish_gate_decision",
        ),
        sa.CheckConstraint(
            "runtime_contract_version > 0",
            name="ck_assistant_skill_publish_gate_runtime_contract_version",
        ),
        sa.CheckConstraint(
            "publication_pin_count >= 0",
            name="ck_assistant_skill_publish_gate_publication_pin_count",
        ),
        sa.CheckConstraint(
            "length(subject_content_digest) = 64",
            name="ck_assistant_skill_publish_gate_subject_content_digest",
        ),
        sa.CheckConstraint(
            "length(subject_binding_digest) = 64",
            name="ck_assistant_skill_publish_gate_subject_binding_digest",
        ),
        sa.CheckConstraint(
            "length(profile_digest) = 64",
            name="ck_assistant_skill_publish_gate_profile_digest",
        ),
        sa.CheckConstraint(
            "length(catalog_digest) = 64",
            name="ck_assistant_skill_publish_gate_catalog_digest",
        ),
        sa.UniqueConstraint(
            "request_id",
            name="uq_assistant_skill_publish_gate_request_id",
        ),
    )
    op.create_index(
        "ix_assistant_skill_publish_gate_subject_aggregate_id",
        "assistant_skill_publish_gate",
        ["subject_aggregate_id"],
    )
    op.create_index(
        "ix_assistant_skill_publish_gate_subject_version_id",
        "assistant_skill_publish_gate",
        ["subject_version_id"],
    )
    op.create_index(
        "ix_assistant_skill_publish_gate_subject_created",
        "assistant_skill_publish_gate",
        ["subject_aggregate_id", "subject_version_id", "created_at"],
    )

    op.create_table(
        "assistant_skill_publish_gate_use",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("gate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resulting_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_principal", sa.String(length=128), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("aggregate_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["gate_id"],
            ["assistant_skill_publish_gate.id"],
            name="fk_assistant_skill_publish_gate_use_gate_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "request_id",
            "action",
            name="uq_assistant_skill_publish_gate_use_request_action",
        ),
        sa.CheckConstraint(
            "action IN ("
            "'skill_publish','skill_catalog_enable',"
            "'profile_publish','profile_runtime_enable'"
            ")",
            name="ck_assistant_skill_publish_gate_use_action",
        ),
        sa.CheckConstraint(
            "aggregate_revision >= 0",
            name="ck_assistant_skill_publish_gate_use_aggregate_revision",
        ),
    )
    op.create_index(
        "ix_assistant_skill_publish_gate_use_gate_id",
        "assistant_skill_publish_gate_use",
        ["gate_id"],
    )
    op.create_index(
        "ix_assistant_skill_publish_gate_use_aggregate_id",
        "assistant_skill_publish_gate_use",
        ["aggregate_id"],
    )

    _create_immutability_triggers()
    _create_eval_run_transition_guard()


def _create_immutability_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_reject_eval_immutable_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'MINDATLAS_PLAN09_EVAL_IMMUTABLE: % on % is not allowed',
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
            EXECUTE PROCEDURE mindatlas_reject_eval_immutable_mutation();
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_reject_delete
            BEFORE DELETE ON {table}
            FOR EACH ROW
            EXECUTE PROCEDURE mindatlas_reject_eval_immutable_mutation();
            """
        )
    for table in APPEND_ONLY_UPDATE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_reject_update
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE PROCEDURE mindatlas_reject_eval_immutable_mutation();
            """
        )


def _create_eval_run_transition_guard() -> None:
    """Reject illegal status transitions at the DB layer (defense in depth)."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mindatlas_eval_run_transition_guard()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.state_revision < OLD.state_revision THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN09_EVAL_REVISION: state_revision must be monotonic'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.status IN ('completed','failed','cancelled')
               AND NEW.status IS DISTINCT FROM OLD.status THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN09_EVAL_TERMINAL: terminal run status is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF OLD.status IS DISTINCT FROM NEW.status THEN
                IF NOT (
                    (OLD.status = 'queued' AND NEW.status IN ('running','cancelling','cancelled','failed'))
                    OR (OLD.status = 'running' AND NEW.status IN (
                        'completed','failed','cancelled','queued','cancelling'
                    ))
                    OR (OLD.status = 'cancelling' AND NEW.status = 'cancelled')
                ) THEN
                    RAISE EXCEPTION
                        'MINDATLAS_PLAN09_EVAL_TRANSITION: % -> % not allowed',
                        OLD.status, NEW.status
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;
            IF NEW.owner_kind IS DISTINCT FROM 'test' THEN
                RAISE EXCEPTION
                    'MINDATLAS_PLAN09_EVAL_OWNER: owner_kind must remain test'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_assistant_skill_eval_run_transition
        BEFORE UPDATE ON assistant_skill_eval_run
        FOR EACH ROW
        EXECUTE PROCEDURE mindatlas_eval_run_transition_guard();
        """
    )


def downgrade() -> None:
    """Guarded downgrade: require explicit ack when evaluation evidence exists.

    Preconditions (enforced when evidence present):
    - MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK=1 (workers stopped / evidence exported)
    - no queued/running eval runs
    - no publication-pinned gates (EXISTS gate_use rows)
    """
    conn = op.get_bind()

    def _count(sql: str) -> int:
        return int(conn.execute(sa.text(sql)).scalar() or 0)

    # Tables may already be gone on partial downgrade; only guard when present.
    tables = {
        row[0]
        for row in conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND ("
                "table_name LIKE 'assistant_skill_eval%' "
                "OR table_name LIKE 'assistant_skill_publish_gate%')"
            )
        )
    }

    if "assistant_skill_eval_run" in tables:
        active = _count(
            "SELECT COUNT(*) FROM assistant_skill_eval_run "
            "WHERE status IN ('queued','running','cancelling')"
        )
        if active:
            raise RuntimeError(
                f"{DOWNGRADE_BLOCKED_TOKEN}: {active} active eval runs remain "
                "(stop workers; no queued/running runs allowed)"
            )

    # Pin correctness is derived from gate_use existence (not publication_pin_count).
    if "assistant_skill_publish_gate_use" in tables:
        pinned = _count(
            "SELECT COUNT(DISTINCT gate_id) FROM assistant_skill_publish_gate_use"
        )
        if pinned:
            raise RuntimeError(
                f"{DOWNGRADE_BLOCKED_TOKEN}: {pinned} publication-pinned gates remain "
                "(export retained evidence; no published/catalog dependency)"
            )
    elif "assistant_skill_publish_gate" in tables:
        # Table presence without gate_use is fine; fall through to evidence ack.
        pass

    if "assistant_skill_publish_gate" in tables or "assistant_skill_eval_run" in tables:
        total_gates = (
            _count("SELECT COUNT(*) FROM assistant_skill_publish_gate")
            if "assistant_skill_publish_gate" in tables
            else 0
        )
        total_runs = (
            _count("SELECT COUNT(*) FROM assistant_skill_eval_run")
            if "assistant_skill_eval_run" in tables
            else 0
        )
        if (total_gates or total_runs) and os.environ.get(DOWNGRADE_ACK_ENV, "").strip() != "1":
            raise RuntimeError(
                f"{DOWNGRADE_BLOCKED_TOKEN}: evaluation evidence exists "
                f"(gates={total_gates}, runs={total_runs}); "
                f"export retained evidence, stop workers, and set {DOWNGRADE_ACK_ENV}=1"
            )

    # Drop triggers/functions then tables (children first).
    # Always use DROP TRIGGER IF EXISTS ... ON <table> for safe partial downgrades.
    op.execute(
        "DROP TRIGGER IF EXISTS trg_assistant_skill_eval_run_transition "
        "ON assistant_skill_eval_run"
    )
    op.execute("DROP FUNCTION IF EXISTS mindatlas_eval_run_transition_guard()")

    for table in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_update ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_delete ON {table}")
    for table in APPEND_ONLY_UPDATE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_update ON {table}")
    op.execute("DROP FUNCTION IF EXISTS mindatlas_reject_eval_immutable_mutation()")

    # Drop deferred FKs that point at version from dataset/draft before tables.
    op.execute(
        "ALTER TABLE IF EXISTS assistant_skill_eval_dataset "
        "DROP CONSTRAINT IF EXISTS fk_assistant_skill_eval_dataset_current_version_id"
    )
    op.execute(
        "ALTER TABLE IF EXISTS assistant_skill_eval_dataset_draft "
        "DROP CONSTRAINT IF EXISTS fk_assistant_skill_eval_dataset_draft_base_version_id"
    )

    for table in ALL_EVAL_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

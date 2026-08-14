"""Additive pre-GA launch state and advance the clean schema identity.

This revision is intentionally self-contained.  It performs a fresh-database
preflight before any DDL, creates only the launch tables/guards owned by this
plan, adds the frozen write fields, and advances the marker in the same
transaction.  The Plan 3 root is never edited or reconnected.
"""

from __future__ import annotations

import hashlib
import json
import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "pre_ga_v1_0002"
down_revision = "pre_ga_v1_0001"
branch_labels = None
depends_on = None

SCHEMA_FAMILY = "pre_ga_v1"
SCHEMA_REVISION = "pre_ga_v1_0002"
ROOT_REVISION = "pre_ga_v1_0001"
ROOT_APPLICATION_FINGERPRINT = "7dda92eee351071dabb9a274399769b1ca01dce07382cb6462653809c5cfbaab"
ROOT_CONTROL_FINGERPRINT = "6bf3db9018a22c66055ade8d16a98dac2fdcf4fd0d97b03077da3bc5641dade7"
ROOT_SEED_CONTRACT_DIGEST = "a728d696b086b0ced78a37de80a7831cd788e22f7668f083a7245706b13334ba"
ROOT_CAPABILITY_FEATURE_DIGEST = "11af8408a0d3a6ff93a5170a9bb6758f430773d1e1343ee3982396f0ed9cd3b4"

# Replaced by the identity generator after the first fresh DDL introspection.
# Keeping the value literal is part of the migration audit contract.
EXPECTED_APPLICATION_FINGERPRINT = "6b74fe6a8d75b4432098172f11562260067031e0a33966df950c39f51f1561db"
CURRENT_SEED_CONTRACT_DIGEST = "a43229f3dc8b186690b0a899c26431537b030cea2fce625b92b6dc387cf7b3d7"
CURRENT_CAPABILITY_FEATURE_DIGEST = "c4050ef9aa836f418bb90ad772beeb4734fbb7815cf533926c0eb011710ad8aa"
IDENTITY_CONTRACT_VERSION = 1


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deployment_class() -> str:
    value = os.environ.get("MINDATLAS_DEPLOYMENT_CLASS", "").strip()
    if value not in {"development", "rehearsal", "production"}:
        raise RuntimeError("schema_deployment_class_invalid")
    return value


def _preflight(connection) -> dict[str, object]:
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext('mindatlas:pre_ga_v1_0002'))"))
    marker = connection.execute(
        sa.text(
            "SELECT schema_family, schema_revision, structural_fingerprint, "
            "seed_contract_digest, runtime_contract_version, "
            "checkpoint_codec_version, capability_feature_digest, "
            "operator_auth_contract_version, identity_contract_version, "
            "deployment_class FROM mindatlas_schema_identity "
            "WHERE singleton_key = 'current' FOR UPDATE"
        )
    ).mappings().one_or_none()
    if marker is None:
        raise RuntimeError("pre_ga_schema_identity_missing")
    if (
        marker["schema_family"] != SCHEMA_FAMILY
        or marker["schema_revision"] != ROOT_REVISION
        or marker["structural_fingerprint"] != ROOT_APPLICATION_FINGERPRINT
        or marker["identity_contract_version"] != IDENTITY_CONTRACT_VERSION
    ):
        raise RuntimeError("pre_ga_schema_identity_not_healthy_0001")
    rows = connection.execute(
        sa.text(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "AND c.relname NOT IN ('alembic_version', 'mindatlas_schema_identity') "
            "AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a "
            "WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) "
            "AND (SELECT count(*) FROM pg_catalog.pg_class c2 "
            "WHERE c2.oid = c.oid) >= 0"
        )
    ).all()
    # The relname query above deliberately does not rely on application table
    # names.  Count rows one table at a time so a future table cannot silently
    # bypass the fresh-reset boundary.
    nonempty: list[str] = []
    for (table_name,) in rows:
        quoted_table = str(table_name).replace('"', '""')
        # Serialize the empty-reset boundary with every application table so
        # a writer that does not know the launch lock cannot insert between
        # the count and the first additive DDL statement.
        connection.execute(
            sa.text(f'LOCK TABLE "public"."{quoted_table}" IN ACCESS EXCLUSIVE MODE')
        )
        count = connection.execute(
            sa.text(f'SELECT 1 FROM "public"."{quoted_table}" LIMIT 1')
        ).first()
        if count is not None:
            nonempty.append(str(table_name))
    if nonempty:
        raise RuntimeError("pre_ga_reset_required")
    return dict(marker)


def _create_launch_tables() -> None:
    digest = lambda column, name: sa.CheckConstraint(
        f"{column} ~ '^[0-9a-f]{{64}}$'", name=name
    )
    op.create_table(
        "pre_ga_launch_candidate",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_kind", sa.String(32), nullable=False),
        sa.Column("creation_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creation_request_digest", sa.String(64), nullable=False),
        sa.Column("created_by_operator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("qualification_target_json", postgresql.JSONB, nullable=False),
        sa.Column("qualification_target_digest", sa.String(64), nullable=False),
        sa.Column("subject_json", postgresql.JSONB, nullable=False),
        sa.Column("subject_digest", sa.String(64), nullable=False),
        sa.Column("build_revision", sa.String(128), nullable=False),
        sa.Column("image_set_digest", sa.String(64), nullable=False),
        sa.Column("deployed_artifact_set_digest", sa.String(64), nullable=False),
        sa.Column("schema_family", sa.String(32), nullable=False),
        sa.Column("schema_revision", sa.String(64), nullable=False),
        sa.Column("schema_runtime_identity_digest", sa.String(64), nullable=False),
        sa.Column("deployment_class", sa.String(16), nullable=False),
        sa.Column("operator_auth_contract_version", sa.String(64), nullable=False),
        sa.Column("rollout_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rollout_revision_digest", sa.String(64), nullable=False),
        sa.Column("runtime_closure_digest", sa.String(64), nullable=False),
        sa.Column("profile_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_content_digest", sa.String(64), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_identity_digest", sa.String(64), nullable=False),
        sa.Column("package_closure_digest", sa.String(64), nullable=False),
        sa.Column("capability_closure_digest", sa.String(64), nullable=False),
        sa.Column("seed_manifest_digest", sa.String(64), nullable=False),
        sa.Column("worker_runtime_contract_version", sa.Integer, nullable=False),
        sa.Column("worker_checkpoint_codec_version", sa.Integer, nullable=False),
        sa.Column("worker_capability_feature_digest", sa.String(64), nullable=False),
        sa.Column("create_entry_contract_digest", sa.String(64), nullable=False),
        sa.Column("write_policy_digest", sa.String(64), nullable=False),
        sa.Column("write_cohort_digest", sa.String(64), nullable=False),
        sa.Column("reconciliation_contract_version", sa.Integer, nullable=False),
        sa.Column("dependency_lock_set_digest", sa.String(64), nullable=False),
        sa.Column("scenario_set_digest", sa.String(64), nullable=False),
        sa.Column("required_assertion_set_digest", sa.String(64), nullable=False),
        sa.Column("runner_contract_version", sa.Integer, nullable=False),
        sa.Column("runner_identity_digest", sa.String(64), nullable=False),
        sa.Column("evidence_trust_set_digest", sa.String(64), nullable=False),
        sa.Column("automated_evidence_ref_json", postgresql.JSONB, nullable=False),
        sa.Column("automated_evidence_manifest_digest", sa.String(64), nullable=False),
        sa.Column("automated_attestation_digest", sa.String(64), nullable=False),
        sa.Column("rehearsal_evidence_ref_json", postgresql.JSONB, nullable=False),
        sa.Column("rehearsal_evidence_manifest_digest", sa.String(64), nullable=False),
        sa.Column("rehearsal_attestation_digest", sa.String(64), nullable=False),
        sa.Column("operational_snapshot_json", postgresql.JSONB, nullable=False),
        sa.Column("operational_snapshot_digest", sa.String(64), nullable=False),
        sa.Column("unknown_call_count", sa.Integer, nullable=False),
        sa.Column("needs_reconciliation_count", sa.Integer, nullable=False),
        sa.Column("active_run_count", sa.Integer, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("safe_failure_codes", postgresql.JSONB, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_operator_id"], ["operator_account.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("creation_request_id", name="uq_pre_ga_launch_candidate_creation_request_id"),
        sa.UniqueConstraint("id", "subject_digest", name="uq_pre_ga_launch_candidate_id_subject_digest"),
        sa.CheckConstraint("candidate_kind = 'pre_ga_launch'", name="ck_pre_ga_launch_candidate_kind"),
        sa.CheckConstraint("schema_family = 'pre_ga_v1' AND schema_revision = 'pre_ga_v1_0002' AND deployment_class = 'production'", name="ck_pre_ga_launch_candidate_schema_identity"),
        sa.CheckConstraint("length(reason) >= 1 AND length(reason) <= 500", name="ck_pre_ga_launch_candidate_reason_len"),
        sa.CheckConstraint("expires_at = issued_at + INTERVAL '24 hours'", name="ck_pre_ga_launch_candidate_expiry"),
        sa.CheckConstraint("unknown_call_count >= 0 AND needs_reconciliation_count >= 0 AND active_run_count >= 0", name="ck_pre_ga_launch_candidate_counts"),
        sa.CheckConstraint("passed = false OR (unknown_call_count = 0 AND needs_reconciliation_count = 0 AND active_run_count = 0 AND safe_failure_codes = '[]'::jsonb)", name="ck_pre_ga_launch_candidate_passed_shape"),
        sa.CheckConstraint("passed = true OR jsonb_array_length(safe_failure_codes) > 0", name="ck_pre_ga_launch_candidate_failed_shape"),
        sa.CheckConstraint("worker_runtime_contract_version > 0 AND worker_checkpoint_codec_version > 0 AND reconciliation_contract_version > 0 AND runner_contract_version > 0", name="ck_pre_ga_launch_candidate_positive_versions"),
        digest("creation_request_digest", "ck_pre_ga_launch_candidate_creation_request_digest"),
        digest("qualification_target_digest", "ck_pre_ga_launch_candidate_qualification_target_digest"),
        digest("subject_digest", "ck_pre_ga_launch_candidate_subject_digest"),
        digest("image_set_digest", "ck_pre_ga_launch_candidate_image_set_digest"),
        digest("deployed_artifact_set_digest", "ck_pre_ga_launch_candidate_deployed_artifact_set_digest"),
        digest("schema_runtime_identity_digest", "ck_pre_ga_launch_candidate_schema_runtime_identity_digest"),
        digest("rollout_revision_digest", "ck_pre_ga_launch_candidate_rollout_revision_digest"),
        digest("runtime_closure_digest", "ck_pre_ga_launch_candidate_runtime_closure_digest"),
        digest("profile_content_digest", "ck_pre_ga_launch_candidate_profile_content_digest"),
        digest("model_identity_digest", "ck_pre_ga_launch_candidate_model_identity_digest"),
        digest("package_closure_digest", "ck_pre_ga_launch_candidate_package_closure_digest"),
        digest("capability_closure_digest", "ck_pre_ga_launch_candidate_capability_closure_digest"),
        digest("seed_manifest_digest", "ck_pre_ga_launch_candidate_seed_manifest_digest"),
        digest("worker_capability_feature_digest", "ck_pre_ga_launch_candidate_worker_feature_digest"),
        digest("create_entry_contract_digest", "ck_pre_ga_launch_candidate_create_entry_digest"),
        digest("write_policy_digest", "ck_pre_ga_launch_candidate_write_policy_digest"),
        digest("write_cohort_digest", "ck_pre_ga_launch_candidate_write_cohort_digest"),
        digest("dependency_lock_set_digest", "ck_pre_ga_launch_candidate_lock_digest"),
        digest("scenario_set_digest", "ck_pre_ga_launch_candidate_scenario_digest"),
        digest("required_assertion_set_digest", "ck_pre_ga_launch_candidate_assertion_digest"),
        digest("runner_identity_digest", "ck_pre_ga_launch_candidate_runner_digest"),
        digest("evidence_trust_set_digest", "ck_pre_ga_launch_candidate_trust_digest"),
        digest("automated_evidence_manifest_digest", "ck_pre_ga_launch_candidate_automated_manifest_digest"),
        digest("automated_attestation_digest", "ck_pre_ga_launch_candidate_automated_attestation_digest"),
        digest("rehearsal_evidence_manifest_digest", "ck_pre_ga_launch_candidate_rehearsal_manifest_digest"),
        digest("rehearsal_attestation_digest", "ck_pre_ga_launch_candidate_rehearsal_attestation_digest"),
        digest("operational_snapshot_digest", "ck_pre_ga_launch_candidate_snapshot_digest"),
    )
    op.create_table(
        "pre_ga_launch_gate_use",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_digest", sa.String(64), nullable=False),
        sa.Column("operator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consumption_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consumption_request_digest", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("expected_control_revision", sa.Integer, nullable=False),
        sa.Column("resulting_control_revision", sa.Integer, nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id", "subject_digest"], ["pre_ga_launch_candidate.id", "pre_ga_launch_candidate.subject_digest"], name="fk_pre_ga_launch_gate_use_candidate_subject"),
        sa.ForeignKeyConstraint(["operator_id"], ["operator_account.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("consumption_request_id", name="uq_pre_ga_launch_gate_use_request_id"),
        sa.UniqueConstraint("id", "candidate_id", "subject_digest", "resulting_control_revision", name="uq_pre_ga_launch_gate_use_revision_tuple"),
        sa.CheckConstraint("length(reason) >= 1 AND length(reason) <= 500", name="ck_pre_ga_launch_gate_use_reason_len"),
        sa.CheckConstraint("expected_control_revision >= 0 AND resulting_control_revision = expected_control_revision + 1", name="ck_pre_ga_launch_gate_use_revision_shape"),
        digest("subject_digest", "ck_pre_ga_launch_gate_use_subject_digest"),
        digest("consumption_request_digest", "ck_pre_ga_launch_gate_use_request_digest"),
    )
    op.create_table(
        "pre_ga_launch_control",
        sa.Column("singleton_key", sa.String(32), nullable=False),
        sa.Column("active_subject_digest", sa.String(64), nullable=True),
        sa.Column("active_candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("active_gate_use_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["active_gate_use_id", "active_candidate_id", "active_subject_digest", "revision"], ["pre_ga_launch_gate_use.id", "pre_ga_launch_gate_use.candidate_id", "pre_ga_launch_gate_use.subject_digest", "pre_ga_launch_gate_use.resulting_control_revision"], name="fk_pre_ga_launch_control_active_use", match="SIMPLE"),
        sa.PrimaryKeyConstraint("singleton_key"),
        sa.CheckConstraint("singleton_key = 'pre_ga_launch'", name="ck_pre_ga_launch_control_singleton"),
        sa.CheckConstraint("revision >= 0", name="ck_pre_ga_launch_control_revision_nonnegative"),
        sa.CheckConstraint("(revision = 0 AND active_subject_digest IS NULL AND active_candidate_id IS NULL AND active_gate_use_id IS NULL AND launched_at IS NULL) OR (revision > 0 AND active_subject_digest IS NOT NULL AND active_candidate_id IS NOT NULL AND active_gate_use_id IS NOT NULL AND launched_at IS NOT NULL)", name="ck_pre_ga_launch_control_revision_shape"),
        digest("active_subject_digest", "ck_pre_ga_launch_control_subject_digest"),
    )
    op.execute("CREATE OR REPLACE FUNCTION mindatlas_reject_pre_ga_launch_candidate_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'pre-GA launch candidate is immutable'; END; $$")
    op.execute("CREATE TRIGGER trg_pre_ga_launch_candidate_immutable BEFORE UPDATE OR DELETE ON pre_ga_launch_candidate FOR EACH ROW EXECUTE FUNCTION mindatlas_reject_pre_ga_launch_candidate_mutation()")
    op.execute("CREATE OR REPLACE FUNCTION mindatlas_reject_pre_ga_launch_gate_use_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'pre-GA launch gate use is append-only'; END; $$")
    op.execute("CREATE TRIGGER trg_pre_ga_launch_gate_use_append_only BEFORE UPDATE OR DELETE ON pre_ga_launch_gate_use FOR EACH ROW EXECUTE FUNCTION mindatlas_reject_pre_ga_launch_gate_use_mutation()")
    op.execute("CREATE OR REPLACE FUNCTION mindatlas_guard_pre_ga_launch_control_update() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'pre-GA launch control deletion is forbidden'; END IF; IF NEW.singleton_key <> OLD.singleton_key OR NEW.revision <> OLD.revision + 1 THEN RAISE EXCEPTION 'pre-GA launch control revision invalid'; END IF; RETURN NEW; END; $$")
    op.execute("CREATE TRIGGER trg_pre_ga_launch_control_revision BEFORE UPDATE OR DELETE ON pre_ga_launch_control FOR EACH ROW EXECUTE FUNCTION mindatlas_guard_pre_ga_launch_control_update()")
    op.execute("INSERT INTO pre_ga_launch_control (singleton_key, revision) VALUES ('pre_ga_launch', 0)")


def _extend_existing_identity_guards() -> None:
    op.execute(
        """CREATE OR REPLACE FUNCTION mindatlas_reject_rollout_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'assistant rollout revision is immutable'; END IF;
  IF TG_TABLE_NAME <> 'assistant_main_agent_rollout_revision' THEN
    RAISE EXCEPTION 'assistant rollout revision is immutable';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.revision_digest IS DISTINCT FROM OLD.revision_digest
     OR NEW.required_create_entry_contract_digest IS DISTINCT FROM OLD.required_create_entry_contract_digest
     OR NEW.required_write_policy_digest IS DISTINCT FROM OLD.required_write_policy_digest
     OR NEW.required_write_cohort_digest IS DISTINCT FROM OLD.required_write_cohort_digest
     OR NEW.required_reconciliation_contract_version IS DISTINCT FROM OLD.required_reconciliation_contract_version
  THEN RAISE EXCEPTION 'assistant rollout revision is immutable'; END IF;
  RETURN NEW;
END; $$"""
    )


def _extend_existing_identity_constraints() -> None:
    """Replace root checks whose expressions now include the four fields."""
    op.drop_constraint(
        "ck_ma_rollout_revision_positive_contract",
        "assistant_main_agent_rollout_revision",
        type_="check",
    )
    op.create_check_constraint(
        "ck_ma_rollout_revision_positive_contract",
        "assistant_main_agent_rollout_revision",
        "runtime_contract_version > 0 AND checkpoint_codec_version > 0 "
        "AND required_reconciliation_contract_version > 0",
    )
    for column, name in (
        ("required_create_entry_contract_digest", "ck_ma_rollout_revision_create_entry_contract_digest"),
        ("required_write_policy_digest", "ck_ma_rollout_revision_write_policy_digest"),
        ("required_write_cohort_digest", "ck_ma_rollout_revision_write_cohort_digest"),
    ):
        op.create_check_constraint(
            name,
            "assistant_main_agent_rollout_revision",
            f"{column} ~ '^[0-9a-f]{{64}}$'",
        )
    op.drop_constraint(
        "ck_assistant_chat_run_runtime_digests",
        "assistant_chat_run",
        type_="check",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_runtime_digests",
        "assistant_chat_run",
        "runtime_closure_digest ~ '^[0-9a-f]{64}$' "
        "AND required_capability_feature_digest ~ '^[0-9a-f]{64}$' "
        "AND required_create_entry_contract_digest ~ '^[0-9a-f]{64}$' "
        "AND required_write_policy_digest ~ '^[0-9a-f]{64}$' "
        "AND required_write_cohort_digest ~ '^[0-9a-f]{64}$'",
    )
    op.drop_constraint(
        "ck_assistant_chat_run_positive_runtime_contract",
        "assistant_chat_run",
        type_="check",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_positive_runtime_contract",
        "assistant_chat_run",
        "runtime_contract_version > 0 AND required_checkpoint_codec_version > 0 "
        "AND required_reconciliation_contract_version > 0",
    )
    for column, name in (
        ("required_create_entry_contract_digest", "ck_assistant_chat_run_create_entry_contract_digest"),
        ("required_write_policy_digest", "ck_assistant_chat_run_write_policy_digest"),
        ("required_write_cohort_digest", "ck_assistant_chat_run_write_cohort_digest"),
    ):
        op.create_check_constraint(
            name,
            "assistant_chat_run",
            f"{column} ~ '^[0-9a-f]{{64}}$'",
        )
    op.execute(
        """CREATE OR REPLACE FUNCTION mindatlas_reject_run_runtime_identity_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.runtime_kind IS DISTINCT FROM OLD.runtime_kind
     OR NEW.main_agent_rollout_revision_id IS DISTINCT FROM OLD.main_agent_rollout_revision_id
     OR NEW.main_agent_profile_version_id IS DISTINCT FROM OLD.main_agent_profile_version_id
     OR NEW.resolved_model_id IS DISTINCT FROM OLD.resolved_model_id
     OR NEW.runtime_closure_digest IS DISTINCT FROM OLD.runtime_closure_digest
     OR NEW.runtime_contract_version IS DISTINCT FROM OLD.runtime_contract_version
     OR NEW.required_checkpoint_codec_version IS DISTINCT FROM OLD.required_checkpoint_codec_version
     OR NEW.required_capability_feature_digest IS DISTINCT FROM OLD.required_capability_feature_digest
     OR NEW.required_create_entry_contract_digest IS DISTINCT FROM OLD.required_create_entry_contract_digest
     OR NEW.required_write_policy_digest IS DISTINCT FROM OLD.required_write_policy_digest
     OR NEW.required_write_cohort_digest IS DISTINCT FROM OLD.required_write_cohort_digest
     OR NEW.required_reconciliation_contract_version IS DISTINCT FROM OLD.required_reconciliation_contract_version
     OR NEW.required_app_build_revision IS DISTINCT FROM OLD.required_app_build_revision
     OR NEW.capability_ledger_mode IS DISTINCT FROM OLD.capability_ledger_mode
  THEN RAISE EXCEPTION 'assistant Run runtime identity is immutable'; END IF;
  RETURN NEW;
END; $$"""
    )


def _advance_schema_identity(marker: dict[str, object], deployment_class: str) -> None:
    structural_fingerprint = EXPECTED_APPLICATION_FINGERPRINT
    runtime_payload = {
        "schemaFamily": SCHEMA_FAMILY,
        "schemaRevision": SCHEMA_REVISION,
        "structuralFingerprint": structural_fingerprint,
        "seedContractDigest": CURRENT_SEED_CONTRACT_DIGEST,
        "deploymentClass": deployment_class,
        "runtimeContractVersion": marker["runtime_contract_version"],
        "checkpointCodecVersion": marker["checkpoint_codec_version"],
        "capabilityFeatureDigest": CURRENT_CAPABILITY_FEATURE_DIGEST,
        "operatorAuthContractVersion": marker["operator_auth_contract_version"],
    }
    runtime_digest = _sha256_json(runtime_payload)
    connection = op.get_bind()
    connection.execute(sa.text("SET LOCAL mindatlas.schema_migration_revision = 'pre_ga_v1_0002'"))
    connection.execute(
        sa.text(
            "UPDATE mindatlas_schema_identity SET schema_revision = :revision, "
            "structural_fingerprint = :structural_fingerprint, "
            "seed_contract_digest = :seed_contract_digest, "
            "capability_feature_digest = :capability_feature_digest, "
            "runtime_identity_digest = :runtime_identity_digest, "
            "updated_at = clock_timestamp() WHERE singleton_key = 'current'"
        ),
        {
            "revision": SCHEMA_REVISION,
            "structural_fingerprint": structural_fingerprint,
            "seed_contract_digest": CURRENT_SEED_CONTRACT_DIGEST,
            "capability_feature_digest": CURRENT_CAPABILITY_FEATURE_DIGEST,
            "runtime_identity_digest": runtime_digest,
        },
    )


def upgrade() -> None:
    deployment_class = _deployment_class()
    marker = _preflight(op.get_bind())
    op.add_column("assistant_main_agent_rollout_revision", sa.Column("required_create_entry_contract_digest", sa.String(64), nullable=False))
    op.add_column("assistant_main_agent_rollout_revision", sa.Column("required_write_policy_digest", sa.String(64), nullable=False))
    op.add_column("assistant_main_agent_rollout_revision", sa.Column("required_write_cohort_digest", sa.String(64), nullable=False))
    op.add_column("assistant_main_agent_rollout_revision", sa.Column("required_reconciliation_contract_version", sa.Integer, nullable=False))
    op.add_column("assistant_chat_run", sa.Column("required_create_entry_contract_digest", sa.String(64), nullable=False))
    op.add_column("assistant_chat_run", sa.Column("required_write_policy_digest", sa.String(64), nullable=False))
    op.add_column("assistant_chat_run", sa.Column("required_write_cohort_digest", sa.String(64), nullable=False))
    op.add_column("assistant_chat_run", sa.Column("required_reconciliation_contract_version", sa.Integer, nullable=False))
    _extend_existing_identity_constraints()
    _extend_existing_identity_guards()
    _create_launch_tables()
    _advance_schema_identity(marker, deployment_class)


def _restore_root_marker() -> None:
    connection = op.get_bind()
    marker = connection.execute(
        sa.text("SELECT seed_contract_digest, runtime_contract_version, checkpoint_codec_version, capability_feature_digest, operator_auth_contract_version, deployment_class FROM mindatlas_schema_identity WHERE singleton_key = 'current'")
    ).mappings().one()
    payload = {
        "schemaFamily": SCHEMA_FAMILY,
        "schemaRevision": ROOT_REVISION,
        "structuralFingerprint": ROOT_APPLICATION_FINGERPRINT,
        "seedContractDigest": ROOT_SEED_CONTRACT_DIGEST,
        "deploymentClass": marker["deployment_class"],
        "runtimeContractVersion": marker["runtime_contract_version"],
        "checkpointCodecVersion": marker["checkpoint_codec_version"],
        "capabilityFeatureDigest": ROOT_CAPABILITY_FEATURE_DIGEST,
        "operatorAuthContractVersion": marker["operator_auth_contract_version"],
    }
    connection.execute(sa.text("SET LOCAL mindatlas.schema_migration_revision = 'pre_ga_v1_0001'"))
    connection.execute(
        sa.text(
            "UPDATE mindatlas_schema_identity SET schema_revision = :revision, "
            "structural_fingerprint = :structural_fingerprint, "
            "seed_contract_digest = :seed_contract_digest, "
            "capability_feature_digest = :capability_feature_digest, "
            "runtime_identity_digest = :runtime_identity_digest, "
            "updated_at = clock_timestamp() WHERE singleton_key = 'current'"
        ),
        {
            "revision": ROOT_REVISION,
            "structural_fingerprint": ROOT_APPLICATION_FINGERPRINT,
            "seed_contract_digest": ROOT_SEED_CONTRACT_DIGEST,
            "capability_feature_digest": ROOT_CAPABILITY_FEATURE_DIGEST,
            "runtime_identity_digest": _sha256_json(payload),
        },
    )


def downgrade() -> None:
    if os.environ.get("APP_ENV", "").strip() != "test" or os.environ.get("MINDATLAS_PRE_GA_0002_TEST_DOWNGRADE_ACK", "") != "I_ACKNOWLEDGE_EMPTY_PRE_GA_0002_DOWNGRADE":
        raise RuntimeError("pre_ga_0002_downgrade_forbidden")
    connection = op.get_bind()
    for table in ("pre_ga_launch_candidate", "pre_ga_launch_gate_use"):
        if connection.execute(sa.text(f'SELECT 1 FROM "{table}" LIMIT 1')).first() is not None:
            raise RuntimeError("pre_ga_0002_downgrade_requires_empty_state")
    control = connection.execute(sa.text("SELECT revision FROM pre_ga_launch_control WHERE singleton_key = 'pre_ga_launch'" )).scalar_one()
    if int(control) != 0:
        raise RuntimeError("pre_ga_0002_downgrade_requires_empty_state")
    rows = connection.execute(
        sa.text(
            "SELECT c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' "
            "AND c.relname NOT IN ('alembic_version', 'mindatlas_schema_identity', "
            "'pre_ga_launch_candidate', 'pre_ga_launch_gate_use', 'pre_ga_launch_control')"
        )
    ).all()
    for (table_name,) in rows:
        quoted_table = str(table_name).replace('"', '""')
        if connection.execute(sa.text(f'SELECT 1 FROM "public"."{quoted_table}" LIMIT 1')).first() is not None:
            raise RuntimeError("pre_ga_0002_downgrade_requires_empty_state")
    # Restore the Plan 3 trigger bodies while all four additive columns still
    # exist. The old rollout function is also used by the append-only event
    # trigger, hence the unconditional rejection for non-revision tables.
    op.execute(
        """CREATE OR REPLACE FUNCTION mindatlas_reject_rollout_revision_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'assistant rollout revision is immutable';
END; $$"""
    )
    op.execute(
        """CREATE OR REPLACE FUNCTION mindatlas_reject_run_runtime_identity_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
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
  THEN RAISE EXCEPTION 'assistant Run runtime identity is immutable'; END IF;
  RETURN NEW;
END; $$"""
    )
    op.drop_table("pre_ga_launch_control")
    op.drop_table("pre_ga_launch_gate_use")
    op.drop_table("pre_ga_launch_candidate")
    op.drop_column("assistant_chat_run", "required_reconciliation_contract_version")
    op.drop_column("assistant_chat_run", "required_write_cohort_digest")
    op.drop_column("assistant_chat_run", "required_write_policy_digest")
    op.drop_column("assistant_chat_run", "required_create_entry_contract_digest")
    op.drop_column("assistant_main_agent_rollout_revision", "required_reconciliation_contract_version")
    op.drop_column("assistant_main_agent_rollout_revision", "required_write_cohort_digest")
    op.drop_column("assistant_main_agent_rollout_revision", "required_write_policy_digest")
    op.drop_column("assistant_main_agent_rollout_revision", "required_create_entry_contract_digest")
    # Dropping an additive column also drops any replacement CHECK that
    # referenced it. Recreate the exact Plan 3 expressions before returning
    # to the root revision.
    op.create_check_constraint(
        "ck_ma_rollout_revision_positive_contract",
        "assistant_main_agent_rollout_revision",
        "runtime_contract_version > 0 AND checkpoint_codec_version > 0",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_runtime_digests",
        "assistant_chat_run",
        "runtime_closure_digest ~ '^[0-9a-f]{64}$' "
        "AND required_capability_feature_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_assistant_chat_run_positive_runtime_contract",
        "assistant_chat_run",
        "runtime_contract_version > 0 AND required_checkpoint_codec_version > 0",
    )
    op.execute("DROP FUNCTION IF EXISTS mindatlas_reject_pre_ga_launch_candidate_mutation()")
    op.execute("DROP FUNCTION IF EXISTS mindatlas_reject_pre_ga_launch_gate_use_mutation()")
    op.execute("DROP FUNCTION IF EXISTS mindatlas_guard_pre_ga_launch_control_update()")
    _restore_root_marker()

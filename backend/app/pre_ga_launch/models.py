"""SQLAlchemy persistence for immutable launch candidates and CAS control."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.common.time import utcnow
from app.database import Base


def _digest(column: str, name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} ~ '^[0-9a-f]{{64}}$'", name=name)


class PreGaLaunchCandidate(Base):
    __tablename__ = "pre_ga_launch_candidate"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    candidate_kind = Column(String(32), nullable=False, default="pre_ga_launch")
    creation_request_id = Column(UUID(as_uuid=True), nullable=False)
    creation_request_digest = Column(String(64), nullable=False)
    created_by_operator_id = Column(UUID(as_uuid=True), ForeignKey("operator_account.id", ondelete="RESTRICT"), nullable=False)
    created_by_session_id = Column(UUID(as_uuid=True), nullable=False)
    reason = Column(String(500), nullable=False)

    qualification_target_json = Column(JSONB, nullable=False)
    qualification_target_digest = Column(String(64), nullable=False)
    subject_json = Column(JSONB, nullable=False)
    subject_digest = Column(String(64), nullable=False)
    build_revision = Column(String(128), nullable=False)
    image_set_digest = Column(String(64), nullable=False)
    deployed_artifact_set_digest = Column(String(64), nullable=False)
    schema_family = Column(String(32), nullable=False)
    schema_revision = Column(String(64), nullable=False)
    schema_runtime_identity_digest = Column(String(64), nullable=False)
    deployment_class = Column(String(16), nullable=False)
    operator_auth_contract_version = Column(String(64), nullable=False)
    rollout_revision_id = Column(UUID(as_uuid=True), nullable=False)
    rollout_revision_digest = Column(String(64), nullable=False)
    runtime_closure_digest = Column(String(64), nullable=False)
    profile_version_id = Column(UUID(as_uuid=True), nullable=False)
    profile_content_digest = Column(String(64), nullable=False)
    model_id = Column(UUID(as_uuid=True), nullable=False)
    model_identity_digest = Column(String(64), nullable=False)
    package_closure_digest = Column(String(64), nullable=False)
    capability_closure_digest = Column(String(64), nullable=False)
    seed_manifest_digest = Column(String(64), nullable=False)
    worker_runtime_contract_version = Column(Integer, nullable=False)
    worker_checkpoint_codec_version = Column(Integer, nullable=False)
    worker_capability_feature_digest = Column(String(64), nullable=False)
    create_entry_contract_digest = Column(String(64), nullable=False)
    write_policy_digest = Column(String(64), nullable=False)
    write_cohort_digest = Column(String(64), nullable=False)
    reconciliation_contract_version = Column(Integer, nullable=False)
    dependency_lock_set_digest = Column(String(64), nullable=False)
    scenario_set_digest = Column(String(64), nullable=False)
    required_assertion_set_digest = Column(String(64), nullable=False)
    runner_contract_version = Column(Integer, nullable=False)
    runner_identity_digest = Column(String(64), nullable=False)
    evidence_trust_set_digest = Column(String(64), nullable=False)

    automated_evidence_ref_json = Column(JSONB, nullable=False)
    automated_evidence_manifest_digest = Column(String(64), nullable=False)
    automated_attestation_digest = Column(String(64), nullable=False)
    rehearsal_evidence_ref_json = Column(JSONB, nullable=False)
    rehearsal_evidence_manifest_digest = Column(String(64), nullable=False)
    rehearsal_attestation_digest = Column(String(64), nullable=False)

    operational_snapshot_json = Column(JSONB, nullable=False)
    operational_snapshot_digest = Column(String(64), nullable=False)
    unknown_call_count = Column(Integer, nullable=False)
    needs_reconciliation_count = Column(Integer, nullable=False)
    active_run_count = Column(Integer, nullable=False)
    passed = Column(Boolean, nullable=False)
    safe_failure_codes = Column(JSONB, nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    issued_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("creation_request_id", name="uq_pre_ga_launch_candidate_creation_request_id"),
        UniqueConstraint("id", "subject_digest", name="uq_pre_ga_launch_candidate_id_subject_digest"),
        CheckConstraint("candidate_kind = 'pre_ga_launch'", name="ck_pre_ga_launch_candidate_kind"),
        CheckConstraint("schema_family = 'pre_ga_v1' AND schema_revision = 'pre_ga_v1_0002' AND deployment_class = 'production'", name="ck_pre_ga_launch_candidate_schema_identity"),
        CheckConstraint("length(reason) >= 1 AND length(reason) <= 500", name="ck_pre_ga_launch_candidate_reason_len"),
        CheckConstraint("expires_at = issued_at + INTERVAL '24 hours'", name="ck_pre_ga_launch_candidate_expiry"),
        CheckConstraint("unknown_call_count >= 0 AND needs_reconciliation_count >= 0 AND active_run_count >= 0", name="ck_pre_ga_launch_candidate_counts"),
        CheckConstraint("passed = false OR (unknown_call_count = 0 AND needs_reconciliation_count = 0 AND active_run_count = 0 AND safe_failure_codes = '[]'::jsonb)", name="ck_pre_ga_launch_candidate_passed_shape"),
        CheckConstraint("passed = true OR jsonb_array_length(safe_failure_codes) > 0", name="ck_pre_ga_launch_candidate_failed_shape"),
        CheckConstraint("worker_runtime_contract_version > 0 AND worker_checkpoint_codec_version > 0 AND reconciliation_contract_version > 0 AND runner_contract_version > 0", name="ck_pre_ga_launch_candidate_positive_versions"),
        _digest("creation_request_digest", "ck_pre_ga_launch_candidate_creation_request_digest"),
        _digest("qualification_target_digest", "ck_pre_ga_launch_candidate_qualification_target_digest"),
        _digest("subject_digest", "ck_pre_ga_launch_candidate_subject_digest"),
        _digest("image_set_digest", "ck_pre_ga_launch_candidate_image_set_digest"),
        _digest("deployed_artifact_set_digest", "ck_pre_ga_launch_candidate_deployed_artifact_set_digest"),
        _digest("schema_runtime_identity_digest", "ck_pre_ga_launch_candidate_schema_runtime_identity_digest"),
        _digest("rollout_revision_digest", "ck_pre_ga_launch_candidate_rollout_revision_digest"),
        _digest("runtime_closure_digest", "ck_pre_ga_launch_candidate_runtime_closure_digest"),
        _digest("profile_content_digest", "ck_pre_ga_launch_candidate_profile_content_digest"),
        _digest("model_identity_digest", "ck_pre_ga_launch_candidate_model_identity_digest"),
        _digest("package_closure_digest", "ck_pre_ga_launch_candidate_package_closure_digest"),
        _digest("capability_closure_digest", "ck_pre_ga_launch_candidate_capability_closure_digest"),
        _digest("seed_manifest_digest", "ck_pre_ga_launch_candidate_seed_manifest_digest"),
        _digest("worker_capability_feature_digest", "ck_pre_ga_launch_candidate_worker_feature_digest"),
        _digest("create_entry_contract_digest", "ck_pre_ga_launch_candidate_create_entry_digest"),
        _digest("write_policy_digest", "ck_pre_ga_launch_candidate_write_policy_digest"),
        _digest("write_cohort_digest", "ck_pre_ga_launch_candidate_write_cohort_digest"),
        _digest("dependency_lock_set_digest", "ck_pre_ga_launch_candidate_lock_digest"),
        _digest("scenario_set_digest", "ck_pre_ga_launch_candidate_scenario_digest"),
        _digest("required_assertion_set_digest", "ck_pre_ga_launch_candidate_assertion_digest"),
        _digest("runner_identity_digest", "ck_pre_ga_launch_candidate_runner_digest"),
        _digest("evidence_trust_set_digest", "ck_pre_ga_launch_candidate_trust_digest"),
        _digest("automated_evidence_manifest_digest", "ck_pre_ga_launch_candidate_automated_manifest_digest"),
        _digest("automated_attestation_digest", "ck_pre_ga_launch_candidate_automated_attestation_digest"),
        _digest("rehearsal_evidence_manifest_digest", "ck_pre_ga_launch_candidate_rehearsal_manifest_digest"),
        _digest("rehearsal_attestation_digest", "ck_pre_ga_launch_candidate_rehearsal_attestation_digest"),
        _digest("operational_snapshot_digest", "ck_pre_ga_launch_candidate_snapshot_digest"),
    )


class PreGaLaunchGateUse(Base):
    __tablename__ = "pre_ga_launch_gate_use"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    candidate_id = Column(UUID(as_uuid=True), nullable=False)
    subject_digest = Column(String(64), nullable=False)
    operator_id = Column(UUID(as_uuid=True), ForeignKey("operator_account.id", ondelete="RESTRICT"), nullable=False)
    session_id = Column(UUID(as_uuid=True), nullable=False)
    consumption_request_id = Column(UUID(as_uuid=True), nullable=False)
    consumption_request_digest = Column(String(64), nullable=False)
    reason = Column(String(500), nullable=False)
    expected_control_revision = Column(Integer, nullable=False)
    resulting_control_revision = Column(Integer, nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_id", "subject_digest"],
            ["pre_ga_launch_candidate.id", "pre_ga_launch_candidate.subject_digest"],
            name="fk_pre_ga_launch_gate_use_candidate_subject",
        ),
        UniqueConstraint("consumption_request_id", name="uq_pre_ga_launch_gate_use_request_id"),
        UniqueConstraint("id", "candidate_id", "subject_digest", "resulting_control_revision", name="uq_pre_ga_launch_gate_use_revision_tuple"),
        CheckConstraint("length(reason) >= 1 AND length(reason) <= 500", name="ck_pre_ga_launch_gate_use_reason_len"),
        CheckConstraint("expected_control_revision >= 0 AND resulting_control_revision = expected_control_revision + 1", name="ck_pre_ga_launch_gate_use_revision_shape"),
        _digest("subject_digest", "ck_pre_ga_launch_gate_use_subject_digest"),
        _digest("consumption_request_digest", "ck_pre_ga_launch_gate_use_request_digest"),
    )


class PreGaLaunchControl(Base):
    __tablename__ = "pre_ga_launch_control"

    singleton_key = Column(String(32), primary_key=True, default="pre_ga_launch")
    active_subject_digest = Column(String(64), nullable=True)
    active_candidate_id = Column(UUID(as_uuid=True), nullable=True)
    active_gate_use_id = Column(UUID(as_uuid=True), nullable=True)
    revision = Column(Integer, nullable=False, default=0)
    launched_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        ForeignKeyConstraint(
            ["active_gate_use_id", "active_candidate_id", "active_subject_digest", "revision"],
            ["pre_ga_launch_gate_use.id", "pre_ga_launch_gate_use.candidate_id", "pre_ga_launch_gate_use.subject_digest", "pre_ga_launch_gate_use.resulting_control_revision"],
            name="fk_pre_ga_launch_control_active_use",
            match="SIMPLE",
        ),
        CheckConstraint("singleton_key = 'pre_ga_launch'", name="ck_pre_ga_launch_control_singleton"),
        CheckConstraint("revision >= 0", name="ck_pre_ga_launch_control_revision_nonnegative"),
        CheckConstraint("(revision = 0 AND active_subject_digest IS NULL AND active_candidate_id IS NULL AND active_gate_use_id IS NULL AND launched_at IS NULL) OR (revision > 0 AND active_subject_digest IS NOT NULL AND active_candidate_id IS NOT NULL AND active_gate_use_id IS NOT NULL AND launched_at IS NOT NULL)", name="ck_pre_ga_launch_control_revision_shape"),
        _digest("active_subject_digest", "ck_pre_ga_launch_control_subject_digest"),
    )


def _reject_candidate_mutation(*_args, **_kwargs):
    raise RuntimeError("pre-GA launch candidate is immutable")


def _reject_gate_use_mutation(*_args, **_kwargs):
    raise RuntimeError("pre-GA launch gate use is append-only")


event.listen(PreGaLaunchCandidate, "before_update", _reject_candidate_mutation)
event.listen(PreGaLaunchCandidate, "before_delete", _reject_candidate_mutation)
event.listen(PreGaLaunchGateUse, "before_update", _reject_gate_use_mutation)
event.listen(PreGaLaunchGateUse, "before_delete", _reject_gate_use_mutation)


__all__ = ["PreGaLaunchCandidate", "PreGaLaunchControl", "PreGaLaunchGateUse"]

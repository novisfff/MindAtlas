"""Evaluation workbench ORM models (Plan 09 Task 3).

Tables:
- assistant_skill_eval_dataset
- assistant_skill_eval_dataset_draft
- assistant_skill_eval_dataset_version
- assistant_skill_eval_case
- assistant_skill_eval_run
- assistant_skill_eval_case_result
- assistant_skill_eval_capability_call
- assistant_skill_eval_event
- assistant_skill_eval_artifact
- assistant_skill_publish_gate
- assistant_skill_publish_gate_use

EvaluationRepository is the only writer. Immutable/append-only children have no
ORM delete-orphan cascades. Eval CapabilityCall uses synthetic IDs with no
production ledger FK.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.common.time import utcnow
from app.database import Base


def _sha256_check(column: str, *, name: str) -> CheckConstraint:
    return CheckConstraint(f"length({column}) = 64", name=name)


def _nullable_sha256_check(column: str, *, name: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR length({column}) = 64",
        name=name,
    )


class AssistantSkillEvalDataset(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Mutable Dataset aggregate root (pointer + metadata only)."""

    __tablename__ = "assistant_skill_eval_dataset"

    stable_key = Column(String(128), nullable=False, index=True)
    display_name = Column(String(256), nullable=False)
    description = Column(String(2048), nullable=False, default="", server_default=text("''"))
    ownership = Column(String(32), nullable=False, default="system", server_default=text("'system'"))
    current_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_eval_dataset_version.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_assistant_skill_eval_dataset_current_version_id",
        ),
        nullable=True,
        index=True,
    )
    aggregate_revision = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by = Column(String(128), nullable=True)

    current_version = relationship(
        "AssistantSkillEvalDatasetVersion",
        foreign_keys=[current_version_id],
        uselist=False,
        post_update=True,
    )
    draft = relationship(
        "AssistantSkillEvalDatasetDraft",
        back_populates="dataset",
        uselist=False,
        # No cascade delete: draft retained for audit; physical delete blocked when referenced.
    )
    versions = relationship(
        "AssistantSkillEvalDatasetVersion",
        back_populates="dataset",
        foreign_keys="AssistantSkillEvalDatasetVersion.dataset_id",
        # No cascade delete: version history is append-only.
    )

    __table_args__ = (
        UniqueConstraint(
            "stable_key",
            name="uq_assistant_skill_eval_dataset_stable_key",
        ),
        CheckConstraint(
            "ownership IN ('system','custom')",
            name="ck_assistant_skill_eval_dataset_ownership",
        ),
        CheckConstraint(
            "aggregate_revision >= 0",
            name="ck_assistant_skill_eval_dataset_aggregate_revision",
        ),
        CheckConstraint(
            "(archived_at IS NULL AND archived_by IS NULL) OR (archived_at IS NOT NULL)",
            name="ck_assistant_skill_eval_dataset_archived_shape",
        ),
    )


class AssistantSkillEvalDatasetDraft(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Exactly one mutable working draft per Dataset aggregate."""

    __tablename__ = "assistant_skill_eval_dataset_draft"

    dataset_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_eval_dataset.id",
            ondelete="RESTRICT",
            name="fk_assistant_skill_eval_dataset_draft_dataset_id",
        ),
        nullable=False,
        index=True,
    )
    draft_revision = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    schema_version = Column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    cases_snapshot = Column(JSON, nullable=False, default=list)
    draft_digest = Column(String(64), nullable=False)
    base_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_eval_dataset_version.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_assistant_skill_eval_dataset_draft_base_version_id",
        ),
        nullable=True,
    )
    updated_by = Column(String(128), nullable=True)
    last_validation_digest = Column(String(64), nullable=True)

    dataset = relationship(
        "AssistantSkillEvalDataset",
        back_populates="draft",
        foreign_keys=[dataset_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            name="uq_assistant_skill_eval_dataset_draft_dataset_id",
        ),
        CheckConstraint(
            "draft_revision >= 0",
            name="ck_assistant_skill_eval_dataset_draft_revision",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_assistant_skill_eval_dataset_draft_schema_version",
        ),
        _sha256_check(
            "draft_digest",
            name="ck_assistant_skill_eval_dataset_draft_digest",
        ),
        _nullable_sha256_check(
            "last_validation_digest",
            name="ck_assistant_skill_eval_dataset_draft_validation_digest",
        ),
    )


class AssistantSkillEvalDatasetVersion(UuidPrimaryKeyMixin, Base):
    """Immutable published Dataset version (append-only)."""

    __tablename__ = "assistant_skill_eval_dataset_version"

    dataset_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_eval_dataset.id",
            ondelete="RESTRICT",
            name="fk_assistant_skill_eval_dataset_version_dataset_id",
        ),
        nullable=False,
        index=True,
    )
    sequence = Column(Integer, nullable=False)
    version_name = Column(String(128), nullable=False)
    schema_version = Column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    content_digest = Column(String(64), nullable=False)
    source_fixture_revision = Column(String(160), nullable=True)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    dataset = relationship(
        "AssistantSkillEvalDataset",
        back_populates="versions",
        foreign_keys=[dataset_id],
    )
    cases = relationship(
        "AssistantSkillEvalCase",
        back_populates="dataset_version",
        foreign_keys="AssistantSkillEvalCase.dataset_version_id",
        # No cascade delete: cases are append-only through dataset version.
    )

    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "sequence",
            name="uq_assistant_skill_eval_dataset_version_sequence",
        ),
        UniqueConstraint(
            "dataset_id",
            "content_digest",
            name="uq_assistant_skill_eval_dataset_version_digest",
        ),
        CheckConstraint(
            "sequence > 0",
            name="ck_assistant_skill_eval_dataset_version_sequence",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_assistant_skill_eval_dataset_version_schema_version",
        ),
        _sha256_check(
            "content_digest",
            name="ck_assistant_skill_eval_dataset_version_content_digest",
        ),
    )


class AssistantSkillEvalCase(UuidPrimaryKeyMixin, Base):
    """Immutable evaluation case bound to a Dataset Version (append-only)."""

    __tablename__ = "assistant_skill_eval_case"

    dataset_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_eval_dataset_version.id",
            ondelete="RESTRICT",
            name="fk_assistant_skill_eval_case_dataset_version_id",
        ),
        nullable=False,
        index=True,
    )
    case_key = Column(String(128), nullable=False)
    ordinal = Column(Integer, nullable=False)
    locale = Column(String(16), nullable=False, default="en", server_default=text("'en'"))
    input_messages = Column(JSON, nullable=False, default=list)
    fixture_refs = Column(JSON, nullable=False, default=list)
    expected_mode = Column(String(64), nullable=False)
    acceptable_skill_keys = Column(JSON, nullable=False, default=list)
    forbidden_skill_keys = Column(JSON, nullable=False, default=list)
    acceptable_capability_paths = Column(JSON, nullable=False, default=list)
    forbidden_side_effect_classes = Column(JSON, nullable=False, default=list)
    expect_completion = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    assertion_json = Column(JSON, nullable=False, default=dict)
    ceilings_json = Column(JSON, nullable=False, default=dict)
    tags = Column(JSON, nullable=False, default=list)
    notes = Column(Text, nullable=False, default="", server_default=text("''"))
    case_digest = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    dataset_version = relationship(
        "AssistantSkillEvalDatasetVersion",
        back_populates="cases",
        foreign_keys=[dataset_version_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id",
            "case_key",
            name="uq_assistant_skill_eval_case_key",
        ),
        UniqueConstraint(
            "dataset_version_id",
            "ordinal",
            name="uq_assistant_skill_eval_case_ordinal",
        ),
        CheckConstraint(
            "ordinal >= 0",
            name="ck_assistant_skill_eval_case_ordinal",
        ),
        _sha256_check(
            "case_digest",
            name="ck_assistant_skill_eval_case_digest",
        ),
    )


class AssistantSkillEvalRun(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Durable evaluation run aggregate (not a production AssistantChatRun)."""

    __tablename__ = "assistant_skill_eval_run"

    subject_kind = Column(String(64), nullable=False)
    subject_aggregate_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    subject_version_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    subject_content_digest = Column(String(64), nullable=False)
    subject_binding_digest = Column(String(64), nullable=False)
    dataset_version_ids = Column(JSON, nullable=False, default=list)
    threshold_policy_version = Column(String(64), nullable=False)
    mode = Column(String(32), nullable=False)
    status = Column(
        String(32), nullable=False, default="queued", server_default=text("'queued'")
    )
    isolation_namespace_id = Column(UUID(as_uuid=True), nullable=False)
    owner_kind = Column(
        String(16), nullable=False, default="test", server_default=text("'test'")
    )
    runtime_contract_version = Column(Integer, nullable=False)
    required_build_revision = Column(String(160), nullable=False)
    runner_contract_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    state_revision = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    lease_owner = Column(String(160), nullable=True)
    lease_generation = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    requested_cancel_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    last_event_seq = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    attempt_count = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    failure_code = Column(String(64), nullable=True)
    isolation_digest = Column(String(64), nullable=False)
    policy_digest = Column(String(64), nullable=True)
    runtime_digest = Column(String(64), nullable=True)
    provider_evidence_digest = Column(String(64), nullable=True)
    # Trustworthiness provenance for gate eligibility (Plan 09 remediation Task 5).
    # structural_synthetic: historical/materialized structural tests (never gate-eligible)
    # real_orchestration: deterministic scripted Provider through real Main Agent path
    # live_model: optional live Provider (promotion-ineligible in this remediation)
    evidence_provenance = Column(
        String(32),
        nullable=False,
        default="structural_synthetic",
        server_default=text("'structural_synthetic'"),
    )
    provider_fixture_revision = Column(String(160), nullable=True)
    provider_fixture_digest = Column(String(64), nullable=True)
    aggregate_metrics = Column(JSON, nullable=False, default=dict)
    gate_eligible = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Plan 10: admin_evaluation (default) | runtime_shadow (always gate-ineligible).
    purpose = Column(
        String(32),
        nullable=False,
        default="admin_evaluation",
        server_default=text("'admin_evaluation'"),
    )
    actor_principal = Column(String(128), nullable=True)
    request_id = Column(String(128), nullable=True)
    # Durable cancel CAS evidence (Plan 09 residual 3).
    last_cancel_request_id = Column(String(128), nullable=True)
    last_cancel_request_digest = Column(String(64), nullable=True)

    case_results = relationship(
        "AssistantSkillEvalCaseResult",
        back_populates="eval_run",
        foreign_keys="AssistantSkillEvalCaseResult.eval_run_id",
    )
    events = relationship(
        "AssistantSkillEvalEvent",
        back_populates="eval_run",
        foreign_keys="AssistantSkillEvalEvent.eval_run_id",
        order_by="AssistantSkillEvalEvent.sequence.asc()",
    )
    artifacts = relationship(
        "AssistantSkillEvalArtifact",
        back_populates="eval_run",
        foreign_keys="AssistantSkillEvalArtifact.eval_run_id",
    )
    capability_calls = relationship(
        "AssistantSkillEvalCapabilityCall",
        back_populates="eval_run",
        foreign_keys="AssistantSkillEvalCapabilityCall.eval_run_id",
    )

    __table_args__ = (
        CheckConstraint(
            "subject_kind IN ("
            "'skill_draft','skill_version',"
            "'main_agent_profile_draft','main_agent_profile_version',"
            "'legacy_baseline'"
            ")",
            name="ck_assistant_skill_eval_run_subject_kind",
        ),
        CheckConstraint(
            "mode IN ('interactive_scripted','dataset_scripted','dataset_live')",
            name="ck_assistant_skill_eval_run_mode",
        ),
        CheckConstraint(
            "status IN ("
            "'queued','running','cancelling','completed','failed','cancelled'"
            ")",
            name="ck_assistant_skill_eval_run_status",
        ),
        CheckConstraint(
            "owner_kind = 'test'",
            name="ck_assistant_skill_eval_run_owner_kind",
        ),
        CheckConstraint(
            "state_revision >= 0",
            name="ck_assistant_skill_eval_run_state_revision",
        ),
        CheckConstraint(
            "lease_generation >= 0",
            name="ck_assistant_skill_eval_run_lease_generation",
        ),
        CheckConstraint(
            "last_event_seq >= 0",
            name="ck_assistant_skill_eval_run_last_event_seq",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_assistant_skill_eval_run_attempt_count",
        ),
        CheckConstraint(
            "runtime_contract_version > 0",
            name="ck_assistant_skill_eval_run_runtime_contract_version",
        ),
        CheckConstraint(
            "runner_contract_version > 0",
            name="ck_assistant_skill_eval_run_runner_contract_version",
        ),
        _sha256_check(
            "subject_content_digest",
            name="ck_assistant_skill_eval_run_subject_content_digest",
        ),
        _sha256_check(
            "subject_binding_digest",
            name="ck_assistant_skill_eval_run_subject_binding_digest",
        ),
        _sha256_check(
            "isolation_digest",
            name="ck_assistant_skill_eval_run_isolation_digest",
        ),
        _nullable_sha256_check(
            "policy_digest",
            name="ck_assistant_skill_eval_run_policy_digest",
        ),
        _nullable_sha256_check(
            "runtime_digest",
            name="ck_assistant_skill_eval_run_runtime_digest",
        ),
        _nullable_sha256_check(
            "provider_evidence_digest",
            name="ck_assistant_skill_eval_run_provider_evidence_digest",
        ),
        CheckConstraint(
            "evidence_provenance IN ("
            "'real_orchestration','structural_synthetic','live_model'"
            ")",
            name="ck_assistant_skill_eval_run_evidence_provenance",
        ),
        _nullable_sha256_check(
            "provider_fixture_digest",
            name="ck_assistant_skill_eval_run_provider_fixture_digest",
        ),
        CheckConstraint(
            # Fixture pins are all-or-nothing: both null, or both present with
            # non-empty revision + 64-char digest.
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
        CheckConstraint(
            # Structural synthetic runs can never be gate-eligible (DB defense).
            "evidence_provenance <> 'structural_synthetic' OR gate_eligible = false",
            name="ck_assistant_skill_eval_run_synthetic_gate_ineligible",
        ),
        CheckConstraint(
            "purpose IN ('admin_evaluation','runtime_shadow')",
            name="ck_assistant_skill_eval_run_purpose",
        ),
        CheckConstraint(
            # Online runtime shadow is rollout evidence, never a publish-gate dataset.
            "purpose <> 'runtime_shadow' OR gate_eligible = false",
            name="ck_assistant_skill_eval_run_runtime_shadow_gate_ineligible",
        ),
        _nullable_sha256_check(
            "last_cancel_request_digest",
            name="ck_assistant_skill_eval_run_last_cancel_request_digest",
        ),
        CheckConstraint(
            # Cancel stamp is all-or-nothing: both null, or both present.
            "("
            "last_cancel_request_id IS NULL AND last_cancel_request_digest IS NULL"
            ") OR ("
            "last_cancel_request_id IS NOT NULL "
            "AND length(last_cancel_request_id) > 0 "
            "AND length(last_cancel_request_id) <= 128 "
            "AND last_cancel_request_digest IS NOT NULL "
            "AND length(last_cancel_request_digest) = 64"
            ")",
            name="ck_assistant_skill_eval_run_last_cancel_request_shape",
        ),
        Index(
            "ix_assistant_skill_eval_run_status_created",
            "status",
            "created_at",
        ),
        Index(
            "ix_assistant_skill_eval_run_lease_claim",
            "status",
            "lease_expires_at",
            "created_at",
        ),
    )


class AssistantSkillEvalCaseResult(UuidPrimaryKeyMixin, Base):
    """Append-only case result; unique per Eval Run / case."""

    __tablename__ = "assistant_skill_eval_case_result"

    eval_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_eval_run.id",
            ondelete="RESTRICT",
            name="fk_assistant_skill_eval_case_result_eval_run_id",
        ),
        nullable=False,
        index=True,
    )
    eval_case_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_eval_case.id",
            ondelete="RESTRICT",
            name="fk_assistant_skill_eval_case_result_eval_case_id",
        ),
        nullable=False,
        index=True,
    )
    result_state = Column(String(32), nullable=False)
    assertion_details = Column(JSON, nullable=False, default=dict)
    actual_active_skills = Column(JSON, nullable=False, default=list)
    visible_capability_aliases = Column(JSON, nullable=False, default=list)
    call_trace = Column(JSON, nullable=False, default=list)
    stop_reason = Column(String(64), nullable=True)
    output_artifact_ids = Column(JSON, nullable=False, default=list)
    evidence_artifact_ids = Column(JSON, nullable=False, default=list)
    rounds = Column(Integer, nullable=True)
    calls = Column(Integer, nullable=True)
    tokens = Column(Integer, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    safe_error = Column(Text, nullable=True)
    result_digest = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    eval_run = relationship(
        "AssistantSkillEvalRun",
        back_populates="case_results",
        foreign_keys=[eval_run_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "eval_run_id",
            "eval_case_id",
            name="uq_assistant_skill_eval_case_result_run_case",
        ),
        CheckConstraint(
            "result_state IN ("
            "'passed','failed','indeterminate','error','skipped','cancelled'"
            ")",
            name="ck_assistant_skill_eval_case_result_state",
        ),
        CheckConstraint(
            "rounds IS NULL OR rounds >= 0",
            name="ck_assistant_skill_eval_case_result_rounds",
        ),
        CheckConstraint(
            "calls IS NULL OR calls >= 0",
            name="ck_assistant_skill_eval_case_result_calls",
        ),
        CheckConstraint(
            "tokens IS NULL OR tokens >= 0",
            name="ck_assistant_skill_eval_case_result_tokens",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_assistant_skill_eval_case_result_latency_ms",
        ),
        _sha256_check(
            "result_digest",
            name="ck_assistant_skill_eval_case_result_digest",
        ),
    )


class AssistantSkillEvalCapabilityCall(UuidPrimaryKeyMixin, Base):
    """Eval-only CapabilityCall evidence with synthetic IDs (no production FK)."""

    __tablename__ = "assistant_skill_eval_capability_call"

    # Synthetic primary key is `id` from UuidPrimaryKeyMixin. `eval_call_id` is
    # the stable logical identity used by recovery/replay CAS (may equal id).
    eval_call_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    eval_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_eval_run.id",
            ondelete="RESTRICT",
            name="fk_assistant_skill_eval_capability_call_eval_run_id",
        ),
        nullable=False,
        index=True,
    )
    eval_case_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_eval_case.id",
            ondelete="RESTRICT",
            name="fk_assistant_skill_eval_capability_call_eval_case_id",
        ),
        nullable=False,
        index=True,
    )
    logical_call_key = Column(String(256), nullable=False)
    parent_ordinal = Column(Integer, nullable=True)
    child_ordinal = Column(Integer, nullable=False, default=0, server_default=text("0"))
    attempt = Column(Integer, nullable=False, default=1, server_default=text("1"))
    owner_kind = Column(
        String(16), nullable=False, default="test", server_default=text("'test'")
    )
    subject_kind = Column(String(64), nullable=False)
    subject_aggregate_id = Column(UUID(as_uuid=True), nullable=False)
    subject_version_id = Column(UUID(as_uuid=True), nullable=False)
    subject_owner_digest = Column(String(64), nullable=False)
    binding_digest = Column(String(64), nullable=False)
    input_digest = Column(String(64), nullable=False)
    descriptor_digest = Column(String(64), nullable=False)
    policy_digest = Column(String(64), nullable=False)
    outcome = Column(String(32), nullable=False)
    decision_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    eval_run = relationship(
        "AssistantSkillEvalRun",
        back_populates="capability_calls",
        foreign_keys=[eval_run_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "eval_call_id",
            name="uq_assistant_skill_eval_capability_call_eval_call_id",
        ),
        UniqueConstraint(
            "eval_run_id",
            "eval_case_id",
            "logical_call_key",
            "attempt",
            name="uq_assistant_skill_eval_capability_call_attempt",
        ),
        CheckConstraint(
            "owner_kind = 'test'",
            name="ck_assistant_skill_eval_capability_call_owner_kind",
        ),
        CheckConstraint(
            "outcome IN ('succeeded_isolated','simulated','denied','failed')",
            name="ck_assistant_skill_eval_capability_call_outcome",
        ),
        CheckConstraint(
            "attempt > 0",
            name="ck_assistant_skill_eval_capability_call_attempt",
        ),
        CheckConstraint(
            "child_ordinal >= 0",
            name="ck_assistant_skill_eval_capability_call_child_ordinal",
        ),
        CheckConstraint(
            "parent_ordinal IS NULL OR parent_ordinal >= 0",
            name="ck_assistant_skill_eval_capability_call_parent_ordinal",
        ),
        _sha256_check(
            "subject_owner_digest",
            name="ck_assistant_skill_eval_capability_call_subject_owner_digest",
        ),
        _sha256_check(
            "binding_digest",
            name="ck_assistant_skill_eval_capability_call_binding_digest",
        ),
        _sha256_check(
            "input_digest",
            name="ck_assistant_skill_eval_capability_call_input_digest",
        ),
        _sha256_check(
            "descriptor_digest",
            name="ck_assistant_skill_eval_capability_call_descriptor_digest",
        ),
        _sha256_check(
            "policy_digest",
            name="ck_assistant_skill_eval_capability_call_policy_digest",
        ),
        # No FK to assistant_capability_call / assistant_chat_run — synthetic only.
    )


class AssistantSkillEvalEvent(UuidPrimaryKeyMixin, Base):
    """Append-only Eval Event: unique Run + monotonic sequence."""

    __tablename__ = "assistant_skill_eval_event"

    eval_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_eval_run.id",
            ondelete="RESTRICT",
            name="fk_assistant_skill_eval_event_eval_run_id",
        ),
        nullable=False,
        index=True,
    )
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    eval_run = relationship(
        "AssistantSkillEvalRun",
        back_populates="events",
        foreign_keys=[eval_run_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "eval_run_id",
            "sequence",
            name="uq_assistant_skill_eval_event_sequence",
        ),
        CheckConstraint(
            "sequence > 0",
            name="ck_assistant_skill_eval_event_sequence",
        ),
    )


class AssistantSkillEvalArtifact(UuidPrimaryKeyMixin, Base):
    """Eval Artifact: exactly one of bounded inline payload or eval object key."""

    __tablename__ = "assistant_skill_eval_artifact"

    eval_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_eval_run.id",
            ondelete="RESTRICT",
            name="fk_assistant_skill_eval_artifact_eval_run_id",
        ),
        nullable=False,
        index=True,
    )
    kind = Column(String(64), nullable=False)
    media_type = Column(String(255), nullable=False)
    label = Column(String(255), nullable=True)
    byte_size = Column(Integer, nullable=False)
    content_digest = Column(String(64), nullable=False)
    storage_kind = Column(String(16), nullable=False)
    inline_payload = Column(LargeBinary, nullable=True)
    object_key = Column(String(1024), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    eval_run = relationship(
        "AssistantSkillEvalRun",
        back_populates="artifacts",
        foreign_keys=[eval_run_id],
    )

    __table_args__ = (
        CheckConstraint(
            "storage_kind IN ('inline','object')",
            name="ck_assistant_skill_eval_artifact_storage_kind",
        ),
        CheckConstraint(
            "byte_size >= 0",
            name="ck_assistant_skill_eval_artifact_byte_size",
        ),
        CheckConstraint(
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
        _sha256_check(
            "content_digest",
            name="ck_assistant_skill_eval_artifact_content_digest",
        ),
        # Must match migration: evaluation-namespace keys only when present.
        CheckConstraint(
            "object_key IS NULL OR object_key LIKE 'skill-eval/%'",
            name="ck_assistant_skill_eval_artifact_object_key_namespace",
        ),
        Index(
            "uq_assistant_skill_eval_artifact_content",
            "eval_run_id",
            "content_digest",
            "byte_size",
            unique=True,
        ),
    )


class AssistantSkillPublishGate(UuidPrimaryKeyMixin, Base):
    """Append-only server-derived publish gate evidence."""

    __tablename__ = "assistant_skill_publish_gate"

    subject_kind = Column(String(64), nullable=False)
    subject_aggregate_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    subject_version_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    subject_content_digest = Column(String(64), nullable=False)
    subject_binding_digest = Column(String(64), nullable=False)
    profile_digest = Column(String(64), nullable=False)
    catalog_digest = Column(String(64), nullable=False)
    dataset_version_ids = Column(JSON, nullable=False, default=list)
    qualifying_eval_run_ids = Column(JSON, nullable=False, default=list)
    runtime_contract_version = Column(Integer, nullable=False)
    policy_version = Column(String(64), nullable=False)
    threshold_version = Column(String(64), nullable=False)
    build_revision = Column(String(160), nullable=False)
    # Server-stored action this gate authorizes (publish vs enable are distinct).
    action = Column(
        String(64),
        nullable=False,
        default="skill_publish",
        server_default=text("'skill_publish'"),
    )
    decision = Column(String(32), nullable=False)
    assertion_snapshot = Column(JSON, nullable=False, default=dict)
    metric_snapshot = Column(JSON, nullable=False, default=dict)
    actor_principal = Column(String(128), nullable=True)
    reason = Column(Text, nullable=True)
    waiver_codes = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Legacy column: pin correctness is derived from gate_use existence, not this.
    # Left at default 0; never relied on for retention/pin decisions.
    publication_pin_count = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    request_id = Column(String(128), nullable=True)

    uses = relationship(
        "AssistantSkillPublishGateUse",
        back_populates="gate",
        foreign_keys="AssistantSkillPublishGateUse.gate_id",
    )

    __table_args__ = (
        UniqueConstraint(
            "request_id",
            name="uq_assistant_skill_publish_gate_request_id",
        ),
        CheckConstraint(
            "subject_kind IN ("
            "'skill_draft','skill_version',"
            "'main_agent_profile_draft','main_agent_profile_version',"
            "'legacy_baseline'"
            ")",
            name="ck_assistant_skill_publish_gate_subject_kind",
        ),
        CheckConstraint(
            "action IN ("
            "'skill_publish','skill_catalog_enable',"
            "'profile_publish','profile_runtime_enable'"
            ")",
            name="ck_assistant_skill_publish_gate_action",
        ),
        CheckConstraint(
            "decision IN ('passed','failed','waived_non_safety')",
            name="ck_assistant_skill_publish_gate_decision",
        ),
        CheckConstraint(
            "runtime_contract_version > 0",
            name="ck_assistant_skill_publish_gate_runtime_contract_version",
        ),
        CheckConstraint(
            "publication_pin_count >= 0",
            name="ck_assistant_skill_publish_gate_publication_pin_count",
        ),
        _sha256_check(
            "subject_content_digest",
            name="ck_assistant_skill_publish_gate_subject_content_digest",
        ),
        _sha256_check(
            "subject_binding_digest",
            name="ck_assistant_skill_publish_gate_subject_binding_digest",
        ),
        _sha256_check(
            "profile_digest",
            name="ck_assistant_skill_publish_gate_profile_digest",
        ),
        _sha256_check(
            "catalog_digest",
            name="ck_assistant_skill_publish_gate_catalog_digest",
        ),
        Index(
            "ix_assistant_skill_publish_gate_subject_created",
            "subject_aggregate_id",
            "subject_version_id",
            "created_at",
        ),
    )


class AssistantSkillPublishGateUse(UuidPrimaryKeyMixin, Base):
    """Append-only link from one gate to the exact promotion action."""

    __tablename__ = "assistant_skill_publish_gate_use"

    gate_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_skill_publish_gate.id",
            ondelete="RESTRICT",
            name="fk_assistant_skill_publish_gate_use_gate_id",
        ),
        nullable=False,
        index=True,
    )
    action = Column(String(64), nullable=False)
    aggregate_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    resulting_version_id = Column(UUID(as_uuid=True), nullable=False)
    actor_principal = Column(String(128), nullable=False)
    request_id = Column(String(128), nullable=False)
    aggregate_revision = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    gate = relationship(
        "AssistantSkillPublishGate",
        back_populates="uses",
        foreign_keys=[gate_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "request_id",
            "action",
            name="uq_assistant_skill_publish_gate_use_request_action",
        ),
        # Durable single-consumption: one use row per gate+action even under concurrent
        # request_ids. Application-level SELECT is only a fast path; this constraint
        # is the source of truth.
        UniqueConstraint(
            "gate_id",
            "action",
            name="uq_assistant_skill_publish_gate_use_gate_action",
        ),
        CheckConstraint(
            "action IN ("
            "'skill_publish','skill_catalog_enable',"
            "'profile_publish','profile_runtime_enable'"
            ")",
            name="ck_assistant_skill_publish_gate_use_action",
        ),
        CheckConstraint(
            "aggregate_revision >= 0",
            name="ck_assistant_skill_publish_gate_use_aggregate_revision",
        ),
    )


EVAL_TABLE_NAMES: tuple[str, ...] = (
    "assistant_skill_eval_dataset",
    "assistant_skill_eval_dataset_draft",
    "assistant_skill_eval_dataset_version",
    "assistant_skill_eval_case",
    "assistant_skill_eval_run",
    "assistant_skill_eval_case_result",
    "assistant_skill_eval_capability_call",
    "assistant_skill_eval_event",
    "assistant_skill_eval_artifact",
    "assistant_skill_publish_gate",
    "assistant_skill_publish_gate_use",
)

# Tables that reject UPDATE/DELETE via PostgreSQL triggers.
# Events/artifacts reject UPDATE only (DELETE allowed for retention cleanup).
IMMUTABLE_EVAL_TABLES: tuple[str, ...] = (
    "assistant_skill_eval_dataset_version",
    "assistant_skill_eval_case",
    "assistant_skill_eval_case_result",
    "assistant_skill_eval_capability_call",
    "assistant_skill_publish_gate",
    "assistant_skill_publish_gate_use",
)
APPEND_ONLY_UPDATE_EVAL_TABLES: tuple[str, ...] = (
    "assistant_skill_eval_event",
    "assistant_skill_eval_artifact",
)

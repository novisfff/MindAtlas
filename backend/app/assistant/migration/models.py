"""Plan 10 runtime migration / rollout evidence ORM models.

Tables (additive, Deploy A):
- assistant_runtime_migration_item
- assistant_runtime_migration_event  (append-only)
- assistant_runtime_migration_batch
- assistant_runtime_rollout_revision  (immutable content after insert)
- assistant_runtime_rollout_event     (append-only)
- assistant_runtime_rollout_control   (singleton pointer)
- assistant_runtime_rollout_assignment
- assistant_runtime_admission_fallback_event  (append-only)
- assistant_runtime_shadow_comparison
- assistant_legacy_approval_archive   (append-only)
- assistant_runtime_cleanup_gate      (append-only)

RuntimeMigrationRepository is the only writer. Immutable/append-only children
have no ORM delete-orphan cascades.
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
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

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


# Fixed singleton primary key for rollout control pointer.
# Avoid all-zero hex prefix so SQLite numeric affinity cannot coerce the id.
ROLLOUT_CONTROL_SINGLETON_ID = "a0000000-0000-4000-8000-000000000010"
ROLLOUT_CONTROL_SINGLETON_KEY = "singleton"


class AssistantRuntimeMigrationItem(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Mutable migration subject aggregate; state changes audited by events."""

    __tablename__ = "assistant_runtime_migration_item"

    subject_kind = Column(String(32), nullable=False)
    source_type = Column(String(64), nullable=False)
    source_id = Column(String(160), nullable=False)
    source_name = Column(String(256), nullable=False, default="", server_default=text("''"))
    source_name_normalized = Column(String(256), nullable=False, default="", server_default=text("''"))
    source_digest = Column(String(64), nullable=False)
    target_type = Column(String(64), nullable=True)
    target_id = Column(String(160), nullable=True)
    target_version = Column(String(160), nullable=True)
    target_digest = Column(String(64), nullable=True)
    state = Column(
        String(32),
        nullable=False,
        default="discovered",
        server_default=text("'discovered'"),
    )
    reason_code = Column(String(128), nullable=True)
    evidence_json = Column(JSON, nullable=False, default=dict)
    source_revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    target_revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    state_revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    verified_at = Column(DateTime(timezone=True), nullable=True)
    actor_principal = Column(String(128), nullable=True)
    build_revision = Column(String(160), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "subject_kind",
            "source_type",
            "source_id",
            name="uq_assistant_runtime_migration_item_source",
        ),
        CheckConstraint(
            "subject_kind IN ("
            "'skill','profile','alias','l2_memory','approval',"
            "'entrypoint','package','write_branch'"
            ")",
            name="ck_assistant_runtime_migration_item_subject_kind",
        ),
        CheckConstraint(
            "state IN ("
            "'discovered','mapped','migrated','verified','blocked','archived'"
            ")",
            name="ck_assistant_runtime_migration_item_state",
        ),
        CheckConstraint(
            "source_revision >= 0",
            name="ck_assistant_runtime_migration_item_source_revision",
        ),
        CheckConstraint(
            "target_revision >= 0",
            name="ck_assistant_runtime_migration_item_target_revision",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_assistant_runtime_migration_item_attempt_count",
        ),
        CheckConstraint(
            "state_revision >= 0",
            name="ck_assistant_runtime_migration_item_state_revision",
        ),
        _sha256_check(
            "source_digest",
            name="ck_assistant_runtime_migration_item_source_digest",
        ),
        _nullable_sha256_check(
            "target_digest",
            name="ck_assistant_runtime_migration_item_target_digest",
        ),
        Index(
            "ix_assistant_runtime_migration_item_state",
            "state",
            "subject_kind",
        ),
    )


class AssistantRuntimeMigrationEvent(UuidPrimaryKeyMixin, Base):
    """Append-only migration item state transition event."""

    __tablename__ = "assistant_runtime_migration_event"

    migration_item_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_runtime_migration_item.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    previous_state = Column(String(32), nullable=True)
    new_state = Column(String(32), nullable=False)
    evidence_digest = Column(String(64), nullable=False)
    safe_details = Column(JSON, nullable=False, default=dict)
    actor_principal = Column(String(128), nullable=True)
    build_revision = Column(String(160), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "migration_item_id",
            "revision",
            name="uq_assistant_runtime_migration_event_revision",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_assistant_runtime_migration_event_revision",
        ),
        CheckConstraint(
            "new_state IN ("
            "'discovered','mapped','migrated','verified','blocked','archived'"
            ")",
            name="ck_assistant_runtime_migration_event_new_state",
        ),
        CheckConstraint(
            "previous_state IS NULL OR previous_state IN ("
            "'discovered','mapped','migrated','verified','blocked','archived'"
            ")",
            name="ck_assistant_runtime_migration_event_previous_state",
        ),
        _sha256_check(
            "evidence_digest",
            name="ck_assistant_runtime_migration_event_evidence_digest",
        ),
    )


class AssistantRuntimeMigrationBatch(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Resumable migration command batch (safe digests/counts only)."""

    __tablename__ = "assistant_runtime_migration_batch"

    command_kind = Column(String(32), nullable=False)
    source_snapshot_digest = Column(String(64), nullable=False)
    configuration_digest = Column(String(64), nullable=False)
    build_revision = Column(String(160), nullable=False)
    schema_revision = Column(String(64), nullable=False)
    environment = Column(String(128), nullable=False)
    database_fingerprint = Column(String(160), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="prepared",
        server_default=text("'prepared'"),
    )
    state_revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    batch_size = Column(Integer, nullable=False, default=100, server_default=text("100"))
    resume_cursor = Column(String(256), nullable=True)
    processed_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    succeeded_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    blocked_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    failed_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    request_id = Column(String(128), nullable=False)
    started_by = Column(String(128), nullable=True)
    completed_by = Column(String(128), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    report_artifact_id = Column(UUID(as_uuid=True), nullable=True)
    report_digest = Column(String(64), nullable=True)
    dry_run_digest = Column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "request_id",
            name="uq_assistant_runtime_migration_batch_request_id",
        ),
        CheckConstraint(
            "command_kind IN ("
            "'inventory','package','l2','approval','verify'"
            ")",
            name="ck_assistant_runtime_migration_batch_command_kind",
        ),
        CheckConstraint(
            "status IN ("
            "'prepared','running','completed','failed','cancelled'"
            ")",
            name="ck_assistant_runtime_migration_batch_status",
        ),
        CheckConstraint(
            "state_revision >= 0",
            name="ck_assistant_runtime_migration_batch_state_revision",
        ),
        CheckConstraint(
            "batch_size > 0 AND batch_size <= 1000",
            name="ck_assistant_runtime_migration_batch_size",
        ),
        CheckConstraint(
            "processed_count >= 0 AND succeeded_count >= 0 "
            "AND blocked_count >= 0 AND failed_count >= 0",
            name="ck_assistant_runtime_migration_batch_counts",
        ),
        _sha256_check(
            "source_snapshot_digest",
            name="ck_assistant_runtime_migration_batch_source_snapshot_digest",
        ),
        _sha256_check(
            "configuration_digest",
            name="ck_assistant_runtime_migration_batch_configuration_digest",
        ),
        _nullable_sha256_check(
            "report_digest",
            name="ck_assistant_runtime_migration_batch_report_digest",
        ),
        _nullable_sha256_check(
            "dry_run_digest",
            name="ck_assistant_runtime_migration_batch_dry_run_digest",
        ),
        Index(
            "ix_assistant_runtime_migration_batch_status",
            "status",
            "command_kind",
        ),
    )


class AssistantRuntimeRolloutRevision(UuidPrimaryKeyMixin, Base):
    """Immutable rollout configuration revision (content never changes after insert)."""

    __tablename__ = "assistant_runtime_rollout_revision"

    revision_label = Column(String(128), nullable=False, unique=True)
    runtime_mode = Column(String(32), nullable=False, default="legacy", server_default=text("'legacy'"))
    shadow_eligible_scope = Column(String(64), nullable=False, default="none", server_default=text("'none'"))
    shadow_percent = Column(Integer, nullable=False, default=0, server_default=text("0"))
    read_canary_percent = Column(Integer, nullable=False, default=0, server_default=text("0"))
    write_mode = Column(String(32), nullable=False, default="off", server_default=text("'off'"))
    write_percent = Column(Integer, nullable=False, default=0, server_default=text("0"))
    eligible_closure_digest = Column(String(64), nullable=False)
    config_origin = Column(
        String(32),
        nullable=False,
        default="native",
        server_default=text("'native'"),
    )
    build_revision = Column(String(160), nullable=False)
    runtime_contract_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    policy_contract_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    worker_contract_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    cohort_salt_fingerprint = Column(String(64), nullable=False)
    metric_definition_id = Column(String(128), nullable=True)
    metric_window_id = Column(String(128), nullable=True)
    approval_artifact_id = Column(UUID(as_uuid=True), nullable=True)
    evidence_artifact_id = Column(UUID(as_uuid=True), nullable=True)
    config_json = Column(JSON, nullable=False, default=dict)
    config_digest = Column(String(64), nullable=False)
    actor_principal = Column(String(128), nullable=True)
    reason = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "runtime_mode IN ('legacy','main_agent')",
            name="ck_assistant_runtime_rollout_revision_runtime_mode",
        ),
        CheckConstraint(
            "shadow_eligible_scope IN ('none','staff','fixture','approved_production')",
            name="ck_assistant_runtime_rollout_revision_shadow_scope",
        ),
        CheckConstraint(
            "shadow_percent >= 0 AND shadow_percent <= 100",
            name="ck_assistant_runtime_rollout_revision_shadow_percent",
        ),
        CheckConstraint(
            "read_canary_percent >= 0 AND read_canary_percent <= 100",
            name="ck_assistant_runtime_rollout_revision_read_canary_percent",
        ),
        CheckConstraint(
            "write_mode IN ('off','golden')",
            name="ck_assistant_runtime_rollout_revision_write_mode",
        ),
        CheckConstraint(
            "write_percent >= 0 AND write_percent <= 100",
            name="ck_assistant_runtime_rollout_revision_write_percent",
        ),
        CheckConstraint(
            "config_origin IN ('native','plan04_compat')",
            name="ck_assistant_runtime_rollout_revision_config_origin",
        ),
        # plan04_compat fixes production paired-shadow eligibility to zero and
        # cannot be activated as canary/main.
        CheckConstraint(
            "config_origin <> 'plan04_compat' OR ("
            "shadow_percent = 0 AND read_canary_percent = 0 "
            "AND write_percent = 0 AND runtime_mode = 'legacy'"
            ")",
            name="ck_assistant_runtime_rollout_revision_plan04_compat_shape",
        ),
        CheckConstraint(
            "runtime_contract_version > 0 AND policy_contract_version > 0 "
            "AND worker_contract_version > 0",
            name="ck_assistant_runtime_rollout_revision_contract_versions",
        ),
        _sha256_check(
            "eligible_closure_digest",
            name="ck_assistant_runtime_rollout_revision_eligible_closure_digest",
        ),
        _sha256_check(
            "cohort_salt_fingerprint",
            name="ck_assistant_runtime_rollout_revision_cohort_salt_fingerprint",
        ),
        _sha256_check(
            "config_digest",
            name="ck_assistant_runtime_rollout_revision_config_digest",
        ),
    )


class AssistantRuntimeRolloutEvent(UuidPrimaryKeyMixin, Base):
    """Append-only rollout control event."""

    __tablename__ = "assistant_runtime_rollout_event"

    rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_runtime_rollout_revision.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    action = Column(String(32), nullable=False)
    previous_active_revision_id = Column(UUID(as_uuid=True), nullable=True)
    control_revision = Column(Integer, nullable=False)
    actor_principal = Column(String(128), nullable=True)
    reason = Column(String(512), nullable=True)
    evidence_digest = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        # control_revision is monotonic per control pointer advance; prepare events
        # share the event stream without requiring global uniqueness alone.
        Index(
            "ix_assistant_runtime_rollout_event_control_revision",
            "control_revision",
        ),
        CheckConstraint(
            "action IN ('prepared','activated','superseded','rolled_back')",
            name="ck_assistant_runtime_rollout_event_action",
        ),
        CheckConstraint(
            "control_revision > 0",
            name="ck_assistant_runtime_rollout_event_control_revision",
        ),
        _sha256_check(
            "evidence_digest",
            name="ck_assistant_runtime_rollout_event_evidence_digest",
        ),
    )


class AssistantRuntimeRolloutControl(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Singleton control pointer: active rollout revision + state_revision."""

    __tablename__ = "assistant_runtime_rollout_control"

    singleton_key = Column(
        String(32),
        nullable=False,
        unique=True,
        default=ROLLOUT_CONTROL_SINGLETON_KEY,
        server_default=text(f"'{ROLLOUT_CONTROL_SINGLETON_KEY}'"),
    )
    active_rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_runtime_rollout_revision.id", ondelete="RESTRICT"),
        nullable=True,
    )
    state_revision = Column(Integer, nullable=False, default=0, server_default=text("0"))

    __table_args__ = (
        CheckConstraint(
            "state_revision >= 0",
            name="ck_assistant_runtime_rollout_control_state_revision",
        ),
        CheckConstraint(
            f"singleton_key = '{ROLLOUT_CONTROL_SINGLETON_KEY}'",
            name="ck_assistant_runtime_rollout_control_singleton_key",
        ),
    )


class AssistantRuntimeRolloutAssignment(UuidPrimaryKeyMixin, Base):
    """Immutable cohort assignment for a conversation scope under a rollout revision."""

    __tablename__ = "assistant_runtime_rollout_assignment"

    conversation_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    principal_scope_digest = Column(String(64), nullable=True)
    rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_runtime_rollout_revision.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    cohort = Column(String(64), nullable=False, default="default", server_default=text("'default'"))
    assigned_runtime_kind = Column(String(32), nullable=False)
    assigned_write_mode = Column(String(32), nullable=False, default="off", server_default=text("'off'"))
    assignment_reason = Column(String(32), nullable=False)
    cohort_key_digest = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "rollout_revision_id",
            name="uq_assistant_runtime_rollout_assignment_scope_revision",
        ),
        CheckConstraint(
            "assigned_runtime_kind IN ('legacy','main_agent')",
            name="ck_assistant_runtime_rollout_assignment_runtime_kind",
        ),
        CheckConstraint(
            "assigned_write_mode IN ('off','golden')",
            name="ck_assistant_runtime_rollout_assignment_write_mode",
        ),
        CheckConstraint(
            "assignment_reason IN ('hash','staff','explicit_override','rollback')",
            name="ck_assistant_runtime_rollout_assignment_reason",
        ),
        _sha256_check(
            "cohort_key_digest",
            name="ck_assistant_runtime_rollout_assignment_cohort_key_digest",
        ),
        _nullable_sha256_check(
            "principal_scope_digest",
            name="ck_assistant_runtime_rollout_assignment_principal_scope_digest",
        ),
    )


class AssistantRuntimeAdmissionFallbackEvent(UuidPrimaryKeyMixin, Base):
    """Append-only pre-insert admission fallback evidence (legacy run atomicity)."""

    __tablename__ = "assistant_runtime_admission_fallback_event"

    request_id = Column(String(128), nullable=False)
    rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_runtime_rollout_revision.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    assignment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_runtime_rollout_assignment.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    candidate_runtime_kind = Column(
        String(32),
        nullable=False,
        default="main_agent",
        server_default=text("'main_agent'"),
    )
    selected_runtime_kind = Column(
        String(32),
        nullable=False,
        default="legacy",
        server_default=text("'legacy'"),
    )
    reason = Column(
        String(64),
        nullable=False,
        default="preinsert_fallback",
        server_default=text("'preinsert_fallback'"),
    )
    admission_failure_digest = Column(String(64), nullable=False)
    resulting_legacy_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    principal_scope_digest = Column(String(64), nullable=True)
    build_revision = Column(String(160), nullable=True)
    schema_revision = Column(String(64), nullable=True)
    runtime_contract_version = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "request_id",
            name="uq_assistant_runtime_admission_fallback_event_request_id",
        ),
        CheckConstraint(
            "candidate_runtime_kind = 'main_agent'",
            name="ck_assistant_runtime_admission_fallback_candidate",
        ),
        CheckConstraint(
            "selected_runtime_kind = 'legacy'",
            name="ck_assistant_runtime_admission_fallback_selected",
        ),
        CheckConstraint(
            "reason = 'preinsert_fallback'",
            name="ck_assistant_runtime_admission_fallback_reason",
        ),
        _sha256_check(
            "admission_failure_digest",
            name="ck_assistant_runtime_admission_fallback_failure_digest",
        ),
        _nullable_sha256_check(
            "principal_scope_digest",
            name="ck_assistant_runtime_admission_fallback_principal_scope_digest",
        ),
    )


class AssistantRuntimeShadowComparison(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Pairs a production Chat Run with an isolated Eval shadow Run (never ChatRun)."""

    __tablename__ = "assistant_runtime_shadow_comparison"

    production_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    eval_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill_eval_run.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    rollout_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_runtime_rollout_revision.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    assignment_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_runtime_rollout_assignment.id", ondelete="RESTRICT"),
        nullable=True,
    )
    shadow_eligible = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    input_digest = Column(String(64), nullable=False)
    context_digest = Column(String(64), nullable=False)
    fixture_digest = Column(String(64), nullable=True)
    catalog_revision = Column(String(160), nullable=True)
    profile_revision = Column(String(160), nullable=True)
    model_revision = Column(String(160), nullable=True)
    runtime_revision = Column(String(160), nullable=True)
    build_revision = Column(String(160), nullable=True)
    intent_class = Column(String(64), nullable=True)
    write_simulation_required = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    legacy_skill_selection = Column(String(160), nullable=True)
    new_skill_selection = Column(String(160), nullable=True)
    capability_path_summary = Column(String(256), nullable=True)
    completion_summary = Column(String(256), nullable=True)
    stop_summary = Column(String(256), nullable=True)
    error_summary = Column(String(256), nullable=True)
    quality_assertion_snapshot = Column(JSON, nullable=False, default=dict)
    rounds_estimate = Column(Integer, nullable=True)
    calls_estimate = Column(Integer, nullable=True)
    tokens_estimate = Column(Integer, nullable=True)
    latency_ms_estimate = Column(Integer, nullable=True)
    cost_estimate_micros = Column(Integer, nullable=True)
    reviewer_state = Column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    result_state = Column(
        String(32),
        nullable=False,
        default="open",
        server_default=text("'open'"),
    )
    private_input_snapshot_id = Column(UUID(as_uuid=True), nullable=True)
    private_input_payload_digest = Column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "production_run_id",
            "eval_run_id",
            name="uq_assistant_runtime_shadow_comparison_pair",
        ),
        UniqueConstraint(
            "eval_run_id",
            name="uq_assistant_runtime_shadow_comparison_eval_run",
        ),
        CheckConstraint(
            "reviewer_state IN ('pending','reviewed','waived')",
            name="ck_assistant_runtime_shadow_comparison_reviewer_state",
        ),
        CheckConstraint(
            "result_state IN ('open','match','diff','error','cancelled')",
            name="ck_assistant_runtime_shadow_comparison_result_state",
        ),
        _sha256_check(
            "input_digest",
            name="ck_assistant_runtime_shadow_comparison_input_digest",
        ),
        _sha256_check(
            "context_digest",
            name="ck_assistant_runtime_shadow_comparison_context_digest",
        ),
        _nullable_sha256_check(
            "fixture_digest",
            name="ck_assistant_runtime_shadow_comparison_fixture_digest",
        ),
        _nullable_sha256_check(
            "private_input_payload_digest",
            name="ck_arm_shadow_cmp_private_input_payload_digest",
        ),
    )


class AssistantLegacyApprovalArchive(UuidPrimaryKeyMixin, Base):
    """Immutable terminal legacy approval archive (not resumable)."""

    __tablename__ = "assistant_legacy_approval_archive"

    source_row_id = Column(String(160), nullable=False)
    source_run_id = Column(String(160), nullable=True)
    source_conversation_id = Column(String(160), nullable=True)
    safe_payload_digest = Column(String(64), nullable=False)
    status = Column(String(64), nullable=False)
    decision = Column(String(64), nullable=True)
    source_created_at = Column(DateTime(timezone=True), nullable=True)
    source_resolved_at = Column(DateTime(timezone=True), nullable=True)
    migration_evidence_digest = Column(String(64), nullable=False)
    actor_principal = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "source_row_id",
            name="uq_assistant_legacy_approval_archive_source_row",
        ),
        _sha256_check(
            "safe_payload_digest",
            name="ck_assistant_legacy_approval_archive_safe_payload_digest",
        ),
        _sha256_check(
            "migration_evidence_digest",
            name="ck_assistant_legacy_approval_archive_migration_evidence_digest",
        ),
    )


class AssistantRuntimeCleanupGate(UuidPrimaryKeyMixin, Base):
    """Append-only cleanup gate evidence (deploy_b1 / deploy_b2)."""

    __tablename__ = "assistant_runtime_cleanup_gate"

    gate_kind = Column(String(32), nullable=False)
    decision = Column(String(16), nullable=False)
    schema_revision = Column(String(64), nullable=False)
    build_revision = Column(String(160), nullable=False)
    runtime_revision = Column(String(160), nullable=True)
    actor_principal = Column(String(128), nullable=True)
    reason = Column(String(512), nullable=True)
    inventory_digest = Column(String(64), nullable=False)
    migration_batch_digest = Column(String(64), nullable=True)
    rollout_revision_digest = Column(String(64), nullable=True)
    metric_window_digest = Column(String(64), nullable=True)
    backup_restore_digest = Column(String(64), nullable=True)
    legacy_access_window_digest = Column(String(64), nullable=True)
    archive_count_digest = Column(String(64), nullable=True)
    reconciliation_digest = Column(String(64), nullable=True)
    evidence_digest = Column(String(64), nullable=False)
    snapshot_counts = Column(JSON, nullable=False, default=dict)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    invalidation_reason = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "gate_kind IN ('deploy_b1','deploy_b2')",
            name="ck_assistant_runtime_cleanup_gate_kind",
        ),
        CheckConstraint(
            "decision IN ('passed','failed')",
            name="ck_assistant_runtime_cleanup_gate_decision",
        ),
        _sha256_check(
            "inventory_digest",
            name="ck_assistant_runtime_cleanup_gate_inventory_digest",
        ),
        _sha256_check(
            "evidence_digest",
            name="ck_assistant_runtime_cleanup_gate_evidence_digest",
        ),
        _nullable_sha256_check(
            "migration_batch_digest",
            name="ck_assistant_runtime_cleanup_gate_migration_batch_digest",
        ),
        _nullable_sha256_check(
            "rollout_revision_digest",
            name="ck_assistant_runtime_cleanup_gate_rollout_revision_digest",
        ),
        _nullable_sha256_check(
            "metric_window_digest",
            name="ck_assistant_runtime_cleanup_gate_metric_window_digest",
        ),
        _nullable_sha256_check(
            "backup_restore_digest",
            name="ck_assistant_runtime_cleanup_gate_backup_restore_digest",
        ),
        _nullable_sha256_check(
            "legacy_access_window_digest",
            name="ck_assistant_runtime_cleanup_gate_legacy_access_window_digest",
        ),
        _nullable_sha256_check(
            "archive_count_digest",
            name="ck_assistant_runtime_cleanup_gate_archive_count_digest",
        ),
        _nullable_sha256_check(
            "reconciliation_digest",
            name="ck_assistant_runtime_cleanup_gate_reconciliation_digest",
        ),
        Index(
            "ix_assistant_runtime_cleanup_gate_kind_created",
            "gate_kind",
            "created_at",
        ),
    )


__all__ = (
    "ROLLOUT_CONTROL_SINGLETON_ID",
    "ROLLOUT_CONTROL_SINGLETON_KEY",
    "AssistantLegacyApprovalArchive",
    "AssistantRuntimeAdmissionFallbackEvent",
    "AssistantRuntimeCleanupGate",
    "AssistantRuntimeMigrationBatch",
    "AssistantRuntimeMigrationEvent",
    "AssistantRuntimeMigrationItem",
    "AssistantRuntimeRolloutAssignment",
    "AssistantRuntimeRolloutControl",
    "AssistantRuntimeRolloutEvent",
    "AssistantRuntimeRolloutRevision",
    "AssistantRuntimeShadowComparison",
)

"""Durable Main Agent Run persistence models (Plan 06).

Child tables, worker registration, and artifact GC outbox.
Aggregate root extensions live on ``app.assistant.models.AssistantChatRun``.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.common.models import UuidPrimaryKeyMixin
from app.common.time import utcnow
from app.database import Base


def _sha256_check(column: str, *, name: str) -> CheckConstraint:
    return CheckConstraint(f"length({column}) = 64", name=name)


def _nullable_sha256_check(column: str, *, name: str) -> CheckConstraint:
    return CheckConstraint(
        f"{column} IS NULL OR length({column}) = 64",
        name=name,
    )


class AssistantWorkerRegistration(Base):
    """Mutable worker liveness registration (not a lease)."""

    __tablename__ = "assistant_worker_registration"

    worker_id = Column(String(160), primary_key=True)
    app_build_revision = Column(String(160), nullable=False)
    runtime_contract_version = Column(Integer, nullable=False)
    supported_checkpoint_codec_versions = Column(JSON, nullable=False)
    capability_feature_digest = Column(String(64), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    heartbeat_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    draining_at = Column(DateTime(timezone=True), nullable=True)
    hostname_label = Column(String(255), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "runtime_contract_version > 0",
            name="ck_assistant_worker_registration_contract_positive",
        ),
        _sha256_check(
            "capability_feature_digest",
            name="ck_assistant_worker_registration_feature_digest",
        ),
    )


class AssistantRunManifestRevision(UuidPrimaryKeyMixin, Base):
    """Immutable Manifest revision for a durable Run."""

    __tablename__ = "assistant_run_manifest_revision"

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    parent_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_manifest_revision.id", ondelete="RESTRICT"),
        nullable=True,
    )
    parent_digest = Column(String(64), nullable=True)
    manifest_digest = Column(String(64), nullable=False)
    schema_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_assistant_run_manifest_revision_positive"),
        CheckConstraint(
            "schema_version > 0",
            name="ck_assistant_run_manifest_schema_version_positive",
        ),
        _sha256_check(
            "manifest_digest",
            name="ck_assistant_run_manifest_digest",
        ),
        _nullable_sha256_check(
            "parent_digest",
            name="ck_assistant_run_manifest_parent_digest",
        ),
        Index(
            "uq_assistant_run_manifest_revision_run_rev",
            "run_id",
            "revision",
            unique=True,
        ),
        Index(
            "uq_assistant_run_manifest_revision_run_digest",
            "run_id",
            "manifest_digest",
            unique=True,
        ),
    )


class AssistantRunPolicyRevision(UuidPrimaryKeyMixin, Base):
    """Immutable EffectiveRunPolicySnapshot (+ grants) revision."""

    __tablename__ = "assistant_run_policy_revision"

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    parent_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_policy_revision.id", ondelete="RESTRICT"),
        nullable=True,
    )
    parent_digest = Column(String(64), nullable=True)
    policy_digest = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_assistant_run_policy_revision_positive"),
        _sha256_check("policy_digest", name="ck_assistant_run_policy_digest"),
        _nullable_sha256_check(
            "parent_digest",
            name="ck_assistant_run_policy_parent_digest",
        ),
        Index(
            "uq_assistant_run_policy_revision_run_rev",
            "run_id",
            "revision",
            unique=True,
        ),
        Index(
            "uq_assistant_run_policy_revision_run_digest",
            "run_id",
            "policy_digest",
            unique=True,
        ),
    )


class AssistantRunBudgetRevision(UuidPrimaryKeyMixin, Base):
    """Immutable BudgetLedgerState revision."""

    __tablename__ = "assistant_run_budget_revision"

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    parent_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_budget_revision.id", ondelete="RESTRICT"),
        nullable=True,
    )
    parent_digest = Column(String(64), nullable=True)
    budget_digest = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_assistant_run_budget_revision_positive"),
        _sha256_check("budget_digest", name="ck_assistant_run_budget_digest"),
        _nullable_sha256_check(
            "parent_digest",
            name="ck_assistant_run_budget_parent_digest",
        ),
        Index(
            "uq_assistant_run_budget_revision_run_rev",
            "run_id",
            "revision",
            unique=True,
        ),
        Index(
            "uq_assistant_run_budget_revision_run_digest",
            "run_id",
            "budget_digest",
            unique=True,
        ),
    )


class AssistantRunObligationRevision(UuidPrimaryKeyMixin, Base):
    """Immutable ObligationLedgerState revision."""

    __tablename__ = "assistant_run_obligation_revision"

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    parent_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_obligation_revision.id", ondelete="RESTRICT"),
        nullable=True,
    )
    parent_digest = Column(String(64), nullable=True)
    obligation_digest = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "revision > 0",
            name="ck_assistant_run_obligation_revision_positive",
        ),
        _sha256_check(
            "obligation_digest",
            name="ck_assistant_run_obligation_digest",
        ),
        _nullable_sha256_check(
            "parent_digest",
            name="ck_assistant_run_obligation_parent_digest",
        ),
        Index(
            "uq_assistant_run_obligation_revision_run_rev",
            "run_id",
            "revision",
            unique=True,
        ),
        Index(
            "uq_assistant_run_obligation_revision_run_digest",
            "run_id",
            "obligation_digest",
            unique=True,
        ),
    )


class AssistantRunProviderMessage(UuidPrimaryKeyMixin, Base):
    """Immutable Provider transcript message with protected role contract."""

    __tablename__ = "assistant_run_provider_message"

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal = Column(Integer, nullable=False)
    provider_round = Column(Integer, nullable=False, default=0, server_default=text("0"))
    role = Column(String(32), nullable=False)
    payload_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    payload_discriminator = Column(String(64), nullable=True)
    payload_body = Column(JSON, nullable=False)
    protection_kind = Column(String(32), nullable=False, default="public")
    content_digest = Column(String(64), nullable=False)
    manifest_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_manifest_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_policy_revision.id", ondelete="RESTRICT"),
        nullable=True,
    )
    obligation_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_obligation_revision.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provider_message_id = Column(String(128), nullable=True)
    tool_call_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ck_assistant_run_provider_message_ordinal"),
        CheckConstraint(
            "provider_round >= 0",
            name="ck_assistant_run_provider_message_round",
        ),
        CheckConstraint(
            "payload_version > 0",
            name="ck_assistant_run_provider_message_payload_version",
        ),
        CheckConstraint(
            "role IN ("
            "'system','runtime_instruction','runtime_context','runtime_completion',"
            "'user','assistant','tool'"
            ")",
            name="ck_assistant_run_provider_message_role",
        ),
        CheckConstraint(
            "protection_kind IN ('public','protected','internal')",
            name="ck_assistant_run_provider_message_protection",
        ),
        # Manifest always required (enforced by NOT NULL FK).
        # Protected Main Agent runtime roles require policy revision;
        # runtime_completion additionally requires obligation revision.
        # Bare system may not carry protected discriminators / protection_kind.
        CheckConstraint(
            "("
            "  role IN ('system','user','assistant','tool')"
            "  AND ("
            "    role <> 'system'"
            "    OR (protection_kind <> 'protected' AND payload_discriminator IS NULL)"
            "  )"
            ") OR ("
            "  role = 'runtime_instruction'"
            "  AND protection_kind = 'protected'"
            "  AND policy_revision_id IS NOT NULL"
            "  AND obligation_revision_id IS NULL"
            ") OR ("
            "  role = 'runtime_context'"
            "  AND protection_kind = 'protected'"
            "  AND policy_revision_id IS NOT NULL"
            "  AND obligation_revision_id IS NULL"
            ") OR ("
            "  role = 'runtime_completion'"
            "  AND protection_kind = 'protected'"
            "  AND policy_revision_id IS NOT NULL"
            "  AND obligation_revision_id IS NOT NULL"
            ")",
            name="ck_assistant_run_provider_message_role_links",
        ),
        _sha256_check(
            "content_digest",
            name="ck_assistant_run_provider_message_content_digest",
        ),
        Index(
            "uq_assistant_run_provider_message_ordinal",
            "run_id",
            "ordinal",
            unique=True,
        ),
        Index(
            "uq_assistant_run_provider_message_tool_call",
            "run_id",
            "tool_call_id",
            unique=True,
            postgresql_where=text("tool_call_id IS NOT NULL"),
            sqlite_where=text("tool_call_id IS NOT NULL"),
        ),
    )


class AssistantRunCheckpoint(UuidPrimaryKeyMixin, Base):
    """Immutable durable Checkpoint for a Main Agent Run."""

    __tablename__ = "assistant_run_checkpoint"

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence = Column(Integer, nullable=False)
    expected_state_revision = Column(Integer, nullable=False)
    committed_state_revision = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    manifest_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_manifest_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    policy_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_policy_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    budget_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_budget_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    obligation_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_obligation_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_message_ordinal = Column(Integer, nullable=False)
    provider_transcript_digest = Column(String(64), nullable=False)
    phase = Column(String(64), nullable=False)
    logical_unit_id = Column(String(128), nullable=True)
    reason = Column(String(128), nullable=True)
    state_payload = Column(JSON, nullable=False)
    state_digest = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("sequence > 0", name="ck_assistant_run_checkpoint_sequence"),
        CheckConstraint(
            "expected_state_revision >= 0",
            name="ck_assistant_run_checkpoint_expected_revision",
        ),
        CheckConstraint(
            "committed_state_revision >= 0",
            name="ck_assistant_run_checkpoint_committed_revision",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_assistant_run_checkpoint_schema_version",
        ),
        CheckConstraint(
            "phase IN ("
            "'ready_for_provider','dispatching_calls','waiting',"
            "'ready_for_completion','ready_for_memory','terminal'"
            ")",
            name="ck_assistant_run_checkpoint_phase",
        ),
        _sha256_check(
            "provider_transcript_digest",
            name="ck_assistant_run_checkpoint_transcript_digest",
        ),
        _sha256_check(
            "state_digest",
            name="ck_assistant_run_checkpoint_state_digest",
        ),
        Index(
            "uq_assistant_run_checkpoint_sequence",
            "run_id",
            "sequence",
            unique=True,
        ),
        Index(
            "uq_assistant_run_checkpoint_committed_revision",
            "run_id",
            "committed_state_revision",
            unique=True,
        ),
        Index(
            "uq_assistant_run_checkpoint_state_digest",
            "run_id",
            "state_digest",
            unique=True,
        ),
    )


class AssistantRunArtifact(UuidPrimaryKeyMixin, Base):
    """Immutable Run Artifact (inline bytes or private object reference)."""

    __tablename__ = "assistant_run_artifact"

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = Column(String(64), nullable=False)
    media_type = Column(String(255), nullable=False)
    display_label = Column(String(255), nullable=True)
    storage_kind = Column(String(16), nullable=False)
    byte_size = Column(Integer, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    inline_bytes = Column(LargeBinary, nullable=True)
    object_key = Column(String(1024), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "storage_kind IN ('inline','object')",
            name="ck_assistant_run_artifact_storage_kind",
        ),
        CheckConstraint(
            "byte_size >= 0",
            name="ck_assistant_run_artifact_byte_size",
        ),
        # Exactly one of inline_bytes / object_key, matching storage_kind.
        CheckConstraint(
            "("
            "  storage_kind = 'inline'"
            "  AND inline_bytes IS NOT NULL"
            "  AND object_key IS NULL"
            ") OR ("
            "  storage_kind = 'object'"
            "  AND inline_bytes IS NULL"
            "  AND object_key IS NOT NULL"
            ")",
            name="ck_assistant_run_artifact_storage_xor",
        ),
        _sha256_check(
            "content_sha256",
            name="ck_assistant_run_artifact_content_sha256",
        ),
        Index(
            "uq_assistant_run_artifact_content",
            "run_id",
            "content_sha256",
            "byte_size",
            unique=True,
        ),
    )


class AssistantRunArtifactGc(UuidPrimaryKeyMixin, Base):
    """Independent Artifact object GC outbox (survives Run deletion)."""

    __tablename__ = "assistant_run_artifact_gc"

    # BigInteger id alternative not needed; UUID PK is fine for outbox.
    bucket_name = Column(String(255), nullable=False)
    object_key = Column(String(1024), nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending", server_default=text("'pending'"))
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','in_progress','deleted','failed')",
            name="ck_assistant_run_artifact_gc_status",
        ),
        CheckConstraint(
            "attempts >= 0",
            name="ck_assistant_run_artifact_gc_attempts",
        ),
        _sha256_check(
            "content_sha256",
            name="ck_assistant_run_artifact_gc_content_sha256",
        ),
        Index(
            "uq_assistant_run_artifact_gc_object",
            "bucket_name",
            "object_key",
            "content_sha256",
            unique=True,
        ),
        Index(
            "ix_assistant_run_artifact_gc_status_next",
            "status",
            "next_attempt_at",
        ),
    )


class AssistantRunInterrupt(UuidPrimaryKeyMixin, Base):
    """Durable human Interrupt row (Plan 07).

    Request identity and budget suspension are immutable. Repository/trigger
    permit only token rotation and one pending -> terminal resolution mutation.
    ``capability_call_id`` remains null in Plan 07.
    """

    __tablename__ = "assistant_run_interrupt"

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    interrupt_key = Column(String(160), nullable=False)
    kind = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="pending", server_default=text("'pending'"))
    checkpoint_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_checkpoint.id", ondelete="RESTRICT"),
        nullable=False,
    )
    resolution_checkpoint_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_checkpoint.id", ondelete="RESTRICT"),
        nullable=True,
    )
    manifest_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_manifest_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_skill_package_id = Column(UUID(as_uuid=True), nullable=True)
    owner_skill_version_id = Column(UUID(as_uuid=True), nullable=True)
    # Always null in Plan 07; Plan 08 adds FK/population.
    capability_call_id = Column(UUID(as_uuid=True), nullable=True)
    workflow_frame_id = Column(UUID(as_uuid=True), nullable=False)
    node_id = Column(String(128), nullable=False)
    node_visit_id = Column(String(160), nullable=False)
    request_revision = Column(Integer, nullable=False, default=1, server_default=text("1"))
    request_run_revision = Column(Integer, nullable=False)
    resolution_run_revision = Column(Integer, nullable=True)
    budget_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_budget_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    budget_suspension_state = Column(JSON, nullable=False)
    budget_suspension_digest = Column(String(64), nullable=False)
    resolution_budget_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_budget_revision.id", ondelete="RESTRICT"),
        nullable=True,
    )
    request_payload = Column(JSON, nullable=False)
    request_digest = Column(String(64), nullable=False)
    field_schema = Column(JSON, nullable=True)
    field_schema_digest = Column(String(64), nullable=True)
    initial_values = Column(JSON, nullable=False, default=dict)
    submitted_values = Column(JSON, nullable=True)
    decision = Column(String(32), nullable=True)
    comment = Column(String(4000), nullable=True)
    resume_token_digest = Column(String(64), nullable=True)
    token_revision = Column(Integer, nullable=False, default=0, server_default=text("0"))
    resolution_request_id = Column(UUID(as_uuid=True), nullable=True)
    resolution_digest = Column(String(64), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    token_rotated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('approval','input')",
            name="ck_assistant_run_interrupt_kind",
        ),
        CheckConstraint(
            "status IN ("
            "'pending','approved','rejected','submitted','cancelled','expired'"
            ")",
            name="ck_assistant_run_interrupt_status",
        ),
        CheckConstraint(
            "request_revision > 0",
            name="ck_assistant_run_interrupt_request_revision",
        ),
        CheckConstraint(
            "request_run_revision >= 0",
            name="ck_assistant_run_interrupt_request_run_revision",
        ),
        CheckConstraint(
            "resolution_run_revision IS NULL OR resolution_run_revision >= 0",
            name="ck_assistant_run_interrupt_resolution_run_revision",
        ),
        CheckConstraint(
            "token_revision >= 0",
            name="ck_assistant_run_interrupt_token_revision",
        ),
        # input requires schema; simple approval may omit it.
        CheckConstraint(
            "("
            "  kind = 'approval'"
            ") OR ("
            "  kind = 'input'"
            "  AND field_schema IS NOT NULL"
            "  AND field_schema_digest IS NOT NULL"
            ")",
            name="ck_assistant_run_interrupt_input_schema",
        ),
        # Queued resolution requires both resolution pointers; terminal outcome keeps both null.
        CheckConstraint(
            "("
            "  resolution_budget_revision_id IS NULL"
            "  AND resolution_checkpoint_id IS NULL"
            ") OR ("
            "  resolution_budget_revision_id IS NOT NULL"
            "  AND resolution_checkpoint_id IS NOT NULL"
            ")",
            name="ck_assistant_run_interrupt_resolution_pair",
        ),
        _sha256_check(
            "budget_suspension_digest",
            name="ck_assistant_run_interrupt_budget_suspension_digest",
        ),
        _sha256_check(
            "request_digest",
            name="ck_assistant_run_interrupt_request_digest",
        ),
        _nullable_sha256_check(
            "field_schema_digest",
            name="ck_assistant_run_interrupt_field_schema_digest",
        ),
        _nullable_sha256_check(
            "resume_token_digest",
            name="ck_assistant_run_interrupt_resume_token_digest",
        ),
        _nullable_sha256_check(
            "resolution_digest",
            name="ck_assistant_run_interrupt_resolution_digest",
        ),
        Index(
            "uq_assistant_run_interrupt_run_key",
            "run_id",
            "interrupt_key",
            unique=True,
        ),
        # One pending Interrupt per Run (PostgreSQL partial unique).
        Index(
            "uq_assistant_run_interrupt_one_pending",
            "run_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index(
            "uq_assistant_run_interrupt_resolution_request",
            "run_id",
            "resolution_request_id",
            unique=True,
            postgresql_where=text("resolution_request_id IS NOT NULL"),
            sqlite_where=text("resolution_request_id IS NOT NULL"),
        ),
        Index(
            "ix_assistant_run_interrupt_run_status",
            "run_id",
            "status",
        ),
        Index(
            "ix_assistant_run_interrupt_expires_at",
            "expires_at",
        ),
    )

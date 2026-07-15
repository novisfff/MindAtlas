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

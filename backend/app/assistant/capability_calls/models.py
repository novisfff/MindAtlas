"""ORM models for the CapabilityCall ledger (Plan 08 Task 1).

Tables:
- assistant_capability_call
- assistant_capability_call_attempt
- assistant_capability_reconciliation

Immutable identity fields and append-only attempt/reconciliation history are
enforced by repository API (Task 2) plus PostgreSQL triggers in the migration.
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


class AssistantCapabilityCall(UuidPrimaryKeyMixin, Base):
    """Durable CapabilityCall ledger row.

    Identity/evidence digests are immutable after insert. Status transitions
    and attempt_count/state_revision advance only through the CAS repository.
    """

    __tablename__ = "assistant_capability_call"

    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    manifest_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_manifest_revision.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_tool_call_id = Column(String(160), nullable=True)
    parent_call_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_capability_call.id", ondelete="RESTRICT"),
        nullable=True,
    )
    logical_call_key = Column(String(160), nullable=False)
    owner_kind = Column(String(32), nullable=False)
    owner_id = Column(UUID(as_uuid=True), nullable=True)
    owner_version_id = Column(UUID(as_uuid=True), nullable=True)
    capability_type = Column(String(32), nullable=False)
    domain_key = Column(String(160), nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=True)
    target_version_id = Column(UUID(as_uuid=True), nullable=True)
    descriptor_digest = Column(String(64), nullable=False)
    authorization_digest = Column(String(64), nullable=False)
    approval_binding_digest = Column(String(64), nullable=True)
    input_artifact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_artifact.id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_digest = Column(String(64), nullable=False)
    side_effect_class = Column(String(32), nullable=False)
    execution_mode = Column(String(32), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    status = Column(
        String(32),
        nullable=False,
        default="proposed",
        server_default=text("'proposed'"),
    )
    state_revision = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    attempt_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    side_effect_started_at = Column(DateTime(timezone=True), nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    output_artifact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_artifact.id", ondelete="RESTRICT"),
        nullable=True,
    )
    interrupt_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_run_interrupt.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_assistant_capability_call_interrupt_id",
        ),
        nullable=True,
    )
    failure_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    terminal_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "owner_kind IN ('main_agent','skill_version','capability_call')",
            name="ck_assistant_capability_call_owner_kind",
        ),
        CheckConstraint(
            "side_effect_class IN ("
            "'none','compute','read','draft','write_local','write_external','unknown'"
            ")",
            name="ck_assistant_capability_call_side_effect",
        ),
        CheckConstraint(
            "execution_mode IN ("
            "'pure_replayable','read_replayable','local_transactional',"
            "'external_idempotent','external_reconcilable','non_retriable','unsupported'"
            ")",
            name="ck_assistant_capability_call_execution_mode",
        ),
        CheckConstraint(
            "status IN ("
            "'proposed','denied','awaiting_approval','authorized','rejected',"
            "'cancelled','expired','executing','succeeded','failed','unknown',"
            "'needs_reconciliation','compensated'"
            ")",
            name="ck_assistant_capability_call_status",
        ),
        CheckConstraint(
            "state_revision >= 0",
            name="ck_assistant_capability_call_state_revision",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_assistant_capability_call_attempt_count",
        ),
        CheckConstraint(
            "("
            "  execution_mode <> 'local_transactional'"
            ") OR ("
            "  side_effect_started_at IS NULL"
            ") OR ("
            "  status = 'succeeded' AND side_effect_started_at IS NOT NULL"
            ")",
            name="ck_assistant_capability_call_local_effect_start",
        ),
        _sha256_check(
            "descriptor_digest",
            name="ck_assistant_capability_call_descriptor_digest",
        ),
        _sha256_check(
            "authorization_digest",
            name="ck_assistant_capability_call_authorization_digest",
        ),
        _nullable_sha256_check(
            "approval_binding_digest",
            name="ck_assistant_capability_call_approval_binding_digest",
        ),
        _sha256_check(
            "input_digest",
            name="ck_assistant_capability_call_input_digest",
        ),
        Index(
            "uq_assistant_capability_call_run_logical_key",
            "run_id",
            "logical_call_key",
            unique=True,
        ),
        Index(
            "uq_assistant_capability_call_run_provider_tool_call",
            "run_id",
            "provider_tool_call_id",
            unique=True,
            postgresql_where=text("provider_tool_call_id IS NOT NULL"),
            sqlite_where=text("provider_tool_call_id IS NOT NULL"),
        ),
        Index(
            "ix_assistant_capability_call_run_status",
            "run_id",
            "status",
        ),
        Index(
            "ix_assistant_capability_call_idempotency_key",
            "idempotency_key",
        ),
    )


class AssistantCapabilityCallAttempt(UuidPrimaryKeyMixin, Base):
    """Append-only attempt history for one CapabilityCall."""

    __tablename__ = "assistant_capability_call_attempt"

    call_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_capability_call.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attempt_number = Column(Integer, nullable=False)
    worker_id = Column(String(160), nullable=False)
    lease_generation = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    dispatch_deadline_at = Column(DateTime(timezone=True), nullable=True)
    external_request_id = Column(String(255), nullable=True)
    external_idempotency_echo = Column(String(255), nullable=True)
    request_digest = Column(String(64), nullable=True)
    response_digest = Column(String(64), nullable=True)
    transport_status = Column(String(64), nullable=True)
    side_effect_started = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    side_effect_started_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(64), nullable=True)
    retry_classification = Column(String(32), nullable=True)
    diagnostic_artifact_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_run_artifact.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "attempt_number > 0",
            name="ck_assistant_capability_call_attempt_number_positive",
        ),
        CheckConstraint(
            "lease_generation >= 0",
            name="ck_assistant_capability_call_attempt_lease_generation",
        ),
        CheckConstraint(
            "status IN ("
            "'claimed','dispatched','response_received','committed',"
            "'failed','uncertain','abandoned'"
            ")",
            name="ck_assistant_capability_call_attempt_status",
        ),
        _nullable_sha256_check(
            "request_digest",
            name="ck_assistant_capability_call_attempt_request_digest",
        ),
        _nullable_sha256_check(
            "response_digest",
            name="ck_assistant_capability_call_attempt_response_digest",
        ),
        Index(
            "uq_assistant_capability_call_attempt_number",
            "call_id",
            "attempt_number",
            unique=True,
        ),
    )


class AssistantCapabilityReconciliation(UuidPrimaryKeyMixin, Base):
    """Append-only operator reconciliation decision for a CapabilityCall."""

    __tablename__ = "assistant_capability_reconciliation"

    call_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_capability_call.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    revision = Column(Integer, nullable=False)
    decision = Column(String(32), nullable=False)
    actor_user_id = Column(UUID(as_uuid=True), nullable=True)
    actor_admin_id = Column(UUID(as_uuid=True), nullable=True)
    authorization_evidence = Column(JSON, nullable=False, default=dict)
    reason = Column(Text, nullable=False)
    evidence_artifact_ids = Column(JSON, nullable=False, default=list)
    expected_call_revision = Column(Integer, nullable=False)
    expected_run_revision = Column(Integer, nullable=False)
    resulting_call_revision = Column(Integer, nullable=True)
    resulting_run_revision = Column(Integer, nullable=True)
    resolution_request_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "revision > 0",
            name="ck_assistant_capability_reconciliation_revision_positive",
        ),
        CheckConstraint(
            "decision IN ("
            "'mark_succeeded','mark_failed','mark_compensated','retry_same_key'"
            ")",
            name="ck_assistant_capability_reconciliation_decision",
        ),
        CheckConstraint(
            "expected_call_revision >= 0",
            name="ck_assistant_capability_reconciliation_expected_call_revision",
        ),
        CheckConstraint(
            "expected_run_revision >= 0",
            name="ck_assistant_capability_reconciliation_expected_run_revision",
        ),
        Index(
            "uq_assistant_capability_reconciliation_call_revision",
            "call_id",
            "revision",
            unique=True,
        ),
        Index(
            "uq_assistant_capability_reconciliation_resolution_request",
            "run_id",
            "resolution_request_id",
            unique=True,
        ),
    )

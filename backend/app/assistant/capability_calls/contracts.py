"""Frozen contracts and status/mode literals for the CapabilityCall ledger.

Task 1 ships the status/mode vocabulary and minimal identity contracts needed
by ORM models and migrations. Full dispatcher/approval contracts land in
Tasks 2–5.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from app.assistant.capabilities.contracts import SideEffectClass
from app.assistant.domain.contracts import FrozenContract

# ---------------------------------------------------------------------------
# Status / mode vocabularies (must match DB CheckConstraints)
# ---------------------------------------------------------------------------

CapabilityLedgerMode = Literal["legacy_read_only", "enforced"]
CAPABILITY_LEDGER_MODES: tuple[CapabilityLedgerMode, ...] = (
    "legacy_read_only",
    "enforced",
)

CapabilityCallStatus = Literal[
    "proposed",
    "denied",
    "awaiting_approval",
    "authorized",
    "rejected",
    "cancelled",
    "expired",
    "executing",
    "succeeded",
    "failed",
    "unknown",
    "needs_reconciliation",
    "compensated",
]
CAPABILITY_CALL_STATUSES: tuple[CapabilityCallStatus, ...] = (
    "proposed",
    "denied",
    "awaiting_approval",
    "authorized",
    "rejected",
    "cancelled",
    "expired",
    "executing",
    "succeeded",
    "failed",
    "unknown",
    "needs_reconciliation",
    "compensated",
)
CAPABILITY_CALL_TERMINAL_STATUSES: frozenset[CapabilityCallStatus] = frozenset(
    {
        "denied",
        "rejected",
        "cancelled",
        "expired",
        "succeeded",
        "failed",
        "compensated",
    }
)

CallAttemptStatus = Literal[
    "claimed",
    "dispatched",
    "response_received",
    "committed",
    "failed",
    "uncertain",
    "abandoned",
]
CALL_ATTEMPT_STATUSES: tuple[CallAttemptStatus, ...] = (
    "claimed",
    "dispatched",
    "response_received",
    "committed",
    "failed",
    "uncertain",
    "abandoned",
)

CapabilityExecutionMode = Literal[
    "pure_replayable",
    "read_replayable",
    "local_transactional",
    "external_idempotent",
    "external_reconcilable",
    "non_retriable",
    "unsupported",
]
CAPABILITY_EXECUTION_MODES: tuple[CapabilityExecutionMode, ...] = (
    "pure_replayable",
    "read_replayable",
    "local_transactional",
    "external_idempotent",
    "external_reconcilable",
    "non_retriable",
    "unsupported",
)

InterruptOrigin = Literal["workflow_node", "capability_call"]
INTERRUPT_ORIGINS: tuple[InterruptOrigin, ...] = (
    "workflow_node",
    "capability_call",
)

ReconciliationDecision = Literal[
    "mark_succeeded",
    "mark_failed",
    "mark_compensated",
    "retry_same_key",
]
RECONCILIATION_DECISIONS: tuple[ReconciliationDecision, ...] = (
    "mark_succeeded",
    "mark_failed",
    "mark_compensated",
    "retry_same_key",
)

CapabilityOwnerKind = Literal["main_agent", "skill_version", "capability_call"]
CAPABILITY_OWNER_KINDS: tuple[CapabilityOwnerKind, ...] = (
    "main_agent",
    "skill_version",
    "capability_call",
)


class CapabilityCallIdentity(FrozenContract):
    """Logical call identity persisted on the ledger row."""

    run_id: UUID
    logical_call_key: str
    parent_call_id: UUID | None
    manifest_revision_id: UUID
    owner_kind: CapabilityOwnerKind
    owner_id: UUID | None
    owner_version_id: UUID | None
    capability_type: str
    domain_key: str
    target_id: UUID | None
    target_version_id: UUID | None
    descriptor_digest: str
    input_digest: str
    side_effect_class: SideEffectClass
    execution_mode: CapabilityExecutionMode



class CapabilityCallApprovalBindingV1(FrozenContract):
    """Exact call-owned approval binding tuple (Plan 08 Task 5).

    Approval binds this digest; any changed field invalidates the pending call.
    Approval never rewrites authorization_digest or grant digests.
    """

    contract_version: Literal[1] = 1
    call_id: UUID
    logical_call_key: str
    owner_digest: str
    binding_contract_digest: str
    input_digest: str
    target_version_id: UUID | None
    target_digest: str
    descriptor_digest: str
    authorization_digest: str
    principal_digest: str
    request_revision: int
    approval_binding_digest: str


class CapabilityCallPauseProposalV1(FrozenContract):
    """Staged pause proposal returned by LedgerDispatcher (not durable alone)."""

    contract_version: Literal[1] = 1
    run_id: UUID
    call_id: UUID
    interrupt_id: UUID
    approval_binding: CapabilityCallApprovalBindingV1
    safe_request_payload: dict[str, object]
    proposal_digest: str


class SafeApprovalCardV1(FrozenContract):
    """Server-rendered bounded approval card (secrets already redacted)."""

    contract_version: Literal[1] = 1
    action_label: str
    object_type: str
    side_effect_class: str
    is_external: bool
    owner_label: str
    target_label: str
    field_summaries: tuple[str, ...]
    retryable: bool
    reconcilable: bool



__all__ = [
    "CAPABILITY_CALL_STATUSES",
    "CAPABILITY_CALL_TERMINAL_STATUSES",
    "CAPABILITY_EXECUTION_MODES",
    "CAPABILITY_LEDGER_MODES",
    "CAPABILITY_OWNER_KINDS",
    "CALL_ATTEMPT_STATUSES",
    "INTERRUPT_ORIGINS",
    "RECONCILIATION_DECISIONS",
    "CapabilityCallIdentity",
    "SafeApprovalCardV1",
    "CapabilityCallPauseProposalV1",
    "CapabilityCallApprovalBindingV1",
    "CapabilityCallStatus",
    "CapabilityExecutionMode",
    "CapabilityLedgerMode",
    "CapabilityOwnerKind",
    "CallAttemptStatus",
    "InterruptOrigin",
    "ReconciliationDecision",
]

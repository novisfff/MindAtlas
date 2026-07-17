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
    "CapabilityCallStatus",
    "CapabilityExecutionMode",
    "CapabilityLedgerMode",
    "CapabilityOwnerKind",
    "CallAttemptStatus",
    "InterruptOrigin",
    "ReconciliationDecision",
]

"""Plan 08 CapabilityCall ledger package.

Storage models, frozen contracts, identity/idempotency factories, state machine,
CAS repository, and call-aware settlement. Dispatcher lands in later tasks.
"""

from app.assistant.capability_calls.contracts import (  # noqa: F401
    CAPABILITY_CALL_STATUSES,
    CAPABILITY_CALL_TERMINAL_STATUSES,
    CAPABILITY_EXECUTION_MODES,
    CAPABILITY_LEDGER_MODES,
    CALL_ATTEMPT_STATUSES,
    INTERRUPT_ORIGINS,
    RECONCILIATION_DECISIONS,
    CapabilityCallStatus,
    CapabilityExecutionMode,
    CapabilityLedgerMode,
    CallAttemptStatus,
    InterruptOrigin,
    ReconciliationDecision,
)
from app.assistant.capability_calls.idempotency import (  # noqa: F401
    digest_input_payload,
    idempotency_key_fingerprint,
    make_nested_agent_logical_call_key,
    make_provider_logical_call_key,
    make_server_idempotency_key,
    make_workflow_logical_call_key,
    require_idempotency_secret,
)
from app.assistant.capability_calls.models import (  # noqa: F401
    AssistantCapabilityCall,
    AssistantCapabilityCallAttempt,
    AssistantCapabilityReconciliation,
)
from app.assistant.capability_calls.repository import (  # noqa: F401
    CapabilityCallConflict,
    CapabilityCallRepository,
    ProposeCallSpec,
)
from app.assistant.capability_calls.settlement import (  # noqa: F401
    CapabilityCallSettlementRepository,
    SettlementRequest,
)
from app.assistant.capability_calls.state_machine import (  # noqa: F401
    ALLOWED_CALL_TRANSITIONS,
    PLAN08_RUN_TRANSITION_DELTA,
    CallTransitionError,
    validate_call_transition,
)

__all__ = [
    "ALLOWED_CALL_TRANSITIONS",
    "AssistantCapabilityCall",
    "AssistantCapabilityCallAttempt",
    "AssistantCapabilityReconciliation",
    "CAPABILITY_CALL_STATUSES",
    "CAPABILITY_CALL_TERMINAL_STATUSES",
    "CAPABILITY_EXECUTION_MODES",
    "CAPABILITY_LEDGER_MODES",
    "CALL_ATTEMPT_STATUSES",
    "CapabilityCallConflict",
    "CapabilityCallRepository",
    "CapabilityCallSettlementRepository",
    "CapabilityCallStatus",
    "CapabilityExecutionMode",
    "CapabilityLedgerMode",
    "CallAttemptStatus",
    "CallTransitionError",
    "INTERRUPT_ORIGINS",
    "PLAN08_RUN_TRANSITION_DELTA",
    "ProposeCallSpec",
    "RECONCILIATION_DECISIONS",
    "SettlementRequest",
    "digest_input_payload",
    "idempotency_key_fingerprint",
    "make_nested_agent_logical_call_key",
    "make_provider_logical_call_key",
    "make_server_idempotency_key",
    "make_workflow_logical_call_key",
    "require_idempotency_secret",
    "validate_call_transition",
    "InterruptOrigin",
    "ReconciliationDecision",
]

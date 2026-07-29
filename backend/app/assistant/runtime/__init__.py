"""Main-Agent runtime package (Plan 2).

Task 2 exports durable contracts, ORM models, and the repository. Seed,
bootstrap, closure builder, readiness, activation, and admission land later.
"""

from app.assistant.runtime.contracts import (
    ASSISTANT_ROLLOUT_NAMESPACE,
    RUNTIME_READINESS_REASON_CODES,
    ActivatedRolloutResult,
    ActivateRolloutRequest,
    AssistantReadinessSnapshot,
    AssistantRuntimeClosure,
    AssistantRuntimeSubject,
    NewChatAdmission,
    NewRolloutEvent,
    PreparedRolloutResult,
    PreparedRolloutRevision,
    PrepareRolloutRequest,
    RuntimeControlConflict,
    RuntimeControlResult,
    RuntimeRequestReuseConflict,
    SetNewRunsEnabledRequest,
    require_sha256,
    rollout_revision_id_for_request,
)
from app.assistant.runtime.models import (
    AssistantMainAgentRolloutControl,
    AssistantMainAgentRolloutEvent,
    AssistantMainAgentRolloutRevision,
)
from app.assistant.runtime.repository import AssistantRuntimeRepository

__all__ = (
    "ASSISTANT_ROLLOUT_NAMESPACE",
    "RUNTIME_READINESS_REASON_CODES",
    "ActivatedRolloutResult",
    "ActivateRolloutRequest",
    "AssistantMainAgentRolloutControl",
    "AssistantMainAgentRolloutEvent",
    "AssistantMainAgentRolloutRevision",
    "AssistantReadinessSnapshot",
    "AssistantRuntimeClosure",
    "AssistantRuntimeRepository",
    "AssistantRuntimeSubject",
    "NewChatAdmission",
    "NewRolloutEvent",
    "PreparedRolloutResult",
    "PreparedRolloutRevision",
    "PrepareRolloutRequest",
    "RuntimeControlConflict",
    "RuntimeControlResult",
    "RuntimeRequestReuseConflict",
    "SetNewRunsEnabledRequest",
    "require_sha256",
    "rollout_revision_id_for_request",
)

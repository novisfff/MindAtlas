"""Main-Agent runtime package (Plan 2).

Exports durable contracts, ORM models, repository, seed loader, canonical
closure builder, and shared readiness evaluator.
"""

from app.assistant.runtime.closure import (
    AssistantRuntimeClosureBuilder,
    BoundAssistantModelIdentity,
    ModelIdentityUnavailable,
    RuntimeClosureDrift,
    resolve_bound_assistant_model_identity,
    run_optional_assistant_model_probe,
)
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
from app.assistant.runtime.readiness import (
    AssistantReadinessService,
    Plan2AlembicHeadCompatibility,
    project_authenticated_readiness,
    project_public_readiness,
    read_single_alembic_version,
)
from app.assistant.runtime.repository import AssistantRuntimeRepository
from app.assistant.runtime.seed import (
    SEED_CONTRACT_DIGEST,
    SEED_MANIFEST_DIGEST,
    AssistantSystemSeedManifest,
    SeedArtifact,
    SeedBuildCompatibility,
    SeedCapabilityBinding,
    SystemSeedInvalid,
    VerifiedAssistantSystemSeed,
    load_verified_assistant_system_seed,
)

__all__ = (
    "ASSISTANT_ROLLOUT_NAMESPACE",
    "RUNTIME_READINESS_REASON_CODES",
    "ActivatedRolloutResult",
    "ActivateRolloutRequest",
    "AssistantMainAgentRolloutControl",
    "AssistantMainAgentRolloutEvent",
    "AssistantMainAgentRolloutRevision",
    "AssistantReadinessService",
    "AssistantReadinessSnapshot",
    "AssistantRuntimeClosure",
    "AssistantRuntimeClosureBuilder",
    "AssistantRuntimeRepository",
    "AssistantRuntimeSubject",
    "AssistantSystemSeedManifest",
    "BoundAssistantModelIdentity",
    "ModelIdentityUnavailable",
    "NewChatAdmission",
    "NewRolloutEvent",
    "Plan2AlembicHeadCompatibility",
    "PreparedRolloutResult",
    "PreparedRolloutRevision",
    "PrepareRolloutRequest",
    "RuntimeClosureDrift",
    "RuntimeControlConflict",
    "RuntimeControlResult",
    "RuntimeRequestReuseConflict",
    "SEED_CONTRACT_DIGEST",
    "SEED_MANIFEST_DIGEST",
    "SeedArtifact",
    "SeedBuildCompatibility",
    "SeedCapabilityBinding",
    "SetNewRunsEnabledRequest",
    "SystemSeedInvalid",
    "VerifiedAssistantSystemSeed",
    "load_verified_assistant_system_seed",
    "project_authenticated_readiness",
    "project_public_readiness",
    "read_single_alembic_version",
    "require_sha256",
    "resolve_bound_assistant_model_identity",
    "rollout_revision_id_for_request",
    "run_optional_assistant_model_probe",
)

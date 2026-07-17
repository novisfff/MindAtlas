"""Plan 06 durable agent run package.

Persistence models, CAS repository, codec, artifacts, leases, recovery,
worker registration, terminal memory finalizer, and Main Agent execution
at Checkpoint boundaries.
"""

from app.assistant.durable.models import (  # noqa: F401
    AssistantRunArtifact,
    AssistantRunArtifactGc,
    AssistantRunBudgetRevision,
    AssistantRunCheckpoint,
    AssistantRunInterrupt,
    AssistantRunManifestRevision,
    AssistantRunObligationRevision,
    AssistantRunPolicyRevision,
    AssistantRunProviderMessage,
    AssistantWorkerRegistration,
)
# Plan 08 ledger models (register with Base.metadata for create_all / alembic).
from app.assistant.capability_calls.models import (  # noqa: F401
    AssistantCapabilityCall,
    AssistantCapabilityCallAttempt,
    AssistantCapabilityReconciliation,
)
from app.assistant.durable.repository import (  # noqa: F401
    ALLOWED_TRANSITIONS,
    CODE_EVENT_KEY_CONFLICT,
    CODE_EVENT_KEY_REQUIRED,
    CODE_INVALID_SOURCE_STATUS,
    CODE_LEASE_MISMATCH,
    CODE_RUN_FINALIZING,
    CODE_STALE_REVISION,
    CODE_TERMINAL_IMMUTABLE,
    DurableChildBundle,
    DurableCommitResult,
    DurableRunConflict,
    DurableRunRepository,
    EventSpec,
    LeaseToken,
)
from app.assistant.durable.artifacts import (  # noqa: F401
    ArtifactStorageError,
    DurableArtifactService,
    enqueue_conversation_artifact_gc,
)
from app.assistant.durable.worker_registry import (  # noqa: F401
    RUNTIME_CONTRACT_VERSION,
    WorkerCompatibility,
    WorkerIdentity,
    WorkerRegistry,
    default_capability_feature_digest,
    generate_worker_id,
)
from app.assistant.durable.leases import (  # noqa: F401
    ClaimedLease,
    RunLeaseService,
    compute_retry_backoff,
)
from app.assistant.durable.recovery import (  # noqa: F401
    CredentialSnapshot,
    RecoveryClassifier,
    RecoveryDecision,
)
from app.assistant.durable.runner import (  # noqa: F401
    MainAgentRunExecutor,
    assert_no_legacy_fallback,
)
from app.assistant.durable.admission import admit_and_select_runtime  # noqa: F401
from app.assistant.durable.materialize import materialize_base_run_state  # noqa: F401
from app.assistant.durable.checkpoints import (  # noqa: F401
    commit_prepared_unit,
    commit_started_unit,
    commit_unit_result,
    find_post_result_for_unit,
    note_capability_adapter_result,
    resolve_retry_unit,
)
from app.assistant.durable.activation import DurableSkillActivationLifecycle  # noqa: F401
from app.assistant.durable.reconstruction import (  # noqa: F401
    issue_fresh_authorization_evidence,
    load_current_checkpoint,
    reconstruct_capability_frames,
    reconstruct_provider_transcript,
    validate_resume_transcript,
)
from app.assistant.durable.memory import (  # noqa: F401
    DurableMemoryError,
    DurableMemoryFinalizer,
    PreparedL1Update,
    PreparedL2Update,
    PreparedMemorySet,
    digest_final_content,
    normalize_facts_v2,
)
from app.assistant.durable.crash import (  # noqa: F401
    CrashInjector,
    CrashPoint,
    TransactionRollbackInject,
    WorkerCrash,
    armed_crash,
    maybe_crash,
)

__all__ = [
    "AssistantWorkerRegistration",
    "AssistantRunManifestRevision",
    "AssistantRunPolicyRevision",
    "AssistantRunBudgetRevision",
    "AssistantRunObligationRevision",
    "AssistantRunProviderMessage",
    "AssistantRunCheckpoint",
    "AssistantRunArtifact",
    "AssistantRunArtifactGc",
    "ALLOWED_TRANSITIONS",
    "CODE_EVENT_KEY_CONFLICT",
    "CODE_EVENT_KEY_REQUIRED",
    "CODE_INVALID_SOURCE_STATUS",
    "CODE_LEASE_MISMATCH",
    "CODE_RUN_FINALIZING",
    "CODE_STALE_REVISION",
    "CODE_TERMINAL_IMMUTABLE",
    "DurableChildBundle",
    "DurableCommitResult",
    "DurableRunConflict",
    "DurableRunRepository",
    "EventSpec",
    "LeaseToken",
    "ArtifactStorageError",
    "DurableArtifactService",
    "enqueue_conversation_artifact_gc",
    "RUNTIME_CONTRACT_VERSION",
    "WorkerCompatibility",
    "WorkerIdentity",
    "WorkerRegistry",
    "default_capability_feature_digest",
    "generate_worker_id",
    "ClaimedLease",
    "RunLeaseService",
    "compute_retry_backoff",
    "CredentialSnapshot",
    "RecoveryClassifier",
    "RecoveryDecision",
    "MainAgentRunExecutor",
    "assert_no_legacy_fallback",
    "admit_and_select_runtime",
    "materialize_base_run_state",
    "commit_prepared_unit",
    "commit_started_unit",
    "commit_unit_result",
    "find_post_result_for_unit",
    "resolve_retry_unit",
    "DurableSkillActivationLifecycle",
    "issue_fresh_authorization_evidence",
    "load_current_checkpoint",
    "reconstruct_capability_frames",
    "reconstruct_provider_transcript",
    "validate_resume_transcript",
    "DurableMemoryError",
    "DurableMemoryFinalizer",
    "PreparedL1Update",
    "PreparedL2Update",
    "PreparedMemorySet",
    "digest_final_content",
    "normalize_facts_v2",
    "CrashInjector",
    "CrashPoint",
    "TransactionRollbackInject",
    "WorkerCrash",
    "armed_crash",
    "maybe_crash",
    "note_capability_adapter_result",
]

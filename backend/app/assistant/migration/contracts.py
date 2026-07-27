"""Frozen contracts for Plan 10 runtime migration inventory tooling."""

from __future__ import annotations

from typing import Any, Literal

from app.assistant.domain.contracts import FrozenContract

MigrationSubjectKind = Literal[
    "skill",
    "profile",
    "alias",
    "l2_memory",
    "approval",
    "entrypoint",
    "package",
    "write_branch",
]

MigrationItemState = Literal[
    "discovered",
    "mapped",
    "migrated",
    "verified",
    "blocked",
    "archived",
]

L2NamespaceClass = Literal[
    "legacy_null_package",
    "native_default_namespace",
    "native_custom_namespace",
    "invalid_shape",
]

OwnerClass = Literal[
    "legacy",
    "native_runtime",
    "migration_tooling",
    "shared_capability",
    "dynamic_composition",
    "frontend_legacy",
    "unknown",
]

MigrationStage = Literal["shadow", "read", "write", "cleanup"]

MetricConfidenceModel = Literal["wilson", "bootstrap", "exact_zero", "none"]

CLI_EXIT_COMPLETED = 0
CLI_EXIT_COMPLETED_WITH_BLOCKERS = 2
CLI_EXIT_PRECONDITION_FAILED = 3
CLI_EXIT_CONFLICT_OR_DRIFT = 4
CLI_EXIT_UNEXPECTED_FAILURE = 5


class InventoryItem(FrozenContract):
    """One discovered migration subject with only safe identity fields."""

    schema_version: Literal[1] = 1
    subject_kind: MigrationSubjectKind
    source_id: str
    source_name: str
    source_name_normalized: str
    source_digest: str
    state: MigrationItemState = "discovered"
    reason_code: str | None = None
    enabled: bool | None = None
    is_system: bool | None = None
    target_type: str | None = None
    target_id: str | None = None
    target_version_id: str | None = None
    target_digest: str | None = None
    # L2-only classification (absent/None for other kinds).
    skill_package_id: str | None = None
    memory_namespace: str | None = None
    namespace_class: L2NamespaceClass | None = None
    # Approval/entrypoint summary fields (never raw payloads).
    status: str | None = None
    channel_type: str | None = None
    runtime: str | None = None
    supports_hitl: bool | None = None
    branch: str | None = None
    plan08_evidence: bool | None = None


class InventorySnapshot(FrozenContract):
    """Immutable inventory scan result (safe digests/IDs/counts only)."""

    schema_version: Literal[1] = 1
    environment: str
    database_fingerprint: str
    schema_head: str
    build_revision: str
    items: tuple[InventoryItem, ...]
    counts: dict[str, int]
    blocker_count: int
    snapshot_digest: str
    known_system_skills: tuple[str, ...] = ()
    scanned_at_utc: str | None = None


class SafeInventoryReport(FrozenContract):
    """CLI/operator-facing report: digests, IDs, counts, reason codes only."""

    schema_version: Literal[1] = 1
    ok: bool
    environment: str
    database_fingerprint: str
    schema_head: str
    build_revision: str
    snapshot_digest: str
    counts: dict[str, int]
    blocker_count: int
    items: tuple[dict[str, Any], ...]
    blockers: tuple[dict[str, Any], ...]
    dry_run: bool = True
    request_id: str | None = None


class MetricDefinition(FrozenContract):
    """Locked metric dictionary entry (definitions only; no live collectors)."""

    schema_version: Literal[1] = 1
    metric_id: str
    display_name: str
    numerator: str
    denominator: str
    eligibility: str
    window: str
    confidence_model: MetricConfidenceModel
    gate_threshold: str | None = None
    stage_applicability: tuple[MigrationStage, ...] = ()
    notes: str | None = None


class UpstreamGateEntry(FrozenContract):
    """Inherited Plan 06/07/08/09 ship-gate vector with stage applicability."""

    schema_version: Literal[1] = 1
    plan: str
    gate_id: str
    description: str
    satisfied: bool
    blocks_stages: tuple[MigrationStage, ...]
    evidence_ref: str | None = None
    reason_code: str | None = None
    local_tooling_allowed: bool = True


class ProductionCutoverStatus(FrozenContract):
    schema_version: Literal[1] = 1
    blocked: bool
    local_tooling_allowed: bool
    reason_codes: tuple[str, ...]


class ModuleOwnershipRow(FrozenContract):
    schema_version: Literal[1] = 1
    module_path: str
    owner_class: OwnerClass
    subject_area: str
    notes: str | None = None


class SnapshotComparison(FrozenContract):
    schema_version: Literal[1] = 1
    equal: bool
    left_digest: str
    right_digest: str
    added_count: int
    removed_count: int
    changed_count: int


class MigrationBatchResultShape(FrozenContract):
    """Forward-compatible batch result shape (Task 0 stub; Task 1 persists)."""

    schema_version: Literal[1] = 1
    command_kind: str
    status: Literal["prepared", "running", "completed", "failed", "cancelled"]
    source_snapshot_digest: str
    configuration_digest: str
    build_revision: str
    schema_head: str
    environment: str
    database_fingerprint: str
    processed_count: int = 0
    succeeded_count: int = 0
    blocked_count: int = 0
    failed_count: int = 0
    report_digest: str | None = None


MigrationBatchStatus = Literal[
    "prepared", "running", "completed", "failed", "cancelled"
]

RolloutAssignmentReason = Literal["hash", "staff", "explicit_override", "rollback"]

RuntimeKind = Literal["legacy", "main_agent"]

EvalRunPurpose = Literal["admin_evaluation", "runtime_shadow"]


class MigrationBatchResult(FrozenContract):
    """Typed batch outcome returned by prepare/apply/resume services."""

    schema_version: Literal[1] = 1
    batch_id: str
    status: MigrationBatchStatus
    processed: int = 0
    succeeded: int = 0
    blocked: int = 0
    failed: int = 0
    next_cursor: str | None = None
    report_artifact_id: str | None = None
    report_digest: str | None = None
    request_id: str | None = None
    dry_run: bool = True


class RuntimeShadowInputSnapshot(FrozenContract):
    """Private evaluation-namespace snapshot identity (no raw content)."""

    schema_version: Literal[1] = 1
    snapshot_id: str
    source_production_run_id: str
    source_user_message_id: str | None = None
    principal_scope_digest: str
    message_prefix_digest: str
    authorized_context_digest: str
    snapshot_policy_digest: str
    private_eval_artifact_id: str | None = None
    payload_digest: str
    expires_at: str | None = None


class RolloutDecision(FrozenContract):
    """Admission decision evidence (Task 1 schema only; routing later)."""

    schema_version: Literal[1] = 1
    rollout_revision_id: str
    assignment_id: str | None = None
    assigned_runtime_kind: RuntimeKind
    selected_runtime_kind: RuntimeKind
    write_mode: str = "off"
    bucket: int = 0
    assignment_reason: RolloutAssignmentReason = "hash"
    selection_reason: Literal["assigned", "preinsert_fallback"] = "assigned"
    fallback_event_id: str | None = None
    admission_failure_digest: str | None = None


class CleanupPreflightResult(FrozenContract):
    schema_version: Literal[1] = 1
    gate_id: str
    valid: bool
    blockers: tuple[dict[str, Any], ...] = ()
    evidence_digest: str

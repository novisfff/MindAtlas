"""Plan 07 durable Workflow/Agent execution contracts package.

Task 1: portable frozen contracts + codec.
Task 2: frozen DurableExecutionPlanV1 planner + durable publish helpers.
Task 3: one-boundary durable Workflow/Agent runner + node adapters.
Task 4: durable human Interrupt persistence (token/schema/repository).
Task 5: nonblocking durable pause (effect port + atomic waiting commit).
Task 6: conversation-scoped token/decision HTTP APIs + expiry scanner.
Task 7: exact resume of child frames + Provider waiting resolution.
Task 9: hidden durable-proposal-review golden path publish + recovery proof.

Heavy modules (adapters/runner/interrupts/pause/resume) are imported lazily via
``__getattr__`` so ``durable.codec`` Checkpoint v2 rebuild can import
``workflow.durable.contracts`` without circular imports.
"""

from __future__ import annotations

from typing import Any

from app.assistant.workflow.durable.contracts import (  # noqa: F401
    DURABLE_WORKFLOW_IDENTITY_NAMESPACE,
    BudgetSuspensionStateV1,
    DurableBranchDecisionV1,
    DurableCallFrameV1,
    DurableEdgeV1,
    DurableExecutionPlanV1,
    DurableLoopCursorV1,
    DurableNodePlanV1,
    DurablePauseEffectPort,
    DurablePauseProposalV1,
    DurableWorkflowStateV1,
    FrozenExecutionDependencyRef,
    build_root_continuation,
    compute_branch_decision_digest,
    compute_loop_cursor_digest,
    compute_plan_digest,
    compute_proposal_digest,
    compute_suspension_digest,
    derive_frame_id,
    derive_interrupt_id,
    derive_node_visit_id,
)
from app.assistant.workflow.durable.context import EphemeralWorkflowContext  # noqa: F401
from app.assistant.workflow.durable.planner import (  # noqa: F401
    DURABLE_PLAN_EXTENSION_CONTRACT_VERSION,
    DURABLE_PLAN_EXTENSION_KEY,
    DurablePlanError,
    attach_durable_plan_extension,
    business_side_effect_maximum,
    extract_durable_plan_digest,
    plan_allows_durable_interrupt,
    plan_durable_execution,
    plan_durable_execution_from_surface,
    plan_parallel_safe,
    publish_durable_binding_snapshot,
)

_LAZY: dict[str, tuple[str, str]] = {
    # adapters
    "AdapterBoundaryResult": (".adapters", "AdapterBoundaryResult"),
    "AdapterExecutionContext": (".adapters", "AdapterExecutionContext"),
    "DefaultDurableNodeAdapterRegistry": (".adapters", "DefaultDurableNodeAdapterRegistry"),
    "DurableAdapterError": (".adapters", "DurableAdapterError"),
    "PortableNodeBag": (".adapters", "PortableNodeBag"),
    "build_default_registry": (".adapters", "build_default_registry"),
    # runner
    "BoundaryKind": (".runner", "BoundaryKind"),
    "BoundaryResult": (".runner", "BoundaryResult"),
    "DurableFrameMaterial": (".runner", "DurableFrameMaterial"),
    "DurableWorkflowRunner": (".runner", "DurableWorkflowRunner"),
    "PreparedBoundary": (".runner", "PreparedBoundary"),
    "build_initial_workflow_state": (".runner", "build_initial_workflow_state"),
    "commit_workflow_boundary_prepare": (".runner", "commit_workflow_boundary_prepare"),
    "commit_workflow_boundary_result": (".runner", "commit_workflow_boundary_result"),
    # interrupts
    "DurableInterruptRepository": (".interrupts", "DurableInterruptRepository"),
    "InterruptConflict": (".interrupts", "InterruptConflict"),
    "build_budget_suspension_state": (".interrupts", "build_budget_suspension_state"),
    "compute_remaining_active_ms": (".interrupts", "compute_remaining_active_ms"),
    "derive_interrupt_key": (".interrupts", "derive_interrupt_key"),
    "derive_resume_budget_ledger": (".interrupts", "derive_resume_budget_ledger"),
    "digest_resume_token": (".interrupts", "digest_resume_token"),
    "generate_resume_token": (".interrupts", "generate_resume_token"),
    "normalize_interrupt_field_schema": (".interrupts", "normalize_interrupt_field_schema"),
    "verify_resume_token": (".interrupts", "verify_resume_token"),
    # pause
    "CODE_DURABLE_BLOCKING_RUNTIME_FORBIDDEN": (".pause", "CODE_DURABLE_BLOCKING_RUNTIME_FORBIDDEN"),
    "CODE_DURABLE_PAUSE_PROTOCOL_ERROR": (".pause", "CODE_DURABLE_PAUSE_PROTOCOL_ERROR"),
    "DurablePauseCommitResult": (".pause", "DurablePauseCommitResult"),
    "DurablePauseProtocolError": (".pause", "DurablePauseProtocolError"),
    "WorkerUnitPauseEffectPort": (".pause", "WorkerUnitPauseEffectPort"),
    "commit_durable_workflow_pause": (".pause", "commit_durable_workflow_pause"),
    "consume_and_commit_pause": (".pause", "consume_and_commit_pause"),
    # resume
    "DurableResumeError": (".resume", "DurableResumeError"),
    "DurableResumeNeedsReconciliation": (".resume", "DurableResumeNeedsReconciliation"),
    "HumanContinuationResult": (".resume", "HumanContinuationResult"),
    "ResumeUnitResult": (".resume", "ResumeUnitResult"),
    "apply_human_result_once": (".resume", "apply_human_result_once"),
    "build_provider_waiting_resolution": (".resume", "build_provider_waiting_resolution"),
    "continue_child_until_boundary": (".resume", "continue_child_until_boundary"),
    "execute_interrupt_resume": (".resume", "execute_interrupt_resume"),
    "load_resume_context": (".resume", "load_resume_context"),
    "validate_provider_waiting_resume": (".resume", "validate_provider_waiting_resume"),
    "verify_resolution_budget_lineage": (".resume", "verify_resolution_budget_lineage"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    from importlib import import_module

    mod = import_module(module_name, __name__)
    value = getattr(mod, attr)
    globals()[name] = value
    return value


__all__ = [
    "DURABLE_PLAN_EXTENSION_CONTRACT_VERSION",
    "DURABLE_PLAN_EXTENSION_KEY",
    "DURABLE_WORKFLOW_IDENTITY_NAMESPACE",
    "CODE_DURABLE_BLOCKING_RUNTIME_FORBIDDEN",
    "CODE_DURABLE_PAUSE_PROTOCOL_ERROR",
    "BudgetSuspensionStateV1",
    "DurableBranchDecisionV1",
    "DurableCallFrameV1",
    "DurableEdgeV1",
    "DurableExecutionPlanV1",
    "DurableInterruptRepository",
    "DurableLoopCursorV1",
    "DurableNodePlanV1",
    "DurablePauseCommitResult",
    "DurablePauseEffectPort",
    "DurablePauseProposalV1",
    "DurablePauseProtocolError",
    "DurablePlanError",
    "DurableWorkflowStateV1",
    "EphemeralWorkflowContext",
    "FrozenExecutionDependencyRef",
    "InterruptConflict",
    "WorkerUnitPauseEffectPort",
    "attach_durable_plan_extension",
    "build_budget_suspension_state",
    "build_root_continuation",
    "business_side_effect_maximum",
    "commit_durable_workflow_pause",
    "compute_branch_decision_digest",
    "compute_loop_cursor_digest",
    "compute_plan_digest",
    "compute_proposal_digest",
    "compute_remaining_active_ms",
    "compute_suspension_digest",
    "consume_and_commit_pause",
    "derive_frame_id",
    "derive_interrupt_id",
    "derive_interrupt_key",
    "derive_node_visit_id",
    "derive_resume_budget_ledger",
    "digest_resume_token",
    "extract_durable_plan_digest",
    "generate_resume_token",
    "normalize_interrupt_field_schema",
    "plan_allows_durable_interrupt",
    "plan_durable_execution",
    "plan_durable_execution_from_surface",
    "plan_parallel_safe",
    "publish_durable_binding_snapshot",
    "verify_resume_token",
    "AdapterBoundaryResult",
    "AdapterExecutionContext",
    "BoundaryKind",
    "BoundaryResult",
    "DefaultDurableNodeAdapterRegistry",
    "DurableAdapterError",
    "DurableFrameMaterial",
    "DurableWorkflowRunner",
    "PortableNodeBag",
    "PreparedBoundary",
    "build_default_registry",
    "build_initial_workflow_state",
    "commit_workflow_boundary_prepare",
    "commit_workflow_boundary_result",
    "DurableResumeError",
    "DurableResumeNeedsReconciliation",
    "HumanContinuationResult",
    "ResumeUnitResult",
    "apply_human_result_once",
    "build_provider_waiting_resolution",
    "continue_child_until_boundary",
    "execute_interrupt_resume",
    "load_resume_context",
    "validate_provider_waiting_resume",
    "verify_resolution_budget_lineage",
]

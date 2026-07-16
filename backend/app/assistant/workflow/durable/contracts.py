"""Plan 07 durable Workflow/Agent frozen contracts (Task 1).

Portable execution plan, call frames, workflow state, pause proposal, budget
suspension, and deterministic identity helpers. No runtime execution here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Sequence
from uuid import UUID, uuid5

from pydantic import Field, field_validator, model_validator

from app.assistant.capabilities.contracts import ContinuationRef, SideEffectClass
from app.assistant.domain.contracts import FrozenContract
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.provider_loop.contracts import ProviderLoopContinuation

# Pydantic cannot materialize recursive JsonValue; use dict[str, Any] on fields.
JsonObject = dict[str, Any]

# Fixed Plan 07 identity namespace (never regenerate; identities are durable).
DURABLE_WORKFLOW_IDENTITY_NAMESPACE = UUID("5ea93ecc-594a-51c1-9b8a-5354e11bcc02")

_DIGEST_LEN = 64

SUPPORTED_EXECUTION_PLAN_CONTRACT_VERSIONS: frozenset[int] = frozenset({1})
SUPPORTED_WORKFLOW_STATE_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})
SUPPORTED_PAUSE_PROPOSAL_CONTRACT_VERSIONS: frozenset[int] = frozenset({1})
SUPPORTED_BUDGET_SUSPENSION_CONTRACT_VERSIONS: frozenset[int] = frozenset({1})


def _require_non_empty_str(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_digest(value: Any, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _DIGEST_LEN
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase 64-character SHA-256 hex digest")
    return value


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int")
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return value


def _require_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be >= 1")
    return value


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(__import__("datetime").timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def derive_frame_id(
    *,
    root_invocation_digest: str,
    parent_path: str,
    target_version_id: UUID,
    invocation_call_id: str,
) -> UUID:
    """Deterministic UUIDv5 frame identity (retries do not mint a new frame)."""
    root_invocation_digest = _require_digest(
        root_invocation_digest, field_name="root_invocation_digest"
    )
    parent_path = _require_non_empty_str(parent_path, field_name="parent_path")
    invocation_call_id = _require_non_empty_str(
        invocation_call_id, field_name="invocation_call_id"
    )
    name = (
        f"frame|{root_invocation_digest}|{parent_path}|"
        f"{target_version_id}|{invocation_call_id}"
    )
    return uuid5(DURABLE_WORKFLOW_IDENTITY_NAMESPACE, name)


def derive_node_visit_id(
    *,
    frame_id: UUID,
    node_id: str,
    node_visit_ordinal: int,
) -> str:
    """Deterministic node visit identity; retries keep the same logical visit."""
    node_id = _require_non_empty_str(node_id, field_name="node_id")
    node_visit_ordinal = _require_non_negative_int(
        node_visit_ordinal, field_name="node_visit_ordinal"
    )
    name = f"visit|{frame_id}|{node_id}|{node_visit_ordinal}"
    return str(uuid5(DURABLE_WORKFLOW_IDENTITY_NAMESPACE, name))


def derive_interrupt_id(
    *,
    run_id: UUID,
    root_invocation_digest: str,
    frame_id: UUID,
    node_visit_id: str,
    logical_interrupt_ordinal: int,
) -> UUID:
    """Deterministic interrupt row identity for pause proposal / insert-or-read."""
    root_invocation_digest = _require_digest(
        root_invocation_digest, field_name="root_invocation_digest"
    )
    node_visit_id = _require_non_empty_str(node_visit_id, field_name="node_visit_id")
    logical_interrupt_ordinal = _require_positive_int(
        logical_interrupt_ordinal, field_name="logical_interrupt_ordinal"
    )
    name = (
        f"interrupt|{run_id}|{root_invocation_digest}|{frame_id}|"
        f"{node_visit_id}|{logical_interrupt_ordinal}"
    )
    return uuid5(DURABLE_WORKFLOW_IDENTITY_NAMESPACE, name)


def build_root_continuation(
    *,
    root_frame_id: UUID,
    root_invocation_digest: str,
) -> ContinuationRef:
    """Stable outer ContinuationRef across multiple human pauses on one root call."""
    root_invocation_digest = _require_digest(
        root_invocation_digest, field_name="root_invocation_digest"
    )
    return ContinuationRef(
        continuation_type="durable_capability_invocation",
        contract_version=1,
        reference_id=str(root_frame_id),
        payload_digest=root_invocation_digest,
    )


# ---------------------------------------------------------------------------
# Supporting plan / branch / loop shapes
# ---------------------------------------------------------------------------


class DurableEdgeV1(FrozenContract):
    edge_id: str
    source_node_id: str
    target_node_id: str
    source_handle: str | None = None
    target_handle: str | None = None

    @field_validator("edge_id", "source_node_id", "target_node_id")
    @classmethod
    def _ids(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)


class FrozenExecutionDependencyRef(FrozenContract):
    """Minimal portable dependency identity for a planned node."""

    dependency_path: str
    dependency_type: Literal["system_tool", "remote_tool", "workflow", "agent", "model"]
    target_identity: str
    target_version_id: UUID | None = None
    resolution_digest: str
    dependency_digest: str

    @field_validator("dependency_path", "target_identity")
    @classmethod
    def _strs(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("resolution_digest", "dependency_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)


class DurableBranchDecisionV1(FrozenContract):
    node_id: str
    node_visit_id: str
    chosen_handle: str
    chosen_target_node_id: str
    decision_digest: str

    @field_validator("node_id", "node_visit_id", "chosen_handle", "chosen_target_node_id")
    @classmethod
    def _strs(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("decision_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="decision_digest")

    @model_validator(mode="after")
    def _check_digest(self) -> DurableBranchDecisionV1:
        expected = compute_branch_decision_digest(
            node_id=self.node_id,
            node_visit_id=self.node_visit_id,
            chosen_handle=self.chosen_handle,
            chosen_target_node_id=self.chosen_target_node_id,
        )
        if self.decision_digest != expected:
            raise ValueError("decision_digest does not match canonical branch decision payload")
        return self


class DurableLoopCursorV1(FrozenContract):
    loop_node_id: str
    node_visit_id: str
    iteration_index: int
    item_key: str | None = None
    completed_child_output_artifact_ids: tuple[UUID, ...] = ()
    cursor_digest: str

    @field_validator("loop_node_id", "node_visit_id")
    @classmethod
    def _strs(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("iteration_index")
    @classmethod
    def _iter(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="iteration_index")

    @field_validator("completed_child_output_artifact_ids", mode="before")
    @classmethod
    def _arts(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise TypeError("completed_child_output_artifact_ids must be a sequence")
        return tuple(value)

    @field_validator("cursor_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="cursor_digest")

    @model_validator(mode="after")
    def _check_digest(self) -> DurableLoopCursorV1:
        expected = compute_loop_cursor_digest(
            loop_node_id=self.loop_node_id,
            node_visit_id=self.node_visit_id,
            iteration_index=self.iteration_index,
            item_key=self.item_key,
            completed_child_output_artifact_ids=self.completed_child_output_artifact_ids,
        )
        if self.cursor_digest != expected:
            raise ValueError("cursor_digest does not match canonical loop cursor payload")
        return self


class DurableNodePlanV1(FrozenContract):
    node_id: str
    node_type: str
    config_digest: str
    outgoing_edges: tuple[DurableEdgeV1, ...]
    adapter_key: str
    business_side_effect: SideEffectClass
    may_interrupt: bool
    dependency_refs: tuple[FrozenExecutionDependencyRef, ...] = ()

    @field_validator("node_id", "node_type", "adapter_key")
    @classmethod
    def _strs(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("config_digest")
    @classmethod
    def _config(cls, value: str) -> str:
        return _require_digest(value, field_name="config_digest")

    @field_validator("outgoing_edges", "dependency_refs", mode="before")
    @classmethod
    def _seq(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise TypeError("sequence fields must be sequences")
        return tuple(value)


class DurableExecutionPlanV1(FrozenContract):
    contract_version: Literal[1] = 1
    target_kind: Literal["workflow", "agent"]
    target_version_id: UUID
    target_digest: str
    entry_node_id: str
    nodes: tuple[DurableNodePlanV1, ...]
    plan_digest: str

    @field_validator("entry_node_id")
    @classmethod
    def _entry(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="entry_node_id")

    @field_validator("target_digest", "plan_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("nodes", mode="before")
    @classmethod
    def _nodes(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise TypeError("nodes must be a sequence")
        return tuple(value)

    @model_validator(mode="after")
    def _check_digest(self) -> DurableExecutionPlanV1:
        expected = compute_plan_digest(
            target_kind=self.target_kind,
            target_version_id=self.target_version_id,
            target_digest=self.target_digest,
            entry_node_id=self.entry_node_id,
            nodes=self.nodes,
        )
        if self.plan_digest != expected:
            raise ValueError("plan_digest does not match canonical execution plan payload")
        return self


# ---------------------------------------------------------------------------
# Portable Workflow / Agent state
# ---------------------------------------------------------------------------


class DurableCallFrameV1(FrozenContract):
    frame_id: UUID
    parent_frame_id: UUID | None
    invocation_call_id: str
    owner_skill_package_id: UUID | None
    owner_skill_version_id: UUID | None
    target_kind: Literal["workflow", "agent"]
    target_id: UUID
    target_version_id: UUID
    target_digest: str
    execution_plan_digest: str
    current_node_id: str | None
    node_visit_id: str | None
    node_visit_ordinal: int
    execution_attempt: int
    phase: Literal[
        "ready",
        "executing",
        "waiting",
        "child_active",
        "completed",
        "failed",
        "cancelled",
    ]
    node_state_artifact_id: UUID | None = None
    node_output_artifact_ids: tuple[UUID, ...] = ()
    branch_decisions: tuple[DurableBranchDecisionV1, ...] = ()
    loop_cursors: tuple[DurableLoopCursorV1, ...] = ()
    child_frame_ids: tuple[UUID, ...] = ()
    agent_loop_continuation: ProviderLoopContinuation | None = None

    @field_validator("invocation_call_id")
    @classmethod
    def _call(cls, value: str) -> str:
        return _require_non_empty_str(value, field_name="invocation_call_id")

    @field_validator("target_digest", "execution_plan_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @field_validator("node_visit_ordinal")
    @classmethod
    def _ord(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="node_visit_ordinal")

    @field_validator("execution_attempt")
    @classmethod
    def _attempt(cls, value: int) -> int:
        return _require_positive_int(value, field_name="execution_attempt")

    @field_validator(
        "node_output_artifact_ids",
        "branch_decisions",
        "loop_cursors",
        "child_frame_ids",
        mode="before",
    )
    @classmethod
    def _seqs(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise TypeError("sequence fields must be sequences")
        return tuple(value)


class DurableWorkflowStateV1(FrozenContract):
    schema_version: Literal[1] = 1
    run_id: UUID
    root_frame_id: UUID
    root_invocation_digest: str
    frame_stack: tuple[DurableCallFrameV1, ...]
    pending_interrupt_id: UUID | None = None
    terminal_output_artifact_id: UUID | None = None

    @field_validator("root_invocation_digest")
    @classmethod
    def _root(cls, value: str) -> str:
        return _require_digest(value, field_name="root_invocation_digest")

    @field_validator("frame_stack", mode="before")
    @classmethod
    def _frames(cls, value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            raise TypeError("frame_stack must be a sequence")
        return tuple(value)


# ---------------------------------------------------------------------------
# Pause proposal
# ---------------------------------------------------------------------------


class DurablePauseProposalV1(FrozenContract):
    contract_version: Literal[1] = 1
    run_id: UUID
    root_call_id: str
    root_continuation: ContinuationRef
    frame_id: UUID
    node_id: str
    node_visit_id: str
    interrupt_id: UUID
    kind: Literal["approval", "input"]
    request_payload: JsonObject
    field_schema: JsonObject | None = None
    initial_values: JsonObject = Field(default_factory=dict)
    proposed_workflow_state: DurableWorkflowStateV1
    proposal_digest: str

    @field_validator("root_call_id", "node_id", "node_visit_id")
    @classmethod
    def _strs(cls, value: str, info: Any) -> str:
        return _require_non_empty_str(value, field_name=info.field_name)

    @field_validator("proposal_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _require_digest(value, field_name="proposal_digest")

    @model_validator(mode="after")
    def _check_digest(self) -> DurablePauseProposalV1:
        expected = compute_proposal_digest(
            run_id=self.run_id,
            root_call_id=self.root_call_id,
            root_continuation=self.root_continuation,
            frame_id=self.frame_id,
            node_id=self.node_id,
            node_visit_id=self.node_visit_id,
            interrupt_id=self.interrupt_id,
            kind=self.kind,
            request_payload=self.request_payload,
            field_schema=self.field_schema,
            initial_values=self.initial_values,
            proposed_workflow_state=self.proposed_workflow_state,
        )
        if self.proposal_digest != expected:
            raise ValueError("proposal_digest does not match canonical pause proposal payload")
        return self


# ---------------------------------------------------------------------------
# Budget suspension (sibling to Plan 05 BudgetLedgerState)
# ---------------------------------------------------------------------------


class BudgetSuspensionStateV1(FrozenContract):
    contract_version: Literal[1] = 1
    run_id: UUID
    interrupt_id: UUID
    parent_budget_revision_id: UUID
    parent_ledger_revision: int
    parent_ledger_digest: str
    suspended_at_utc: datetime
    remaining_active_ms: int
    human_wait_expires_at_utc: datetime
    suspension_digest: str

    @field_validator("parent_ledger_revision")
    @classmethod
    def _rev(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="parent_ledger_revision")

    @field_validator("remaining_active_ms")
    @classmethod
    def _remaining(cls, value: int) -> int:
        return _require_non_negative_int(value, field_name="remaining_active_ms")

    @field_validator("parent_ledger_digest", "suspension_digest")
    @classmethod
    def _digests(cls, value: str, info: Any) -> str:
        return _require_digest(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _check_digest(self) -> BudgetSuspensionStateV1:
        expected = compute_suspension_digest(
            run_id=self.run_id,
            interrupt_id=self.interrupt_id,
            parent_budget_revision_id=self.parent_budget_revision_id,
            parent_ledger_revision=self.parent_ledger_revision,
            parent_ledger_digest=self.parent_ledger_digest,
            suspended_at_utc=self.suspended_at_utc,
            remaining_active_ms=self.remaining_active_ms,
            human_wait_expires_at_utc=self.human_wait_expires_at_utc,
        )
        if self.suspension_digest != expected:
            raise ValueError(
                "suspension_digest does not match canonical budget suspension payload"
            )
        return self


# ---------------------------------------------------------------------------
# Digest helpers (canonical payloads exclude self-digest fields)
# ---------------------------------------------------------------------------


def _edge_payload(edge: DurableEdgeV1) -> dict[str, Any]:
    return edge.model_dump(mode="json", by_alias=True)


def _dep_payload(dep: FrozenExecutionDependencyRef) -> dict[str, Any]:
    return dep.model_dump(mode="json", by_alias=True)


def _node_payload(node: DurableNodePlanV1) -> dict[str, Any]:
    return node.model_dump(mode="json", by_alias=True)


def compute_plan_digest(
    *,
    target_kind: str,
    target_version_id: UUID,
    target_digest: str,
    entry_node_id: str,
    nodes: Sequence[DurableNodePlanV1],
) -> str:
    payload: dict[str, Any] = {
        "contractVersion": 1,
        "entryNodeId": entry_node_id,
        "nodes": [_node_payload(n) for n in nodes],
        "targetDigest": target_digest,
        "targetKind": target_kind,
        "targetVersionId": str(target_version_id),
    }
    return sha256_canonical_json(payload)


def compute_branch_decision_digest(
    *,
    node_id: str,
    node_visit_id: str,
    chosen_handle: str,
    chosen_target_node_id: str,
) -> str:
    return sha256_canonical_json(
        {
            "chosenHandle": chosen_handle,
            "chosenTargetNodeId": chosen_target_node_id,
            "nodeId": node_id,
            "nodeVisitId": node_visit_id,
        }
    )


def compute_loop_cursor_digest(
    *,
    loop_node_id: str,
    node_visit_id: str,
    iteration_index: int,
    item_key: str | None,
    completed_child_output_artifact_ids: Sequence[UUID],
) -> str:
    return sha256_canonical_json(
        {
            "completedChildOutputArtifactIds": [
                str(x) for x in completed_child_output_artifact_ids
            ],
            "itemKey": item_key,
            "iterationIndex": iteration_index,
            "loopNodeId": loop_node_id,
            "nodeVisitId": node_visit_id,
        }
    )


def compute_suspension_digest(
    *,
    run_id: UUID,
    interrupt_id: UUID,
    parent_budget_revision_id: UUID,
    parent_ledger_revision: int,
    parent_ledger_digest: str,
    suspended_at_utc: datetime,
    remaining_active_ms: int,
    human_wait_expires_at_utc: datetime,
) -> str:
    return sha256_canonical_json(
        {
            "contractVersion": 1,
            "humanWaitExpiresAtUtc": _utc_iso(human_wait_expires_at_utc),
            "interruptId": str(interrupt_id),
            "parentBudgetRevisionId": str(parent_budget_revision_id),
            "parentLedgerDigest": parent_ledger_digest,
            "parentLedgerRevision": parent_ledger_revision,
            "remainingActiveMs": remaining_active_ms,
            "runId": str(run_id),
            "suspendedAtUtc": _utc_iso(suspended_at_utc),
        }
    )


def compute_proposal_digest(
    *,
    run_id: UUID,
    root_call_id: str,
    root_continuation: ContinuationRef,
    frame_id: UUID,
    node_id: str,
    node_visit_id: str,
    interrupt_id: UUID,
    kind: str,
    request_payload: dict[str, Any],
    field_schema: dict[str, Any] | None,
    initial_values: dict[str, Any],
    proposed_workflow_state: DurableWorkflowStateV1,
) -> str:
    return sha256_canonical_json(
        {
            "contractVersion": 1,
            "fieldSchema": field_schema,
            "frameId": str(frame_id),
            "initialValues": initial_values,
            "interruptId": str(interrupt_id),
            "kind": kind,
            "nodeId": node_id,
            "nodeVisitId": node_visit_id,
            "proposedWorkflowState": proposed_workflow_state.model_dump(
                mode="json", by_alias=True
            ),
            "requestPayload": request_payload,
            "rootCallId": root_call_id,
            "rootContinuation": root_continuation.model_dump(mode="json", by_alias=True),
            "runId": str(run_id),
        }
    )


__all__ = [
    "DURABLE_WORKFLOW_IDENTITY_NAMESPACE",
    "SUPPORTED_BUDGET_SUSPENSION_CONTRACT_VERSIONS",
    "SUPPORTED_EXECUTION_PLAN_CONTRACT_VERSIONS",
    "SUPPORTED_PAUSE_PROPOSAL_CONTRACT_VERSIONS",
    "SUPPORTED_WORKFLOW_STATE_SCHEMA_VERSIONS",
    "BudgetSuspensionStateV1",
    "DurableBranchDecisionV1",
    "DurableCallFrameV1",
    "DurableEdgeV1",
    "DurableExecutionPlanV1",
    "DurableLoopCursorV1",
    "DurableNodePlanV1",
    "DurablePauseProposalV1",
    "DurableWorkflowStateV1",
    "FrozenExecutionDependencyRef",
    "build_root_continuation",
    "compute_branch_decision_digest",
    "compute_loop_cursor_digest",
    "compute_plan_digest",
    "compute_proposal_digest",
    "compute_suspension_digest",
    "derive_frame_id",
    "derive_interrupt_id",
    "derive_node_visit_id",
]

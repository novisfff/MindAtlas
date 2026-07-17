"""Plan 07 Task 7: exact resume of durable child frames + Provider waiting resolution.

Worker resume path (plan §11.3):

1. claim queued Run (caller / outer worker);
2. load resume-ready Checkpoint, resolved Interrupt, original waiting Checkpoint;
3. verify root continuation, frame/node visit, digests, suspension parent →
   resolution budget lineage, Manifest/policy/obligation lineage;
4. inject one typed immutable human result into that exact node visit;
5. checkpoint the node output/branch once;
6. continue child boundaries until second pause or root Capability terminal.

Only after root terminal is one exact ``ProviderWaitingResolution`` built and
validated against Plan 03's resume contracts. Second pauses keep the original
outer ``ContinuationRef`` and never call ``resume_provider_loop``.

No second Run status machine. Every result CAS still requires Plan 06
``status=running`` + expected revision. Irreconcilable drift routes to
``needs_reconciliation``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.capabilities.contracts import (
    CapabilityMetrics,
    CapabilityResult,
    ContinuationRef,
    cancelled_result,
    completed_result,
    failed_result,
)
from app.assistant.durable.codec import (
    NeedsReconciliationError,
    decode_checkpoint,
)
from app.assistant.durable.contracts import DurableAgentCheckpointV2
from app.assistant.durable.models import (
    AssistantRunBudgetRevision,
    AssistantRunCheckpoint,
    AssistantRunInterrupt,
)
from app.assistant.durable.repository import (
    DurableRunConflict,
    DurableRunRepository,
    LeaseToken,
)
from app.assistant.models import AssistantChatRun
from app.assistant.provider_loop.contracts import (
    ProviderLoopContinuation,
    ProviderLoopResumeRequest,
    ProviderWaitingResolution,
)
from app.assistant.workflow.durable.adapters import (
    DurableAdapterError,
    PortableNodeBag,
    _next_from_single_edge,
    build_default_registry,
)
from app.assistant.workflow.durable.contracts import (
    BudgetSuspensionStateV1,
    DurableExecutionPlanV1,
    DurablePauseProposalV1,
    DurableWorkflowStateV1,
    build_root_continuation,
)
from app.assistant.workflow.durable.interrupts import non_time_budget_snapshot
from app.assistant.workflow.durable.pause import (
    DurablePauseCommitResult,
    DurablePauseProtocolError,
    WorkerUnitPauseEffectPort,
    commit_durable_workflow_pause,
)
from app.assistant.workflow.durable.runner import (
    BoundaryKind,
    DurableFrameMaterial,
    DurableWorkflowRunner,
    _copy_frame,
)

# ---------------------------------------------------------------------------
# Reason codes
# ---------------------------------------------------------------------------

CODE_RESUME_PROTOCOL_ERROR = "durable_resume_protocol_error"
CODE_RESUME_LINEAGE_MISMATCH = "durable_resume_lineage_mismatch"
CODE_RESUME_BUDGET_MISMATCH = "durable_resume_budget_lineage_mismatch"
CODE_RESUME_CONTINUATION_MISMATCH = "durable_resume_continuation_mismatch"
CODE_RESUME_FRAME_MISMATCH = "durable_resume_frame_mismatch"
CODE_RESUME_SURFACE_TAMPERED = "durable_resume_surface_tampered"
CODE_RESUME_INTERRUPT_NOT_RESOLVED = "durable_resume_interrupt_not_resolved"
CODE_RESUME_NEEDS_RECONCILIATION = "needs_reconciliation"
CODE_RESUME_ALREADY_APPLIED = "durable_resume_already_applied"
CODE_RESUME_STALE_EVENT = "durable_resume_stale_event"
CODE_RESUME_STOP_WON = "durable_resume_stop_won"


class DurableResumeError(ValueError):
    """Recoverable resume protocol / lineage failure (before adapter work)."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = CODE_RESUME_PROTOCOL_ERROR,
        needs_reconciliation: bool = False,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.needs_reconciliation = needs_reconciliation


class DurableResumeNeedsReconciliation(DurableResumeError):
    """Irreconcilable target/build/plan/Checkpoint/Artifact drift."""

    def __init__(self, message: str, *, reason_code: str = CODE_RESUME_NEEDS_RECONCILIATION) -> None:
        super().__init__(message, reason_code=reason_code, needs_reconciliation=True)


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------

ResumeOutcomeKind = Literal[
    "human_applied",
    "second_pause",
    "root_terminal",
    "cancelled",
    "failed",
    "needs_reconciliation",
    "stop_won",
    "already_applied",
]


@dataclass(slots=True)
class HumanContinuationResult:
    """Typed immutable human node output injected once into the exact visit."""

    outcome: str
    status: str
    values: dict[str, Any]
    comment: str | None
    resolution_request_id: UUID | None
    resolution_digest: str | None
    interrupt_id: UUID
    node_id: str
    node_visit_id: str
    frame_id: UUID

    def as_bag_payload(self) -> dict[str, Any]:
        return {
            "text": self.outcome,
            "json_fields": {
                "outcome": self.outcome,
                "status": self.status,
                "values": dict(self.values),
                "comment": self.comment,
                "interruptId": str(self.interrupt_id),
                "resolutionRequestId": (
                    str(self.resolution_request_id) if self.resolution_request_id else None
                ),
                "resolutionDigest": self.resolution_digest,
            },
            "outcome": self.outcome,
            "status": self.status,
            "values": dict(self.values),
            "comment": self.comment,
        }


@dataclass(slots=True)
class LoadedResumeContext:
    """Exact resume-ready state loaded before any adapter/runtime construction."""

    run: AssistantChatRun
    interrupt: AssistantRunInterrupt
    resume_checkpoint_row: AssistantRunCheckpoint
    resume_checkpoint: DurableAgentCheckpointV2
    waiting_checkpoint_row: AssistantRunCheckpoint
    waiting_checkpoint: DurableAgentCheckpointV2
    workflow_state: DurableWorkflowStateV1
    root_continuation: ContinuationRef
    suspension: BudgetSuspensionStateV1
    resolution_budget_row: AssistantRunBudgetRevision
    parent_budget_row: AssistantRunBudgetRevision
    human_result: HumanContinuationResult
    # True when Checkpoint shows human already applied (crash-after-apply recovery).
    # Frame advanced, pending_interrupt_id cleared, next_action=continue_child.
    human_already_applied: bool = False
    # Portable bag snapshot rehydrated from post-apply Artifact (if any).
    bag_snapshot: dict[str, Any] | None = None


@dataclass(slots=True)
class ResumeUnitResult:
    """Outcome of one interrupt-resume worker unit."""

    kind: ResumeOutcomeKind
    reason_code: str | None = None
    state_revision: int | None = None
    workflow_state: DurableWorkflowStateV1 | None = None
    human_result: HumanContinuationResult | None = None
    root_continuation: ContinuationRef | None = None
    pause_commit: DurablePauseCommitResult | None = None
    provider_waiting_resolution: ProviderWaitingResolution | None = None
    capability_result: CapabilityResult | None = None
    applied_node_visit_id: str | None = None
    needs_reconciliation: bool = False
    detail: str | None = None


# ---------------------------------------------------------------------------
# Load + verify
# ---------------------------------------------------------------------------


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decode_v2(row: AssistantRunCheckpoint) -> DurableAgentCheckpointV2:
    try:
        decoded = decode_checkpoint(row.state_payload)
    except NeedsReconciliationError as exc:
        raise DurableResumeNeedsReconciliation(
            f"checkpoint {row.id} codec drift: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise DurableResumeNeedsReconciliation(
            f"checkpoint {row.id} decode failed: {exc}",
        ) from exc
    if not isinstance(decoded, DurableAgentCheckpointV2):
        raise DurableResumeNeedsReconciliation(
            f"resume requires Checkpoint v2, got {type(decoded)!r}",
        )
    return decoded


def _parse_suspension(payload: Mapping[str, Any] | None) -> BudgetSuspensionStateV1:
    if not isinstance(payload, Mapping) or not payload:
        raise DurableResumeError(
            "interrupt missing budget_suspension_state",
            reason_code=CODE_RESUME_LINEAGE_MISMATCH,
        )
    try:
        return BudgetSuspensionStateV1.model_validate(dict(payload))
    except Exception as exc:  # noqa: BLE001
        raise DurableResumeNeedsReconciliation(
            f"budget suspension decode failed: {exc}",
        ) from exc


def _continuation_equal(a: ContinuationRef, b: ContinuationRef) -> bool:
    return (
        a.continuation_type == b.continuation_type
        and int(a.contract_version) == int(b.contract_version)
        and str(a.reference_id) == str(b.reference_id)
        and str(a.payload_digest) == str(b.payload_digest)
    )


def build_human_continuation_result(interrupt: AssistantRunInterrupt) -> HumanContinuationResult:
    """Build one typed immutable human result from a terminal Interrupt row."""
    status = str(interrupt.status or "")
    if status == "pending":
        raise DurableResumeError(
            "cannot resume from pending interrupt",
            reason_code=CODE_RESUME_INTERRUPT_NOT_RESOLVED,
        )
    outcome = str(interrupt.decision or status)
    values = dict(interrupt.submitted_values or {})
    return HumanContinuationResult(
        outcome=outcome,
        status=status,
        values=values,
        comment=interrupt.comment,
        resolution_request_id=interrupt.resolution_request_id,
        resolution_digest=interrupt.resolution_digest,
        interrupt_id=interrupt.id,
        node_id=str(interrupt.node_id),
        node_visit_id=str(interrupt.node_visit_id),
        frame_id=interrupt.workflow_frame_id,
    )


def _parse_human_applied_reason(reason: str | None) -> UUID | None:
    """Parse interrupt_id from ``human_applied:{interrupt_id}:{node_visit_id}``."""
    if not reason or not str(reason).startswith("human_applied:"):
        return None
    parts = str(reason).split(":")
    if len(parts) < 2:
        return None
    try:
        return UUID(parts[1])
    except (TypeError, ValueError):
        return None


def _load_bag_snapshot_from_artifact(
    db: Session,
    *,
    artifact_id: UUID | None,
) -> dict[str, Any] | None:
    """Rehydrate portable bag snapshot from a post-apply Artifact row."""
    if artifact_id is None:
        return None
    from app.assistant.durable.models import AssistantRunArtifact

    art = db.get(AssistantRunArtifact, artifact_id)
    if art is None or art.inline_bytes is None:
        return None
    if str(art.kind) not in {"node_bag_snapshot", "bag_snapshot"}:
        # Still try to decode JSON if content is JSON.
        if not str(art.media_type or "").startswith("application/json"):
            return None
    try:
        import json

        payload = json.loads(art.inline_bytes.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, dict):
        return None
    # Unwrap optional envelope.
    if "bagSnapshot" in payload and isinstance(payload["bagSnapshot"], dict):
        return dict(payload["bagSnapshot"])
    if "nodeOutputs" in payload or "node_outputs" in payload:
        return payload
    return None


def _build_bag_snapshot_artifact(
    *,
    run_id: UUID,
    frame_id: UUID,
    interrupt_id: UUID,
    bag: PortableNodeBag,
) -> Any:
    """Build inline Artifact carrying portable bag snapshot for crash recovery."""
    import json

    from app.assistant.domain.digests import sha256_bytes
    from app.assistant.durable.models import AssistantRunArtifact

    snapshot = bag.to_snapshot()
    body_obj = {
        "contractVersion": 1,
        "kind": "node_bag_snapshot",
        "frameId": str(frame_id),
        "interruptId": str(interrupt_id),
        "bagSnapshot": snapshot,
    }
    body = json.dumps(
        body_obj,
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    digest = sha256_bytes(body)
    return AssistantRunArtifact(
        id=uuid4(),
        run_id=run_id,
        kind="node_bag_snapshot",
        media_type="application/json",
        display_label=f"bag:{frame_id}",
        storage_kind="inline",
        byte_size=len(body),
        content_sha256=digest,
        inline_bytes=body,
        object_key=None,
        metadata_json={
            "frameId": str(frame_id),
            "interruptId": str(interrupt_id),
        },
    )


def load_resume_context(
    db: Session,
    *,
    run_id: UUID,
    expected_interrupt_id: UUID | None = None,
) -> LoadedResumeContext:
    """Load resume-ready Checkpoint, resolved Interrupt, and original waiting Checkpoint.

    Rejects missing/mismatched child revision and incomplete lineage *before*
    any adapter/runtime construction.

    Also accepts crash-after-apply recovery: Checkpoint with
    ``next_action=continue_child``, no ``pending_interrupt_id``, frame advanced
    past the human node. In that case the resolved Interrupt is recovered from
    the post-apply reason / resolution_checkpoint pointer, and bag_snapshot is
    rehydrated from the frame's node_state_artifact_id when present.
    """
    run = db.get(AssistantChatRun, run_id)
    if run is None:
        raise DurableResumeError(
            f"run {run_id} not found",
            reason_code=CODE_RESUME_PROTOCOL_ERROR,
        )
    if run.current_checkpoint_id is None:
        raise DurableResumeError(
            "run missing current_checkpoint_id for resume",
            reason_code=CODE_RESUME_PROTOCOL_ERROR,
        )

    resume_row = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
    if resume_row is None:
        raise DurableResumeNeedsReconciliation(
            "resume-ready checkpoint row missing",
        )
    resume_cp = _decode_v2(resume_row)

    next_kind = getattr(resume_cp.next_action, "kind", None)
    human_already_applied = False
    interrupt_id = resume_cp.pending_interrupt_id
    bag_snapshot: dict[str, Any] | None = None

    if next_kind == "resume_child" and interrupt_id is not None:
        # Normal first entry: resume-ready with pending resolved interrupt.
        pass
    elif next_kind == "continue_child" and interrupt_id is None:
        # Crash-after-apply recovery: human already applied, frame advanced.
        human_already_applied = True
        interrupt_id = _parse_human_applied_reason(getattr(resume_row, "reason", None))
        if interrupt_id is None:
            # Fall back: find most recent terminal interrupt whose resolution
            # checkpoint is an ancestor of current (or any resolved on this run).
            from sqlalchemy import select

            rows = list(
                db.scalars(
                    select(AssistantRunInterrupt)
                    .where(
                        AssistantRunInterrupt.run_id == run_id,
                        AssistantRunInterrupt.status != "pending",
                    )
                    .order_by(AssistantRunInterrupt.resolved_at.desc())
                )
            )
            if not rows:
                raise DurableResumeError(
                    "continue_child checkpoint missing recoverable interrupt identity",
                    reason_code=CODE_RESUME_PROTOCOL_ERROR,
                )
            interrupt_id = rows[0].id
    elif next_kind in {"resume_child", "continue_child"}:
        # continue_child with pending_interrupt_id, or resume_child without it — protocol error.
        if interrupt_id is None:
            raise DurableResumeError(
                "resume checkpoint missing pending_interrupt_id",
                reason_code=CODE_RESUME_PROTOCOL_ERROR,
            )
    else:
        raise DurableResumeError(
            f"resume checkpoint next_action={next_kind!r} is not resume_child/continue_child",
            reason_code=CODE_RESUME_PROTOCOL_ERROR,
        )

    if interrupt_id is None:
        raise DurableResumeError(
            "resume checkpoint missing pending_interrupt_id",
            reason_code=CODE_RESUME_PROTOCOL_ERROR,
        )
    if expected_interrupt_id is not None and interrupt_id != expected_interrupt_id:
        # For crash-after recovery, expected may still be the original interrupt.
        if not human_already_applied:
            raise DurableResumeError(
                f"pending_interrupt_id {interrupt_id} != expected {expected_interrupt_id}",
                reason_code=CODE_RESUME_LINEAGE_MISMATCH,
            )
        if interrupt_id != expected_interrupt_id:
            raise DurableResumeError(
                f"recovered interrupt_id {interrupt_id} != expected {expected_interrupt_id}",
                reason_code=CODE_RESUME_LINEAGE_MISMATCH,
            )

    interrupt = db.get(AssistantRunInterrupt, interrupt_id)
    if interrupt is None:
        raise DurableResumeNeedsReconciliation(
            f"resolved interrupt {interrupt_id} missing",
        )
    if interrupt.run_id != run_id:
        raise DurableResumeError(
            "interrupt run_id mismatch",
            reason_code=CODE_RESUME_LINEAGE_MISMATCH,
        )
    if str(interrupt.status) == "pending":
        raise DurableResumeError(
            "interrupt still pending; cannot resume",
            reason_code=CODE_RESUME_INTERRUPT_NOT_RESOLVED,
        )

    waiting_row = db.get(AssistantRunCheckpoint, interrupt.checkpoint_id)
    if waiting_row is None:
        raise DurableResumeNeedsReconciliation(
            f"original waiting checkpoint {interrupt.checkpoint_id} missing",
        )
    waiting_cp = _decode_v2(waiting_row)

    # Prefer workflow_state from resume checkpoint (carried from waiting / post-apply).
    workflow_state = resume_cp.workflow_state or waiting_cp.workflow_state
    if workflow_state is None:
        raise DurableResumeNeedsReconciliation(
            "resume/waiting checkpoint missing workflow_state",
        )
    if not isinstance(workflow_state, DurableWorkflowStateV1):
        try:
            workflow_state = DurableWorkflowStateV1.model_validate(workflow_state)
        except Exception as exc:  # noqa: BLE001
            raise DurableResumeNeedsReconciliation(
                f"workflow_state invalid: {exc}",
            ) from exc

    root_cont = (
        resume_cp.active_capability_continuation
        or waiting_cp.active_capability_continuation
    )
    if root_cont is None:
        # Derive from workflow state as last resort (stable across pauses).
        root_cont = build_root_continuation(
            root_frame_id=workflow_state.root_frame_id,
            root_invocation_digest=workflow_state.root_invocation_digest,
        )
    if not isinstance(root_cont, ContinuationRef):
        try:
            root_cont = ContinuationRef.model_validate(root_cont)
        except Exception as exc:  # noqa: BLE001
            raise DurableResumeNeedsReconciliation(
                f"active_capability_continuation invalid: {exc}",
            ) from exc

    suspension = _parse_suspension(interrupt.budget_suspension_state)
    if suspension.interrupt_id != interrupt.id:
        raise DurableResumeError(
            "suspension.interrupt_id mismatch",
            reason_code=CODE_RESUME_LINEAGE_MISMATCH,
        )
    if suspension.run_id != run_id:
        raise DurableResumeError(
            "suspension.run_id mismatch",
            reason_code=CODE_RESUME_LINEAGE_MISMATCH,
        )

    parent_budget = db.get(AssistantRunBudgetRevision, interrupt.budget_revision_id)
    if parent_budget is None:
        raise DurableResumeNeedsReconciliation(
            "parent budget revision missing",
        )
    if str(parent_budget.budget_digest) != str(suspension.parent_ledger_digest):
        raise DurableResumeError(
            "suspension parent_ledger_digest does not match parent budget row",
            reason_code=CODE_RESUME_BUDGET_MISMATCH,
        )
    if parent_budget.id != suspension.parent_budget_revision_id:
        raise DurableResumeError(
            "suspension parent_budget_revision_id mismatch",
            reason_code=CODE_RESUME_BUDGET_MISMATCH,
        )

    # Resolution child budget (derived at resolve) must line up.
    res_budget_id = interrupt.resolution_budget_revision_id
    if res_budget_id is None:
        raise DurableResumeError(
            "resolved interrupt missing resolution_budget_revision_id",
            reason_code=CODE_RESUME_BUDGET_MISMATCH,
        )
    child_budget = db.get(AssistantRunBudgetRevision, res_budget_id)
    if child_budget is None:
        raise DurableResumeNeedsReconciliation(
            "resolution budget revision missing",
        )
    if child_budget.parent_revision_id != parent_budget.id:
        raise DurableResumeError(
            "resolution budget parent_revision_id != suspension parent",
            reason_code=CODE_RESUME_BUDGET_MISMATCH,
        )
    if child_budget.parent_digest is not None and str(child_budget.parent_digest) != str(
        suspension.parent_ledger_digest
    ):
        raise DurableResumeError(
            "resolution budget parent_digest != suspension parent_ledger_digest",
            reason_code=CODE_RESUME_BUDGET_MISMATCH,
        )
    if resume_cp.budget_revision_id != child_budget.id and run.current_budget_revision_id != child_budget.id:
        # Resume checkpoint or run pointer must reference the derived child.
        # After human apply the budget pointer may still be the child; accept either.
        if (
            not human_already_applied
            and resume_cp.budget_revision_id != child_budget.id
        ):
            raise DurableResumeError(
                "resume checkpoint budget_revision_id != resolution child budget",
                reason_code=CODE_RESUME_BUDGET_MISMATCH,
            )

    # Manifest/policy/obligation lineage vs waiting + resume.
    if (
        waiting_cp.manifest_revision_id is not None
        and resume_cp.manifest_revision_id is not None
        and waiting_cp.manifest_revision_id != resume_cp.manifest_revision_id
        and interrupt.manifest_revision_id not in {
            waiting_cp.manifest_revision_id,
            resume_cp.manifest_revision_id,
        }
    ):
        raise DurableResumeError(
            "manifest revision lineage drift",
            reason_code=CODE_RESUME_LINEAGE_MISMATCH,
        )

    # Frame / node visit continuity vs interrupt identity.
    top = workflow_state.frame_stack[-1] if workflow_state.frame_stack else None
    if top is None:
        raise DurableResumeNeedsReconciliation("workflow frame_stack empty")
    if top.frame_id != interrupt.workflow_frame_id:
        # Nested: interrupt may be on a non-top frame? Require exact frame still in stack.
        frame_ids = {f.frame_id for f in workflow_state.frame_stack}
        if interrupt.workflow_frame_id not in frame_ids:
            raise DurableResumeError(
                "interrupt frame not present in workflow frame_stack",
                reason_code=CODE_RESUME_FRAME_MISMATCH,
            )
    # When still waiting at human node, top should match.
    if top.phase == "waiting" and not human_already_applied:
        if str(top.current_node_id) != str(interrupt.node_id):
            raise DurableResumeError(
                f"waiting frame current_node_id {top.current_node_id!r} "
                f"!= interrupt.node_id {interrupt.node_id!r}",
                reason_code=CODE_RESUME_FRAME_MISMATCH,
            )
        if top.node_visit_id is not None and str(top.node_visit_id) != str(interrupt.node_visit_id):
            raise DurableResumeError(
                "waiting frame node_visit_id mismatch",
                reason_code=CODE_RESUME_FRAME_MISMATCH,
            )

    # Crash-after-apply: rehydrate bag from node_state_artifact_id on the frame.
    if human_already_applied:
        # Prefer the human frame's node_state_artifact_id.
        bag_art_id = None
        for fr in workflow_state.frame_stack:
            if fr.frame_id == interrupt.workflow_frame_id and fr.node_state_artifact_id:
                bag_art_id = fr.node_state_artifact_id
                break
        if bag_art_id is None and top is not None:
            bag_art_id = top.node_state_artifact_id
        # Also accept checkpoint-level artifact_ids (first bag snapshot kind).
        bag_snapshot = _load_bag_snapshot_from_artifact(db, artifact_id=bag_art_id)
        if bag_snapshot is None and resume_cp.artifact_ids:
            for aid in resume_cp.artifact_ids:
                bag_snapshot = _load_bag_snapshot_from_artifact(db, artifact_id=aid)
                if bag_snapshot is not None:
                    break

    # Root continuation must match derived root identity.
    expected_root = build_root_continuation(
        root_frame_id=workflow_state.root_frame_id,
        root_invocation_digest=workflow_state.root_invocation_digest,
    )
    if not _continuation_equal(root_cont, expected_root):
        raise DurableResumeError(
            "root ContinuationRef does not match workflow root identity",
            reason_code=CODE_RESUME_CONTINUATION_MISMATCH,
        )

    human = build_human_continuation_result(interrupt)

    return LoadedResumeContext(
        run=run,
        interrupt=interrupt,
        resume_checkpoint_row=resume_row,
        resume_checkpoint=resume_cp,
        waiting_checkpoint_row=waiting_row,
        waiting_checkpoint=waiting_cp,
        workflow_state=workflow_state,
        root_continuation=root_cont,
        suspension=suspension,
        resolution_budget_row=child_budget,
        parent_budget_row=parent_budget,
        human_result=human,
        human_already_applied=human_already_applied,
        bag_snapshot=bag_snapshot,
    )


def verify_resolution_budget_lineage(
    *,
    suspension: BudgetSuspensionStateV1,
    parent_budget: AssistantRunBudgetRevision,
    child_budget: AssistantRunBudgetRevision,
    parent_ledger_payload: Mapping[str, Any] | None = None,
) -> None:
    """Explicit suspension parent → derived child budget lineage check.

    Reject missing/mismatched child revision before adapter/runtime construction.
    """
    if parent_budget.id != suspension.parent_budget_revision_id:
        raise DurableResumeError(
            "parent budget id != suspension.parent_budget_revision_id",
            reason_code=CODE_RESUME_BUDGET_MISMATCH,
        )
    if str(parent_budget.budget_digest) != str(suspension.parent_ledger_digest):
        raise DurableResumeError(
            "parent budget digest != suspension.parent_ledger_digest",
            reason_code=CODE_RESUME_BUDGET_MISMATCH,
        )
    if child_budget.parent_revision_id != parent_budget.id:
        raise DurableResumeError(
            "child budget parent_revision_id mismatch",
            reason_code=CODE_RESUME_BUDGET_MISMATCH,
        )
    if int(child_budget.revision) <= int(parent_budget.revision):
        raise DurableResumeError(
            "child budget revision must be > parent revision",
            reason_code=CODE_RESUME_BUDGET_MISMATCH,
        )
    if parent_ledger_payload is not None:
        # Non-time fields must be byte-identical between parent ledger and child payload.
        try:
            from app.assistant.policy.budgets import BudgetLedgerState

            parent_ledger = BudgetLedgerState.model_validate(dict(parent_ledger_payload))
            child_payload = dict(child_budget.payload or {})
            child_ledger = BudgetLedgerState.model_validate(child_payload)
            parent_snap = non_time_budget_snapshot(parent_ledger)
            child_snap = non_time_budget_snapshot(child_ledger)
            if parent_snap != child_snap:
                raise DurableResumeError(
                    "child budget non-time fields drifted from parent ledger",
                    reason_code=CODE_RESUME_BUDGET_MISMATCH,
                )
        except DurableResumeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DurableResumeError(
                f"budget ledger compare failed: {exc}",
                reason_code=CODE_RESUME_BUDGET_MISMATCH,
            ) from exc


# ---------------------------------------------------------------------------
# Apply human result once + advance frame
# ---------------------------------------------------------------------------


def _handle_for_outcome(*, kind: str, outcome: str) -> str | None:
    """Map human outcome to optional edge source_handle."""
    o = str(outcome).lower()
    if o == "approved":
        return "approved"
    if o == "submitted":
        return "submitted"
    if o in {"rejected"}:
        return "rejected"
    if o in {"cancelled"}:
        return "cancelled"
    if o in {"expired"}:
        return "expired"
    return None


def select_human_successor(
    *,
    plan: DurableExecutionPlanV1,
    node_id: str,
    outcome: str,
    kind: str,
) -> str | None:
    """Choose next node after human decision (typed branch or single edge)."""
    nodes = {n.node_id: n for n in plan.nodes}
    node = nodes.get(node_id)
    if node is None:
        raise DurableResumeError(
            f"human node {node_id!r} missing from plan",
            reason_code=CODE_RESUME_FRAME_MISMATCH,
            needs_reconciliation=True,
        )
    handle = _handle_for_outcome(kind=kind, outcome=outcome)
    if handle is not None:
        for edge in node.outgoing_edges:
            if (edge.source_handle or "") == handle:
                return edge.target_node_id
    if str(outcome or "").strip().lower() in {"approved", "submitted"}:
        # Backward-compatible success path for reviewed linear HITL graphs.
        return _next_from_single_edge(node)
    # Non-success outcomes must never fall back to an approve/default edge.
    return None


def apply_human_result_once(
    *,
    state: DurableWorkflowStateV1,
    human: HumanContinuationResult,
    plan: DurableExecutionPlanV1,
    bag: PortableNodeBag | None = None,
    interrupt_kind: str = "approval",
) -> tuple[DurableWorkflowStateV1, PortableNodeBag, str | None]:
    """Inject one typed human result into the exact node visit and advance.

    Does not re-enter the human adapter. Committed branch/loop/child progress
    elsewhere on the stack is preserved. Returns (new_state, bag, next_node_id).
    """
    if not state.frame_stack:
        raise DurableResumeError(
            "empty frame_stack",
            reason_code=CODE_RESUME_FRAME_MISMATCH,
            needs_reconciliation=True,
        )

    # Locate the exact frame that owns the interrupt.
    frame_idx = None
    for i, fr in enumerate(state.frame_stack):
        if fr.frame_id == human.frame_id:
            frame_idx = i
            break
    if frame_idx is None:
        raise DurableResumeError(
            "human frame not in stack",
            reason_code=CODE_RESUME_FRAME_MISMATCH,
        )

    frame = state.frame_stack[frame_idx]
    if str(frame.current_node_id) != str(human.node_id):
        # Already applied? Idempotent short-circuit if bag already has output.
        if bag is not None and human.node_id in bag.node_outputs:
            next_id = select_human_successor(
                plan=plan,
                node_id=human.node_id,
                outcome=human.outcome,
                kind=interrupt_kind,
            )
            return state, bag, next_id
        raise DurableResumeError(
            f"frame current_node_id {frame.current_node_id!r} != human node {human.node_id!r}",
            reason_code=CODE_RESUME_FRAME_MISMATCH,
        )
    if frame.node_visit_id is not None and str(frame.node_visit_id) != str(human.node_visit_id):
        raise DurableResumeError(
            "frame node_visit_id mismatch on human apply",
            reason_code=CODE_RESUME_FRAME_MISMATCH,
        )

    # Input cancellation without typed branch cancels the root Capability.
    if human.outcome == "cancelled" and interrupt_kind == "input":
        # Check if plan declares a cancelled handle; else cancel root.
        nodes = {n.node_id: n for n in plan.nodes}
        node = nodes[human.node_id]
        has_cancel_edge = any(
            (e.source_handle or "") == "cancelled" for e in node.outgoing_edges
        )
        if not has_cancel_edge:
            cancelled = _copy_frame(frame, phase="cancelled")
            new_stack = tuple(state.frame_stack[:frame_idx]) + (cancelled,) + tuple(
                state.frame_stack[frame_idx + 1 :]
            )
            # Cancel all frames above/root.
            new_stack = tuple(
                _copy_frame(f, phase="cancelled") if f.phase not in {"completed", "failed", "cancelled"} else f
                for f in new_stack
            )
            new_state = DurableWorkflowStateV1(
                run_id=state.run_id,
                root_frame_id=state.root_frame_id,
                root_invocation_digest=state.root_invocation_digest,
                frame_stack=new_stack,
                pending_interrupt_id=None,
                terminal_output_artifact_id=state.terminal_output_artifact_id,
            )
            out_bag = bag or PortableNodeBag()
            out_bag.set_output(human.node_id, human.as_bag_payload())
            return new_state, out_bag, None

    next_id = select_human_successor(
        plan=plan,
        node_id=human.node_id,
        outcome=human.outcome,
        kind=interrupt_kind,
    )

    out_bag = bag or PortableNodeBag()
    # Idempotent: do not overwrite if already applied with same digest.
    existing = out_bag.node_outputs.get(human.node_id)
    if existing is not None:
        existing_digest = existing.get("json_fields", {}).get("resolutionDigest")
        if existing_digest and human.resolution_digest and existing_digest != human.resolution_digest:
            raise DurableResumeError(
                "human node already applied with different resolution_digest",
                reason_code=CODE_RESUME_ALREADY_APPLIED,
            )
        # Same result already applied — still ensure frame advanced.
    else:
        out_bag.set_output(human.node_id, human.as_bag_payload())

    if next_id is None:
        # Non-success without an exact typed edge terminates safely; it must not
        # be represented as a successful completed frame.
        terminal_phase = (
            "completed"
            if human.outcome in {"approved", "submitted"}
            else "cancelled"
        )
        completed = _copy_frame(
            frame,
            phase=terminal_phase,
            current_node_id=human.node_id,
            node_visit_id=human.node_visit_id,
        )
        new_stack = tuple(state.frame_stack[:frame_idx]) + (completed,) + tuple(
            state.frame_stack[frame_idx + 1 :]
        )
        new_state = DurableWorkflowStateV1(
            run_id=state.run_id,
            root_frame_id=state.root_frame_id,
            root_invocation_digest=state.root_invocation_digest,
            frame_stack=new_stack,
            pending_interrupt_id=None,
            terminal_output_artifact_id=state.terminal_output_artifact_id,
        )
        return new_state, out_bag, None

    advanced = _copy_frame(
        frame,
        phase="ready",
        current_node_id=next_id,
        node_visit_id=None,  # next prepare mints
        node_visit_ordinal=int(frame.node_visit_ordinal) + 1,
        execution_attempt=1,
    )
    new_stack = tuple(state.frame_stack[:frame_idx]) + (advanced,) + tuple(
        state.frame_stack[frame_idx + 1 :]
    )
    # Drop any frames above the human frame (should not happen while waiting).
    new_state = DurableWorkflowStateV1(
        run_id=state.run_id,
        root_frame_id=state.root_frame_id,
        root_invocation_digest=state.root_invocation_digest,
        frame_stack=new_stack,
        pending_interrupt_id=None,
        terminal_output_artifact_id=state.terminal_output_artifact_id,
    )
    return new_state, out_bag, next_id


# ---------------------------------------------------------------------------
# Continue child until second pause or root terminal
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ContinueBoundaryOutcome:
    kind: Literal["second_pause", "root_terminal", "cancelled", "failed", "continue"]
    workflow_state: DurableWorkflowStateV1
    pause_proposal: DurablePauseProposalV1 | None = None
    capability_status: Literal["completed", "failed", "cancelled"] | None = None
    user_text: str | None = None
    structured_output: Any | None = None
    reason_code: str | None = None
    bag: PortableNodeBag | None = None


def _material_for_top(
    *,
    top: Any,
    material: DurableFrameMaterial,
    child_materials: Mapping[str, DurableFrameMaterial] | None,
) -> DurableFrameMaterial:
    """Select root or child material by top frame target_version_id."""
    if child_materials:
        key = str(getattr(top, "target_version_id", "") or "")
        if key in child_materials:
            return child_materials[key]
        # Root material may itself be a child plan when waiting on nested frame.
        if str(material.plan.target_version_id) == key:
            return material
        # Prefer root material matching frame when provided as child map entry.
        for mat in child_materials.values():
            if str(mat.plan.target_version_id) == key:
                return mat
    return material


def continue_child_until_boundary(
    *,
    runner: DurableWorkflowRunner,
    state: DurableWorkflowStateV1,
    material: DurableFrameMaterial,
    child_materials: Mapping[str, DurableFrameMaterial] | None = None,
    max_boundaries: int = 64,
) -> ContinueBoundaryOutcome:
    """Drive one-boundary prepare/execute/apply until pause, root terminal, or fail.

    Does not recompute committed branch/loop/child progress; only advances from
    the current ready frame position.
    """
    current = state
    bag_snapshot = None
    for _ in range(max_boundaries):
        if not current.frame_stack:
            return ContinueBoundaryOutcome(
                kind="failed",
                workflow_state=current,
                reason_code="empty_frame_stack",
            )
        top = current.frame_stack[-1]
        if top.phase in {"completed", "failed", "cancelled"}:
            if top.parent_frame_id is None:
                status: Literal["completed", "failed", "cancelled"]
                if top.phase == "completed":
                    status = "completed"
                elif top.phase == "cancelled":
                    status = "cancelled"
                else:
                    status = "failed"
                # Project text from bag if available.
                text = None
                bag = runner.export_bag_snapshot(top.frame_id)
                if bag and isinstance(bag.get("nodeOutputs"), dict):
                    for nid, out in bag["nodeOutputs"].items():
                        if isinstance(out, dict) and out.get("text"):
                            text = str(out["text"])
                return ContinueBoundaryOutcome(
                    kind="root_terminal",
                    workflow_state=current,
                    capability_status=status,
                    user_text=text,
                    structured_output=bag,
                    bag=runner._bags.get(top.frame_id),
                )
            # Child terminal should have been popped by apply; treat as failed protocol.
            return ContinueBoundaryOutcome(
                kind="failed",
                workflow_state=current,
                reason_code="child_terminal_not_popped",
            )

        active_material = _material_for_top(
            top=top, material=material, child_materials=child_materials
        )
        try:
            prepared = runner.prepare_boundary(state=current, material=active_material)
        except DurableAdapterError as exc:
            return ContinueBoundaryOutcome(
                kind="failed",
                workflow_state=current,
                reason_code=getattr(exc, "reason_code", None) or "prepare_failed",
            )

        try:
            result = runner.execute_boundary(
                prepared=prepared,
                material=active_material,
                child_materials=child_materials,
            )
        except DurableAdapterError as exc:
            return ContinueBoundaryOutcome(
                kind="failed",
                workflow_state=current,
                reason_code=getattr(exc, "reason_code", None) or "execute_failed",
            )

        if result.kind == BoundaryKind.HUMAN_PAUSE:
            current = runner.apply_boundary_result(state=prepared.workflow_state, result=result)
            return ContinueBoundaryOutcome(
                kind="second_pause",
                workflow_state=current,
                pause_proposal=result.pause_proposal,
                bag=result.bag,
            )
        if result.kind == BoundaryKind.CANCELLED:
            current = runner.apply_boundary_result(state=prepared.workflow_state, result=result)
            return ContinueBoundaryOutcome(
                kind="cancelled",
                workflow_state=current,
                capability_status="cancelled",
                reason_code=result.reason_code,
                bag=result.bag,
            )
        if result.kind == BoundaryKind.LEASE_LOST:
            return ContinueBoundaryOutcome(
                kind="failed",
                workflow_state=current,
                reason_code="lease_lost",
            )
        if result.kind == BoundaryKind.FAILED:
            return ContinueBoundaryOutcome(
                kind="failed",
                workflow_state=current,
                reason_code=result.reason_code or "boundary_failed",
                bag=result.bag,
            )

        current = runner.apply_boundary_result(state=prepared.workflow_state, result=result)
        bag_snapshot = result.bag

        if result.kind == BoundaryKind.ROOT_COMPLETED:
            text = None
            if result.bag is not None:
                for nid, out in result.bag.node_outputs.items():
                    if nid != "start" and isinstance(out, dict) and out.get("text") is not None:
                        text = str(out.get("text"))
            return ContinueBoundaryOutcome(
                kind="root_terminal",
                workflow_state=current,
                capability_status="completed",
                user_text=text,
                structured_output=result.bag_snapshot,
                bag=result.bag or bag_snapshot,
            )

    return ContinueBoundaryOutcome(
        kind="failed",
        workflow_state=current,
        reason_code="max_boundaries_exceeded",
    )


# ---------------------------------------------------------------------------
# Provider waiting resolution (only after root terminal)
# ---------------------------------------------------------------------------


def build_provider_waiting_resolution(
    *,
    provider_loop_continuation: ProviderLoopContinuation,
    root_continuation: ContinuationRef,
    capability_result: CapabilityResult,
) -> ProviderWaitingResolution:
    """Build exactly one trusted ProviderWaitingResolution for the waiting call.

    Plan 03 forbids another ``waiting`` result as the resolution of a waiting
    Tool Call. Root child must already be completed|failed|cancelled.
    """
    if capability_result.status == "waiting":
        raise DurableResumeError(
            "cannot resolve waiting call with another waiting result",
            reason_code=CODE_RESUME_PROTOCOL_ERROR,
        )
    if capability_result.status not in {"completed", "failed", "cancelled"}:
        raise DurableResumeError(
            f"capability_result status {capability_result.status!r} not terminal",
            reason_code=CODE_RESUME_PROTOCOL_ERROR,
        )
    waiting_cont = provider_loop_continuation.waiting_call.capability_continuation
    if not _continuation_equal(waiting_cont, root_continuation):
        raise DurableResumeError(
            "ProviderLoopContinuation.waiting_call.capability_continuation "
            "does not match outer root ContinuationRef",
            reason_code=CODE_RESUME_CONTINUATION_MISMATCH,
        )
    return ProviderWaitingResolution(
        call_id=provider_loop_continuation.waiting_call.call_id,
        capability_continuation=root_continuation,
        capability_result=capability_result,
    )


def validate_provider_waiting_resume(
    *,
    manifest: Any,
    messages: Sequence[Any],
    continuation: ProviderLoopContinuation,
    resolved_waiting: ProviderWaitingResolution,
) -> ProviderLoopResumeRequest:
    """Run Plan 03 ProviderLoopResumeRequest validation (no Provider I/O).

    Preserves completed sibling prefix / pending suffix / original surface /
    Manifest / round usage invariants encoded in the continuation.
    """
    try:
        return ProviderLoopResumeRequest(
            manifest=manifest,
            messages=tuple(messages),
            continuation=continuation,
            resolved_waiting=resolved_waiting,
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "surface" in msg.lower() or "manifest" in msg.lower() or "transcript" in msg.lower():
            raise DurableResumeError(
                f"provider waiting resume validation failed: {exc}",
                reason_code=CODE_RESUME_SURFACE_TAMPERED,
                needs_reconciliation=True,
            ) from exc
        if "continuation" in msg.lower() or "call_id" in msg.lower():
            raise DurableResumeError(
                f"provider waiting resume validation failed: {exc}",
                reason_code=CODE_RESUME_CONTINUATION_MISMATCH,
                needs_reconciliation=True,
            ) from exc
        raise DurableResumeNeedsReconciliation(
            f"provider waiting resume validation failed: {exc}",
        ) from exc


def capability_result_from_root(
    *,
    status: Literal["completed", "failed", "cancelled"],
    user_text: str | None = None,
    structured_output: Any = None,
    metrics: CapabilityMetrics | None = None,
) -> CapabilityResult:
    """Build terminal CapabilityResult for the root durable invocation."""
    m = metrics or CapabilityMetrics(duration_ms=0.0, input_bytes=0, output_bytes=0)
    if status == "completed":
        return completed_result(
            user_text=user_text,
            structured_output=structured_output,
            metrics=m,
            terminal_output=True,
            needs_followup=False,
        )
    if status == "cancelled":
        return cancelled_result(metrics=m, safe_message="durable capability cancelled")
    return failed_result(
        error=__import__(
            "app.assistant.capabilities.contracts", fromlist=["CapabilityError"]
        ).CapabilityError(
            error_type="failed",
            safe_code="durable_capability_failed",
            safe_message=user_text or "durable capability failed",
            retry_disposition="never",
            target_identity=None,
            call_id=None,
            validation_issues=(),
        ),
        metrics=m,
        user_text=user_text,
        structured_output=structured_output,
    )


# ---------------------------------------------------------------------------
# Top-level resume unit (library entry used by worker hooks / tests)
# ---------------------------------------------------------------------------


def execute_interrupt_resume(
    db: Session,
    *,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    material: DurableFrameMaterial,
    child_materials: Mapping[str, DurableFrameMaterial] | None = None,
    provider_loop_continuation: ProviderLoopContinuation | None = None,
    provider_messages: Sequence[Any] | None = None,
    provider_manifest: Any | None = None,
    parent_ledger: Any | None = None,
    bag_snapshot: Mapping[str, Any] | None = None,
    pause_ttl_sec: int | None = None,
    max_boundaries: int = 64,
    commit_human_apply: bool = True,
    commit_continue: bool = True,
    crash_after_human_apply: bool = False,
    crash_before_human_apply: bool = False,
    expected_interrupt_id: UUID | None = None,
) -> ResumeUnitResult:
    """Claim-ready interrupt resume unit: verify → apply human once → continue.

    Every durable result CAS requires ``status=running`` + expected revision
    (Plan 06). Stop-first leaves the result unable to commit; caller observes
    ``stop_won``. Second pause reuses the original outer ContinuationRef and
    commits a new Interrupt via ``commit_durable_workflow_pause``. Root terminal
    builds one ``ProviderWaitingResolution`` when a ProviderLoopContinuation is
    supplied.
    """
    def _route_needs_reconciliation(
        *,
        reason_code: str,
        detail: str,
        human_result: HumanContinuationResult | None = None,
        root_continuation: ContinuationRef | None = None,
        workflow_state: DurableWorkflowStateV1 | None = None,
        revision: int | None = None,
    ) -> ResumeUnitResult:
        """CAS Run to needs_reconciliation when possible; always return the outcome."""
        routed_revision = revision if revision is not None else int(expected_revision)
        try:
            commit = route_irreconcilable_to_needs_reconciliation(
                db,
                run_id=run_id,
                lease=lease,
                expected_revision=routed_revision,
                reason_code=reason_code,
                detail=detail,
            )
            routed_revision = int(commit.state_revision)
        except DurableRunConflict as exc:
            # Stop/stale lease may prevent the CAS; still surface needs_reconciliation.
            code = getattr(exc, "code", None)
            if code not in {"invalid_source_status", "stale_revision", "lease_mismatch"}:
                raise
            return ResumeUnitResult(
                kind="needs_reconciliation",
                reason_code=reason_code,
                needs_reconciliation=True,
                detail=f"{detail}; route CAS failed: {exc}",
                human_result=human_result,
                root_continuation=root_continuation,
                workflow_state=workflow_state,
                state_revision=routed_revision,
            )
        return ResumeUnitResult(
            kind="needs_reconciliation",
            reason_code=reason_code,
            needs_reconciliation=True,
            detail=detail,
            human_result=human_result,
            root_continuation=root_continuation,
            workflow_state=workflow_state,
            state_revision=routed_revision,
        )

    try:
        ctx = load_resume_context(
            db,
            run_id=run_id,
            expected_interrupt_id=expected_interrupt_id,
        )
    except DurableResumeNeedsReconciliation as exc:
        return _route_needs_reconciliation(
            reason_code=exc.reason_code,
            detail=str(exc),
            revision=expected_revision,
        )
    except DurableResumeError as exc:
        if exc.needs_reconciliation:
            return _route_needs_reconciliation(
                reason_code=exc.reason_code,
                detail=str(exc),
                revision=expected_revision,
            )
        return ResumeUnitResult(
            kind="failed",
            reason_code=exc.reason_code,
            detail=str(exc),
        )

    # Explicit budget lineage check before adapter work.
    try:
        parent_payload = None
        if parent_ledger is not None:
            parent_payload = parent_ledger.model_dump(mode="json", by_alias=True)
        elif ctx.parent_budget_row.payload:
            parent_payload = dict(ctx.parent_budget_row.payload)
        verify_resolution_budget_lineage(
            suspension=ctx.suspension,
            parent_budget=ctx.parent_budget_row,
            child_budget=ctx.resolution_budget_row,
            parent_ledger_payload=parent_payload,
        )
    except DurableResumeError as exc:
        if exc.needs_reconciliation:
            return _route_needs_reconciliation(
                reason_code=exc.reason_code,
                detail=str(exc),
                human_result=ctx.human_result,
                root_continuation=ctx.root_continuation,
                workflow_state=ctx.workflow_state,
                revision=expected_revision,
            )
        return ResumeUnitResult(
            kind="failed",
            reason_code=exc.reason_code,
            detail=str(exc),
            human_result=ctx.human_result,
            root_continuation=ctx.root_continuation,
        )

    if crash_before_human_apply and not ctx.human_already_applied:
        # Test kill point: after verify, before node continuation commit.
        return ResumeUnitResult(
            kind="failed",
            reason_code="crash_before_human_apply",
            detail="injected crash before human apply",
            human_result=ctx.human_result,
            root_continuation=ctx.root_continuation,
            workflow_state=ctx.workflow_state,
            state_revision=expected_revision,
        )

    human_frame = next(
        (
            frame
            for frame in ctx.workflow_state.frame_stack
            if frame.frame_id == ctx.human_result.frame_id
        ),
        None,
    )
    if human_frame is None:
        return _route_needs_reconciliation(
            reason_code=CODE_RESUME_FRAME_MISMATCH,
            detail="interrupt-owning human frame is missing",
            human_result=ctx.human_result,
            root_continuation=ctx.root_continuation,
            workflow_state=ctx.workflow_state,
            revision=expected_revision,
        )
    human_material = _material_for_top(
        top=human_frame,
        material=material,
        child_materials=child_materials,
    )
    if (
        human_material.plan.target_version_id != human_frame.target_version_id
        or human_material.plan.plan_digest != human_frame.execution_plan_digest
    ):
        return _route_needs_reconciliation(
            reason_code=CODE_RESUME_FRAME_MISMATCH,
            detail="exact material for interrupt-owning frame is missing or drifted",
            human_result=ctx.human_result,
            root_continuation=ctx.root_continuation,
            workflow_state=ctx.workflow_state,
            revision=expected_revision,
        )

    # Build runner + bag (may restore snapshot from waiting material / post-apply Artifact).
    pause_port = WorkerUnitPauseEffectPort()
    runner = DurableWorkflowRunner(
        registry=build_default_registry(),
        pause_effect_port=pause_port,
    )
    # Prefer caller bag_snapshot, then recovered post-apply Artifact snapshot.
    effective_bag_snapshot = bag_snapshot if bag_snapshot is not None else ctx.bag_snapshot
    for fr in ctx.workflow_state.frame_stack:
        frame_material = _material_for_top(
            top=fr,
            material=material,
            child_materials=child_materials,
        )
        frame_snapshot = (
            effective_bag_snapshot
            if fr.frame_id == ctx.human_result.frame_id
            else _load_bag_snapshot_from_artifact(
                db,
                artifact_id=fr.node_state_artifact_id,
            )
        )
        if frame_snapshot is not None:
            runner.load_bag_snapshot(
                fr.frame_id,
                frame_snapshot,
                inputs=frame_material.inputs,
            )
        else:
            runner.get_bag(fr.frame_id, inputs=frame_material.inputs)

    # Also rehydrate via material.bag_snapshot pattern (Task 3) when provided.
    if effective_bag_snapshot is not None:
        # Ensure material carries snapshot so prepare_boundary can rehydrate too.
        if human_material.bag_snapshot is None:
            human_material = DurableFrameMaterial(
                plan=human_material.plan,
                node_configs=human_material.node_configs,
                inputs=human_material.inputs,
                bag_snapshot=effective_bag_snapshot,
            )

    revision = int(expected_revision)
    human_bag = runner.get_bag(ctx.human_result.frame_id)

    if ctx.human_already_applied:
        # Crash-after-apply recovery: frame already advanced, bag rehydrated.
        # Do not re-apply human result or re-commit the apply Checkpoint.
        new_state = ctx.workflow_state
        # Ensure bag still has human node output (idempotent inject if missing).
        if ctx.human_result.node_id not in human_bag.node_outputs:
            human_bag.set_output(
                ctx.human_result.node_id, ctx.human_result.as_bag_payload()
            )
            runner._bags[ctx.human_result.frame_id] = human_bag
    else:
        try:
            new_state, human_bag, _next = apply_human_result_once(
                state=ctx.workflow_state,
                human=ctx.human_result,
                plan=human_material.plan,
                bag=human_bag,
                interrupt_kind=str(ctx.interrupt.kind),
            )
        except DurableResumeError as exc:
            if exc.needs_reconciliation:
                return _route_needs_reconciliation(
                    reason_code=exc.reason_code,
                    detail=str(exc),
                    human_result=ctx.human_result,
                    root_continuation=ctx.root_continuation,
                    workflow_state=ctx.workflow_state,
                    revision=revision,
                )
            return ResumeUnitResult(
                kind="failed",
                reason_code=exc.reason_code,
                detail=str(exc),
                human_result=ctx.human_result,
                root_continuation=ctx.root_continuation,
            )

        runner._bags[ctx.human_result.frame_id] = human_bag

        # Persist bag_snapshot on post-apply Checkpoint (Task 3 pattern + Artifact).
        # Attach node_state_artifact_id on the advanced human frame so re-entry
        # can rehydrate without process-local runner._bags.
        bag_art = _build_bag_snapshot_artifact(
            run_id=run_id,
            frame_id=ctx.human_result.frame_id,
            interrupt_id=ctx.human_result.interrupt_id,
            bag=human_bag,
        )
        # Stamp node_state_artifact_id on the frame that owns the human node.
        stamped_stack = []
        for fr in new_state.frame_stack:
            if fr.frame_id == ctx.human_result.frame_id:
                stamped_stack.append(
                    _copy_frame(fr, node_state_artifact_id=bag_art.id)
                )
            else:
                stamped_stack.append(fr)
        new_state = DurableWorkflowStateV1(
            run_id=new_state.run_id,
            root_frame_id=new_state.root_frame_id,
            root_invocation_digest=new_state.root_invocation_digest,
            frame_stack=tuple(stamped_stack),
            pending_interrupt_id=None,
            terminal_output_artifact_id=new_state.terminal_output_artifact_id,
        )

        # Checkpoint human node output once (Plan 06 CAS: running + expected revision).
        # Resume-ready checkpoint has no inflight_unit; use commit_checkpoint_v2.
        if commit_human_apply:
            from app.assistant.durable.checkpoints import commit_checkpoint_v2

            try:
                applied = commit_checkpoint_v2(
                    db,
                    run_id=run_id,
                    lease=lease,
                    expected_revision=revision,
                    phase="dispatching_calls",
                    next_action_kind="continue_child",
                    unit=None,
                    workflow_state=new_state,
                    active_capability_continuation=ctx.root_continuation,
                    pending_interrupt_id=None,
                    budget_suspension=None,
                    reason=(
                        f"human_applied:{ctx.human_result.interrupt_id}:"
                        f"{ctx.human_result.node_visit_id}"
                    ),
                    artifact_ids=(bag_art.id,),
                    extra_child_rows=(bag_art,),
                )
                revision = int(applied.state_revision)
            except DurableRunConflict as exc:
                code = getattr(exc, "code", None)
                if code in {"invalid_source_status", "stale_revision", "lease_mismatch"}:
                    return ResumeUnitResult(
                        kind="stop_won",
                        reason_code=CODE_RESUME_STOP_WON,
                        detail=str(exc),
                        human_result=ctx.human_result,
                        root_continuation=ctx.root_continuation,
                        workflow_state=new_state,
                        state_revision=revision,
                    )
                raise

        if crash_after_human_apply:
            # Test kill point: after continuation node commit, before further boundaries.
            return ResumeUnitResult(
                kind="human_applied",
                reason_code="crash_after_human_apply",
                detail="injected crash after human apply commit",
                human_result=ctx.human_result,
                root_continuation=ctx.root_continuation,
                workflow_state=new_state,
                state_revision=revision,
                applied_node_visit_id=ctx.human_result.node_visit_id,
            )

    # If human apply cancelled the root, short-circuit.
    top = new_state.frame_stack[-1] if new_state.frame_stack else None
    if top is not None and top.phase == "cancelled" and top.parent_frame_id is None:
        cap = capability_result_from_root(status="cancelled")
        resolution = None
        if provider_loop_continuation is not None:
            try:
                resolution = build_provider_waiting_resolution(
                    provider_loop_continuation=provider_loop_continuation,
                    root_continuation=ctx.root_continuation,
                    capability_result=cap,
                )
                if provider_manifest is not None and provider_messages is not None:
                    validate_provider_waiting_resume(
                        manifest=provider_manifest,
                        messages=provider_messages,
                        continuation=provider_loop_continuation,
                        resolved_waiting=resolution,
                    )
            except DurableResumeError as exc:
                if exc.needs_reconciliation:
                    return _route_needs_reconciliation(
                        reason_code=exc.reason_code,
                        detail=str(exc),
                        human_result=ctx.human_result,
                        root_continuation=ctx.root_continuation,
                        workflow_state=new_state,
                        revision=revision,
                    )
                return ResumeUnitResult(
                    kind="failed",
                    reason_code=exc.reason_code,
                    detail=str(exc),
                    human_result=ctx.human_result,
                    root_continuation=ctx.root_continuation,
                    workflow_state=new_state,
                    state_revision=revision,
                )
        return ResumeUnitResult(
            kind="root_terminal",
            workflow_state=new_state,
            human_result=ctx.human_result,
            root_continuation=ctx.root_continuation,
            provider_waiting_resolution=resolution,
            capability_result=cap,
            state_revision=revision,
            applied_node_visit_id=ctx.human_result.node_visit_id,
        )

    # Continue boundaries.
    cont = continue_child_until_boundary(
        runner=runner,
        state=new_state,
        material=material,
        child_materials=child_materials,
        max_boundaries=max_boundaries,
    )

    if cont.kind == "second_pause":
        if cont.pause_proposal is None:
            return ResumeUnitResult(
                kind="failed",
                reason_code=CODE_RESUME_PROTOCOL_ERROR,
                detail="second_pause missing proposal",
                human_result=ctx.human_result,
                root_continuation=ctx.root_continuation,
                workflow_state=cont.workflow_state,
                state_revision=revision,
            )
        # Outer ContinuationRef must stay unchanged across sequential pauses.
        if not _continuation_equal(
            cont.pause_proposal.root_continuation, ctx.root_continuation
        ):
            return _route_needs_reconciliation(
                reason_code=CODE_RESUME_CONTINUATION_MISMATCH,
                detail="second pause changed outer ContinuationRef",
                human_result=ctx.human_result,
                root_continuation=ctx.root_continuation,
                workflow_state=cont.workflow_state,
                revision=revision,
            )
        pause_result = None
        if commit_continue:
            try:
                # Second pause must suspend the *current* active budget (the
                # first resume child), not the original pre-wait parent ledger
                # passed into the first resume unit.
                pause_result = commit_durable_workflow_pause(
                    db,
                    run_id=run_id,
                    lease=lease,
                    expected_revision=revision,
                    proposal=cont.pause_proposal,
                    parent_ledger=None,
                    ttl_sec=pause_ttl_sec,
                    reason="second_human_pause",
                    # Obligation digests include interrupt_id + node_visit_id so
                    # multi-pause wait obligations remain unique per Run.
                    add_wait_obligation=True,
                )
                revision = int(pause_result.commit.state_revision)
            except DurableRunConflict as exc:
                code = getattr(exc, "code", None)
                if code in {"invalid_source_status", "stale_revision", "lease_mismatch"}:
                    return ResumeUnitResult(
                        kind="stop_won",
                        reason_code=CODE_RESUME_STOP_WON,
                        detail=str(exc),
                        human_result=ctx.human_result,
                        root_continuation=ctx.root_continuation,
                        workflow_state=cont.workflow_state,
                        state_revision=revision,
                    )
                raise
            except DurablePauseProtocolError as exc:
                return ResumeUnitResult(
                    kind="failed",
                    reason_code=getattr(exc, "reason_code", CODE_RESUME_PROTOCOL_ERROR),
                    detail=str(exc),
                    human_result=ctx.human_result,
                    root_continuation=ctx.root_continuation,
                    workflow_state=cont.workflow_state,
                    state_revision=revision,
                )
        return ResumeUnitResult(
            kind="second_pause",
            workflow_state=cont.workflow_state,
            human_result=ctx.human_result,
            root_continuation=ctx.root_continuation,
            pause_commit=pause_result,
            state_revision=revision,
            applied_node_visit_id=ctx.human_result.node_visit_id,
        )

    if cont.kind in {"root_terminal", "cancelled"}:
        status = cont.capability_status or (
            "cancelled" if cont.kind == "cancelled" else "completed"
        )
        cap = capability_result_from_root(
            status=status,  # type: ignore[arg-type]
            user_text=cont.user_text,
            structured_output=cont.structured_output,
        )
        if commit_continue:
            from app.assistant.durable.checkpoints import commit_checkpoint_v2

            try:
                terminal_commit = commit_checkpoint_v2(
                    db,
                    run_id=run_id,
                    lease=lease,
                    expected_revision=revision,
                    phase="ready_for_completion",
                    next_action_kind="resume_provider_loop",
                    unit=None,
                    workflow_state=cont.workflow_state,
                    active_capability_continuation=ctx.root_continuation,
                    pending_interrupt_id=None,
                    reason=f"root_terminal:{status}",
                )
                revision = int(terminal_commit.state_revision)
            except DurableRunConflict as exc:
                code = getattr(exc, "code", None)
                if code in {"invalid_source_status", "stale_revision", "lease_mismatch"}:
                    return ResumeUnitResult(
                        kind="stop_won",
                        reason_code=CODE_RESUME_STOP_WON,
                        detail=str(exc),
                        human_result=ctx.human_result,
                        root_continuation=ctx.root_continuation,
                        workflow_state=cont.workflow_state,
                        capability_result=cap,
                        state_revision=revision,
                    )
                raise

        resolution = None
        if provider_loop_continuation is not None:
            try:
                resolution = build_provider_waiting_resolution(
                    provider_loop_continuation=provider_loop_continuation,
                    root_continuation=ctx.root_continuation,
                    capability_result=cap,
                )
                if provider_manifest is not None and provider_messages is not None:
                    validate_provider_waiting_resume(
                        manifest=provider_manifest,
                        messages=provider_messages,
                        continuation=provider_loop_continuation,
                        resolved_waiting=resolution,
                    )
            except DurableResumeError as exc:
                if exc.needs_reconciliation:
                    return _route_needs_reconciliation(
                        reason_code=exc.reason_code,
                        detail=str(exc),
                        human_result=ctx.human_result,
                        root_continuation=ctx.root_continuation,
                        workflow_state=cont.workflow_state,
                        revision=revision,
                    )
                return ResumeUnitResult(
                    kind="failed",
                    reason_code=exc.reason_code,
                    detail=str(exc),
                    human_result=ctx.human_result,
                    root_continuation=ctx.root_continuation,
                    workflow_state=cont.workflow_state,
                    capability_result=cap,
                    state_revision=revision,
                )

        return ResumeUnitResult(
            kind="root_terminal",
            workflow_state=cont.workflow_state,
            human_result=ctx.human_result,
            root_continuation=ctx.root_continuation,
            provider_waiting_resolution=resolution,
            capability_result=cap,
            state_revision=revision,
            applied_node_visit_id=ctx.human_result.node_visit_id,
        )

    return ResumeUnitResult(
        kind="failed",
        reason_code=cont.reason_code or CODE_RESUME_PROTOCOL_ERROR,
        detail=cont.reason_code,
        human_result=ctx.human_result,
        root_continuation=ctx.root_continuation,
        workflow_state=cont.workflow_state,
        state_revision=revision,
        applied_node_visit_id=ctx.human_result.node_visit_id,
    )


def route_irreconcilable_to_needs_reconciliation(
    db: Session,
    *,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    reason_code: str,
    detail: str | None = None,
) -> Any:
    """CAS Run into needs_reconciliation for irreconcilable drift."""
    repo = DurableRunRepository(db)
    return repo.commit_running_result(
        run_id=run_id,
        lease=lease,
        expected_revision=expected_revision,
        target_status="needs_reconciliation",
        failure_code=reason_code,
        error_message=detail,
    )


__all__ = [
    "CODE_RESUME_ALREADY_APPLIED",
    "CODE_RESUME_BUDGET_MISMATCH",
    "CODE_RESUME_CONTINUATION_MISMATCH",
    "CODE_RESUME_FRAME_MISMATCH",
    "CODE_RESUME_INTERRUPT_NOT_RESOLVED",
    "CODE_RESUME_LINEAGE_MISMATCH",
    "CODE_RESUME_NEEDS_RECONCILIATION",
    "CODE_RESUME_PROTOCOL_ERROR",
    "CODE_RESUME_STALE_EVENT",
    "CODE_RESUME_STOP_WON",
    "CODE_RESUME_SURFACE_TAMPERED",
    "ContinueBoundaryOutcome",
    "DurableResumeError",
    "DurableResumeNeedsReconciliation",
    "HumanContinuationResult",
    "LoadedResumeContext",
    "ResumeUnitResult",
    "apply_human_result_once",
    "build_human_continuation_result",
    "build_provider_waiting_resolution",
    "capability_result_from_root",
    "continue_child_until_boundary",
    "execute_interrupt_resume",
    "load_resume_context",
    "route_irreconcilable_to_needs_reconciliation",
    "select_human_successor",
    "validate_provider_waiting_resume",
    "verify_resolution_budget_lineage",
]

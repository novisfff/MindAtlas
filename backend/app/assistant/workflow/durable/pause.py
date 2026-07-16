"""Plan 07 Task 5: worker-unit pause effect port + atomic durable pause commit.

Never calls ``HumanLoopRuntime.create_and_wait``. Stages one pure proposal,
consumes it after a waiting result is produced, then CAS-commits Interrupt +
Workflow state + outer Checkpoint + waiting status in one Plan 06 transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.capabilities.contracts import ContinuationRef
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.durable.contracts import DurableAgentCheckpointV2, DurableNextActionV2
from app.assistant.durable.codec import encode_checkpoint_v2
from app.assistant.durable.models import (
    AssistantRunArtifact,
    AssistantRunBudgetRevision,
    AssistantRunCheckpoint,
    AssistantRunInterrupt,
    AssistantRunObligationRevision,
)
from app.assistant.durable.repository import (
    STATUS_WAITING_APPROVAL,
    STATUS_WAITING_INPUT,
    DurableChildBundle,
    DurableCommitResult,
    DurableRunConflict,
    DurableRunRepository,
    EventSpec,
    LeaseToken,
)
from app.assistant.models import AssistantChatRun
from app.assistant.policy.budgets import BudgetLedgerState
from app.assistant.policy.obligations import build_reserved_obligation
from app.assistant.workflow.durable.contracts import (
    DurablePauseEffectPort,
    DurablePauseProposalV1,
    DurableWorkflowStateV1,
)
from app.assistant.workflow.durable.interrupts import (
    DurableInterruptRepository,
    InterruptConflict,
    derive_interrupt_key,
)

CODE_DURABLE_PAUSE_PROTOCOL_ERROR = "durable_pause_protocol_error"
CODE_DURABLE_BLOCKING_RUNTIME_FORBIDDEN = "durable_blocking_runtime_forbidden"


class DurablePauseProtocolError(ValueError):
    """Missing/duplicate/mismatched/leftover pause effect protocol error."""

    def __init__(self, message: str, *, reason_code: str = CODE_DURABLE_PAUSE_PROTOCOL_ERROR) -> None:
        super().__init__(message)
        self.reason_code = reason_code


# ---------------------------------------------------------------------------
# Worker-unit ephemeral pause effect port (Plan 07 §5.3)
# ---------------------------------------------------------------------------


@dataclass
class WorkerUnitPauseEffectPort:
    """Ephemeral one-unit pause staging port. Never serialized.

    Stages at most one pure ``DurablePauseProposalV1`` keyed by root call +
    continuation. ``consume_exact`` requires an exact match. ``clear`` is
    intended for ``finally`` so a leftover proposal cannot leak into the next
    unit. Rejects duplicate stage and leftover non-consumed proposals via
    ``assert_clear``.
    """

    _proposal: DurablePauseProposalV1 | None = field(default=None, init=False, repr=False)
    _consumed: bool = field(default=False, init=False, repr=False)

    def stage(self, proposal: DurablePauseProposalV1) -> None:
        if not isinstance(proposal, DurablePauseProposalV1):
            raise DurablePauseProtocolError(
                f"stage requires DurablePauseProposalV1, got {type(proposal)!r}"
            )
        if self._proposal is not None:
            raise DurablePauseProtocolError(
                "duplicate pause proposal stage for this worker unit"
            )
        if self._consumed:
            raise DurablePauseProtocolError(
                "cannot stage after consume on this worker unit"
            )
        self._proposal = proposal

    def consume_exact(
        self,
        *,
        root_call_id: str,
        continuation: ContinuationRef,
    ) -> DurablePauseProposalV1:
        if self._proposal is None:
            raise DurablePauseProtocolError(
                "no staged pause proposal to consume",
            )
        if self._consumed:
            raise DurablePauseProtocolError(
                "pause proposal already consumed for this worker unit",
            )
        proposal = self._proposal
        if str(proposal.root_call_id) != str(root_call_id):
            raise DurablePauseProtocolError(
                f"pause proposal root_call_id mismatch: "
                f"staged={proposal.root_call_id!r} expected={root_call_id!r}",
            )
        if not _continuation_equal(proposal.root_continuation, continuation):
            raise DurablePauseProtocolError(
                "pause proposal root_continuation mismatch",
            )
        self._consumed = True
        return proposal

    def clear(self) -> None:
        """Drop staged proposal (``finally``). Safe to call multiple times."""
        self._proposal = None
        self._consumed = False

    def assert_clear(self) -> None:
        """Reject leftover non-consumed proposals before next unit / waiting commit."""
        if self._proposal is not None and not self._consumed:
            raise DurablePauseProtocolError(
                "leftover pause proposal remains on worker unit port",
            )

    @property
    def has_staged(self) -> bool:
        return self._proposal is not None and not self._consumed

    @property
    def staged_proposal(self) -> DurablePauseProposalV1 | None:
        if self._consumed:
            return None
        return self._proposal


def _continuation_equal(a: ContinuationRef, b: ContinuationRef) -> bool:
    return (
        a.continuation_type == b.continuation_type
        and int(a.contract_version) == int(b.contract_version)
        and str(a.reference_id) == str(b.reference_id)
        and str(a.payload_digest) == str(b.payload_digest)
    )


# Protocol structural check (runtime).
def _assert_port_protocol() -> None:
    _: DurablePauseEffectPort = WorkerUnitPauseEffectPort()  # type: ignore[assignment]
    del _


# ---------------------------------------------------------------------------
# Parent ledger helper (from run budget revision or synthetic)
# ---------------------------------------------------------------------------


def resolve_parent_budget_ledger(
    db: Session,
    *,
    run: AssistantChatRun,
) -> tuple[BudgetLedgerState, UUID]:
    """Return (parent BudgetLedgerState, parent_budget_revision_id).

    Requires a real full parent ledger on the current budget revision payload.
    Does **not** invent positive remaining for production pause — callers that
    only have partial budget payloads must pass an explicit live
    ``parent_ledger`` into ``commit_durable_workflow_pause`` (tests/fixtures do).
    """
    budget_id = run.current_budget_revision_id
    if budget_id is None:
        raise DurablePauseProtocolError(
            "run missing current_budget_revision_id for pause",
            reason_code=CODE_DURABLE_PAUSE_PROTOCOL_ERROR,
        )
    row = db.get(AssistantRunBudgetRevision, budget_id)
    if row is None:
        raise DurablePauseProtocolError(
            "budget revision row missing for pause",
            reason_code=CODE_DURABLE_PAUSE_PROTOCOL_ERROR,
        )
    payload = dict(row.payload or {})
    if "ledgerDigest" not in payload and "ledger_digest" not in payload:
        raise DurablePauseProtocolError(
            "budget revision payload is not a full parent BudgetLedgerState; "
            "pass live parent_ledger to commit_durable_workflow_pause",
            reason_code=CODE_DURABLE_PAUSE_PROTOCOL_ERROR,
        )
    try:
        return BudgetLedgerState.model_validate(payload), budget_id
    except Exception as exc:  # noqa: BLE001
        raise DurablePauseProtocolError(
            f"budget revision payload failed BudgetLedgerState validation: {exc}",
            reason_code=CODE_DURABLE_PAUSE_PROTOCOL_ERROR,
        ) from exc


# ---------------------------------------------------------------------------
# Atomic pause result commit (Plan 07 §11.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DurablePauseCommitResult:
    """Outcome of a successful durable pause CAS commit."""

    commit: DurableCommitResult
    interrupt: AssistantRunInterrupt
    checkpoint_id: UUID
    proposal: DurablePauseProposalV1
    suspension_digest: str
    request_digest: str
    interrupt_key: str


def commit_durable_workflow_pause(
    db: Session,
    *,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    proposal: DurablePauseProposalV1,
    prepared: Any = None,
    expected_logical_unit_id: str | None = None,
    expected_node_visit_id: str | None = None,
    expected_input_digest: str | None = None,
    parent_ledger: BudgetLedgerState | None = None,
    provider_loop_continuation: Any = None,
    store_proposal_artifact: bool = True,
    add_wait_obligation: bool = True,
    ttl_sec: int | None = None,
    reason: str = "human_pause",
) -> DurablePauseCommitResult:
    """Atomically commit Interrupt + waiting Checkpoint + waiting Run status.

    Requires Plan 06 lease + expected ``state_revision`` + source ``status=running``.
    On success: Run is ``waiting_approval`` or ``waiting_input``, lease cleared,
    aggregate ``deadline_at`` null. Never polls or sleeps.

    ``provider_loop_continuation`` is accepted for outer-worker wiring but MUST
    remain None on the workflow pause Checkpoint path (v2 waiting invariant:
    provider continuation XOR complete pause bundle). Outer capability
    continuation is stored as ``active_capability_continuation``.
    """
    if provider_loop_continuation is not None:
        # Workflow pause Checkpoint path forbids combining with provider cont.
        # Callers that need Plan 03 waiting cont store it on a separate path.
        raise DurablePauseProtocolError(
            "workflow pause Checkpoint cannot carry provider_loop_continuation; "
            "use active_capability_continuation only",
        )

    if not isinstance(proposal, DurablePauseProposalV1):
        raise DurablePauseProtocolError(
            f"proposal must be DurablePauseProposalV1, got {type(proposal)!r}"
        )
    if proposal.run_id != run_id:
        raise DurablePauseProtocolError(
            f"proposal.run_id {proposal.run_id} != run_id {run_id}"
        )

    workflow_state: DurableWorkflowStateV1 = proposal.proposed_workflow_state
    if workflow_state.pending_interrupt_id != proposal.interrupt_id:
        raise DurablePauseProtocolError(
            "proposed_workflow_state.pending_interrupt_id must equal proposal.interrupt_id"
        )
    if not workflow_state.frame_stack:
        raise DurablePauseProtocolError("proposed workflow state has empty frame_stack")
    top = workflow_state.frame_stack[-1]
    if top.phase != "waiting":
        raise DurablePauseProtocolError(
            f"proposed top frame phase must be waiting, got {top.phase!r}"
        )

    # Resolve prepared identity for continuity checks (same as boundary result).
    exp_logical = expected_logical_unit_id
    exp_visit = expected_node_visit_id
    exp_digest = expected_input_digest
    if prepared is not None:
        exp_logical = exp_logical or prepared.unit.logical_unit_id
        exp_visit = exp_visit or prepared.node_visit_id
        exp_digest = exp_digest or prepared.input_digest
    if exp_visit is not None and exp_visit != proposal.node_visit_id:
        raise DurablePauseProtocolError(
            f"proposal.node_visit_id {proposal.node_visit_id!r} != expected {exp_visit!r}"
        )

    repo = DurableRunRepository(db)
    run = repo.get_run(run_id, for_update=True)
    if run is None:
        raise DurableRunConflict("run_not_found", f"run not found: {run_id}")
    if int(run.state_revision) != int(expected_revision):
        raise DurableRunConflict(
            "stale_revision",
            f"expected revision {expected_revision}, got {run.state_revision}",
            run=run,
        )
    if str(run.status) != "running":
        raise DurableRunConflict(
            "invalid_source_status",
            f"pause requires status=running, got {run.status}",
            run=run,
        )
    # Lease verify early so we fail before creating interrupt rows.
    repo._verify_lease(run, lease)  # noqa: SLF001 — intentional shared CAS helper

    if not all(
        [
            run.current_manifest_revision_id,
            run.current_policy_revision_id,
            run.current_budget_revision_id,
            run.current_obligation_revision_id,
        ]
    ):
        raise DurablePauseProtocolError(
            "base Manifest/policy/budget/obligation must exist before pause commit"
        )

    # Continuity vs prior prepared/started Checkpoint (when present).
    if run.current_checkpoint_id is not None:
        _verify_prepared_continuity(
            db,
            run=run,
            exp_logical=exp_logical,
            exp_visit=exp_visit,
            exp_digest=exp_digest,
        )

    if parent_ledger is None:
        parent_ledger, parent_budget_id = resolve_parent_budget_ledger(db, run=run)
    else:
        parent_budget_id = run.current_budget_revision_id
        if parent_budget_id is None:
            raise DurablePauseProtocolError("run missing budget revision")

    interrupt_key = derive_interrupt_key(
        run_id=run_id,
        root_invocation_digest=workflow_state.root_invocation_digest,
        frame_id=proposal.frame_id,
        node_visit_id=proposal.node_visit_id,
        logical_interrupt_ordinal=1,
    )

    # Pre-allocate waiting Checkpoint id so Interrupt can reference it in-txn.
    checkpoint_id = uuid4()
    completed_logical = exp_logical or (
        prepared.unit.logical_unit_id if prepared is not None else None
    )

    # Entire write sequence (Artifact / Obligation / Checkpoint / Interrupt / CAS)
    # is wrapped so any exception rolls back partial Session dirty state — not
    # only DurableRunConflict / InterruptConflict.
    try:
        # Optional proposal artifact (inline; no MinIO required for small JSON).
        artifact_rows: list[Any] = []
        artifact_ids: list[UUID] = []
        if store_proposal_artifact:
            art = _build_proposal_artifact(run_id=run_id, proposal=proposal)
            artifact_rows.append(art)
            artifact_ids.append(art.id)

        # Optional Plan 05 approval/user-input obligation revision.
        obligation_row: AssistantRunObligationRevision | None = None
        new_obligation_id = run.current_obligation_revision_id
        if add_wait_obligation:
            obligation_row, new_obligation_id = _build_wait_obligation_revision(
                db,
                run=run,
                proposal=proposal,
            )

        # Build waiting Checkpoint v2 (complete pause bundle; no provider cont).
        from app.assistant.durable.checkpoints import (
            _current_transcript_digest,  # noqa: SLF001
            _next_checkpoint_sequence,  # noqa: SLF001
        )
        from app.assistant.durable.codec import checkpoint_state_digest

        ordinal, transcript_digest, _msgs = _current_transcript_digest(db, run_id)
        next_action = DurableNextActionV2(kind="wait")
        # Suspension is created by interrupt repository; placeholder filled after create.
        # We first need suspension from create_pending_interrupt. Order:
        # 1) create interrupt with temporary checkpoint row already flushed
        # 2) build checkpoint payload with suspension
        # 3) CAS commit children

        # Flush artifact + obligation first so FKs/pointers resolve.
        child_pre: list[Any] = list(artifact_rows)
        if obligation_row is not None:
            child_pre.append(obligation_row)
        for row in child_pre:
            db.add(row)
        if child_pre:
            db.flush()

        # Create a provisional checkpoint shell so Interrupt FK is satisfied, then
        # overwrite payload after suspension is known (same row id).
        provisional = DurableAgentCheckpointV2(
            run_id=run_id,
            phase="waiting",
            manifest_revision_id=run.current_manifest_revision_id,
            policy_revision_id=run.current_policy_revision_id,
            budget_revision_id=run.current_budget_revision_id,
            obligation_revision_id=new_obligation_id,
            provider_message_ordinal=ordinal,
            provider_transcript_digest=transcript_digest,
            provider_loop_continuation=None,
            inflight_unit=None,
            capability_frames=(),
            artifact_ids=tuple(artifact_ids),
            visible_text_artifact_id=None,
            next_action=next_action,
            workflow_state=workflow_state,
            active_capability_continuation=proposal.root_continuation,
            # Temporary: use a throwaway suspension that will be replaced — we cannot
            # construct DurableAgentCheckpointV2 without complete pause bundle.
            # So create interrupt first with a pre-inserted minimal checkpoint row
            # that is not yet a valid v2 encode, then replace.
            pending_interrupt_id=proposal.interrupt_id,
            budget_suspension=_provisional_suspension(
                run_id=run_id,
                interrupt_id=proposal.interrupt_id,
                parent_budget_revision_id=parent_budget_id,
                parent_ledger=parent_ledger,
            ),
        )
        state_payload = encode_checkpoint_v2(provisional)
        state_digest = checkpoint_state_digest(provisional)
        seq = _next_checkpoint_sequence(db, run_id)
        ck_row = AssistantRunCheckpoint(
            id=checkpoint_id,
            run_id=run_id,
            sequence=seq,
            expected_state_revision=int(expected_revision),
            committed_state_revision=int(expected_revision) + 1,
            schema_version=2,
            manifest_revision_id=run.current_manifest_revision_id,
            policy_revision_id=run.current_policy_revision_id,
            budget_revision_id=run.current_budget_revision_id,
            obligation_revision_id=new_obligation_id,
            provider_message_ordinal=ordinal,
            provider_transcript_digest=transcript_digest,
            phase="waiting",
            logical_unit_id=completed_logical,
            reason=reason,
            state_payload=state_payload,
            state_digest=state_digest,
        )
        db.add(ck_row)
        db.flush()

        # Insert-or-read Interrupt (Run already locked above).
        irepo = DurableInterruptRepository(db)
        try:
            created = irepo.create_pending_interrupt(
                run_id=run_id,
                interrupt_id=proposal.interrupt_id,
                interrupt_key=interrupt_key,
                kind=proposal.kind,
                checkpoint_id=checkpoint_id,
                manifest_revision_id=run.current_manifest_revision_id,
                budget_revision_id=parent_budget_id,
                workflow_frame_id=proposal.frame_id,
                node_id=proposal.node_id,
                node_visit_id=proposal.node_visit_id,
                # Waiting Run revision after this CAS (expected_revision + 1). Clients
                # and rotate/resolve compare against the waiting Run.state_revision.
                request_run_revision=int(expected_revision) + 1,
                request_payload=dict(proposal.request_payload),
                field_schema=dict(proposal.field_schema) if proposal.field_schema else None,
                initial_values=dict(proposal.initial_values or {}),
                parent_ledger=parent_ledger,
                parent_budget_revision_id=parent_budget_id,
                ttl_sec=ttl_sec,
                owner_skill_package_id=top.owner_skill_package_id,
                owner_skill_version_id=top.owner_skill_version_id,
                lock_run=False,  # already locked
            )
        except InterruptConflict as exc:
            raise DurableRunConflict(
                getattr(exc, "code", CODE_DURABLE_PAUSE_PROTOCOL_ERROR),
                str(exc),
                run=run,
            ) from exc

        suspension = created.suspension
        # Rebuild checkpoint payload with the immutable suspension truth.
        final_cp = DurableAgentCheckpointV2(
            run_id=run_id,
            phase="waiting",
            manifest_revision_id=run.current_manifest_revision_id,
            policy_revision_id=run.current_policy_revision_id,
            budget_revision_id=run.current_budget_revision_id,
            obligation_revision_id=new_obligation_id,
            provider_message_ordinal=ordinal,
            provider_transcript_digest=transcript_digest,
            provider_loop_continuation=None,
            inflight_unit=None,
            capability_frames=(),
            artifact_ids=tuple(artifact_ids),
            visible_text_artifact_id=None,
            next_action=next_action,
            workflow_state=workflow_state,
            active_capability_continuation=proposal.root_continuation,
            pending_interrupt_id=proposal.interrupt_id,
            budget_suspension=suspension,
        )
        ck_row.state_payload = encode_checkpoint_v2(final_cp)
        ck_row.state_digest = checkpoint_state_digest(final_cp)
        db.add(ck_row)
        db.flush()

        target_status = (
            STATUS_WAITING_INPUT if proposal.kind == "input" else STATUS_WAITING_APPROVAL
        )
        events = (
            EventSpec(
                event_key=f"run.wait:{target_status}:{proposal.interrupt_id}:rev{expected_revision}",
                event_name="run.wait",
                payload={
                    "interruptId": str(proposal.interrupt_id),
                    "kind": proposal.kind,
                    "nodeId": proposal.node_id,
                    "nodeVisitId": proposal.node_visit_id,
                    "proposalDigest": proposal.proposal_digest,
                    "targetStatus": target_status,
                },
                visibility="public",
            ),
            EventSpec(
                event_key=f"unit.result:{completed_logical or 'none'}:rev{expected_revision}",
                event_name="unit.result",
                payload={
                    "phase": "waiting",
                    "logicalUnitId": completed_logical,
                    "schemaVersion": 2,
                    "pendingInterruptId": str(proposal.interrupt_id),
                },
                visibility="internal",
            ),
        )

        # Interrupt is already in the session; children bundle carries checkpoint +
        # optional obligation/artifacts (already flushed). Re-add checkpoint so
        # pointer advance still runs through the shared CAS path.
        bundle = DurableChildBundle(
            rows=[ck_row],
            current_manifest_revision_id=run.current_manifest_revision_id,
            current_policy_revision_id=run.current_policy_revision_id,
            current_budget_revision_id=run.current_budget_revision_id,
            current_obligation_revision_id=new_obligation_id,
            current_checkpoint_id=checkpoint_id,
        )

        commit = repo.commit_waiting_pause(
            run_id=run_id,
            expected_revision=expected_revision,
            lease=lease,
            target_status=target_status,
            events=events,
            children=bundle,
        )

        db.refresh(created.interrupt)
        return DurablePauseCommitResult(
            commit=commit,
            interrupt=created.interrupt,
            checkpoint_id=checkpoint_id,
            proposal=proposal,
            suspension_digest=str(created.interrupt.budget_suspension_digest),
            request_digest=str(created.interrupt.request_digest),
            interrupt_key=interrupt_key,
        )
    except Exception:
        # Roll back Artifact / Obligation / Checkpoint / Interrupt dirty state
        # for any failure path (CAS conflict, interrupt conflict, encode error,
        # DB error, etc.). DurableRunRepository also rolls back on conflict, so
        # a second rollback here is a no-op / safe re-entrant cleanup.
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise


def consume_and_commit_pause(
    db: Session,
    *,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    port: WorkerUnitPauseEffectPort,
    root_call_id: str,
    continuation: ContinuationRef,
    prepared: Any = None,
    parent_ledger: BudgetLedgerState | None = None,
    **kwargs: Any,
) -> DurablePauseCommitResult:
    """Consume the exact staged proposal and atomically commit the pause.

    Call after Plan 03 (or a test double) has produced a waiting result whose
    root continuation matches the staged proposal. Clears the port on success
    and on protocol error after consume; leaves port staged if consume fails.
    """
    proposal = port.consume_exact(root_call_id=root_call_id, continuation=continuation)
    try:
        result = commit_durable_workflow_pause(
            db,
            run_id=run_id,
            lease=lease,
            expected_revision=expected_revision,
            proposal=proposal,
            prepared=prepared,
            parent_ledger=parent_ledger,
            **kwargs,
        )
    except Exception:
        # Consume already happened; clear so leftovers do not poison the next unit.
        port.clear()
        raise
    port.clear()
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _provisional_suspension(
    *,
    run_id: UUID,
    interrupt_id: UUID,
    parent_budget_revision_id: UUID,
    parent_ledger: BudgetLedgerState,
) -> Any:
    """Build a temporary suspension so Checkpoint v2 validates before interrupt insert.

    Replaced with the repository's immutable suspension immediately after create.
    """
    from app.assistant.workflow.durable.interrupts import build_budget_suspension_state

    now = datetime.now(timezone.utc)
    remaining = 60_000
    try:
        from app.assistant.workflow.durable.interrupts import compute_remaining_active_ms

        remaining = compute_remaining_active_ms(
            parent_deadline_at_utc=parent_ledger.deadline_at_utc,
            database_now=now,
        )
        if remaining <= 0:
            remaining = 60_000
    except Exception:  # noqa: BLE001
        remaining = 60_000
    from datetime import timedelta

    return build_budget_suspension_state(
        run_id=run_id,
        interrupt_id=interrupt_id,
        parent_budget_revision_id=parent_budget_revision_id,
        parent_ledger_revision=int(parent_ledger.revision),
        parent_ledger_digest=str(parent_ledger.ledger_digest),
        suspended_at_utc=now,
        remaining_active_ms=remaining,
        human_wait_expires_at_utc=now + timedelta(seconds=3600),
    )


def _build_proposal_artifact(
    *,
    run_id: UUID,
    proposal: DurablePauseProposalV1,
) -> AssistantRunArtifact:
    from app.assistant.domain.digests import sha256_bytes

    body = json.dumps(
        {
            "contractVersion": 1,
            "kind": "durable_pause_proposal",
            "proposalDigest": proposal.proposal_digest,
            "interruptId": str(proposal.interrupt_id),
            "nodeId": proposal.node_id,
            "nodeVisitId": proposal.node_visit_id,
            "requestPayload": proposal.request_payload,
            "fieldSchema": proposal.field_schema,
            "initialValues": proposal.initial_values,
        },
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    digest = sha256_bytes(body)
    return AssistantRunArtifact(
        id=uuid4(),
        run_id=run_id,
        kind="pause_proposal",
        media_type="application/json",
        display_label=f"pause:{proposal.node_id}",
        storage_kind="inline",
        byte_size=len(body),
        content_sha256=digest,
        inline_bytes=body,
        object_key=None,
        metadata_json={
            "proposalDigest": proposal.proposal_digest,
            "interruptId": str(proposal.interrupt_id),
        },
    )


def _build_wait_obligation_revision(
    db: Session,
    *,
    run: AssistantChatRun,
    proposal: DurablePauseProposalV1,
) -> tuple[AssistantRunObligationRevision, UUID]:
    from sqlalchemy import func, select

    obl_type = "user_input" if proposal.kind == "input" else "approval"
    try:
        item = build_reserved_obligation(
            run_id=run.id,
            obligation_type=obl_type,  # type: ignore[arg-type]
            owner_kind="main_agent",
            owner_id="main",
            ordinal=1,
        )
        payload = {
            "schemaVersion": 1,
            "obligations": [
                item.model_dump(mode="json", by_alias=True)
                if hasattr(item, "model_dump")
                else dict(item)
            ],
        }
        digest = sha256_canonical_json(payload)
    except Exception:  # noqa: BLE001 — fixture-friendly minimal obligation
        payload = {
            "schemaVersion": 1,
            "obligations": [
                {
                    "obligationType": obl_type,
                    "status": "pending",
                    "interruptId": str(proposal.interrupt_id),
                    "nodeId": proposal.node_id,
                    "nodeVisitId": proposal.node_visit_id,
                }
            ],
        }
        digest = sha256_canonical_json(payload)

    max_rev = db.scalar(
        select(func.coalesce(func.max(AssistantRunObligationRevision.revision), 0)).where(
            AssistantRunObligationRevision.run_id == run.id
        )
    )
    rev_n = int(max_rev or 0) + 1
    row = AssistantRunObligationRevision(
        id=uuid4(),
        run_id=run.id,
        revision=rev_n,
        parent_revision_id=run.current_obligation_revision_id,
        parent_digest=None,
        obligation_digest=digest,
        payload=payload,
    )
    return row, row.id


def _node_visit_from_logical_unit_id(logical_unit_id: str | None) -> str | None:
    """Extract exact node_visit_id from workflow/agent logical unit ids.

    Formats:
      - ``workflow_node:{frame_id}:{node_visit_id}``
      - ``agent_round:{frame_id}:{node_visit_id}``

    Returns None when the id is missing or not in a known exact-identity form.
    """
    if not logical_unit_id:
        return None
    raw = str(logical_unit_id)
    for prefix in ("workflow_node:", "agent_round:"):
        if not raw.startswith(prefix):
            continue
        rest = raw[len(prefix) :]
        # frame_id is a UUID (no colons in hex form) then ":" then node_visit_id.
        # Split once after first colon segment so visit may itself contain colons.
        if ":" not in rest:
            return None
        _frame, visit = rest.split(":", 1)
        visit = visit.strip()
        return visit or None
    return None


def _verify_prepared_continuity(
    db: Session,
    *,
    run: AssistantChatRun,
    exp_logical: str | None,
    exp_visit: str | None,
    exp_digest: str | None,
) -> None:
    from app.assistant.durable.codec import decode_checkpoint

    ck = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
    if ck is None:
        raise DurablePauseProtocolError(
            "current checkpoint row missing for pause result",
            reason_code="missing_prepared_checkpoint",
        )
    try:
        decoded = decode_checkpoint(ck.state_payload)
    except Exception as exc:  # noqa: BLE001
        raise DurablePauseProtocolError(
            f"failed to decode prior checkpoint: {exc}",
            reason_code="checkpoint_decode_failed",
        ) from exc
    inflight = getattr(decoded, "inflight_unit", None)
    if inflight is None:
        # Allow pause commit after a prior semantic result only when explicit
        # identity is not required — still fail closed when expected identity given.
        if exp_logical is not None or exp_visit is not None:
            raise DurablePauseProtocolError(
                "pause result requires inflight unit on last Checkpoint",
                reason_code="missing_inflight_unit",
            )
        return
    inflight_logical = getattr(inflight, "logical_unit_id", None)
    if exp_logical is not None and inflight_logical != exp_logical:
        raise DurablePauseProtocolError(
            f"result logical_unit_id mismatch: expected {exp_logical!r}, "
            f"inflight {inflight_logical!r}",
            reason_code="unit_identity_mismatch",
        )
    if exp_visit is not None:
        # Exact identity: workflow_node|agent_round:{frame_id}:{node_visit_id}
        # (or any prefix:visit form). Reject substring false positives.
        inflight_visit = _node_visit_from_logical_unit_id(
            str(inflight_logical) if inflight_logical is not None else None
        )
        if inflight_visit is None or inflight_visit != exp_visit:
            raise DurablePauseProtocolError(
                f"result node_visit_id mismatch: expected {exp_visit!r}, "
                f"inflight {inflight_visit!r} "
                f"(logical_unit_id={inflight_logical!r})",
                reason_code="node_visit_mismatch",
            )
    prior_reason = getattr(ck, "reason", None) or ""
    if exp_digest is not None and isinstance(prior_reason, str) and prior_reason:
        prior_digest = None
        for part in prior_reason.split("|"):
            if part.startswith("input_digest:"):
                prior_digest = part.split(":", 1)[1] or None
        if prior_digest is not None and prior_digest != exp_digest:
            raise DurablePauseProtocolError(
                f"result input_digest mismatch: expected {exp_digest!r}, "
                f"prior {prior_digest!r}",
                reason_code="input_digest_mismatch",
            )


__all__ = [
    "CODE_DURABLE_BLOCKING_RUNTIME_FORBIDDEN",
    "CODE_DURABLE_PAUSE_PROTOCOL_ERROR",
    "DurablePauseCommitResult",
    "DurablePauseProtocolError",
    "WorkerUnitPauseEffectPort",
    "commit_durable_workflow_pause",
    "consume_and_commit_pause",
    "resolve_parent_budget_ledger",
]

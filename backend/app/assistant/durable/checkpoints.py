"""Persist-before-execute Checkpoint unit boundaries (Plan 06 Task 6 §7).

Prepare / started / result CAS helpers. No adapter I/O occurs inside these
transactions. Callers must invoke started immediately before external I/O and
only after prepare has committed.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.assistant.durable.codec import (
    checkpoint_state_digest,
    encode_checkpoint_v1,
    encode_checkpoint_v2,
    encode_provider_message,
)
from app.assistant.durable.contracts import (
    DurableAgentCheckpointV1,
    DurableAgentCheckpointV2,
    DurableExecutionUnitV1,
    DurableExecutionUnitV2,
    DurableNextActionV1,
    DurableNextActionV2,
)
from app.assistant.durable.models import (
    AssistantRunBudgetRevision,
    AssistantRunCheckpoint,
    AssistantRunManifestRevision,
    AssistantRunObligationRevision,
    AssistantRunPolicyRevision,
    AssistantRunProviderMessage,
)
from app.assistant.durable.repository import (
    DurableChildBundle,
    DurableCommitResult,
    DurableRunConflict,
    DurableRunRepository,
    EventSpec,
    LeaseToken,
)
from app.assistant.provider_loop.contracts import ProviderLoopContinuation
from app.assistant.provider_loop.messages import (
    ProviderMessage,
    digest_provider_message,
    digest_provider_transcript,
)
from app.assistant.policy.recursion import CapabilityCallFrame

logger = logging.getLogger(__name__)

_PROTECTED_ROLES = frozenset(
    {"runtime_instruction", "runtime_context", "runtime_completion"}
)


def _next_checkpoint_sequence(db: Session, run_id: UUID) -> int:
    current = db.scalar(
        select(func.coalesce(func.max(AssistantRunCheckpoint.sequence), 0)).where(
            AssistantRunCheckpoint.run_id == run_id
        )
    )
    return int(current or 0) + 1


def _next_provider_ordinal(db: Session, run_id: UUID) -> int:
    current = db.scalar(
        select(func.coalesce(func.max(AssistantRunProviderMessage.ordinal), 0)).where(
            AssistantRunProviderMessage.run_id == run_id
        )
    )
    # ordinals are 1-based for non-empty transcripts; 0 means empty.
    return int(current or 0) + 1


def _load_run_pointers(db: Session, run_id: UUID):
    from app.assistant.models import AssistantChatRun

    run = db.get(AssistantChatRun, run_id)
    if run is None:
        raise DurableRunConflict("run_not_found", f"run not found: {run_id}")
    return run


def _current_transcript_digest(db: Session, run_id: UUID) -> tuple[int, str, tuple[ProviderMessage, ...]]:
    rows = (
        db.execute(
            select(AssistantRunProviderMessage)
            .where(AssistantRunProviderMessage.run_id == run_id)
            .order_by(AssistantRunProviderMessage.ordinal.asc())
        )
        .scalars()
        .all()
    )
    if not rows:
        empty = ()
        return 0, digest_provider_transcript(empty), empty
    from app.assistant.durable.codec import decode_provider_message

    messages: list[ProviderMessage] = []
    for row in rows:
        body = dict(row.payload_body or {})
        # Ensure role discriminator is present for decode.
        if "role" not in body and row.role:
            body = {**body, "role": row.role}
        messages.append(decode_provider_message(body))
    tup = tuple(messages)
    return int(rows[-1].ordinal), digest_provider_transcript(tup), tup


def _build_provider_message_rows(
    *,
    run_id: UUID,
    messages: Sequence[ProviderMessage],
    start_ordinal: int,
    manifest_revision_id: UUID,
    policy_revision_id: UUID | None,
    obligation_revision_id: UUID | None,
    provider_round: int = 0,
    protection_kinds: Sequence[str] | None = None,
) -> list[AssistantRunProviderMessage]:
    rows: list[AssistantRunProviderMessage] = []
    for offset, msg in enumerate(messages):
        role = str(getattr(msg, "role", "") or "")
        if protection_kinds is not None and offset < len(protection_kinds):
            protection = protection_kinds[offset]
        else:
            protection = "protected" if role in _PROTECTED_ROLES else "public"
        body = encode_provider_message(msg)
        content_digest = digest_provider_message(msg)
        policy_id = policy_revision_id if role in _PROTECTED_ROLES or protection == "protected" else None
        # runtime_completion needs obligation; others in protected set must not.
        obl_id = None
        if role == "runtime_completion":
            obl_id = obligation_revision_id
        rows.append(
            AssistantRunProviderMessage(
                id=uuid4(),
                run_id=run_id,
                ordinal=start_ordinal + offset,
                provider_round=provider_round,
                role=role,
                payload_version=1,
                payload_discriminator=role if role in _PROTECTED_ROLES else None,
                payload_body=body,
                protection_kind=protection,
                content_digest=content_digest,
                manifest_revision_id=manifest_revision_id,
                policy_revision_id=policy_id if protection == "protected" else (
                    policy_revision_id if role in _PROTECTED_ROLES else None
                ),
                obligation_revision_id=obl_id,
                provider_message_id=getattr(msg, "provider_message_id", None),
                tool_call_id=getattr(msg, "tool_call_id", None)
                if role == "tool"
                else None,
            )
        )
    return rows


def _append_checkpoint_bundle(
    *,
    db: Session,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    phase: str,
    next_action_kind: str,
    unit: DurableExecutionUnitV1 | None,
    capability_frames: Sequence[CapabilityCallFrame] = (),
    provider_loop_continuation: ProviderLoopContinuation | None = None,
    provider_messages: Sequence[ProviderMessage] = (),
    protection_kinds: Sequence[str] | None = None,
    budget_payload: Mapping[str, Any] | None = None,
    budget_digest: str | None = None,
    budget_revision_number: int | None = None,
    policy_payload: Mapping[str, Any] | None = None,
    policy_digest: str | None = None,
    policy_revision_number: int | None = None,
    obligation_payload: Mapping[str, Any] | None = None,
    obligation_digest: str | None = None,
    obligation_revision_number: int | None = None,
    manifest_payload: Mapping[str, Any] | None = None,
    manifest_digest: str | None = None,
    manifest_revision_number: int | None = None,
    parent_manifest_id: UUID | None = None,
    parent_manifest_digest: str | None = None,
    reason: str | None = None,
    completed_logical_unit_id: str | None = None,
    enter_ready_for_memory: bool = False,
    event_key: str | None = None,
    event_name: str | None = None,
    event_payload: Mapping[str, Any] | None = None,
) -> DurableCommitResult:
    run = _load_run_pointers(db, run_id)
    repo = DurableRunRepository(db)

    manifest_id = run.current_manifest_revision_id
    policy_id = run.current_policy_revision_id
    budget_id = run.current_budget_revision_id
    obligation_id = run.current_obligation_revision_id
    if not all([manifest_id, policy_id, budget_id, obligation_id]):
        raise DurableRunConflict(
            "protocol_error",
            "base Manifest/policy/budget/obligation must exist before unit commits",
            run=run,
        )

    rows: list[Any] = []
    new_manifest_id = manifest_id
    new_policy_id = policy_id
    new_budget_id = budget_id
    new_obligation_id = obligation_id

    if manifest_payload is not None and manifest_digest is not None:
        rev_n = int(manifest_revision_number or 0)
        if rev_n < 1:
            # Derive next revision.
            max_rev = db.scalar(
                select(func.coalesce(func.max(AssistantRunManifestRevision.revision), 0)).where(
                    AssistantRunManifestRevision.run_id == run_id
                )
            )
            rev_n = int(max_rev or 0) + 1
        m = AssistantRunManifestRevision(
            id=uuid4(),
            run_id=run_id,
            revision=rev_n,
            parent_revision_id=parent_manifest_id or manifest_id,
            parent_digest=parent_manifest_digest,
            manifest_digest=manifest_digest,
            schema_version=1,
            payload=dict(manifest_payload),
        )
        rows.append(m)
        new_manifest_id = m.id

    if policy_payload is not None and policy_digest is not None:
        rev_n = int(policy_revision_number or 0)
        if rev_n < 1:
            max_rev = db.scalar(
                select(func.coalesce(func.max(AssistantRunPolicyRevision.revision), 0)).where(
                    AssistantRunPolicyRevision.run_id == run_id
                )
            )
            rev_n = int(max_rev or 0) + 1
        p = AssistantRunPolicyRevision(
            id=uuid4(),
            run_id=run_id,
            revision=rev_n,
            parent_revision_id=policy_id,
            parent_digest=None,
            policy_digest=policy_digest,
            payload=dict(policy_payload),
        )
        rows.append(p)
        new_policy_id = p.id

    if budget_payload is not None and budget_digest is not None:
        rev_n = int(budget_revision_number or 0)
        if rev_n < 1:
            max_rev = db.scalar(
                select(func.coalesce(func.max(AssistantRunBudgetRevision.revision), 0)).where(
                    AssistantRunBudgetRevision.run_id == run_id
                )
            )
            rev_n = int(max_rev or 0) + 1
        b = AssistantRunBudgetRevision(
            id=uuid4(),
            run_id=run_id,
            revision=rev_n,
            parent_revision_id=budget_id,
            parent_digest=None,
            budget_digest=budget_digest,
            payload=dict(budget_payload),
        )
        rows.append(b)
        new_budget_id = b.id

    if obligation_payload is not None and obligation_digest is not None:
        rev_n = int(obligation_revision_number or 0)
        if rev_n < 1:
            max_rev = db.scalar(
                select(
                    func.coalesce(func.max(AssistantRunObligationRevision.revision), 0)
                ).where(AssistantRunObligationRevision.run_id == run_id)
            )
            rev_n = int(max_rev or 0) + 1
        o = AssistantRunObligationRevision(
            id=uuid4(),
            run_id=run_id,
            revision=rev_n,
            parent_revision_id=obligation_id,
            parent_digest=None,
            obligation_digest=obligation_digest,
            payload=dict(obligation_payload),
        )
        rows.append(o)
        new_obligation_id = o.id

    # Flush revision rows first so IDs are stable for Checkpoint pointers.
    if rows:
        for r in rows:
            db.add(r)
        db.flush()

    ordinal, transcript_digest, existing_msgs = _current_transcript_digest(db, run_id)
    next_ord = ordinal + 1 if ordinal > 0 else 1
    if ordinal == 0 and not existing_msgs:
        next_ord = 1
    elif ordinal > 0:
        next_ord = ordinal + 1
    else:
        next_ord = 1

    msg_rows: list[AssistantRunProviderMessage] = []
    if provider_messages:
        # If transcript empty, start at 1; else continue.
        start = 1 if ordinal == 0 else ordinal + 1
        msg_rows = _build_provider_message_rows(
            run_id=run_id,
            messages=provider_messages,
            start_ordinal=start,
            manifest_revision_id=new_manifest_id,
            policy_revision_id=new_policy_id,
            obligation_revision_id=new_obligation_id,
            protection_kinds=protection_kinds,
        )
        for mr in msg_rows:
            db.add(mr)
        db.flush()
        # Recompute transcript including new messages.
        all_msgs = existing_msgs + tuple(provider_messages)
        ordinal = start + len(provider_messages) - 1
        transcript_digest = digest_provider_transcript(all_msgs)

    frames = tuple(capability_frames or ())
    if unit is not None and unit.kind == "capability_group" and unit.state == "started":
        if unit.call_ids and not frames:
            raise DurableRunConflict(
                "protocol_error",
                "started capability_group requires capability_frames",
                run=run,
            )

    next_action = DurableNextActionV1(kind=next_action_kind)  # type: ignore[arg-type]
    checkpoint = DurableAgentCheckpointV1(
        run_id=run_id,
        phase=phase,  # type: ignore[arg-type]
        manifest_revision_id=new_manifest_id,
        policy_revision_id=new_policy_id,
        budget_revision_id=new_budget_id,
        obligation_revision_id=new_obligation_id,
        provider_message_ordinal=ordinal,
        provider_transcript_digest=transcript_digest,
        provider_loop_continuation=provider_loop_continuation,
        inflight_unit=unit,
        capability_frames=frames,
        artifact_ids=(),
        visible_text_artifact_id=None,
        next_action=next_action,
    )
    state_payload = encode_checkpoint_v1(checkpoint)
    state_digest = checkpoint_state_digest(checkpoint)
    seq = _next_checkpoint_sequence(db, run_id)
    logical_unit_id = None
    if unit is not None:
        logical_unit_id = unit.logical_unit_id
    elif completed_logical_unit_id:
        logical_unit_id = completed_logical_unit_id

    ck_row = AssistantRunCheckpoint(
        id=uuid4(),
        run_id=run_id,
        sequence=seq,
        expected_state_revision=int(expected_revision),
        committed_state_revision=int(expected_revision) + 1,
        schema_version=1,
        manifest_revision_id=new_manifest_id,
        policy_revision_id=new_policy_id,
        budget_revision_id=new_budget_id,
        obligation_revision_id=new_obligation_id,
        provider_message_ordinal=ordinal,
        provider_transcript_digest=transcript_digest,
        phase=phase,
        logical_unit_id=logical_unit_id,
        reason=reason,
        state_payload=state_payload,
        state_digest=state_digest,
    )
    # Bundle: revision rows already flushed; include only checkpoint (+ msgs already flushed).
    # Repository will re-add checkpoint and set pointers.
    bundle = DurableChildBundle(
        rows=[ck_row],
        current_manifest_revision_id=new_manifest_id,
        current_policy_revision_id=new_policy_id,
        current_budget_revision_id=new_budget_id,
        current_obligation_revision_id=new_obligation_id,
        current_checkpoint_id=ck_row.id,
    )

    events: list[EventSpec] = []
    if event_key and event_name:
        events.append(
            EventSpec(
                event_key=event_key,
                event_name=event_name,
                payload=dict(event_payload or {}),
                visibility="internal",
            )
        )
    else:
        # Deterministic internal boundary event.
        unit_key = logical_unit_id or "none"
        state = unit.state if unit is not None else ("result" if completed_logical_unit_id else phase)
        events.append(
            EventSpec(
                event_key=f"unit.{state}:{unit_key}:rev{expected_revision}",
                event_name=f"unit.{state}",
                payload={
                    "phase": phase,
                    "logicalUnitId": unit_key,
                    "attempt": unit.attempt if unit is not None else None,
                },
                visibility="internal",
            )
        )

    if enter_ready_for_memory:
        return repo.enter_ready_for_memory(
            run_id=run_id,
            expected_revision=expected_revision,
            lease=lease,
            events=events,
            children=bundle,
        )
    return repo.commit_semantic(
        run_id=run_id,
        expected_revision=expected_revision,
        lease=lease,
        events=events,
        children=bundle,
    )


def note_capability_adapter_result() -> None:
    """Mark that Capability adapter I/O completed; crash inject before result CAS.

    Call immediately after external Capability I/O returns and before
    :func:`commit_unit_result`. Kill point 4 in Plan 06 Task 9.
    """
    from app.assistant.durable.crash import CrashPoint, maybe_crash

    maybe_crash(CrashPoint.AFTER_CAPABILITY_RESULT_BEFORE_RESULT)


def commit_prepared_unit(
    db: Session,
    *,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    unit: DurableExecutionUnitV1,
    phase: str,
    next_action_kind: str,
    capability_frames: Sequence[CapabilityCallFrame] = (),
    budget_payload: Mapping[str, Any] | None = None,
    budget_digest: str | None = None,
    budget_revision_number: int | None = None,
    skip_frame_check: bool = False,
) -> DurableCommitResult:
    """§7.1 Prepare transaction: reservation only; started_budget_revision=None."""
    if unit.state != "prepared":
        raise DurableRunConflict(
            "protocol_error",
            "commit_prepared_unit requires unit.state=prepared",
        )
    if unit.started_budget_revision is not None:
        raise DurableRunConflict(
            "protocol_error",
            "prepared unit must have started_budget_revision=None",
        )
    frames = tuple(capability_frames or ())
    if (
        not skip_frame_check
        and unit.kind == "capability_group"
        and unit.call_ids
        and unit.state == "started"
        and not frames
    ):
        raise DurableRunConflict(
            "protocol_error",
            "started capability_group requires frames",
        )
    return _append_checkpoint_bundle(
        db=db,
        run_id=run_id,
        lease=lease,
        expected_revision=expected_revision,
        phase=phase,
        next_action_kind=next_action_kind,
        unit=unit,
        capability_frames=frames,
        budget_payload=budget_payload,
        budget_digest=budget_digest,
        budget_revision_number=budget_revision_number,
        reason="prepared",
    )


def commit_started_unit(
    db: Session,
    *,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    unit: DurableExecutionUnitV1,
    phase: str,
    next_action_kind: str,
    capability_frames: Sequence[CapabilityCallFrame] = (),
    budget_payload: Mapping[str, Any] | None = None,
    budget_digest: str | None = None,
    budget_revision_number: int | None = None,
) -> DurableCommitResult:
    """§7.2 Started transaction: immediately before external adapter I/O."""
    if unit.state != "started":
        raise DurableRunConflict(
            "protocol_error",
            "commit_started_unit requires unit.state=started",
        )
    if unit.started_budget_revision is None:
        raise DurableRunConflict(
            "protocol_error",
            "started unit requires started_budget_revision",
        )
    return _append_checkpoint_bundle(
        db=db,
        run_id=run_id,
        lease=lease,
        expected_revision=expected_revision,
        phase=phase,
        next_action_kind=next_action_kind,
        unit=unit,
        capability_frames=tuple(capability_frames or ()),
        budget_payload=budget_payload,
        budget_digest=budget_digest,
        budget_revision_number=budget_revision_number,
        reason="started",
    )


def commit_unit_result(
    db: Session,
    *,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    phase: str,
    next_action_kind: str,
    clear_inflight: bool = True,
    provider_messages: Sequence[ProviderMessage] = (),
    protection_kinds: Sequence[str] | None = None,
    provider_loop_continuation: ProviderLoopContinuation | None = None,
    capability_frames: Sequence[CapabilityCallFrame] = (),
    budget_payload: Mapping[str, Any] | None = None,
    budget_digest: str | None = None,
    budget_revision_number: int | None = None,
    policy_payload: Mapping[str, Any] | None = None,
    policy_digest: str | None = None,
    policy_revision_number: int | None = None,
    obligation_payload: Mapping[str, Any] | None = None,
    obligation_digest: str | None = None,
    obligation_revision_number: int | None = None,
    manifest_payload: Mapping[str, Any] | None = None,
    manifest_digest: str | None = None,
    manifest_revision_number: int | None = None,
    parent_manifest_id: UUID | None = None,
    parent_manifest_digest: str | None = None,
    completed_logical_unit_id: str | None = None,
    enter_ready_for_memory: bool = False,
    reason: str | None = "result",
) -> DurableCommitResult:
    """§7.4 Result transaction: append outputs + post-unit Checkpoint."""
    unit = None  # post-result has no inflight when clear_inflight
    if not clear_inflight:
        raise DurableRunConflict(
            "protocol_error",
            "commit_unit_result currently requires clear_inflight=True",
        )
    return _append_checkpoint_bundle(
        db=db,
        run_id=run_id,
        lease=lease,
        expected_revision=expected_revision,
        phase=phase,
        next_action_kind=next_action_kind,
        unit=unit,
        capability_frames=tuple(capability_frames or ()),
        provider_loop_continuation=provider_loop_continuation,
        provider_messages=provider_messages,
        protection_kinds=protection_kinds,
        budget_payload=budget_payload,
        budget_digest=budget_digest,
        budget_revision_number=budget_revision_number,
        policy_payload=policy_payload,
        policy_digest=policy_digest,
        policy_revision_number=policy_revision_number,
        obligation_payload=obligation_payload,
        obligation_digest=obligation_digest,
        obligation_revision_number=obligation_revision_number,
        manifest_payload=manifest_payload,
        manifest_digest=manifest_digest,
        manifest_revision_number=manifest_revision_number,
        parent_manifest_id=parent_manifest_id,
        parent_manifest_digest=parent_manifest_digest,
        completed_logical_unit_id=completed_logical_unit_id,
        enter_ready_for_memory=enter_ready_for_memory,
        reason=reason,
    )


def resolve_retry_unit(unit: DurableExecutionUnitV1) -> DurableExecutionUnitV1:
    """Same logical unit identity; attempt + 1; preserve reservation/started state."""
    return DurableExecutionUnitV1(
        logical_unit_id=unit.logical_unit_id,
        kind=unit.kind,
        state=unit.state,
        provider_round=unit.provider_round,
        call_ids=unit.call_ids,
        attempt=int(unit.attempt) + 1,
        reserved_budget_revision=unit.reserved_budget_revision,
        started_budget_revision=unit.started_budget_revision,
    )


def find_post_result_for_unit(
    db: Session,
    *,
    run_id: UUID,
    logical_unit_id: str,
) -> AssistantRunCheckpoint | None:
    """Return a committed post-result Checkpoint for this logical unit if present.

    Post-result Checkpoints store ``logical_unit_id`` with no inflight unit and a
    non-prepared/started phase (ready_for_completion / dispatching_calls / waiting /
    ready_for_memory / terminal).
    """
    rows = (
        db.execute(
            select(AssistantRunCheckpoint)
            .where(
                AssistantRunCheckpoint.run_id == run_id,
                AssistantRunCheckpoint.logical_unit_id == logical_unit_id,
            )
            .order_by(AssistantRunCheckpoint.sequence.desc())
        )
        .scalars()
        .all()
    )
    from app.assistant.durable.codec import decode_checkpoint

    for row in rows:
        try:
            decoded = decode_checkpoint(row.state_payload)
        except Exception:
            continue
        if decoded.inflight_unit is None and decoded.phase not in {
            "ready_for_provider",
        }:
            # Prefer phases that indicate result committed.
            if decoded.phase in {
                "dispatching_calls",
                "waiting",
                "ready_for_completion",
                "ready_for_memory",
                "terminal",
            }:
                return row
    return None


def commit_checkpoint_v2(
    db: Session,
    *,
    run_id: UUID,
    lease: LeaseToken,
    expected_revision: int,
    phase: str,
    next_action_kind: str,
    unit: DurableExecutionUnitV2 | None = None,
    workflow_state: Any = None,
    capability_frames: Sequence[Any] = (),
    provider_loop_continuation: ProviderLoopContinuation | None = None,
    provider_messages: Sequence[ProviderMessage] = (),
    protection_kinds: Sequence[str] | None = None,
    budget_payload: Mapping[str, Any] | None = None,
    budget_digest: str | None = None,
    budget_revision_number: int | None = None,
    policy_payload: Mapping[str, Any] | None = None,
    policy_digest: str | None = None,
    policy_revision_number: int | None = None,
    obligation_payload: Mapping[str, Any] | None = None,
    obligation_digest: str | None = None,
    obligation_revision_number: int | None = None,
    manifest_payload: Mapping[str, Any] | None = None,
    manifest_digest: str | None = None,
    manifest_revision_number: int | None = None,
    parent_manifest_id: UUID | None = None,
    parent_manifest_digest: str | None = None,
    completed_logical_unit_id: str | None = None,
    pending_interrupt_id: UUID | None = None,
    budget_suspension: Any = None,
    active_capability_continuation: Any = None,
    enter_ready_for_memory: bool = False,
    reason: str | None = None,
) -> DurableCommitResult:
    """Append a Checkpoint v2 carrying optional workflow_state / workflow units.

    Mirrors :func:`_append_checkpoint_bundle` but encodes
    :class:`DurableAgentCheckpointV2` (schema_version=2) so Plan 07 workflow
    frames, agent rounds, and pause pointers can be persisted. Does not change
    the V1 prepare/started/result path used by Plan 06 provider units.
    """
    run = _load_run_pointers(db, run_id)
    repo = DurableRunRepository(db)

    manifest_id = run.current_manifest_revision_id
    policy_id = run.current_policy_revision_id
    budget_id = run.current_budget_revision_id
    obligation_id = run.current_obligation_revision_id
    if not all([manifest_id, policy_id, budget_id, obligation_id]):
        raise DurableRunConflict(
            "protocol_error",
            "base Manifest/policy/budget/obligation must exist before unit commits",
            run=run,
        )

    rows: list[Any] = []
    new_manifest_id = manifest_id
    new_policy_id = policy_id
    new_budget_id = budget_id
    new_obligation_id = obligation_id

    if manifest_payload is not None and manifest_digest is not None:
        rev_n = int(manifest_revision_number or 0)
        if rev_n < 1:
            max_rev = db.scalar(
                select(func.coalesce(func.max(AssistantRunManifestRevision.revision), 0)).where(
                    AssistantRunManifestRevision.run_id == run_id
                )
            )
            rev_n = int(max_rev or 0) + 1
        m = AssistantRunManifestRevision(
            id=uuid4(),
            run_id=run_id,
            revision=rev_n,
            parent_revision_id=parent_manifest_id or manifest_id,
            parent_digest=parent_manifest_digest,
            manifest_digest=manifest_digest,
            schema_version=1,
            payload=dict(manifest_payload),
        )
        rows.append(m)
        new_manifest_id = m.id

    if policy_payload is not None and policy_digest is not None:
        rev_n = int(policy_revision_number or 0)
        if rev_n < 1:
            max_rev = db.scalar(
                select(func.coalesce(func.max(AssistantRunPolicyRevision.revision), 0)).where(
                    AssistantRunPolicyRevision.run_id == run_id
                )
            )
            rev_n = int(max_rev or 0) + 1
        p = AssistantRunPolicyRevision(
            id=uuid4(),
            run_id=run_id,
            revision=rev_n,
            parent_revision_id=policy_id,
            parent_digest=None,
            policy_digest=policy_digest,
            payload=dict(policy_payload),
        )
        rows.append(p)
        new_policy_id = p.id

    if budget_payload is not None and budget_digest is not None:
        rev_n = int(budget_revision_number or 0)
        if rev_n < 1:
            max_rev = db.scalar(
                select(func.coalesce(func.max(AssistantRunBudgetRevision.revision), 0)).where(
                    AssistantRunBudgetRevision.run_id == run_id
                )
            )
            rev_n = int(max_rev or 0) + 1
        b = AssistantRunBudgetRevision(
            id=uuid4(),
            run_id=run_id,
            revision=rev_n,
            parent_revision_id=budget_id,
            parent_digest=None,
            budget_digest=budget_digest,
            payload=dict(budget_payload),
        )
        rows.append(b)
        new_budget_id = b.id

    if obligation_payload is not None and obligation_digest is not None:
        rev_n = int(obligation_revision_number or 0)
        if rev_n < 1:
            max_rev = db.scalar(
                select(func.coalesce(func.max(AssistantRunObligationRevision.revision), 0)).where(
                    AssistantRunObligationRevision.run_id == run_id
                )
            )
            rev_n = int(max_rev or 0) + 1
        o = AssistantRunObligationRevision(
            id=uuid4(),
            run_id=run_id,
            revision=rev_n,
            parent_revision_id=obligation_id,
            parent_digest=None,
            obligation_digest=obligation_digest,
            payload=dict(obligation_payload),
        )
        rows.append(o)
        new_obligation_id = o.id

    if rows:
        for r in rows:
            db.add(r)
        db.flush()

    ordinal, transcript_digest, existing_msgs = _current_transcript_digest(db, run_id)
    if provider_messages:
        start = 1 if ordinal == 0 else ordinal + 1
        msg_rows = _build_provider_message_rows(
            run_id=run_id,
            messages=provider_messages,
            start_ordinal=start,
            manifest_revision_id=new_manifest_id,
            policy_revision_id=new_policy_id,
            obligation_revision_id=new_obligation_id,
            protection_kinds=protection_kinds,
        )
        for mr in msg_rows:
            db.add(mr)
        db.flush()
        all_msgs = existing_msgs + tuple(provider_messages)
        ordinal = start + len(provider_messages) - 1
        transcript_digest = digest_provider_transcript(all_msgs)

    frames = tuple(capability_frames or ())
    next_action = DurableNextActionV2(kind=next_action_kind)  # type: ignore[arg-type]
    checkpoint = DurableAgentCheckpointV2(
        run_id=run_id,
        phase=phase,  # type: ignore[arg-type]
        manifest_revision_id=new_manifest_id,
        policy_revision_id=new_policy_id,
        budget_revision_id=new_budget_id,
        obligation_revision_id=new_obligation_id,
        provider_message_ordinal=ordinal,
        provider_transcript_digest=transcript_digest,
        provider_loop_continuation=provider_loop_continuation,
        inflight_unit=unit,
        capability_frames=frames,
        artifact_ids=(),
        visible_text_artifact_id=None,
        next_action=next_action,
        workflow_state=workflow_state,
        active_capability_continuation=active_capability_continuation,
        pending_interrupt_id=pending_interrupt_id,
        budget_suspension=budget_suspension,
    )
    state_payload = encode_checkpoint_v2(checkpoint)
    state_digest = checkpoint_state_digest(checkpoint)
    seq = _next_checkpoint_sequence(db, run_id)
    logical_unit_id = None
    if unit is not None:
        logical_unit_id = unit.logical_unit_id
    elif completed_logical_unit_id:
        logical_unit_id = completed_logical_unit_id

    ck_row = AssistantRunCheckpoint(
        id=uuid4(),
        run_id=run_id,
        sequence=seq,
        expected_state_revision=int(expected_revision),
        committed_state_revision=int(expected_revision) + 1,
        schema_version=2,
        manifest_revision_id=new_manifest_id,
        policy_revision_id=new_policy_id,
        budget_revision_id=new_budget_id,
        obligation_revision_id=new_obligation_id,
        provider_message_ordinal=ordinal,
        provider_transcript_digest=transcript_digest,
        phase=phase,
        logical_unit_id=logical_unit_id,
        reason=reason,
        state_payload=state_payload,
        state_digest=state_digest,
    )
    bundle = DurableChildBundle(
        rows=[ck_row],
        current_manifest_revision_id=new_manifest_id,
        current_policy_revision_id=new_policy_id,
        current_budget_revision_id=new_budget_id,
        current_obligation_revision_id=new_obligation_id,
        current_checkpoint_id=ck_row.id,
    )

    unit_key = logical_unit_id or "none"
    state = unit.state if unit is not None else ("result" if completed_logical_unit_id else phase)
    events = [
        EventSpec(
            event_key=f"unit.{state}:{unit_key}:rev{expected_revision}",
            event_name=f"unit.{state}",
            payload={
                "phase": phase,
                "logicalUnitId": unit_key,
                "attempt": unit.attempt if unit is not None else None,
                "schemaVersion": 2,
            },
            visibility="internal",
        )
    ]

    if enter_ready_for_memory:
        return repo.enter_ready_for_memory(
            run_id=run_id,
            expected_revision=expected_revision,
            lease=lease,
            events=events,
            children=bundle,
        )
    return repo.commit_semantic(
        run_id=run_id,
        expected_revision=expected_revision,
        lease=lease,
        events=events,
        children=bundle,
    )


__all__ = [
    "commit_checkpoint_v2",
    "commit_prepared_unit",
    "commit_started_unit",
    "commit_unit_result",
    "find_post_result_for_unit",
    "note_capability_adapter_result",
    "resolve_retry_unit",
]

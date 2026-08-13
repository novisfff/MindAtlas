"""Call-aware settlement while a Run is cancelling (Plan 08 Task 2).

No new I/O: only already-captured Attempt evidence may be committed. Adds the
Run edge ``cancelling -> needs_reconciliation`` when an already-started call
cannot be proven.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, NoReturn
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.capability_calls.models import (
    AssistantCapabilityCall,
    AssistantCapabilityCallAttempt,
)
from app.assistant.capability_calls.repository import (
    CODE_CALL_NOT_FOUND,
    CODE_INVALID_TRANSITION,
    CODE_STALE_CALL_REVISION,
    CODE_STALE_RUN_REVISION,
    CapabilityCallConflict,
    CapabilityCallRepository,
)
from app.assistant.capability_calls.result_codec import (
    CapabilityResultCodecError,
    decode_capability_result,
)
from app.assistant.domain.digests import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_canonical_json,
)
from app.assistant.durable.checkpoints import (
    _build_provider_message_rows,
    _current_transcript_digest,
    _next_checkpoint_sequence,
)
from app.assistant.durable.codec import (
    checkpoint_state_digest,
    decode_checkpoint,
    encode_checkpoint_v3,
)
from app.assistant.durable.contracts import (
    DurableCapabilityCallStateV1,
    DurableNextActionV2,
)
from app.assistant.durable.models import (
    AssistantRunArtifact,
    AssistantRunBudgetRevision,
    AssistantRunCheckpoint,
    AssistantRunObligationRevision,
    AssistantRunProviderMessage,
)
from app.assistant.models import AssistantChatRun
from app.assistant.durable.repository import (
    ALLOWED_TRANSITIONS,
    STATUS_RECOVERING,
    STATUS_RUNNING,
    STATUS_CANCELLING,
    STATUS_NEEDS_RECONCILIATION,
    DurableChildBundle,
    DurableRunRepository,
    EventSpec,
)
from app.assistant.policy.obligations import (
    ObligationLedgerState,
    build_reserved_obligation,
    pure_create_obligation,
)
from app.assistant.provider_loop.messages import (
    ProviderAssistantMessage,
    ProviderToolMessage,
    digest_provider_message,
    digest_provider_transcript,
    make_cancelled_envelope,
    open_provider_tool_calls,
    project_tool_result_envelope,
    validate_provider_transcript,
)
from app.assistant.capability_calls.state_machine import PLAN08_RUN_TRANSITION_DELTA
from app.assistant.capability_calls.state_machine import (
    CallTransitionError,
    validate_call_transition,
)
from app.common.time import utcnow


# Install Plan 08 Run delta once at import (idempotent).
for _edge, _rule in PLAN08_RUN_TRANSITION_DELTA.items():
    ALLOWED_TRANSITIONS.setdefault(_edge, _rule)


Outcome = Literal["succeeded", "failed", "unknown"]

CODE_SETTLEMENT_EVIDENCE_INVALID = "settlement_evidence_invalid"
SETTLEMENT_OUTCOME_UNKNOWN = "settlement_outcome_unknown"


def _is_exact_call_reconciliation_obligation(item: Any, call_id: UUID) -> bool:
    """Require the reconciliation obligation's owner and source to agree."""
    return (
        str(getattr(item, "obligation_type", "")) == "reconciliation"
        and str(getattr(item, "owner_kind", "")) == "capability_call"
        and str(getattr(item, "owner_id", "")) == str(call_id)
        and str(getattr(item, "source_call_id", "")) == str(call_id)
    )


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def compute_settlement_evidence_digest(
    *,
    attempt: AssistantCapabilityCallAttempt,
    outcome: Outcome,
    result_artifact: AssistantRunArtifact | None,
) -> str:
    """Digest the exact durable evidence authorized for one settlement.

    This helper intentionally accepts persisted ORM rows, rather than a caller
    payload. The caller may carry the digest across a process boundary, but the
    settlement repository always recomputes it from locked database state.
    """
    result = None
    if result_artifact is not None:
        inline_bytes = (
            bytes(result_artifact.inline_bytes)
            if result_artifact.inline_bytes is not None
            else None
        )
        result = {
            "artifactId": str(result_artifact.id),
            "runId": str(result_artifact.run_id),
            "kind": str(result_artifact.kind),
            "mediaType": str(result_artifact.media_type),
            "displayLabel": result_artifact.display_label,
            "storageKind": str(result_artifact.storage_kind),
            "byteSize": int(result_artifact.byte_size),
            "contentSha256": str(result_artifact.content_sha256),
            "inlineBytesBase64": (
                base64.b64encode(inline_bytes).decode("ascii")
                if inline_bytes is not None
                else None
            ),
            "objectKey": result_artifact.object_key,
            "metadataJson": result_artifact.metadata_json or {},
            "createdAt": _timestamp(result_artifact.created_at),
        }
    return sha256_canonical_json(
        {
            "contractVersion": 1,
            "outcome": outcome,
            "attempt": {
                "attemptId": str(attempt.id),
                "callId": str(attempt.call_id),
                "attemptNumber": int(attempt.attempt_number),
                "workerId": str(attempt.worker_id),
                "leaseGeneration": int(attempt.lease_generation),
                "status": str(attempt.status),
                "startedAt": _timestamp(attempt.started_at),
                "endedAt": _timestamp(attempt.ended_at),
                "dispatchDeadlineAt": _timestamp(attempt.dispatch_deadline_at),
                "requestDigest": attempt.request_digest,
                "responseDigest": attempt.response_digest,
                "externalRequestId": attempt.external_request_id,
                "externalIdempotencyEcho": attempt.external_idempotency_echo,
                "transportStatus": attempt.transport_status,
                "sideEffectStarted": bool(attempt.side_effect_started),
                "sideEffectStartedAt": _timestamp(attempt.side_effect_started_at),
                "errorCode": attempt.error_code,
                "retryClassification": attempt.retry_classification,
                "diagnosticArtifactId": (
                    str(attempt.diagnostic_artifact_id)
                    if attempt.diagnostic_artifact_id is not None
                    else None
                ),
                "createdAt": _timestamp(attempt.created_at),
            },
            "resultArtifact": result,
        }
    )


@dataclass(slots=True)
class SettlementRequest:
    call_id: UUID
    attempt_id: UUID
    expected_call_revision: int
    expected_run_revision: int
    outcome: Outcome
    result_artifact_id: UUID | None
    evidence_digest: str


class CapabilityCallSettlementRepository:
    """Settle already-started call evidence under Run ``cancelling``."""

    def __init__(self, db: Session, *, write_safety_lock: Any | None = None) -> None:
        self.db = db
        self.calls = CapabilityCallRepository(
            db,
            write_safety_lock=write_safety_lock,
        )

    def settle_while_cancelling(
        self,
        request: SettlementRequest,
        *,
        now: datetime | None = None,
    ) -> AssistantChatRun:
        """Apply trusted captured outcome without adapter/I/O.

        - succeeded/failed: call becomes terminal; Run remains cancelling.
        - unknown: call -> unknown -> needs_reconciliation; Run cancelling ->
          needs_reconciliation.
        """
        if request.outcome not in {"succeeded", "failed", "unknown"}:
            raise CapabilityCallConflict(
                CODE_SETTLEMENT_EVIDENCE_INVALID,
                f"unsupported settlement outcome {request.outcome!r}",
            )
        if request.outcome == "unknown":
            from app.assistant.capability_calls.write_guard import (
                acquire_write_safety_advisory_lock,
            )

            if self.calls.write_safety_lock is None:
                acquire_write_safety_advisory_lock(self.db)
            else:
                self.calls.write_safety_lock.acquire(self.db)
        ts = now or utcnow()
        probe = self.calls.get_call(request.call_id)
        if probe is None:
            raise CapabilityCallConflict(
                CODE_CALL_NOT_FOUND, f"call {request.call_id} not found"
            )
        # Global lock order: Run before call/Attempt/Interrupt rows.
        run = self.calls.get_run(probe.run_id, for_update=True)
        call = self.calls.get_call(request.call_id, for_update=True)
        if call is None:
            raise CapabilityCallConflict(
                CODE_CALL_NOT_FOUND, f"call {request.call_id} not found"
            )
        if int(run.state_revision) != int(request.expected_run_revision):
            raise CapabilityCallConflict(
                CODE_STALE_RUN_REVISION,
                f"expected run revision {request.expected_run_revision}, got {run.state_revision}",
                run=run,
                call=call,
            )
        if int(call.state_revision) != int(request.expected_call_revision):
            raise CapabilityCallConflict(
                CODE_STALE_CALL_REVISION,
                f"expected call revision {request.expected_call_revision}, got {call.state_revision}",
                call=call,
                run=run,
            )
        if str(run.status) != STATUS_CANCELLING:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                f"settlement requires run status cancelling, got {run.status!r}",
                call=call,
                run=run,
            )
        if call.side_effect_started_at is None:
            # Unstarted calls should use ordinary cancel, not settlement.
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "settlement requires side_effect_started_at",
                call=call,
                run=run,
            )

        (
            attempt,
            result_artifact,
            captured_failure_code,
            decoded_result,
        ) = self._validate_evidence(
            request=request,
            run=run,
            call=call,
        )
        expected_digest = compute_settlement_evidence_digest(
            attempt=attempt,
            outcome=request.outcome,
            result_artifact=result_artifact,
        )
        if not hmac.compare_digest(
            expected_digest.encode("ascii"),
            str(request.evidence_digest).encode("utf-8"),
        ):
            raise CapabilityCallConflict(
                CODE_SETTLEMENT_EVIDENCE_INVALID,
                "settlement evidence digest does not match locked Attempt evidence",
                call=call,
                run=run,
            )

        if (
            request.outcome in {"succeeded", "failed"}
            and str(attempt.status) == "response_received"
        ):
            attempt = self.calls.transition_attempt(
                attempt_id=attempt.id,
                expected_status="response_received",
                to_status="committed",
                now=ts,
            )
        elif request.outcome == "unknown" and str(attempt.status) == "dispatched":
            attempt = self.calls.transition_attempt(
                attempt_id=attempt.id,
                expected_status="dispatched",
                to_status="uncertain",
                error_code=SETTLEMENT_OUTCOME_UNKNOWN,
                now=ts,
            )

        if request.outcome in {"succeeded", "failed"}:
            to_status = request.outcome
            self._validate_transition(call, to_status)
            call.status = to_status
            call.state_revision = int(call.state_revision) + 1
            call.updated_at = ts
            call.terminal_at = ts
            if result_artifact is not None:
                call.output_artifact_id = result_artifact.id
            if request.outcome == "failed":
                call.failure_code = captured_failure_code or attempt.error_code
            self.db.flush()
            return self._commit_terminal_settlement(
                run=run,
                call=call,
                attempt=attempt,
                outcome=request.outcome,
                result_artifact=result_artifact,
                decoded_result=decoded_result,
                now=ts,
            )

        # unknown -> needs_reconciliation on call; Run cancelling -> needs_reconciliation
        if str(call.status) == "executing":
            self._validate_transition(call, "unknown")
            call.status = "unknown"
            call.state_revision = int(call.state_revision) + 1
            call.updated_at = ts
            self.db.flush()
        if str(call.status) == "unknown":
            self._validate_transition(call, "needs_reconciliation")
            call.status = "needs_reconciliation"
            call.state_revision = int(call.state_revision) + 1
            call.updated_at = ts
            self.db.flush()

        return self._commit_unknown_settlement(
            run=run,
            call=call,
            attempt=attempt,
            now=ts,
        )

    def mark_local_commit_outcome_unknown(
        self,
        *,
        call_id: UUID,
        failure_code: str = "local_commit_outcome_unknown",
        budget_snapshot: Any | None = None,
        now: datetime | None = None,
    ) -> AssistantChatRun:
        """Quarantine a local write after an indeterminate commit boundary.

        This path performs no adapter I/O and never retries.  It is deliberately
        Run-first and uses the same write-safety advisory lock as new write
        admission and ordinary unknown settlement.
        """
        from app.assistant.capability_calls.write_guard import (
            acquire_write_safety_advisory_lock,
        )

        if self.calls.write_safety_lock is None:
            acquire_write_safety_advisory_lock(self.db)
        else:
            self.calls.write_safety_lock.acquire(self.db)
        ts = now or utcnow()
        try:
            probe = self.calls.get_call(call_id)
            if probe is None:
                raise CapabilityCallConflict(
                    CODE_CALL_NOT_FOUND, f"call {call_id} not found"
                )
            run = self.calls.get_run(probe.run_id, for_update=True)
            call = self.calls.get_call(call_id, for_update=True)
            if call is None:
                raise CapabilityCallConflict(
                    CODE_CALL_NOT_FOUND, f"call {call_id} not found"
                )
            if str(call.execution_mode) != "local_transactional":
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "local commit quarantine requires local_transactional mode",
                    call=call,
                    run=run,
                )
            if str(run.status) not in {
                STATUS_RUNNING,
                STATUS_RECOVERING,
                STATUS_CANCELLING,
            }:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    f"local commit quarantine cannot run from {run.status!r}",
                    call=call,
                    run=run,
                )
            if str(call.status) in {"authorized", "executing"}:
                call = self.calls.transition_call(
                    call_id=call.id,
                    expected_call_revision=int(call.state_revision),
                    expected_run_revision=int(run.state_revision),
                    to_status="unknown",
                    lease=None,
                    allow_while_cancelling=True,
                    failure_code=failure_code,
                    now=ts,
                )
            if str(call.status) == "unknown":
                call = self.calls.transition_call(
                    call_id=call.id,
                    expected_call_revision=int(call.state_revision),
                    expected_run_revision=int(run.state_revision),
                    to_status="needs_reconciliation",
                    lease=None,
                    allow_while_cancelling=True,
                    failure_code=failure_code,
                    now=ts,
                )
            if str(call.status) != "needs_reconciliation":
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "local commit ambiguity cannot terminalize the Call",
                    call=call,
                    run=run,
                )
            checkpoint, obligation = self._prepare_unknown_context(
                run=run,
                call=call,
                attempt=None,
                now=ts,
                reason=failure_code,
            )
            budget_revision = self._prepare_unknown_budget_revision(
                run=run,
                snapshot=budget_snapshot,
                now=ts,
            )
            return self._commit_unknown_run_context(
                run=run,
                call=call,
                checkpoint=checkpoint,
                obligation=obligation,
                budget_revision=budget_revision,
                now=ts,
                failure_code=failure_code,
            )
        except Exception:
            self.db.rollback()
            raise

    def _prepare_unknown_context(
        self,
        *,
        run: AssistantChatRun,
        call: AssistantCapabilityCall,
        attempt: AssistantCapabilityCallAttempt | None,
        now: datetime,
        reason: str,
    ) -> tuple[Any, AssistantRunObligationRevision]:
        """Build a v3 reconcile checkpoint and exact pending obligation."""
        if run.current_checkpoint_id is None:
            self._invalid_evidence(
                "unknown local settlement requires a current Checkpoint",
                call=call,
                run=run,
            )
        row = (
            self.db.query(AssistantRunCheckpoint)
            .filter(AssistantRunCheckpoint.id == run.current_checkpoint_id)
            .with_for_update()
            .one_or_none()
        )
        if row is None or row.run_id != run.id:
            self._invalid_evidence(
                "unknown local settlement Checkpoint pointer is invalid",
                call=call,
                run=run,
            )
        checkpoint = decode_checkpoint(row.state_payload)
        if int(getattr(checkpoint, "schema_version", 0)) != 3:
            self._invalid_evidence(
                "unknown local settlement requires Checkpoint schema v3",
                call=call,
                run=run,
            )
        states = []
        replaced = False
        for state in checkpoint.capability_calls:
            if state.call_id != call.id:
                states.append(state)
                continue
            states.append(
                DurableCapabilityCallStateV1(
                    call_id=state.call_id,
                    logical_call_key=state.logical_call_key,
                    provider_tool_call_id=state.provider_tool_call_id,
                    provider_order=state.provider_order,
                    status="needs_reconciliation",
                    attempt_id=attempt.id if attempt is not None else None,
                    output_artifact_id=None,
                    interrupt_id=call.interrupt_id,
                    approval_binding_digest=call.approval_binding_digest,
                    result_message_digest=None,
                )
            )
            replaced = True
        if not replaced:
            self._invalid_evidence(
                "unknown local settlement Checkpoint lacks the Call",
                call=call,
                run=run,
            )
        if run.current_obligation_revision_id is None:
            self._invalid_evidence(
                "unknown local settlement requires an obligation ledger",
                call=call,
                run=run,
            )
        current = (
            self.db.query(AssistantRunObligationRevision)
            .filter(
                AssistantRunObligationRevision.id
                == run.current_obligation_revision_id
            )
            .with_for_update()
            .one_or_none()
        )
        if current is None or current.run_id != run.id:
            self._invalid_evidence(
                "unknown local settlement obligation pointer is invalid",
                call=call,
                run=run,
            )
        ledger = ObligationLedgerState.model_validate(current.payload)
        pending = [item for item in ledger.obligations if item.status == "pending"]
        matching = [
            item
            for item in pending
            if _is_exact_call_reconciliation_obligation(item, call.id)
        ]
        # Preserve unrelated pending obligations.  An ambiguous local commit
        # must remain quarantined even when another owner already has work
        # pending; only duplicate Call-owned reconciliation obligations are a
        # malformed state.
        if len(matching) > 1:
            self._invalid_evidence(
                "unknown local settlement has duplicate reconciliation obligations",
                call=call,
                run=run,
            )
        if not matching:
            ledger, created = pure_create_obligation(
                ledger,
                build_reserved_obligation(
                    run_id=run.id,
                    obligation_type="reconciliation",
                    owner_kind="capability_call",
                    owner_id=str(call.id),
                    source_call_id=str(call.id),
                    revision=int(current.revision) + 1,
                ),
            )
            if not created.allowed:
                self._invalid_evidence(
                    "unknown local settlement obligation creation failed",
                    call=call,
                    run=run,
                )
            obligation = AssistantRunObligationRevision(
                id=uuid4(),
                run_id=run.id,
                revision=int(current.revision) + 1,
                parent_revision_id=current.id,
                parent_digest=current.obligation_digest,
                obligation_digest=ledger.ledger_digest,
                payload=ledger.model_dump(mode="json", by_alias=True),
                created_at=now,
            )
            self.db.add(obligation)
            self.db.flush()
        else:
            obligation = current
        updated = checkpoint.model_copy(
            update={
                "phase": (
                    "waiting"
                    if checkpoint.provider_loop_continuation is not None
                    else "ready_for_provider"
                ),
                "obligation_revision_id": obligation.id,
                "next_action": DurableNextActionV2(
                    kind=(
                        "wait"
                        if checkpoint.provider_loop_continuation is not None
                        else "reconcile"
                    )
                ),
                "capability_calls": tuple(states),
            }
        )
        return updated, obligation

    def _commit_unknown_run_context(
        self,
        *,
        run: AssistantChatRun,
        call: AssistantCapabilityCall,
        checkpoint: Any,
        obligation: AssistantRunObligationRevision,
        budget_revision: AssistantRunBudgetRevision | None = None,
        now: datetime,
        failure_code: str,
    ) -> AssistantChatRun:
        phase = str(checkpoint.phase)
        checkpoint_id = uuid4()
        budget_id = budget_revision.id if budget_revision is not None else run.current_budget_revision_id
        checkpoint = checkpoint.model_copy(update={"budget_revision_id": budget_id})
        checkpoint_row = AssistantRunCheckpoint(
            id=checkpoint_id,
            run_id=run.id,
            sequence=_next_checkpoint_sequence(self.db, run.id),
            expected_state_revision=int(run.state_revision),
            committed_state_revision=int(run.state_revision) + 1,
            schema_version=3,
            manifest_revision_id=checkpoint.manifest_revision_id,
            policy_revision_id=checkpoint.policy_revision_id,
            budget_revision_id=budget_id,
            obligation_revision_id=obligation.id,
            provider_message_ordinal=checkpoint.provider_message_ordinal,
            provider_transcript_digest=checkpoint.provider_transcript_digest,
            phase=phase,
            logical_unit_id=str(call.logical_call_key),
            reason=failure_code,
            state_payload=encode_checkpoint_v3(checkpoint),
            state_digest=checkpoint_state_digest(checkpoint),
            created_at=now,
        )
        rows: list[Any] = [checkpoint_row]
        if budget_revision is not None:
            rows.insert(0, budget_revision)
        if obligation.id != run.current_obligation_revision_id:
            rows.insert(0, obligation)
        commit = DurableRunRepository(self.db).commit_local_commit_outcome_unknown(
            run_id=run.id,
            expected_revision=int(run.state_revision),
            events=(
                EventSpec(
                    event_key=f"capability_call.local_commit_unknown:{call.id}:{int(call.state_revision)}",
                    event_name="capability_call.local_commit_unknown",
                    payload={
                        "callId": str(call.id),
                        "failureCode": failure_code,
                    },
                    visibility="internal",
                ),
            ),
            children=DurableChildBundle(
                rows=rows,
                current_checkpoint_id=checkpoint_id,
                current_budget_revision_id=budget_id,
                current_obligation_revision_id=obligation.id,
            ),
        )
        return commit.run

    def _prepare_unknown_budget_revision(
        self,
        *,
        run: AssistantChatRun,
        snapshot: Any | None,
        now: datetime,
    ) -> AssistantRunBudgetRevision | None:
        """Persist the in-memory reservation lifecycle at the quarantine edge."""
        if snapshot is None or run.current_budget_revision_id is None:
            return None
        digest = str(getattr(snapshot, "ledger_digest", ""))
        if len(digest) != 64:
            return None
        current = self.db.get(AssistantRunBudgetRevision, run.current_budget_revision_id)
        if current is None or digest == str(current.budget_digest):
            return None
        payload = snapshot.model_dump(mode="json", by_alias=True)
        latest = (
            self.db.query(AssistantRunBudgetRevision.revision)
            .filter(AssistantRunBudgetRevision.run_id == run.id)
            .order_by(AssistantRunBudgetRevision.revision.desc())
            .limit(1)
            .scalar()
        )
        row = AssistantRunBudgetRevision(
            id=uuid4(),
            run_id=run.id,
            revision=int(latest or 0) + 1,
            parent_revision_id=current.id,
            parent_digest=current.budget_digest,
            budget_digest=digest,
            payload=payload,
            created_at=now,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def _load_checkpoint_context(
        self,
        *,
        run: AssistantChatRun,
        call: AssistantCapabilityCall,
        attempt: AssistantCapabilityCallAttempt,
    ) -> tuple[object, tuple[object, ...], dict[str, object]]:
        if run.current_checkpoint_id is None:
            self._invalid_evidence(
                "settlement requires a current v3 Checkpoint", call=call, run=run
            )
        row = (
            self.db.query(AssistantRunCheckpoint)
            .filter(AssistantRunCheckpoint.id == run.current_checkpoint_id)
            .with_for_update()
            .one_or_none()
        )
        if row is None or row.run_id != run.id:
            self._invalid_evidence(
                "settlement Checkpoint pointer is invalid", call=call, run=run
            )
        checkpoint = decode_checkpoint(row.state_payload)
        if int(getattr(checkpoint, "schema_version", 0)) != 3:
            self._invalid_evidence(
                "settlement requires Checkpoint schema v3", call=call, run=run
            )
        matches = [item for item in checkpoint.capability_calls if item.call_id == call.id]
        if len(matches) != 1:
            self._invalid_evidence(
                "settlement Checkpoint does not contain the Call exactly once",
                call=call,
                run=run,
            )
        state = matches[0]
        if (
            state.provider_tool_call_id != str(call.provider_tool_call_id)
            or state.logical_call_key != str(call.logical_call_key)
            or state.status != "executing"
            or state.attempt_id != attempt.id
            or state.interrupt_id != call.interrupt_id
            or state.approval_binding_digest != call.approval_binding_digest
            or state.result_message_digest is not None
            or state.output_artifact_id is not None
        ):
            self._invalid_evidence(
                "settlement Checkpoint contains stale or duplicate result state",
                call=call,
                run=run,
            )
        ordinal, transcript_digest, transcript = _current_transcript_digest(
            self.db, run.id
        )
        if (
            ordinal != int(checkpoint.provider_message_ordinal)
            or transcript_digest != checkpoint.provider_transcript_digest
        ):
            self._invalid_evidence(
                "settlement Provider transcript disagrees with Checkpoint",
                call=call,
                run=run,
            )
        tool_calls: dict[str, object] = {}
        for message in transcript:
            if isinstance(message, ProviderToolMessage):
                if message.call_id == str(call.provider_tool_call_id):
                    self._invalid_evidence(
                        "settlement would duplicate an existing Tool Result",
                        call=call,
                        run=run,
                    )
            if isinstance(message, ProviderAssistantMessage):
                for provider_call in message.tool_calls:
                    tool_calls[provider_call.call_id] = provider_call
        if str(call.provider_tool_call_id) not in tool_calls:
            self._invalid_evidence(
                "settlement Call has no preceding durable Provider Tool Call",
                call=call,
                run=run,
            )
        duplicate = (
            self.db.query(AssistantRunProviderMessage)
            .filter(
                AssistantRunProviderMessage.run_id == run.id,
                AssistantRunProviderMessage.tool_call_id
                == str(call.provider_tool_call_id),
            )
            .with_for_update()
            .first()
        )
        if duplicate is not None:
            self._invalid_evidence(
                "settlement durable Tool Result already exists",
                call=call,
                run=run,
            )
        return checkpoint, transcript, tool_calls

    def _commit_terminal_settlement(
        self,
        *,
        run: AssistantChatRun,
        call: AssistantCapabilityCall,
        attempt: AssistantCapabilityCallAttempt,
        outcome: Literal["succeeded", "failed"],
        result_artifact: AssistantRunArtifact | None,
        decoded_result: object | None,
        now: datetime,
    ) -> AssistantChatRun:
        checkpoint, prior, tool_calls = self._load_checkpoint_context(
            run=run, call=call, attempt=attempt
        )
        if decoded_result is not None:
            capability_result = decoded_result.capability_result  # type: ignore[attr-defined]
            artifact = result_artifact
        else:
            from app.assistant.capabilities.contracts import (
                CapabilityError,
                CapabilityMetrics,
                CapabilityResult,
            )

            capability_result = CapabilityResult(
                status="failed",
                user_text="The capability call failed before a trusted response was captured.",
                structured_output={"settlementOutcome": "failed"},
                artifact_refs=(),
                continuation=None,
                terminal_output=False,
                needs_followup=False,
                error=CapabilityError(
                    error_type="execution_failed",
                    safe_code=str(call.failure_code or "capability_settlement_failed"),
                    safe_message="Captured transport evidence proved the capability call failed.",
                    retry_disposition="never",
                    call_id=str(call.provider_tool_call_id),
                ),
                metrics=CapabilityMetrics(
                    duration_ms=0,
                    adapter_duration_ms=None,
                    input_bytes=0,
                    output_bytes=0,
                ),
            )
            payload = canonical_json_bytes(  # type: ignore[arg-type]
                {
                    "contractVersion": 1,
                    "callId": str(call.provider_tool_call_id),
                    "bindingContractDigest": str(call.authorization_digest),
                    "descriptorDigest": str(call.descriptor_digest),
                    "settlementOutcome": "failed",
                    "result": capability_result.model_dump(mode="json"),
                }
            )
            artifact = AssistantRunArtifact(
                run_id=run.id,
                kind="capability_call_settlement_result",
                media_type="application/json",
                storage_kind="inline",
                byte_size=len(payload),
                content_sha256=sha256_bytes(payload),
                inline_bytes=payload,
                metadata_json={
                    "contractVersion": 1,
                    "callId": str(call.id),
                    "settlementOutcome": "failed",
                },
                created_at=now,
            )
            self.db.add(artifact)
            self.db.flush()
            call.output_artifact_id = artifact.id
            self.db.flush()
        assert artifact is not None
        provider_call = tool_calls[str(call.provider_tool_call_id)]
        tool_messages: list[ProviderToolMessage] = [
            ProviderToolMessage(
                call_id=str(call.provider_tool_call_id),
                provider_alias=provider_call.provider_alias,  # type: ignore[attr-defined]
                content=project_tool_result_envelope(
                    domain_key=str(call.domain_key), result=capability_result
                ),
            )
        ]

        updated_calls = {item.call_id: item for item in checkpoint.capability_calls}
        target_state = updated_calls[call.id]
        updated_calls[call.id] = DurableCapabilityCallStateV1(
            call_id=target_state.call_id,
            logical_call_key=target_state.logical_call_key,
            provider_tool_call_id=target_state.provider_tool_call_id,
            provider_order=target_state.provider_order,
            status=str(call.status),
            attempt_id=attempt.id,
            output_artifact_id=artifact.id,
            interrupt_id=call.interrupt_id,
            approval_binding_digest=call.approval_binding_digest,
            result_message_digest=digest_provider_message(tool_messages[0]),
        )

        continuation = checkpoint.provider_loop_continuation
        try:
            open_calls = open_provider_tool_calls(prior)
        except (TypeError, ValueError) as exc:
            self._invalid_evidence(
                f"settlement durable Provider transcript is invalid: {exc}",
                call=call,
                run=run,
            )
        open_ids = [item.call_id for item in open_calls]
        if not open_ids or open_ids[0] != str(call.provider_tool_call_id):
            self._invalid_evidence(
                "settlement Call must be the first unpaired Provider Tool Call",
                call=call,
                run=run,
            )
        pending_ids = open_ids[1:]
        if continuation is not None:
            if continuation.waiting_call.call_id != str(call.provider_tool_call_id):
                self._invalid_evidence(
                    "settlement continuation waiting Call mismatch",
                    call=call,
                    run=run,
                )
            if list(continuation.pending_call_ids) != pending_ids:
                self._invalid_evidence(
                    "settlement continuation does not match the open sibling suffix",
                    call=call,
                    run=run,
                )
        for pending_id in pending_ids:
                sibling = (
                    self.db.query(AssistantCapabilityCall)
                    .filter(
                        AssistantCapabilityCall.run_id == run.id,
                        AssistantCapabilityCall.provider_tool_call_id == pending_id,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if sibling is None or sibling.id not in updated_calls:
                    self._invalid_evidence(
                        "settlement pending sibling is missing from durable Call state",
                        call=call,
                        run=run,
                    )
                sibling_state = updated_calls[sibling.id]
                sibling_status = str(sibling.status)
                if (
                    sibling_state.logical_call_key != str(sibling.logical_call_key)
                    or sibling_state.provider_tool_call_id
                    != str(sibling.provider_tool_call_id)
                    or sibling_state.status != sibling_status
                    or sibling_state.attempt_id is not None
                    or sibling_state.output_artifact_id is not None
                    or sibling_state.result_message_digest is not None
                    or sibling_state.interrupt_id != sibling.interrupt_id
                    or sibling_state.approval_binding_digest
                    != sibling.approval_binding_digest
                ):
                    self._invalid_evidence(
                        "settlement pending sibling Checkpoint state is stale or mismatched",
                        call=call,
                        run=run,
                    )
                sibling_attempt = (
                    self.db.query(AssistantCapabilityCallAttempt)
                    .filter(AssistantCapabilityCallAttempt.call_id == sibling.id)
                    .with_for_update()
                    .first()
                )
                if (
                    sibling_attempt is not None
                    or int(sibling.attempt_count or 0) != 0
                    or sibling.side_effect_started_at is not None
                    or sibling.interrupt_id is not None
                    or sibling.output_artifact_id is not None
                    or sibling_status not in {"proposed", "authorized", "denied"}
                ):
                    self._invalid_evidence(
                        "started or non-cancellable sibling requires independent reconciliation",
                        call=call,
                        run=run,
                    )
                if sibling_status != "denied":
                    sibling = self.calls.transition_call(
                        call_id=sibling.id,
                        expected_call_revision=int(sibling.state_revision),
                        expected_run_revision=int(run.state_revision),
                        to_status="cancelled",
                        lease=None,
                        allow_while_cancelling=True,
                        now=now,
                    )
                provider_sibling = tool_calls.get(pending_id)
                if provider_sibling is None:
                    self._invalid_evidence(
                        "pending sibling has no preceding Provider Tool Call",
                        call=call,
                        run=run,
                    )
                sibling_tool = ProviderToolMessage(
                    call_id=pending_id,
                    provider_alias=provider_sibling.provider_alias,  # type: ignore[attr-defined]
                    content=make_cancelled_envelope(
                        domain_key=str(sibling.domain_key),
                        status="cancelled_before_start",
                        safe_code=(
                            "policy_denied"
                            if str(sibling.status) == "denied"
                            else "cancelled_before_start"
                        ),
                        safe_message=(
                            "pending sibling was denied by policy"
                            if str(sibling.status) == "denied"
                            else "pending sibling closed during cancellation settlement"
                        ),
                        call_id=pending_id,
                    ),
                )
                tool_messages.append(sibling_tool)
                old = updated_calls[sibling.id]
                updated_calls[sibling.id] = DurableCapabilityCallStateV1(
                    call_id=old.call_id,
                    logical_call_key=old.logical_call_key,
                    provider_tool_call_id=old.provider_tool_call_id,
                    provider_order=old.provider_order,
                    status=str(sibling.status),
                    attempt_id=None,
                    output_artifact_id=None,
                    interrupt_id=sibling.interrupt_id,
                    approval_binding_digest=sibling.approval_binding_digest,
                    result_message_digest=digest_provider_message(sibling_tool),
                )

        transcript = tuple(prior) + tuple(tool_messages)
        validate_provider_transcript(transcript)
        ordinal = int(checkpoint.provider_message_ordinal)
        provider_rows = _build_provider_message_rows(
            run_id=run.id,
            messages=tuple(tool_messages),
            start_ordinal=ordinal + 1,
            manifest_revision_id=checkpoint.manifest_revision_id,
            policy_revision_id=checkpoint.policy_revision_id,
            obligation_revision_id=checkpoint.obligation_revision_id,
        )
        final_ordinal = ordinal + len(tool_messages)
        final_digest = digest_provider_transcript(transcript)
        final_checkpoint = checkpoint.model_copy(
            update={
                "phase": "terminal",
                "provider_message_ordinal": final_ordinal,
                "provider_transcript_digest": final_digest,
                "provider_loop_continuation": None,
                "next_action": DurableNextActionV2(kind="terminal"),
                "artifact_ids": tuple(
                    dict.fromkeys([*checkpoint.artifact_ids, artifact.id])
                ),
                "capability_calls": tuple(
                    sorted(updated_calls.values(), key=lambda item: item.provider_order)
                ),
            }
        )
        checkpoint_id = uuid4()
        checkpoint_row = AssistantRunCheckpoint(
            id=checkpoint_id,
            run_id=run.id,
            sequence=_next_checkpoint_sequence(self.db, run.id),
            expected_state_revision=int(run.state_revision),
            committed_state_revision=int(run.state_revision) + 1,
            schema_version=3,
            manifest_revision_id=checkpoint.manifest_revision_id,
            policy_revision_id=checkpoint.policy_revision_id,
            budget_revision_id=checkpoint.budget_revision_id,
            obligation_revision_id=checkpoint.obligation_revision_id,
            provider_message_ordinal=final_ordinal,
            provider_transcript_digest=final_digest,
            phase="terminal",
            logical_unit_id=str(call.logical_call_key),
            reason="capability_call_settled",
            state_payload=encode_checkpoint_v3(final_checkpoint),
            state_digest=checkpoint_state_digest(final_checkpoint),
            created_at=now,
        )
        commit = DurableRunRepository(self.db).commit_cancellation_settlement(
            run_id=run.id,
            expected_revision=int(run.state_revision),
            target_status=STATUS_CANCELLING,
            events=(
                EventSpec(
                    event_key=f"capability_call.settled:{call.id}:{attempt.id}",
                    event_name="capability_call.settled",
                    payload={
                        "callId": str(call.id),
                        "attemptId": str(attempt.id),
                        "outcome": outcome,
                    },
                    visibility="public",
                ),
            ),
            children=DurableChildBundle(
                rows=[*provider_rows, checkpoint_row],
                current_checkpoint_id=checkpoint_id,
            ),
        )
        return commit.run

    def _commit_unknown_settlement(
        self,
        *,
        run: AssistantChatRun,
        call: AssistantCapabilityCall,
        attempt: AssistantCapabilityCallAttempt,
        now: datetime,
    ) -> AssistantChatRun:
        checkpoint, _prior, _tool_calls = self._load_checkpoint_context(
            run=run, call=call, attempt=attempt
        )
        if run.current_obligation_revision_id is None:
            self._invalid_evidence(
                "unknown settlement requires a current obligation ledger",
                call=call,
                run=run,
            )
        current = (
            self.db.query(AssistantRunObligationRevision)
            .filter(
                AssistantRunObligationRevision.id
                == run.current_obligation_revision_id
            )
            .with_for_update()
            .one_or_none()
        )
        if current is None or current.run_id != run.id:
            self._invalid_evidence(
                "unknown settlement obligation pointer is invalid",
                call=call,
                run=run,
            )
        ledger = ObligationLedgerState.model_validate(current.payload)
        pending = [item for item in ledger.obligations if item.status == "pending"]
        matching = [
            item
            for item in pending
            if _is_exact_call_reconciliation_obligation(item, call.id)
        ]
        if not matching:
            ledger, created = pure_create_obligation(
                ledger,
                build_reserved_obligation(
                    run_id=run.id,
                    obligation_type="reconciliation",
                    owner_kind="capability_call",
                    owner_id=str(call.id),
                    source_call_id=str(call.id),
                    revision=int(current.revision) + 1,
                ),
            )
            if not created.allowed:
                self._invalid_evidence(
                    "reconciliation obligation creation failed",
                    call=call,
                    run=run,
                )
            obligation_row = AssistantRunObligationRevision(
                id=uuid4(),
                run_id=run.id,
                revision=int(current.revision) + 1,
                parent_revision_id=current.id,
                parent_digest=current.obligation_digest,
                obligation_digest=ledger.ledger_digest,
                payload=ledger.model_dump(mode="json", by_alias=True),
                created_at=now,
            )
            self.db.add(obligation_row)
            self.db.flush()
        else:
            if len(matching) != 1:
                self._invalid_evidence(
                    "unknown settlement requires one exact reconciliation obligation",
                    call=call,
                    run=run,
                )
            obligation_row = current

        states = []
        replaced = False
        for item in checkpoint.capability_calls:
            if item.call_id != call.id:
                states.append(item)
                continue
            states.append(
                DurableCapabilityCallStateV1(
                    call_id=item.call_id,
                    logical_call_key=item.logical_call_key,
                    provider_tool_call_id=item.provider_tool_call_id,
                    provider_order=item.provider_order,
                    status="needs_reconciliation",
                    attempt_id=attempt.id,
                    output_artifact_id=None,
                    interrupt_id=call.interrupt_id,
                    approval_binding_digest=call.approval_binding_digest,
                    result_message_digest=None,
                )
            )
            replaced = True
        if not replaced:
            self._invalid_evidence(
                "unknown settlement Checkpoint lacks Call state", call=call, run=run
            )
        has_continuation = checkpoint.provider_loop_continuation is not None
        phase = "waiting" if has_continuation else "ready_for_provider"
        next_action = DurableNextActionV2(
            kind="wait" if has_continuation else "reconcile"
        )
        updated = checkpoint.model_copy(
            update={
                "phase": phase,
                "obligation_revision_id": obligation_row.id,
                "next_action": next_action,
                "capability_calls": tuple(states),
            }
        )
        checkpoint_id = uuid4()
        checkpoint_row = AssistantRunCheckpoint(
            id=checkpoint_id,
            run_id=run.id,
            sequence=_next_checkpoint_sequence(self.db, run.id),
            expected_state_revision=int(run.state_revision),
            committed_state_revision=int(run.state_revision) + 1,
            schema_version=3,
            manifest_revision_id=checkpoint.manifest_revision_id,
            policy_revision_id=checkpoint.policy_revision_id,
            budget_revision_id=checkpoint.budget_revision_id,
            obligation_revision_id=obligation_row.id,
            provider_message_ordinal=checkpoint.provider_message_ordinal,
            provider_transcript_digest=checkpoint.provider_transcript_digest,
            phase=phase,
            logical_unit_id=str(call.logical_call_key),
            reason="capability_call_needs_reconciliation",
            state_payload=encode_checkpoint_v3(updated),
            state_digest=checkpoint_state_digest(updated),
            created_at=now,
        )
        rows = [checkpoint_row]
        if obligation_row is not current:
            rows.insert(0, obligation_row)
        commit = DurableRunRepository(self.db).commit_cancellation_settlement(
            run_id=run.id,
            expected_revision=int(run.state_revision),
            target_status=STATUS_NEEDS_RECONCILIATION,
            events=(
                EventSpec(
                    event_key=f"capability_call.settled:{call.id}:{attempt.id}",
                    event_name="capability_call.settled",
                    payload={
                        "callId": str(call.id),
                        "attemptId": str(attempt.id),
                        "outcome": "unknown",
                    },
                    visibility="public",
                ),
            ),
            children=DurableChildBundle(
                rows=rows,
                current_checkpoint_id=checkpoint_id,
                current_obligation_revision_id=obligation_row.id,
            ),
        )
        return commit.run

    def _validate_evidence(
        self,
        *,
        request: SettlementRequest,
        run: AssistantChatRun,
        call: AssistantCapabilityCall,
    ) -> tuple[
        AssistantCapabilityCallAttempt,
        AssistantRunArtifact | None,
        str | None,
        object | None,
    ]:
        """Load and validate evidence after locking Run then Call then Attempt."""
        attempt = (
            self.db.query(AssistantCapabilityCallAttempt)
            .filter(AssistantCapabilityCallAttempt.id == request.attempt_id)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if attempt is None:
            self._invalid_evidence(
                "settlement Attempt does not exist", call=call, run=run
            )
        assert attempt is not None
        if attempt.call_id != call.id:
            self._invalid_evidence(
                "settlement Attempt belongs to another CapabilityCall",
                call=call,
                run=run,
            )
        if int(attempt.attempt_number) != int(call.attempt_count):
            self._invalid_evidence(
                "settlement requires evidence from the latest Call Attempt",
                call=call,
                run=run,
            )
        if str(attempt.request_digest or "") != str(call.input_digest):
            self._invalid_evidence(
                "settlement Attempt request digest does not match the Call input",
                call=call,
                run=run,
            )

        result_artifact = None
        if request.result_artifact_id is not None:
            result_artifact = (
                self.db.query(AssistantRunArtifact)
                .filter(AssistantRunArtifact.id == request.result_artifact_id)
                .populate_existing()
                .with_for_update()
                .one_or_none()
            )
            if result_artifact is None:
                self._invalid_evidence(
                    "settlement result Artifact does not exist", call=call, run=run
                )

        captured_failure_code = None
        decoded_result = None
        if request.outcome == "succeeded":
            if str(attempt.status) not in {"response_received", "committed"}:
                self._invalid_evidence(
                    "successful settlement requires a captured response Attempt",
                    call=call,
                    run=run,
                )
            if attempt.response_digest is None:
                self._invalid_evidence(
                    "successful settlement Attempt lacks response_digest",
                    call=call,
                    run=run,
                )
            if result_artifact is None:
                self._invalid_evidence(
                    "successful settlement requires a result Artifact",
                    call=call,
                    run=run,
                )
            assert result_artifact is not None
            _failure, decoded_result = self._validate_result_artifact(
                artifact=result_artifact,
                run=run,
                attempt=attempt,
                call=call,
                expected_status="completed",
            )
        elif request.outcome == "failed":
            if str(attempt.status) in {"response_received", "committed"}:
                if attempt.response_digest is None or result_artifact is None:
                    self._invalid_evidence(
                        "captured failed settlement requires response evidence and a result Artifact",
                        call=call,
                        run=run,
                    )
                assert result_artifact is not None
                captured_failure_code, decoded_result = self._validate_result_artifact(
                    artifact=result_artifact,
                    run=run,
                    attempt=attempt,
                    call=call,
                    expected_status="failed",
                )
            else:
                if str(attempt.status) != "failed" or not attempt.error_code:
                    self._invalid_evidence(
                        "failed settlement requires captured failure evidence",
                        call=call,
                        run=run,
                    )
                if result_artifact is not None:
                    self._invalid_evidence(
                        "transport-failed settlement cannot attach a result Artifact",
                        call=call,
                        run=run,
                    )
        else:
            status = str(attempt.status)
            if not attempt.side_effect_started or attempt.side_effect_started_at is None:
                self._invalid_evidence(
                    "unknown settlement requires durable Attempt effect-start evidence",
                    call=call,
                    run=run,
                )
            if status == "dispatched" and attempt.response_digest is not None:
                self._invalid_evidence(
                    "dispatched unknown settlement cannot carry response evidence",
                    call=call,
                    run=run,
                )
            if status == "uncertain" and not attempt.error_code:
                self._invalid_evidence(
                    "uncertain settlement Attempt lacks classification evidence",
                    call=call,
                    run=run,
                )
            if status not in {"dispatched", "uncertain"}:
                self._invalid_evidence(
                    "unknown settlement requires dispatched or uncertain Attempt evidence",
                    call=call,
                    run=run,
                )
            if result_artifact is not None:
                self._invalid_evidence(
                    "unknown settlement cannot attach a result Artifact",
                    call=call,
                    run=run,
                )
        return attempt, result_artifact, captured_failure_code, decoded_result

    def _validate_result_artifact(
        self,
        *,
        artifact: AssistantRunArtifact,
        run: AssistantChatRun,
        attempt: AssistantCapabilityCallAttempt,
        call: AssistantCapabilityCall,
        expected_status: Literal["completed", "failed"],
    ) -> tuple[str | None, object]:
        if artifact.run_id != run.id:
            self._invalid_evidence(
                "settlement result Artifact belongs to another Run",
                call=call,
                run=run,
            )
        if (
            str(artifact.kind) != "capability_call_result"
            or str(artifact.media_type) != "application/json"
            or (artifact.metadata_json or {}).get("contractVersion") != 1
        ):
            self._invalid_evidence(
                "settlement result Artifact is not a normalized capability result",
                call=call,
                run=run,
            )
        if str(artifact.content_sha256) != str(attempt.response_digest):
            self._invalid_evidence(
                "settlement result Artifact digest was not captured by the Attempt",
                call=call,
                run=run,
            )
        if str(artifact.storage_kind) != "inline" or artifact.inline_bytes is None:
            self._invalid_evidence(
                "settlement cannot trust object-backed result Artifact without a reader",
                call=call,
                run=run,
            )
        payload = bytes(artifact.inline_bytes)
        if len(payload) != int(artifact.byte_size) or hashlib.sha256(
            payload
        ).hexdigest() != str(artifact.content_sha256):
            self._invalid_evidence(
                "settlement result Artifact content does not match its durable digest",
                call=call,
                run=run,
            )
        if call.provider_tool_call_id is None:
            self._invalid_evidence(
                "settlement result lacks a provider tool-call identity",
                call=call,
                run=run,
            )
        try:
            decoded = decode_capability_result(
                payload,
                expected_digest=str(artifact.content_sha256),
                expected_call_id=str(call.provider_tool_call_id),
                expected_binding_contract_digest=str(call.authorization_digest),
                expected_descriptor_digest=str(call.descriptor_digest),
            )
        except (CapabilityResultCodecError, AttributeError, KeyError, TypeError) as exc:
            self._invalid_evidence(
                f"settlement result Artifact contract is invalid: {exc}",
                call=call,
                run=run,
            )
        if str(decoded.capability_result.status) != expected_status:
            self._invalid_evidence(
                f"settlement result status does not prove {expected_status}",
                call=call,
                run=run,
            )
        if expected_status == "failed":
            error = decoded.capability_result.error
            return (str(error.safe_code) if error is not None else None), decoded
        return None, decoded

    @staticmethod
    def _invalid_evidence(
        message: str,
        *,
        call: AssistantCapabilityCall,
        run: AssistantChatRun,
    ) -> NoReturn:
        raise CapabilityCallConflict(
            CODE_SETTLEMENT_EVIDENCE_INVALID,
            message,
            call=call,
            run=run,
        )

    @staticmethod
    def _validate_transition(call: object, to_status: str) -> None:
        try:
            validate_call_transition(
                from_status=str(getattr(call, "status")),
                to_status=to_status,
                side_effect_started_at_is_set=bool(
                    getattr(call, "side_effect_started_at", None) is not None
                ),
                execution_mode=str(getattr(call, "execution_mode")),
            )
        except CallTransitionError as exc:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION, exc.message, call=call  # type: ignore[arg-type]
            ) from exc

    def refuse_cancel_finalizer_if_unproven(self, run_id: UUID) -> None:
        """Raise if cancelling -> cancelled would lie about started calls."""
        if self.calls.has_unproven_started_calls(run_id):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "cannot finalize cancelled while started calls are unproven",
            )


__all__ = [
    "CODE_SETTLEMENT_EVIDENCE_INVALID",
    "SETTLEMENT_OUTCOME_UNKNOWN",
    "CapabilityCallSettlementRepository",
    "SettlementRequest",
    "compute_settlement_evidence_digest",
]

"""Conversation-scoped durable Interrupt HTTP APIs (Plan 07 Task 6).

Pending/detail/token/resolve under the existing conversation boundary.
HTTP resolution queues only — never constructs Workflow/Provider/Gateway.
Expiry scanner uses the same Run-first lock order and repository CAS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.durable.codec import (
    checkpoint_state_digest,
    decode_checkpoint,
    decode_checkpoint_v3,
    encode_checkpoint_v2,
    encode_checkpoint_v3,
)
from app.assistant.durable.contracts import (
    DurableAgentCheckpointV2,
    DurableAgentCheckpointV3,
    DurableCapabilityCallStateV1,
    DurableNextActionV2,
)
from app.assistant.durable.models import (
    AssistantRunBudgetRevision,
    AssistantRunCheckpoint,
    AssistantRunInterrupt,
    AssistantRunManifestRevision,
    AssistantRunPolicyRevision,
)
from app.assistant.durable.repository import (
    CODE_STALE_REVISION as CODE_RUN_STALE_REVISION,
    CODE_TERMINAL_IMMUTABLE,
    DurableChildBundle,
    DurableRunConflict,
    DurableRunRepository,
    EventSpec,
    STATUS_CANCELLED,
    STATUS_CANCELLING,
    WAITING_STATUSES,
)
from app.assistant.models import AssistantChatRun
from app.assistant.policy.budgets import BudgetLedgerState
from app.assistant.run_service import AssistantChatRunService
from app.assistant.workflow.durable.interrupts import (
    CODE_CALL_OWNED_APPROVAL_REQUIRED as _CODE_CALL_OWNED_APPROVAL_REQUIRED,
    CODE_INTERRUPT_ALREADY_RESOLVED,
    CODE_INTERRUPT_COMMENT_TOO_LONG,
    CODE_INTERRUPT_EXPIRED,
    CODE_INTERRUPT_IDEMPOTENCY_CONFLICT,
    CODE_INTERRUPT_NOT_FOUND,
    CODE_INTERRUPT_NOT_EXPIRED,
    CODE_INTERRUPT_NOT_PENDING,
    CODE_INTERRUPT_OUTCOME_INVALID,
    CODE_INTERRUPT_PEPPER_REQUIRED,
    CODE_INTERRUPT_SCHEMA_INVALID,
    CODE_STALE_REVISION as CODE_INTERRUPT_STALE_REVISION,
    CODE_INTERRUPT_TOKEN_INVALID,
    CODE_INTERRUPT_TOKEN_STALE,
    CODE_INTERRUPT_VALUES_INVALID,
    DurableInterruptRepository,
    InterruptConflict,
    InterruptSchemaError,
    InterruptTokenError,
    derive_resume_budget_ledger,
    render_interrupt_fields,
)

# Re-export public not-pending code for API mapping convenience.
_ = CODE_INTERRUPT_NOT_PENDING
from app.common.exceptions import ApiException
from app.common.time import utcnow
from app.config import get_settings

# ---------------------------------------------------------------------------
# Stable public HTTP reason codes (Plan 07 §13)
# ---------------------------------------------------------------------------

CODE_DURABLE_INTERRUPT_NOT_FOUND = "durable_interrupt_not_found"
CODE_DURABLE_INTERRUPT_CONVERSATION_MISMATCH = "durable_interrupt_conversation_mismatch"
CODE_INTERRUPT_REQUEST_REVISION_MISMATCH = "interrupt_request_revision_mismatch"
CODE_INTERRUPT_RUN_REVISION_MISMATCH = "interrupt_run_revision_mismatch"
CODE_INTERRUPT_RUN_CANCELLED = "interrupt_run_cancelled"
CODE_DURABLE_INTERRUPT_AUTH_MODE_UNAVAILABLE = "durable_interrupt_auth_mode_unavailable"
CODE_INTERRUPT_ALREADY_RESOLVED = "interrupt_already_resolved"
CODE_CALL_OWNED_APPROVAL_REQUIRED = _CODE_CALL_OWNED_APPROVAL_REQUIRED

# Outcomes that continue the frozen graph (queue resume).
_QUEUEING_OUTCOMES = frozenset({"approved", "submitted"})
_TERMINAL_NON_QUEUE_OUTCOMES = frozenset({"rejected", "cancelled", "expired"})


@dataclass(frozen=True)
class ExpiryScanResult:
    expired_count: int
    cancelled_run_count: int
    skipped_count: int
    conflict_count: int
    queued_run_count: int = 0


class DurableInterruptApiError(Exception):
    """Mapped to ApiException by the service boundary."""

    def __init__(
        self,
        reason_code: str,
        message: str = "",
        *,
        status_code: int = 409,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.message = message or reason_code
        self.status_code = status_code
        self.details = dict(details or {})
        super().__init__(self.message)


def _raise_api(exc: DurableInterruptApiError) -> None:
    raise ApiException(
        status_code=exc.status_code,
        code=exc.status_code * 100 if exc.status_code < 1000 else exc.status_code,
        message=exc.message,
        details={"reasonCode": exc.reason_code, **exc.details},
    ) from exc


def _settings_repo_kwargs() -> dict[str, Any]:
    s = get_settings()
    return {
        "token_pepper": s.assistant_interrupt_token_pepper or None,
        "comment_max_chars": int(s.assistant_interrupt_comment_max_chars),
        "default_ttl_sec": int(s.assistant_interrupt_default_ttl_sec),
        "max_ttl_sec": int(s.assistant_interrupt_max_ttl_sec),
    }


def _require_pepper_for_token_ops() -> str:
    s = get_settings()
    pepper = (s.assistant_interrupt_token_pepper or "").strip()
    if not pepper:
        raise DurableInterruptApiError(
            CODE_DURABLE_INTERRUPT_AUTH_MODE_UNAVAILABLE,
            "ASSISTANT_INTERRUPT_TOKEN_PEPPER is required for durable interrupt token/decision APIs",
            status_code=503,
        )
    return pepper


def _map_conflict_code(code: str) -> tuple[str, int]:
    """Map repository conflict codes to public HTTP reason + status."""
    mapping: dict[str, tuple[str, int]] = {
        CODE_INTERRUPT_NOT_FOUND: (CODE_DURABLE_INTERRUPT_NOT_FOUND, 404),
        CODE_INTERRUPT_NOT_PENDING: (CODE_INTERRUPT_NOT_PENDING, 409),
        CODE_INTERRUPT_ALREADY_RESOLVED: (CODE_INTERRUPT_ALREADY_RESOLVED, 409),
        CODE_INTERRUPT_EXPIRED: (CODE_INTERRUPT_EXPIRED, 409),
        CODE_INTERRUPT_TOKEN_INVALID: (CODE_INTERRUPT_TOKEN_INVALID, 403),
        CODE_INTERRUPT_TOKEN_STALE: (CODE_INTERRUPT_TOKEN_STALE, 409),
        CODE_INTERRUPT_IDEMPOTENCY_CONFLICT: (CODE_INTERRUPT_IDEMPOTENCY_CONFLICT, 409),
        CODE_INTERRUPT_VALUES_INVALID: (CODE_INTERRUPT_VALUES_INVALID, 422),
        CODE_INTERRUPT_SCHEMA_INVALID: (CODE_INTERRUPT_VALUES_INVALID, 422),
        CODE_INTERRUPT_COMMENT_TOO_LONG: (CODE_INTERRUPT_VALUES_INVALID, 422),
        CODE_INTERRUPT_OUTCOME_INVALID: (CODE_INTERRUPT_VALUES_INVALID, 422),
        CODE_INTERRUPT_PEPPER_REQUIRED: (CODE_DURABLE_INTERRUPT_AUTH_MODE_UNAVAILABLE, 503),
        CODE_CALL_OWNED_APPROVAL_REQUIRED: (CODE_CALL_OWNED_APPROVAL_REQUIRED, 409),
        CODE_RUN_STALE_REVISION: (CODE_INTERRUPT_RUN_REVISION_MISMATCH, 409),
        CODE_TERMINAL_IMMUTABLE: (CODE_INTERRUPT_RUN_CANCELLED, 409),
    }
    if code == CODE_RUN_STALE_REVISION or code == "stale_revision":
        # Distinguish request vs run revision by message when possible; default run.
        return CODE_INTERRUPT_RUN_REVISION_MISMATCH, 409
    return mapping.get(code, (code, 409))


def _map_stale_message(message: str) -> str:
    msg = (message or "").lower()
    if "request_revision" in msg:
        return CODE_INTERRUPT_REQUEST_REVISION_MISMATCH
    if "request_run_revision" in msg or "state_revision" in msg or "run" in msg:
        return CODE_INTERRUPT_RUN_REVISION_MISMATCH
    return CODE_INTERRUPT_RUN_REVISION_MISMATCH


def _conflict_to_api(exc: InterruptConflict | DurableRunConflict | InterruptTokenError | InterruptSchemaError) -> DurableInterruptApiError:
    if isinstance(exc, InterruptTokenError):
        return DurableInterruptApiError(
            CODE_DURABLE_INTERRUPT_AUTH_MODE_UNAVAILABLE,
            str(exc),
            status_code=503,
        )
    if isinstance(exc, InterruptSchemaError):
        text = str(exc)
        code = CODE_INTERRUPT_VALUES_INVALID
        if CODE_INTERRUPT_COMMENT_TOO_LONG in text:
            code = CODE_INTERRUPT_VALUES_INVALID
        elif CODE_INTERRUPT_OUTCOME_INVALID in text:
            code = CODE_INTERRUPT_VALUES_INVALID
        return DurableInterruptApiError(code, text, status_code=422)

    code = getattr(exc, "code", "protocol_error")
    message = getattr(exc, "message", str(exc))
    if code in {CODE_RUN_STALE_REVISION, "stale_revision"}:
        public = _map_stale_message(message)
        return DurableInterruptApiError(public, message or public, status_code=409)
    public, status = _map_conflict_code(str(code))
    return DurableInterruptApiError(public, message or public, status_code=status)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _allowed_actions(
    *, kind: str, status: str, interrupt_origin: str = "workflow_node"
) -> list[str]:
    if status != "pending":
        return []
    if interrupt_origin == "capability_call":
        # Call-owned approval has a separate authenticated decision route;
        # resume tokens are deliberately not exposed as an alternate path.
        return ["approve", "reject", "cancel"] if kind == "approval" else []
    if kind == "approval":
        return ["approve", "reject", "token", "resolve"]
    if kind == "input":
        return ["submit", "token", "resolve"]
    return ["token", "resolve"]


def serialize_interrupt_safe(
    interrupt: AssistantRunInterrupt,
    *,
    run: AssistantChatRun,
    include_resolution_request_id: bool = True,
) -> dict[str, Any]:
    """Public safe render state. Never exposes digests/tokens/values/comments."""
    status = str(interrupt.status)
    kind = str(interrupt.kind)
    origin = str(getattr(interrupt, "interrupt_origin", "workflow_node"))
    stored_payload = dict(interrupt.request_payload or {})
    if origin == "capability_call":
        # Binding digests and identity fields are server-side evidence, not a
        # client-facing pending card. The aggregate persists a bounded safe
        # proposal separately for display.
        safe_payload = stored_payload.get("safeRequestPayload")
        request_payload = dict(safe_payload) if isinstance(safe_payload, Mapping) else {}
    else:
        request_payload = stored_payload
    payload: dict[str, Any] = {
        "interruptId": str(interrupt.id),
        "runId": str(run.id),
        "conversationId": str(run.conversation_id),
        "messageId": (
            str(run.assistant_message_id)
            if getattr(run, "assistant_message_id", None) is not None
            else None
        ),
        "status": status,
        "kind": kind,
        "requestRevision": int(interrupt.request_revision),
        "runRevision": int(interrupt.request_run_revision),
        "tokenRevision": int(interrupt.token_revision),
        "expiresAt": _iso(interrupt.expires_at),
        "allowedActions": _allowed_actions(
            kind=kind, status=status, interrupt_origin=origin
        ),
        "fields": render_interrupt_fields(interrupt.field_schema),
        "requestPayload": request_payload,
        "initialValues": dict(interrupt.initial_values or {}),
        "nodeId": str(interrupt.node_id) if interrupt.node_id is not None else None,
        "nodeVisitId": (
            str(interrupt.node_visit_id)
            if interrupt.node_visit_id is not None
            else None
        ),
        "resolvedAt": _iso(interrupt.resolved_at),
    }
    if (
        include_resolution_request_id
        and status != "pending"
        and interrupt.resolution_request_id is not None
    ):
        payload["resolutionRequestId"] = str(interrupt.resolution_request_id)
    return payload


def _authorize_run(
    db: Session,
    *,
    conversation_id: UUID,
    run_id: UUID,
) -> AssistantChatRun:
    from app.assistant.evaluation.contracts import reraise_evaluation_id_as_not_found

    run_svc = AssistantChatRunService(db)
    try:
        run = run_svc.get_run(conversation_id=conversation_id, run_id=run_id)
    except ValueError as exc:
        # Eval-namespace IDs must surface as production not-found (404), not 500.
        reraise_evaluation_id_as_not_found(
            exc,
            not_found=DurableInterruptApiError(
                CODE_DURABLE_INTERRUPT_NOT_FOUND,
                f"run not found: {run_id}",
                status_code=404,
            ),
        )
    if run is None:
        # Distinguish missing run vs conversation mismatch.
        raw = db.get(AssistantChatRun, run_id)
        if raw is not None and raw.conversation_id != conversation_id:
            raise DurableInterruptApiError(
                CODE_DURABLE_INTERRUPT_CONVERSATION_MISMATCH,
                "run does not belong to conversation",
                status_code=404,
            )
        raise DurableInterruptApiError(
            CODE_DURABLE_INTERRUPT_NOT_FOUND,
            f"run not found: {run_id}",
            status_code=404,
        )
    if str(run.runtime_kind or "") != "main_agent":
        raise DurableInterruptApiError(
            CODE_DURABLE_INTERRUPT_NOT_FOUND,
            "run is not a durable main_agent run",
            status_code=404,
        )
    return run


def _get_interrupt_for_run(
    db: Session,
    *,
    run_id: UUID,
    interrupt_id: UUID,
) -> AssistantRunInterrupt:
    row = db.get(AssistantRunInterrupt, interrupt_id)
    if row is None or row.run_id != run_id:
        raise DurableInterruptApiError(
            CODE_DURABLE_INTERRUPT_NOT_FOUND,
            f"interrupt not found: {interrupt_id}",
            status_code=404,
        )
    return row


# ---------------------------------------------------------------------------
# Read APIs
# ---------------------------------------------------------------------------


def list_pending_interrupts(
    db: Session,
    *,
    conversation_id: UUID,
    run_id: UUID,
) -> list[dict[str, Any]]:
    run = _authorize_run(db, conversation_id=conversation_id, run_id=run_id)
    repo = DurableInterruptRepository(db, **_settings_repo_kwargs())
    pending = repo.get_pending_for_run(run_id)
    if pending is None:
        return []
    return [serialize_interrupt_safe(pending, run=run, include_resolution_request_id=False)]


def get_interrupt_detail(
    db: Session,
    *,
    conversation_id: UUID,
    run_id: UUID,
    interrupt_id: UUID,
) -> dict[str, Any]:
    run = _authorize_run(db, conversation_id=conversation_id, run_id=run_id)
    row = _get_interrupt_for_run(db, run_id=run_id, interrupt_id=interrupt_id)
    return serialize_interrupt_safe(row, run=run)


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


def rotate_interrupt_token(
    db: Session,
    *,
    conversation_id: UUID,
    run_id: UUID,
    interrupt_id: UUID,
    expected_request_revision: int,
    expected_run_revision: int,
) -> dict[str, Any]:
    run = _authorize_run(db, conversation_id=conversation_id, run_id=run_id)
    interrupt_row = _get_interrupt_for_run(
        db, run_id=run_id, interrupt_id=interrupt_id
    )
    if str(interrupt_row.interrupt_origin) == "capability_call":
        raise DurableInterruptApiError(
            CODE_CALL_OWNED_APPROVAL_REQUIRED,
            "call-owned approvals require the operator decision boundary",
            status_code=409,
        )
    pepper = _require_pepper_for_token_ops()
    if str(run.status) in {STATUS_CANCELLED, STATUS_CANCELLING, "failed", "completed"}:
        raise DurableInterruptApiError(
            CODE_INTERRUPT_RUN_CANCELLED,
            f"run status is {run.status}",
            status_code=409,
        )
    repo = DurableInterruptRepository(db, token_pepper=pepper, **{
        k: v for k, v in _settings_repo_kwargs().items() if k != "token_pepper"
    })
    try:
        result = repo.rotate_token(
            run_id=run_id,
            interrupt_id=interrupt_id,
            expected_request_revision=expected_request_revision,
            expected_run_revision=expected_run_revision,
            token_pepper=pepper,
        )
        db.commit()
    except (InterruptConflict, DurableRunConflict, InterruptTokenError) as exc:
        db.rollback()
        raise _conflict_to_api(exc) from exc
    return {
        "token": result.token,
        "tokenRevision": int(result.token_revision),
    }


# ---------------------------------------------------------------------------
# Resolve (queue-only HTTP path)
# ---------------------------------------------------------------------------


def _load_parent_ledger(db: Session, budget_revision_id: UUID) -> BudgetLedgerState:
    row = db.get(AssistantRunBudgetRevision, budget_revision_id)
    if row is None:
        raise DurableInterruptApiError(
            "protocol_error",
            "parent budget revision missing",
            status_code=409,
        )
    payload = dict(row.payload or {})
    if "ledgerDigest" not in payload and "ledger_digest" not in payload:
        raise DurableInterruptApiError(
            "protocol_error",
            "parent budget revision is not a full BudgetLedgerState",
            status_code=409,
        )
    try:
        return BudgetLedgerState.model_validate(payload)
    except Exception as exc:
        raise DurableInterruptApiError(
            "protocol_error",
            f"parent budget ledger invalid: {exc}",
            status_code=409,
        ) from exc


def _build_resume_children(
    db: Session,
    *,
    run: AssistantChatRun,
    interrupt: AssistantRunInterrupt,
    expected_revision: int,
    call_owned_call: Any | None = None,
    call_owned_outcome: str | None = None,
    _force_generic: bool = False,
) -> tuple[list[Any], UUID, UUID, datetime]:
    """Derive child budget + resume-ready Checkpoint rows (not yet committed)."""
    if str(interrupt.interrupt_origin) == "capability_call" and not _force_generic:
        if call_owned_call is None or call_owned_outcome is None:
            raise DurableInterruptApiError(
                "protocol_error",
                "call-owned resume requires the locked CapabilityCall and outcome",
                status_code=409,
            )
        return _build_call_owned_resume_children(
            db,
            run=run,
            interrupt=interrupt,
            expected_revision=expected_revision,
            call=call_owned_call,
            outcome=call_owned_outcome,
        )

    from app.assistant.durable.checkpoints import (  # noqa: PLC0415
        _current_transcript_digest,
        _next_checkpoint_sequence,
    )

    suspension = interrupt.budget_suspension_state or {}
    remaining = int(suspension.get("remainingActiveMs") or suspension.get("remaining_active_ms") or 0)
    parent_budget_row = db.get(AssistantRunBudgetRevision, interrupt.budget_revision_id)
    if parent_budget_row is None:
        raise DurableInterruptApiError(
            "protocol_error",
            "parent budget revision missing",
            status_code=409,
        )
    parent_ledger = _load_parent_ledger(db, interrupt.budget_revision_id)
    now = utcnow()
    # Child DB revision must be strictly greater than the parent row revision
    # (ledger payload revision may be 0 while the row is revision 1).
    child_revision = max(
        int(parent_budget_row.revision) + 1,
        int(parent_ledger.revision) + 1,
    )
    child_ledger = derive_resume_budget_ledger(
        parent=parent_ledger,
        remaining_active_ms=remaining,
        database_now=now,
        child_revision=child_revision,
    )
    budget_id = uuid4()
    budget_row = AssistantRunBudgetRevision(
        id=budget_id,
        run_id=run.id,
        revision=child_revision,
        parent_revision_id=interrupt.budget_revision_id,
        parent_digest=str(parent_ledger.ledger_digest),
        budget_digest=str(child_ledger.ledger_digest),
        payload=child_ledger.model_dump(mode="json", by_alias=True),
    )

    ordinal, transcript_digest, _msgs = _current_transcript_digest(db, run.id)
    # Carry workflow_state / active continuation from waiting checkpoint when present.
    workflow_state = None
    active_cont = None
    waiting_ck = db.get(AssistantRunCheckpoint, interrupt.checkpoint_id)
    if waiting_ck is not None and isinstance(waiting_ck.state_payload, dict):
        payload = waiting_ck.state_payload
        workflow_state = payload.get("workflowState") or payload.get("workflow_state")
        active_cont = (
            payload.get("activeCapabilityContinuation")
            or payload.get("active_capability_continuation")
        )

    manifest_id = run.current_manifest_revision_id or interrupt.manifest_revision_id
    policy_id = run.current_policy_revision_id
    if policy_id is None and waiting_ck is not None:
        policy_id = waiting_ck.policy_revision_id
    obligation_id = run.current_obligation_revision_id
    if obligation_id is None and waiting_ck is not None:
        obligation_id = waiting_ck.obligation_revision_id
    if policy_id is None or obligation_id is None or manifest_id is None:
        raise DurableInterruptApiError(
            "protocol_error",
            "run missing manifest/policy/obligation revision for resume checkpoint",
            status_code=409,
        )

    checkpoint_id = uuid4()
    resume_cp = DurableAgentCheckpointV2(
        run_id=run.id,
        phase="ready_for_provider",
        manifest_revision_id=manifest_id,
        policy_revision_id=policy_id,
        budget_revision_id=budget_id,
        obligation_revision_id=obligation_id,
        provider_message_ordinal=ordinal,
        provider_transcript_digest=transcript_digest,
        provider_loop_continuation=None,
        inflight_unit=None,
        capability_frames=(),
        artifact_ids=(),
        visible_text_artifact_id=None,
        next_action=DurableNextActionV2(kind="resume_child"),
        workflow_state=workflow_state,
        active_capability_continuation=active_cont,
        pending_interrupt_id=interrupt.id,
        budget_suspension=None,
    )
    state_payload = encode_checkpoint_v2(resume_cp)
    state_digest = checkpoint_state_digest(resume_cp)
    seq = _next_checkpoint_sequence(db, run.id)
    ck_row = AssistantRunCheckpoint(
        id=checkpoint_id,
        run_id=run.id,
        sequence=seq,
        expected_state_revision=int(expected_revision),
        committed_state_revision=int(expected_revision) + 1,
        schema_version=2,
        manifest_revision_id=manifest_id,
        policy_revision_id=policy_id,
        budget_revision_id=budget_id,
        obligation_revision_id=obligation_id,
        provider_message_ordinal=ordinal,
        provider_transcript_digest=transcript_digest,
        phase="ready_for_provider",
        logical_unit_id=None,
        reason=f"interrupt_resolved:{interrupt.id}",
        state_payload=state_payload,
        state_digest=state_digest,
    )
    deadline = child_ledger.deadline_at_utc
    return [budget_row, ck_row], budget_id, checkpoint_id, deadline


def _build_call_owned_resume_children(
    db: Session,
    *,
    run: AssistantChatRun,
    interrupt: AssistantRunInterrupt,
    expected_revision: int,
    call: Any,
    outcome: str,
) -> tuple[list[Any], UUID, UUID, datetime]:
    """Build a v3 Provider-resume child for one exact call-owned decision.

    Capability-call pauses are Provider-loop pauses, not Workflow-frame pauses.
    The waiting v3 checkpoint is therefore kept as v3 with its frozen
    ``ProviderLoopContinuation`` intact.  A v2 ``resume_child`` would be routed
    to the Workflow executor and has no workflow state to reconstruct.
    """
    # Reuse the ordinary budget derivation, but discard its workflow-only v2
    # checkpoint.  This keeps the budget lineage and database-time deadline
    # identical across workflow and capability-call resolution paths.
    generic_rows, budget_id, _discarded_checkpoint_id, deadline = _build_resume_children(
        db,
        run=run,
        interrupt=interrupt,
        expected_revision=expected_revision,
        call_owned_call=None,
        call_owned_outcome=None,
        _force_generic=True,
    )
    budget_rows = [
        row for row in generic_rows if isinstance(row, AssistantRunBudgetRevision)
    ]
    if len(budget_rows) != 1:
        raise DurableInterruptApiError(
            "protocol_error",
            "call-owned resume budget lineage is invalid",
            status_code=409,
        )
    budget_row = budget_rows[0]

    waiting_row = db.get(AssistantRunCheckpoint, interrupt.checkpoint_id)
    if waiting_row is None or not isinstance(waiting_row.state_payload, Mapping):
        raise DurableInterruptApiError(
            "protocol_error",
            "call-owned waiting checkpoint is missing",
            status_code=409,
        )
    try:
        waiting = decode_checkpoint_v3(waiting_row.state_payload)
    except Exception as exc:  # noqa: BLE001 - fail closed at the boundary
        raise DurableInterruptApiError(
            "protocol_error",
            f"call-owned waiting checkpoint is not schema v3: {exc}",
            status_code=409,
        ) from exc
    if not isinstance(waiting, DurableAgentCheckpointV3):
        raise DurableInterruptApiError(
            "protocol_error",
            "call-owned waiting checkpoint is not a v3 checkpoint",
            status_code=409,
        )
    if waiting.provider_loop_continuation is None:
        raise DurableInterruptApiError(
            "protocol_error",
            "call-owned waiting checkpoint has no Provider continuation",
            status_code=409,
        )

    target_status = {
        "approved": "authorized",
        "rejected": "rejected",
        "expired": "expired",
        "cancelled": "cancelled",
    }.get(str(outcome))
    if target_status is None:
        raise DurableInterruptApiError(
            CODE_INTERRUPT_OUTCOME_INVALID,
            "unsupported call-owned approval outcome",
            status_code=422,
        )
    states: list[DurableCapabilityCallStateV1] = []
    replaced = False
    for state in waiting.capability_calls:
        if state.call_id != call.id:
            states.append(state)
            continue
        if replaced:
            raise DurableInterruptApiError(
                "protocol_error",
                "call-owned waiting checkpoint contains the Call more than once",
                status_code=409,
            )
        states.append(
            state.model_copy(
                update={
                    "status": target_status,
                    "interrupt_id": interrupt.id,
                }
            )
        )
        replaced = True
    if not replaced:
        raise DurableInterruptApiError(
            "protocol_error",
            "call-owned waiting checkpoint does not contain the exact Call",
            status_code=409,
        )

    # The Provider loop owns this continuation.  ``dispatch_calls`` makes the
    # v3 checkpoint take the enforced Provider path, where the aggregate will
    # replay the exact frozen sibling reservation and never use a Workflow
    # material resolver.
    resume_checkpoint = waiting.model_copy(
        update={
            "budget_revision_id": budget_id,
            "next_action": DurableNextActionV2(kind="dispatch_calls"),
            "capability_calls": tuple(states),
        }
    )
    checkpoint_id = uuid4()
    state_payload = encode_checkpoint_v3(resume_checkpoint)
    state_digest = checkpoint_state_digest(resume_checkpoint)
    from app.assistant.durable.checkpoints import _next_checkpoint_sequence  # noqa: PLC0415

    ck_row = AssistantRunCheckpoint(
        id=checkpoint_id,
        run_id=run.id,
        sequence=_next_checkpoint_sequence(db, run.id),
        expected_state_revision=int(expected_revision),
        committed_state_revision=int(expected_revision) + 1,
        schema_version=3,
        manifest_revision_id=resume_checkpoint.manifest_revision_id,
        policy_revision_id=resume_checkpoint.policy_revision_id,
        budget_revision_id=budget_id,
        obligation_revision_id=resume_checkpoint.obligation_revision_id,
        provider_message_ordinal=resume_checkpoint.provider_message_ordinal,
        provider_transcript_digest=resume_checkpoint.provider_transcript_digest,
        phase=resume_checkpoint.phase,
        logical_unit_id=waiting_row.logical_unit_id,
        reason=f"call_owned_interrupt_resolved:{interrupt.id}",
        state_payload=state_payload,
        state_digest=state_digest,
    )
    return [budget_row, ck_row], budget_id, checkpoint_id, deadline


def _call_owned_binding_error(message: str) -> DurableInterruptApiError:
    return DurableInterruptApiError(
        "approval_binding_mismatch",
        message,
        status_code=409,
    )


def _call_owned_resolution_digest(
    *,
    interrupt_id: UUID,
    call_id: UUID | None,
    resolution_request_id: UUID,
    expected_request_revision: int,
    expected_run_revision: int,
    outcome: str,
    comment: str | None,
    actor: Any,
) -> str:
    """Bind call-owned idempotency to the authenticated operator session."""
    from app.assistant.domain.digests import sha256_canonical_json

    return sha256_canonical_json(
        {
            "actorOperatorId": str(actor.operator_id),
            "actorSessionId": str(actor.session_id),
            "callId": str(call_id) if call_id is not None else None,
            "comment": comment,
            "expectedRequestRevision": int(expected_request_revision),
            "expectedRunRevision": int(expected_run_revision),
            "interruptId": str(interrupt_id),
            "outcome": str(outcome),
            "resolutionRequestId": str(resolution_request_id),
        }
    )


def _load_and_verify_call_owned_binding(
    db: Session,
    *,
    run: AssistantChatRun,
    interrupt: AssistantRunInterrupt,
    expected_call_id: UUID | None = None,
    allow_resolved: bool = False,
) -> tuple[Any, Any]:
    """Load the exact frozen call/Interrupt binding under the Run lock.

    Every input used to rebuild the digest comes from persisted rows. The
    request body is deliberately absent from this function.
    """
    from app.assistant.capability_calls.approval import build_approval_binding
    from app.assistant.capability_calls.repository import CapabilityCallRepository

    if str(interrupt.interrupt_origin) != "capability_call":
        raise _call_owned_binding_error("interrupt is not call-owned")
    if interrupt.capability_call_id is None:
        raise _call_owned_binding_error("call-owned interrupt has no capability call")
    if interrupt.run_id != run.id:
        raise _call_owned_binding_error("interrupt run ownership does not match")
    if str(interrupt.status) != "pending" and not (
        allow_resolved and str(interrupt.status) == "expired"
    ):
        raise DurableInterruptApiError(
            CODE_INTERRUPT_ALREADY_RESOLVED,
            f"interrupt already terminal with status {interrupt.status}",
            status_code=409,
        )

    call_repo = CapabilityCallRepository(db)
    call = call_repo.get_call(interrupt.capability_call_id, for_update=True)
    if call is None:
        raise _call_owned_binding_error("capability call is missing")
    if expected_call_id is not None and call.id != expected_call_id:
        raise _call_owned_binding_error("target capability call does not own the Interrupt")
    if call.id != interrupt.capability_call_id or call.run_id != run.id:
        raise _call_owned_binding_error("capability call ownership does not match")
    if call.interrupt_id != interrupt.id:
        raise _call_owned_binding_error("call/interrupt linkage does not match")
    if call.manifest_revision_id != interrupt.manifest_revision_id:
        raise _call_owned_binding_error("call/interrupt manifest revision drifted")
    if str(call.status) != "awaiting_approval":
        raise _call_owned_binding_error("capability call is not awaiting approval")

    payload = interrupt.request_payload
    if not isinstance(payload, Mapping):
        raise _call_owned_binding_error("approval binding payload is missing")

    def _required_text(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise _call_owned_binding_error(f"approval binding field {name} is missing")
        return value

    def _optional_uuid(name: str) -> UUID | None:
        value = payload.get(name)
        if value in (None, ""):
            return None
        try:
            return UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise _call_owned_binding_error(
                f"approval binding field {name} is invalid"
            ) from exc

    payload_call_id = _required_text("callId")
    if payload_call_id != str(call.id):
        raise _call_owned_binding_error("call ID drift invalidates approval")
    if payload.get("runId") not in (None, str(run.id)):
        raise _call_owned_binding_error("run ID drift invalidates approval")

    logical_call_key = _required_text("logicalCallKey")
    owner_digest = _required_text("ownerDigest")
    binding_contract_digest = _required_text("bindingContractDigest")
    input_digest = _required_text("inputDigest")
    target_digest = _required_text("targetDigest")
    descriptor_digest = _required_text("descriptorDigest")
    authorization_digest = _required_text("authorizationDigest")
    principal_digest = _required_text("principalDigest")
    approval_binding_digest = _required_text("approvalBindingDigest")
    target_version_id = _optional_uuid("targetVersionId")

    if logical_call_key != str(call.logical_call_key):
        raise _call_owned_binding_error("logical_call_key drift invalidates approval")
    if input_digest != str(call.input_digest):
        raise _call_owned_binding_error("input_digest drift invalidates approval")
    if descriptor_digest != str(call.descriptor_digest):
        raise _call_owned_binding_error("descriptor_digest drift invalidates approval")
    if authorization_digest != str(call.authorization_digest):
        raise _call_owned_binding_error(
            "authorization_digest drift invalidates approval"
        )
    if target_version_id != call.target_version_id:
        raise _call_owned_binding_error("target_version_id drift invalidates approval")
    try:
        request_revision = int(payload.get("requestRevision"))
    except (TypeError, ValueError) as exc:
        raise _call_owned_binding_error(
            "approval binding request revision is invalid"
        ) from exc
    if request_revision != int(interrupt.request_revision):
        raise _call_owned_binding_error("request revision drift invalidates approval")

    try:
        binding = build_approval_binding(
            call_id=call.id,
            logical_call_key=logical_call_key,
            owner_digest=owner_digest,
            binding_contract_digest=binding_contract_digest,
            input_digest=input_digest,
            target_version_id=target_version_id,
            target_digest=target_digest,
            descriptor_digest=descriptor_digest,
            authorization_digest=authorization_digest,
            principal_digest=principal_digest,
            request_revision=request_revision,
        )
    except (TypeError, ValueError) as exc:
        raise _call_owned_binding_error("approval binding fields are invalid") from exc
    if binding.approval_binding_digest != approval_binding_digest:
        raise _call_owned_binding_error("approval binding digest is not canonical")
    if str(call.approval_binding_digest or "") != approval_binding_digest:
        raise _call_owned_binding_error(
            "stored call approval binding does not match the Interrupt"
        )

    # Where the full frozen snapshots are present, cross-check the target and
    # principal evidence as an additional independent persisted boundary. Older
    # synthetic rows may omit these shapes; their canonical payload digest still
    # provides the immutable binding check above.
    manifest = db.get(AssistantRunManifestRevision, call.manifest_revision_id)
    manifest_payload = getattr(manifest, "payload", None)
    if not isinstance(manifest_payload, Mapping):
        raise _call_owned_binding_error("persisted manifest evidence is missing")
    capabilities = manifest_payload.get("capabilities")
    if not isinstance(capabilities, list):
        raise _call_owned_binding_error("persisted manifest capability evidence is missing")
    matching = [
        item
        for item in capabilities
        if isinstance(item, Mapping)
        and str(item.get("capabilityKey") or item.get("capability_key") or "")
        == str(call.domain_key)
    ]
    if len(matching) != 1:
        raise _call_owned_binding_error("persisted manifest target is missing or ambiguous")
    item = matching[0]
    if str(
        item.get("bindingContractDigest") or item.get("binding_contract_digest") or ""
    ) != binding_contract_digest:
        raise _call_owned_binding_error("manifest binding contract drifted")
    target_version_value = item.get("targetVersionId") or item.get("target_version_id")
    if target_version_value not in (
        None,
        str(target_version_id) if target_version_id else None,
    ):
        raise _call_owned_binding_error("manifest target version drifted")
    if str(item.get("resolutionDigest") or item.get("resolution_digest") or "") != target_digest:
        raise _call_owned_binding_error("manifest target digest drifted")

    checkpoint = db.get(AssistantRunCheckpoint, interrupt.checkpoint_id)
    policy = (
        db.get(AssistantRunPolicyRevision, checkpoint.policy_revision_id)
        if checkpoint is not None and checkpoint.policy_revision_id is not None
        else None
    )
    policy_payload = getattr(policy, "payload", None)
    if not isinstance(policy_payload, Mapping):
        raise _call_owned_binding_error("persisted policy evidence is missing")
    principal_payload = policy_payload.get("principal")
    if not isinstance(principal_payload, Mapping):
        raise _call_owned_binding_error("persisted principal evidence is missing")
    try:
        from app.assistant.capabilities.contracts import CapabilityPrincipal
        from app.assistant.policy.contracts import compute_principal_digest

        frozen_principal = CapabilityPrincipal.model_validate(principal_payload)
        if compute_principal_digest(frozen_principal) != principal_digest:
            raise _call_owned_binding_error("persisted principal digest drifted")
    except DurableInterruptApiError:
        raise
    except (TypeError, ValueError) as exc:
        raise _call_owned_binding_error("persisted principal evidence is invalid") from exc
    owner_refs = policy_payload.get("ownerPolicyRefs") or policy_payload.get(
        "owner_policy_refs"
    )
    if not isinstance(owner_refs, list):
        raise _call_owned_binding_error("persisted owner policy evidence is missing")
    matching_owner = [
        owner_item
        for owner_item in owner_refs
        if isinstance(owner_item, Mapping)
        and str(owner_item.get("ownerKind") or owner_item.get("owner_kind") or "")
        == str(call.owner_kind)
        and (
            call.owner_version_id is None
            or str(owner_item.get("ownerVersionId") or owner_item.get("owner_version_id") or "")
            == str(call.owner_version_id)
        )
    ]
    if len(matching_owner) != 1:
        raise _call_owned_binding_error("persisted owner policy is missing or ambiguous")
    owner_policy = matching_owner[0].get("policyDigest") or matching_owner[0].get(
        "policy_digest"
    )
    if str(owner_policy or "") != owner_digest:
        raise _call_owned_binding_error("persisted owner digest drifted")

    return call, binding


def decide_call_owned(
    db: Session,
    *,
    conversation_id: UUID,
    run_id: UUID,
    interrupt_id: UUID,
    call_id: UUID | None = None,
    resolution_request_id: UUID,
    expected_request_revision: int,
    expected_run_revision: int,
    outcome: str,
    comment: str | None = None,
    actor: Any,
) -> dict[str, Any]:
    """Resolve a capability-call approval with authenticated operator authority."""
    from app.operator_auth.contracts import OperatorPrincipal

    if not isinstance(actor, OperatorPrincipal) or actor.role != "operator":
        raise DurableInterruptApiError(
            "operator_role_required", "operator role is required", status_code=403
        )
    if outcome not in {"approved", "rejected", "expired", "cancelled"}:
        raise DurableInterruptApiError(
            CODE_INTERRUPT_OUTCOME_INVALID,
            "unsupported call-owned approval outcome",
            status_code=422,
        )

    run = _authorize_run(db, conversation_id=conversation_id, run_id=run_id)
    repo = DurableInterruptRepository(db, **_settings_repo_kwargs())
    try:
        locked_run = repo._lock_run(run_id)  # noqa: SLF001 - shared CAS boundary
        existing = repo.get_by_resolution_request_id(
            run_id=run_id,
            resolution_request_id=resolution_request_id,
            for_update=True,
        )
        expected_digest = _call_owned_resolution_digest(
            interrupt_id=interrupt_id,
            call_id=call_id,
            resolution_request_id=resolution_request_id,
            expected_request_revision=expected_request_revision,
            expected_run_revision=expected_run_revision,
            outcome=outcome,
            comment=comment,
            actor=actor,
        )
        if existing is not None:
            if existing.id != interrupt_id or existing.resolution_digest != expected_digest:
                raise InterruptConflict(
                    CODE_INTERRUPT_IDEMPOTENCY_CONFLICT,
                    "resolution_request_id reused with different decision or actor",
                    run=locked_run,
                )
            db.commit()
            db.refresh(existing)
            return serialize_interrupt_safe(existing, run=locked_run)

        if int(locked_run.state_revision) != int(expected_run_revision):
            raise InterruptConflict(
                CODE_RUN_STALE_REVISION,
                "run state_revision mismatch",
                run=locked_run,
            )

        interrupt = repo._lock_interrupt(interrupt_id)  # noqa: SLF001
        if interrupt.run_id != run_id:
            raise InterruptConflict(
                CODE_INTERRUPT_NOT_FOUND, "interrupt does not belong to run", run=locked_run
            )
        if int(interrupt.request_revision) != int(expected_request_revision):
            raise InterruptConflict(
                CODE_INTERRUPT_STALE_REVISION, "request_revision mismatch", run=locked_run
            )
        if int(interrupt.request_run_revision) != int(expected_run_revision):
            raise InterruptConflict(
                CODE_INTERRUPT_STALE_REVISION, "request_run_revision mismatch", run=locked_run
            )
        call, binding = _load_and_verify_call_owned_binding(
            db,
            run=locked_run,
            interrupt=interrupt,
            expected_call_id=call_id,
        )
        now = repo._db_now()  # noqa: SLF001
        if outcome == "expired" and _as_utc(interrupt.expires_at) > now:
            raise InterruptConflict(
                CODE_INTERRUPT_NOT_EXPIRED,
                "interrupt has not reached expires_at",
                run=locked_run,
            )
        if outcome != "expired" and _as_utc(interrupt.expires_at) <= now:
            raise InterruptConflict(
                CODE_INTERRUPT_EXPIRED,
                "interrupt has expired",
                run=locked_run,
            )
        clean_comment = comment
        if clean_comment is not None and len(clean_comment) > int(
            get_settings().assistant_interrupt_comment_max_chars
        ):
            raise InterruptSchemaError(CODE_INTERRUPT_COMMENT_TOO_LONG)
        if str(locked_run.status) not in WAITING_STATUSES:
            raise InterruptConflict(
                CODE_INTERRUPT_NOT_PENDING,
                f"run is not waiting (status={locked_run.status})",
                run=locked_run,
            )

        child_rows, budget_id, checkpoint_id, deadline = _build_resume_children(
            db,
            run=locked_run,
            interrupt=interrupt,
            expected_revision=int(locked_run.state_revision),
            call_owned_call=call,
            call_owned_outcome=outcome,
        )
        for row in child_rows:
            db.add(row)
        db.flush()

        status = {
            "approved": "approved",
            "rejected": "rejected",
            "expired": "expired",
            "cancelled": "cancelled",
        }[outcome]
        interrupt.status = status
        interrupt.decision = outcome
        interrupt.comment = clean_comment
        interrupt.resolution_request_id = resolution_request_id
        interrupt.resolution_digest = expected_digest
        interrupt.resolution_checkpoint_id = checkpoint_id
        interrupt.resolution_budget_revision_id = budget_id
        interrupt.resolution_run_revision = int(locked_run.state_revision) + 1
        interrupt.resume_token_digest = None
        interrupt.resolved_at = now
        interrupt.updated_at = now
        db.flush()

        from app.assistant.capability_calls.repository import (
            CapabilityCallConflict,
            CapabilityCallRepository,
        )

        call_repo = CapabilityCallRepository(db)
        target = {
            "approved": "authorized",
            "rejected": "rejected",
            "expired": "expired",
            "cancelled": "cancelled",
        }[outcome]
        try:
            call = call_repo.transition_call(
                call_id=call.id,
                expected_call_revision=int(call.state_revision),
                expected_run_revision=int(locked_run.state_revision),
                to_status=target,
                lease=None,
                approval_binding_digest=(
                    binding.approval_binding_digest if target == "authorized" else None
                ),
                failure_code=None if target == "authorized" else f"approval_{target}",
            )
        except CapabilityCallConflict as exc:
            raise DurableInterruptApiError(
                getattr(exc, "code", "approval_binding_mismatch"),
                str(exc),
                status_code=409,
            ) from exc

        run_repo = DurableRunRepository(db)
        events = (
            EventSpec(
                event_key=f"human_interrupt_resolved:{interrupt_id}:{resolution_request_id}",
                event_name="human_interrupt_resolved",
                payload={
                    "interruptId": str(interrupt_id),
                    "status": status,
                    "kind": str(interrupt.kind),
                    "resolutionRequestId": str(resolution_request_id),
                    "requestRevision": int(interrupt.request_revision),
                    "runRevision": int(locked_run.state_revision),
                },
                visibility="public",
            ),
            EventSpec(
                event_key=f"run_status:queued:{interrupt_id}:{resolution_request_id}",
                event_name="run_status",
                payload={
                    "status": "queued",
                    "interruptId": str(interrupt_id),
                    "fromStatus": str(locked_run.status),
                },
                visibility="public",
            ),
        )
        commit = run_repo.commit_resume_queued(
            run_id=run_id,
            expected_revision=int(locked_run.state_revision),
            events=events,
            children=DurableChildBundle(
                rows=[],
                current_checkpoint_id=checkpoint_id,
                current_budget_revision_id=budget_id,
            ),
            set_deadline_at=deadline,
        )
        db.refresh(interrupt)
        return serialize_interrupt_safe(interrupt, run=commit.run)
    except DurableInterruptApiError:
        db.rollback()
        raise
    except (InterruptConflict, DurableRunConflict, InterruptSchemaError) as exc:
        db.rollback()
        raise _conflict_to_api(exc) from exc
    except Exception:
        db.rollback()
        raise


def resolve_interrupt_http(
    db: Session,
    *,
    conversation_id: UUID,
    run_id: UUID,
    interrupt_id: UUID,
    token: str,
    resolution_request_id: UUID,
    expected_token_revision: int,
    expected_request_revision: int,
    expected_run_revision: int,
    outcome: str,
    values: Mapping[str, Any] | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """One-transaction HTTP resolve: queue-only, no Workflow/Provider/Gateway.

    Spec §11.2 order: authorize → lock Run (inside resolve_interrupt) → idempotency
    → (unknown only) validate pending/token/revisions/deadline → validate decision →
    derive child budget + resume Checkpoint under lock → CAS → commit.
    Children are never built/flushed before first-resolution validation succeeds.
    """
    run = _authorize_run(db, conversation_id=conversation_id, run_id=run_id)
    interrupt_row = _get_interrupt_for_run(
        db, run_id=run_id, interrupt_id=interrupt_id
    )

    if str(interrupt_row.interrupt_origin) == "capability_call":
        raise DurableInterruptApiError(
            CODE_CALL_OWNED_APPROVAL_REQUIRED,
            "call-owned approvals require the operator decision boundary",
            status_code=409,
        )

    pepper = _require_pepper_for_token_ops()

    outcome = str(outcome)
    call_owned = False
    # A call-owned terminal decision is a Tool result, not a terminal Run
    # cancellation. Queue the Run for Provider resume for approved and denied
    # outcomes alike; only workflow-node cancellation terminates the Run.
    queues = outcome in _QUEUEING_OUTCOMES or call_owned

    # Soft pre-check: terminal run allows exact idempotent replay only.
    run_status = str(run.status)
    if run_status in {STATUS_CANCELLED, STATUS_CANCELLING, "failed", "completed"}:
        existing = (
            db.execute(
                select(AssistantRunInterrupt).where(
                    AssistantRunInterrupt.run_id == run_id,
                    AssistantRunInterrupt.resolution_request_id == resolution_request_id,
                )
            ).scalar_one_or_none()
        )
        if existing is None or existing.id != interrupt_id:
            raise DurableInterruptApiError(
                CODE_INTERRUPT_RUN_CANCELLED,
                f"run status is {run_status}",
                status_code=409,
            )

    repo = DurableInterruptRepository(
        db,
        token_pepper=pepper,
        comment_max_chars=int(get_settings().assistant_interrupt_comment_max_chars),
        default_ttl_sec=int(get_settings().assistant_interrupt_default_ttl_sec),
        max_ttl_sec=int(get_settings().assistant_interrupt_max_ttl_sec),
    )

    # Captures filled only after unknown-ID validation under Run lock.
    prepared: dict[str, Any] = {
        "budget_id": None,
        "checkpoint_id": None,
        "deadline": None,
        "from_status": run_status,
        "expected_revision": int(run.state_revision),
    }

    def _require_waiting(locked_run: AssistantChatRun) -> str:
        locked_status = str(locked_run.status)
        if locked_status in WAITING_STATUSES:
            return locked_status
        if locked_status in {STATUS_CANCELLED, STATUS_CANCELLING, "failed", "completed"}:
            raise DurableInterruptApiError(
                CODE_INTERRUPT_RUN_CANCELLED,
                f"run status is {locked_status}",
                status_code=409,
            )
        raise DurableInterruptApiError(
            CODE_INTERRUPT_NOT_PENDING,
            f"run is not waiting (status={locked_status})",
            status_code=409,
        )

    def _prepare_queued(locked_run: AssistantChatRun, locked_interrupt: AssistantRunInterrupt):
        locked_status = _require_waiting(locked_run)
        expected_revision = int(locked_run.state_revision)
        child_rows, budget_id, checkpoint_id, deadline = _build_resume_children(
            db,
            run=locked_run,
            interrupt=locked_interrupt,
            expected_revision=expected_revision,
        )
        for row in child_rows:
            db.add(row)
        db.flush()
        prepared["budget_id"] = budget_id
        prepared["checkpoint_id"] = checkpoint_id
        prepared["deadline"] = deadline
        prepared["from_status"] = locked_status
        prepared["expected_revision"] = expected_revision
        return checkpoint_id, budget_id, expected_revision + 1

    def _finish_after_resolve(result) -> dict[str, Any]:
        """Continue queue/cancel CAS after a first-resolution interrupt mutation.

        Used by both the primary path and IntegrityError re-entry when
        ``created_resolution`` is true. Short-circuit only happens on
        ``idempotent_replay`` before this helper is called.
        """
        nonlocal run
        if str(result.interrupt.interrupt_origin) == "capability_call":
            raise DurableInterruptApiError(
                CODE_CALL_OWNED_APPROVAL_REQUIRED,
                "call-owned approvals require the operator decision boundary",
                status_code=409,
            )
        run_repo = DurableRunRepository(db)
        if queues:
            from_status = str(prepared["from_status"])
            expected_revision = int(prepared["expected_revision"])
            budget_id = prepared["budget_id"]
            checkpoint_id = prepared["checkpoint_id"]
            deadline = prepared["deadline"]
            events = (
                EventSpec(
                    event_key=f"human_interrupt_resolved:{interrupt_id}:{resolution_request_id}",
                    event_name="human_interrupt_resolved",
                    payload={
                        "interruptId": str(interrupt_id),
                        "status": result.interrupt.status,
                        "kind": str(result.interrupt.kind),
                        "resolutionRequestId": str(resolution_request_id),
                        "requestRevision": int(result.interrupt.request_revision),
                        "runRevision": expected_revision,
                    },
                    visibility="public",
                ),
                EventSpec(
                    event_key=f"run_status:queued:{interrupt_id}:{resolution_request_id}",
                    event_name="run_status",
                    payload={
                        "status": "queued",
                        "interruptId": str(interrupt_id),
                        "fromStatus": from_status,
                    },
                    visibility="public",
                ),
            )
            # Children already flushed under lock during prepare; pass empty rows so
            # CAS only advances pointers/deadline/events/status (no second insert).
            # checkpoint_seq is folded into _append_children pointer advance.
            bundle = DurableChildBundle(
                rows=[],
                current_checkpoint_id=checkpoint_id,
                current_budget_revision_id=budget_id,
            )
            commit = run_repo.commit_resume_queued(
                run_id=run_id,
                expected_revision=expected_revision,
                events=events,
                children=bundle,
                set_deadline_at=deadline,
            )
            run = commit.run
        else:
            # Terminal non-queue outcome via HTTP (cancelled): cancel waiting run.
            current = db.get(AssistantChatRun, run_id)
            if current is None:
                raise DurableInterruptApiError(
                    CODE_DURABLE_INTERRUPT_NOT_FOUND,
                    f"run not found: {run_id}",
                    status_code=404,
                )
            _require_waiting(current)
            expected_revision = int(current.state_revision)
            events = (
                EventSpec(
                    event_key=f"human_interrupt_resolved:{interrupt_id}:{resolution_request_id}",
                    event_name="human_interrupt_resolved",
                    payload={
                        "interruptId": str(interrupt_id),
                        "status": result.interrupt.status,
                        "kind": str(result.interrupt.kind),
                        "resolutionRequestId": str(resolution_request_id),
                    },
                    visibility="public",
                ),
                EventSpec(
                    event_key=f"run_status:cancelled:{interrupt_id}:{resolution_request_id}",
                    event_name="run_status",
                    payload={"status": "cancelled", "interruptId": str(interrupt_id)},
                    visibility="public",
                ),
            )
            commit = run_repo.commit_waiting_terminal_cancel(
                run_id=run_id,
                expected_revision=expected_revision,
                events=events,
                failure_code=None,
                error_message=None,
            )
            run = commit.run

        db.refresh(result.interrupt)
        return serialize_interrupt_safe(result.interrupt, run=run)

    try:
        result = repo.resolve_interrupt(
            run_id=run_id,
            interrupt_id=interrupt_id,
            resolution_request_id=resolution_request_id,
            token=token,
            expected_token_revision=expected_token_revision,
            expected_request_revision=expected_request_revision,
            expected_run_revision=expected_run_revision,
            outcome=outcome,
            submitted_values=values,
            comment=comment,
            token_pepper=pepper,
            queues_execution=queues,
            prepare_queued_children=_prepare_queued if queues else None,
        )

        if result.idempotent_replay:
            db.commit()
            db.refresh(run)
            return serialize_interrupt_safe(result.interrupt, run=run)

        return _finish_after_resolve(result)

    except DurableInterruptApiError:
        db.rollback()
        raise
    except (InterruptConflict, DurableRunConflict, InterruptTokenError, InterruptSchemaError) as exc:
        db.rollback()
        raise _conflict_to_api(exc) from exc
    except IntegrityError as exc:
        # Concurrent multi-tab first paths / unique races: re-enter resolve under lock.
        # Pass prepare_queued_children so first-resolution re-entry still prepares
        # children under the Run lock; only short-circuit on exact idempotent_replay.
        try:
            db.rollback()
        except Exception:
            pass
        try:
            replay = repo.resolve_interrupt(
                run_id=run_id,
                interrupt_id=interrupt_id,
                resolution_request_id=resolution_request_id,
                token=token,
                expected_token_revision=expected_token_revision,
                expected_request_revision=expected_request_revision,
                expected_run_revision=expected_run_revision,
                outcome=outcome,
                submitted_values=values,
                comment=comment,
                token_pepper=pepper,
                queues_execution=queues,
                prepare_queued_children=_prepare_queued if queues else None,
            )
            if replay.idempotent_replay:
                db.commit()
                db.refresh(run)
                return serialize_interrupt_safe(replay.interrupt, run=run)
            # Re-entry took the first-resolution path (e.g. IntegrityError from
            # event/pointer CAS after interrupt mutation rolled back). Continue
            # the same queue/cancel CAS finish path instead of rolling back a win.
            return _finish_after_resolve(replay)
        except DurableInterruptApiError:
            raise
        except (InterruptConflict, DurableRunConflict, InterruptTokenError, InterruptSchemaError) as race_exc:
            db.rollback()
            raise _conflict_to_api(race_exc) from exc
        except Exception as race_exc:
            db.rollback()
            raise DurableInterruptApiError(
                CODE_INTERRUPT_IDEMPOTENCY_CONFLICT,
                f"concurrent resolution race: {race_exc}",
                status_code=409,
            ) from exc
    except ApiException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# Expiry scanner
# ---------------------------------------------------------------------------


def _has_explicit_expired_edge(plan: Any, node_id: str) -> bool:
    """Return true only for a frozen typed edge, never a default linear edge."""
    for node in getattr(plan, "nodes", ()):
        if str(getattr(node, "node_id", "")) != str(node_id):
            continue
        return any(
            str(getattr(edge, "source_handle", "") or "") == "expired"
            for edge in getattr(node, "outgoing_edges", ())
        )
    return False


def _interrupt_has_typed_expiry(
    db: Session,
    *,
    interrupt: AssistantRunInterrupt,
) -> bool:
    waiting_row = db.get(AssistantRunCheckpoint, interrupt.checkpoint_id)
    if waiting_row is None:
        # Legacy HITL rows may point at a checkpoint that is not present in
        # the durable workflow table; preserve their terminal expiry path.
        return False
    try:
        payload = waiting_row.state_payload
        if isinstance(payload, Mapping) and not (
            payload.get("runId") or payload.get("run_id") or payload.get("workflowState")
        ):
            # Older synthetic/legacy HITL checkpoints are not durable workflow
            # material and retain the existing terminal expiry behavior.
            return False
        checkpoint = decode_checkpoint(waiting_row.state_payload)
        workflow_state = getattr(checkpoint, "workflow_state", None)
        if workflow_state is None:
            # Legacy/non-workflow HITL rows have no durable workflow state and
            # retain the existing terminal-expiry cancellation behavior.
            return False
        from app.assistant.workflow.durable.material import (
            DurableRuntimeMaterialResolver,
        )

        root, materials = DurableRuntimeMaterialResolver(db).resolve(
            workflow_state=workflow_state
        )
        frame = next(
            (
                item
                for item in workflow_state.frame_stack
                if item.frame_id == interrupt.workflow_frame_id
            ),
            None,
        )
        if frame is None:
            raise DurableInterruptApiError(
                "durable_expiry_material",
                "durable expiry material frame is missing",
                status_code=409,
            )
        material = materials.get(str(frame.target_version_id), root)
        return _has_explicit_expired_edge(material.plan, str(interrupt.node_id))
    except DurableInterruptApiError:
        raise
    except Exception as exc:
        raise DurableInterruptApiError(
            "durable_expiry_material",
            f"durable expiry material cannot be reconstructed: {exc}",
            status_code=409,
        ) from exc


def list_expired_pending_interrupt_ids(
    db: Session,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> list[tuple[UUID, UUID]]:
    """Bounded candidate list without locking Interrupt rows (Plan 07 §11.4)."""
    clock = _as_utc(now or utcnow())
    stmt = (
        select(AssistantRunInterrupt.id, AssistantRunInterrupt.run_id)
        .where(
            AssistantRunInterrupt.status == "pending",
            AssistantRunInterrupt.expires_at <= clock,
        )
        .order_by(AssistantRunInterrupt.expires_at.asc())
        .limit(int(limit))
    )
    rows = db.execute(stmt).all()
    return [(row[0], row[1]) for row in rows]


def expire_one_interrupt(
    db: Session,
    *,
    run_id: UUID,
    interrupt_id: UUID,
) -> bool:
    """Lock Run then Interrupt; expire if still pending/expired. Returns True if newly expired."""
    repo = DurableInterruptRepository(db, **_settings_repo_kwargs())
    run_repo = DurableRunRepository(db)
    try:
        # Re-check under locks via expire_interrupt.
        result = repo.expire_interrupt(run_id=run_id, interrupt_id=interrupt_id)
        if not result.created_resolution:
            db.commit()
            return False

        run = db.get(AssistantChatRun, run_id)
        if run is None:
            db.commit()
            return True

        if str(result.interrupt.interrupt_origin) == "capability_call":
            # Expiry is a call-owned decision as well: validate the frozen
            # binding before closing the call, then resume the waiting Run so
            # the Provider observes the terminal call result.
            call, binding = _load_and_verify_call_owned_binding(
                db,
                run=run,
                interrupt=result.interrupt,
                allow_resolved=True,
            )
            from app.assistant.capability_calls.repository import CapabilityCallRepository

            # Expiry is terminal for the Call even if a stale/crashed Run is
            # no longer in a waiting status.  Never leave a call-owned
            # Interrupt expired while its Call remains awaiting approval.
            expected_revision = int(run.state_revision)
            CapabilityCallRepository(db).transition_call(
                call_id=call.id,
                expected_call_revision=int(call.state_revision),
                expected_run_revision=expected_revision,
                to_status="expired",
                lease=None,
                failure_code="approval_expired",
                allow_while_cancelling=True,
            )
            if str(run.status) not in WAITING_STATUSES:
                db.commit()
                return True
            child_rows, budget_id, checkpoint_id, deadline = _build_resume_children(
                db,
                run=run,
                interrupt=result.interrupt,
                expected_revision=expected_revision,
                call_owned_call=call,
                call_owned_outcome="expired",
            )
            for row in child_rows:
                db.add(row)
            db.flush()
            result.interrupt.resolution_checkpoint_id = checkpoint_id
            result.interrupt.resolution_budget_revision_id = budget_id
            result.interrupt.resolution_run_revision = expected_revision + 1
            db.flush()
            events = (
                EventSpec(
                    event_key=(
                        f"human_interrupt_expired:{interrupt_id}:"
                        f"{result.interrupt.resolution_request_id}"
                    ),
                    event_name="human_interrupt_expired",
                    payload={
                        "interruptId": str(interrupt_id),
                        "status": "expired",
                        "resolutionRequestId": str(result.interrupt.resolution_request_id),
                    },
                    visibility="public",
                ),
                EventSpec(
                    event_key=f"run_status:queued:expired:{interrupt_id}",
                    event_name="run_status",
                    payload={
                        "status": "queued",
                        "interruptId": str(interrupt_id),
                        "reason": "capability_call_expired",
                    },
                    visibility="public",
                ),
            )
            run_repo.commit_resume_queued(
                run_id=run_id,
                expected_revision=expected_revision,
                events=events,
                children=DurableChildBundle(
                    rows=[],
                    current_checkpoint_id=checkpoint_id,
                    current_budget_revision_id=budget_id,
                ),
                set_deadline_at=deadline,
            )
            return True

        if str(run.status) in WAITING_STATUSES and _interrupt_has_typed_expiry(
            db,
            interrupt=result.interrupt,
        ):
            expected_revision = int(run.state_revision)
            child_rows, budget_id, checkpoint_id, deadline = _build_resume_children(
                db,
                run=run,
                interrupt=result.interrupt,
                expected_revision=expected_revision,
            )
            for row in child_rows:
                db.add(row)
            db.flush()
            result.interrupt.resolution_checkpoint_id = checkpoint_id
            result.interrupt.resolution_budget_revision_id = budget_id
            result.interrupt.resolution_run_revision = expected_revision + 1
            db.flush()
            events = (
                EventSpec(
                    event_key=(
                        f"human_interrupt_expired:{interrupt_id}:"
                        f"{result.interrupt.resolution_request_id}"
                    ),
                    event_name="human_interrupt_expired",
                    payload={
                        "interruptId": str(interrupt_id),
                        "status": "expired",
                        "resolutionRequestId": str(
                            result.interrupt.resolution_request_id
                        ),
                    },
                    visibility="public",
                ),
                EventSpec(
                    event_key=f"run_status:queued:expired:{interrupt_id}",
                    event_name="run_status",
                    payload={
                        "status": "queued",
                        "interruptId": str(interrupt_id),
                        "reason": "typed_interrupt_expiry",
                    },
                    visibility="public",
                ),
            )
            run_repo.commit_resume_queued(
                run_id=run_id,
                expected_revision=expected_revision,
                events=events,
                children=DurableChildBundle(
                    rows=[],
                    current_checkpoint_id=checkpoint_id,
                    current_budget_revision_id=budget_id,
                ),
                set_deadline_at=deadline,
            )
        elif str(run.status) in WAITING_STATUSES:
            events = (
                EventSpec(
                    event_key=f"human_interrupt_expired:{interrupt_id}:{result.interrupt.resolution_request_id}",
                    event_name="human_interrupt_expired",
                    payload={
                        "interruptId": str(interrupt_id),
                        "status": "expired",
                        "resolutionRequestId": str(result.interrupt.resolution_request_id),
                    },
                    visibility="public",
                ),
                EventSpec(
                    event_key=f"run_status:cancelled:expired:{interrupt_id}",
                    event_name="run_status",
                    payload={
                        "status": "cancelled",
                        "interruptId": str(interrupt_id),
                        "reason": "interrupt_expired",
                    },
                    visibility="public",
                ),
            )
            run_repo.commit_waiting_terminal_cancel(
                run_id=run_id,
                expected_revision=int(run.state_revision),
                events=events,
                failure_code="interrupt_expired",
                error_message="durable interrupt expired",
            )
        else:
            db.commit()
        return True
    except (InterruptConflict, DurableRunConflict) as exc:
        db.rollback()
        # Competing decision/cancel/expiry: no-op.
        if getattr(exc, "code", None) in {
            CODE_INTERRUPT_ALREADY_RESOLVED,
            CODE_INTERRUPT_NOT_PENDING,
            CODE_INTERRUPT_NOT_FOUND,
            CODE_RUN_STALE_REVISION,
            "stale_revision",
            CODE_TERMINAL_IMMUTABLE,
        }:
            return False
        raise
    except Exception:
        db.rollback()
        raise


def scan_expired_interrupts(
    db: Session,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> ExpiryScanResult:
    candidates = list_expired_pending_interrupt_ids(db, limit=limit, now=now)
    expired = 0
    cancelled = 0
    queued = 0
    skipped = 0
    conflicts = 0
    for interrupt_id, run_id in candidates:
        try:
            # Snapshot run status before.
            run_before = db.get(AssistantChatRun, run_id)
            before_status = str(run_before.status) if run_before is not None else None
            ok = expire_one_interrupt(db, run_id=run_id, interrupt_id=interrupt_id)
            if ok:
                expired += 1
                run_after = db.get(AssistantChatRun, run_id)
                if (
                    run_after is not None
                    and before_status in WAITING_STATUSES
                    and str(run_after.status) == STATUS_CANCELLED
                ):
                    cancelled += 1
                elif run_after is not None and str(run_after.status) == "queued":
                    queued += 1
            else:
                skipped += 1
        except (InterruptConflict, DurableRunConflict):
            conflicts += 1
            try:
                db.rollback()
            except Exception:
                pass
        except Exception:
            conflicts += 1
            try:
                db.rollback()
            except Exception:
                pass
    return ExpiryScanResult(
        expired_count=expired,
        cancelled_run_count=cancelled,
        skipped_count=skipped,
        conflict_count=conflicts,
        queued_run_count=queued,
    )


# ---------------------------------------------------------------------------
# Service facade used by AssistantService / router
# ---------------------------------------------------------------------------


def service_list_pending(db: Session, conversation_id: UUID, run_id: UUID) -> list[dict[str, Any]]:
    try:
        return list_pending_interrupts(db, conversation_id=conversation_id, run_id=run_id)
    except DurableInterruptApiError as exc:
        _raise_api(exc)
        raise  # pragma: no cover


def service_get_detail(
    db: Session,
    conversation_id: UUID,
    run_id: UUID,
    interrupt_id: UUID,
) -> dict[str, Any]:
    try:
        return get_interrupt_detail(
            db,
            conversation_id=conversation_id,
            run_id=run_id,
            interrupt_id=interrupt_id,
        )
    except DurableInterruptApiError as exc:
        _raise_api(exc)
        raise  # pragma: no cover


def service_rotate_token(
    db: Session,
    *,
    conversation_id: UUID,
    run_id: UUID,
    interrupt_id: UUID,
    expected_request_revision: int,
    expected_run_revision: int,
) -> dict[str, Any]:
    try:
        return rotate_interrupt_token(
            db,
            conversation_id=conversation_id,
            run_id=run_id,
            interrupt_id=interrupt_id,
            expected_request_revision=expected_request_revision,
            expected_run_revision=expected_run_revision,
        )
    except DurableInterruptApiError as exc:
        _raise_api(exc)
        raise  # pragma: no cover


def service_resolve(
    db: Session,
    *,
    conversation_id: UUID,
    run_id: UUID,
    interrupt_id: UUID,
    token: str,
    resolution_request_id: UUID,
    expected_token_revision: int,
    expected_request_revision: int,
    expected_run_revision: int,
    outcome: str,
    values: Mapping[str, Any] | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    try:
        return resolve_interrupt_http(
            db,
            conversation_id=conversation_id,
            run_id=run_id,
            interrupt_id=interrupt_id,
            token=token,
            resolution_request_id=resolution_request_id,
            expected_token_revision=expected_token_revision,
            expected_request_revision=expected_request_revision,
            expected_run_revision=expected_run_revision,
            outcome=outcome,
            values=values,
            comment=comment,
        )
    except DurableInterruptApiError as exc:
        _raise_api(exc)
        raise  # pragma: no cover


def service_decide_call_owned(
    db: Session,
    *,
    conversation_id: UUID,
    run_id: UUID,
    interrupt_id: UUID,
    call_id: UUID | None = None,
    resolution_request_id: UUID,
    expected_request_revision: int,
    expected_run_revision: int,
    outcome: str,
    comment: str | None = None,
    actor: Any,
) -> dict[str, Any]:
    try:
        return decide_call_owned(
            db,
            conversation_id=conversation_id,
            run_id=run_id,
            interrupt_id=interrupt_id,
            call_id=call_id,
            resolution_request_id=resolution_request_id,
            expected_request_revision=expected_request_revision,
            expected_run_revision=expected_run_revision,
            outcome=outcome,
            comment=comment,
            actor=actor,
        )
    except DurableInterruptApiError as exc:
        _raise_api(exc)
        raise  # pragma: no cover


__all__ = [
    "CODE_DURABLE_INTERRUPT_AUTH_MODE_UNAVAILABLE",
    "CODE_CALL_OWNED_APPROVAL_REQUIRED",
    "CODE_DURABLE_INTERRUPT_CONVERSATION_MISMATCH",
    "CODE_DURABLE_INTERRUPT_NOT_FOUND",
    "CODE_INTERRUPT_ALREADY_RESOLVED",
    "CODE_INTERRUPT_REQUEST_REVISION_MISMATCH",
    "CODE_INTERRUPT_RUN_CANCELLED",
    "CODE_INTERRUPT_RUN_REVISION_MISMATCH",
    "DurableInterruptApiError",
    "ExpiryScanResult",
    "expire_one_interrupt",
    "get_interrupt_detail",
    "list_expired_pending_interrupt_ids",
    "list_pending_interrupts",
    "resolve_interrupt_http",
    "decide_call_owned",
    "rotate_interrupt_token",
    "scan_expired_interrupts",
    "serialize_interrupt_safe",
    "service_get_detail",
    "service_list_pending",
    "service_resolve",
    "service_decide_call_owned",
    "service_rotate_token",
]

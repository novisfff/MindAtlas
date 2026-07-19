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
    encode_checkpoint_v2,
)
from app.assistant.durable.contracts import DurableAgentCheckpointV2, DurableNextActionV2
from app.assistant.durable.models import (
    AssistantRunBudgetRevision,
    AssistantRunCheckpoint,
    AssistantRunInterrupt,
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
    CODE_INTERRUPT_ALREADY_RESOLVED,
    CODE_INTERRUPT_COMMENT_TOO_LONG,
    CODE_INTERRUPT_EXPIRED,
    CODE_INTERRUPT_IDEMPOTENCY_CONFLICT,
    CODE_INTERRUPT_NOT_FOUND,
    CODE_INTERRUPT_NOT_PENDING,
    CODE_INTERRUPT_OUTCOME_INVALID,
    CODE_INTERRUPT_PEPPER_REQUIRED,
    CODE_INTERRUPT_SCHEMA_INVALID,
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


def _allowed_actions(*, kind: str, status: str) -> list[str]:
    if status != "pending":
        return []
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
        "allowedActions": _allowed_actions(kind=kind, status=status),
        "fields": render_interrupt_fields(interrupt.field_schema),
        "requestPayload": dict(interrupt.request_payload or {}),
        "initialValues": dict(interrupt.initial_values or {}),
        "nodeId": str(interrupt.node_id),
        "nodeVisitId": str(interrupt.node_visit_id),
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
    run_svc = AssistantChatRunService(db)
    run = run_svc.get_run(conversation_id=conversation_id, run_id=run_id)
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
    pepper = _require_pepper_for_token_ops()
    run = _authorize_run(db, conversation_id=conversation_id, run_id=run_id)
    _get_interrupt_for_run(db, run_id=run_id, interrupt_id=interrupt_id)
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
) -> tuple[list[Any], UUID, UUID, datetime]:
    """Derive child budget + resume-ready Checkpoint rows (not yet committed)."""
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
    pepper = _require_pepper_for_token_ops()
    run = _authorize_run(db, conversation_id=conversation_id, run_id=run_id)
    interrupt_row = _get_interrupt_for_run(
        db, run_id=run_id, interrupt_id=interrupt_id
    )

    outcome = str(outcome)
    call_owned = str(interrupt_row.interrupt_origin) == "capability_call"
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
            from app.assistant.capability_calls.repository import (
                CapabilityCallConflict,
                CapabilityCallRepository,
            )

            call_id = result.interrupt.capability_call_id
            if call_id is None:
                raise DurableInterruptApiError(
                    "protocol_error",
                    "call-owned interrupt is missing capability_call_id",
                    status_code=409,
                )
            call_repo = CapabilityCallRepository(db)
            call = call_repo.get_call(call_id, for_update=True)
            if (
                call is None
                or call.run_id != run_id
                or call.interrupt_id != result.interrupt.id
            ):
                raise DurableInterruptApiError(
                    "approval_binding_mismatch",
                    "call-owned interrupt linkage does not match",
                    status_code=409,
                )
            binding = (result.interrupt.request_payload or {}).get(
                "approvalBindingDigest"
            )
            if not binding or str(call.approval_binding_digest) != str(binding):
                raise DurableInterruptApiError(
                    "approval_binding_mismatch",
                    "stored approval binding does not match the call",
                    status_code=409,
                )
            target = {
                "approved": "authorized",
                "rejected": "rejected",
                "expired": "expired",
                "cancelled": "cancelled",
            }.get(str(result.interrupt.status))
            if target is None:
                raise DurableInterruptApiError(
                    "protocol_error",
                    f"unsupported call-owned resolution {result.interrupt.status!r}",
                    status_code=409,
                )
            try:
                call_repo.transition_call(
                    call_id=call.id,
                    expected_call_revision=int(call.state_revision),
                    expected_run_revision=int(run.state_revision),
                    to_status=target,
                    lease=None,
                    approval_binding_digest=(
                        str(binding) if target == "authorized" else None
                    ),
                    failure_code=(
                        None if target == "authorized" else f"approval_{target}"
                    ),
                )
            except CapabilityCallConflict as exc:
                raise DurableInterruptApiError(
                    getattr(exc, "code", "approval_binding_mismatch"),
                    str(exc),
                    status_code=409,
                ) from exc
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


__all__ = [
    "CODE_DURABLE_INTERRUPT_AUTH_MODE_UNAVAILABLE",
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
    "rotate_interrupt_token",
    "scan_expired_interrupts",
    "serialize_interrupt_safe",
    "service_get_detail",
    "service_list_pending",
    "service_resolve",
    "service_rotate_token",
]

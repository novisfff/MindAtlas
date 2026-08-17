"""Capability call reconciliation operations (Plan 08 Task 7).

No production external write is enabled. This module provides the complete
backend contract for operator decisions with mode-matrix enforcement. The
authenticated HTTP transport lives in ``reconciliation_router``; the legacy
CLI remains inspection-only for mutations.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.assistant.capability_calls.models import (
    AssistantCapabilityCall,
    AssistantCapabilityCallAttempt,
    AssistantCapabilityReconciliation,
)
from app.assistant.capability_calls.result_codec import (
    CapabilityResultCodecError,
    decode_capability_result,
)
from app.assistant.capability_calls.repository import (
    CODE_CALL_NOT_FOUND,
    CODE_INVALID_TRANSITION,
    CODE_STALE_CALL_REVISION,
    CODE_STALE_RUN_REVISION,
    CapabilityCallConflict,
    CapabilityCallRepository,
)
from app.assistant.domain.digests import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_canonical_json,
)
from app.assistant.durable.checkpoints import _next_checkpoint_sequence
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
    AssistantRunCheckpoint,
    AssistantRunObligationRevision,
)
from app.assistant.durable.repository import (
    DurableChildBundle,
    DurableRunRepository,
    EventSpec,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_NEEDS_RECONCILIATION,
    STATUS_QUEUED,
)
from app.assistant.policy.obligations import (
    ObligationEvidenceEdge,
    ObligationLedgerState,
    compute_predicate_digest,
    pure_resolve_obligation,
)
from app.common.time import utcnow

ReconciliationDecision = Literal[
    "mark_succeeded",
    "mark_failed",
    "mark_compensated",
    "retry_same_key",
]

MODES_FORBIDDING_RETRY = frozenset(
    {
        "local_transactional",
        "non_retriable",
        "unsupported",
        "pure_replayable",
        "read_replayable",
    }
)


def _is_exact_call_reconciliation_obligation(item: Any, call_id: UUID) -> bool:
    """Match the immutable Call owner and source identity together.

    Either field alone is insufficient: accepting an OR match lets an
    obligation created for one Call be satisfied by another Call that happens
    to share an owner or source pointer.
    """
    return (
        str(getattr(item, "obligation_type", "")) == "reconciliation"
        and str(getattr(item, "owner_kind", "")) == "capability_call"
        and str(getattr(item, "owner_id", "")) == str(call_id)
        and str(getattr(item, "source_call_id", "")) == str(call_id)
    )


def validate_retry_authorization_for_dispatch(
    db: Session,
    *,
    call: AssistantCapabilityCall,
    now: datetime,
) -> dict[str, Any] | None:
    """Revalidate frozen retry claims immediately before a new Attempt claim.

    Normal first dispatches have no retry reconciliation row and return None.
    A Call re-authorized by reconciliation fails closed on any drift or expiry.
    """
    row = (
        db.query(AssistantCapabilityReconciliation)
        .filter(
            AssistantCapabilityReconciliation.call_id == call.id,
            AssistantCapabilityReconciliation.decision == "retry_same_key",
        )
        .order_by(AssistantCapabilityReconciliation.revision.desc())
        .with_for_update()
        .first()
    )
    if row is None:
        return None
    if str(call.status) != "authorized":
        raise CapabilityCallConflict(
            CODE_INVALID_TRANSITION,
            "retry authorization can dispatch only an authorized Call",
            call=call,
        )
    claims = (row.authorization_evidence or {}).get("verifiedClaims") or []
    candidates = [
        claim
        for claim in claims
        if claim.get("evidenceType")
        in {"retry_authorization", "external_status_lookup"}
    ]
    if not candidates:
        raise CapabilityCallConflict(
            CODE_INVALID_TRANSITION,
            "retry authorization lacks persisted verified claims",
            call=call,
        )
    claim = candidates[0]
    latest_attempt = (
        db.query(AssistantCapabilityCallAttempt)
        .filter(AssistantCapabilityCallAttempt.call_id == call.id)
        .order_by(AssistantCapabilityCallAttempt.attempt_number.desc())
        .with_for_update()
        .first()
    )
    expected = {
        "callId": str(call.id),
        "runId": str(call.run_id),
        "decision": "retry_same_key",
        "inputDigest": str(call.input_digest),
        "idempotencyKeyDigest": sha256_bytes(
            str(call.idempotency_key).encode("utf-8")
        ),
        "attempt": CapabilityReconciliationService._attempt_claim(latest_attempt),
    }
    if any(claim.get(key) != value for key, value in expected.items()):
        raise CapabilityCallConflict(
            CODE_INVALID_TRANSITION,
            "persisted retry authorization drifted from the frozen Call/Attempt",
            call=call,
        )
    provider_contract = str(claim.get("providerContract") or "").strip()
    if (
        latest_attempt is None
        or claim.get("requestDigest") != latest_attempt.request_digest
        or not provider_contract
        or len(provider_contract) > 256
    ):
        raise CapabilityCallConflict(
            CODE_INVALID_TRANSITION,
            "persisted retry request/provider contract is invalid",
            call=call,
        )
    try:
        maximum = int(claim["maxAttempts"])
        remaining = int(claim["remainingAttempts"])
        deadline = datetime.fromisoformat(
            str(claim["deadlineAt"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CapabilityCallConflict(
            CODE_INVALID_TRANSITION,
            "persisted retry bounds are invalid",
            call=call,
        ) from exc
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    if (
        maximum <= int(call.attempt_count)
        or remaining != maximum - int(call.attempt_count)
        or deadline.tzinfo is None
        or deadline <= current
    ):
        raise CapabilityCallConflict(
            CODE_INVALID_TRANSITION,
            "persisted retry authorization is exhausted or expired",
            call=call,
        )
    if str(call.execution_mode) == "external_reconcilable" and not any(
        item.get("evidenceType") == "external_status_lookup"
        and item.get("providerStatus") == "not_accepted"
        for item in candidates
    ):
        raise CapabilityCallConflict(
            CODE_INVALID_TRANSITION,
            "external_reconcilable retry lacks persisted not_accepted proof",
            call=call,
        )
    return claim


@dataclass(frozen=True, slots=True)
class ReconciliationDecisionRequest:
    call_id: UUID
    expected_call_revision: int
    expected_run_revision: int
    decision: ReconciliationDecision
    reason: str
    evidence_artifact_ids: tuple[UUID, ...]
    resolution_request_id: UUID


@dataclass(frozen=True, slots=True)
class AuthorizedReconciliationActor:
    """Identity established by a trusted server-side operator boundary."""

    actor_admin_id: UUID
    authorization_method: str
    session_id: UUID | None = None


OperatorAuthorizer = Callable[
    [ReconciliationDecisionRequest], AuthorizedReconciliationActor | None
]


class HmacReconciliationEvidenceVerifier:
    """Authenticates canonical reconciliation claims issued by a trusted service."""

    def __init__(self, secret: str | bytes, *, max_age: timedelta = timedelta(days=1)):
        raw = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
        if len(raw) < 32:
            raise ValueError("reconciliation evidence secret must be at least 32 bytes")
        self._secret = raw
        self.max_age = max_age

    def sign_claims(self, claims: dict[str, Any]) -> bytes:
        body = canonical_json_bytes(claims)  # type: ignore[arg-type]
        signature = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return canonical_json_bytes(  # type: ignore[arg-type]
            {"contractVersion": 1, "claims": claims, "signature": signature}
        )

    def verify(self, payload: bytes, *, now: datetime) -> dict[str, Any]:
        try:
            envelope = json.loads(payload.decode("utf-8"))
            claims = envelope["claims"]
            signature = str(envelope["signature"])
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError("signed reconciliation evidence envelope is invalid") from exc
        if envelope.get("contractVersion") != 1 or not isinstance(claims, dict):
            raise ValueError("signed reconciliation evidence version is invalid")
        expected = hmac.new(
            self._secret,
            canonical_json_bytes(claims),  # type: ignore[arg-type]
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signed reconciliation evidence signature mismatch")
        try:
            issued_at = datetime.fromisoformat(str(claims["issuedAt"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise ValueError("signed reconciliation evidence issuedAt is invalid") from exc
        if issued_at.tzinfo is None:
            raise ValueError("signed reconciliation evidence issuedAt must be timezone-aware")
        current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        age = current.astimezone(timezone.utc) - issued_at.astimezone(timezone.utc)
        if age < timedelta(minutes=-5) or age > self.max_age:
            raise ValueError("signed reconciliation evidence is expired or future-dated")
        return claims


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    call_id: UUID
    decision: str
    resulting_call_status: str
    resulting_call_revision: int
    resulting_run_revision: int
    reconciliation_id: UUID
    created: bool


class CapabilityReconciliationService:
    """Append-only reconciliation decisions with mode matrix enforcement."""

    def __init__(
        self,
        db: Session,
        *,
        operator_authorizer: OperatorAuthorizer | None = None,
        evidence_verifier: HmacReconciliationEvidenceVerifier | None = None,
        write_safety_lock: Any | None = None,
    ) -> None:
        self.db = db
        self.calls = CapabilityCallRepository(
            db,
            write_safety_lock=write_safety_lock,
        )
        self.operator_authorizer = operator_authorizer
        self.evidence_verifier = evidence_verifier
        self.write_safety_lock = self.calls.write_safety_lock

    def get_call(self, call_id: UUID) -> AssistantCapabilityCall | None:
        return self.calls.get_call(call_id)

    def list_for_run(self, run_id: UUID) -> list[AssistantCapabilityReconciliation]:
        return (
            self.db.query(AssistantCapabilityReconciliation)
            .filter(AssistantCapabilityReconciliation.run_id == run_id)
            .order_by(AssistantCapabilityReconciliation.revision.asc())
            .all()
        )

    def apply(
        self,
        request: ReconciliationDecisionRequest,
        *,
        now: datetime | None = None,
        actor: Any | None = None,
        commit: bool = True,
    ) -> ReconciliationResult:
        ts = now or utcnow()
        if not (request.reason or "").strip():
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION, "reconciliation reason is required"
            )
        if not request.evidence_artifact_ids:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "reconciliation requires at least one evidence artifact id",
            )
        if actor is None:
            if self.operator_authorizer is None:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "reconciliation requires a trusted operator authorization boundary",
                )
            actor = self.operator_authorizer(request)
        else:
            # HTTP production callers pass the authenticated OperatorPrincipal;
            # request JSON never supplies either identity field.
            try:
                from app.operator_auth.contracts import OperatorPrincipal

                if not isinstance(actor, OperatorPrincipal) or actor.role != "operator":
                    actor = None
                else:
                    actor = AuthorizedReconciliationActor(
                        actor_admin_id=actor.operator_id,
                        authorization_method=actor.authentication_method,
                        session_id=actor.session_id,
                    )
            except Exception:
                actor = None
        if (
            actor is None
            or not isinstance(actor.actor_admin_id, UUID)
            or not (actor.authorization_method or "").strip()
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "reconciliation denied by trusted operator authorization boundary",
            )
        if self.evidence_verifier is None:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "reconciliation requires a trusted evidence authenticity verifier",
            )

        from app.assistant.capability_calls.write_guard import (
            acquire_write_safety_advisory_lock,
        )

        if self.write_safety_lock is None:
            acquire_write_safety_advisory_lock(self.db)
        else:
            self.write_safety_lock.acquire(self.db)

        # Advisory lock is already held; resolve the parent without a row lock,
        # then continue Run -> Interrupt (none here) -> CapabilityCall.
        call_probe = self.calls.get_call(request.call_id)
        if call_probe is None:
            raise CapabilityCallConflict(
                CODE_CALL_NOT_FOUND, f"call {request.call_id} not found"
            )
        run = self.calls.get_run(call_probe.run_id, for_update=True)
        # Re-check idempotency only after the Run lock. Concurrent duplicates
        # serialize here and replay the persisted outcome, never mutable live state.
        existing = (
            self.db.query(AssistantCapabilityReconciliation)
            .filter(
                AssistantCapabilityReconciliation.run_id == run.id,
                AssistantCapabilityReconciliation.resolution_request_id
                == request.resolution_request_id,
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.call_id != request.call_id:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "resolution_request_id was already used for another Call in this Run",
                )
            stored_evidence = existing.authorization_evidence or {}
            stored_operator_id = str(stored_evidence.get("operatorId") or "")
            stored_session_id = str(stored_evidence.get("sessionId") or "")
            stored_authentication_method = str(
                stored_evidence.get("authorizationMethod") or ""
            )
            actor_session_id = getattr(actor, "session_id", None)
            if (
                str(existing.decision) != request.decision
                or str(existing.reason) != request.reason.strip()
                or int(existing.expected_call_revision)
                != int(request.expected_call_revision)
                or int(existing.expected_run_revision)
                != int(request.expected_run_revision)
                or list(existing.evidence_artifact_ids or [])
                != [str(value) for value in request.evidence_artifact_ids]
                or stored_operator_id != str(actor.actor_admin_id)
                or stored_session_id
                != (str(actor_session_id) if actor_session_id is not None else "")
                or stored_authentication_method
                != str(actor.authorization_method)
            ):
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "resolution_request_id was already used for a different decision or actor",
                )
            persisted_status = {
                "mark_succeeded": "succeeded",
                "mark_failed": "failed",
                "mark_compensated": "compensated",
                "retry_same_key": "authorized",
            }[str(existing.decision)]
            return ReconciliationResult(
                call_id=existing.call_id,
                decision=str(existing.decision),
                resulting_call_status=persisted_status,
                resulting_call_revision=int(existing.resulting_call_revision or 0),
                resulting_run_revision=int(existing.resulting_run_revision or 0),
                reconciliation_id=existing.id,
                created=False,
            )
        call = self.calls.get_call(request.call_id, for_update=True)
        if call is None:
            raise CapabilityCallConflict(
                CODE_CALL_NOT_FOUND, f"call {request.call_id} not found"
            )
        if int(call.state_revision) != int(request.expected_call_revision):
            raise CapabilityCallConflict(
                CODE_STALE_CALL_REVISION,
                f"expected call revision {request.expected_call_revision}, "
                f"got {call.state_revision}",
                call=call,
                run=run,
            )
        if int(run.state_revision) != int(request.expected_run_revision):
            raise CapabilityCallConflict(
                CODE_STALE_RUN_REVISION,
                f"expected run revision {request.expected_run_revision}, got {run.state_revision}",
                call=call,
                run=run,
            )
        if str(call.status) not in {"needs_reconciliation", "unknown"}:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                f"call status {call.status!r} is not reconcilable",
                call=call,
                run=run,
            )

        current_checkpoint, current_obligations = self._require_pending_context(
            run=run,
            call=call,
            decision=request.decision,
        )

        mode = str(call.execution_mode)
        decision = request.decision
        evidence, evidence_summary, verified_claims = self._validate_evidence(
            call=call,
            decision=decision,
            artifact_ids=request.evidence_artifact_ids,
            now=ts,
        )
        result_artifact = next(
            (
                artifact
                for artifact in evidence
                if str(artifact.kind) == "capability_call_result"
            ),
            None,
        )
        status_lookup_proved_not_accepted = any(
            claim.get("evidenceType") == "external_status_lookup"
            and claim.get("providerStatus") == "not_accepted"
            for claim in verified_claims
        )

        if decision == "retry_same_key":
            if mode in MODES_FORBIDDING_RETRY:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    f"retry_same_key forbidden for execution_mode={mode!r}",
                    call=call,
                    run=run,
                )
            if (
                mode == "external_reconcilable"
                and not status_lookup_proved_not_accepted
            ):
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "external_reconcilable requires authoritative not_accepted "
                    "status lookup before retry_same_key",
                    call=call,
                    run=run,
                )
            if mode == "external_idempotent" or (
                mode == "external_reconcilable" and status_lookup_proved_not_accepted
            ):
                if str(call.status) == "unknown":
                    call.status = "needs_reconciliation"
                    call.state_revision = int(call.state_revision) + 1
                    call.updated_at = ts
                    self.db.flush()
                call = self.calls.transition_call(
                    call_id=call.id,
                    expected_call_revision=int(call.state_revision),
                    expected_run_revision=int(run.state_revision),
                    to_status="authorized",
                    lease=None,
                    has_retry_same_key_authorization=True,
                    allow_while_cancelling=True,
                    now=ts,
                )
            else:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    f"retry_same_key not permitted for mode={mode!r}",
                    call=call,
                    run=run,
                )
        else:
            result_artifact, tool_message = self._build_terminal_projection(
                call=call,
                decision=decision,
                existing_result_artifact=result_artifact,
                current_checkpoint=current_checkpoint,
                now=ts,
            )
            if result_artifact.id is None:
                self.db.add(result_artifact)
                self.db.flush()
            to_status = {
                "mark_succeeded": "succeeded",
                "mark_failed": "failed",
                "mark_compensated": "compensated",
            }[decision]
            if str(call.status) == "unknown":
                call.status = "needs_reconciliation"
                call.state_revision = int(call.state_revision) + 1
                call.updated_at = ts
                self.db.flush()
            call = self.calls.transition_call(
                call_id=call.id,
                expected_call_revision=int(call.state_revision),
                expected_run_revision=int(run.state_revision),
                to_status=to_status,
                lease=None,
                output_artifact_id=result_artifact.id,
                allow_while_cancelling=True,
                now=ts,
            )

        next_rev = (
            self.db.query(AssistantCapabilityReconciliation)
            .filter(AssistantCapabilityReconciliation.call_id == call.id)
            .count()
            + 1
        )
        resulting_run_revision = int(run.state_revision) + 1
        row = AssistantCapabilityReconciliation(
            id=uuid4(),
            call_id=call.id,
            run_id=run.id,
            revision=next_rev,
            decision=decision,
            actor_user_id=None,
            actor_admin_id=actor.actor_admin_id,
            authorization_evidence={
                "mode": mode,
                "authorizationMethod": actor.authorization_method,
                "operatorId": str(actor.actor_admin_id),
                "sessionId": (
                    str(actor.session_id) if actor.session_id is not None else None
                ),
                "evidence": evidence_summary,
                "verifiedClaims": verified_claims,
            },
            reason=request.reason.strip(),
            evidence_artifact_ids=[str(x) for x in request.evidence_artifact_ids],
            expected_call_revision=request.expected_call_revision,
            expected_run_revision=request.expected_run_revision,
            resulting_call_revision=int(call.state_revision),
            resulting_run_revision=resulting_run_revision,
            resolution_request_id=request.resolution_request_id,
            created_at=ts,
        )
        self.db.add(row)
        self.db.flush()
        obligation_children = self._resolve_reconciliation_obligation(
            run=run,
            call=call,
            decision=decision,
            evidence_summary=evidence_summary,
            current=current_obligations,
            now=ts,
        )
        # Establish the self-referential obligation revision before Checkpoint /
        # ProviderMessage children reference it. This remains in the same Run
        # transaction and avoids dialect-dependent insert ordering.
        for child in obligation_children.rows:
            self.db.add(child)
        self.db.flush()
        aggregate_children, target_run_status = self._build_reconciliation_checkpoint(
            run=run,
            call=call,
            decision=decision,
            obligation_children=obligation_children,
            current=current_checkpoint,
            tool_message=(tool_message if decision != "retry_same_key" else None),
            now=ts,
        )
        commit_result = DurableRunRepository(self.db).commit_reconciliation_resolution(
            run_id=run.id,
            expected_revision=int(run.state_revision),
            target_status=target_run_status,
            failure_code=(
                "capability_reconciliation_failed"
                if target_run_status == STATUS_FAILED
                else None
            ),
            events=(
                EventSpec(
                    event_key=(
                        f"capability_call.reconciled:{call.id}:"
                        f"{request.resolution_request_id}"
                    ),
                    event_name="capability_call.reconciled",
                    payload={
                        "callId": str(call.id),
                        "decision": decision,
                        "status": str(call.status),
                        "resolutionRequestId": str(request.resolution_request_id),
                        "runStatus": target_run_status,
                    },
                    visibility="public",
                ),
            ),
            children=aggregate_children,
            commit=commit,
        )
        return ReconciliationResult(
            call_id=call.id,
            decision=decision,
            resulting_call_status=str(call.status),
            resulting_call_revision=int(call.state_revision),
            resulting_run_revision=int(commit_result.run.state_revision),
            reconciliation_id=row.id,
            created=True,
        )

    def _require_pending_context(
        self,
        *,
        run: Any,
        call: AssistantCapabilityCall,
        decision: ReconciliationDecision,
    ) -> tuple[Any, AssistantRunObligationRevision]:
        """Require the exact durable checkpoint/obligation that authorizes wake-up."""
        if run.current_checkpoint_id is None:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "reconciliation requires a current durable checkpoint",
                call=call,
                run=run,
            )
        checkpoint_row = (
            self.db.query(AssistantRunCheckpoint)
            .filter(AssistantRunCheckpoint.id == run.current_checkpoint_id)
            .with_for_update()
            .one_or_none()
        )
        if checkpoint_row is None or checkpoint_row.run_id != run.id:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "Run reconciliation checkpoint pointer is invalid",
                call=call,
                run=run,
            )
        checkpoint = decode_checkpoint(checkpoint_row.state_payload)
        if int(getattr(checkpoint, "schema_version", 0)) != 3:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "Plan 08 reconciliation requires checkpoint schema v3",
                call=call,
                run=run,
            )
        if checkpoint.obligation_revision_id != run.current_obligation_revision_id:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "checkpoint does not reference the current obligation revision",
                call=call,
                run=run,
            )
        call_states = [item for item in checkpoint.capability_calls if item.call_id == call.id]
        if len(call_states) != 1:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "Run reconciliation checkpoint does not contain the Call exactly once",
                call=call,
                run=run,
            )
        call_state = call_states[0]
        if (
            call_state.logical_call_key != str(call.logical_call_key)
            or call_state.provider_tool_call_id != str(call.provider_tool_call_id)
            or call_state.status not in {"unknown", "needs_reconciliation"}
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "Run reconciliation checkpoint Call identity/status is stale",
                call=call,
                run=run,
            )
        continuation = checkpoint.provider_loop_continuation
        if continuation is not None and (
            continuation.execution_scope.run_id != run.id
            or continuation.waiting_call.call_id
            != str(call.provider_tool_call_id)
            or continuation.transcript_digest
            != checkpoint.provider_transcript_digest
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "Run reconciliation continuation is not bound to the Call/transcript",
                call=call,
                run=run,
            )
        if decision == "retry_same_key" and checkpoint.phase == "terminal":
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "retry_same_key is forbidden from a terminal checkpoint",
                call=call,
                run=run,
            )
        if run.current_obligation_revision_id is None:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "reconciliation requires a current obligation revision",
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
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "Run reconciliation obligation pointer is invalid",
                call=call,
                run=run,
            )
        state = ObligationLedgerState.model_validate(current.payload)
        pending = [item for item in state.obligations if item.status == "pending"]
        matching = [
            item
            for item in pending
            if _is_exact_call_reconciliation_obligation(item, call.id)
        ]
        if len(matching) != 1:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "reconciliation requires exactly one pending obligation for the Call",
                call=call,
                run=run,
            )
        # Other pending obligations belong to other durable owners.  Resolving
        # this exact Call remains valid; the Run stays unresolved until those
        # obligations are also handled.
        return checkpoint, current

    def _build_terminal_projection(
        self,
        *,
        call: AssistantCapabilityCall,
        decision: ReconciliationDecision,
        existing_result_artifact: AssistantRunArtifact | None,
        current_checkpoint: Any,
        now: datetime,
    ) -> tuple[AssistantRunArtifact, Any]:
        from app.assistant.capabilities.contracts import (
            CapabilityError,
            CapabilityMetrics,
            CapabilityResult,
        )
        from app.assistant.durable.checkpoints import _current_transcript_digest
        from app.assistant.provider_loop.messages import (
            ProviderAssistantMessage,
            ProviderToolMessage,
            project_tool_result_envelope,
        )

        ordinal, transcript_digest, transcript = _current_transcript_digest(
            self.db, call.run_id
        )
        if (
            ordinal != int(current_checkpoint.provider_message_ordinal)
            or transcript_digest != str(current_checkpoint.provider_transcript_digest)
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "durable Provider transcript does not match the current checkpoint",
                call=call,
            )
        provider_alias = None
        for message in transcript:
            if not isinstance(message, ProviderAssistantMessage):
                continue
            for provider_call in message.tool_calls:
                if provider_call.call_id == str(call.provider_tool_call_id):
                    provider_alias = provider_call.provider_alias
        if provider_alias is None:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "reconciled Call has no durable preceding Provider Tool Call",
                call=call,
            )

        if decision == "mark_succeeded":
            if existing_result_artifact is None:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "mark_succeeded requires a normalized result Artifact",
                    call=call,
                )
            result = self._decode_normalized_result(
                call=call, artifact=existing_result_artifact
            ).capability_result
            artifact = existing_result_artifact
        else:
            metrics = CapabilityMetrics(
                duration_ms=0,
                adapter_duration_ms=None,
                input_bytes=0,
                output_bytes=0,
            )
            if decision == "mark_failed":
                result = CapabilityResult(
                    status="failed",
                    user_text="The capability call was resolved as failed.",
                    structured_output={"reconciliationDecision": "mark_failed"},
                    artifact_refs=(),
                    continuation=None,
                    terminal_output=False,
                    needs_followup=False,
                    error=CapabilityError(
                        error_type="execution_failed",
                        safe_code="capability_reconciliation_failed",
                        safe_message="Authorized reconciliation resolved the call as failed.",
                        retry_disposition="never",
                        call_id=str(call.provider_tool_call_id),
                    ),
                    metrics=metrics,
                )
            else:
                result = CapabilityResult(
                    status="completed",
                    user_text="The capability call was resolved after compensation.",
                    structured_output={
                        "reconciliationDecision": "mark_compensated",
                        "compensated": True,
                    },
                    artifact_refs=(),
                    continuation=None,
                    terminal_output=False,
                    needs_followup=False,
                    error=None,
                    metrics=metrics,
                )
            payload = canonical_json_bytes(  # type: ignore[arg-type]
                {
                    "contractVersion": 1,
                    "callId": str(call.provider_tool_call_id),
                    "bindingContractDigest": str(call.authorization_digest),
                    "descriptorDigest": str(call.descriptor_digest),
                    "decision": decision,
                    "result": result.model_dump(mode="json"),
                }
            )
            artifact = AssistantRunArtifact(
                run_id=call.run_id,
                kind="capability_call_reconciliation_result",
                media_type="application/json",
                storage_kind="inline",
                byte_size=len(payload),
                content_sha256=sha256_bytes(payload),
                inline_bytes=payload,
                metadata_json={
                    "contractVersion": 1,
                    "callId": str(call.id),
                    "decision": decision,
                    "reconciliationResult": True,
                },
                created_at=now,
            )
        tool_message = ProviderToolMessage(
            call_id=str(call.provider_tool_call_id),
            provider_alias=provider_alias,
            content=project_tool_result_envelope(
                domain_key=str(call.domain_key), result=result
            ),
        )
        return artifact, tool_message

    def _resolve_reconciliation_obligation(
        self,
        *,
        run: Any,
        call: AssistantCapabilityCall,
        decision: ReconciliationDecision,
        evidence_summary: dict[str, Any],
        current: AssistantRunObligationRevision,
        now: datetime,
    ) -> DurableChildBundle:
        state = ObligationLedgerState.model_validate(current.payload)
        obligation = next(
            (
                item
                for item in state.obligations
                if item.status == "pending"
                and _is_exact_call_reconciliation_obligation(item, call.id)
            ),
            None,
        )
        if obligation is None:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "Run lacks the exact pending reconciliation obligation",
                call=call,
                run=run,
            )
        evidence_digest = sha256_canonical_json(
            {
                "callId": str(call.id),
                "decision": decision,
                "artifactDigests": evidence_summary["artifactDigests"],
            }
        )
        edge = ObligationEvidenceEdge(
            obligation_id=obligation.obligation_id,
            evidence_kind="artifact",
            source_owner_version_id=None,
            source_call_id=str(call.id),
            evidence_digest=evidence_digest,
            predicate_digest=compute_predicate_digest(
                evidence_kind="artifact",
                obligation_type="reconciliation",
                owner_kind="capability_call",
            ),
        )
        resolved, result = pure_resolve_obligation(
            state,
            obligation_id=obligation.obligation_id,
            status="satisfied",
            evidence_edge=edge,
        )
        if not result.allowed:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "Run reconciliation obligation could not be resolved",
                call=call,
                run=run,
            )
        row = AssistantRunObligationRevision(
            id=uuid4(),
            run_id=run.id,
            revision=int(current.revision) + 1,
            parent_revision_id=current.id,
            parent_digest=current.obligation_digest,
            obligation_digest=resolved.ledger_digest,
            payload=resolved.model_dump(mode="json", by_alias=True),
            created_at=now,
        )
        return DurableChildBundle(
            rows=[row], current_obligation_revision_id=row.id
        )

    def _build_reconciliation_checkpoint(
        self,
        *,
        run: Any,
        call: AssistantCapabilityCall,
        decision: ReconciliationDecision,
        obligation_children: DurableChildBundle,
        current: Any,
        tool_message: Any | None,
        now: datetime,
    ) -> tuple[DurableChildBundle, str]:
        from app.assistant.durable.checkpoints import (
            _build_provider_message_rows,
            _current_transcript_digest,
        )
        from app.assistant.provider_loop.messages import (
            ProviderToolMessage,
            digest_provider_message,
            digest_provider_transcript,
            make_cancelled_envelope,
            open_provider_tool_calls,
            validate_provider_transcript,
        )

        ordinal, transcript_digest, prior = _current_transcript_digest(self.db, run.id)
        if (
            ordinal != int(current.provider_message_ordinal)
            or transcript_digest != str(current.provider_transcript_digest)
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "durable Provider transcript does not match the current checkpoint",
                call=call,
                run=run,
            )
        tool_messages = [tool_message] if tool_message is not None else []
        sibling_updates: dict[UUID, tuple[AssistantCapabilityCall, Any]] = {}
        continuation = current.provider_loop_continuation
        if tool_message is not None:
            try:
                open_calls = open_provider_tool_calls(prior)
                open_ids = [item.call_id for item in open_calls]
                if not open_ids or open_ids[0] != str(call.provider_tool_call_id):
                    raise ValueError(
                        "reconciled Call must be the first unpaired Tool Call"
                    )
                pending_provider_ids = open_ids[1:]
            except (TypeError, ValueError) as exc:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "durable transcript cannot identify the reconciliation sibling suffix",
                    call=call,
                    run=run,
                ) from exc
        else:
            pending_provider_ids = []
        if tool_message is not None and continuation is not None:
            try:
                validate_provider_transcript(
                    prior, allowed_open_continuation=continuation
                )
            except (TypeError, ValueError) as exc:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "waiting reconciliation continuation does not match the durable transcript",
                    call=call,
                    run=run,
                ) from exc
            if str(continuation.waiting_call.call_id) != str(
                call.provider_tool_call_id
            ):
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "reconciliation continuation waiting Call does not match the ledger Call",
                    call=call,
                    run=run,
                )
            if list(continuation.pending_call_ids) != pending_provider_ids:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "reconciliation continuation does not match the open sibling suffix",
                    call=call,
                    run=run,
                )
        if tool_message is not None:
            for pending_provider_id in pending_provider_ids:
                sibling = (
                    self.db.query(AssistantCapabilityCall)
                    .filter(
                        AssistantCapabilityCall.run_id == run.id,
                        AssistantCapabilityCall.provider_tool_call_id
                        == str(pending_provider_id),
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if sibling is None:
                    raise CapabilityCallConflict(
                        CODE_INVALID_TRANSITION,
                        "pending reconciliation sibling is missing from the ledger",
                        call=call,
                        run=run,
                    )
                has_attempt = (
                    self.db.query(AssistantCapabilityCallAttempt.id)
                    .filter(AssistantCapabilityCallAttempt.call_id == sibling.id)
                    .first()
                    is not None
                )
                if (
                    has_attempt
                    or int(sibling.attempt_count or 0) != 0
                    or sibling.side_effect_started_at is not None
                    or sibling.interrupt_id is not None
                ):
                    raise CapabilityCallConflict(
                        CODE_INVALID_TRANSITION,
                        "started or waiting sibling requires independent settlement",
                        call=call,
                        run=run,
                    )
                sibling_status = str(sibling.status)
                if sibling_status in {"proposed", "authorized"}:
                    sibling = self.calls.transition_call(
                        call_id=sibling.id,
                        expected_call_revision=int(sibling.state_revision),
                        expected_run_revision=int(run.state_revision),
                        to_status="cancelled",
                        lease=None,
                        now=now,
                    )
                    sibling_status = "cancelled"
                elif sibling_status != "denied":
                    raise CapabilityCallConflict(
                        CODE_INVALID_TRANSITION,
                        f"pending sibling status {sibling_status!r} cannot be sealed",
                        call=call,
                        run=run,
                    )
                sibling_message = ProviderToolMessage(
                    call_id=str(sibling.provider_tool_call_id),
                    provider_alias=next(
                        provider_call.provider_alias
                        for message in prior
                        if hasattr(message, "tool_calls")
                        for provider_call in message.tool_calls
                        if provider_call.call_id
                        == str(sibling.provider_tool_call_id)
                    ),
                    content=make_cancelled_envelope(
                        domain_key=str(sibling.domain_key),
                        status="cancelled_before_start",
                        safe_code=(
                            str(sibling.failure_code or "policy_denied")
                            if sibling_status == "denied"
                            else "cancelled_before_start"
                        ),
                        safe_message=(
                            "pending sibling was denied by policy"
                            if sibling_status == "denied"
                            else "pending sibling sealed before start"
                        ),
                        call_id=str(sibling.provider_tool_call_id),
                    ),
                )
                tool_messages.append(sibling_message)
                sibling_updates[sibling.id] = (sibling, sibling_message)

        suffix = tuple(tool_messages)
        transcript = prior + suffix
        if tool_message is not None:
            try:
                validate_provider_transcript(transcript)
            except (TypeError, ValueError) as exc:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "reconciliation Tool results do not close the Provider transcript",
                    call=call,
                    run=run,
                ) from exc
        start_ordinal = 1 if ordinal == 0 else ordinal + 1
        provider_rows = _build_provider_message_rows(
            run_id=run.id,
            messages=suffix,
            start_ordinal=start_ordinal,
            manifest_revision_id=current.manifest_revision_id,
            policy_revision_id=current.policy_revision_id,
            obligation_revision_id=(
                obligation_children.current_obligation_revision_id
                or current.obligation_revision_id
            ),
        )
        final_ordinal = start_ordinal + len(suffix) - 1 if suffix else ordinal
        final_transcript_digest = digest_provider_transcript(transcript)
        result_message_digest = (
            digest_provider_message(tool_message) if tool_message is not None else None
        )
        replaced = False
        seen_sibling_updates: set[UUID] = set()
        call_states = []
        latest_attempt = (
            self.db.query(AssistantCapabilityCallAttempt)
            .filter(AssistantCapabilityCallAttempt.call_id == call.id)
            .order_by(AssistantCapabilityCallAttempt.attempt_number.desc())
            .with_for_update()
            .first()
        )
        for item in current.capability_calls:
            if item.call_id in sibling_updates:
                sibling, sibling_message = sibling_updates[item.call_id]
                if (
                    item.call_id in seen_sibling_updates
                    or str(item.logical_call_key) != str(sibling.logical_call_key)
                    or str(item.provider_tool_call_id)
                    != str(sibling.provider_tool_call_id)
                    or str(item.status) not in {"proposed", "authorized", "denied"}
                ):
                    raise CapabilityCallConflict(
                        CODE_INVALID_TRANSITION,
                        "pending sibling checkpoint identity or state is invalid",
                        call=call,
                        run=run,
                    )
                seen_sibling_updates.add(item.call_id)
                if item.result_message_digest is not None:
                    raise CapabilityCallConflict(
                        CODE_INVALID_TRANSITION,
                        "pending sibling already has a Tool result",
                        call=call,
                        run=run,
                    )
                call_states.append(
                    DurableCapabilityCallStateV1(
                        call_id=item.call_id,
                        logical_call_key=item.logical_call_key,
                        provider_tool_call_id=item.provider_tool_call_id,
                        provider_order=item.provider_order,
                        status=str(sibling.status),
                        attempt_id=None,
                        output_artifact_id=sibling.output_artifact_id,
                        interrupt_id=sibling.interrupt_id,
                        approval_binding_digest=sibling.approval_binding_digest,
                        result_message_digest=digest_provider_message(
                            sibling_message
                        ),
                    )
                )
                continue
            if item.call_id != call.id:
                call_states.append(item)
                continue
            if tool_message is not None and item.result_message_digest is not None:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "reconciled Call already has a Tool result",
                    call=call,
                    run=run,
                )
            call_states.append(
                DurableCapabilityCallStateV1(
                    call_id=item.call_id,
                    logical_call_key=item.logical_call_key,
                    provider_tool_call_id=item.provider_tool_call_id,
                    provider_order=item.provider_order,
                    status=str(call.status),
                    attempt_id=(latest_attempt.id if latest_attempt is not None else None),
                    output_artifact_id=call.output_artifact_id,
                    interrupt_id=call.interrupt_id,
                    approval_binding_digest=call.approval_binding_digest,
                    result_message_digest=(
                        result_message_digest
                        if tool_message is not None
                        else item.result_message_digest
                    ),
                )
            )
            replaced = True
        if not replaced:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "Run reconciliation checkpoint does not contain the Call",
                call=call,
                run=run,
            )
        if seen_sibling_updates != set(sibling_updates):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "pending sibling is missing from the reconciliation checkpoint",
                call=call,
                run=run,
            )
        obligation_id = (
            obligation_children.current_obligation_revision_id
            or current.obligation_revision_id
        )
        resolved_obligation_row = next(
            (
                row
                for row in obligation_children.rows
                if isinstance(row, AssistantRunObligationRevision)
            ),
            None,
        )
        remaining_pending = False
        if resolved_obligation_row is not None:
            resolved_state = ObligationLedgerState.model_validate(
                resolved_obligation_row.payload
            )
            remaining_pending = any(
                item.status == "pending" for item in resolved_state.obligations
            )

        if remaining_pending:
            # Do not mark a Run terminal while a sibling obligation remains.
            # A Tool result seals the open Provider suffix (including every
            # unstarted sibling), so the old continuation would point back to
            # the already-resolved waiting Call.  Only a retry-without-result
            # may preserve that continuation for a future dispatch.
            continuation_after = (
                current.provider_loop_continuation
                if tool_message is None
                else None
            )
            if continuation_after is not None:
                phase = "waiting"
                next_action = DurableNextActionV2(kind="wait")
            else:
                phase = "terminal"
                next_action = DurableNextActionV2(kind="reconcile")
            target_run_status = STATUS_NEEDS_RECONCILIATION
        elif decision == "retry_same_key":
            phase = "ready_for_provider"
            next_action = DurableNextActionV2(kind="dispatch_calls")
            target_run_status = STATUS_QUEUED
        elif current.provider_loop_continuation is not None and current.phase != "terminal":
            phase = "ready_for_provider"
            next_action = DurableNextActionV2(kind="continue_provider")
            target_run_status = STATUS_QUEUED
        else:
            phase = "terminal"
            next_action = DurableNextActionV2(kind="terminal")
            target_run_status = (
                STATUS_FAILED if decision == "mark_failed" else STATUS_COMPLETED
            )
        checkpoint = current.model_copy(
            update={
                "phase": phase,
                "obligation_revision_id": obligation_id,
                "next_action": next_action,
                "provider_loop_continuation": (
                    continuation_after if remaining_pending else None
                ),
                "provider_message_ordinal": final_ordinal,
                "provider_transcript_digest": final_transcript_digest,
                "artifact_ids": tuple(
                    dict.fromkeys(
                        [
                            *current.artifact_ids,
                            *(
                                [call.output_artifact_id]
                                if call.output_artifact_id is not None
                                else []
                            ),
                        ]
                    )
                ),
                "capability_calls": tuple(call_states),
            }
        )
        checkpoint_id = uuid4()
        row = AssistantRunCheckpoint(
            id=checkpoint_id,
            run_id=run.id,
            sequence=_next_checkpoint_sequence(self.db, run.id),
            expected_state_revision=int(run.state_revision),
            committed_state_revision=int(run.state_revision) + 1,
            schema_version=3,
            manifest_revision_id=current.manifest_revision_id,
            policy_revision_id=current.policy_revision_id,
            budget_revision_id=current.budget_revision_id,
            obligation_revision_id=obligation_id,
            provider_message_ordinal=final_ordinal,
            provider_transcript_digest=final_transcript_digest,
            phase=phase,
            logical_unit_id=str(call.logical_call_key),
            reason="capability_call_reconciled",
            state_payload=encode_checkpoint_v3(checkpoint),
            state_digest=checkpoint_state_digest(checkpoint),
            created_at=now,
        )
        return DurableChildBundle(
            rows=[*obligation_children.rows, *provider_rows, row],
            current_checkpoint_id=checkpoint_id,
            current_obligation_revision_id=(
                obligation_children.current_obligation_revision_id
            ),
        ), target_run_status

    def _validate_evidence(
        self,
        *,
        call: AssistantCapabilityCall,
        decision: ReconciliationDecision,
        artifact_ids: tuple[UUID, ...],
        now: datetime,
    ) -> tuple[list[AssistantRunArtifact], dict[str, Any], list[dict[str, Any]]]:
        if len(set(artifact_ids)) != len(artifact_ids):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "reconciliation evidence artifact ids must be unique",
                call=call,
            )
        rows = (
            self.db.query(AssistantRunArtifact)
            .filter(AssistantRunArtifact.id.in_(artifact_ids))
            .with_for_update()
            .all()
        )
        by_id = {row.id: row for row in rows}
        if len(by_id) != len(artifact_ids):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "reconciliation evidence artifact is missing",
                call=call,
            )

        evidence = [by_id[artifact_id] for artifact_id in artifact_ids]
        evidence_types: list[str] = []
        digests: list[str] = []
        verified_claims: list[dict[str, Any]] = []
        latest_attempt = (
            self.db.query(AssistantCapabilityCallAttempt)
            .filter(AssistantCapabilityCallAttempt.call_id == call.id)
            .order_by(AssistantCapabilityCallAttempt.attempt_number.desc())
            .with_for_update()
            .first()
        )
        for artifact in evidence:
            if artifact.run_id != call.run_id:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "reconciliation evidence artifact belongs to another Run",
                    call=call,
                )
            digest = str(artifact.content_sha256 or "")
            if len(digest) != 64 or int(artifact.byte_size) < 0:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "reconciliation evidence artifact integrity metadata is invalid",
                    call=call,
                )
            if str(artifact.storage_kind) == "inline":
                payload = artifact.inline_bytes
                if payload is None:
                    raise CapabilityCallConflict(
                        CODE_INVALID_TRANSITION,
                        "inline reconciliation evidence is missing bytes",
                        call=call,
                    )
                actual_digest = hashlib.sha256(payload).hexdigest()
                if int(artifact.byte_size) != len(payload) or not hmac.compare_digest(
                    digest, actual_digest
                ):
                    raise CapabilityCallConflict(
                        CODE_INVALID_TRANSITION,
                        "inline reconciliation evidence failed integrity validation",
                        call=call,
                    )
                if str(artifact.media_type) != "application/json":
                    raise CapabilityCallConflict(
                        CODE_INVALID_TRANSITION,
                        "inline reconciliation evidence must be application/json",
                        call=call,
                    )
            elif str(artifact.storage_kind) == "object":
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "object reconciliation evidence requires a verified object reader",
                    call=call,
                )
            else:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "reconciliation evidence storage kind is unsupported",
                    call=call,
                )

            if str(artifact.kind) == "capability_call_result":
                digests.append(digest)
                continue
            try:
                claims = self.evidence_verifier.verify(
                    bytes(artifact.inline_bytes),  # type: ignore[arg-type]
                    now=now,
                )
            except ValueError as exc:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION, str(exc), call=call
                ) from exc
            claims = self._sanitize_verified_claims(claims)
            evidence_type = str(claims.get("evidenceType") or "")
            expected_attempt = self._attempt_claim(latest_attempt)
            expected_base = {
                "callId": str(call.id),
                "runId": str(call.run_id),
                "decision": decision,
                "inputDigest": str(call.input_digest),
                "idempotencyKeyDigest": sha256_bytes(
                    str(call.idempotency_key).encode("utf-8")
                ),
                "attempt": expected_attempt,
            }
            if (
                str(call.execution_mode) == "local_transactional"
                and decision != "retry_same_key"
            ):
                expected_base["entryObservation"] = self._local_entry_observation(call)
            if not evidence_type or any(
                claims.get(key) != value for key, value in expected_base.items()
            ):
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "signed reconciliation evidence is not bound to durable Call evidence",
                    call=call,
                )
            evidence_types.append(evidence_type)
            digests.append(digest)
            verified_claims.append(claims)

        required_type = {
            "mark_succeeded": "capability_call_success_attestation",
            "mark_failed": "capability_call_failure",
            "mark_compensated": "capability_call_compensation",
        }.get(decision)
        if required_type is not None and required_type not in evidence_types:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                f"{decision} requires {required_type!r} evidence",
                call=call,
            )
        if decision == "mark_succeeded":
            results = [
                artifact
                for artifact in evidence
                if str(artifact.kind) == "capability_call_result"
            ]
            if len(results) != 1:
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "mark_succeeded requires exactly one normalized "
                    "capability_call_result artifact",
                    call=call,
                )
            self._validate_normalized_result(call=call, artifact=results[0])
            if not any(
                claim.get("evidenceType") == "capability_call_success_attestation"
                and claim.get("resultArtifactDigest")
                == str(results[0].content_sha256)
                for claim in verified_claims
            ):
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "mark_succeeded requires signed result attestation",
                    call=call,
                )
            if (
                str(call.execution_mode) == "local_transactional"
                and self._local_entry_observation(call).get("kind") != "present"
            ):
                raise CapabilityCallConflict(
                    CODE_INVALID_TRANSITION,
                    "local mark_succeeded requires an observed durable Entry",
                    call=call,
                )
        if decision == "mark_failed" and not any(
            claim.get("failureDisposition")
            in {"proven_not_occurred", "explicit_product_acceptance_unresolved"}
            for claim in verified_claims
            if claim.get("evidenceType") == "capability_call_failure"
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "mark_failed requires a signed failure disposition",
                call=call,
            )
        if (
            decision == "mark_failed"
            and str(call.execution_mode) == "local_transactional"
            and self._local_entry_observation(call).get("kind") != "proven_absent"
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "local mark_failed requires proven absence of the Entry",
                call=call,
            )
        if decision == "mark_compensated" and not any(
            claim.get("compensationStatus") == "completed"
            and bool(str(claim.get("compensationActionId") or "").strip())
            for claim in verified_claims
            if claim.get("evidenceType") == "capability_call_compensation"
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "mark_compensated requires an independently completed compensation action",
                call=call,
            )
        if decision == "retry_same_key" and not any(
            evidence_type in {"retry_authorization", "external_status_lookup"}
            for evidence_type in evidence_types
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "retry_same_key requires typed retry evidence",
                call=call,
            )
        if decision == "retry_same_key":
            retry_claim = next(
                (
                    claim
                    for claim in verified_claims
                    if claim.get("evidenceType")
                    in {"retry_authorization", "external_status_lookup"}
                ),
                None,
            )
            self._validate_retry_claim(
                call=call,
                latest_attempt=latest_attempt,
                claim=retry_claim,
                now=now,
            )

        return evidence, {
            "artifactCount": len(evidence),
            "artifactIds": [str(artifact.id) for artifact in evidence],
            "artifactDigests": digests,
            "evidenceTypes": evidence_types,
            "verifiedClaims": verified_claims,
        }, verified_claims

    @staticmethod
    def _sanitize_verified_claims(claims: dict[str, Any]) -> dict[str, Any]:
        """Whitelist bounded audit/authorization fields; discard provider payloads."""
        allowed = {
            "callId",
            "runId",
            "decision",
            "evidenceType",
            "inputDigest",
            "idempotencyKeyDigest",
            "attempt",
            "issuedAt",
            "resultArtifactDigest",
            "failureDisposition",
            "acceptanceReasonDigest",
            "acceptedBy",
            "authorizationMethod",
            "compensationStatus",
            "compensationActionId",
            "providerContract",
            "providerStatus",
            "requestDigest",
            "maxAttempts",
            "remainingAttempts",
            "deadlineAt",
            "diagnosticArtifactId",
            "diagnosticArtifactDigest",
            "entryObservation",
        }
        sanitized = {key: value for key, value in claims.items() if key in allowed}
        attempt = sanitized.get("attempt")
        if isinstance(attempt, dict):
            sanitized["attempt"] = {
                key: attempt.get(key)
                for key in (
                    "attemptId",
                    "status",
                    "requestDigest",
                    "responseDigest",
                    "diagnosticArtifactId",
                )
            }
        return sanitized

    def _local_entry_observation(self, call: AssistantCapabilityCall) -> dict[str, Any]:
        """Return the durable truth for a local ``create_entry`` settlement."""
        if str(call.execution_mode) != "local_transactional":
            return {"kind": "not_applicable", "entryId": None}
        from app.entry.models import Entry

        entry = (
            self.db.query(Entry)
            .filter(Entry.source_capability_call_id == call.id)
            .with_for_update()
            .one_or_none()
        )
        return {
            "kind": "present" if entry is not None else "proven_absent",
            "entryId": str(entry.id) if entry is not None else None,
        }

    @staticmethod
    def _attempt_claim(attempt: AssistantCapabilityCallAttempt | None) -> dict[str, Any] | None:
        if attempt is None:
            return None
        return {
            "attemptId": str(attempt.id),
            "status": str(attempt.status),
            "requestDigest": attempt.request_digest,
            "responseDigest": attempt.response_digest,
            "diagnosticArtifactId": (
                str(attempt.diagnostic_artifact_id)
                if attempt.diagnostic_artifact_id is not None
                else None
            ),
        }

    @staticmethod
    def _decode_normalized_result(
        *, call: AssistantCapabilityCall, artifact: AssistantRunArtifact
    ) -> Any:
        if (
            str(artifact.media_type) != "application/json"
            or (artifact.metadata_json or {}).get("contractVersion") != 1
            or str(artifact.storage_kind) != "inline"
            or artifact.inline_bytes is None
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "mark_succeeded result Artifact is not normalized",
                call=call,
            )
        try:
            result = decode_capability_result(
                bytes(artifact.inline_bytes),
                expected_digest=str(artifact.content_sha256),
                expected_call_id=str(call.provider_tool_call_id),
                expected_binding_contract_digest=str(call.authorization_digest),
                expected_descriptor_digest=str(call.descriptor_digest),
            )
        except CapabilityResultCodecError as exc:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "mark_succeeded result Artifact failed codec validation",
                call=call,
            ) from exc
        if result.capability_result.status != "completed":
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "mark_succeeded result is not completed",
                call=call,
            )
        return result

    @staticmethod
    def _validate_normalized_result(
        *, call: AssistantCapabilityCall, artifact: AssistantRunArtifact
    ) -> None:
        CapabilityReconciliationService._decode_normalized_result(
            call=call, artifact=artifact
        )

    def _validate_retry_claim(
        self,
        *,
        call: AssistantCapabilityCall,
        latest_attempt: AssistantCapabilityCallAttempt | None,
        claim: dict[str, Any] | None,
        now: datetime,
    ) -> None:
        provider_contract = str(claim.get("providerContract") or "").strip() if claim else ""
        if claim is None or not provider_contract or len(provider_contract) > 256:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "retry_same_key requires signed provider-contract authorization",
                call=call,
            )
        if latest_attempt is None or claim.get("requestDigest") != latest_attempt.request_digest:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "retry_same_key signed request does not match latest Attempt",
                call=call,
            )
        try:
            maximum = int(claim["maxAttempts"])
            remaining = int(claim["remainingAttempts"])
            deadline = datetime.fromisoformat(
                str(claim["deadlineAt"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "retry_same_key signed retry bounds are invalid",
                call=call,
            ) from exc
        if (
            maximum <= int(call.attempt_count)
            or remaining != maximum - int(call.attempt_count)
            or deadline.tzinfo is None
            or deadline <= now
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "retry_same_key signed retry budget is exhausted or expired",
                call=call,
            )
        prior_rows = (
            self.db.query(AssistantCapabilityReconciliation)
            .filter(
                AssistantCapabilityReconciliation.call_id == call.id,
                AssistantCapabilityReconciliation.decision == "retry_same_key",
            )
            .order_by(AssistantCapabilityReconciliation.revision.desc())
            .all()
        )
        frozen_maximum = None
        frozen_deadline = None
        for row in prior_rows:
            claims = (row.authorization_evidence or {}).get("verifiedClaims") or []
            for previous in claims:
                if previous.get("evidenceType") not in {
                    "retry_authorization",
                    "external_status_lookup",
                }:
                    continue
                try:
                    candidate_maximum = int(previous["maxAttempts"])
                    candidate_deadline = datetime.fromisoformat(
                        str(previous["deadlineAt"]).replace("Z", "+00:00")
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                frozen_maximum = (
                    candidate_maximum
                    if frozen_maximum is None
                    else min(frozen_maximum, candidate_maximum)
                )
                frozen_deadline = (
                    candidate_deadline
                    if frozen_deadline is None
                    else min(frozen_deadline, candidate_deadline)
                )
        if frozen_maximum is not None and maximum > frozen_maximum:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "retry_same_key cannot raise the frozen maxAttempts cap",
                call=call,
            )
        if frozen_deadline is not None and deadline > frozen_deadline:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "retry_same_key cannot extend the frozen deadline",
                call=call,
            )


class ReconciliationEvidenceIssuer:
    """Server-owned issuer for the two operator-safe evidence workflows.

    Provider lookup/retry evidence is intentionally not accepted as free-form
    input here. Such evidence must come from an injected collector and a
    diagnostic Artifact already attached to the latest Attempt.
    """

    def __init__(
        self,
        db: Session,
        *,
        signer: HmacReconciliationEvidenceVerifier,
        operator_authorizer: OperatorAuthorizer | None = None,
    ) -> None:
        self.db = db
        self.signer = signer
        self.operator_authorizer = operator_authorizer
        self.calls = CapabilityCallRepository(db)

    def issue_success_attestation(
        self,
        *,
        call_id: UUID,
        result_artifact_id: UUID,
        expected_call_revision: int | None = None,
        expected_run_revision: int | None = None,
        now: datetime | None = None,
    ) -> AssistantRunArtifact:
        ts = now or utcnow()
        run, call, attempt = self._locked_call_attempt(call_id)
        if (
            expected_call_revision is not None
            and int(call.state_revision) != int(expected_call_revision)
        ):
            raise CapabilityCallConflict(
                CODE_STALE_CALL_REVISION,
                "success evidence issuance call revision is stale",
                call=call,
                run=run,
            )
        if (
            expected_run_revision is not None
            and int(run.state_revision) != int(expected_run_revision)
        ):
            raise CapabilityCallConflict(
                CODE_STALE_RUN_REVISION,
                "success evidence issuance Run revision is stale",
                call=call,
                run=run,
            )
        artifact = (
            self.db.query(AssistantRunArtifact)
            .filter(AssistantRunArtifact.id == result_artifact_id)
            .with_for_update()
            .one_or_none()
        )
        if artifact is None or artifact.run_id != run.id:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "success attestation result Artifact is missing or cross-Run",
                call=call,
                run=run,
            )
        CapabilityReconciliationService._decode_normalized_result(
            call=call, artifact=artifact
        )
        if (
            attempt is None
            or str(attempt.status) not in {"response_received", "committed"}
            or not attempt.response_digest
            or not hmac.compare_digest(
                str(attempt.response_digest), str(artifact.content_sha256)
            )
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "success attestation requires captured matching Attempt response",
                call=call,
                run=run,
            )
        claims = {
            **self._base_claims(call, attempt, "mark_succeeded", ts),
            "evidenceType": "capability_call_success_attestation",
            "resultArtifactDigest": str(artifact.content_sha256),
        }
        return self._persist(call=call, claims=claims, now=ts)

    def issue_failure_acceptance(
        self,
        *,
        call_id: UUID,
        reason: str,
        expected_call_revision: int | None = None,
        expected_run_revision: int | None = None,
        now: datetime | None = None,
    ) -> AssistantRunArtifact:
        ts = now or utcnow()
        cleaned = (reason or "").strip()
        if not cleaned:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "product failure acceptance reason is required",
            )
        run, call, attempt = self._locked_call_attempt(call_id)
        if (
            expected_call_revision is not None
            and int(call.state_revision) != int(expected_call_revision)
        ):
            raise CapabilityCallConflict(
                CODE_STALE_CALL_REVISION,
                "failure evidence issuance call revision is stale",
                call=call,
                run=run,
            )
        if (
            expected_run_revision is not None
            and int(run.state_revision) != int(expected_run_revision)
        ):
            raise CapabilityCallConflict(
                CODE_STALE_RUN_REVISION,
                "failure evidence issuance Run revision is stale",
                call=call,
                run=run,
            )
        if self.operator_authorizer is None:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "failure acceptance requires a trusted operator boundary",
                call=call,
                run=run,
            )
        actor = self.operator_authorizer(
            ReconciliationDecisionRequest(
                call_id=call.id,
                expected_call_revision=int(call.state_revision),
                expected_run_revision=int(run.state_revision),
                decision="mark_failed",
                reason=cleaned,
                evidence_artifact_ids=(),
                resolution_request_id=uuid4(),
            )
        )
        if actor is None:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "failure acceptance was denied by the trusted operator boundary",
                call=call,
                run=run,
            )
        claims = {
            **self._base_claims(call, attempt, "mark_failed", ts),
            "evidenceType": "capability_call_failure",
            "failureDisposition": "explicit_product_acceptance_unresolved",
            "acceptedBy": str(actor.actor_admin_id),
            "authorizationMethod": actor.authorization_method,
            "acceptanceReasonDigest": sha256_bytes(cleaned.encode("utf-8")),
        }
        return self._persist(call=call, claims=claims, now=ts)

    def issue_collected_retry_evidence(
        self,
        *,
        call_id: UUID,
        diagnostic_artifact_id: UUID,
        trusted_collector: Callable[
            [AssistantCapabilityCall, AssistantCapabilityCallAttempt, AssistantRunArtifact],
            dict[str, Any],
        ],
        now: datetime | None = None,
    ) -> AssistantRunArtifact:
        """Issue retry evidence only from an injected trusted collector result."""
        ts = now or utcnow()
        run, call, attempt = self._locked_call_attempt(call_id)
        if attempt is None or attempt.diagnostic_artifact_id != diagnostic_artifact_id:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "retry collector Artifact is not attached to the latest Attempt",
                call=call,
                run=run,
            )
        diagnostic = (
            self.db.query(AssistantRunArtifact)
            .filter(AssistantRunArtifact.id == diagnostic_artifact_id)
            .with_for_update()
            .one_or_none()
        )
        if diagnostic is None or diagnostic.run_id != run.id:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "retry collector diagnostic Artifact is missing or cross-Run",
                call=call,
                run=run,
            )
        if (
            str(diagnostic.storage_kind) != "inline"
            or diagnostic.inline_bytes is None
            or int(diagnostic.byte_size) != len(diagnostic.inline_bytes)
            or not hmac.compare_digest(
                str(diagnostic.content_sha256),
                sha256_bytes(bytes(diagnostic.inline_bytes)),
            )
        ):
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "retry collector diagnostic Artifact failed inline integrity validation",
                call=call,
                run=run,
            )
        collected = trusted_collector(call, attempt, diagnostic)
        evidence_type = str(collected.get("evidenceType") or "")
        if evidence_type not in {"retry_authorization", "external_status_lookup"}:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "trusted collector returned an unsupported retry evidence type",
                call=call,
                run=run,
            )
        claims = {
            **collected,
            **self._base_claims(call, attempt, "retry_same_key", ts),
            "evidenceType": evidence_type,
            "diagnosticArtifactId": str(diagnostic.id),
            "diagnosticArtifactDigest": str(diagnostic.content_sha256),
        }
        claims = CapabilityReconciliationService._sanitize_verified_claims(claims)
        CapabilityReconciliationService(self.db)._validate_retry_claim(
            call=call,
            latest_attempt=attempt,
            claim=claims,
            now=ts,
        )
        return self._persist(call=call, claims=claims, now=ts)

    def _locked_call_attempt(
        self, call_id: UUID
    ) -> tuple[Any, AssistantCapabilityCall, AssistantCapabilityCallAttempt | None]:
        probe = self.calls.get_call(call_id)
        if probe is None:
            raise CapabilityCallConflict(CODE_CALL_NOT_FOUND, f"call {call_id} not found")
        run = self.calls.get_run(probe.run_id, for_update=True)
        call = self.calls.get_call(call_id, for_update=True)
        if call is None or str(call.status) not in {"unknown", "needs_reconciliation"}:
            raise CapabilityCallConflict(
                CODE_INVALID_TRANSITION,
                "evidence issuance requires a reconcilable Call",
                call=call,
                run=run,
            )
        attempt = (
            self.db.query(AssistantCapabilityCallAttempt)
            .filter(AssistantCapabilityCallAttempt.call_id == call.id)
            .order_by(AssistantCapabilityCallAttempt.attempt_number.desc())
            .with_for_update()
            .first()
        )
        return run, call, attempt

    def _base_claims(
        self,
        call: AssistantCapabilityCall,
        attempt: AssistantCapabilityCallAttempt | None,
        decision: ReconciliationDecision,
        now: datetime,
    ) -> dict[str, Any]:
        claims = {
            "callId": str(call.id),
            "runId": str(call.run_id),
            "decision": decision,
            "inputDigest": str(call.input_digest),
            "idempotencyKeyDigest": sha256_bytes(
                str(call.idempotency_key).encode("utf-8")
            ),
            "attempt": CapabilityReconciliationService._attempt_claim(attempt),
            "issuedAt": now.astimezone(timezone.utc).isoformat(),
        }
        claims["entryObservation"] = self._local_entry_observation(call)
        return claims

    def _local_entry_observation(
        self, call: AssistantCapabilityCall
    ) -> dict[str, Any]:
        """Read the durable local Entry truth while issuing a claim."""
        if str(call.execution_mode) != "local_transactional":
            return {"kind": "not_applicable", "entryId": None}
        from app.entry.models import Entry

        entry = (
            self.db.query(Entry)
            .filter(Entry.source_capability_call_id == call.id)
            .with_for_update()
            .one_or_none()
        )
        return {
            "kind": "present" if entry is not None else "proven_absent",
            "entryId": str(entry.id) if entry is not None else None,
        }

    def _persist(
        self,
        *,
        call: AssistantCapabilityCall,
        claims: dict[str, Any],
        now: datetime,
    ) -> AssistantRunArtifact:
        payload = self.signer.sign_claims(claims)
        artifact = AssistantRunArtifact(
            run_id=call.run_id,
            kind="capability_call_evidence",
            media_type="application/json",
            storage_kind="inline",
            byte_size=len(payload),
            content_sha256=sha256_bytes(payload),
            inline_bytes=payload,
            metadata_json={
                "contractVersion": 1,
                "callId": str(call.id),
                "evidenceType": str(claims["evidenceType"]),
                "serverIssued": True,
            },
            created_at=now,
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact


@dataclass
class ScriptedExternalOutcome:
    """One scripted transport outcome for external uncertainty tests."""

    kind: Literal[
        "before_send_refusal",
        "accepted_then_timeout",
        "ambiguous_5xx",
        "key_echo_success",
        "status_lookup",
        "duplicate_key",
        "non_retriable_uncertain",
    ]
    status_code: int | None = None
    body: dict[str, Any] | None = None
    echo_key: str | None = None
    accepted: bool | None = None


class ScriptedExternalAdapter:
    """Network-free scripted external adapter for uncertainty matrix tests."""

    def __init__(self, outcomes: list[ScriptedExternalOutcome] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[dict[str, Any]] = []
        self._i = 0

    def send(self, *, idempotency_key: str, payload: dict[str, Any]) -> ScriptedExternalOutcome:
        if self._i >= len(self.outcomes):
            raise RuntimeError("scripted adapter exhausted")
        outcome = self.outcomes[self._i]
        self._i += 1
        self.calls.append(
            {"key": idempotency_key, "payload": payload, "outcome": outcome.kind}
        )
        return outcome

    def classify_for_ledger(
        self, outcome: ScriptedExternalOutcome
    ) -> Literal["succeeded", "failed", "unknown"]:
        if outcome.kind in {"key_echo_success", "duplicate_key"}:
            return "succeeded"
        if outcome.kind in {"before_send_refusal"}:
            return "failed"
        return "unknown"


__all__ = [
    "AuthorizedReconciliationActor",
    "CapabilityReconciliationService",
    "HmacReconciliationEvidenceVerifier",
    "MODES_FORBIDDING_RETRY",
    "ReconciliationDecisionRequest",
    "ReconciliationEvidenceIssuer",
    "ReconciliationResult",
    "ScriptedExternalAdapter",
    "ScriptedExternalOutcome",
    "validate_retry_authorization_for_dispatch",
]

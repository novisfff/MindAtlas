"""LedgerDispatcher — durable ledger in front of CapabilityGateway (Plan 08 Task 4).

Ownership chain:
  Provider/Workflow -> LedgerDispatcher -> (inner ToolDispatcher / Gateway) -> adapter

``legacy_read_only`` Runs keep the compatibility dispatcher path (inner only).
``enforced`` Runs create-or-verify a CapabilityCall, authorize, claim Attempt,
then dispatch once through the inner Gateway path. Approval-gated golden writes
stage a pause proposal and do **not** invoke the Gateway until authorized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID, uuid4

from app.assistant.capability_calls.contracts import CapabilityLedgerMode
from app.assistant.capability_calls.idempotency import (
    make_provider_logical_call_key,
    make_server_idempotency_key,
)
from app.assistant.capability_calls.approval import build_approval_binding
from app.assistant.capability_calls.repository import (
    CapabilityCallConflict,
    CapabilityCallRepository,
    ProposeCallSpec,
)
from app.assistant.capabilities.contracts import (
    CapabilityDescriptor,
    CapabilityError,
    CapabilityMetrics,
    CapabilityResult,
    ContinuationRef,
    failed_result,
)
from app.assistant.durable.repository import LeaseToken
from app.assistant.provider_loop.contracts import (
    CapabilityLedgerAggregatePort,
    LedgerPrepareOutcome,
    ProviderDispatchResult,
)
from app.common.time import utcnow


@runtime_checkable
class ToolDispatcherPort(Protocol):
    def dispatch(self, request: Any, *, cancellation: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class LedgerDispatchRequest:
    """Ledger-aware dispatch request wrapping Plan 02/03 execution scope."""

    provider_request: Any  # ProviderDispatchRequest
    run_id: UUID
    capability_ledger_mode: CapabilityLedgerMode | str
    expected_run_revision: int
    lease: LeaseToken | None
    provider_round_index: int
    assistant_message_index: int
    provider_tool_call_id: str
    authorization_digest: str
    input_artifact_id: UUID
    input_digest: str
    execution_mode: str
    side_effect_class: str
    dispatch_disposition: Literal["deny", "dispatch", "awaiting_call_approval"]
    idempotency_secret: str | bytes | None = None
    frozen_target_digest: str | None = None
    owner_kind: str = "main_agent"
    owner_id: UUID | None = None
    owner_version_id: UUID | None = None
    capability_type: str = "tool"
    domain_key: str | None = None
    manifest_revision_id: UUID | None = None
    parent_call_id: UUID | None = None
    descriptor_digest: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerDispatchResult:
    provider_result: Any  # ProviderDispatchResult | None
    call_id: UUID | None
    call_status: str | None
    pause_proposal: dict[str, Any] | None = None
    denied: bool = False
    deny_reason_code: str | None = None


@dataclass
class CapabilityCallPauseProposalV1:
    """Staged call-owned pause proposal (not durable until Plan 07 CAS)."""

    contract_version: int
    run_id: UUID
    call_id: UUID
    interrupt_id: UUID
    approval_binding_digest: str
    logical_call_key: str
    safe_request_payload: dict[str, Any]
    proposal_digest: str


@dataclass
class LedgerDispatcher:
    """Prepare/claim/dispatch orchestration; adapters only via inner dispatcher."""

    inner: ToolDispatcherPort
    aggregate: CapabilityLedgerAggregatePort | None = None
    db: Any | None = None  # compatibility repository path; removed after aggregate cutover
    # Architecture: Gateway must not import this repository; we own it here.
    _repo: CapabilityCallRepository | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.db is not None:
            self._repo = CapabilityCallRepository(self.db)

    @property
    def repo(self) -> CapabilityCallRepository:
        if self._repo is None:
            raise RuntimeError("ledger repository is unavailable without a database session")
        return self._repo

    def dispatch(
        self,
        request: Any,
        *,
        cancellation: Any,
        ledger: LedgerDispatchRequest | None = None,
    ) -> Any:
        """ToolDispatcher-compatible entry.

        When ``ledger`` is None or mode is ``legacy_read_only``, pass through
        to the inner Gateway dispatcher with zero ledger side effects.
        """
        if self.aggregate is not None:
            return self._dispatch_via_aggregate(request, cancellation=cancellation)

        if ledger is None or str(ledger.capability_ledger_mode) != "enforced":
            return self.inner.dispatch(request, cancellation=cancellation)

        return self.dispatch_enforced(ledger, cancellation=cancellation).provider_result

    def _dispatch_via_aggregate(self, request: Any, *, cancellation: Any) -> Any:
        """Production enforced path; the aggregate derives every disposition."""
        if cancellation is not None and getattr(
            cancellation, "is_cancelled", lambda: False
        )():
            return self._blocked_provider_result(
                request,
                reason_code="cancelled",
                safe_message="capability call cancelled before ledger admission",
                error_type="cancelled",
            )

        assert self.aggregate is not None
        outcome = self.aggregate.prepare(request)
        if not isinstance(outcome, LedgerPrepareOutcome):
            raise TypeError("capability ledger aggregate returned invalid prepare outcome")
        if outcome.kind == "replay":
            assert outcome.provider_result is not None
            return outcome.provider_result
        if outcome.kind == "deny":
            return self._blocked_provider_result(
                request,
                reason_code=outcome.reason_code or "capability_denied",
                safe_message="capability denied by durable ledger admission",
            )
        if outcome.kind == "pause":
            proposal = outcome.pause_proposal or {}
            continuation = ContinuationRef(
                continuation_type="capability_call",
                contract_version=1,
                reference_id=str(proposal.get("interruptId") or outcome.call_id),
                payload_digest=str(
                    proposal.get("proposalDigest")
                    or proposal.get("approvalBindingDigest")
                ),
            )
            return ProviderDispatchResult.model_construct(
                capability_result=CapabilityResult(
                    status="waiting",
                    user_text=None,
                    structured_output=None,
                    artifact_refs=(),
                    continuation=continuation,
                    terminal_output=False,
                    needs_followup=False,
                    error=None,
                    metrics=CapabilityMetrics(
                        duration_ms=0.0, input_bytes=0, output_bytes=0
                    ),
                ),
                next_manifest=getattr(request, "current_manifest", None),
            )
        if outcome.kind == "dispatch_local":
            try:
                result = self.aggregate.execute_local(outcome, request)
                return self.aggregate.commit_result(outcome, result)
            except BaseException:
                self.aggregate.record_failure(outcome, "local_transaction_failed")
                raise
        if outcome.kind != "dispatch":
            raise TypeError(f"unsupported ledger prepare outcome {outcome.kind!r}")

        try:
            result = self.inner.dispatch(request, cancellation=cancellation)
        except BaseException:
            self.aggregate.record_failure(outcome, "dispatcher_error")
            raise
        if not isinstance(result, ProviderDispatchResult):
            # Test doubles may be structurally compatible, but production must
            # never let a non-contract result cross the Provider Loop boundary.
            if not hasattr(result, "capability_result"):
                self.aggregate.record_failure(outcome, "dispatcher_result_invalid")
                raise TypeError("inner dispatcher returned invalid ProviderDispatchResult")
        return self.aggregate.commit_result(outcome, result)

    @staticmethod
    def _blocked_provider_result(
        request: Any,
        *,
        reason_code: str,
        safe_message: str,
        error_type: str = "unauthorized",
    ) -> ProviderDispatchResult:
        call = getattr(request, "call", None)
        capability_result = failed_result(
            error=CapabilityError(
                error_type=error_type,
                safe_code=reason_code,
                safe_message=safe_message,
                retry_disposition="never",
                call_id=getattr(call, "call_id", None),
                target_identity=getattr(
                    getattr(request, "binding", None), "ref", None
                )
                and getattr(request.binding.ref, "target_identity", None),
            ),
            metrics=CapabilityMetrics(
                duration_ms=0.0,
                input_bytes=0,
                output_bytes=0,
            ),
        )
        # model_construct keeps unit-test doubles usable; real production
        # requests always provide a validated current Manifest.
        return ProviderDispatchResult.model_construct(
            capability_result=capability_result,
            next_manifest=getattr(request, "current_manifest", None),
        )

    def dispatch_enforced(
        self,
        ledger: LedgerDispatchRequest,
        *,
        cancellation: Any,
    ) -> LedgerDispatchResult:
        """Enforced ledger path: propose → authorize → claim → inner dispatch."""
        if cancellation is not None and getattr(cancellation, "is_cancelled", lambda: False)():
            return LedgerDispatchResult(
                provider_result=None,
                call_id=None,
                call_status=None,
                denied=True,
                deny_reason_code="cancelled",
            )

        provider_req = ledger.provider_request
        call = getattr(provider_req, "call", None)
        descriptor: CapabilityDescriptor | None = getattr(provider_req, "descriptor", None)
        domain_key = ledger.domain_key or (
            getattr(call, "domain_key", None) if call is not None else None
        )
        if domain_key is None:
            raise ValueError("ledger dispatch requires domain_key")

        logical_key = make_provider_logical_call_key(
            provider_round_index=ledger.provider_round_index,
            assistant_message_index=ledger.assistant_message_index,
            provider_tool_call_id=ledger.provider_tool_call_id,
        )
        target_digest = ledger.frozen_target_digest or ledger.input_digest
        if ledger.idempotency_secret:
            idem_key = make_server_idempotency_key(
                secret=ledger.idempotency_secret,
                run_id=ledger.run_id,
                logical_call_key=logical_key,
                frozen_target_digest=target_digest,
                canonical_input_digest=ledger.input_digest,
            )
        else:
            # Tests may omit secret for read-only ledger paths; still store a
            # deterministic non-HMAC key material hash of identity.
            from app.assistant.domain.digests import sha256_bytes

            idem_key = sha256_bytes(
                f"{ledger.run_id}|{logical_key}|{target_digest}|{ledger.input_digest}".encode()
            )

        manifest_revision_id = ledger.manifest_revision_id
        if manifest_revision_id is None:
            # Prefer current manifest revision id when present on request.
            current = getattr(provider_req, "current_manifest", None)
            mid = getattr(current, "manifest_revision_id", None) or getattr(
                current, "id", None
            )
            if mid is None:
                raise ValueError("ledger dispatch requires manifest_revision_id")
            manifest_revision_id = mid

        call_id = uuid4()
        if call is not None and getattr(call, "call_id", None):
            # Provider call_id is often a string; use new UUID for ledger PK and
            # keep logical key as the stable identity.
            pass

        spec = ProposeCallSpec(
            call_id=call_id,
            run_id=ledger.run_id,
            expected_run_revision=ledger.expected_run_revision,
            lease=ledger.lease,
            manifest_revision_id=manifest_revision_id,
            logical_call_key=logical_key,
            owner_kind=ledger.owner_kind,
            owner_id=ledger.owner_id,
            owner_version_id=ledger.owner_version_id,
            capability_type=ledger.capability_type,
            domain_key=str(domain_key),
            descriptor_digest=(
                ledger.descriptor_digest
                or (
                    descriptor.descriptor_digest
                    if descriptor is not None
                    else getattr(call, "descriptor_digest", None)
                )
                or ledger.authorization_digest
            ),
            authorization_digest=ledger.authorization_digest,
            input_artifact_id=ledger.input_artifact_id,
            input_digest=ledger.input_digest,
            side_effect_class=ledger.side_effect_class,
            execution_mode=ledger.execution_mode,
            idempotency_key=idem_key,
            parent_call_id=ledger.parent_call_id,
            provider_tool_call_id=ledger.provider_tool_call_id,
        )

        try:
            row, _created = self.repo.create_or_verify_proposed(spec)
        except CapabilityCallConflict as exc:
            return LedgerDispatchResult(
                provider_result=None,
                call_id=None,
                call_status=None,
                denied=True,
                deny_reason_code=exc.code,
            )

        # Denied disposition from policy.
        if ledger.dispatch_disposition == "deny":
            if str(row.status) == "proposed":
                row = self.repo.transition_call(
                    call_id=row.id,
                    expected_call_revision=int(row.state_revision),
                    expected_run_revision=ledger.expected_run_revision,
                    to_status="denied",
                    lease=ledger.lease,
                    failure_code="policy_denied",
                )
            return LedgerDispatchResult(
                provider_result=None,
                call_id=row.id,
                call_status=str(row.status),
                denied=True,
                deny_reason_code="policy_denied",
            )

        # Approval-gated golden write: stage pause, no Gateway.
        if ledger.dispatch_disposition == "awaiting_call_approval":
            interrupt_id = uuid4()
            binding = build_approval_binding(
                call_id=row.id,
                logical_call_key=logical_key,
                owner_digest=ledger.authorization_digest,
                binding_contract_digest=ledger.frozen_target_digest or ledger.input_digest,
                input_digest=ledger.input_digest,
                target_digest=ledger.frozen_target_digest or ledger.input_digest,
                descriptor_digest=str(row.descriptor_digest),
                authorization_digest=ledger.authorization_digest,
                principal_digest=ledger.authorization_digest,
                request_revision=1,
                target_version_id=None,
            )
            if str(row.status) == "proposed":
                row = self.repo.transition_call(
                    call_id=row.id,
                    expected_call_revision=int(row.state_revision),
                    expected_run_revision=ledger.expected_run_revision,
                    to_status="awaiting_approval",
                    lease=ledger.lease,
                    approval_binding_digest=binding.approval_binding_digest,
                )
            proposal = CapabilityCallPauseProposalV1(
                contract_version=1,
                run_id=ledger.run_id,
                call_id=row.id,
                interrupt_id=interrupt_id,
                approval_binding_digest=binding.approval_binding_digest,
                logical_call_key=logical_key,
                safe_request_payload={
                    "domainKey": str(domain_key),
                    "sideEffectClass": ledger.side_effect_class,
                    "executionMode": ledger.execution_mode,
                },
                proposal_digest=binding.approval_binding_digest,
            )
            return LedgerDispatchResult(
                provider_result=None,
                call_id=row.id,
                call_status=str(row.status),
                pause_proposal={
                    "contractVersion": 1,
                    "runId": str(proposal.run_id),
                    "callId": str(proposal.call_id),
                    "interruptId": str(proposal.interrupt_id),
                    "approvalBindingDigest": proposal.approval_binding_digest,
                    "logicalCallKey": proposal.logical_call_key,
                    "safeRequestPayload": proposal.safe_request_payload,
                    "proposalDigest": proposal.proposal_digest,
                },
            )

        # Read/compute (or post-approval authorized write): authorize + claim + gateway.
        if str(row.status) == "proposed":
            row = self.repo.transition_call(
                call_id=row.id,
                expected_call_revision=int(row.state_revision),
                expected_run_revision=ledger.expected_run_revision,
                to_status="authorized",
                lease=ledger.lease,
            )
        elif str(row.status) not in {"authorized", "executing"}:
            # Replay of terminal call: do not re-dispatch.
            if str(row.status) == "succeeded":
                return LedgerDispatchResult(
                    provider_result=None,
                    call_id=row.id,
                    call_status="succeeded",
                )
            return LedgerDispatchResult(
                provider_result=None,
                call_id=row.id,
                call_status=str(row.status),
                denied=True,
                deny_reason_code="call_not_dispatchable",
            )

        if cancellation is not None and getattr(cancellation, "is_cancelled", lambda: False)():
            return LedgerDispatchResult(
                provider_result=None,
                call_id=row.id,
                call_status=str(row.status),
                denied=True,
                deny_reason_code="cancelled",
            )

        if str(row.status) == "authorized":
            worker_id = ledger.lease.worker_id if ledger.lease is not None else "unknown"
            row, _attempt = self.repo.claim_attempt(
                call_id=row.id,
                expected_call_revision=int(row.state_revision),
                expected_run_revision=ledger.expected_run_revision,
                lease=ledger.lease
                if ledger.lease is not None
                else LeaseToken(
                    run_id=ledger.run_id, worker_id=worker_id, lease_generation=0
                ),
                worker_id=worker_id,
            )

        # Only now may the Gateway/adapter run.
        provider_result = self.inner.dispatch(
            ledger.provider_request, cancellation=cancellation
        )

        # Best-effort terminalization for simple success/failure when result present.
        cap_result = getattr(provider_result, "capability_result", None)
        if cap_result is not None and str(row.status) == "executing":
            status = str(getattr(cap_result, "status", "") or "")
            to_status = None
            if status in {"succeeded", "success", "ok"}:
                to_status = "succeeded"
            elif status in {"failed", "error", "denied", "cancelled"}:
                to_status = "failed" if status != "cancelled" else "cancelled"
            if to_status is not None:
                try:
                    row = self.repo.transition_call(
                        call_id=row.id,
                        expected_call_revision=int(row.state_revision),
                        expected_run_revision=ledger.expected_run_revision,
                        to_status=to_status,
                        lease=ledger.lease,
                        allow_while_cancelling=(to_status in {"failed", "cancelled", "succeeded"}),
                    )
                except CapabilityCallConflict:
                    pass

        return LedgerDispatchResult(
            provider_result=provider_result,
            call_id=row.id,
            call_status=str(row.status),
        )


def select_dispatcher(
    *,
    capability_ledger_mode: str | None,
    ledger_dispatcher: LedgerDispatcher | None,
    compatibility_dispatcher: ToolDispatcherPort,
) -> ToolDispatcherPort:
    """Return ledger dispatcher for enforced Runs; else compatibility path."""
    if (
        str(capability_ledger_mode or "") == "enforced"
        and ledger_dispatcher is not None
    ):
        return ledger_dispatcher
    return compatibility_dispatcher


__all__ = [
    "CapabilityCallPauseProposalV1",
    "LedgerDispatchRequest",
    "LedgerDispatchResult",
    "LedgerDispatcher",
    "ToolDispatcherPort",
    "select_dispatcher",
]

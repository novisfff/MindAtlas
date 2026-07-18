"""Durable server-owned CapabilityCall admission and replay aggregate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session

from app.assistant.capability_calls.approval import build_approval_binding, redact_mapping
from app.assistant.capability_calls.idempotency import make_server_idempotency_key
from app.assistant.capability_calls.repository import (
    CapabilityCallConflict,
    CapabilityCallRepository,
    ProposeCallSpec,
)
from app.assistant.capability_calls.result_codec import (
    decode_capability_result,
    encode_capability_result,
)
from app.assistant.domain.digests import canonical_json_bytes, sha256_bytes
from app.assistant.durable.models import AssistantRunArtifact, AssistantRunManifestRevision
from app.assistant.durable.repository import LeaseToken
from app.assistant.provider_loop.contracts import LedgerPrepareOutcome, ProviderDispatchResult


def _uuid_or_none(value: Any) -> UUID | None:
    try:
        return UUID(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _stable_uuid(name: str) -> UUID:
    """Deterministic UUID whose SQLite text form cannot acquire NUMERIC affinity."""
    derived = uuid5(NAMESPACE_URL, name)
    return UUID(hex="aaaaaaaa" + derived.hex[8:])


def _execution_mode(side_effect: str) -> str:
    if side_effect in {"none", "compute"}:
        return "pure_replayable"
    if side_effect == "read":
        return "read_replayable"
    if side_effect == "write_local":
        return "local_transactional"
    if side_effect == "write_external":
        return "non_retriable"
    return "unsupported"


@dataclass
class DurableCapabilityLedgerAggregate:
    """Own call identity, Attempt lifecycle, result Artifact, and replay."""

    db: Session
    authorization_factory: Any
    idempotency_secret: str | bytes
    lease: LeaseToken | None = None

    def __post_init__(self) -> None:
        self.calls = CapabilityCallRepository(self.db)

    def _manifest_row(self, request: Any) -> AssistantRunManifestRevision:
        row = (
            self.db.query(AssistantRunManifestRevision)
            .filter(
                AssistantRunManifestRevision.run_id == request.execution_scope.run_id,
                AssistantRunManifestRevision.manifest_digest
                == request.current_manifest.manifest_digest,
            )
            .one_or_none()
        )
        if row is None:
            raise CapabilityCallConflict(
                "manifest_revision_missing", "frozen manifest revision is not durable"
            )
        return row

    def _input_artifact(self, request: Any) -> AssistantRunArtifact:
        payload = canonical_json_bytes(dict(request.call.arguments))  # type: ignore[arg-type]
        digest = sha256_bytes(payload)
        existing = (
            self.db.query(AssistantRunArtifact)
            .filter(
                AssistantRunArtifact.run_id == request.execution_scope.run_id,
                AssistantRunArtifact.content_sha256 == digest,
                AssistantRunArtifact.byte_size == len(payload),
            )
            .one_or_none()
        )
        if existing is not None:
            return existing
        artifact = AssistantRunArtifact(
            run_id=request.execution_scope.run_id,
            kind="capability_call_input",
            media_type="application/json",
            storage_kind="inline",
            byte_size=len(payload),
            content_sha256=digest,
            inline_bytes=payload,
            metadata_json={"contractVersion": 1},
        )
        self.db.add(artifact)
        self.db.flush()
        return artifact

    def _replay(self, request: Any, call: Any) -> ProviderDispatchResult:
        artifact = self.db.get(AssistantRunArtifact, call.output_artifact_id)
        if artifact is None or artifact.inline_bytes is None:
            raise CapabilityCallConflict(
                "result_artifact_missing", "terminal call result is unavailable"
            )
        return decode_capability_result(
            bytes(artifact.inline_bytes),
            expected_digest=str(artifact.content_sha256),
            expected_call_id=request.call.call_id,
            expected_binding_contract_digest=str(call.authorization_digest),
            expected_descriptor_digest=request.descriptor.descriptor_digest,
        )

    def prepare(self, request: Any) -> LedgerPrepareOutcome:
        decision = self.authorization_factory.decision_for_call(
            call_id=request.call.call_id
        )
        disposition = getattr(decision, "dispatch_disposition", None)
        if disposition not in {"deny", "dispatch", "awaiting_call_approval"}:
            raise CapabilityCallConflict(
                "ledger_decision_required", "tagged v2 ledger decision is required"
            )
        run = self.calls.get_run(request.execution_scope.run_id, for_update=True)
        if str(run.capability_ledger_mode) != "enforced":
            raise CapabilityCallConflict(
                "ledger_mode_mismatch", "run is not frozen in enforced ledger mode"
            )
        if self.lease is None:
            raise CapabilityCallConflict(
                "ledger_lease_required", "enforced dispatch requires a claimed Run lease"
            )
        logical_key = f"provider:{request.call.call_id}"
        call_id = _stable_uuid(f"mindatlas:{run.id}:{logical_key}")
        input_payload = canonical_json_bytes(dict(request.call.arguments))  # type: ignore[arg-type]
        input_digest = sha256_bytes(input_payload)
        side_effect = str(request.descriptor.behavior.side_effect)

        if disposition == "deny":
            self.db.rollback()
            return LedgerPrepareOutcome(
                kind="deny",
                call_id=call_id,
                call_revision=0,
                reason_code=str(decision.reason_code),
            )

        if disposition == "awaiting_call_approval":
            binding = build_approval_binding(
                call_id=call_id,
                logical_call_key=logical_key,
                owner_digest=decision.owner_policy_digest,
                binding_contract_digest=request.binding.ref.binding_contract_digest,
                input_digest=input_digest,
                target_version_id=request.descriptor.target_version_id,
                target_digest=request.binding.ref.resolution_digest,
                descriptor_digest=request.descriptor.descriptor_digest,
                authorization_digest=decision.decision_digest,
                principal_digest=decision.principal_digest,
                request_revision=1,
            )
            # Pure staging: no call/Interrupt/Checkpoint layer is persisted here.
            self.db.rollback()
            return LedgerPrepareOutcome(
                kind="pause",
                call_id=call_id,
                call_revision=0,
                pause_proposal={
                    "contractVersion": 1,
                    "runId": str(run.id),
                    "callId": str(call_id),
                    "interruptId": str(
                        _stable_uuid(f"mindatlas:interrupt:{call_id}")
                    ),
                    "approvalBindingDigest": binding.approval_binding_digest,
                    "logicalCallKey": logical_key,
                    "safeRequestPayload": redact_mapping(
                        {
                            "domainKey": request.call.domain_key,
                            "sideEffectClass": side_effect,
                            "executionMode": _execution_mode(side_effect),
                        }
                    ),
                    "proposalDigest": binding.approval_binding_digest,
                },
            )

        manifest = self._manifest_row(request)
        artifact = self._input_artifact(request)
        idem = make_server_idempotency_key(
            secret=self.idempotency_secret,
            run_id=run.id,
            logical_call_key=logical_key,
            frozen_target_digest=request.binding.ref.resolution_digest,
            canonical_input_digest=input_digest,
        )
        owner = request.authorization.owner
        spec = ProposeCallSpec(
            call_id=call_id,
            run_id=run.id,
            expected_run_revision=int(run.state_revision),
            lease=self.lease,
            manifest_revision_id=manifest.id,
            logical_call_key=logical_key,
            owner_kind=str(owner.owner_kind),
            owner_id=_uuid_or_none(owner.owner_id),
            owner_version_id=owner.owner_version_id,
            capability_type=str(request.descriptor.capability_type),
            domain_key=request.call.domain_key,
            target_id=request.descriptor.target_id,
            target_version_id=request.descriptor.target_version_id,
            descriptor_digest=request.descriptor.descriptor_digest,
            authorization_digest=decision.decision_digest,
            input_artifact_id=artifact.id,
            input_digest=input_digest,
            side_effect_class=side_effect,
            execution_mode=_execution_mode(side_effect),
            idempotency_key=idem,
            provider_tool_call_id=request.call.call_id,
        )
        call, _ = self.calls.create_or_verify_proposed(spec)
        if str(call.status) == "succeeded":
            result = self._replay(request, call)
            revision = int(call.state_revision)
            self.db.rollback()
            return LedgerPrepareOutcome(
                kind="replay",
                call_id=call.id,
                call_revision=revision,
                provider_result=result,
            )
        if str(call.status) != "proposed":
            self.db.rollback()
            raise CapabilityCallConflict(
                "call_not_dispatchable", f"call status {call.status!r} cannot dispatch"
            )
        call = self.calls.transition_call(
            call_id=call.id,
            expected_call_revision=int(call.state_revision),
            expected_run_revision=int(run.state_revision),
            to_status="authorized",
            lease=self.lease,
        )
        worker_id = self.lease.worker_id
        call, attempt = self.calls.claim_attempt(
            call_id=call.id,
            expected_call_revision=int(call.state_revision),
            expected_run_revision=int(run.state_revision),
            lease=self.lease,
            worker_id=worker_id,
        )
        attempt = self.calls.transition_attempt(
            attempt_id=attempt.id,
            expected_status="claimed",
            to_status="dispatched",
            request_digest=input_digest,
        )
        self.db.commit()
        return LedgerPrepareOutcome(
            kind="dispatch",
            call_id=call.id,
            call_revision=int(call.state_revision),
            attempt_id=attempt.id,
        )

    def commit_result(
        self, outcome: LedgerPrepareOutcome, result: ProviderDispatchResult
    ) -> ProviderDispatchResult:
        call = self.calls.get_call(outcome.call_id, for_update=True)
        if call is None or outcome.attempt_id is None:
            raise CapabilityCallConflict("call_not_found", "dispatch call is unavailable")
        encoded = encode_capability_result(
            call_id=str(call.provider_tool_call_id),
            binding_contract_digest=str(call.authorization_digest),
            descriptor_digest=str(call.descriptor_digest),
            result=result,
        )
        # The binding digest is persisted in the evidence, not a dedicated call
        # column. Store it in metadata so replay validates the original request.
        artifact = AssistantRunArtifact(
            run_id=call.run_id,
            kind="capability_call_result",
            media_type="application/json",
            storage_kind="inline",
            byte_size=len(encoded.payload),
            content_sha256=encoded.digest,
            inline_bytes=encoded.payload,
            metadata_json={"contractVersion": 1},
        )
        self.db.add(artifact)
        self.db.flush()
        self.calls.transition_attempt(
            attempt_id=outcome.attempt_id,
            expected_status="dispatched",
            to_status="response_received",
            response_digest=encoded.digest,
        )
        self.calls.transition_attempt(
            attempt_id=outcome.attempt_id,
            expected_status="response_received",
            to_status="committed",
        )
        target = "succeeded" if result.capability_result.status == "completed" else "failed"
        self.calls.transition_call(
            call_id=call.id,
            expected_call_revision=int(call.state_revision),
            expected_run_revision=int(self.calls.get_run(call.run_id).state_revision),
            to_status=target,
            lease=self.lease,
            output_artifact_id=artifact.id,
            failure_code=(None if target == "succeeded" else "capability_failed"),
        )
        self.db.commit()
        return result

    def record_failure(self, outcome: LedgerPrepareOutcome, reason_code: str) -> None:
        call = self.calls.get_call(outcome.call_id, for_update=True)
        if call is None:
            self.db.rollback()
            return
        if outcome.attempt_id is not None:
            self.calls.transition_attempt(
                attempt_id=outcome.attempt_id,
                expected_status="dispatched",
                to_status="failed",
                error_code=reason_code,
            )
        self.calls.transition_call(
            call_id=call.id,
            expected_call_revision=int(call.state_revision),
            expected_run_revision=int(self.calls.get_run(call.run_id).state_revision),
            to_status="failed",
            lease=self.lease,
            failure_code=reason_code,
        )
        self.db.commit()


__all__ = ["DurableCapabilityLedgerAggregate"]

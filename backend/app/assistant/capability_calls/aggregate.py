"""Durable server-owned CapabilityCall admission and replay aggregate."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Mapping
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

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
from app.assistant.durable.models import (
    AssistantRunArtifact,
    AssistantRunBudgetRevision,
    AssistantRunCheckpoint,
    AssistantRunInterrupt,
    AssistantRunManifestRevision,
    AssistantRunObligationRevision,
    AssistantRunPolicyRevision,
)
from app.assistant.durable.repository import (
    STATUS_FAILED,
    STATUS_WAITING_APPROVAL,
    DurableChildBundle,
    DurableRunRepository,
    EventSpec,
    LeaseToken,
)
from app.assistant.provider_loop.contracts import (
    LedgerPrepareOutcome,
    ProviderDispatchResult,
)
from app.common.time import utcnow


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
    runtime_snapshot_provider: Callable[[], Mapping[str, Any]] | None = None

    def __post_init__(self) -> None:
        self.calls = CapabilityCallRepository(self.db)
        self._pending_pause: dict[str, Any] | None = None
        self._pending_result: dict[str, Any] | None = None

    def _stage_runtime_snapshot(
        self, run: Any, *, manifest_override: Any | None = None
    ) -> tuple[list[Any], UUID, UUID, UUID, UUID]:
        """Stage changed live runtime revisions for the enclosing semantic CAS."""
        pointer_ids = (
            run.current_manifest_revision_id,
            run.current_policy_revision_id,
            run.current_budget_revision_id,
            run.current_obligation_revision_id,
        )
        if not all(pointer_ids):
            raise CapabilityCallConflict(
                "runtime_snapshot_missing", "Run runtime revision pointers are incomplete"
            )
        if self.runtime_snapshot_provider is None:
            return [], *pointer_ids

        snapshot = self.runtime_snapshot_provider()
        required = {"manifest", "policy", "budget", "obligation"}
        if not isinstance(snapshot, Mapping) or set(snapshot) != required:
            raise CapabilityCallConflict(
                "runtime_snapshot_invalid", "live runtime snapshot is incomplete"
            )
        manifest = manifest_override or snapshot["manifest"]
        policy = snapshot["policy"]
        budget = snapshot["budget"]
        obligation = snapshot["obligation"]
        if getattr(manifest, "run_id", None) != run.id:
            raise CapabilityCallConflict(
                "runtime_snapshot_run_mismatch", "live Manifest belongs to another Run"
            )

        rows: list[Any] = []

        def _next_revision(model: Any) -> int:
            value = (
                self.db.query(model.revision)
                .filter(model.run_id == run.id)
                .order_by(model.revision.desc())
                .limit(1)
                .scalar()
            )
            return int(value or 0) + 1

        current_manifest = self.db.get(
            AssistantRunManifestRevision, run.current_manifest_revision_id
        )
        current_policy = self.db.get(
            AssistantRunPolicyRevision, run.current_policy_revision_id
        )
        current_budget = self.db.get(
            AssistantRunBudgetRevision, run.current_budget_revision_id
        )
        current_obligation = self.db.get(
            AssistantRunObligationRevision, run.current_obligation_revision_id
        )
        if any(
            row is None
            for row in (
                current_manifest,
                current_policy,
                current_budget,
                current_obligation,
            )
        ):
            raise CapabilityCallConflict(
                "runtime_snapshot_missing", "current runtime revision row is missing"
            )

        manifest_id = current_manifest.id
        if manifest.manifest_digest != current_manifest.manifest_digest:
            row = AssistantRunManifestRevision(
                id=uuid4(),
                run_id=run.id,
                revision=_next_revision(AssistantRunManifestRevision),
                parent_revision_id=current_manifest.id,
                parent_digest=current_manifest.manifest_digest,
                manifest_digest=manifest.manifest_digest,
                schema_version=1,
                payload=manifest.model_dump(mode="json", by_alias=True),
            )
            rows.append(row)
            manifest_id = row.id

        policy_id = current_policy.id
        if policy.effective_policy_digest != current_policy.policy_digest:
            row = AssistantRunPolicyRevision(
                id=uuid4(),
                run_id=run.id,
                revision=_next_revision(AssistantRunPolicyRevision),
                parent_revision_id=current_policy.id,
                parent_digest=current_policy.policy_digest,
                policy_digest=policy.effective_policy_digest,
                payload=policy.model_dump(mode="json", by_alias=True),
            )
            rows.append(row)
            policy_id = row.id

        budget_id = current_budget.id
        if budget.ledger_digest != current_budget.budget_digest:
            row = AssistantRunBudgetRevision(
                id=uuid4(),
                run_id=run.id,
                revision=_next_revision(AssistantRunBudgetRevision),
                parent_revision_id=current_budget.id,
                parent_digest=current_budget.budget_digest,
                budget_digest=budget.ledger_digest,
                payload=budget.model_dump(mode="json", by_alias=True),
            )
            rows.append(row)
            budget_id = row.id

        obligation_id = current_obligation.id
        if obligation.ledger_digest != current_obligation.obligation_digest:
            row = AssistantRunObligationRevision(
                id=uuid4(),
                run_id=run.id,
                revision=_next_revision(AssistantRunObligationRevision),
                parent_revision_id=current_obligation.id,
                parent_digest=current_obligation.obligation_digest,
                obligation_digest=obligation.ledger_digest,
                payload=obligation.model_dump(mode="json", by_alias=True),
            )
            rows.append(row)
            obligation_id = row.id

        if rows:
            self.db.add_all(rows)
            self.db.flush()
        return rows, manifest_id, policy_id, budget_id, obligation_id

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

    def _commit_attempt_started(self, *, run: Any, call: Any, attempt: Any) -> None:
        """Persist the live reservation snapshot with the external-I/O fence."""
        from app.assistant.durable.codec import (
            checkpoint_state_digest,
            decode_checkpoint,
            encode_checkpoint_v3,
        )
        from app.assistant.durable.contracts import DurableCapabilityCallStateV1
        from app.assistant.durable.checkpoints import _next_checkpoint_sequence  # noqa: SLF001

        if self.lease is None or run.current_checkpoint_id is None:
            raise CapabilityCallConflict(
                "attempt_start_checkpoint_missing",
                "external dispatch requires a reserved v3 checkpoint",
            )
        current_row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        if current_row is None:
            raise CapabilityCallConflict(
                "attempt_start_checkpoint_missing", "reserved checkpoint is missing"
            )
        current = decode_checkpoint(current_row.state_payload)
        if int(getattr(current, "schema_version", 0)) != 3:
            raise CapabilityCallConflict(
                "attempt_start_checkpoint_invalid",
                "external dispatch requires checkpoint schema v3",
            )
        replaced = False
        states = []
        for state in current.capability_calls:
            if state.call_id == call.id:
                states.append(
                    DurableCapabilityCallStateV1(
                        call_id=state.call_id,
                        logical_call_key=state.logical_call_key,
                        provider_tool_call_id=state.provider_tool_call_id,
                        provider_order=state.provider_order,
                        status="executing",
                        attempt_id=attempt.id,
                        output_artifact_id=state.output_artifact_id,
                        interrupt_id=state.interrupt_id,
                        approval_binding_digest=state.approval_binding_digest,
                        result_message_digest=state.result_message_digest,
                    )
                )
                replaced = True
            else:
                states.append(state)
        if not replaced:
            raise CapabilityCallConflict(
                "attempt_start_call_missing",
                "reserved checkpoint does not contain the dispatch call",
            )
        (
            runtime_rows,
            manifest_revision_id,
            policy_revision_id,
            budget_revision_id,
            obligation_revision_id,
        ) = self._stage_runtime_snapshot(run)
        expected_revision = int(run.state_revision)
        checkpoint = current.model_copy(
            update={
                "manifest_revision_id": manifest_revision_id,
                "policy_revision_id": policy_revision_id,
                "budget_revision_id": budget_revision_id,
                "obligation_revision_id": obligation_revision_id,
                "capability_calls": tuple(states),
            }
        )
        checkpoint_id = uuid4()
        checkpoint_row = AssistantRunCheckpoint(
            id=checkpoint_id,
            run_id=run.id,
            sequence=_next_checkpoint_sequence(self.db, run.id),
            expected_state_revision=expected_revision,
            committed_state_revision=expected_revision + 1,
            schema_version=3,
            manifest_revision_id=manifest_revision_id,
            policy_revision_id=policy_revision_id,
            budget_revision_id=budget_revision_id,
            obligation_revision_id=obligation_revision_id,
            provider_message_ordinal=current.provider_message_ordinal,
            provider_transcript_digest=current.provider_transcript_digest,
            phase=current.phase,
            logical_unit_id=str(call.logical_call_key),
            reason="capability_attempt_started",
            state_payload=encode_checkpoint_v3(checkpoint),
            state_digest=checkpoint_state_digest(checkpoint),
        )
        DurableRunRepository(self.db).commit_semantic(
            run_id=run.id,
            expected_revision=expected_revision,
            lease=self.lease,
            events=(
                EventSpec(
                    event_key=f"capability_call.started:{call.id}:rev{expected_revision}",
                    event_name="capability_call.started",
                    payload={"callId": str(call.id), "attemptId": str(attempt.id)},
                    visibility="internal",
                ),
            ),
            children=DurableChildBundle(
                rows=[*runtime_rows, checkpoint_row],
                current_checkpoint_id=checkpoint_id,
                current_manifest_revision_id=manifest_revision_id,
                current_policy_revision_id=policy_revision_id,
                current_budget_revision_id=budget_revision_id,
                current_obligation_revision_id=obligation_revision_id,
            ),
        )

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

    def _trusted_denial_owner(self, request: Any, decision: Any) -> Any:
        """Validate ledger-only denial evidence and return its frozen owner.

        A policy denial must never be represented by executable authorization
        evidence.  Only the server-issued, non-executable reservation contract
        can cross this persistence boundary.
        """
        from app.assistant.provider_loop.contracts import (
            ProviderDeniedLedgerReservationRequest,
        )

        if not isinstance(request, ProviderDeniedLedgerReservationRequest):
            raise CapabilityCallConflict(
                "denial_reservation_non_executable_required",
                "denied call requires non-executable ledger reservation evidence",
            )
        if getattr(request, "authorization", None) is not None:
            raise CapabilityCallConflict(
                "denial_reservation_non_executable_required",
                "denied call cannot carry executable authorization evidence",
            )
        denial_evidence = request.denial_evidence
        denial_lookup = getattr(
            self.authorization_factory,
            "denial_reservation_for_call",
            None,
        )
        if not callable(denial_lookup):
            raise CapabilityCallConflict(
                "denial_reservation_untrusted",
                "denied call lacks a trusted reservation issuer",
            )
        try:
            trusted_denial = denial_lookup(call_id=request.call.call_id)
        except Exception as exc:
            raise CapabilityCallConflict(
                "denial_reservation_untrusted",
                "denied call lacks trusted frozen reservation evidence",
            ) from exc
        if (
            trusted_denial != denial_evidence
            or str(denial_evidence.decision_digest)
            != str(decision.decision_digest)
            or str(denial_evidence.reason_code)
            != str(decision.reason_code)
        ):
            raise CapabilityCallConflict(
                "denial_reservation_mismatch",
                "denied reservation evidence does not match the frozen decision",
            )
        return denial_evidence.owner

    def reserve_siblings(
        self, requests: Any, provider_messages: Any = ()
    ) -> None:
        """Persist valid Provider siblings in order before any one may start."""
        requests = tuple(requests or ())
        if not requests:
            return
        if self.lease is None:
            raise CapabilityCallConflict(
                "ledger_lease_required", "sibling reservation requires a Run lease"
            )
        run_ids = {request.execution_scope.run_id for request in requests}
        if len(run_ids) != 1:
            raise CapabilityCallConflict(
                "sibling_run_mismatch", "siblings must belong to one durable Run"
            )
        run_id = next(iter(run_ids))
        try:
            run = self.calls.get_run(run_id, for_update=True)
            if str(run.capability_ledger_mode) != "enforced":
                raise CapabilityCallConflict(
                    "ledger_mode_mismatch", "run is not frozen in enforced ledger mode"
                )
            expected_run_revision = int(run.state_revision)
            reserved_artifacts: list[AssistantRunArtifact] = []
            created_any = False
            for request in requests:
                decision = self.authorization_factory.decision_for_call(
                    call_id=request.call.call_id
                )
                disposition = getattr(decision, "dispatch_disposition", None)
                if disposition not in {
                    "deny",
                    "dispatch",
                    "awaiting_call_approval",
                }:
                    raise CapabilityCallConflict(
                        "ledger_decision_required",
                        "tagged v2 ledger decision is required",
                    )
                if disposition == "deny":
                    owner = self._trusted_denial_owner(request, decision)
                else:
                    if getattr(request, "denial_evidence", None) is not None:
                        raise CapabilityCallConflict(
                            "denial_reservation_forbidden",
                            "non-denied sibling cannot carry denial reservation evidence",
                        )
                    authorization = getattr(request, "authorization", None)
                    if authorization is None:
                        raise CapabilityCallConflict(
                            "ledger_authorization_required",
                            "non-denied sibling requires executable authorization evidence",
                        )
                    owner = authorization.owner

                if owner is None:
                    raise CapabilityCallConflict(
                        "ledger_authorization_required",
                        "ledger reservation requires a frozen owner",
                    )

                logical_key = f"provider:{request.call.call_id}"
                call_id = _stable_uuid(f"mindatlas:{run.id}:{logical_key}")
                input_payload = canonical_json_bytes(dict(request.call.arguments))
                input_digest = sha256_bytes(input_payload)
                side_effect = str(request.descriptor.behavior.side_effect)
                approval_binding_digest = None
                if disposition == "awaiting_call_approval":
                    approval_binding_digest = build_approval_binding(
                        call_id=call_id,
                        logical_call_key=logical_key,
                        owner_digest=decision.owner_policy_digest,
                        binding_contract_digest=(
                            request.binding.ref.binding_contract_digest
                        ),
                        input_digest=input_digest,
                        target_version_id=request.descriptor.target_version_id,
                        target_digest=request.binding.ref.resolution_digest,
                        descriptor_digest=request.descriptor.descriptor_digest,
                        authorization_digest=decision.decision_digest,
                        principal_digest=decision.principal_digest,
                        request_revision=1,
                    ).approval_binding_digest
                elif disposition != "deny" and side_effect == "write_local":
                    raise CapabilityCallConflict(
                        "call_approval_required",
                        "local write requires call-owned approval",
                    )

                manifest = self._manifest_row(request)
                artifact = self._input_artifact(request)
                reserved_artifacts.append(artifact)
                call, created = self.calls.create_or_verify_proposed(
                    ProposeCallSpec(
                        call_id=call_id,
                        run_id=run.id,
                        expected_run_revision=expected_run_revision,
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
                        idempotency_key=make_server_idempotency_key(
                            secret=self.idempotency_secret,
                            run_id=run.id,
                            logical_call_key=logical_key,
                            frozen_target_digest=request.binding.ref.resolution_digest,
                            canonical_input_digest=input_digest,
                        ),
                        provider_tool_call_id=request.call.call_id,
                        approval_binding_digest=approval_binding_digest,
                    )
                )
                mutated = created
                if disposition == "deny":
                    reason_code = str(decision.reason_code or "policy_denied")
                    if str(call.status) == "proposed":
                        call = self.calls.transition_call(
                            call_id=call.id,
                            expected_call_revision=int(call.state_revision),
                            expected_run_revision=expected_run_revision,
                            to_status="denied",
                            lease=self.lease,
                            failure_code=reason_code,
                        )
                        mutated = True
                    elif (
                        str(call.status) != "denied"
                        or str(call.failure_code or "") != reason_code
                    ):
                        raise CapabilityCallConflict(
                            "denial_replay_mismatch",
                            "stored denial does not match the frozen policy decision",
                        )
                created_any = created_any or mutated

            from app.assistant.capability_calls.models import (
                AssistantCapabilityCall,
                AssistantCapabilityCallAttempt,
            )
            from app.assistant.durable.checkpoints import (
                _build_provider_message_rows,  # noqa: SLF001
                _current_transcript_digest,  # noqa: SLF001
                _next_checkpoint_sequence,  # noqa: SLF001
            )
            from app.assistant.durable.codec import (
                checkpoint_state_digest,
                encode_checkpoint_v3,
            )
            from app.assistant.durable.contracts import (
                DurableAgentCheckpointV3,
                DurableCapabilityCallStateV1,
                DurableNextActionV2,
            )
            from app.assistant.provider_loop.messages import (
                ProviderAssistantMessage,
                ProviderToolMessage,
                digest_provider_message,
                digest_provider_transcript,
            )

            ordinal, _digest, prior = _current_transcript_digest(self.db, run.id)
            supplied = tuple(provider_messages or ())
            if len(supplied) >= len(prior) and supplied[: len(prior)] == prior:
                suffix = supplied[len(prior) :]
                transcript = supplied
            elif not prior:
                suffix = supplied
                transcript = supplied
            else:
                raise CapabilityCallConflict(
                    "reservation_transcript_mismatch",
                    "sibling reservation does not preserve the durable transcript",
                )
            if not created_any and not suffix:
                self.db.rollback()
                return

            (
                runtime_rows,
                manifest_revision_id,
                policy_revision_id,
                budget_revision_id,
                obligation_revision_id,
            ) = self._stage_runtime_snapshot(run)

            provider_order: dict[str, int] = {}
            next_order = 0
            for message in transcript:
                if isinstance(message, ProviderAssistantMessage):
                    for tool_call in message.tool_calls:
                        if tool_call.call_id not in provider_order:
                            provider_order[tool_call.call_id] = next_order
                            next_order += 1
            call_rows = (
                self.db.query(AssistantCapabilityCall)
                .filter(
                    AssistantCapabilityCall.run_id == run.id,
                    AssistantCapabilityCall.provider_tool_call_id.is_not(None),
                )
                .all()
            )
            attempt_rows = (
                self.db.query(AssistantCapabilityCallAttempt)
                .filter(
                    AssistantCapabilityCallAttempt.call_id.in_(
                        [item.id for item in call_rows]
                    )
                )
                .order_by(AssistantCapabilityCallAttempt.attempt_number.desc())
                .all()
                if call_rows
                else []
            )
            latest_attempt: dict[UUID, Any] = {}
            for item in attempt_rows:
                latest_attempt.setdefault(item.call_id, item)
            result_digests = {
                item.call_id: digest_provider_message(item)
                for item in transcript
                if isinstance(item, ProviderToolMessage)
            }
            call_states = []
            for item in sorted(
                call_rows,
                key=lambda row: provider_order.get(
                    str(row.provider_tool_call_id), 10**9
                ),
            ):
                tool_id = str(item.provider_tool_call_id)
                if tool_id not in provider_order:
                    raise CapabilityCallConflict(
                        "checkpoint_call_order_missing",
                        f"CapabilityCall {item.id} is absent from Provider order",
                    )
                attempt = latest_attempt.get(item.id)
                call_states.append(
                    DurableCapabilityCallStateV1(
                        call_id=item.id,
                        logical_call_key=str(item.logical_call_key),
                        provider_tool_call_id=tool_id,
                        provider_order=provider_order[tool_id],
                        status=str(item.status),
                        attempt_id=(attempt.id if attempt is not None else None),
                        output_artifact_id=item.output_artifact_id,
                        interrupt_id=item.interrupt_id,
                        approval_binding_digest=item.approval_binding_digest,
                        result_message_digest=result_digests.get(tool_id),
                    )
                )

            start_ordinal = 1 if ordinal == 0 else ordinal + 1
            provider_rows = _build_provider_message_rows(
                run_id=run.id,
                messages=suffix,
                start_ordinal=start_ordinal,
                manifest_revision_id=manifest_revision_id,
                policy_revision_id=policy_revision_id,
                obligation_revision_id=obligation_revision_id,
            )
            final_ordinal = start_ordinal + len(suffix) - 1 if suffix else ordinal
            transcript_digest = digest_provider_transcript(transcript)
            checkpoint_id = uuid4()
            checkpoint = DurableAgentCheckpointV3(
                run_id=run.id,
                phase="ready_for_provider",
                manifest_revision_id=manifest_revision_id,
                policy_revision_id=policy_revision_id,
                budget_revision_id=budget_revision_id,
                obligation_revision_id=obligation_revision_id,
                provider_message_ordinal=final_ordinal,
                provider_transcript_digest=transcript_digest,
                provider_loop_continuation=None,
                inflight_unit=None,
                capability_frames=(),
                artifact_ids=tuple(
                    dict.fromkeys(
                        value
                        for row in call_rows
                        for value in (row.input_artifact_id, row.output_artifact_id)
                        if value is not None
                    )
                ),
                visible_text_artifact_id=None,
                next_action=DurableNextActionV2(kind="dispatch_calls"),
                policy_contract_version=2,
                capability_calls=tuple(call_states),
            )
            checkpoint_row = AssistantRunCheckpoint(
                id=checkpoint_id,
                run_id=run.id,
                sequence=_next_checkpoint_sequence(self.db, run.id),
                expected_state_revision=expected_run_revision,
                committed_state_revision=expected_run_revision + 1,
                schema_version=3,
                manifest_revision_id=manifest_revision_id,
                policy_revision_id=policy_revision_id,
                budget_revision_id=budget_revision_id,
                obligation_revision_id=obligation_revision_id,
                provider_message_ordinal=final_ordinal,
                provider_transcript_digest=transcript_digest,
                phase="ready_for_provider",
                logical_unit_id=f"capability-group:{requests[0].call.call_id}",
                reason="capability_siblings_reserved",
                state_payload=encode_checkpoint_v3(checkpoint),
                state_digest=checkpoint_state_digest(checkpoint),
            )
            DurableRunRepository(self.db).commit_semantic(
                run_id=run.id,
                expected_revision=expected_run_revision,
                lease=self.lease,
                events=(
                    EventSpec(
                        event_key=(
                            "capability_call.reserve:"
                            f"{requests[0].call.call_id}:rev{expected_run_revision}"
                        ),
                        event_name="capability_call.reserve",
                        payload={
                            "callIds": [request.call.call_id for request in requests]
                        },
                        visibility="internal",
                    ),
                ),
                children=DurableChildBundle(
                    rows=[
                        *reserved_artifacts,
                        *runtime_rows,
                        *provider_rows,
                        checkpoint_row,
                    ],
                    current_checkpoint_id=checkpoint_id,
                    current_manifest_revision_id=manifest_revision_id,
                    current_policy_revision_id=policy_revision_id,
                    current_budget_revision_id=budget_revision_id,
                    current_obligation_revision_id=obligation_revision_id,
                ),
            )
        except Exception:
            self.db.rollback()
            raise

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
        approval_satisfied = False

        if disposition == "deny":
            owner = self._trusted_denial_owner(request, decision)
            existing = self.calls.get_call_by_logical_key(
                run_id=run.id,
                logical_call_key=logical_key,
                for_update=True,
            )
            if existing is None:
                self.db.rollback()
                raise CapabilityCallConflict(
                    "call_reservation_required",
                    "denied call requires a durable sibling reservation",
                )
            artifact = self.db.get(AssistantRunArtifact, existing.input_artifact_id)
            if (
                existing.id != call_id
                or artifact is None
                or artifact.run_id != run.id
                or str(artifact.kind) != "capability_call_input"
                or str(artifact.storage_kind) != "inline"
                or artifact.inline_bytes is None
                or bytes(artifact.inline_bytes) != input_payload
                or str(artifact.content_sha256) != input_digest
                or int(artifact.byte_size) != len(input_payload)
            ):
                self.db.rollback()
                raise CapabilityCallConflict(
                    "denial_identity_mismatch",
                    "stored denial input Artifact does not match the frozen request",
                )
            manifest = self._manifest_row(request)
            expected = ProposeCallSpec(
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
                idempotency_key=make_server_idempotency_key(
                    secret=self.idempotency_secret,
                    run_id=run.id,
                    logical_call_key=logical_key,
                    frozen_target_digest=request.binding.ref.resolution_digest,
                    canonical_input_digest=input_digest,
                ),
                provider_tool_call_id=request.call.call_id,
            )
            call, _ = self.calls.create_or_verify_proposed(expected)
            reason_code = str(decision.reason_code or "policy_denied")
            if (
                str(call.status) != "denied"
                or str(call.failure_code or "") != reason_code
            ):
                self.db.rollback()
                raise CapabilityCallConflict(
                    "denial_replay_mismatch",
                    "stored denial does not match the frozen policy decision",
                )
            revision = int(call.state_revision)
            self.db.rollback()
            return LedgerPrepareOutcome(
                kind="deny",
                call_id=call.id,
                call_revision=revision,
                reason_code=reason_code,
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
            existing = self.calls.get_call_by_logical_key(
                run_id=run.id,
                logical_call_key=logical_key,
                for_update=True,
            )
            approved_existing = False
            if existing is not None:
                interrupt = (
                    self.db.get(AssistantRunInterrupt, existing.interrupt_id)
                    if existing.interrupt_id is not None
                    else None
                )
                if (
                    existing.id != call_id
                    or str(existing.provider_tool_call_id) != str(request.call.call_id)
                    or str(existing.input_digest) != input_digest
                    or str(existing.descriptor_digest)
                    != str(request.descriptor.descriptor_digest)
                    or str(existing.authorization_digest) != str(decision.decision_digest)
                    or str(existing.approval_binding_digest)
                    != str(binding.approval_binding_digest)
                    or interrupt is None
                    or interrupt.capability_call_id != existing.id
                    or str(interrupt.interrupt_origin) != "capability_call"
                ):
                    self.db.rollback()
                    raise CapabilityCallConflict(
                        "approval_binding_mismatch",
                        "stored call approval does not match the frozen request",
                    )
                approved_existing = (
                    str(existing.status) == "authorized"
                    and str(interrupt.status) == "approved"
                    and str(
                        (interrupt.request_payload or {}).get(
                            "approvalBindingDigest"
                        )
                    )
                    == str(binding.approval_binding_digest)
                )
                if str(existing.status) == "authorized" and not approved_existing:
                    self.db.rollback()
                    raise CapabilityCallConflict(
                        "approval_binding_mismatch",
                        "authorized call lacks exact approved interrupt evidence",
                    )
            if approved_existing:
                from app.assistant.policy.write_admission import (
                    issue_post_approval_gateway_evidence,
                )

                approved_decision = issue_post_approval_gateway_evidence(
                    frozen_decision=decision,
                    approval_binding_digest=binding.approval_binding_digest,
                )
                if (
                    str(approved_decision.decision_digest)
                    != str(decision.decision_digest)
                ):
                    self.db.rollback()
                    raise CapabilityCallConflict(
                        "post_approval_policy_drift",
                        "post-approval evidence changed the frozen decision",
                    )
                authorize_pending = getattr(
                    self.authorization_factory, "authorize_pending_call", None
                )
                if callable(authorize_pending):
                    authorize_pending(
                        call_id=request.call.call_id,
                        approval_binding_digest=binding.approval_binding_digest,
                    )
                approval_satisfied = True
                disposition = "dispatch"
            else:
                # Pure staging: no call/Interrupt/Checkpoint layer is persisted here.
                self.db.rollback()
                outcome = LedgerPrepareOutcome(
                    kind="pause",
                    call_id=call_id,
                    call_revision=(
                        int(existing.state_revision) if existing is not None else 0
                    ),
                    pause_proposal={
                        "contractVersion": 1,
                        "runId": str(run.id),
                        "callId": str(call_id),
                        "interruptId": str(
                            existing.interrupt_id
                            if existing is not None
                            else _stable_uuid(f"mindatlas:interrupt:{call_id}")
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
                self._pending_pause = {
                    "request": request,
                    "outcome": outcome,
                    "decision": decision,
                    "binding": binding,
                    "inputPayload": input_payload,
                    "inputDigest": input_digest,
                    "logicalKey": logical_key,
                    "sideEffect": side_effect,
                }
                return outcome

        if side_effect == "write_local" and not approval_satisfied:
            self.db.rollback()
            raise CapabilityCallConflict(
                "call_approval_required",
                "local write requires an exact terminal-approved call-owned Interrupt",
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
        if str(call.status) not in {"proposed", "authorized"}:
            self.db.rollback()
            raise CapabilityCallConflict(
                "call_not_dispatchable", f"call status {call.status!r} cannot dispatch"
            )
        if str(call.status) == "proposed":
            call = self.calls.transition_call(
                call_id=call.id,
                expected_call_revision=int(call.state_revision),
                expected_run_revision=int(run.state_revision),
                to_status="authorized",
                lease=self.lease,
            )
        from app.assistant.capability_calls.reconciliation import (
            validate_retry_authorization_for_dispatch,
        )

        claim_now = utcnow()
        validate_retry_authorization_for_dispatch(
            self.db,
            call=call,
            now=claim_now,
        )
        worker_id = self.lease.worker_id
        call, attempt = self.calls.claim_attempt(
            call_id=call.id,
            expected_call_revision=int(call.state_revision),
            expected_run_revision=int(run.state_revision),
            lease=self.lease,
            worker_id=worker_id,
            now=claim_now,
            mark_side_effect_started=str(call.execution_mode)
            in {
                "external_idempotent",
                "external_reconcilable",
                "non_retriable",
            },
        )
        attempt = self.calls.transition_attempt(
            attempt_id=attempt.id,
            expected_status="claimed",
            to_status="dispatched",
            request_digest=input_digest,
        )
        local_dispatch = side_effect == "write_local"
        if local_dispatch and request.call.domain_key != "create_entry":
            self.db.rollback()
            raise CapabilityCallConflict(
                "local_write_binding_forbidden",
                "only the frozen golden create_entry binding may execute locally",
            )
        if not local_dispatch:
            self._commit_attempt_started(run=run, call=call, attempt=attempt)
        return LedgerPrepareOutcome(
            kind="dispatch_local" if local_dispatch else "dispatch",
            call_id=call.id,
            call_revision=int(call.state_revision),
            attempt_id=attempt.id,
        )

    def execute_local(
        self, outcome: LedgerPrepareOutcome, request: Any
    ) -> ProviderDispatchResult:
        """Stage the exact golden create_entry mutation without owning commit."""
        if outcome.kind != "dispatch_local":
            raise CapabilityCallConflict(
                "local_dispatch_required", "outcome is not a local dispatch"
            )
        call = self.calls.get_call(outcome.call_id, for_update=True)
        if (
            call is None
            or str(call.status) != "executing"
            or str(call.domain_key) != "create_entry"
            or str(call.execution_mode) != "local_transactional"
        ):
            raise CapabilityCallConflict(
                "local_write_binding_forbidden",
                "local write call identity is not the golden create_entry binding",
            )
        from app.assistant.capabilities.contracts import (
            CapabilityMetrics,
            completed_result,
        )
        from app.assistant.capability_calls.local_write import stage_create_entry_local
        from app.assistant.tools.entry_tools import _build_entry_request

        arguments = dict(request.call.arguments)
        create_args = {
            "title": None,
            "summary": None,
            "content": None,
            "type_code": None,
            "tags": None,
            "time_mode": None,
            "time_at": None,
            "time_from": None,
            "time_to": None,
        }
        create_args.update(arguments)
        entry_request = _build_entry_request(self.db, **create_args)
        entry = stage_create_entry_local(
            session=self.db,
            request=entry_request,
            call_id=call.id,
        )
        return ProviderDispatchResult(
            capability_result=completed_result(
                user_text=f"Created entry {entry.title}",
                structured_output={
                    "status": "ok",
                    "entryId": str(entry.id),
                    "title": str(entry.title),
                },
                metrics=CapabilityMetrics(
                    duration_ms=0.0,
                    input_bytes=len(canonical_json_bytes(arguments)),
                    output_bytes=0,
                ),
            ),
            next_manifest=request.current_manifest,
        )

    def commit_pause(self, continuation: Any, provider_messages: Any = ()) -> None:
        """Commit call + Interrupt + transcript + waiting Checkpoint in one Run CAS."""
        # Plan 09 Task 4: hard tripwire when Eval scope reaches ledger pause commit.
        from app.assistant.evaluation.isolation import tripwire_production_writer

        tripwire_production_writer("DurableCapabilityLedgerAggregate.commit")
        staged = self._pending_pause
        if staged is None:
            raise CapabilityCallConflict(
                "pause_proposal_missing", "no staged call-owned pause is available"
            )
        if self.lease is None:
            raise CapabilityCallConflict(
                "ledger_lease_required", "call-owned pause requires the claimed Run lease"
            )
        request = staged["request"]
        outcome = staged["outcome"]
        decision = staged["decision"]
        binding = staged["binding"]
        messages = tuple(provider_messages or ())
        waiting = getattr(continuation, "waiting_call", None)
        if (
            waiting is None
            or str(waiting.call_id) != str(request.call.call_id)
            or str(continuation.execution_scope.run_id)
            != str(request.execution_scope.run_id)
        ):
            raise CapabilityCallConflict(
                "pause_continuation_mismatch",
                "Provider waiting continuation does not own the staged call",
            )
        cap_cont = waiting.capability_continuation
        if (
            str(cap_cont.reference_id)
            != str(outcome.pause_proposal["interruptId"])
            or str(cap_cont.payload_digest) != str(binding.approval_binding_digest)
        ):
            raise CapabilityCallConflict(
                "pause_continuation_mismatch",
                "capability continuation does not match the approval proposal",
            )

        repo = DurableRunRepository(self.db)
        try:
            run = repo.get_run(request.execution_scope.run_id, for_update=True)
            if run is None:
                raise CapabilityCallConflict("run_not_found", "Run is unavailable")
            repo._verify_lease(run, self.lease)  # noqa: SLF001 - shared aggregate lock
            expected_revision = int(run.state_revision)
            manifest = self._manifest_row(request)
            input_artifact = self._input_artifact(request)
            idem = make_server_idempotency_key(
                secret=self.idempotency_secret,
                run_id=run.id,
                logical_call_key=staged["logicalKey"],
                frozen_target_digest=request.binding.ref.resolution_digest,
                canonical_input_digest=staged["inputDigest"],
            )
            owner = request.authorization.owner
            spec = ProposeCallSpec(
                call_id=outcome.call_id,
                run_id=run.id,
                expected_run_revision=expected_revision,
                lease=self.lease,
                manifest_revision_id=manifest.id,
                logical_call_key=staged["logicalKey"],
                owner_kind=str(owner.owner_kind),
                owner_id=_uuid_or_none(owner.owner_id),
                owner_version_id=owner.owner_version_id,
                capability_type=str(request.descriptor.capability_type),
                domain_key=request.call.domain_key,
                target_id=request.descriptor.target_id,
                target_version_id=request.descriptor.target_version_id,
                descriptor_digest=request.descriptor.descriptor_digest,
                authorization_digest=decision.decision_digest,
                input_artifact_id=input_artifact.id,
                input_digest=staged["inputDigest"],
                side_effect_class=staged["sideEffect"],
                execution_mode=_execution_mode(staged["sideEffect"]),
                idempotency_key=idem,
                provider_tool_call_id=request.call.call_id,
                approval_binding_digest=binding.approval_binding_digest,
            )
            call, _ = self.calls.create_or_verify_proposed(spec)
            if str(call.status) != "proposed":
                raise CapabilityCallConflict(
                    "call_not_awaiting_pause",
                    f"call status {call.status!r} cannot enter approval wait",
                )

            approval_payload = json.dumps(
                outcome.pause_proposal,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            approval_artifact = AssistantRunArtifact(
                id=uuid4(),
                run_id=run.id,
                kind="capability_call_approval",
                media_type="application/json",
                storage_kind="inline",
                byte_size=len(approval_payload),
                content_sha256=sha256_bytes(approval_payload),
                inline_bytes=approval_payload,
                metadata_json={
                    "contractVersion": 1,
                    "callId": str(call.id),
                    "approvalBindingDigest": binding.approval_binding_digest,
                },
            )
            self.db.add(approval_artifact)

            from app.assistant.durable.checkpoints import (
                _build_provider_message_rows,  # noqa: PLC0415, SLF001
                _current_transcript_digest,  # noqa: PLC0415, SLF001
                _next_checkpoint_sequence,  # noqa: PLC0415, SLF001
            )
            from app.assistant.durable.codec import (
                checkpoint_state_digest,
                encode_checkpoint_v3,
            )
            from app.assistant.durable.contracts import (
                DurableAgentCheckpointV3,
                DurableCapabilityCallStateV1,
                DurableNextActionV2,
            )
            from app.assistant.capability_calls.models import (
                AssistantCapabilityCall,
                AssistantCapabilityCallAttempt,
            )
            from app.assistant.provider_loop.messages import (
                ProviderAssistantMessage,
                ProviderToolMessage,
                digest_provider_message,
                digest_provider_transcript,
            )

            (
                runtime_rows,
                manifest_revision_id,
                policy_revision_id,
                budget_revision_id,
                obligation_revision_id,
            ) = self._stage_runtime_snapshot(run)

            ordinal, _old_digest, prior_messages = _current_transcript_digest(
                self.db, run.id
            )
            start_ordinal = 1 if ordinal == 0 else ordinal + 1
            provider_rows = _build_provider_message_rows(
                run_id=run.id,
                messages=messages,
                start_ordinal=start_ordinal,
                manifest_revision_id=manifest_revision_id,
                policy_revision_id=policy_revision_id,
                obligation_revision_id=obligation_revision_id,
            )
            transcript = prior_messages + messages
            transcript_digest = digest_provider_transcript(transcript)
            if transcript_digest != continuation.transcript_digest:
                raise CapabilityCallConflict(
                    "pause_transcript_mismatch",
                    "waiting transcript does not match Provider continuation",
                )
            final_ordinal = (
                start_ordinal + len(messages) - 1 if messages else ordinal
            )
            checkpoint_id = uuid4()
            interrupt_id = UUID(str(outcome.pause_proposal["interruptId"]))
            provider_order: dict[str, int] = {}
            next_order = 0
            for message in transcript:
                if isinstance(message, ProviderAssistantMessage):
                    for tool_call in message.tool_calls:
                        if tool_call.call_id not in provider_order:
                            provider_order[tool_call.call_id] = next_order
                            next_order += 1
            call_rows = (
                self.db.query(AssistantCapabilityCall)
                .filter(
                    AssistantCapabilityCall.run_id == run.id,
                    AssistantCapabilityCall.provider_tool_call_id.is_not(None),
                )
                .all()
            )
            attempt_rows = (
                self.db.query(AssistantCapabilityCallAttempt)
                .filter(
                    AssistantCapabilityCallAttempt.call_id.in_(
                        [item.id for item in call_rows]
                    )
                )
                .order_by(AssistantCapabilityCallAttempt.attempt_number.desc())
                .all()
                if call_rows
                else []
            )
            latest_attempt: dict[UUID, Any] = {}
            for item in attempt_rows:
                latest_attempt.setdefault(item.call_id, item)
            result_digests = {
                item.call_id: digest_provider_message(item)
                for item in transcript
                if isinstance(item, ProviderToolMessage)
            }
            call_states = []
            for item in sorted(
                call_rows,
                key=lambda row: provider_order.get(
                    str(row.provider_tool_call_id), 10**9
                ),
            ):
                tool_id = str(item.provider_tool_call_id)
                if tool_id not in provider_order:
                    raise CapabilityCallConflict(
                        "checkpoint_call_order_missing",
                        f"CapabilityCall {item.id} is absent from Provider order",
                    )
                attempt = latest_attempt.get(item.id)
                is_waiting = item.id == call.id
                call_states.append(
                    DurableCapabilityCallStateV1(
                        call_id=item.id,
                        logical_call_key=str(item.logical_call_key),
                        provider_tool_call_id=tool_id,
                        provider_order=provider_order[tool_id],
                        status=("awaiting_approval" if is_waiting else str(item.status)),
                        attempt_id=(attempt.id if attempt is not None else None),
                        output_artifact_id=item.output_artifact_id,
                        interrupt_id=(interrupt_id if is_waiting else item.interrupt_id),
                        approval_binding_digest=item.approval_binding_digest,
                        result_message_digest=result_digests.get(tool_id),
                    )
                )
            checkpoint_artifact_ids = tuple(
                dict.fromkeys(
                    [input_artifact.id, approval_artifact.id]
                    + [
                        value
                        for row in call_rows
                        for value in (row.input_artifact_id, row.output_artifact_id)
                        if value is not None
                    ]
                )
            )
            checkpoint = DurableAgentCheckpointV3(
                run_id=run.id,
                phase="waiting",
                manifest_revision_id=manifest_revision_id,
                policy_revision_id=policy_revision_id,
                budget_revision_id=budget_revision_id,
                obligation_revision_id=obligation_revision_id,
                provider_message_ordinal=final_ordinal,
                provider_transcript_digest=transcript_digest,
                provider_loop_continuation=continuation,
                inflight_unit=None,
                capability_frames=(),
                artifact_ids=checkpoint_artifact_ids,
                visible_text_artifact_id=None,
                next_action=DurableNextActionV2(kind="wait"),
                policy_contract_version=2,
                capability_calls=tuple(call_states),
            )
            checkpoint_row = AssistantRunCheckpoint(
                id=checkpoint_id,
                run_id=run.id,
                sequence=_next_checkpoint_sequence(self.db, run.id),
                expected_state_revision=expected_revision,
                committed_state_revision=expected_revision + 1,
                schema_version=3,
                manifest_revision_id=manifest_revision_id,
                policy_revision_id=policy_revision_id,
                budget_revision_id=budget_revision_id,
                obligation_revision_id=obligation_revision_id,
                provider_message_ordinal=final_ordinal,
                provider_transcript_digest=transcript_digest,
                phase="waiting",
                logical_unit_id=staged["logicalKey"],
                reason="capability_call_approval",
                state_payload=encode_checkpoint_v3(checkpoint),
                state_digest=checkpoint_state_digest(checkpoint),
            )
            self.db.add(checkpoint_row)
            self.db.flush()

            from app.assistant.workflow.durable.interrupts import (
                DurableInterruptRepository,
            )
            from app.assistant.workflow.durable.pause import resolve_parent_budget_ledger

            if self.runtime_snapshot_provider is None:
                parent_ledger, parent_budget_id = resolve_parent_budget_ledger(
                    self.db, run=run
                )
            else:
                parent_ledger = self.runtime_snapshot_provider()["budget"]
                parent_budget_id = budget_revision_id
            created = DurableInterruptRepository(self.db).create_pending_interrupt(
                run_id=run.id,
                interrupt_id=interrupt_id,
                interrupt_key=f"capability:{call.id}",
                kind="approval",
                checkpoint_id=checkpoint_id,
                manifest_revision_id=manifest_revision_id,
                budget_revision_id=parent_budget_id,
                capability_call_id=call.id,
                interrupt_origin="capability_call",
                workflow_frame_id=None,
                node_id=None,
                node_visit_id=None,
                request_run_revision=expected_revision + 1,
                request_payload=dict(outcome.pause_proposal),
                field_schema=None,
                initial_values={},
                parent_ledger=parent_ledger,
                parent_budget_revision_id=parent_budget_id,
                lock_run=False,
            )
            call = self.calls.transition_call(
                call_id=call.id,
                expected_call_revision=int(call.state_revision),
                expected_run_revision=expected_revision,
                to_status="awaiting_approval",
                lease=self.lease,
                approval_binding_digest=binding.approval_binding_digest,
                interrupt_id=created.interrupt.id,
            )

            from app.assistant.durable.crash import CrashPoint, maybe_crash

            maybe_crash(CrashPoint.AFTER_INTERRUPT_INSERT_BEFORE_OUTER_POINTER_CAS)
            bundle = DurableChildBundle(
                rows=[
                    input_artifact,
                    approval_artifact,
                    *runtime_rows,
                    *provider_rows,
                    checkpoint_row,
                ],
                current_checkpoint_id=checkpoint_id,
                current_manifest_revision_id=manifest_revision_id,
                current_policy_revision_id=policy_revision_id,
                current_budget_revision_id=budget_revision_id,
                current_obligation_revision_id=obligation_revision_id,
            )
            repo.commit_waiting_pause(
                run_id=run.id,
                expected_revision=expected_revision,
                lease=self.lease,
                target_status=STATUS_WAITING_APPROVAL,
                events=(
                    EventSpec(
                        event_key=f"capability_call.wait:{call.id}:rev{expected_revision}",
                        event_name="capability_call.wait",
                        payload={
                            "callId": str(call.id),
                            "interruptId": str(created.interrupt.id),
                            "approvalBindingDigest": binding.approval_binding_digest,
                        },
                        visibility="public",
                    ),
                ),
                children=bundle,
            )
            self._pending_pause = None
        except Exception:
            self.db.rollback()
            raise

    def commit_result(
        self, outcome: LedgerPrepareOutcome, result: ProviderDispatchResult
    ) -> ProviderDispatchResult:
        # Plan 09 Task 4: hard tripwire when Eval scope reaches ledger commit.
        from app.assistant.evaluation.isolation import tripwire_production_writer

        tripwire_production_writer("DurableCapabilityLedgerAggregate.commit")
        call_hint = self.calls.get_call(outcome.call_id)
        if call_hint is None or outcome.attempt_id is None:
            raise CapabilityCallConflict("call_not_found", "dispatch call is unavailable")
        self.calls.get_run(call_hint.run_id, for_update=True)
        call = self.calls.get_call(outcome.call_id, for_update=True)
        if call is None:
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
        call = self.calls.transition_call(
            call_id=call.id,
            expected_call_revision=int(call.state_revision),
            expected_run_revision=int(self.calls.get_run(call.run_id).state_revision),
            to_status=target,
            lease=self.lease,
            output_artifact_id=artifact.id,
            failure_code=(None if target == "succeeded" else "capability_failed"),
            side_effect_started_at=(
                utcnow()
                if target == "succeeded"
                and str(call.execution_mode) == "local_transactional"
                else None
            ),
        )
        if self._pending_result is not None:
            self.db.rollback()
            raise CapabilityCallConflict(
                "result_pending",
                "a capability result is already waiting for its Tool pairing",
            )
        self._pending_result = {
            "outcome": outcome,
            "result": result,
            "artifact": artifact,
            "callId": call.id,
        }
        return result

    def commit_progress(
        self,
        provider_messages: Any = (),
        *,
        current_manifest: Any | None = None,
    ) -> None:
        """Pair a staged result with its Tool message, Checkpoint, and Run CAS."""
        pending = self._pending_result
        if pending is None:
            return
        if self.lease is None:
            self.db.rollback()
            raise CapabilityCallConflict(
                "ledger_lease_required", "result commit requires a Run lease"
            )

        from app.assistant.capability_calls.models import (
            AssistantCapabilityCall,
            AssistantCapabilityCallAttempt,
        )
        from app.assistant.durable.checkpoints import (
            _build_provider_message_rows,  # noqa: SLF001
            _current_transcript_digest,  # noqa: SLF001
            _next_checkpoint_sequence,  # noqa: SLF001
        )
        from app.assistant.durable.codec import (
            checkpoint_state_digest,
            encode_checkpoint_v3,
        )
        from app.assistant.durable.contracts import (
            DurableAgentCheckpointV3,
            DurableCapabilityCallStateV1,
            DurableNextActionV2,
        )
        from app.assistant.provider_loop.messages import (
            ProviderAssistantMessage,
            ProviderToolMessage,
            digest_provider_message,
            digest_provider_transcript,
            project_tool_result_envelope,
        )

        try:
            run_repo = DurableRunRepository(self.db)
            call_hint = self.calls.get_call(pending["callId"])
            if call_hint is None:
                raise CapabilityCallConflict(
                    "result_not_succeeded",
                    "staged local result call is unavailable",
                )
            run = run_repo.get_run(call_hint.run_id, for_update=True)
            if run is None:
                raise CapabilityCallConflict("run_not_found", "Run is unavailable")
            call = self.calls.get_call(pending["callId"], for_update=True)
            if call is None or str(call.status) not in {"succeeded", "failed"}:
                raise CapabilityCallConflict(
                    "result_not_terminal",
                    "staged result is not a terminal call",
                )
            run_repo._verify_lease(run, self.lease)  # noqa: SLF001
            expected_revision = int(run.state_revision)

            ordinal, _digest, prior = _current_transcript_digest(self.db, run.id)
            supplied = tuple(provider_messages or ())
            if len(supplied) >= len(prior) and supplied[: len(prior)] == prior:
                suffix = supplied[len(prior) :]
                transcript = supplied
            elif not prior:
                suffix = supplied
                transcript = supplied
            else:
                raise CapabilityCallConflict(
                    "result_transcript_mismatch",
                    "Tool result transcript does not preserve the durable prefix",
                )

            provider_call_id = str(call.provider_tool_call_id)
            tool_messages = [
                item
                for item in suffix
                if isinstance(item, ProviderToolMessage)
                and item.call_id == provider_call_id
            ]
            if len(tool_messages) != 1:
                raise CapabilityCallConflict(
                    "tool_result_pairing_mismatch",
                    "capability result requires exactly one new matching Tool Result",
                )
            tool_message = tool_messages[0]
            expected_envelope = project_tool_result_envelope(
                domain_key=str(call.domain_key),
                result=pending["result"].capability_result,
            )
            if tool_message.content != expected_envelope:
                raise CapabilityCallConflict(
                    "tool_result_pairing_mismatch",
                    "Tool Result does not match the staged capability result",
                )

            provider_order: dict[str, int] = {}
            next_order = 0
            for message in transcript:
                if isinstance(message, ProviderAssistantMessage):
                    for tool_call in message.tool_calls:
                        if tool_call.call_id not in provider_order:
                            provider_order[tool_call.call_id] = next_order
                            next_order += 1
            if provider_call_id not in provider_order:
                raise CapabilityCallConflict(
                    "tool_result_pairing_mismatch",
                    "Tool Result has no preceding Provider Tool Call",
                )

            (
                runtime_rows,
                manifest_revision_id,
                policy_revision_id,
                budget_revision_id,
                obligation_revision_id,
            ) = self._stage_runtime_snapshot(
                run, manifest_override=current_manifest
            )

            start_ordinal = 1 if ordinal == 0 else ordinal + 1
            provider_rows = _build_provider_message_rows(
                run_id=run.id,
                messages=suffix,
                start_ordinal=start_ordinal,
                manifest_revision_id=manifest_revision_id,
                policy_revision_id=policy_revision_id,
                obligation_revision_id=obligation_revision_id,
            )
            transcript_digest = digest_provider_transcript(transcript)
            final_ordinal = start_ordinal + len(suffix) - 1 if suffix else ordinal

            call_rows = (
                self.db.query(AssistantCapabilityCall)
                .filter(
                    AssistantCapabilityCall.run_id == run.id,
                    AssistantCapabilityCall.provider_tool_call_id.is_not(None),
                )
                .all()
            )
            attempt_rows = (
                self.db.query(AssistantCapabilityCallAttempt)
                .filter(
                    AssistantCapabilityCallAttempt.call_id.in_(
                        [item.id for item in call_rows]
                    )
                )
                .order_by(AssistantCapabilityCallAttempt.attempt_number.desc())
                .all()
                if call_rows
                else []
            )
            latest_attempt: dict[UUID, Any] = {}
            for item in attempt_rows:
                latest_attempt.setdefault(item.call_id, item)
            result_digests = {
                item.call_id: digest_provider_message(item)
                for item in transcript
                if isinstance(item, ProviderToolMessage)
            }
            call_states = []
            for item in sorted(
                call_rows,
                key=lambda row: provider_order.get(str(row.provider_tool_call_id), 10**9),
            ):
                tool_id = str(item.provider_tool_call_id)
                if tool_id not in provider_order:
                    raise CapabilityCallConflict(
                        "checkpoint_call_order_missing",
                        f"CapabilityCall {item.id} is absent from Provider order",
                    )
                attempt = latest_attempt.get(item.id)
                call_states.append(
                    DurableCapabilityCallStateV1(
                        call_id=item.id,
                        logical_call_key=str(item.logical_call_key),
                        provider_tool_call_id=tool_id,
                        provider_order=provider_order[tool_id],
                        status=str(item.status),
                        attempt_id=(attempt.id if attempt is not None else None),
                        output_artifact_id=item.output_artifact_id,
                        interrupt_id=item.interrupt_id,
                        approval_binding_digest=item.approval_binding_digest,
                        result_message_digest=result_digests.get(tool_id),
                    )
                )

            artifact_ids = tuple(
                dict.fromkeys(
                    value
                    for row in call_rows
                    for value in (row.input_artifact_id, row.output_artifact_id)
                    if value is not None
                )
            )
            has_unfinished_siblings = any(
                str(item.status)
                not in {
                    "succeeded",
                    "failed",
                    "denied",
                    "rejected",
                    "cancelled",
                    "expired",
                    "compensated",
                    "unknown",
                    "needs_reconciliation",
                }
                for item in call_rows
            )
            checkpoint_id = uuid4()
            checkpoint = DurableAgentCheckpointV3(
                run_id=run.id,
                phase="ready_for_provider",
                manifest_revision_id=manifest_revision_id,
                policy_revision_id=policy_revision_id,
                budget_revision_id=budget_revision_id,
                obligation_revision_id=obligation_revision_id,
                provider_message_ordinal=final_ordinal,
                provider_transcript_digest=transcript_digest,
                provider_loop_continuation=None,
                inflight_unit=None,
                capability_frames=(),
                artifact_ids=artifact_ids,
                visible_text_artifact_id=None,
                next_action=DurableNextActionV2(
                    kind=(
                        "dispatch_calls"
                        if has_unfinished_siblings
                        else "continue_provider"
                    )
                ),
                policy_contract_version=2,
                capability_calls=tuple(call_states),
            )
            checkpoint_row = AssistantRunCheckpoint(
                id=checkpoint_id,
                run_id=run.id,
                sequence=_next_checkpoint_sequence(self.db, run.id),
                expected_state_revision=expected_revision,
                committed_state_revision=expected_revision + 1,
                schema_version=3,
                manifest_revision_id=manifest_revision_id,
                policy_revision_id=policy_revision_id,
                budget_revision_id=budget_revision_id,
                obligation_revision_id=obligation_revision_id,
                provider_message_ordinal=final_ordinal,
                provider_transcript_digest=transcript_digest,
                phase="ready_for_provider",
                logical_unit_id=str(call.logical_call_key),
                reason="capability_call_result",
                state_payload=encode_checkpoint_v3(checkpoint),
                state_digest=checkpoint_state_digest(checkpoint),
            )
            bundle = DurableChildBundle(
                rows=[
                    pending["artifact"],
                    *runtime_rows,
                    *provider_rows,
                    checkpoint_row,
                ],
                current_checkpoint_id=checkpoint_id,
                current_manifest_revision_id=manifest_revision_id,
                current_policy_revision_id=policy_revision_id,
                current_budget_revision_id=budget_revision_id,
                current_obligation_revision_id=obligation_revision_id,
            )
            run_repo.commit_semantic(
                run_id=run.id,
                expected_revision=expected_revision,
                lease=self.lease,
                events=(
                    EventSpec(
                        event_key=f"capability_call.result:{call.id}:rev{expected_revision}",
                        event_name="capability_call.result",
                        payload={
                            "callId": str(call.id),
                            "resultArtifactId": str(call.output_artifact_id),
                            "toolResultDigest": digest_provider_message(tool_message),
                        },
                        visibility="public",
                    ),
                ),
                children=bundle,
            )
            self._pending_result = None
        except Exception:
            self.db.rollback()
            self._pending_result = None
            raise

    def record_failure(self, outcome: LedgerPrepareOutcome, reason_code: str) -> None:
        if outcome.kind == "dispatch_local":
            # The call/Attempt and all business rows are still uncommitted. A
            # local failure must leave the complete zero set, not a partial
            # failure record that could accidentally retain staged mutations.
            self.db.rollback()
            return
        call_hint = self.calls.get_call(outcome.call_id)
        if call_hint is None:
            self.db.rollback()
            return
        self.calls.get_run(call_hint.run_id, for_update=True)
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

    def commit_recovery_drift(
        self, provider_messages: Any, *, stale_call_id: str
    ) -> None:
        """Durably seal an unstarted reserved suffix and fail the Run once."""
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.durable.checkpoints import (
            _build_provider_message_rows,  # noqa: SLF001
            _current_transcript_digest,  # noqa: SLF001
            _next_checkpoint_sequence,  # noqa: SLF001
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
        from app.assistant.provider_loop.messages import (
            ProviderAssistantMessage,
            ProviderToolMessage,
            digest_provider_message,
            digest_provider_transcript,
        )

        if self.lease is None:
            raise CapabilityCallConflict(
                "ledger_lease_required", "recovery drift settlement requires a lease"
            )
        run = self.calls.get_run(self.lease.run_id, for_update=True)
        expected_revision = int(run.state_revision)
        ordinal, _digest, prior = _current_transcript_digest(self.db, run.id)
        supplied = tuple(provider_messages or ())
        if len(supplied) < len(prior) or supplied[: len(prior)] != prior:
            raise CapabilityCallConflict(
                "recovery_drift_transcript_mismatch",
                "drift settlement does not preserve the durable transcript",
            )
        suffix = supplied[len(prior) :]
        if not suffix or any(not isinstance(item, ProviderToolMessage) for item in suffix):
            raise CapabilityCallConflict(
                "recovery_drift_transcript_invalid",
                "drift settlement requires only new Tool Results",
            )
        by_tool_id = {
            str(row.provider_tool_call_id): row
            for row in self.db.query(AssistantCapabilityCall)
            .filter(AssistantCapabilityCall.run_id == run.id)
            .all()
            if row.provider_tool_call_id is not None
        }
        for message in suffix:
            call = by_tool_id.get(message.call_id)
            if call is None or str(call.status) != "proposed":
                raise CapabilityCallConflict(
                    "recovery_drift_call_invalid",
                    "drift settlement call is missing or already started",
                )
            target = "denied" if message.call_id == stale_call_id else "cancelled"
            self.calls.transition_call(
                call_id=call.id,
                expected_call_revision=int(call.state_revision),
                expected_run_revision=expected_revision,
                to_status=target,
                lease=self.lease,
                failure_code="classification_changed",
            )

        current_row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        current = decode_checkpoint(current_row.state_payload) if current_row else None
        if current is None or int(getattr(current, "schema_version", 0)) != 3:
            raise CapabilityCallConflict(
                "recovery_drift_checkpoint_invalid",
                "drift settlement requires checkpoint schema v3",
            )
        (
            runtime_rows,
            manifest_revision_id,
            policy_revision_id,
            budget_revision_id,
            obligation_revision_id,
        ) = self._stage_runtime_snapshot(run)
        start_ordinal = 1 if ordinal == 0 else ordinal + 1
        provider_rows = _build_provider_message_rows(
            run_id=run.id,
            messages=suffix,
            start_ordinal=start_ordinal,
            manifest_revision_id=manifest_revision_id,
            policy_revision_id=policy_revision_id,
            obligation_revision_id=obligation_revision_id,
        )
        provider_order: dict[str, int] = {}
        next_order = 0
        for message in supplied:
            if isinstance(message, ProviderAssistantMessage):
                for tool_call in message.tool_calls:
                    if tool_call.call_id not in provider_order:
                        provider_order[tool_call.call_id] = next_order
                        next_order += 1
        result_digests = {
            item.call_id: digest_provider_message(item)
            for item in supplied
            if isinstance(item, ProviderToolMessage)
        }
        call_states = tuple(
            DurableCapabilityCallStateV1(
                call_id=row.id,
                logical_call_key=str(row.logical_call_key),
                provider_tool_call_id=str(row.provider_tool_call_id),
                provider_order=provider_order[str(row.provider_tool_call_id)],
                status=str(row.status),
                attempt_id=None,
                output_artifact_id=row.output_artifact_id,
                interrupt_id=row.interrupt_id,
                approval_binding_digest=row.approval_binding_digest,
                result_message_digest=result_digests.get(
                    str(row.provider_tool_call_id)
                ),
            )
            for row in sorted(
                by_tool_id.values(),
                key=lambda item: provider_order[str(item.provider_tool_call_id)],
            )
        )
        transcript_digest = digest_provider_transcript(supplied)
        final_ordinal = start_ordinal + len(suffix) - 1
        checkpoint = current.model_copy(
            update={
                "phase": "terminal",
                "manifest_revision_id": manifest_revision_id,
                "policy_revision_id": policy_revision_id,
                "budget_revision_id": budget_revision_id,
                "obligation_revision_id": obligation_revision_id,
                "provider_message_ordinal": final_ordinal,
                "provider_transcript_digest": transcript_digest,
                "next_action": DurableNextActionV2(kind="terminal"),
                "capability_calls": call_states,
            }
        )
        checkpoint_id = uuid4()
        checkpoint_row = AssistantRunCheckpoint(
            id=checkpoint_id,
            run_id=run.id,
            sequence=_next_checkpoint_sequence(self.db, run.id),
            expected_state_revision=expected_revision,
            committed_state_revision=expected_revision + 1,
            schema_version=3,
            manifest_revision_id=manifest_revision_id,
            policy_revision_id=policy_revision_id,
            budget_revision_id=budget_revision_id,
            obligation_revision_id=obligation_revision_id,
            provider_message_ordinal=final_ordinal,
            provider_transcript_digest=transcript_digest,
            phase="terminal",
            logical_unit_id=f"recovery-drift:{stale_call_id}",
            reason="classification_changed",
            state_payload=encode_checkpoint_v3(checkpoint),
            state_digest=checkpoint_state_digest(checkpoint),
        )
        DurableRunRepository(self.db).commit_running_result(
            run_id=run.id,
            expected_revision=expected_revision,
            lease=self.lease,
            target_status=STATUS_FAILED,
            failure_code="classification_changed",
            error_message="capability classification changed before recovery dispatch",
            events=(
                EventSpec(
                    event_key=f"capability_call.recovery_drift:{stale_call_id}:rev{expected_revision}",
                    event_name="capability_call.recovery_drift",
                    payload={"staleCallId": stale_call_id},
                    visibility="internal",
                ),
            ),
            children=DurableChildBundle(
                rows=[*runtime_rows, *provider_rows, checkpoint_row],
                current_checkpoint_id=checkpoint_id,
                current_manifest_revision_id=manifest_revision_id,
                current_policy_revision_id=policy_revision_id,
                current_budget_revision_id=budget_revision_id,
                current_obligation_revision_id=obligation_revision_id,
            ),
        )


__all__ = ["DurableCapabilityLedgerAggregate"]

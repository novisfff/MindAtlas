"""Plan 08 Task 7: external uncertainty matrix + reconciliation service."""

from __future__ import annotations

import unittest
import uuid
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
EVIDENCE_SECRET = "reconciliation-test-secret-32-bytes-minimum"


def _seed_external_call(
    db,
    *,
    mode: str = "external_idempotent",
    status: str = "needs_reconciliation",
    provider_tool_call_id: str | None = None,
):
    from app.assistant.capability_calls.models import (
        AssistantCapabilityCall,
        AssistantCapabilityCallAttempt,
    )
    from app.assistant.durable.models import (
        AssistantRunArtifact,
        AssistantRunBudgetRevision,
        AssistantRunCheckpoint,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )
    from app.assistant.durable.checkpoints import _build_provider_message_rows
    from app.assistant.durable.codec import checkpoint_state_digest, encode_checkpoint_v3
    from app.assistant.durable.contracts import (
        DurableAgentCheckpointV3,
        DurableCapabilityCallStateV1,
        DurableNextActionV2,
    )
    from app.assistant.provider_loop.messages import (
        ProviderAssistantMessage,
        ProviderToolCall,
        digest_arguments,
        digest_provider_transcript,
    )
    from app.assistant.policy.obligations import (
        build_reserved_obligation,
        create_initial_obligation_ledger_state,
        pure_create_obligation,
    )
    from app.assistant.models import Conversation
    from tests.assistant_runtime_support import make_main_agent_run
    conv = Conversation(title="t")
    db.add(conv)
    db.flush()
    run = make_main_agent_run(
        db,
        conversation=conv,
        status=status,
        build_revision="b1",
        runtime_contract_version=1,
        required_app_build_revision="b1",
        capability_ledger_mode="enforced",
        state_revision=2,
        lease_owner="worker-1",
        lease_generation=1,
        memory_commit_status="pending",
    )
    manifest = AssistantRunManifestRevision(
        run_id=run.id,
        revision=1,
        manifest_digest=DIGEST_A,
        schema_version=1,
        payload={},
    )
    db.add(manifest)
    db.flush()
    payload = os.urandom(8)
    art = AssistantRunArtifact(
        run_id=run.id,
        kind="call_input",
        media_type="application/json",
        storage_kind="inline",
        byte_size=len(payload),
        content_sha256=hashlib.sha256(payload).hexdigest(),
        inline_bytes=payload,
        metadata_json={},
    )
    db.add(art)
    db.flush()
    call = AssistantCapabilityCall(
        id=uuid.uuid4(),
        run_id=run.id,
        manifest_revision_id=manifest.id,
        logical_call_key=f"provider:0:0:{uuid.uuid4().hex[:8]}",
        owner_kind="main_agent",
        capability_type="tool",
        domain_key="external_write",
        descriptor_digest=DIGEST_A,
        authorization_digest=DIGEST_A,
        input_artifact_id=art.id,
        input_digest=DIGEST_A,
        side_effect_class=(
            "write_local" if mode == "local_transactional" else "write_external"
        ),
        execution_mode=mode,
        idempotency_key="idem-" + uuid.uuid4().hex,
        provider_tool_call_id=(
            provider_tool_call_id or "external-" + uuid.uuid4().hex
        ),
        status=status,
        state_revision=3,
        attempt_count=1,
        side_effect_started_at=(
            None if mode == "local_transactional" else datetime.now(timezone.utc)
        ),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(call)
    db.flush()
    attempt = AssistantCapabilityCallAttempt(
        call_id=call.id,
        attempt_number=1,
        worker_id="worker-1",
        lease_generation=1,
        status="uncertain",
        request_digest=DIGEST_A,
        error_code="transport_outcome_unknown",
        side_effect_started=True,
        side_effect_started_at=datetime.now(timezone.utc),
    )
    db.add(attempt)
    obligation_state, created = pure_create_obligation(
        create_initial_obligation_ledger_state(),
        build_reserved_obligation(
            run_id=run.id,
            obligation_type="reconciliation",
            owner_kind="capability_call",
            owner_id=str(call.id),
            source_call_id=str(call.id),
            revision=1,
        ),
    )
    assert created.allowed
    obligation_row = AssistantRunObligationRevision(
        run_id=run.id,
        revision=1,
        obligation_digest=obligation_state.ledger_digest,
        payload=obligation_state.model_dump(mode="json", by_alias=True),
    )
    db.add(obligation_row)
    db.flush()
    policy_row = AssistantRunPolicyRevision(
        run_id=run.id,
        revision=1,
        policy_digest="b" * 64,
        payload={},
    )
    budget_row = AssistantRunBudgetRevision(
        run_id=run.id,
        revision=1,
        budget_digest="c" * 64,
        payload={},
    )
    db.add_all([policy_row, budget_row])
    db.flush()
    provider_call = ProviderToolCall(
        call_id=str(call.provider_tool_call_id),
        call_index=0,
        provider_alias="external_tool",
        domain_key=str(call.domain_key),
        arguments={},
        arguments_digest=digest_arguments({}),
        binding_contract_digest=str(call.authorization_digest),
        descriptor_digest=str(call.descriptor_digest),
        behavior_digest="d" * 64,
        classification_revision="plan08-test",
        classification_ruleset_digest="e" * 64,
        manifest_revision=1,
        manifest_digest=str(manifest.manifest_digest),
        surface_digest="f" * 64,
    )
    assistant_message = ProviderAssistantMessage(content=None, tool_calls=(provider_call,))
    provider_rows = _build_provider_message_rows(
        run_id=run.id,
        messages=(assistant_message,),
        start_ordinal=1,
        manifest_revision_id=manifest.id,
        policy_revision_id=policy_row.id,
        obligation_revision_id=obligation_row.id,
    )
    checkpoint = DurableAgentCheckpointV3(
        run_id=run.id,
        phase="ready_for_provider",
        manifest_revision_id=manifest.id,
        policy_revision_id=policy_row.id,
        budget_revision_id=budget_row.id,
        obligation_revision_id=obligation_row.id,
        provider_message_ordinal=1,
        provider_transcript_digest=digest_provider_transcript((assistant_message,)),
        provider_loop_continuation=None,
        inflight_unit=None,
        capability_frames=(),
        artifact_ids=(art.id,),
        visible_text_artifact_id=None,
        next_action=DurableNextActionV2(kind="reconcile"),
        policy_contract_version=2,
        capability_calls=(
            DurableCapabilityCallStateV1(
                call_id=call.id,
                logical_call_key=str(call.logical_call_key),
                provider_tool_call_id=str(call.provider_tool_call_id),
                provider_order=0,
                status=str(call.status),
                attempt_id=attempt.id,
            ),
        ),
    )
    checkpoint_row = AssistantRunCheckpoint(
        run_id=run.id,
        sequence=1,
        expected_state_revision=1,
        committed_state_revision=2,
        schema_version=3,
        manifest_revision_id=manifest.id,
        policy_revision_id=policy_row.id,
        budget_revision_id=budget_row.id,
        obligation_revision_id=obligation_row.id,
        provider_message_ordinal=1,
        provider_transcript_digest=checkpoint.provider_transcript_digest,
        phase=checkpoint.phase,
        logical_unit_id=str(call.logical_call_key),
        reason="test_reconciliation_pending",
        state_payload=encode_checkpoint_v3(checkpoint),
        state_digest=checkpoint_state_digest(checkpoint),
    )
    db.add_all([*provider_rows, checkpoint_row])
    db.flush()
    run.current_manifest_revision_id = manifest.id
    run.current_policy_revision_id = policy_row.id
    run.current_budget_revision_id = budget_row.id
    run.current_obligation_revision_id = obligation_row.id
    run.current_checkpoint_id = checkpoint_row.id
    db.commit()
    db.refresh(call)
    return run, call, art


def _evidence_artifact(
    db,
    *,
    run_id,
    call_id,
    evidence_type: str,
    kind: str = "capability_call_evidence",
    metadata: dict | None = None,
    payload: bytes | None = None,
    decision: str | None = None,
    signed: bool = True,
):
    from app.assistant.capability_calls.models import (
        AssistantCapabilityCall,
        AssistantCapabilityCallAttempt,
    )
    from app.assistant.capability_calls.reconciliation import (
        CapabilityReconciliationService,
        HmacReconciliationEvidenceVerifier,
    )
    from app.assistant.domain.digests import sha256_bytes
    from app.assistant.durable.models import AssistantRunArtifact

    call = db.get(AssistantCapabilityCall, call_id)
    attempt = (
        db.query(AssistantCapabilityCallAttempt)
        .filter(AssistantCapabilityCallAttempt.call_id == call_id)
        .order_by(AssistantCapabilityCallAttempt.attempt_number.desc())
        .first()
    )
    inferred_decision = decision or {
        "capability_call_failure": "mark_failed",
        "capability_call_compensation": "mark_compensated",
        "capability_call_success_attestation": "mark_succeeded",
        "retry_authorization": "retry_same_key",
        "external_status_lookup": "retry_same_key",
    }.get(evidence_type, "mark_failed")
    evidence_metadata = {
        "contractVersion": 1,
        "evidenceType": evidence_type,
        "callId": str(call_id),
        **(metadata or {}),
    }
    if signed and call is not None:
        now = datetime.now(timezone.utc)
        claims = {
            "callId": str(call.id),
            "runId": str(call.run_id),
            "decision": inferred_decision,
            "evidenceType": evidence_type,
            "inputDigest": str(call.input_digest),
            "idempotencyKeyDigest": sha256_bytes(
                str(call.idempotency_key).encode("utf-8")
            ),
            "attempt": CapabilityReconciliationService._attempt_claim(attempt),
            "issuedAt": now.isoformat(),
            **(metadata or {}),
        }
        if str(call.execution_mode) == "local_transactional":
            from app.entry.models import Entry

            observed_entry = (
                db.query(Entry)
                .filter(Entry.source_capability_call_id == call.id)
                .one_or_none()
            )
            claims["entryObservation"] = {
                "kind": "present" if observed_entry is not None else "proven_absent",
                "entryId": (
                    str(observed_entry.id) if observed_entry is not None else None
                ),
            }
        if evidence_type == "capability_call_failure":
            claims.setdefault(
                "failureDisposition", "explicit_product_acceptance_unresolved"
            )
        if evidence_type == "capability_call_compensation":
            claims.setdefault("compensationStatus", "completed")
            claims.setdefault("compensationActionId", f"comp-{uuid.uuid4()}")
        if evidence_type in {"retry_authorization", "external_status_lookup"}:
            claims.setdefault("providerContract", "test-provider:v1")
            claims.setdefault("requestDigest", attempt.request_digest if attempt else None)
            claims.setdefault("maxAttempts", 3)
            claims.setdefault("remainingAttempts", 3 - int(call.attempt_count))
            claims.setdefault(
                "deadlineAt", (now + timedelta(minutes=5)).isoformat()
            )
        raw = HmacReconciliationEvidenceVerifier(EVIDENCE_SECRET).sign_claims(claims)
    else:
        raw = payload or json.dumps(
            evidence_metadata, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    if payload is not None:
        raw = payload
    artifact = AssistantRunArtifact(
        run_id=run_id,
        kind=kind,
        media_type="application/json",
        storage_kind="inline",
        byte_size=len(raw),
        content_sha256=hashlib.sha256(raw).hexdigest(),
        inline_bytes=raw,
        metadata_json=evidence_metadata,
    )
    db.add(artifact)
    db.flush()
    return artifact


def _add_pending_sibling_continuation(
    db, *, run, call, with_continuation=True, sibling_status="proposed"
):
    from tests.test_durable_checkpoint_codec import _waiting_continuation
    from app.assistant.capability_calls.models import AssistantCapabilityCall
    from app.assistant.durable.codec import (
        checkpoint_state_digest,
        decode_checkpoint,
        decode_provider_message,
        encode_checkpoint_v3,
        encode_provider_message,
    )
    from app.assistant.durable.contracts import (
        DurableCapabilityCallStateV1,
        DurableNextActionV2,
    )
    from app.assistant.durable.models import (
        AssistantRunCheckpoint,
        AssistantRunProviderMessage,
    )
    from app.assistant.provider_loop.contracts import create_execution_scope
    from app.assistant.provider_loop.messages import (
        digest_arguments,
        digest_provider_message,
        digest_provider_transcript,
    )

    sibling_provider_id = "pending-" + uuid.uuid4().hex
    sibling = AssistantCapabilityCall(
        run_id=run.id,
        manifest_revision_id=call.manifest_revision_id,
        logical_call_key=f"provider:pending:{uuid.uuid4().hex}",
        owner_kind="main_agent",
        capability_type="tool",
        domain_key=str(call.domain_key),
        descriptor_digest=str(call.descriptor_digest),
        authorization_digest=str(call.authorization_digest),
        input_artifact_id=call.input_artifact_id,
        input_digest=str(call.input_digest),
        side_effect_class="read",
        execution_mode="read_replayable",
        idempotency_key="idem-" + uuid.uuid4().hex,
        provider_tool_call_id=sibling_provider_id,
        status=sibling_status,
        state_revision=1 if sibling_status == "denied" else 0,
        failure_code="policy_denied" if sibling_status == "denied" else None,
        attempt_count=0,
    )
    db.add(sibling)
    db.flush()
    message_row = (
        db.query(AssistantRunProviderMessage)
        .filter(AssistantRunProviderMessage.run_id == run.id)
        .one()
    )
    assistant = decode_provider_message(message_row.payload_body)
    first = assistant.tool_calls[0]
    sibling_tool_call = first.model_copy(
        update={
            "call_id": sibling_provider_id,
            "call_index": 1,
            "arguments": {"pending": True},
            "arguments_digest": digest_arguments({"pending": True}),
        }
    )
    assistant = assistant.model_copy(
        update={"tool_calls": (first, sibling_tool_call)}
    )
    message_row.payload_body = encode_provider_message(assistant)
    message_row.content_digest = digest_provider_message(assistant)
    checkpoint_row = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
    checkpoint = decode_checkpoint(checkpoint_row.state_payload)
    base = _waiting_continuation()
    scope = create_execution_scope(
        run_id=run.id,
        conversation_id=base.execution_scope.conversation_id,
        principal=base.execution_scope.principal,
        tenant_scope_id=base.execution_scope.tenant_scope_id,
    )
    waiting = base.waiting_call.model_copy(
        update={
            "call_id": str(call.provider_tool_call_id),
            "call_index": 0,
            "binding_contract_digest": first.binding_contract_digest,
            "descriptor_digest": first.descriptor_digest,
            "behavior_digest": first.behavior_digest,
            "classification_revision": first.classification_revision,
            "classification_ruleset_digest": first.classification_ruleset_digest,
        }
    )
    transcript_digest = digest_provider_transcript((assistant,))
    continuation = base.model_copy(
        update={
            "execution_scope": scope,
            "assistant_message_digest": digest_provider_message(assistant),
            "transcript_digest": transcript_digest,
            "waiting_call": waiting,
            "pending_call_ids": (sibling_provider_id,),
        }
    )
    sibling_state = DurableCapabilityCallStateV1(
        call_id=sibling.id,
        logical_call_key=str(sibling.logical_call_key),
        provider_tool_call_id=sibling_provider_id,
        provider_order=1,
        status=sibling_status,
    )
    updated = checkpoint.model_copy(
        update={
            "phase": "waiting" if with_continuation else checkpoint.phase,
            "provider_transcript_digest": transcript_digest,
            "provider_loop_continuation": continuation if with_continuation else None,
            "next_action": (
                DurableNextActionV2(kind="wait")
                if with_continuation
                else checkpoint.next_action
            ),
            "capability_calls": (*checkpoint.capability_calls, sibling_state),
        }
    )
    checkpoint_row.phase = "waiting" if with_continuation else checkpoint.phase
    checkpoint_row.provider_transcript_digest = transcript_digest
    checkpoint_row.state_payload = encode_checkpoint_v3(updated)
    checkpoint_row.state_digest = checkpoint_state_digest(updated)
    db.commit()
    db.refresh(sibling)
    return sibling


def _evidence_verifier():
    from app.assistant.capability_calls.reconciliation import (
        HmacReconciliationEvidenceVerifier,
    )

    return HmacReconciliationEvidenceVerifier(EVIDENCE_SECRET)


def _trusted_authorizer(actor_id: uuid.UUID | None = None):
    from app.assistant.capability_calls.reconciliation import (
        AuthorizedReconciliationActor,
    )

    actor = actor_id or uuid.uuid4()

    def authorize(_request):
        return AuthorizedReconciliationActor(
            actor_admin_id=actor,
            authorization_method="configured_operator",
        )

    return authorize


class ScriptedAdapterTests(unittest.TestCase):
    def test_scripted_outcomes_classify(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            ScriptedExternalAdapter,
            ScriptedExternalOutcome,
        )

        adapter = ScriptedExternalAdapter(
            [
                ScriptedExternalOutcome(kind="before_send_refusal"),
                ScriptedExternalOutcome(kind="accepted_then_timeout"),
                ScriptedExternalOutcome(kind="key_echo_success", echo_key="k"),
                ScriptedExternalOutcome(kind="ambiguous_5xx", status_code=503),
                ScriptedExternalOutcome(kind="non_retriable_uncertain"),
            ]
        )
        o1 = adapter.send(idempotency_key="k", payload={})
        self.assertEqual(adapter.classify_for_ledger(o1), "failed")
        o2 = adapter.send(idempotency_key="k", payload={})
        self.assertEqual(adapter.classify_for_ledger(o2), "unknown")
        o3 = adapter.send(idempotency_key="k", payload={})
        self.assertEqual(adapter.classify_for_ledger(o3), "succeeded")
        o4 = adapter.send(idempotency_key="k", payload={})
        self.assertEqual(adapter.classify_for_ledger(o4), "unknown")
        o5 = adapter.send(idempotency_key="k", payload={})
        self.assertEqual(adapter.classify_for_ledger(o5), "unknown")


class ReconciliationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_mark_failed_and_idempotent_resolution_request(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        run, call, _art = _seed_external_call(self.db)
        art = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        actor_id = uuid.uuid4()
        svc = CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(actor_id),
            evidence_verifier=_evidence_verifier(),
        )
        rid = uuid.uuid4()
        req = ReconciliationDecisionRequest(
            call_id=call.id,
            expected_call_revision=3,
            expected_run_revision=2,
            decision="mark_failed",
            reason="status lookup not_found",
            evidence_artifact_ids=(art.id,),
            resolution_request_id=rid,
        )
        r1 = svc.apply(req)
        self.db.commit()
        self.assertTrue(r1.created)
        self.assertEqual(r1.resulting_call_status, "failed")
        self.db.refresh(run)
        self.assertEqual(run.status, "failed")
        self.assertIsNone(run.lease_owner)
        from app.assistant.durable.codec import decode_checkpoint, decode_provider_message
        from app.assistant.durable.models import (
            AssistantRunCheckpoint,
            AssistantRunObligationRevision,
            AssistantRunProviderMessage,
        )

        resolved_obligation = self.db.get(
            AssistantRunObligationRevision, run.current_obligation_revision_id
        )
        self.assertEqual(
            resolved_obligation.payload["obligations"][0]["status"], "satisfied"
        )
        rows = svc.list_for_run(run.id)
        self.assertEqual(rows[0].actor_admin_id, actor_id)
        self.assertEqual(rows[0].actor_user_id, None)
        self.assertEqual(
            rows[0].authorization_evidence["authorizationMethod"],
            "configured_operator",
        )
        self.assertNotIn(
            "statusLookupProvedNotAccepted", rows[0].authorization_evidence
        )
        self.assertEqual(
            rows[0].authorization_evidence["verifiedClaims"][0]["evidenceType"],
            "capability_call_failure",
        )
        provider_rows = (
            self.db.query(AssistantRunProviderMessage)
            .filter(AssistantRunProviderMessage.run_id == run.id)
            .order_by(AssistantRunProviderMessage.ordinal.asc())
            .all()
        )
        self.assertEqual([row.role for row in provider_rows], ["assistant", "tool"])
        self.assertEqual(provider_rows[-1].tool_call_id, str(call.provider_tool_call_id))
        tool_message = decode_provider_message(provider_rows[-1].payload_body)
        self.assertEqual(tool_message.call_id, str(call.provider_tool_call_id))
        self.assertEqual(tool_message.content.status, "failed")
        self.db.refresh(call)
        self.assertIsNotNone(call.output_artifact_id)
        checkpoint_row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        checkpoint = decode_checkpoint(checkpoint_row.state_payload)
        self.assertEqual(checkpoint.phase, "terminal")
        self.assertEqual(checkpoint.next_action.kind, "terminal")
        call_state = next(item for item in checkpoint.capability_calls if item.call_id == call.id)
        self.assertEqual(call_state.result_message_digest, provider_rows[-1].content_digest)
        self.assertNotEqual(
            call_state.result_message_digest,
            str(self.db.get(type(_art), call.output_artifact_id).content_sha256),
        )
        call.status = "compensated"
        self.db.commit()
        r2 = svc.apply(req)
        self.assertFalse(r2.created)
        self.assertEqual(r2.reconciliation_id, r1.reconciliation_id)
        self.assertEqual(r2.resulting_call_status, "failed")
        with self.assertRaises(CapabilityCallConflict):
            CapabilityReconciliationService(
                self.db,
                operator_authorizer=_trusted_authorizer(uuid.uuid4()),
                evidence_verifier=_evidence_verifier(),
            ).apply(req)

    def test_retry_same_key_forbidden_for_local_transactional(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        # Seed as external first (allows side_effect_started_at), then rewrite
        # mode/status to local_transactional without effect-start for the matrix.
        run, call, _art = _seed_external_call(
            self.db, mode="external_idempotent", status="needs_reconciliation"
        )
        art = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="retry_authorization",
        )
        call.execution_mode = "local_transactional"
        call.side_effect_class = "write_local"
        call.side_effect_started_at = None
        self.db.commit()
        self.db.refresh(call)
        self.db.refresh(run)
        svc = CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        )
        with self.assertRaises(CapabilityCallConflict) as ctx:
            svc.apply(
                ReconciliationDecisionRequest(
                    call_id=call.id,
                    expected_call_revision=int(call.state_revision),
                    expected_run_revision=int(run.state_revision),
                    decision="retry_same_key",
                    reason="should fail",
                    evidence_artifact_ids=(art.id,),
                    resolution_request_id=uuid.uuid4(),
                )
            )
        self.assertIn("forbidden", ctx.exception.message)

    def test_external_idempotent_retry_same_key(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )

        run, call, _art = _seed_external_call(
            self.db, mode="external_idempotent", status="needs_reconciliation"
        )
        art = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="retry_authorization",
        )
        svc = CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        )
        result = svc.apply(
            ReconciliationDecisionRequest(
                call_id=call.id,
                expected_call_revision=3,
                expected_run_revision=2,
                decision="retry_same_key",
                reason="operator approved same-key retry",
                evidence_artifact_ids=(art.id,),
                resolution_request_id=uuid.uuid4(),
            )
        )
        self.db.commit()
        self.assertEqual(result.resulting_call_status, "authorized")
        from app.assistant.capability_calls.models import AssistantCapabilityReconciliation

        row = self.db.query(AssistantCapabilityReconciliation).one()
        claim = row.authorization_evidence["verifiedClaims"][0]
        self.assertEqual(claim["requestDigest"], DIGEST_A)
        self.assertEqual(claim["maxAttempts"], 3)
        self.assertEqual(claim["remainingAttempts"], 2)
        self.assertIn("deadlineAt", claim)
        from app.assistant.capability_calls.reconciliation import (
            validate_retry_authorization_for_dispatch,
        )

        self.db.refresh(call)
        validated = validate_retry_authorization_for_dispatch(
            self.db, call=call, now=datetime.now(timezone.utc)
        )
        self.assertEqual(validated["maxAttempts"], 3)
        expired = dict(row.authorization_evidence)
        expired_claims = [dict(item) for item in expired["verifiedClaims"]]
        expired_claims[0]["deadlineAt"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        expired["verifiedClaims"] = expired_claims
        row.authorization_evidence = expired
        self.db.commit()
        with self.assertRaisesRegex(Exception, "expired"):
            validate_retry_authorization_for_dispatch(
                self.db, call=call, now=datetime.now(timezone.utc)
            )

    def test_reconciliation_requires_checkpoint_and_exact_pending_obligation(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        run, call, _ = _seed_external_call(self.db)
        evidence = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        self.db.commit()
        service = CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        )
        request = ReconciliationDecisionRequest(
            call_id=call.id,
            expected_call_revision=3,
            expected_run_revision=2,
            decision="mark_failed",
            reason="must be durably pending",
            evidence_artifact_ids=(evidence.id,),
            resolution_request_id=uuid.uuid4(),
        )
        checkpoint_id = run.current_checkpoint_id
        run.current_checkpoint_id = None
        self.db.commit()
        with self.assertRaises(CapabilityCallConflict):
            service.apply(request)
        self.db.rollback()
        run.current_checkpoint_id = checkpoint_id
        run.current_obligation_revision_id = None
        self.db.commit()
        with self.assertRaises(CapabilityCallConflict):
            service.apply(request)

    def test_other_pending_obligation_keeps_run_reconcilable(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.durable.models import AssistantRunObligationRevision
        from app.assistant.policy.obligations import (
            ObligationLedgerState,
            build_reserved_obligation,
            pure_create_obligation,
        )

        run, call, _ = _seed_external_call(self.db)
        current = self.db.get(
            AssistantRunObligationRevision, run.current_obligation_revision_id
        )
        state = ObligationLedgerState.model_validate(current.payload)
        state, created = pure_create_obligation(
            state,
            build_reserved_obligation(
                run_id=run.id,
                obligation_type="user_input",
                owner_kind="main_agent",
                owner_id=str(run.id),
                source_call_id=None,
                revision=2,
            ),
        )
        self.assertTrue(created.allowed)
        current.payload = state.model_dump(mode="json", by_alias=True)
        current.obligation_digest = state.ledger_digest
        evidence = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        self.db.commit()
        result = CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        ).apply(
            ReconciliationDecisionRequest(
                call_id=call.id,
                expected_call_revision=3,
                expected_run_revision=2,
                decision="mark_failed",
                reason="resolve this Call without dropping unrelated work",
                evidence_artifact_ids=(evidence.id,),
                resolution_request_id=uuid.uuid4(),
            )
        )
        self.assertTrue(result.created)
        self.db.refresh(call)
        self.db.refresh(run)
        self.assertEqual(call.status, "failed")
        self.assertEqual(run.status, "needs_reconciliation")
        resolved = self.db.get(
            AssistantRunObligationRevision, run.current_obligation_revision_id
        )
        statuses = [item["status"] for item in resolved.payload["obligations"]]
        self.assertIn("pending", statuses)
        self.assertIn("satisfied", statuses)

    def test_local_commit_ambiguity_is_persisted_and_not_auto_retried(self) -> None:
        from app.assistant.capability_calls.settlement import (
            CapabilityCallSettlementRepository,
        )
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.durable.models import (
            AssistantRunCheckpoint,
            AssistantRunObligationRevision,
        )
        from app.assistant.durable.codec import decode_checkpoint
        from app.assistant.policy.obligations import ObligationLedgerState

        run, call, _ = _seed_external_call(
            self.db,
            mode="local_transactional",
            status="needs_reconciliation",
        )
        # Recreate the pre-boundary Run snapshot: the helper builds a valid v3
        # checkpoint/obligation bundle for a reconcilable local Call, while
        # this test exercises the durable unknown quarantine transition.
        run.status = "running"
        run.state_revision = int(run.state_revision) + 1
        self.db.commit()
        settlement = CapabilityCallSettlementRepository(self.db)
        result = settlement.mark_local_commit_outcome_unknown(
            call_id=call.id,
            failure_code="local_commit_outcome_unknown",
        )
        self.assertEqual(result.status, "needs_reconciliation")
        self.db.refresh(call)
        self.db.refresh(run)
        self.assertEqual(call.status, "needs_reconciliation")
        self.assertEqual(run.status, "needs_reconciliation")
        self.assertEqual(call.attempt_count, 1)
        self.assertIsNone(call.output_artifact_id)
        self.assertEqual(
            self.db.query(AssistantRunCheckpoint)
            .filter_by(id=run.current_checkpoint_id, schema_version=3)
            .count(),
            1,
        )
        checkpoint = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        state = decode_checkpoint(checkpoint.state_payload)
        self.assertEqual(state.capability_calls[0].status, "needs_reconciliation")
        obligations = self.db.get(
            AssistantRunObligationRevision, run.current_obligation_revision_id
        )
        # The quarantine obligation itself remains pending until a signed
        # Operator evidence decision; no Entry/result Artifact was fabricated.
        ledger = ObligationLedgerState.model_validate(obligations.payload)
        self.assertEqual(
            [
                item.status
                for item in ledger.obligations
                if item.obligation_type == "reconciliation"
                and item.source_call_id == str(call.id)
            ],
            ["pending"],
        )
        self.assertEqual(
            self.db.query(AssistantCapabilityCall).filter_by(
                id=call.id, output_artifact_id=None
            ).count(),
            1,
        )

    def test_reconciliation_requires_exact_owner_and_source_binding(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.durable.models import AssistantRunObligationRevision
        from app.assistant.policy.obligations import ObligationLedgerState

        run, call, _ = _seed_external_call(self.db)
        obligation = self.db.get(
            AssistantRunObligationRevision, run.current_obligation_revision_id
        )
        ledger = ObligationLedgerState.model_validate(obligation.payload)
        target = next(item for item in ledger.obligations if item.status == "pending")
        replaced = target.model_copy(update={"source_call_id": str(uuid.uuid4())})
        ledger = ledger.model_copy(
            update={
                "obligations": tuple(
                    replaced if item is target else item for item in ledger.obligations
                )
            }
        )
        obligation.payload = ledger.model_dump(mode="json", by_alias=True)
        obligation.obligation_digest = ledger.ledger_digest
        evidence = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        self.db.commit()

        with self.assertRaises(CapabilityCallConflict):
            CapabilityReconciliationService(
                self.db,
                operator_authorizer=_trusted_authorizer(),
                evidence_verifier=_evidence_verifier(),
            ).apply(
                ReconciliationDecisionRequest(
                    call_id=call.id,
                    expected_call_revision=3,
                    expected_run_revision=2,
                    decision="mark_failed",
                    reason="reject mismatched obligation source",
                    evidence_artifact_ids=(evidence.id,),
                    resolution_request_id=uuid.uuid4(),
                )
            )
        self.db.rollback()
    def test_retry_same_key_is_forbidden_from_terminal_checkpoint(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict
        from app.assistant.durable.codec import (
            checkpoint_state_digest,
            decode_checkpoint,
            encode_checkpoint_v3,
        )
        from app.assistant.durable.contracts import DurableNextActionV2
        from app.assistant.durable.models import AssistantRunCheckpoint
        from app.assistant.durable.contracts import DurableNextActionV2

        run, call, _ = _seed_external_call(self.db)
        row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        checkpoint = decode_checkpoint(row.state_payload).model_copy(
            update={
                "phase": "terminal",
                "next_action": DurableNextActionV2(kind="reconcile"),
            }
        )
        row.phase = "terminal"
        row.state_payload = encode_checkpoint_v3(checkpoint)
        row.state_digest = checkpoint_state_digest(checkpoint)
        evidence = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="retry_authorization",
        )
        self.db.commit()
        with self.assertRaises(CapabilityCallConflict):
            CapabilityReconciliationService(
                self.db,
                operator_authorizer=_trusted_authorizer(),
                evidence_verifier=_evidence_verifier(),
            ).apply(
                ReconciliationDecisionRequest(
                    call_id=call.id,
                    expected_call_revision=3,
                    expected_run_revision=2,
                    decision="retry_same_key",
                    reason="terminal cannot redispatch",
                    evidence_artifact_ids=(evidence.id,),
                    resolution_request_id=uuid.uuid4(),
                )
            )

    def test_terminal_decision_with_continuation_queues_continue_provider(self) -> None:
        from tests.test_durable_checkpoint_codec import _waiting_continuation
        from app.assistant.durable.checkpoints import _current_transcript_digest
        from app.assistant.provider_loop.contracts import create_execution_scope
        from app.assistant.provider_loop.messages import (
            ProviderAssistantMessage,
            digest_provider_message,
        )
        from app.assistant.durable.contracts import DurableNextActionV2
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.durable.codec import (
            checkpoint_state_digest,
            decode_checkpoint,
            encode_checkpoint_v3,
        )
        from app.assistant.durable.models import AssistantRunCheckpoint

        run, call, _ = _seed_external_call(
            self.db, provider_tool_call_id="call-1"
        )
        row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        current = decode_checkpoint(row.state_payload)
        _ordinal, _digest, prior = _current_transcript_digest(self.db, run.id)
        assistant = next(
            message for message in prior if isinstance(message, ProviderAssistantMessage)
        )
        provider_call = assistant.tool_calls[0]
        base_continuation = _waiting_continuation()
        execution_scope = create_execution_scope(
            run_id=run.id,
            conversation_id=base_continuation.execution_scope.conversation_id,
            principal=base_continuation.execution_scope.principal,
            tenant_scope_id=base_continuation.execution_scope.tenant_scope_id,
        )
        waiting_state = base_continuation.waiting_call.model_copy(
            update={
                "call_id": provider_call.call_id,
                "call_index": provider_call.call_index,
                "binding_contract_digest": provider_call.binding_contract_digest,
                "descriptor_digest": provider_call.descriptor_digest,
                "behavior_digest": provider_call.behavior_digest,
                "classification_revision": provider_call.classification_revision,
                "classification_ruleset_digest": provider_call.classification_ruleset_digest,
            }
        )
        continuation = base_continuation.model_copy(
            update={
                "execution_scope": execution_scope,
                "transcript_digest": current.provider_transcript_digest,
                "assistant_message_digest": digest_provider_message(assistant),
                "waiting_call": waiting_state,
            }
        )
        updated = current.model_copy(
            update={
                "phase": "waiting",
                "provider_loop_continuation": continuation,
                "next_action": DurableNextActionV2(kind="wait"),
            }
        )
        row.phase = "waiting"
        row.state_payload = encode_checkpoint_v3(updated)
        row.state_digest = checkpoint_state_digest(updated)
        evidence = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        self.db.commit()
        CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        ).apply(
            ReconciliationDecisionRequest(
                call_id=call.id,
                expected_call_revision=3,
                expected_run_revision=2,
                decision="mark_failed",
                reason="continue after reconciled failure",
                evidence_artifact_ids=(evidence.id,),
                resolution_request_id=uuid.uuid4(),
            )
        )
        self.db.commit()
        self.db.refresh(run)
        self.assertEqual(run.status, "queued")
        final_row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        final = decode_checkpoint(final_row.state_payload)
        self.assertEqual(final.phase, "ready_for_provider")
        self.assertEqual(final.next_action.kind, "continue_provider")

    def test_terminal_reconciliation_closes_unstarted_pending_sibling(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.durable.checkpoints import _current_transcript_digest
        from app.assistant.durable.codec import decode_checkpoint
        from app.assistant.durable.models import AssistantRunCheckpoint
        from app.assistant.provider_loop.messages import (
            ProviderToolMessage,
            digest_provider_message,
            validate_provider_transcript,
        )

        run, call, _ = _seed_external_call(self.db)
        sibling = _add_pending_sibling_continuation(
            self.db, run=run, call=call
        )
        evidence = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        self.db.commit()
        CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        ).apply(
            ReconciliationDecisionRequest(
                call_id=call.id,
                expected_call_revision=3,
                expected_run_revision=2,
                decision="mark_failed",
                reason="close the remaining unstarted sibling",
                evidence_artifact_ids=(evidence.id,),
                resolution_request_id=uuid.uuid4(),
            )
        )
        self.db.commit()
        self.db.refresh(run)
        self.db.refresh(sibling)
        self.assertEqual(run.status, "queued")
        self.assertEqual(sibling.status, "cancelled")
        _ordinal, _digest, transcript = _current_transcript_digest(self.db, run.id)
        validate_provider_transcript(transcript)
        tool_messages = [
            item for item in transcript if isinstance(item, ProviderToolMessage)
        ]
        self.assertEqual(len(tool_messages), 2)
        self.assertEqual(tool_messages[-1].content.status, "cancelled_before_start")
        checkpoint_row = self.db.get(
            AssistantRunCheckpoint, run.current_checkpoint_id
        )
        checkpoint = decode_checkpoint(checkpoint_row.state_payload)
        self.assertEqual(checkpoint.phase, "ready_for_provider")
        self.assertEqual(checkpoint.next_action.kind, "continue_provider")
        self.assertIsNone(checkpoint.provider_loop_continuation)
        states = {item.call_id: item for item in checkpoint.capability_calls}
        self.assertEqual(states[sibling.id].status, "cancelled")
        self.assertEqual(
            states[sibling.id].result_message_digest,
            digest_provider_message(tool_messages[-1]),
        )

    def test_terminal_reconciliation_with_unrelated_pending_obligation_clears_continuation(
        self,
    ) -> None:
        """A sealed Provider suffix cannot leave a continuation for the failed Call."""
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.durable.checkpoints import _current_transcript_digest
        from app.assistant.durable.codec import decode_checkpoint
        from app.assistant.durable.models import (
            AssistantRunCheckpoint,
            AssistantRunObligationRevision,
        )
        from app.assistant.policy.obligations import (
            ObligationLedgerState,
            build_reserved_obligation,
            pure_create_obligation,
        )

        run, call, _ = _seed_external_call(self.db)
        sibling = _add_pending_sibling_continuation(
            self.db, run=run, call=call
        )
        current_obligation = self.db.get(
            AssistantRunObligationRevision, run.current_obligation_revision_id
        )
        ledger = ObligationLedgerState.model_validate(current_obligation.payload)
        ledger, created = pure_create_obligation(
            ledger,
            build_reserved_obligation(
                run_id=run.id,
                obligation_type="user_input",
                owner_kind="main_agent",
                owner_id=str(run.id),
                source_call_id=None,
                revision=int(current_obligation.revision) + 1,
            ),
        )
        self.assertTrue(created.allowed)
        current_obligation.payload = ledger.model_dump(mode="json", by_alias=True)
        current_obligation.obligation_digest = ledger.ledger_digest
        evidence = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        self.db.commit()

        CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        ).apply(
            ReconciliationDecisionRequest(
                call_id=call.id,
                expected_call_revision=3,
                expected_run_revision=2,
                decision="mark_failed",
                reason="seal the Provider suffix while unrelated work remains",
                evidence_artifact_ids=(evidence.id,),
                resolution_request_id=uuid.uuid4(),
            )
        )
        self.db.commit()

        self.db.refresh(run)
        self.db.refresh(sibling)
        self.assertEqual(run.status, "needs_reconciliation")
        self.assertEqual(sibling.status, "cancelled")
        final_row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        final = decode_checkpoint(final_row.state_payload)
        self.assertEqual(final.phase, "terminal")
        self.assertEqual(final.next_action.kind, "reconcile")
        self.assertIsNone(final.provider_loop_continuation)
        _ordinal, transcript_digest, _transcript = _current_transcript_digest(
            self.db, run.id
        )
        self.assertEqual(final.provider_transcript_digest, transcript_digest)

    def test_terminal_reconciliation_closes_reserved_sibling_without_continuation(
        self,
    ) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.durable.checkpoints import _current_transcript_digest
        from app.assistant.provider_loop.messages import validate_provider_transcript

        run, call, _ = _seed_external_call(self.db)
        sibling = _add_pending_sibling_continuation(
            self.db, run=run, call=call, with_continuation=False
        )
        evidence = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        self.db.commit()

        CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        ).apply(
            ReconciliationDecisionRequest(
                call_id=call.id,
                expected_call_revision=3,
                expected_run_revision=2,
                decision="mark_failed",
                reason="close an ordinary reserved sibling",
                evidence_artifact_ids=(evidence.id,),
                resolution_request_id=uuid.uuid4(),
            )
        )
        self.db.commit()

        self.db.refresh(sibling)
        self.assertEqual(sibling.status, "cancelled")
        _ordinal, _digest, transcript = _current_transcript_digest(self.db, run.id)
        validate_provider_transcript(transcript)

    def test_terminal_decision_closes_unstarted_pending_siblings(self) -> None:
        from tests.test_durable_checkpoint_codec import _waiting_continuation
        from app.assistant.capability_calls.models import AssistantCapabilityCall
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.durable.checkpoints import _current_transcript_digest
        from app.assistant.durable.codec import (
            checkpoint_state_digest,
            decode_checkpoint,
            encode_checkpoint_v3,
            encode_provider_message,
        )
        from app.assistant.durable.contracts import (
            DurableCapabilityCallStateV1,
            DurableNextActionV2,
        )
        from app.assistant.durable.models import (
            AssistantRunCheckpoint,
            AssistantRunProviderMessage,
        )
        from app.assistant.provider_loop.contracts import create_execution_scope
        from app.assistant.provider_loop.messages import (
            ProviderAssistantMessage,
            digest_provider_message,
            digest_provider_transcript,
            validate_provider_transcript,
        )

        run, call, _ = _seed_external_call(
            self.db, provider_tool_call_id="call-1"
        )
        row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        current = decode_checkpoint(row.state_payload)
        _ordinal, _digest, prior = _current_transcript_digest(self.db, run.id)
        assistant = next(
            message for message in prior if isinstance(message, ProviderAssistantMessage)
        )
        waiting_provider_call = assistant.tool_calls[0]
        pending_provider_call = waiting_provider_call.model_copy(
            update={"call_id": "call-2", "call_index": 1}
        )
        expanded_assistant = assistant.model_copy(
            update={"tool_calls": (waiting_provider_call, pending_provider_call)}
        )
        assistant_row = (
            self.db.query(AssistantRunProviderMessage)
            .filter_by(run_id=run.id, role="assistant")
            .one()
        )
        assistant_row.payload_body = encode_provider_message(expanded_assistant)
        assistant_row.content_digest = digest_provider_message(expanded_assistant)

        sibling = AssistantCapabilityCall(
            run_id=run.id,
            manifest_revision_id=call.manifest_revision_id,
            logical_call_key="provider:pending:call-2",
            owner_kind="main_agent",
            capability_type=call.capability_type,
            domain_key=call.domain_key,
            descriptor_digest=call.descriptor_digest,
            authorization_digest=call.authorization_digest,
            input_artifact_id=call.input_artifact_id,
            input_digest=call.input_digest,
            side_effect_class=call.side_effect_class,
            execution_mode=call.execution_mode,
            idempotency_key="idem-pending-call-2",
            provider_tool_call_id="call-2",
            status="proposed",
            state_revision=0,
            attempt_count=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(sibling)
        self.db.flush()

        base_continuation = _waiting_continuation()
        execution_scope = create_execution_scope(
            run_id=run.id,
            conversation_id=base_continuation.execution_scope.conversation_id,
            principal=base_continuation.execution_scope.principal,
            tenant_scope_id=base_continuation.execution_scope.tenant_scope_id,
        )
        waiting_state = base_continuation.waiting_call.model_copy(
            update={
                "call_id": waiting_provider_call.call_id,
                "call_index": waiting_provider_call.call_index,
                "binding_contract_digest": waiting_provider_call.binding_contract_digest,
                "descriptor_digest": waiting_provider_call.descriptor_digest,
                "behavior_digest": waiting_provider_call.behavior_digest,
                "classification_revision": waiting_provider_call.classification_revision,
                "classification_ruleset_digest": waiting_provider_call.classification_ruleset_digest,
            }
        )
        expanded_transcript = (expanded_assistant,)
        continuation = base_continuation.model_copy(
            update={
                "execution_scope": execution_scope,
                "assistant_message_digest": digest_provider_message(expanded_assistant),
                "transcript_digest": digest_provider_transcript(expanded_transcript),
                "waiting_call": waiting_state,
                "next_call_index": 1,
                "pending_call_ids": ("call-2",),
            }
        )
        updated = current.model_copy(
            update={
                "phase": "waiting",
                "provider_loop_continuation": continuation,
                "provider_transcript_digest": continuation.transcript_digest,
                "next_action": DurableNextActionV2(kind="wait"),
                "capability_calls": (
                    *current.capability_calls,
                    DurableCapabilityCallStateV1(
                        call_id=sibling.id,
                        logical_call_key=sibling.logical_call_key,
                        provider_tool_call_id="call-2",
                        provider_order=1,
                        status="proposed",
                    ),
                ),
            }
        )
        row.phase = "waiting"
        row.provider_transcript_digest = continuation.transcript_digest
        row.state_payload = encode_checkpoint_v3(updated)
        row.state_digest = checkpoint_state_digest(updated)
        evidence = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        self.db.commit()

        CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        ).apply(
            ReconciliationDecisionRequest(
                call_id=call.id,
                expected_call_revision=3,
                expected_run_revision=2,
                decision="mark_failed",
                reason="close waiting call and unstarted sibling",
                evidence_artifact_ids=(evidence.id,),
                resolution_request_id=uuid.uuid4(),
            )
        )
        self.db.commit()
        self.db.refresh(sibling)
        self.assertEqual(sibling.status, "cancelled")
        self.assertEqual(sibling.attempt_count, 0)
        final_row = self.db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        final = decode_checkpoint(final_row.state_payload)
        self.assertEqual(final.phase, "ready_for_provider")
        self.assertEqual(final.next_action.kind, "continue_provider")
        self.assertIsNone(final.provider_loop_continuation)
        sibling_state = next(
            state for state in final.capability_calls if state.call_id == sibling.id
        )
        self.assertEqual(sibling_state.status, "cancelled")
        self.assertIsNotNone(sibling_state.result_message_digest)
        _ordinal, _digest, final_transcript = _current_transcript_digest(
            self.db, run.id
        )
        validate_provider_transcript(final_transcript)
        self.assertEqual(
            [message.call_id for message in final_transcript if message.role == "tool"],
            ["call-1", "call-2"],
        )

    def test_resolution_request_id_is_scoped_to_run_not_call(self) -> None:
        from app.assistant.capability_calls.models import (
            AssistantCapabilityCall,
            AssistantCapabilityReconciliation,
        )
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        run, call, input_artifact = _seed_external_call(self.db)
        other = AssistantCapabilityCall(
            run_id=run.id,
            manifest_revision_id=call.manifest_revision_id,
            logical_call_key=f"provider:other:{uuid.uuid4().hex}",
            owner_kind="main_agent",
            capability_type="tool",
            domain_key="external_write",
            descriptor_digest=DIGEST_A,
            authorization_digest=DIGEST_A,
            input_artifact_id=input_artifact.id,
            input_digest=DIGEST_A,
            side_effect_class="write_external",
            execution_mode="external_idempotent",
            idempotency_key="idem-" + uuid.uuid4().hex,
            provider_tool_call_id="other-" + uuid.uuid4().hex,
            status="failed",
            state_revision=4,
            attempt_count=1,
        )
        self.db.add(other)
        self.db.flush()
        resolution_id = uuid.uuid4()
        self.db.add(
            AssistantCapabilityReconciliation(
                call_id=other.id,
                run_id=run.id,
                revision=1,
                decision="mark_failed",
                actor_admin_id=uuid.uuid4(),
                authorization_evidence={"verifiedClaims": []},
                reason="existing other call decision",
                evidence_artifact_ids=[],
                expected_call_revision=3,
                expected_run_revision=1,
                resulting_call_revision=4,
                resulting_run_revision=2,
                resolution_request_id=resolution_id,
            )
        )
        evidence = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        self.db.commit()
        with self.assertRaises(CapabilityCallConflict) as ctx:
            CapabilityReconciliationService(
                self.db,
                operator_authorizer=_trusted_authorizer(),
                evidence_verifier=_evidence_verifier(),
            ).apply(
                ReconciliationDecisionRequest(
                    call_id=call.id,
                    expected_call_revision=3,
                    expected_run_revision=2,
                    decision="mark_failed",
                    reason="must conflict stably",
                    evidence_artifact_ids=(evidence.id,),
                    resolution_request_id=resolution_id,
                )
            )
        self.assertIn("another Call", ctx.exception.message)

    def test_external_reconcilable_requires_lookup_proof(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        run, call, _art = _seed_external_call(
            self.db, mode="external_reconcilable", status="needs_reconciliation"
        )
        unrelated = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="retry_authorization",
        )
        status_lookup = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="external_status_lookup",
            metadata={"providerStatus": "not_accepted"},
        )
        svc = CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        )
        with self.assertRaises(CapabilityCallConflict):
            svc.apply(
                ReconciliationDecisionRequest(
                    call_id=call.id,
                    expected_call_revision=3,
                    expected_run_revision=2,
                    decision="retry_same_key",
                    reason="missing lookup",
                    evidence_artifact_ids=(unrelated.id,),
                    resolution_request_id=uuid.uuid4(),
                )
            )
        result = svc.apply(
            ReconciliationDecisionRequest(
                call_id=call.id,
                expected_call_revision=3,
                expected_run_revision=2,
                decision="retry_same_key",
                reason="lookup proved not_accepted",
                evidence_artifact_ids=(status_lookup.id,),
                resolution_request_id=uuid.uuid4(),
            )
        )
        self.db.commit()
        self.assertEqual(result.resulting_call_status, "authorized")

    def test_reconciliation_denies_without_trusted_operator(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        run, call, _ = _seed_external_call(self.db)
        art = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        with self.assertRaises(CapabilityCallConflict) as ctx:
            CapabilityReconciliationService(
                self.db, evidence_verifier=_evidence_verifier()
            ).apply(
                ReconciliationDecisionRequest(
                    call_id=call.id,
                    expected_call_revision=3,
                    expected_run_revision=2,
                    decision="mark_failed",
                    reason="operator evidence",
                    evidence_artifact_ids=(art.id,),
                    resolution_request_id=uuid.uuid4(),
                )
            )
        self.assertIn("trusted operator", ctx.exception.message)

    def test_reconciliation_rejects_missing_cross_run_and_tampered_evidence(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        run, call, _ = _seed_external_call(self.db)
        other_run, other_call, _ = _seed_external_call(self.db)
        cross_run = _evidence_artifact(
            self.db,
            run_id=other_run.id,
            call_id=other_call.id,
            evidence_type="capability_call_failure",
        )
        tampered = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        malformed = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=uuid.uuid4(),
            evidence_type="capability_call_failure",
        )
        content_unbound = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
            payload=json.dumps(
                {
                    "contractVersion": 1,
                    "evidenceType": "capability_call_failure",
                    "callId": str(uuid.uuid4()),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        from app.assistant.durable.models import AssistantRunArtifact

        object_evidence = AssistantRunArtifact(
            run_id=run.id,
            kind="capability_call_evidence",
            media_type="application/json",
            storage_kind="object",
            byte_size=128,
            content_sha256="b" * 64,
            inline_bytes=None,
            object_key=f"reconciliation/{uuid.uuid4()}.json",
            metadata_json={
                "contractVersion": 1,
                "evidenceType": "capability_call_failure",
                "callId": str(call.id),
            },
        )
        self.db.add(object_evidence)
        tampered.byte_size += 1
        self.db.commit()
        svc = CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        )

        for artifact_id in (
            uuid.uuid4(),
            cross_run.id,
            tampered.id,
            malformed.id,
            content_unbound.id,
            object_evidence.id,
        ):
            with self.subTest(artifact_id=artifact_id):
                with self.assertRaises(CapabilityCallConflict):
                    svc.apply(
                        ReconciliationDecisionRequest(
                            call_id=call.id,
                            expected_call_revision=3,
                            expected_run_revision=2,
                            decision="mark_failed",
                            reason="must validate evidence",
                            evidence_artifact_ids=(artifact_id,),
                            resolution_request_id=uuid.uuid4(),
                        )
                    )
                self.db.rollback()

    def test_mark_succeeded_requires_normalized_result_and_sets_output(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        run, call, _ = _seed_external_call(self.db)
        wrong = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        from tests.test_capability_call_repository import _normalized_result_artifact
        from app.assistant.capability_calls.models import AssistantCapabilityCallAttempt

        result_artifact = _normalized_result_artifact(self.db, call)
        attempt = (
            self.db.query(AssistantCapabilityCallAttempt)
            .filter(AssistantCapabilityCallAttempt.call_id == call.id)
            .one()
        )
        attempt.status = "committed"
        attempt.response_digest = result_artifact.content_sha256
        self.db.flush()
        attestation = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_success_attestation",
            metadata={"resultArtifactDigest": result_artifact.content_sha256},
            decision="mark_succeeded",
        )
        self.db.commit()
        svc = CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        )
        base = dict(
            call_id=call.id,
            expected_call_revision=3,
            expected_run_revision=2,
            decision="mark_succeeded",
            reason="provider receipt proves success",
        )
        with self.assertRaises(CapabilityCallConflict):
            svc.apply(
                ReconciliationDecisionRequest(
                    **base,
                    evidence_artifact_ids=(wrong.id,),
                    resolution_request_id=uuid.uuid4(),
                )
            )
        self.db.rollback()
        outcome = svc.apply(
            ReconciliationDecisionRequest(
                **base,
                evidence_artifact_ids=(result_artifact.id, attestation.id),
                resolution_request_id=uuid.uuid4(),
            )
        )
        self.db.commit()
        self.db.refresh(call)
        self.assertEqual(outcome.resulting_call_status, "succeeded")
        self.assertEqual(call.output_artifact_id, result_artifact.id)

    def test_reconciliation_locks_run_before_call(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            ReconciliationDecisionRequest,
        )

        run, call, _ = _seed_external_call(self.db)
        art = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        svc = CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=_evidence_verifier(),
        )
        lock_order: list[str] = []
        original_get_run = svc.calls.get_run
        original_get_call = svc.calls.get_call

        def get_run(*args, **kwargs):
            if kwargs.get("for_update"):
                lock_order.append("run")
            return original_get_run(*args, **kwargs)

        def get_call(*args, **kwargs):
            if kwargs.get("for_update"):
                lock_order.append("call")
            return original_get_call(*args, **kwargs)

        with patch.object(svc.calls, "get_run", side_effect=get_run), patch.object(
            svc.calls, "get_call", side_effect=get_call
        ):
            svc.apply(
                ReconciliationDecisionRequest(
                    call_id=call.id,
                    expected_call_revision=3,
                    expected_run_revision=2,
                    decision="mark_failed",
                    reason="evidence proves failure",
                    evidence_artifact_ids=(art.id,),
                    resolution_request_id=uuid.uuid4(),
                )
            )
        self.assertGreaterEqual(len(lock_order), 2)
        self.assertEqual(lock_order[:2], ["run", "call"])

    def test_cli_contract_surface(self) -> None:
        from app.assistant.capability_calls.cli import _build_parser, main

        _run, call, _artifact = _seed_external_call(self.db)
        code = main(
            ["inspect", "--call-id", str(call.id)],
            session_factory=lambda: self.db,
        )
        self.assertEqual(code, 0)
        decide_help = (
            _build_parser()
            ._subparsers._group_actions[0]
            .choices["decide"]
            .format_help()
        )
        self.assertNotIn("actor-admin-id", decide_help)
        self.assertNotIn("status-lookup-not-accepted", decide_help)
        parser = _build_parser()
        choices = parser._subparsers._group_actions[0].choices
        self.assertIn("issue-success", choices)
        self.assertIn("issue-failure-acceptance", choices)
        self.assertNotIn("provider-status", choices["issue-failure-acceptance"].format_help())

        disabled = main(
            [
                "decide",
                "--call-id",
                str(call.id),
                "--expected-call-revision",
                "3",
                "--expected-run-revision",
                "2",
                "--decision",
                "mark_failed",
                "--reason",
                "test",
                "--evidence-artifact-id",
                str(uuid.uuid4()),
            ],
            session_factory=lambda: self.db,
            settings=SimpleNamespace(
                assistant_capability_reconciliation_enabled=False,
                assistant_capability_reconciliation_operator_id=None,
                assistant_capability_reconciliation_evidence_secret="",
            ),
        )
        self.assertEqual(disabled, 2)

    def test_cli_issues_server_derived_success_attestation(self) -> None:
        """CLI issue-success is refused; mutations require HTTP Operator session."""
        import io
        import sys

        from app.assistant.capability_calls.cli import main
        from app.assistant.capability_calls.models import AssistantCapabilityCallAttempt
        from tests.test_capability_call_repository import _normalized_result_artifact

        run, call, _ = _seed_external_call(self.db)
        result_artifact = _normalized_result_artifact(self.db, call)
        attempt = (
            self.db.query(AssistantCapabilityCallAttempt)
            .filter(AssistantCapabilityCallAttempt.call_id == call.id)
            .one()
        )
        attempt.status = "committed"
        attempt.response_digest = result_artifact.content_sha256
        self.db.commit()
        err = io.StringIO()
        old_err = sys.stderr
        try:
            sys.stderr = err
            code = main(
                [
                    "issue-success",
                    "--call-id",
                    str(call.id),
                    "--result-artifact-id",
                    str(result_artifact.id),
                ],
                session_factory=lambda: self.db,
                settings=SimpleNamespace(
                    assistant_capability_reconciliation_enabled=True,
                    assistant_capability_reconciliation_operator_id=uuid.uuid4(),
                    assistant_capability_reconciliation_evidence_secret=EVIDENCE_SECRET,
                ),
            )
        finally:
            sys.stderr = old_err
        self.assertEqual(code, 2)
        self.assertIn("authenticated HTTP Operator session is required", err.getvalue())

    def test_cli_issues_authenticated_product_failure_acceptance(self) -> None:
        """CLI issue-failure-acceptance is refused; mutations require HTTP session."""
        import io
        import sys

        from app.assistant.capability_calls.cli import main

        run, call, _ = _seed_external_call(self.db)
        operator_id = uuid.uuid4()
        err = io.StringIO()
        old_err = sys.stderr
        try:
            sys.stderr = err
            code = main(
                [
                    "issue-failure-acceptance",
                    "--call-id",
                    str(call.id),
                    "--reason",
                    "product accepts unresolved provider outcome",
                ],
                session_factory=lambda: self.db,
                settings=SimpleNamespace(
                    assistant_capability_reconciliation_enabled=True,
                    assistant_capability_reconciliation_operator_id=operator_id,
                    assistant_capability_reconciliation_evidence_secret=EVIDENCE_SECRET,
                ),
            )
        finally:
            sys.stderr = old_err
        self.assertEqual(code, 2)
        self.assertIn("authenticated HTTP Operator session is required", err.getvalue())

    def test_cli_decide_uses_only_server_configured_operator(self) -> None:
        """CLI decide is refused; env-asserted operator identity is not authorized."""
        import io
        import sys

        from app.assistant.capability_calls.cli import main
        from app.assistant.capability_calls.models import (
            AssistantCapabilityReconciliation,
        )

        run, call, _ = _seed_external_call(self.db)
        artifact = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="capability_call_failure",
        )
        self.db.commit()
        operator_id = uuid.uuid4()
        err = io.StringIO()
        old_err = sys.stderr
        try:
            sys.stderr = err
            code = main(
                [
                    "decide",
                    "--call-id",
                    str(call.id),
                    "--expected-call-revision",
                    "3",
                    "--expected-run-revision",
                    "2",
                    "--decision",
                    "mark_failed",
                    "--reason",
                    "typed evidence proves failure",
                    "--evidence-artifact-id",
                    str(artifact.id),
                ],
                session_factory=lambda: self.db,
                settings=SimpleNamespace(
                    assistant_capability_reconciliation_enabled=True,
                    assistant_capability_reconciliation_operator_id=operator_id,
                    assistant_capability_reconciliation_evidence_secret=EVIDENCE_SECRET,
                ),
            )
        finally:
            sys.stderr = old_err
        self.assertEqual(code, 2)
        self.assertIn("authenticated HTTP Operator session is required", err.getvalue())
        self.assertEqual(self.db.query(AssistantCapabilityReconciliation).count(), 0)

    def test_unsigned_status_label_is_not_trusted_evidence(self) -> None:
        from app.assistant.capability_calls.reconciliation import (
            CapabilityReconciliationService,
            HmacReconciliationEvidenceVerifier,
            ReconciliationDecisionRequest,
        )
        from app.assistant.capability_calls.repository import CapabilityCallConflict

        run, call, _ = _seed_external_call(
            self.db, mode="external_reconcilable"
        )
        unsigned = _evidence_artifact(
            self.db,
            run_id=run.id,
            call_id=call.id,
            evidence_type="external_status_lookup",
            metadata={"providerStatus": "not_accepted"},
            signed=False,
        )
        self.db.commit()
        service = CapabilityReconciliationService(
            self.db,
            operator_authorizer=_trusted_authorizer(),
            evidence_verifier=HmacReconciliationEvidenceVerifier(EVIDENCE_SECRET),
        )
        with self.assertRaises(CapabilityCallConflict):
            service.apply(
                ReconciliationDecisionRequest(
                    call_id=call.id,
                    expected_call_revision=3,
                    expected_run_revision=2,
                    decision="retry_same_key",
                    reason="label alone is untrusted",
                    evidence_artifact_ids=(unsigned.id,),
                    resolution_request_id=uuid.uuid4(),
                )
            )


if __name__ == "__main__":
    unittest.main()

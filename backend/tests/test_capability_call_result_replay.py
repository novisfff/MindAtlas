"""Durable CapabilityCall result codec and replay guards."""

from __future__ import annotations

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def _provider_result():
    from tests.test_agent_policy_runtime import _base_manifest
    from app.assistant.capabilities.contracts import CapabilityMetrics, completed_result
    from app.assistant.provider_loop.contracts import ProviderDispatchResult

    manifest, _ = _base_manifest()
    return ProviderDispatchResult(
        capability_result=completed_result(
            structured_output={"value": 7},
            metrics=CapabilityMetrics(duration_ms=1.0, input_bytes=2, output_bytes=3),
        ),
        next_manifest=manifest,
    )


def test_result_round_trip_and_identity_guards() -> None:
    from app.assistant.capability_calls.result_codec import (
        CapabilityResultCodecError,
        decode_capability_result,
        encode_capability_result,
    )

    encoded = encode_capability_result(
        call_id="call-1",
        binding_contract_digest="a" * 64,
        descriptor_digest="b" * 64,
        result=_provider_result(),
    )
    replay = decode_capability_result(
        encoded.payload,
        expected_digest=encoded.digest,
        expected_call_id="call-1",
        expected_binding_contract_digest="a" * 64,
        expected_descriptor_digest="b" * 64,
    )
    assert replay == _provider_result()
    with pytest.raises(CapabilityResultCodecError, match="call mismatch"):
        decode_capability_result(
            encoded.payload,
            expected_digest=encoded.digest,
            expected_call_id="call-2",
            expected_binding_contract_digest="a" * 64,
            expected_descriptor_digest="b" * 64,
        )


def test_result_corruption_fails_closed() -> None:
    from app.assistant.capability_calls.result_codec import (
        CapabilityResultCodecError,
        decode_capability_result,
        encode_capability_result,
    )

    encoded = encode_capability_result(
        call_id="call-1",
        binding_contract_digest="a" * 64,
        descriptor_digest="b" * 64,
        result=_provider_result(),
    )
    with pytest.raises(CapabilityResultCodecError, match="digest mismatch"):
        decode_capability_result(
            encoded.payload + b"x",
            expected_digest=encoded.digest,
            expected_call_id="call-1",
            expected_binding_contract_digest="a" * 64,
            expected_descriptor_digest="b" * 64,
        )


@pytest.mark.parametrize("side_effect", ["read", "write_external"])
def test_durable_aggregate_replays_success_without_redispatch(side_effect: str) -> None:
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    import uuid

    from tests._db import make_session
    from tests.test_capability_call_repository import _make_main_agent_run
    from app.assistant.capabilities.contracts import CapabilityError
    from app.assistant.capability_calls.aggregate import DurableCapabilityLedgerAggregate
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )
    from app.assistant.durable.repository import DurableRunRepository, LeaseToken
    from app.assistant.policy import (
        create_initial_ledger_state,
        create_initial_obligation_ledger_state,
        normalize_run_budget_limits,
    )
    from app.assistant.policy.budgets import pure_start_provider_round
    from app.assistant.policy.contracts import build_authorization_decision_v2
    from app.assistant.provider_loop.messages import (
        ProviderAssistantMessage,
        ProviderToolCall,
        ProviderToolMessage,
        ProviderToolResultEnvelope,
        ProviderUserMessage,
        digest_arguments,
        project_tool_result_envelope,
    )

    db = make_session()
    try:
        run_id = uuid.uuid4()
        run = _make_main_agent_run(
            db,
            id=run_id,
            status="running",
            state_revision=3,
            capability_ledger_mode="enforced",
            lease_owner="worker-1",
            lease_generation=1,
        )
        manifest = AssistantRunManifestRevision(
            run_id=run.id,
            revision=1,
            manifest_digest="a" * 64,
            schema_version=1,
            payload={},
        )
        started = datetime.now(timezone.utc)
        ledger = create_initial_ledger_state(
            limits=normalize_run_budget_limits(),
            started_at_utc=started,
            deadline_at_utc=started + timedelta(minutes=2),
        )
        live_ledger, round_decision = pure_start_provider_round(
            ledger,
            cancelled=False,
            mono_now_ms=0,
            mono_deadline_ms=1,
        )
        assert round_decision.allowed is True
        policy = AssistantRunPolicyRevision(
            run_id=run.id,
            revision=1,
            policy_digest="b" * 64,
            payload={},
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id,
            revision=1,
            budget_digest=ledger.ledger_digest,
            payload=ledger.model_dump(mode="json", by_alias=True),
        )
        obligation_state = create_initial_obligation_ledger_state()
        obligation = AssistantRunObligationRevision(
            run_id=run.id,
            revision=1,
            obligation_digest=obligation_state.ledger_digest,
            payload=obligation_state.model_dump(mode="json", by_alias=True),
        )
        db.add_all([manifest, policy, budget, obligation])
        db.flush()
        run.current_manifest_revision_id = manifest.id
        run.current_policy_revision_id = policy.id
        run.current_budget_revision_id = budget.id
        run.current_obligation_revision_id = obligation.id
        run.deadline_at = ledger.deadline_at_utc
        db.commit()
        decision = build_authorization_decision_v2(
            policy_allowed=True,
            dispatch_disposition="dispatch",
            reason_code="allowed",
            principal_digest="a" * 64,
            entrypoint_policy_digest="a" * 64,
            global_policy_digest="a" * 64,
            owner_policy_digest="a" * 64,
            allowed_side_effects=("none", "compute", "read", "write_external"),
            grant_source_digest="b" * 64,
            exposure_digest="a" * 64,
            effective_policy_digest="a" * 64,
        )
        factory = SimpleNamespace(
            decision_for_call=lambda **_kwargs: decision,
        )
        request = SimpleNamespace(
            execution_scope=SimpleNamespace(run_id=run.id),
            call=SimpleNamespace(
                call_id="durable-read-1",
                domain_key=(
                    "external_write" if side_effect == "write_external" else "search_entries"
                ),
                arguments={"q": "x"},
            ),
            current_manifest=SimpleNamespace(manifest_digest="a" * 64),
            binding=SimpleNamespace(
                ref=SimpleNamespace(
                    binding_contract_digest="c" * 64,
                    resolution_digest="d" * 64,
                )
            ),
            descriptor=SimpleNamespace(
                behavior=SimpleNamespace(side_effect=side_effect),
                capability_type="tool",
                target_id=None,
                target_version_id=None,
                descriptor_digest="e" * 64,
            ),
            authorization=SimpleNamespace(
                owner=SimpleNamespace(
                    owner_kind="main_agent",
                    owner_id="general_chat",
                    owner_version_id=None,
                )
            ),
        )
        sibling_request = SimpleNamespace(**vars(request))
        sibling_request.call = SimpleNamespace(
            call_id="durable-read-2",
            domain_key=request.call.domain_key,
            arguments={"q": "y"},
        )
        runtime_snapshot = {
            "manifest": SimpleNamespace(
                run_id=run.id,
                manifest_digest=manifest.manifest_digest,
                model_dump=lambda **_kwargs: {},
            ),
            "policy": SimpleNamespace(
                effective_policy_digest=policy.policy_digest,
                model_dump=lambda **_kwargs: {},
            ),
            "budget": live_ledger,
            "obligation": obligation_state,
        }
        from tests._db import allowing_test_write_guard

        aggregate = DurableCapabilityLedgerAggregate(
            db=db,
            authorization_factory=factory,
            idempotency_secret="s" * 32,
            write_guard=allowing_test_write_guard(db),
            lease=LeaseToken(
                run_id=run.id, worker_id="worker-1", lease_generation=1
            ),
            runtime_snapshot_provider=lambda: runtime_snapshot,
        )
        tool_call = ProviderToolCall(
            call_id="durable-read-1",
            call_index=0,
            provider_alias=request.call.domain_key,
            domain_key=request.call.domain_key,
            arguments={"q": "x"},
            arguments_digest=digest_arguments({"q": "x"}),
            binding_contract_digest="c" * 64,
            descriptor_digest="e" * 64,
            behavior_digest="f" * 64,
            classification_revision="1",
            classification_ruleset_digest="1" * 64,
            manifest_revision=1,
            manifest_digest="a" * 64,
            surface_digest="2" * 64,
        )
        sibling_tool_call = tool_call.model_copy(
            update={
                "call_id": "durable-read-2",
                "call_index": 1,
                "arguments": {"q": "y"},
                "arguments_digest": digest_arguments({"q": "y"}),
            }
        )
        base_messages = (
            ProviderUserMessage(content="search"),
            ProviderAssistantMessage(
                content=None, tool_calls=(tool_call, sibling_tool_call)
            ),
        )
        aggregate.reserve_siblings((request, sibling_request), base_messages)
        db.refresh(run)
        assert run.current_budget_revision_id != budget.id
        persisted_live_budget = db.get(
            AssistantRunBudgetRevision, run.current_budget_revision_id
        )
        assert persisted_live_budget.budget_digest == live_ledger.ledger_digest
        assert persisted_live_budget.payload["providerRoundsStarted"] == 1
        from app.assistant.capability_calls.models import AssistantCapabilityCall

        reserved = (
            db.query(AssistantCapabilityCall)
            .filter_by(run_id=run.id, provider_tool_call_id="durable-read-1")
            .one()
        )
        assert reserved.status == "proposed"
        from app.assistant.durable.codec import decode_checkpoint
        from app.assistant.durable.models import AssistantRunCheckpoint

        db.refresh(run)
        reservation_checkpoint = db.get(
            AssistantRunCheckpoint, run.current_checkpoint_id
        )
        assert reservation_checkpoint.schema_version == 3
        reserved_state = decode_checkpoint(reservation_checkpoint.state_payload)
        assert reserved_state.next_action.kind == "dispatch_calls"
        assert [item.provider_tool_call_id for item in reserved_state.capability_calls] == [
            "durable-read-1",
            "durable-read-2",
        ]
        assert {item.status for item in reserved_state.capability_calls} == {"proposed"}
        prepared = aggregate.prepare(request)
        assert prepared.kind == "dispatch"
        from app.assistant.capability_calls.models import AssistantCapabilityCallAttempt

        prepared_call = db.get(AssistantCapabilityCall, prepared.call_id)
        prepared_attempt = db.get(AssistantCapabilityCallAttempt, prepared.attempt_id)
        assert prepared_call is not None
        assert prepared_attempt is not None
        if side_effect == "write_external":
            assert prepared_call.side_effect_started_at is not None
            assert prepared_attempt.side_effect_started is True
            assert (
                prepared_attempt.side_effect_started_at
                == prepared_call.side_effect_started_at
            )
            from app.assistant.capability_calls.settlement import (
                CapabilityCallSettlementRepository,
                SettlementRequest,
                compute_settlement_evidence_digest,
            )

            stopped = DurableRunRepository(db).request_stop(
                run_id=run.id,
                expected_revision=int(run.state_revision),
            )
            db.refresh(prepared_call)
            db.refresh(prepared_attempt)
            evidence_digest = compute_settlement_evidence_digest(
                attempt=prepared_attempt,
                outcome="unknown",
                result_artifact=None,
            )
            settled = CapabilityCallSettlementRepository(db).settle_while_cancelling(
                SettlementRequest(
                    call_id=prepared_call.id,
                    attempt_id=prepared_attempt.id,
                    expected_call_revision=int(prepared_call.state_revision),
                    expected_run_revision=int(stopped.state_revision),
                    outcome="unknown",
                    result_artifact_id=None,
                    evidence_digest=evidence_digest,
                )
            )
            db.commit()
            db.refresh(prepared_call)
            db.refresh(prepared_attempt)
            assert settled.status == "needs_reconciliation"
            assert prepared_call.status == "needs_reconciliation"
            assert prepared_attempt.status == "uncertain"
            # Existing exact identity replays its unresolved disposition before
            # any newly-blocking policy/new-write admission is considered.
            factory.decision_for_call = lambda **_kwargs: SimpleNamespace(
                dispatch_disposition="deny"
            )
            aggregate.reserve_siblings((request,), base_messages)
            unresolved_replay = aggregate.prepare(request)
            assert unresolved_replay.kind == "deny"
            assert unresolved_replay.reason_code == "reconciliation_required"
            return

        assert prepared_call.side_effect_started_at is None
        assert prepared_attempt.side_effect_started is False
        assert prepared_attempt.side_effect_started_at is None
        db.refresh(run)
        started_checkpoint = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        started_state = decode_checkpoint(started_checkpoint.state_payload)
        assert started_checkpoint.reason == "capability_attempt_started"
        assert started_state.capability_calls[0].status == "executing"
        assert started_state.budget_revision_id == run.current_budget_revision_id
        assert (
            db.get(AssistantRunBudgetRevision, run.current_budget_revision_id).budget_digest
            == live_ledger.ledger_digest
        )
        result = _provider_result()
        aggregate.commit_result(prepared, result)
        runtime_snapshot["manifest"] = SimpleNamespace(
            run_id=run.id,
            manifest_digest="9" * 64,
            model_dump=lambda **_kwargs: {"accepted": True},
        )
        first_tool_message = ProviderToolMessage(
            call_id=tool_call.call_id,
            provider_alias=tool_call.provider_alias,
            content=project_tool_result_envelope(
                domain_key="search_entries",
                result=result.capability_result,
            ),
        )
        aggregate.commit_progress(
            (
                *base_messages,
                first_tool_message,
            )
        )
        db.refresh(run)
        assert run.current_manifest_revision_id != manifest.id
        persisted_manifest = db.get(
            AssistantRunManifestRevision, run.current_manifest_revision_id
        )
        assert persisted_manifest.manifest_digest == "9" * 64
        assert persisted_manifest.payload == {"accepted": True}
        progress_checkpoint = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        progress_state = decode_checkpoint(progress_checkpoint.state_payload)
        assert progress_state.next_action.kind == "dispatch_calls"
        assert [item.status for item in progress_state.capability_calls] == [
            "succeeded",
            "proposed",
        ]
        replay = aggregate.prepare(request)
        assert replay.kind == "replay"
        assert replay.provider_result == result
        aggregate.commit_recovery_drift(
            (
                *base_messages,
                first_tool_message,
                ProviderToolMessage(
                    call_id=sibling_tool_call.call_id,
                    provider_alias=sibling_tool_call.provider_alias,
                    content=ProviderToolResultEnvelope(
                        status="blocked",
                        domain_key=sibling_tool_call.domain_key,
                        user_text=None,
                        structured_output=None,
                        terminal_output=False,
                        needs_followup=False,
                        error=CapabilityError(
                            error_type="version_drift",
                            safe_code="classification_changed",
                            safe_message="classification changed",
                            retry_disposition="never",
                            call_id=sibling_tool_call.call_id,
                        ),
                    ),
                ),
            ),
            stale_call_id=sibling_tool_call.call_id,
        )
        db.refresh(run)
        assert run.status == "failed"
        terminal_checkpoint = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        terminal_state = decode_checkpoint(terminal_checkpoint.state_payload)
        assert terminal_state.phase == "terminal"
        assert terminal_state.next_action.kind == "terminal"
        assert [item.status for item in terminal_state.capability_calls] == [
            "succeeded",
            "denied",
        ]
    finally:
        db.close()

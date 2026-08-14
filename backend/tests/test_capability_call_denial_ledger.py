"""Denied enforced calls remain durable, ordered ledger facts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


def _decision(*, disposition: str, reason_code: str):
    from app.assistant.policy.contracts import build_authorization_decision_v2

    return build_authorization_decision_v2(
        policy_allowed=disposition != "deny",
        dispatch_disposition=disposition,
        reason_code=reason_code,
        principal_digest="a" * 64,
        entrypoint_policy_digest="b" * 64,
        global_policy_digest="c" * 64,
        owner_policy_digest="d" * 64,
        allowed_side_effects=("none", "compute", "read"),
        grant_source_digest="e" * 64,
        exposure_digest="f" * 64,
        effective_policy_digest="1" * 64,
    )


def _harness(
    dispositions: tuple[str, ...],
    *,
    side_effects: tuple[str, ...] | None = None,
):
    from tests._db import make_session
    from tests.test_capability_call_repository import _make_main_agent_run
    from app.assistant.capability_calls.aggregate import DurableCapabilityLedgerAggregate
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )
    from app.assistant.durable.repository import LeaseToken
    from app.assistant.policy import create_initial_ledger_state, normalize_run_budget_limits
    from app.assistant.provider_loop.messages import (
        ProviderAssistantMessage,
        ProviderToolCall,
        ProviderUserMessage,
        digest_arguments,
    )
    from app.assistant.provider_loop.contracts import (
        DeniedLedgerReservationEvidence,
        ProviderDeniedLedgerReservationRequest,
    )

    db = make_session()
    run = _make_main_agent_run(
        db,
        id=uuid4(),
        status="running",
        state_revision=3,
        capability_ledger_mode="enforced",
        lease_owner="worker-denial-ledger",
        lease_generation=1,
    )
    manifest = AssistantRunManifestRevision(
        run_id=run.id,
        revision=1,
        manifest_digest="2" * 64,
        schema_version=1,
        payload={},
    )
    started = datetime.now(timezone.utc)
    ledger = create_initial_ledger_state(
        limits=normalize_run_budget_limits(),
        started_at_utc=started,
        deadline_at_utc=started + timedelta(minutes=2),
    )
    policy = AssistantRunPolicyRevision(
        run_id=run.id,
        revision=1,
        policy_digest="3" * 64,
        payload={},
    )
    budget = AssistantRunBudgetRevision(
        run_id=run.id,
        revision=1,
        budget_digest=ledger.ledger_digest,
        payload=ledger.model_dump(mode="json", by_alias=True),
    )
    obligation = AssistantRunObligationRevision(
        run_id=run.id,
        revision=1,
        obligation_digest="4" * 64,
        payload={},
    )
    db.add_all([manifest, policy, budget, obligation])
    db.flush()
    run.current_manifest_revision_id = manifest.id
    run.current_policy_revision_id = policy.id
    run.current_budget_revision_id = budget.id
    run.current_obligation_revision_id = obligation.id
    run.deadline_at = ledger.deadline_at_utc
    db.commit()

    requests = []
    tool_calls = []
    decisions = {}
    denial_reservations = {}
    for index, disposition in enumerate(dispositions):
        side_effect = (
            side_effects[index] if side_effects is not None else "read"
        )
        provider_call_id = f"denial-ledger-{index}"
        arguments = {"q": f"value-{index}"}
        decisions[provider_call_id] = _decision(
            disposition=disposition,
            reason_code=("policy_denied" if disposition == "deny" else "allowed"),
        )
        owner = SimpleNamespace(
            owner_kind="main_agent",
            owner_id="general_chat",
            owner_version_id=None,
        )
        execution_scope = SimpleNamespace(
            run_id=run.id,
            scope_digest="b" * 64,
        )
        request = SimpleNamespace(
                execution_scope=SimpleNamespace(run_id=run.id),
                call=SimpleNamespace(
                    call_id=provider_call_id,
                    domain_key=(
                        "create_entry" if side_effect == "write_local" else "search_entries"
                    ),
                    arguments=arguments,
                ),
                current_manifest=SimpleNamespace(manifest_digest=manifest.manifest_digest),
                binding=SimpleNamespace(
                    ref=SimpleNamespace(
                        binding_contract_digest="5" * 64,
                        resolution_digest="6" * 64,
                    )
                ),
                descriptor=SimpleNamespace(
                    behavior=SimpleNamespace(side_effect=side_effect),
                    capability_type="tool",
                    target_id=None,
                    target_version_id=None,
                    descriptor_digest="7" * 64,
                ),
                authorization=SimpleNamespace(owner=owner),
        )
        if disposition == "deny":
            evidence = DeniedLedgerReservationEvidence.model_construct(
                call_id=provider_call_id,
                owner=owner,
                decision_digest=decisions[provider_call_id].decision_digest,
                reason_code=decisions[provider_call_id].reason_code,
                scope_digest=execution_scope.scope_digest,
                manifest_digest=manifest.manifest_digest,
                binding_contract_digest="5" * 64,
                descriptor_digest="7" * 64,
            )
            denial_reservations[provider_call_id] = evidence
            request = ProviderDeniedLedgerReservationRequest.model_construct(
                call=request.call,
                binding=request.binding,
                descriptor=request.descriptor,
                current_manifest=request.current_manifest,
                execution_scope=execution_scope,
                denial_evidence=evidence,
            )
        requests.append(request)
        tool_calls.append(
            ProviderToolCall(
                call_id=provider_call_id,
                call_index=index,
                provider_alias="search_entries",
                domain_key="search_entries",
                arguments=arguments,
                arguments_digest=digest_arguments(arguments),
                binding_contract_digest="5" * 64,
                descriptor_digest="7" * 64,
                behavior_digest="8" * 64,
                classification_revision="1",
                classification_ruleset_digest="9" * 64,
                manifest_revision=1,
                manifest_digest=manifest.manifest_digest,
                surface_digest="a" * 64,
            )
        )

    factory = SimpleNamespace(
        decision_for_call=lambda *, call_id: decisions[call_id],
        denial_reservation_for_call=lambda *, call_id: denial_reservations[call_id],
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
        "budget": ledger,
        "obligation": SimpleNamespace(
            ledger_digest=obligation.obligation_digest,
            model_dump=lambda **_kwargs: {},
        ),
    }
    from tests._db import allowing_test_write_guard

    aggregate = DurableCapabilityLedgerAggregate(
        db=db,
        authorization_factory=factory,
        idempotency_secret="s" * 32,
        write_guard=allowing_test_write_guard(db),
        lease=LeaseToken(
            run_id=run.id,
            worker_id="worker-denial-ledger",
            lease_generation=1,
        ),
        runtime_snapshot_provider=lambda: runtime_snapshot,
    )
    messages = (
        ProviderUserMessage(content="search"),
        ProviderAssistantMessage(content=None, tool_calls=tuple(tool_calls)),
    )
    return db, run, aggregate, tuple(requests), messages


def test_denied_call_is_persisted_and_replayed_without_gateway() -> None:
    from app.assistant.capability_calls.dispatcher import LedgerDispatcher
    from app.assistant.capability_calls.models import (
        AssistantCapabilityCall,
        AssistantCapabilityCallAttempt,
    )
    from app.assistant.durable.codec import decode_checkpoint
    from app.assistant.durable.models import AssistantRunArtifact, AssistantRunCheckpoint

    db, run, aggregate, requests, messages = _harness(("deny",))
    try:
        aggregate.reserve_siblings(requests, messages)
        call = db.query(AssistantCapabilityCall).one()
        assert call.status == "denied"
        assert call.failure_code == "policy_denied"
        assert call.state_revision > 0
        assert call.attempt_count == 0
        assert db.query(AssistantCapabilityCallAttempt).count() == 0
        artifact = db.get(AssistantRunArtifact, call.input_artifact_id)
        assert artifact is not None
        assert artifact.kind == "capability_call_input"
        assert artifact.content_sha256 == call.input_digest

        db.refresh(run)
        checkpoint = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        decoded = decode_checkpoint(checkpoint.state_payload)
        assert [item.provider_tool_call_id for item in decoded.capability_calls] == [
            requests[0].call.call_id
        ]
        assert [item.status for item in decoded.capability_calls] == ["denied"]
        assert decoded.capability_calls[0].attempt_id is None

        class _ForbiddenGateway:
            calls = 0

            def dispatch(self, _request, *, cancellation):
                del cancellation
                self.calls += 1
                raise AssertionError("denied call reached Gateway")

        gateway = _ForbiddenGateway()
        dispatcher = LedgerDispatcher(inner=gateway, aggregate=aggregate)
        result = dispatcher.dispatch(
            requests[0], cancellation=SimpleNamespace(is_cancelled=lambda: False)
        )
        assert result.capability_result.status == "failed"
        assert gateway.calls == 0

        first_revision = call.state_revision
        aggregate.reserve_siblings(requests, messages)
        replay = aggregate.prepare(requests[0])
        db.refresh(call)
        assert replay.kind == "deny"
        assert replay.call_id == call.id
        assert replay.call_revision == first_revision
        assert replay.reason_code == "policy_denied"
        assert call.state_revision == first_revision
        assert db.query(AssistantCapabilityCall).count() == 1
        assert db.query(AssistantCapabilityCallAttempt).count() == 0
    finally:
        db.close()


def test_mixed_siblings_preserve_provider_order_and_only_dispatch_allowed() -> None:
    from app.assistant.capability_calls.models import (
        AssistantCapabilityCall,
        AssistantCapabilityCallAttempt,
    )
    from app.assistant.durable.codec import decode_checkpoint
    from app.assistant.durable.models import AssistantRunCheckpoint

    db, run, aggregate, requests, messages = _harness(("deny", "dispatch"))
    try:
        aggregate.reserve_siblings(requests, messages)
        db.refresh(run)
        checkpoint = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        decoded = decode_checkpoint(checkpoint.state_payload)
        assert [item.provider_tool_call_id for item in decoded.capability_calls] == [
            request.call.call_id for request in requests
        ]
        assert [item.status for item in decoded.capability_calls] == [
            "denied",
            "proposed",
        ]

        denied = aggregate.prepare(requests[0])
        allowed = aggregate.prepare(requests[1])
        assert denied.kind == "deny"
        assert denied.call_revision > 0
        assert allowed.kind == "dispatch"
        rows = {
            row.provider_tool_call_id: row
            for row in db.query(AssistantCapabilityCall).all()
        }
        assert rows[requests[0].call.call_id].attempt_count == 0
        assert rows[requests[1].call.call_id].attempt_count == 1
        attempts = db.query(AssistantCapabilityCallAttempt).all()
        assert len(attempts) == 1
        assert attempts[0].call_id == rows[requests[1].call.call_id].id
    finally:
        db.close()


def test_denied_prepare_requires_reservation_and_rejects_input_drift() -> None:
    from app.assistant.capability_calls.models import AssistantCapabilityCall
    from app.assistant.capability_calls.repository import CapabilityCallConflict

    db, _run, aggregate, requests, messages = _harness(("deny",))
    try:
        with pytest.raises(CapabilityCallConflict, match="reservation"):
            aggregate.prepare(requests[0])
        assert db.query(AssistantCapabilityCall).count() == 0

        aggregate.reserve_siblings(requests, messages)
        requests[0].call.arguments = {"q": "drifted"}
        with pytest.raises(CapabilityCallConflict, match="canonical input"):
            aggregate.prepare(requests[0])
        assert db.query(AssistantCapabilityCall).count() == 1
        assert db.query(AssistantCapabilityCall).one().status == "denied"
    finally:
        db.close()


def test_denied_local_write_is_ledgered_without_requesting_approval() -> None:
    from app.assistant.capability_calls.models import AssistantCapabilityCall

    db, _run, aggregate, requests, messages = _harness(
        ("deny",),
        side_effects=("write_local",),
    )
    try:
        aggregate.reserve_siblings(requests, messages)
        call = db.query(AssistantCapabilityCall).one()
        assert call.status == "denied"
        assert call.side_effect_class == "write_local"
        assert call.execution_mode == "local_transactional"
        assert call.interrupt_id is None
        assert call.attempt_count == 0
    finally:
        db.close()


def test_denial_reservation_rejects_executable_authorization_request() -> None:
    from app.assistant.capability_calls.models import AssistantCapabilityCall
    from app.assistant.capability_calls.repository import CapabilityCallConflict
    from app.assistant.provider_loop.contracts import ProviderDispatchRequest

    db, _run, aggregate, requests, messages = _harness(("deny",))
    try:
        executable_request = ProviderDispatchRequest.model_construct(
            call=requests[0].call,
            binding=requests[0].binding,
            descriptor=requests[0].descriptor,
            current_manifest=requests[0].current_manifest,
            execution_scope=requests[0].execution_scope,
            authorization=SimpleNamespace(
                owner=requests[0].denial_evidence.owner
            ),
        )
        with pytest.raises(CapabilityCallConflict, match="non-executable"):
            aggregate.reserve_siblings((executable_request,), messages)
        assert db.query(AssistantCapabilityCall).count() == 0
    finally:
        db.close()


def test_real_v2_policy_deny_is_reserved_but_never_dispatched() -> None:
    from app.assistant.capabilities.policy import AuthorizationEvidenceVerificationError
    from app.assistant.capability_calls.aggregate import DurableCapabilityLedgerAggregate
    from app.assistant.capability_calls.models import (
        AssistantCapabilityCall,
        AssistantCapabilityCallAttempt,
    )
    from app.assistant.durable.codec import decode_checkpoint
    from app.assistant.durable.models import (
        AssistantRunBudgetRevision,
        AssistantRunCheckpoint,
        AssistantRunManifestRevision,
        AssistantRunObligationRevision,
        AssistantRunPolicyRevision,
    )
    from app.assistant.durable.repository import LeaseToken
    from app.assistant.provider_loop.loop import (
        _FatalCapability,
        _dispatch_one,
        _reserve_ledger_siblings,
    )
    from app.assistant.provider_loop.messages import (
        ProviderAssistantMessage,
        ProviderToolCall,
        ProviderUserMessage,
        digest_arguments,
    )
    from tests.test_capability_call_repository import _make_main_agent_run
    from tests.test_agent_policy_runtime import (
        BUILD,
        CONV_ID,
        DIGEST_A,
        _base_manifest,
    )
    from tests._db import make_session
    from app.assistant.main_agent.policy_runtime import compose_main_agent_policy_runtime

    os.environ["APP_BUILD_REVISION"] = BUILD
    reset_caches()
    safe_run_id = UUID(hex="aaaaaaaa" + uuid4().hex[8:])
    safe_profile_version_id = UUID(hex="aaaaaaaa" + uuid4().hex[8:])
    template_manifest, _ = _base_manifest(run_id=safe_run_id)
    from app.assistant.main_agent.control_capabilities import (
        build_all_main_agent_control_bindings,
    )
    from app.assistant.main_agent.service import build_base_manifest_with_controls

    control_bindings = build_all_main_agent_control_bindings(
        owner_version_id=safe_profile_version_id,
        source_snapshot_digest=DIGEST_A,
        app_build_revision=BUILD,
    )
    base_manifest = build_base_manifest_with_controls(
        run_id=safe_run_id,
        main_agent=template_manifest.main_agent.model_copy(
            update={"version_id": safe_profile_version_id}
        ),
        provider=template_manifest.provider,
        model=template_manifest.model,
        effective_policy_digest=template_manifest.effective_policy_digest,
        control_bindings=control_bindings,
    )
    db = make_session()
    runtime, ports = compose_main_agent_policy_runtime(
        db=db,
        run_id=safe_run_id,
        conversation_id=CONV_ID,
        manifest=base_manifest,
        profile_key="general_chat",
        profile_version_id=safe_profile_version_id,
        profile_content_digest=DIGEST_A,
        app_build_revision=BUILD,
        provider=SimpleNamespace(),
        capability_ledger_mode="legacy_read_only",
        policy_contract_version=2,
    )
    try:
        factory = runtime.authorization_factory
        factory.owner_materials = {}
        resolved = ports.tools_provider.resolve(
            runtime.manifest,
            scope=factory.scope,
            locale="en",
        )
        definition = resolved.surface.tools[0]
        arguments: dict[str, object] = {}
        call = ProviderToolCall(
            call_id="real-v2-policy-deny",
            call_index=0,
            provider_alias=definition.provider_alias,
            domain_key=definition.domain_key,
            arguments=arguments,
            arguments_digest=digest_arguments(arguments),
            binding_contract_digest=definition.binding.ref.binding_contract_digest,
            descriptor_digest=definition.descriptor.descriptor_digest,
            behavior_digest="b" * 64,
            classification_revision="1",
            classification_ruleset_digest="c" * 64,
            manifest_revision=runtime.manifest.revision,
            manifest_digest=runtime.manifest.manifest_digest,
            surface_digest=resolved.surface.surface_digest,
        )
        run = _make_main_agent_run(
            db,
            id=runtime.run_id,
            status="running",
            state_revision=3,
            capability_ledger_mode="enforced",
            lease_owner="worker-real-deny",
            lease_generation=1,
        )
        budget_state = runtime.budget_ledger.snapshot()
        obligation_state = runtime.obligation_ledger.snapshot()
        manifest = AssistantRunManifestRevision(
            run_id=run.id,
            revision=runtime.manifest.revision,
            manifest_digest=runtime.manifest.manifest_digest,
            schema_version=1,
            payload=runtime.manifest.model_dump(mode="json", by_alias=True),
        )
        policy = AssistantRunPolicyRevision(
            run_id=run.id,
            revision=1,
            policy_digest=runtime.policy_snapshot.effective_policy_digest,
            payload=runtime.policy_snapshot.model_dump(mode="json", by_alias=True),
        )
        budget = AssistantRunBudgetRevision(
            run_id=run.id,
            revision=1,
            budget_digest=budget_state.ledger_digest,
            payload=budget_state.model_dump(mode="json", by_alias=True),
        )
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
        db.commit()
        from tests._db import allowing_test_write_guard

        aggregate = DurableCapabilityLedgerAggregate(
            db=db,
            authorization_factory=factory,
            idempotency_secret="s" * 32,
            write_guard=allowing_test_write_guard(db),
            lease=LeaseToken(
                run_id=run.id,
                worker_id="worker-real-deny",
                lease_generation=1,
            ),
            runtime_snapshot_provider=lambda: {
                "manifest": runtime.manifest,
                "policy": runtime.policy_snapshot,
                "budget": budget_state,
                "obligation": obligation_state,
            },
        )
        messages = (
            ProviderUserMessage(content="deny"),
            ProviderAssistantMessage(content=None, tool_calls=(call,)),
        )

        preauthorized = _reserve_ledger_siblings(
            ports=SimpleNamespace(
                capability_ledger=aggregate,
                authorization_evidence=factory,
            ),
            surface=resolved.surface,
            calls=(call,),
            current_manifest=runtime.manifest,
            scope=factory.scope,
            provider_messages=messages,
        )

        assert preauthorized == {}
        row = db.query(AssistantCapabilityCall).one()
        assert row.status == "denied"
        assert row.failure_code
        assert row.attempt_count == 0
        assert db.query(AssistantCapabilityCallAttempt).count() == 0
        db.refresh(run)
        checkpoint = db.get(AssistantRunCheckpoint, run.current_checkpoint_id)
        decoded = decode_checkpoint(checkpoint.state_payload)
        assert [item.status for item in decoded.capability_calls] == ["denied"]

        class _RecordingDispatcher:
            calls = 0

            def dispatch(self, _request, *, cancellation):
                del cancellation
                self.calls += 1
                raise AssertionError("policy-denied request reached dispatcher")

        dispatcher = _RecordingDispatcher()
        dispatch_ports = SimpleNamespace(
            current_descriptors=SimpleNamespace(
                require_current=lambda **_kwargs: definition.descriptor
            ),
            authorization_evidence=factory,
            tool_dispatcher=dispatcher,
            dispatch_guard=None,
        )
        with pytest.raises(_FatalCapability):
            _dispatch_one(
                ports=dispatch_ports,
                call=call,
                definition=definition,
                current_manifest=runtime.manifest,
                scope=factory.scope,
                authorization=None,
            )
        assert dispatcher.calls == 0
        with pytest.raises(AuthorizationEvidenceVerificationError, match="verifier"):
            factory.take_verifier(call_id=call.call_id)
    finally:
        db.close()

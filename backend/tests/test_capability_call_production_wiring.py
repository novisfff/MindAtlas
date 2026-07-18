"""Production wiring tests for the Plan 08 capability ledger."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


@dataclass
class _RecordingInner:
    calls: list[Any] = field(default_factory=list)

    def dispatch(self, request: Any, *, cancellation: Any) -> Any:
        self.calls.append(request)
        return SimpleNamespace(capability_result=SimpleNamespace(status="succeeded"))


@dataclass
class _RecordingAggregate:
    outcome: Any
    prepared: list[Any] = field(default_factory=list)

    def prepare(self, request: Any) -> Any:
        self.prepared.append(request)
        return self.outcome

    def commit_result(self, outcome: Any, result: Any) -> Any:
        return result

    def record_failure(self, outcome: Any, reason_code: str) -> None:
        del outcome, reason_code


def _compose(
    *, ledger_mode: str, aggregate: Any = None, policy_contract_version: int = 1
):
    from tests._db import make_session
    from tests.test_agent_policy_runtime import (
        BUILD,
        CONV_ID,
        DIGEST_A,
        PROFILE_VERSION_ID,
        RUN_ID,
        _base_manifest,
    )

    from app.assistant.main_agent.policy_runtime import compose_main_agent_policy_runtime

    os.environ["APP_BUILD_REVISION"] = BUILD
    reset_caches()
    db = make_session()
    manifest, _ = _base_manifest()
    runtime, ports = compose_main_agent_policy_runtime(
        db=db,
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        manifest=manifest,
        profile_key="general_chat",
        profile_version_id=PROFILE_VERSION_ID,
        profile_content_digest=DIGEST_A,
        app_build_revision=BUILD,
        provider=SimpleNamespace(),
        capability_ledger_mode=ledger_mode,
        capability_ledger=aggregate,
        policy_contract_version=policy_contract_version,
    )
    return db, runtime, ports


def test_enforced_composition_installs_ledger_dispatcher() -> None:
    from app.assistant.provider_loop.contracts import LedgerPrepareOutcome

    aggregate = _RecordingAggregate(
        outcome=LedgerPrepareOutcome(
            kind="deny",
            call_id=uuid4(),
            call_revision=0,
            reason_code="policy_denied",
        )
    )
    db, _runtime, ports = _compose(ledger_mode="enforced", aggregate=aggregate)
    try:
        from app.assistant.capability_calls.dispatcher import LedgerDispatcher

        assert isinstance(ports.tool_dispatcher, LedgerDispatcher)
        assert ports.capability_ledger is aggregate
    finally:
        db.close()


def test_legacy_composition_keeps_gateway_dispatcher() -> None:
    db, _runtime, ports = _compose(ledger_mode="legacy_read_only")
    try:
        from app.assistant.main_agent.policy_runtime import MainAgentGatewayToolDispatcher

        assert isinstance(ports.tool_dispatcher, MainAgentGatewayToolDispatcher)
        assert ports.capability_ledger is None
    finally:
        db.close()


def test_enforced_composition_requires_aggregate() -> None:
    with pytest.raises(RuntimeError, match="requires durable aggregate port"):
        _compose(ledger_mode="enforced")


def test_deny_outcome_never_calls_gateway() -> None:
    from app.assistant.capability_calls.dispatcher import LedgerDispatcher
    from app.assistant.provider_loop.contracts import LedgerPrepareOutcome

    inner = _RecordingInner()
    aggregate = _RecordingAggregate(
        outcome=LedgerPrepareOutcome(
            kind="deny",
            call_id=uuid4(),
            call_revision=0,
            reason_code="policy_denied",
        )
    )
    dispatcher = LedgerDispatcher(inner=inner, aggregate=aggregate)

    result = dispatcher.dispatch(object(), cancellation=SimpleNamespace(is_cancelled=lambda: False))

    assert result.capability_result.status == "failed"
    assert inner.calls == []


def test_replay_outcome_returns_stored_result_without_gateway() -> None:
    from app.assistant.capability_calls.dispatcher import LedgerDispatcher
    from app.assistant.provider_loop.contracts import (
        LedgerPrepareOutcome,
        ProviderDispatchResult,
    )

    inner = _RecordingInner()
    stored = ProviderDispatchResult.model_construct(
        capability_result=SimpleNamespace(status="succeeded"),
        next_manifest=None,
    )
    aggregate = _RecordingAggregate(
        outcome=LedgerPrepareOutcome(
            kind="replay",
            call_id=uuid4(),
            call_revision=3,
            provider_result=stored,
        )
    )
    dispatcher = LedgerDispatcher(inner=inner, aggregate=aggregate)

    result = dispatcher.dispatch(object(), cancellation=SimpleNamespace(is_cancelled=lambda: False))

    assert result is stored
    assert inner.calls == []


def test_v2_factory_freezes_tagged_server_decision_for_read() -> None:
    from app.assistant.provider_loop.messages import ProviderToolCall

    db, runtime, ports = _compose(
        ledger_mode="legacy_read_only", policy_contract_version=2
    )
    try:
        resolved = ports.tools_provider.resolve(
            runtime.manifest,
            scope=runtime.authorization_factory.scope,
            locale="en",
        )
        definition = resolved.surface.tools[0]
        call = ProviderToolCall.model_construct(
            call_id="v2-read-1",
            domain_key=definition.domain_key,
        )
        runtime.authorization_factory.issue(
            call=call,
            binding=definition.binding,
            descriptor=definition.descriptor,
            scope=runtime.authorization_factory.scope,
        )
        decision = runtime.authorization_factory.decision_for_call(
            call_id="v2-read-1"
        )
        assert decision.contract_version == 2
        assert decision.dispatch_disposition == "dispatch"
    finally:
        db.close()


def test_server_decision_forces_write_pause_without_orphan_call() -> None:
    from tests._db import make_session
    from tests.test_capability_call_repository import _make_main_agent_run
    from app.assistant.capability_calls.aggregate import DurableCapabilityLedgerAggregate
    from app.assistant.capability_calls.models import AssistantCapabilityCall
    from app.assistant.durable.repository import LeaseToken
    from app.assistant.policy.contracts import (
        GOLDEN_WRITE_LATTICE_PREFIX,
        build_authorization_decision_v2,
    )

    db = make_session()
    try:
        run = _make_main_agent_run(
            db,
            status="running",
            state_revision=4,
            capability_ledger_mode="enforced",
        )
        run.lease_owner = "worker-1"
        run.lease_generation = 2
        db.commit()
        decision = build_authorization_decision_v2(
            policy_allowed=True,
            dispatch_disposition="awaiting_call_approval",
            reason_code="awaiting_call_approval",
            principal_digest="a" * 64,
            entrypoint_policy_digest="a" * 64,
            global_policy_digest="a" * 64,
            owner_policy_digest="a" * 64,
            allowed_side_effects=GOLDEN_WRITE_LATTICE_PREFIX,
            grant_source_digest="b" * 64,
            exposure_digest="a" * 64,
            effective_policy_digest="a" * 64,
            write_release_digest="b" * 64,
        )
        factory = SimpleNamespace(
            decision_for_call=lambda **_kwargs: decision,
        )
        request = SimpleNamespace(
            execution_scope=SimpleNamespace(run_id=run.id),
            call=SimpleNamespace(
                call_id="provider-write-1",
                domain_key="create_entry",
                arguments={"title": "safe"},
            ),
            descriptor=SimpleNamespace(
                behavior=SimpleNamespace(side_effect="write_local"),
                target_version_id=None,
                descriptor_digest="c" * 64,
            ),
            binding=SimpleNamespace(
                ref=SimpleNamespace(
                    binding_contract_digest="d" * 64,
                    resolution_digest="e" * 64,
                )
            ),
        )
        aggregate = DurableCapabilityLedgerAggregate(
            db=db,
            authorization_factory=factory,
            idempotency_secret="s" * 32,
            lease=LeaseToken(
                run_id=run.id, worker_id="worker-1", lease_generation=2
            ),
        )
        outcome = aggregate.prepare(request)
        assert outcome.kind == "pause"
        assert db.query(AssistantCapabilityCall).count() == 0
    finally:
        db.close()


def test_pause_outcome_is_a_portable_waiting_result() -> None:
    from app.assistant.capability_calls.dispatcher import LedgerDispatcher
    from app.assistant.provider_loop.contracts import LedgerPrepareOutcome

    inner = _RecordingInner()
    aggregate = _RecordingAggregate(
        outcome=LedgerPrepareOutcome(
            kind="pause",
            call_id=uuid4(),
            call_revision=0,
            pause_proposal={
                "interruptId": str(uuid4()),
                "proposalDigest": "a" * 64,
            },
        )
    )
    result = LedgerDispatcher(inner=inner, aggregate=aggregate).dispatch(
        SimpleNamespace(current_manifest=SimpleNamespace()),
        cancellation=SimpleNamespace(is_cancelled=lambda: False),
    )
    assert result.capability_result.status == "waiting"
    assert result.capability_result.continuation.continuation_type == "capability_call"
    assert inner.calls == []

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


def _compose(*, ledger_mode: str, aggregate: Any = None):
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

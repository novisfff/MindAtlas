"""Plan 05 Task 4: atomic reservations integrated with scheduler and Gateway."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.capabilities.contracts import (  # noqa: E402
    CapabilityAuthorizationEvidence,
    CapabilityAvailability,
    CapabilityBehavior,
    CapabilityDescriptor,
    CapabilityError,
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityMetrics,
    CapabilityOwnerRef,
    CapabilityPrincipal,
    CapabilityResult,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
    ContinuationRef,
    FrozenBindingProvenance,
    completed_result,
    project_frozen_capability_binding,
)
from app.assistant.capabilities.errors import CapabilityDomainError  # noqa: E402
from app.assistant.capabilities.gateway import CapabilityGateway  # noqa: E402
from app.assistant.capabilities.ports import (  # noqa: E402
    CapabilityRuntimePorts,
    NoOpCapabilityDispatchGuard,
)
from app.assistant.domain.contracts import (  # noqa: E402
    CapabilityCompletionContract,
    ModelRef,
    ResolvedCapabilityBinding,
    ResolvedMainAgentRef,
    ResolvedRunManifestRevision,
    create_base_run_manifest,
    create_model_ref,
    create_provider_ref,
)
from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.domain.json_schema import (  # noqa: E402
    binding_schema_digest,
    normalize_binding_schema,
)
from app.assistant.policy.budgets import (  # noqa: E402
    BudgetLedger,
    DeterministicBudgetClock,
)
from app.assistant.policy.contracts import (  # noqa: E402
    normalize_owner_budget_limits,
    normalize_run_budget_limits,
)
from app.assistant.policy.runtime import (  # noqa: E402
    BudgetLedgerDispatchGuard,
    BudgetLedgerReservationPort,
    BudgetLedgerRoundGuard,
    DomainKeyOwnerResolver,
    FixedOwnerResolver,
)
from app.assistant.provider_loop.aliases import (  # noqa: E402
    OPENAI_CHAT_PROVIDER_PROTOCOL,
    build_provider_tool_surface,
)
from app.assistant.provider_loop.contracts import (  # noqa: E402
    CapabilityCallReservationItem,
    NoOpCapabilityCallReservationPort,
    NoOpProviderRoundBudgetGuard,
    ProviderDispatchRequest,
    ProviderDispatchResult,
    ProviderGenerationOptions,
    ProviderLoopPorts,
    ProviderLoopRequest,
    ProviderLoopResumeRequest,
    ProviderRoundBudgetDeniedError,
    ProviderRoundRequest,
    ProviderRoundResult,
    ProviderRoundTerminal,
    ProviderToolCallDelta,
    ProviderToolChoice,
    ProviderUsage,
    ProviderUsageSnapshot,
    ProviderWaitingResolution,
    create_execution_scope,
)
from app.assistant.provider_loop.loop import (  # noqa: E402
    resume_provider_agent_loop,
    run_provider_agent_loop,
)
from app.assistant.provider_loop.messages import (  # noqa: E402
    ProviderAssistantMessage,
    ProviderToolCall,
    ProviderUserMessage,
    digest_arguments,
)
from app.assistant.provider_loop.scheduler import (  # noqa: E402
    BoundedIsolatedSiblingExecutor,
    DispatcherCapabilities,
    SequentialSiblingExecutor,
    plan_sibling_execution,
)
from app.assistant.provider_loop.scripted_provider import text_then_terminal  # noqa: E402
from app.assistant.skills.resolution import build_binding_snapshot  # noqa: E402

# Reuse proven gateway test helpers for mark_started boundary coverage.
from tests import test_capability_gateway as gw_helpers  # noqa: E402


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
DIGEST_3 = "3" * 64
DIGEST_4 = "4" * 64
DIGEST_5 = "5" * 64

PROFILE_VERSION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SKILL_VERSION_A = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
SKILL_VERSION_B = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
RUN_ID = UUID("00000000-0000-4000-8000-000000000601")
CONV_ID = UUID("00000000-0000-4000-8000-000000000602")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000610")
MODEL_ID = UUID("00000000-0000-4000-8000-000000000650")
CREDENTIAL_ID = UUID("00000000-0000-4000-8000-000000000651")
PROVIDER_CONFIG_ID = UUID("00000000-0000-4000-8000-000000000640")

P = OPENAI_CHAT_PROVIDER_PROTOCOL
ADAPTER_KEY = "openai"
ADAPTER_REVISION = "a1"
MODEL_CONFIG = DIGEST_5

_ARGS_EMPTY = sha256_canonical_json({})
_ARGS_ONE = sha256_canonical_json({"query": "one"})
_ARGS_TWO = sha256_canonical_json({"query": "two"})
_BINDING_A = "a" * 64


def _clock() -> DeterministicBudgetClock:
    return DeterministicBudgetClock(
        utc_start=datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc),
        monotonic_start_ms=1_000_000,
    )


def _main_owner(limits=None):
    run = limits or normalize_run_budget_limits()
    return normalize_owner_budget_limits(
        owner_kind="main_agent",
        owner_version_id=PROFILE_VERSION_ID,
        run_limits=run,
    )


def _skill_owner(version_id: UUID = SKILL_VERSION_A, *, max_calls: int = 4, limits=None):
    run = limits or normalize_run_budget_limits()
    return normalize_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=version_id,
        run_limits=run,
        max_skill_calls=max_calls,
        max_same_read_calls=2,
    )


def _ledger(*, limits=None, owners=None, clock=None, events=None) -> BudgetLedger:
    clock = clock or _clock()
    limits = limits or normalize_run_budget_limits()
    owner_limits = owners if owners is not None else [_main_owner(limits)]
    sink = None if events is None else events.append
    return BudgetLedger.create(
        limits=limits,
        owner_limits=owner_limits,
        clock=clock,
        event_sink=sink,
    )


def _reserve_item(
    call_id: str,
    *,
    owner_kind: str = "main_agent",
    owner_version_id: UUID = PROFILE_VERSION_ID,
    domain_key: str = "skill.search",
    side_effect: str = "read",
    arguments_digest: str = _ARGS_EMPTY,
    binding_contract_digest: str = _BINDING_A,
) -> CapabilityCallReservationItem:
    return CapabilityCallReservationItem(
        call_id=call_id,
        owner_kind=owner_kind,
        owner_version_id=owner_version_id,
        domain_key=domain_key,
        side_effect=side_effect,
        arguments_digest=arguments_digest,
        binding_contract_digest=binding_contract_digest,
        capability_depth=1,
        agent_depth=1,
    )


# ---------------------------------------------------------------------------
# No-op defaults / import boundary
# ---------------------------------------------------------------------------


def test_noop_dispatch_guard_is_default_on_runtime_ports() -> None:
    cancel = SimpleNamespace(is_cancelled=lambda: False, raise_if_cancelled=lambda: None)
    events = SimpleNamespace(emit=lambda e: None)
    ports = CapabilityRuntimePorts(cancellation=cancel, events=events)  # type: ignore[arg-type]
    assert isinstance(ports.dispatch_guard, NoOpCapabilityDispatchGuard)
    ports.dispatch_guard.mark_started(call_id="c1", validated_arguments_digest=_ARGS_EMPTY)
    ports.dispatch_guard.finish(call_id="c1", status="completed")
    ports.dispatch_guard.release_unstarted(call_id="c2", reason_code="cancelled")


def test_noop_round_and_reservation_defaults_on_provider_loop_ports() -> None:
    ports = ProviderLoopPorts(
        provider=SimpleNamespace(),  # type: ignore[arg-type]
        tools_provider=SimpleNamespace(),  # type: ignore[arg-type]
        current_descriptors=SimpleNamespace(),  # type: ignore[arg-type]
        authorization_evidence=SimpleNamespace(),  # type: ignore[arg-type]
        tool_dispatcher=SimpleNamespace(),  # type: ignore[arg-type]
        sibling_executor=SequentialSiblingExecutor(),
        cancellation=SimpleNamespace(is_cancelled=lambda: False),  # type: ignore[arg-type]
        events=SimpleNamespace(emit=lambda *a, **k: None),  # type: ignore[arg-type]
    )
    assert isinstance(ports.round_budget_guard, NoOpProviderRoundBudgetGuard)
    assert isinstance(ports.call_reservation, NoOpCapabilityCallReservationPort)
    item = _reserve_item("c1")
    d = ports.call_reservation.reserve_one(item)
    assert d.allowed is True
    assert d.reserved_call_ids == ("c1",)
    batch = ports.call_reservation.reserve_batch([_reserve_item("a"), _reserve_item("b")])
    assert batch.allowed is True
    assert set(batch.reserved_call_ids) == {"a", "b"}


def test_provider_loop_modules_do_not_import_policy() -> None:
    import app.assistant.provider_loop.contracts as c
    import app.assistant.provider_loop.loop as loop
    import app.assistant.provider_loop.scheduler as sched

    for mod in (c, loop, sched):
        src = open(mod.__file__, encoding="utf-8").read()
        assert "app.assistant.policy" not in src
        assert "BudgetLedgerState" not in src
        assert "ObligationLedgerState" not in src


# ---------------------------------------------------------------------------
# Gateway mark_started boundary (via gateway test helpers)
# ---------------------------------------------------------------------------


@dataclass
class RecordingDispatchGuard:
    events: list[tuple[str, str, str | None]] = field(default_factory=list)
    deny_start_for: set[str] = field(default_factory=set)
    deny_reason: str = "budget_exhausted_total_calls"
    expected_digest: dict[str, str] = field(default_factory=dict)

    def mark_started(self, *, call_id: str, validated_arguments_digest: str) -> None:
        expected = self.expected_digest.get(call_id)
        if expected is not None and expected != validated_arguments_digest:
            self.events.append(("release_unstarted", call_id, "arguments_digest_mismatch"))
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="unauthorized",
                    safe_code="arguments_digest_mismatch",
                    safe_message="arguments digest mismatch at start",
                    retry_disposition="never",
                    call_id=call_id,
                )
            )
        if call_id in self.deny_start_for:
            self.events.append(("release_unstarted", call_id, self.deny_reason))
            raise CapabilityDomainError(
                CapabilityError(
                    error_type="unauthorized",
                    safe_code=self.deny_reason,
                    safe_message="capability call budget denied at start",
                    retry_disposition="never",
                    call_id=call_id,
                )
            )
        self.events.append(("mark_started", call_id, validated_arguments_digest))

    def finish(self, *, call_id: str, status: str) -> None:
        self.events.append(("finish", call_id, status))

    def release_unstarted(self, *, call_id: str, reason_code: str) -> None:
        self.events.append(("release_unstarted", call_id, reason_code))


def test_gateway_mark_started_only_immediately_before_adapter() -> None:
    gw, _reg, _pol, ads, binding, _target = gw_helpers._gateway()
    guard = RecordingDispatchGuard()
    ports = CapabilityRuntimePorts(
        cancellation=gw_helpers._FakeCancellation(),  # type: ignore[arg-type]
        events=gw_helpers._RecordingEventSink(),  # type: ignore[arg-type]
        dispatch_guard=guard,  # type: ignore[arg-type]
    )
    args = {"query": "hello"}
    args_digest = sha256_canonical_json(args)
    guard.expected_digest["call-1"] = args_digest
    request = CapabilityExecutionRequest(
        binding=binding,
        input=args,
        context=gw_helpers._context("call-1"),
        authorization=gw_helpers._evidence(call_id="call-1"),
    )
    result = gw.execute(request, ports=ports)
    assert result.status == "completed"
    assert ads["tool"].calls  # type: ignore[attr-defined]
    kinds = [e[0] for e in guard.events]
    assert kinds == ["mark_started", "finish"]
    assert guard.events[0][2] == args_digest
    assert guard.events[1][2] == "completed"


def test_gateway_invalid_input_releases_unstarted_not_started() -> None:
    gw, _reg, _pol, ads, binding, _target = gw_helpers._gateway()
    guard = RecordingDispatchGuard()
    ports = CapabilityRuntimePorts(
        cancellation=gw_helpers._FakeCancellation(),  # type: ignore[arg-type]
        events=gw_helpers._RecordingEventSink(),  # type: ignore[arg-type]
        dispatch_guard=guard,  # type: ignore[arg-type]
    )
    request = CapabilityExecutionRequest(
        binding=binding,
        input={"not_query": 1},
        context=gw_helpers._context("call-bad"),
        authorization=gw_helpers._evidence(call_id="call-bad"),
    )
    result = gw.execute(request, ports=ports)
    assert result.status == "failed"
    assert not ads["tool"].calls  # type: ignore[attr-defined]
    assert any(e[0] == "release_unstarted" for e in guard.events)
    assert not any(e[0] == "mark_started" for e in guard.events)


def test_gateway_cancel_before_adapter_releases_unstarted() -> None:
    gw, _reg, _pol, ads, binding, _target = gw_helpers._gateway()
    guard = RecordingDispatchGuard()
    ports = CapabilityRuntimePorts(
        cancellation=gw_helpers._FakeCancellation(cancelled=True),  # type: ignore[arg-type]
        events=gw_helpers._RecordingEventSink(),  # type: ignore[arg-type]
        dispatch_guard=guard,  # type: ignore[arg-type]
    )
    request = CapabilityExecutionRequest(
        binding=binding,
        input={"query": "x"},
        context=gw_helpers._context("call-c"),
        authorization=gw_helpers._evidence(call_id="call-c"),
    )
    result = gw.execute(request, ports=ports)
    assert result.status == "cancelled"
    assert not ads["tool"].calls  # type: ignore[attr-defined]
    assert any(e[0] == "release_unstarted" for e in guard.events)
    assert not any(e[0] == "mark_started" for e in guard.events)


def test_gateway_digest_mismatch_blocks_and_releases() -> None:
    gw, _reg, _pol, ads, binding, _target = gw_helpers._gateway()
    guard = RecordingDispatchGuard()
    guard.expected_digest["call-d"] = _ARGS_EMPTY
    ports = CapabilityRuntimePorts(
        cancellation=gw_helpers._FakeCancellation(),  # type: ignore[arg-type]
        events=gw_helpers._RecordingEventSink(),  # type: ignore[arg-type]
        dispatch_guard=guard,  # type: ignore[arg-type]
    )
    request = CapabilityExecutionRequest(
        binding=binding,
        input={"query": "different"},
        context=gw_helpers._context("call-d"),
        authorization=gw_helpers._evidence(call_id="call-d"),
    )
    result = gw.execute(request, ports=ports)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.safe_code == "arguments_digest_mismatch"
    assert not ads["tool"].calls  # type: ignore[attr-defined]
    assert any(
        e[0] == "release_unstarted" and e[2] == "arguments_digest_mismatch"
        for e in guard.events
    )
    assert not any(e[0] == "mark_started" for e in guard.events)


def test_gateway_simple_namespace_ports_without_guard_remain_compatible() -> None:
    gw, _reg, _pol, ads, binding, _target = gw_helpers._gateway()
    ports = SimpleNamespace(
        cancellation=gw_helpers._FakeCancellation(),
        events=gw_helpers._RecordingEventSink(),
    )
    request = CapabilityExecutionRequest(
        binding=binding,
        input={"query": "x"},
        context=gw_helpers._context("call-ns"),
        authorization=gw_helpers._evidence(call_id="call-ns"),
    )
    result = gw.execute(request, ports=ports)  # type: ignore[arg-type]
    assert result.status == "completed"
    assert ads["tool"].calls  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# BudgetLedger adapters
# ---------------------------------------------------------------------------


def test_ledger_dispatch_guard_lifecycle_and_digest() -> None:
    ledger = _ledger()
    port = BudgetLedgerReservationPort(ledger=ledger)
    guard = BudgetLedgerDispatchGuard(ledger=ledger)
    item = _reserve_item("c1", arguments_digest=_ARGS_ONE)
    assert port.reserve_one(item).allowed is True
    guard.mark_started(call_id="c1", validated_arguments_digest=_ARGS_ONE)
    assert ledger.snapshot().capability_calls_started == 1
    guard.finish(call_id="c1", status="completed")
    res = next(r for r in ledger.snapshot().reservations if r.call_id == "c1")
    assert res.state == "finished"


def test_ledger_dispatch_guard_digest_mismatch_releases() -> None:
    ledger = _ledger()
    port = BudgetLedgerReservationPort(ledger=ledger)
    guard = BudgetLedgerDispatchGuard(ledger=ledger)
    port.reserve_one(_reserve_item("c1", arguments_digest=_ARGS_ONE))
    with pytest.raises(CapabilityDomainError) as ei:
        guard.mark_started(call_id="c1", validated_arguments_digest=_ARGS_TWO)
    assert ei.value.error.safe_code == "arguments_digest_mismatch"
    res = next(r for r in ledger.snapshot().reservations if r.call_id == "c1")
    assert res.state == "released"
    assert ledger.snapshot().capability_calls_started == 0


def test_ledger_release_unstarted_before_start() -> None:
    ledger = _ledger()
    port = BudgetLedgerReservationPort(ledger=ledger)
    guard = BudgetLedgerDispatchGuard(ledger=ledger)
    port.reserve_one(_reserve_item("c1"))
    guard.release_unstarted(call_id="c1", reason_code="invalid_input")
    res = next(r for r in ledger.snapshot().reservations if r.call_id == "c1")
    assert res.state == "released"
    assert ledger.snapshot().capability_calls_started == 0


def test_reuse_reserved_requires_exact_reserved_state_and_arguments_digest() -> None:
    ledger = _ledger()
    port = BudgetLedgerReservationPort(ledger=ledger)
    item = _reserve_item("approval-call", arguments_digest=_ARGS_ONE)
    assert port.reserve_one(item).allowed

    mismatch = port.reuse_reserved(
        _reserve_item("approval-call", arguments_digest=_ARGS_TWO)
    )
    assert mismatch.allowed is False
    assert mismatch.reason_code == "reservation_identity_mismatch"

    for mismatch_item in (
        _reserve_item(
            "approval-call",
            owner_version_id=SKILL_VERSION_A,
            arguments_digest=_ARGS_ONE,
        ),
        _reserve_item(
            "approval-call",
            domain_key="other.domain",
            arguments_digest=_ARGS_ONE,
        ),
        _reserve_item(
            "approval-call",
            side_effect="compute",
            arguments_digest=_ARGS_ONE,
        ),
    ):
        decision = port.reuse_reserved(mismatch_item)
        assert decision.allowed is False
        assert decision.reason_code == "reservation_identity_mismatch"

    assert port.reuse_reserved(item).allowed
    BudgetLedgerDispatchGuard(ledger=ledger).mark_started(
        call_id="approval-call", validated_arguments_digest=_ARGS_ONE
    )
    started = port.reuse_reserved(item)
    assert started.allowed is False
    assert started.reason_code == "reservation_state_invalid"


def test_reserve_batch_all_or_none_partial_capacity() -> None:
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_total_capability_calls": 2,
            "max_parallel_calls": 2,
            "max_same_read_signature": 2,
        }
    )
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)])
    port = BudgetLedgerReservationPort(ledger=ledger)
    # Distinct digests so signature limits don't interfere with total capacity.
    assert port.reserve_one(
        _reserve_item("x1", arguments_digest=sha256_canonical_json({"q": "x1"}))
    ).allowed
    assert port.reserve_one(
        _reserve_item("x2", arguments_digest=sha256_canonical_json({"q": "x2"}))
    ).allowed
    d = port.reserve_batch(
        [
            _reserve_item("b1", arguments_digest=sha256_canonical_json({"q": "b1"})),
            _reserve_item("b2", arguments_digest=sha256_canonical_json({"q": "b2"})),
        ]
    )
    assert d.allowed is False
    assert d.reason_code in {
        "budget_exhausted_total_calls",
        "budget_exhausted_parallel",
    }
    assert d.reserved_call_ids == ()
    ids = {r.call_id for r in ledger.snapshot().reservations if r.state == "reserved"}
    assert ids == {"x1", "x2"}


def test_reserve_batch_same_and_different_owners() -> None:
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_total_capability_calls": 8,
            "max_parallel_calls": 4,
            "max_same_read_signature": 4,
        }
    )
    ledger = _ledger(
        limits=limits,
        owners=[
            _main_owner(limits),
            _skill_owner(SKILL_VERSION_A, max_calls=2, limits=limits),
            _skill_owner(SKILL_VERSION_B, max_calls=2, limits=limits),
        ],
    )
    port = BudgetLedgerReservationPort(ledger=ledger)
    same = port.reserve_batch(
        [
            _reserve_item(
                "s1",
                owner_kind="skill_version",
                owner_version_id=SKILL_VERSION_A,
                arguments_digest=sha256_canonical_json({"q": "s1"}),
            ),
            _reserve_item(
                "s2",
                owner_kind="skill_version",
                owner_version_id=SKILL_VERSION_A,
                arguments_digest=sha256_canonical_json({"q": "s2"}),
            ),
        ]
    )
    assert same.allowed is True
    mixed = port.reserve_batch(
        [
            _reserve_item(
                "m1",
                owner_kind="skill_version",
                owner_version_id=SKILL_VERSION_B,
                arguments_digest=sha256_canonical_json({"q": "m1"}),
            ),
            _reserve_item(
                "m2",
                owner_kind="main_agent",
                owner_version_id=PROFILE_VERSION_ID,
                arguments_digest=sha256_canonical_json({"q": "m2"}),
            ),
        ]
    )
    assert mixed.allowed is True


def test_reserve_batch_duplicate_signatures_and_contention() -> None:
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_total_capability_calls": 8,
            "max_parallel_calls": 4,
            "max_same_read_signature": 2,
        }
    )
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)])
    port = BudgetLedgerReservationPort(ledger=ledger)
    d = port.reserve_batch(
        [
            _reserve_item("d1", arguments_digest=_ARGS_ONE),
            _reserve_item("d2", arguments_digest=_ARGS_ONE),
            _reserve_item("d3", arguments_digest=_ARGS_ONE),
        ]
    )
    assert d.allowed is False
    assert "signature" in d.reason_code

    # Two identical signatures are allowed under max_same_read_signature=2.
    d2 = port.reserve_batch(
        [
            _reserve_item("ok1", arguments_digest=_ARGS_TWO),
            _reserve_item("ok2", arguments_digest=_ARGS_TWO),
        ]
    )
    assert d2.allowed is True

    limits2 = normalize_run_budget_limits(
        operator_limits={
            "max_total_capability_calls": 1,
            "max_parallel_calls": 1,
            "max_same_read_signature": 1,
        }
    )
    ledger2 = _ledger(limits=limits2, owners=[_main_owner(limits2)])
    port2 = BudgetLedgerReservationPort(ledger=ledger2)
    results: list[bool] = []
    lock = threading.Lock()

    def worker(cid: str) -> None:
        decision = port2.reserve_one(
            _reserve_item(cid, arguments_digest=sha256_canonical_json({"id": cid}))
        )
        with lock:
            results.append(decision.allowed)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(worker, f"c{i}") for i in range(4)]
        for f in as_completed(futs):
            f.result()
    assert results.count(True) == 1
    assert results.count(False) == 3


def test_reserve_batch_deadline_and_cancel() -> None:
    clock = _clock()
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_wall_time_ms": 100,
            "max_total_capability_calls": 8,
            "max_same_read_signature": 3,
        }
    )
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)], clock=clock)
    port = BudgetLedgerReservationPort(ledger=ledger)
    clock.advance_monotonic(milliseconds=200)
    d = port.reserve_batch(
        [
            _reserve_item("late1", arguments_digest=sha256_canonical_json({"q": "l1"})),
            _reserve_item("late2", arguments_digest=sha256_canonical_json({"q": "l2"})),
        ]
    )
    assert d.allowed is False
    assert d.reason_code == "budget_exhausted_deadline"

    ledger2 = _ledger()
    ledger2.cancel()
    port2 = BudgetLedgerReservationPort(ledger=ledger2)
    d2 = port2.reserve_one(_reserve_item("c"))
    assert d2.allowed is False
    assert d2.reason_code == "cancelled"


# ---------------------------------------------------------------------------
# Provider round budget guard
# ---------------------------------------------------------------------------


def _model() -> ModelRef:
    provider = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_3,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    return create_model_ref(
        model_id=MODEL_ID,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=2,
        credential_id=CREDENTIAL_ID,
        credential_runtime_revision=3,
        credential_config_digest=DIGEST_4,
        model_config_digest=MODEL_CONFIG,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=None,
        capability_probe_digest=None,
    )


def _manifest(run_id: UUID = RUN_ID) -> ResolvedRunManifestRevision:
    provider = create_provider_ref(
        provider_protocol="openai_compat",
        provider_config_id=PROVIDER_CONFIG_ID,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_3,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        protocol_revision="p1",
        app_build_revision="build-1",
    )
    return create_base_run_manifest(
        run_id=run_id,
        main_agent=ResolvedMainAgentRef(
            profile_id=PROFILE_ID,
            version_id=PROFILE_VERSION_ID,
            profile_key="general_chat",
            sequence=1,
            content_digest=DIGEST_A,
        ),
        provider=provider,
        model=_model(),
        effective_policy_digest=None,
    )


def _scope(*, run_id: UUID = RUN_ID):
    return create_execution_scope(
        run_id=run_id,
        conversation_id=CONV_ID,
        principal=CapabilityPrincipal(
            principal_type="test",
            principal_id="principal-loop",
            authenticated=True,
        ),
        tenant_scope_id=None,
    )


def _empty_surface(manifest: ResolvedRunManifestRevision):
    resolution = build_provider_tool_surface(
        manifest=manifest,
        provider_protocol=P,
        visible=(),
        scope=_scope(run_id=manifest.run_id),
    )
    return resolution.surface


def test_round_budget_guard_starts_round_and_caps_tokens() -> None:
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_provider_rounds": 3,
            "max_completion_tokens": 100,
            "max_completion_followup_rounds": 1,
        }
    )
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)])
    guard = BudgetLedgerRoundGuard(ledger=ledger)
    model = _model()
    manifest = _manifest()
    surface = _empty_surface(manifest)
    req = ProviderRoundRequest(
        round_index=0,
        messages=(ProviderUserMessage(content="hi"),),
        tool_surface=surface,
        tools_enabled=True,
        finalization_round=False,
        model_ref=model,
        generation=ProviderGenerationOptions(
            max_output_tokens=500,
            temperature=None,
            tool_choice=ProviderToolChoice(mode="auto"),
        ),
    )
    gen = guard.before_round(req)
    assert gen.max_output_tokens == 100
    assert ledger.snapshot().provider_rounds_started == 1

    result = ProviderRoundResult(
        assistant_message=ProviderAssistantMessage(content="ok", tool_calls=()),
        finish_reason="stop",
        usage=ProviderUsage(input_tokens=10, output_tokens=40, total_tokens=50),
    )
    guard.after_round(result)
    snap = ledger.snapshot()
    assert snap.prompt_tokens_used == 10
    assert snap.completion_tokens_used == 40
    assert snap.provider_rounds_started == 1


def test_round_budget_guard_denies_when_rounds_exhausted() -> None:
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_provider_rounds": 2,
            "max_completion_followup_rounds": 1,
        }
    )
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)])
    guard = BudgetLedgerRoundGuard(ledger=ledger)
    model = _model()
    surface = _empty_surface(_manifest())
    req = ProviderRoundRequest(
        round_index=0,
        messages=(ProviderUserMessage(content="hi"),),
        tool_surface=surface,
        tools_enabled=False,
        finalization_round=False,
        model_ref=model,
        generation=ProviderGenerationOptions(
            max_output_tokens=16,
            tool_choice=ProviderToolChoice(mode="none"),
        ),
    )
    guard.before_round(req)
    guard.before_round(req)
    with pytest.raises(ProviderRoundBudgetDeniedError) as ei:
        guard.before_round(req)
    assert ei.value.reason_code == "budget_exhausted_provider_rounds"


# ---------------------------------------------------------------------------
# Loop integration helpers (aligned with multi-tool test contracts)
# ---------------------------------------------------------------------------


def _resolved_binding(
    *,
    capability_key: str,
    capability_type: str = "tool",
    target_id: UUID | None = None,
    config_digest: str = DIGEST_B,
) -> ResolvedCapabilityBinding:
    input_schema = normalize_binding_schema(
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )
    output_schema = normalize_binding_schema({"type": "string"}, require_object_root=False)
    completion = CapabilityCompletionContract()
    target = target_id or uuid4()
    target_identity = f"{capability_type}:{target}"
    executable_revision = "1"
    input_digest = binding_schema_digest(input_schema)
    output_digest = binding_schema_digest(output_schema)
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": capability_type,
            "targetIdentity": target_identity,
            "targetId": str(target),
            "targetVersionId": None,
            "targetRevision": 1,
            "inputSchemaDigest": input_digest,
            "outputSchemaDigest": output_digest,
            "executableRevision": executable_revision,
            "configDigest": config_digest,
            "systemToolContractSetDigest": None,
        }
    )
    snapshot, closure_digest, contract_digest = build_binding_snapshot(
        capability_type=capability_type,  # type: ignore[arg-type]
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        target_revision=1,
        input_schema=input_schema,
        output_schema=output_schema,
        completion=completion,
        config_digest=config_digest,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        dependencies=(),
    )
    return ResolvedCapabilityBinding(
        capability_type=capability_type,  # type: ignore[arg-type]
        capability_key=capability_key,
        target_identity=target_identity,
        target_id=target,
        target_version_id=None,
        resolved_tool_id=target if capability_type == "tool" else None,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=target if capability_type == "agent" else None,
        resolved_revision=1,
        input_schema=input_schema,
        output_schema=output_schema,
        input_schema_digest=input_digest,
        output_schema_digest=output_digest,
        completion=completion,
        config_digest=config_digest,
        executable_revision=executable_revision,
        resolution_digest=resolution_digest,
        resolution_snapshot=snapshot,
        dependencies=(),
        dependency_closure_digest=closure_digest,
        binding_contract_digest=contract_digest,
    )


def _frozen(*, capability_key: str, capability_type: str = "tool", target_id: UUID | None = None):
    resolved = _resolved_binding(
        capability_key=capability_key,
        capability_type=capability_type,
        target_id=target_id,
    )
    return project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_D,
        ),
    )


def _descriptor(
    binding,
    *,
    parallel_safe: bool = True,
    side_effect: str = "read",
    interrupt_mode: str = "none",
    classification_revision: str = "plan02-v1",
    ruleset_digest: str = DIGEST_A,
    behavior_digest: str = DIGEST_B,
    descriptor_digest: str = DIGEST_C,
):
    resolved = binding.resolved
    behavior = CapabilityBehavior(
        classification=ClassificationContractRef(
            schema_version=1,
            revision=classification_revision,
            ruleset_digest=ruleset_digest,
        ),
        side_effect=side_effect,  # type: ignore[arg-type]
        parallel_safe=parallel_safe,
        interrupt_mode=interrupt_mode,  # type: ignore[arg-type]
        timeout_policy=CapabilityTimeoutPolicy(
            mode="none",
            timeout_seconds=None,
            cancellation_supported=False,
        ),
        behavior_digest=behavior_digest,
    )
    return CapabilityDescriptor(
        capability_key=resolved.capability_key,
        capability_type=resolved.capability_type,
        target_identity=resolved.target_identity,
        target_id=resolved.target_id,
        target_version_id=resolved.target_version_id,
        target_revision=resolved.resolved_revision,
        resolution_digest=resolved.resolution_digest,
        binding_contract_digest=resolved.binding_contract_digest,
        dependency_closure_digest=resolved.dependency_closure_digest,
        display_name=resolved.capability_key,
        description="tool description",
        input_schema=resolved.input_schema,
        output_schema=resolved.output_schema,
        input_schema_digest=resolved.input_schema_digest,
        output_schema_digest=resolved.output_schema_digest,
        descriptor_digest=descriptor_digest,
        executable_revision=resolved.executable_revision or "1",
        behavior=behavior,
        availability=CapabilityAvailability(
            status="available",
            reason_code=None,
            compatibility_only=False,
        ),
        completion=resolved.completion,
    )


def _pair(capability_key: str, **kwargs):
    binding = _frozen(capability_key=capability_key)
    return binding, _descriptor(binding, **kwargs)


@dataclass
class _RecCancellation:
    cancelled: bool = False

    def is_cancelled(self) -> bool:
        return self.cancelled


@dataclass
class _RecEvents:
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))


@dataclass
class _RecTools:
    """Return one or more prebuilt ToolSurfaceResolution values (matching multi-tool tests)."""

    resolutions: list[Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def resolve(self, manifest, *, scope, locale):
        self.calls.append(
            {
                "manifest_digest": manifest.manifest_digest,
                "scope_digest": scope.scope_digest,
                "locale": locale,
            }
        )
        if not self.resolutions:
            # Reuse last surface for follow-up rounds when only one was supplied.
            raise AssertionError("tools provider exhausted")
        if len(self.resolutions) == 1:
            return self.resolutions[0]
        return self.resolutions.pop(0)


@dataclass
class _RecVerifier:
    def require_current(self, *, binding, exposed_descriptor, scope):
        del binding, scope
        return exposed_descriptor


@dataclass
class _RecAuth:
    def issue(self, *, call, binding, descriptor, scope):
        return CapabilityAuthorizationEvidence(
            issuer="test",
            entrypoint="test",
            call_id=call.call_id,
            principal=scope.principal,
            owner=CapabilityOwnerRef(
                owner_kind="test",
                owner_id="owner-1",
                owner_version_id=None,
            ),
            capability_key=descriptor.capability_key,
            resolution_digest=descriptor.resolution_digest,
            binding_contract_digest=binding.ref.binding_contract_digest,
            dependency_closure_digest=descriptor.dependency_closure_digest,
            allowed_side_effects=("none", "compute", "read", "write_local"),
            grant_source_digest=DIGEST_E,
            evidence_digest=DIGEST_F,
        )


@dataclass
class _RecDispatcher:
    requests: list[ProviderDispatchRequest] = field(default_factory=list)
    results_by_call_id: dict[str, ProviderDispatchResult] = field(default_factory=dict)
    dispatch_guard: Any | None = None

    def dispatch(self, request: ProviderDispatchRequest, *, cancellation):
        del cancellation
        self.requests.append(request)
        call_id = request.call.call_id
        if self.dispatch_guard is not None:
            try:
                self.dispatch_guard.mark_started(
                    call_id=call_id,
                    validated_arguments_digest=request.call.arguments_digest,
                )
                result = self.results_by_call_id[call_id]
                self.dispatch_guard.finish(
                    call_id=call_id, status=result.capability_result.status
                )
                return result
            except CapabilityDomainError as exc:
                try:
                    self.dispatch_guard.release_unstarted(
                        call_id=call_id, reason_code=exc.error.safe_code
                    )
                except Exception:
                    pass
                return ProviderDispatchResult(
                    capability_result=CapabilityResult(
                        status="failed",
                        user_text=None,
                        structured_output=None,
                        artifact_refs=(),
                        continuation=None,
                        terminal_output=False,
                        needs_followup=False,
                        error=exc.error,
                        metrics=CapabilityMetrics(
                            duration_ms=0.0, input_bytes=0, output_bytes=0
                        ),
                    ),
                    next_manifest=request.current_manifest,
                )
        return self.results_by_call_id[call_id]


@dataclass
class _TrackingReservationPort:
    inner: BudgetLedgerReservationPort
    calls: list[tuple[str, str]] = field(default_factory=list)

    def reserve_one(self, item):
        self.calls.append(("reserve_one", item.call_id))
        return self.inner.reserve_one(item)

    def reserve_batch(self, items):
        self.calls.extend(("reserve_batch", item.call_id) for item in items)
        return self.inner.reserve_batch(items)

    def reuse_reserved(self, item):
        self.calls.append(("reuse_reserved", item.call_id))
        return self.inner.reuse_reserved(item)


def _completed_dispatch(manifest: ResolvedRunManifestRevision) -> ProviderDispatchResult:
    return ProviderDispatchResult(
        capability_result=completed_result(
            structured_output={"ok": True},
            metrics=CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=0),
        ),
        next_manifest=manifest,
    )


def _tool_events(specs, *, usage=None):
    events = []
    seq = 0
    for index, (cid, alias, args) in enumerate(specs):
        events.append(
            ProviderToolCallDelta(
                sequence=seq,
                call_index=index,
                call_id=cid,
                provider_alias_delta=alias,
                arguments_delta=json.dumps(args, separators=(",", ":"), sort_keys=True),
            )
        )
        seq += 1
    if usage is not None:
        events.append(ProviderUsageSnapshot(sequence=seq, usage=usage))
        seq += 1
    events.append(ProviderRoundTerminal(sequence=seq, finish_reason="tool_calls"))
    return events


@dataclass
class _FlexProvider:
    """Request-count driven fake provider (avoids brittle full-transcript scripting)."""

    provider_protocol: str
    adapter_key: str
    adapter_revision: str
    model_config_digest: str
    expected_model_ref: ModelRef
    round_scripts: list[Any] = field(default_factory=list)
    request_count: int = 0

    def stream_round(self, request: ProviderRoundRequest, *, cancellation):
        del cancellation
        self.request_count += 1
        if not self.round_scripts:
            raise AssertionError(f"unexpected provider request #{self.request_count}")
        script = self.round_scripts.pop(0)
        if callable(script):
            yield from script(request)
        else:
            yield from script


def test_loop_with_noop_defaults_preserves_multi_call_behavior() -> None:
    manifest = _manifest()
    scope = _scope()
    model = _model()
    pairs = (
        _pair("skill.search", parallel_safe=True, descriptor_digest="1" * 64),
        _pair("skill.lookup", parallel_safe=True, descriptor_digest="2" * 64),
    )
    resolution = build_provider_tool_surface(
        manifest=manifest,
        provider_protocol=P,
        visible=pairs,
        scope=scope,
    )
    surface = resolution.surface
    alias_by_domain = {t.domain_key: t.provider_alias for t in surface.tools}
    provider = _FlexProvider(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
        round_scripts=[
            _tool_events(
                [
                    ("c0", alias_by_domain["skill.search"], {"query": "a"}),
                    ("c1", alias_by_domain["skill.lookup"], {"query": "b"}),
                ],
                usage=ProviderUsage(input_tokens=5, output_tokens=3, total_tokens=8),
            ),
            text_then_terminal("done"),
        ],
    )

    dispatcher = _RecDispatcher(
        results_by_call_id={
            "c0": _completed_dispatch(resolution.manifest),
            "c1": _completed_dispatch(resolution.manifest),
        }
    )
    ports = ProviderLoopPorts(
        provider=provider,  # type: ignore[arg-type]
        tools_provider=_RecTools(resolutions=[resolution, resolution]),
        current_descriptors=_RecVerifier(),
        authorization_evidence=_RecAuth(),
        tool_dispatcher=dispatcher,
        sibling_executor=BoundedIsolatedSiblingExecutor(max_workers=2),
        cancellation=_RecCancellation(),
        events=_RecEvents(),
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            execution_scope=scope,
            model_ref=model,
            locale="en",
            max_rounds=4,
            generation=ProviderGenerationOptions(
                max_output_tokens=64,
                tool_choice=ProviderToolChoice(mode="auto"),
            ),
            initial_messages=(ProviderUserMessage(content="go"),),
            manifest=manifest,
        ),
        ports,
    )
    assert result.status == "completed"
    assert result.final_text == "done"
    assert {r.call.call_id for r in result.tool_calls} == {"c0", "c1"}
    assert set(r.call.call_id for r in dispatcher.requests) == {"c0", "c1"}
    assert result.round_count >= 2


def test_loop_batch_reserve_fail_replans_sequentially_with_budget() -> None:
    # max_parallel=1 forces batch of 2 parallel-eligible calls to fail reservation.
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_total_capability_calls": 4,
            "max_parallel_calls": 1,
            "max_provider_rounds": 4,
            "max_completion_followup_rounds": 1,
            "max_same_read_signature": 4,
        }
    )
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)])
    reservation = BudgetLedgerReservationPort(ledger=ledger)
    guard = BudgetLedgerDispatchGuard(ledger=ledger)
    round_guard = BudgetLedgerRoundGuard(ledger=ledger)

    manifest = _manifest()
    scope = _scope()
    model = _model()
    pairs = (
        _pair("skill.search", parallel_safe=True, descriptor_digest="1" * 64),
        _pair("skill.lookup", parallel_safe=True, descriptor_digest="2" * 64),
    )
    resolution = build_provider_tool_surface(
        manifest=manifest, provider_protocol=P, visible=pairs, scope=scope
    )
    surface = resolution.surface
    alias_by_domain = {t.domain_key: t.provider_alias for t in surface.tools}
    provider = _FlexProvider(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
        round_scripts=[
            _tool_events(
                [
                    ("c0", alias_by_domain["skill.search"], {"query": "a"}),
                    ("c1", alias_by_domain["skill.lookup"], {"query": "b"}),
                ],
                usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            ),
            text_then_terminal("all done"),
        ],
    )

    dispatcher = _RecDispatcher(
        results_by_call_id={
            "c0": _completed_dispatch(resolution.manifest),
            "c1": _completed_dispatch(resolution.manifest),
        },
        dispatch_guard=guard,
    )
    ports = ProviderLoopPorts(
        provider=provider,  # type: ignore[arg-type]
        tools_provider=_RecTools(resolutions=[resolution, resolution]),
        current_descriptors=_RecVerifier(),
        authorization_evidence=_RecAuth(),
        tool_dispatcher=dispatcher,
        sibling_executor=BoundedIsolatedSiblingExecutor(max_workers=2),
        cancellation=_RecCancellation(),
        events=_RecEvents(),
        round_budget_guard=round_guard,
        call_reservation=reservation,
        call_owner_resolver=FixedOwnerResolver(
            owner_kind="main_agent",
            owner_version_id=PROFILE_VERSION_ID,
        ),
        dispatch_guard=guard,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            execution_scope=scope,
            model_ref=model,
            locale="en",
            max_rounds=4,
            generation=ProviderGenerationOptions(
                max_output_tokens=64,
                tool_choice=ProviderToolChoice(mode="auto"),
            ),
            initial_messages=(ProviderUserMessage(content="go"),),
            manifest=manifest,
        ),
        ports,
    )
    assert result.status == "completed"
    assert result.final_text == "all done"
    assert [r.call.call_id for r in result.tool_calls] == ["c0", "c1"]
    assert ledger.snapshot().capability_calls_started == 2
    assert ledger.snapshot().provider_rounds_started >= 2
    assert result.round_count >= 2


def test_loop_budget_denial_blocks_and_cancels_suffix() -> None:
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_total_capability_calls": 1,
            "max_parallel_calls": 1,
            "max_provider_rounds": 4,
            "max_completion_followup_rounds": 1,
            "max_same_read_signature": 1,
        }
    )
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)])
    reservation = BudgetLedgerReservationPort(ledger=ledger)
    guard = BudgetLedgerDispatchGuard(ledger=ledger)
    round_guard = BudgetLedgerRoundGuard(ledger=ledger)

    manifest = _manifest()
    scope = _scope()
    model = _model()
    pairs = (
        _pair(
            "skill.search",
            parallel_safe=False,
            side_effect="write_local",
            descriptor_digest="1" * 64,
        ),
        _pair(
            "skill.lookup",
            parallel_safe=False,
            side_effect="write_local",
            descriptor_digest="2" * 64,
        ),
    )
    resolution = build_provider_tool_surface(
        manifest=manifest, provider_protocol=P, visible=pairs, scope=scope
    )
    surface = resolution.surface
    alias_by_domain = {t.domain_key: t.provider_alias for t in surface.tools}
    provider = _FlexProvider(
        provider_protocol=P,
        adapter_key=ADAPTER_KEY,
        adapter_revision=ADAPTER_REVISION,
        model_config_digest=MODEL_CONFIG,
        expected_model_ref=model,
        round_scripts=[
            _tool_events(
                [
                    ("c0", alias_by_domain["skill.search"], {"query": "a"}),
                    ("c1", alias_by_domain["skill.lookup"], {"query": "b"}),
                ],
                usage=ProviderUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            ),
        ],
    )

    dispatcher = _RecDispatcher(
        results_by_call_id={
            "c0": _completed_dispatch(manifest),
            "c1": _completed_dispatch(manifest),
        },
        dispatch_guard=guard,
    )
    ports = ProviderLoopPorts(
        provider=provider,  # type: ignore[arg-type]
        tools_provider=_RecTools(resolutions=[resolution, resolution]),
        current_descriptors=_RecVerifier(),
        authorization_evidence=_RecAuth(),
        tool_dispatcher=dispatcher,
        sibling_executor=SequentialSiblingExecutor(),
        cancellation=_RecCancellation(),
        events=_RecEvents(),
        round_budget_guard=round_guard,
        call_reservation=reservation,
        call_owner_resolver=FixedOwnerResolver(
            owner_kind="main_agent",
            owner_version_id=PROFILE_VERSION_ID,
        ),
        dispatch_guard=guard,
    )
    result = run_provider_agent_loop(
        ProviderLoopRequest(
            execution_scope=scope,
            model_ref=model,
            locale="en",
            max_rounds=4,
            generation=ProviderGenerationOptions(
                max_output_tokens=64,
                tool_choice=ProviderToolChoice(mode="auto"),
            ),
            initial_messages=(ProviderUserMessage(content="go"),),
            manifest=manifest,
        ),
        ports,
    )
    assert any(r.call.call_id == "c0" for r in result.tool_calls)
    c1_records = [r for r in result.tool_calls if r.call.call_id == "c1"]
    assert c1_records
    assert c1_records[0].status in {"blocked", "cancelled_before_start"}
    assert ledger.snapshot().capability_calls_started == 1
    assert [r.call.call_id for r in dispatcher.requests] == ["c0"]


def test_call_owned_approval_resume_reuses_budget_reservation_across_restart() -> None:
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_total_capability_calls": 2,
            "max_parallel_calls": 1,
            "max_provider_rounds": 4,
            "max_completion_followup_rounds": 1,
            "max_same_read_signature": 2,
        }
    )
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)])
    manifest = _manifest()
    scope = _scope()
    model = _model()
    binding, descriptor = _pair(
        "create_entry",
        parallel_safe=False,
        side_effect="write_local",
        interrupt_mode="durable",
    )
    resolution = build_provider_tool_surface(
        manifest=manifest,
        provider_protocol=P,
        visible=((binding, descriptor),),
        scope=scope,
    )
    tool = resolution.surface.tools[0]
    continuation_ref = ContinuationRef(
        continuation_type="capability_call",
        contract_version=1,
        reference_id="approval-1",
        payload_digest=DIGEST_A,
    )
    waiting_result = ProviderDispatchResult(
        capability_result=CapabilityResult(
            status="waiting",
            user_text=None,
            structured_output=None,
            artifact_refs=(),
            continuation=continuation_ref,
            terminal_output=False,
            needs_followup=True,
            error=None,
            metrics=CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=0),
        ),
        next_manifest=resolution.manifest,
    )

    @dataclass
    class _PauseSnapshotLedger:
        snapshots: list[dict[str, Any]] = field(default_factory=list)
        reserved_call_ids: list[tuple[str, ...]] = field(default_factory=list)

        def reserve_siblings(self, requests, provider_messages=()):
            del provider_messages
            self.reserved_call_ids.append(
                tuple(request.call.call_id for request in requests)
            )

        def commit_pause(self, continuation, provider_messages=()):
            del continuation, provider_messages
            self.snapshots.append(ledger.serialize())

        def commit_progress(self, provider_messages=(), **_kwargs):
            del provider_messages

        def commit_recovery_drift(self, provider_messages, *, stale_call_id):
            del provider_messages, stale_call_id

    pause_ledger = _PauseSnapshotLedger()
    initial_dispatcher = _RecDispatcher(
        results_by_call_id={"approval-call": waiting_result},
    )
    initial = run_provider_agent_loop(
        ProviderLoopRequest(
            execution_scope=scope,
            model_ref=model,
            locale="en",
            max_rounds=4,
            generation=ProviderGenerationOptions(
                max_output_tokens=64,
                tool_choice=ProviderToolChoice(mode="auto"),
            ),
            initial_messages=(ProviderUserMessage(content="create"),),
            manifest=manifest,
        ),
        ProviderLoopPorts(
            provider=_FlexProvider(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
                round_scripts=[
                    _tool_events(
                        [("approval-call", tool.provider_alias, {"query": "create"})]
                    ),
                ],
            ),
            tools_provider=_RecTools(resolutions=[resolution]),
            current_descriptors=_RecVerifier(),
            authorization_evidence=_RecAuth(),
            tool_dispatcher=initial_dispatcher,
            sibling_executor=SequentialSiblingExecutor(),
            cancellation=_RecCancellation(),
            events=_RecEvents(),
            call_reservation=BudgetLedgerReservationPort(ledger=ledger),
            call_owner_resolver=FixedOwnerResolver(
                owner_kind="main_agent",
                owner_version_id=PROFILE_VERSION_ID,
            ),
            dispatch_guard=BudgetLedgerDispatchGuard(ledger=ledger),
            capability_ledger=pause_ledger,
        ),
    )
    assert initial.status == "waiting"
    assert initial.continuation is not None
    assert [request.call.call_id for request in initial_dispatcher.requests] == [
        "approval-call"
    ]
    assert len(pause_ledger.snapshots) == 1
    assert pause_ledger.reserved_call_ids == [("approval-call",)]
    paused_state = BudgetLedger.deserialize(pause_ledger.snapshots[0], clock=_clock())
    assert [item.state for item in paused_state.snapshot().reservations] == ["reserved"]

    restarted = BudgetLedger.deserialize(pause_ledger.snapshots[0], clock=_clock())
    resumed_dispatcher = _RecDispatcher(
        results_by_call_id={"approval-call": _completed_dispatch(resolution.manifest)},
        dispatch_guard=BudgetLedgerDispatchGuard(ledger=restarted),
    )
    restarted_reservations = _TrackingReservationPort(
        inner=BudgetLedgerReservationPort(ledger=restarted)
    )
    resumed = resume_provider_agent_loop(
        ProviderLoopResumeRequest(
            manifest=initial.manifest,
            messages=initial.messages,
            continuation=initial.continuation,
            resolved_waiting=ProviderWaitingResolution(
                call_id="approval-call",
                capability_continuation=continuation_ref,
                capability_result=completed_result(
                    user_text="approved",
                    metrics=CapabilityMetrics(
                        duration_ms=1.0, input_bytes=0, output_bytes=0
                    ),
                ),
            ),
        ),
        ProviderLoopPorts(
            provider=_FlexProvider(
                provider_protocol=P,
                adapter_key=ADAPTER_KEY,
                adapter_revision=ADAPTER_REVISION,
                model_config_digest=MODEL_CONFIG,
                expected_model_ref=model,
                round_scripts=[text_then_terminal("done")],
            ),
            tools_provider=_RecTools(resolutions=[resolution]),
            current_descriptors=_RecVerifier(),
            authorization_evidence=_RecAuth(),
            tool_dispatcher=resumed_dispatcher,
            sibling_executor=SequentialSiblingExecutor(),
            cancellation=_RecCancellation(),
            events=_RecEvents(),
            call_reservation=restarted_reservations,  # type: ignore[arg-type]
            call_owner_resolver=FixedOwnerResolver(
                owner_kind="main_agent",
                owner_version_id=PROFILE_VERSION_ID,
            ),
            dispatch_guard=BudgetLedgerDispatchGuard(ledger=restarted),
            capability_ledger=pause_ledger,
        ),
    )
    assert resumed.status == "completed", (
        None if resumed.error is None else resumed.error.semantic_code
    )
    assert resumed.final_text == "done"
    assert [request.call.call_id for request in resumed_dispatcher.requests] == [
        "approval-call"
    ]
    assert pause_ledger.reserved_call_ids == [
        ("approval-call",),
        ("approval-call",),
    ]
    assert ("reuse_reserved", "approval-call") in restarted_reservations.calls
    assert ("reserve_one", "approval-call") not in restarted_reservations.calls
    snapshot = restarted.snapshot()
    assert snapshot.capability_calls_started == 1
    assert [item.state for item in snapshot.reservations] == ["finished"]


def test_plan_sibling_execution_still_groups_parallel_eligible() -> None:
    manifest = _manifest()
    scope = _scope()
    pairs = (
        _pair("a", parallel_safe=True, descriptor_digest="1" * 64),
        _pair("b", parallel_safe=True, descriptor_digest="2" * 64),
    )
    resolution = build_provider_tool_surface(
        manifest=manifest, provider_protocol=P, visible=pairs, scope=scope
    )
    surface = resolution.surface
    calls = []
    for index, tool in enumerate(surface.tools):
        args = {"query": tool.domain_key}
        calls.append(
            ProviderToolCall(
                call_id=f"c{index}",
                call_index=index,
                provider_alias=tool.provider_alias,
                domain_key=tool.domain_key,
                arguments=args,
                arguments_digest=digest_arguments(args),
                binding_contract_digest=tool.binding.ref.binding_contract_digest,
                descriptor_digest=tool.descriptor.descriptor_digest,
                behavior_digest=tool.descriptor.behavior.behavior_digest,
                classification_revision=tool.descriptor.behavior.classification.revision,
                classification_ruleset_digest=tool.descriptor.behavior.classification.ruleset_digest,
                manifest_revision=manifest.revision,
                manifest_digest=manifest.manifest_digest,
                surface_digest=surface.surface_digest,
            )
        )
    groups = plan_sibling_execution(
        tuple(calls),
        surface=surface,
        dispatcher_capabilities=DispatcherCapabilities(
            supports_isolated_parallel=True, max_workers=2
        ),
    )
    assert len(groups) == 1
    assert groups[0].mode == "parallel"
    assert len(groups[0].calls) == 2


def test_owner_resolver_maps_domain_keys() -> None:
    resolver = DomainKeyOwnerResolver(
        owners_by_domain_key={
            "skill.search": ("skill_version", SKILL_VERSION_A),
        },
        default_owner_kind="main_agent",
        default_owner_version_id=PROFILE_VERSION_ID,
    )
    kind, vid = resolver.resolve_owner(
        call=SimpleNamespace(domain_key="skill.search"),
        descriptor=SimpleNamespace(),
    )
    assert kind == "skill_version"
    assert vid == SKILL_VERSION_A
    kind2, vid2 = resolver.resolve_owner(
        call=SimpleNamespace(domain_key="other"),
        descriptor=SimpleNamespace(),
    )
    assert kind2 == "main_agent"
    assert vid2 == PROFILE_VERSION_ID


def test_owner_resolver_rebind_updates_shared_instance() -> None:
    """rebind mutates in place so ports + runtime can share one resolver."""
    resolver = DomainKeyOwnerResolver(
        owners_by_domain_key={
            "skill.search": ("main_agent", PROFILE_VERSION_ID),
        },
        default_owner_kind="main_agent",
        default_owner_version_id=PROFILE_VERSION_ID,
    )
    # Simulate inject accept: rebind ownership without rebuilding ports.
    resolver.rebind(
        {
            "skill.search": ("main_agent", PROFILE_VERSION_ID),
            "get_statistics": ("skill_version", SKILL_VERSION_A),
        }
    )
    kind, vid = resolver.resolve_owner(
        call=SimpleNamespace(domain_key="get_statistics"),
        descriptor=SimpleNamespace(),
    )
    assert kind == "skill_version"
    assert vid == SKILL_VERSION_A


def test_multi_call_property_budget_invariants() -> None:
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_total_capability_calls": 5,
            "max_parallel_calls": 2,
        }
    )
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)])
    port = BudgetLedgerReservationPort(ledger=ledger)
    guard = BudgetLedgerDispatchGuard(ledger=ledger)

    def one_call(i: int) -> str:
        cid = f"p{i}"
        d = port.reserve_one(
            _reserve_item(cid, arguments_digest=sha256_canonical_json({"i": i}))
        )
        if not d.allowed:
            return "denied"
        try:
            guard.mark_started(
                call_id=cid,
                validated_arguments_digest=sha256_canonical_json({"i": i}),
            )
        except CapabilityDomainError:
            return "start_denied"
        guard.finish(call_id=cid, status="completed")
        return "ok"

    outcomes: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(one_call, i) for i in range(12)]
        for f in as_completed(futs):
            outcomes.append(f.result())

    ok = outcomes.count("ok")
    denied = outcomes.count("denied") + outcomes.count("start_denied")
    assert ok == 5
    assert denied == 7
    snap = ledger.snapshot()
    assert snap.capability_calls_started == 5
    finished = [r for r in snap.reservations if r.state == "finished"]
    assert len(finished) == 5
    assert not any(r.state == "reserved" for r in snap.reservations)


def test_round_guard_caps_none_generation_to_remaining_tokens() -> None:
    """generation.max_output_tokens=None must be capped by remaining completion tokens.

    When remaining is already 0, pure_start_provider_round denies first
    (budget_exhausted_completion_tokens) — the cap path only runs after a
    successful start, so exercise remaining>0 with no generation cap.
    """
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_completion_tokens": 10,
            "max_provider_rounds": 8,
            "max_completion_followup_rounds": 2,
        }
    )
    ledger = BudgetLedger.create(limits=limits)
    ledger.start_provider_round(is_finalization=False)
    ledger.record_token_usage(prompt_tokens=0, completion_tokens=7)
    assert ledger.remaining_completion_tokens() == 3

    guard = BudgetLedgerRoundGuard(ledger=ledger)
    model = _model()
    surface = _empty_surface(_manifest())
    request = ProviderRoundRequest(
        round_index=0,
        messages=(ProviderUserMessage(content="hi"),),
        tool_surface=surface,
        tools_enabled=True,
        finalization_round=False,
        model_ref=model,
        generation=ProviderGenerationOptions(
            max_output_tokens=None,
            tool_choice=ProviderToolChoice(mode="auto"),
        ),
    )
    gen = guard.before_round(request)
    assert gen.max_output_tokens == 3

    # Exhausted tokens: next before_round fails closed at start_provider_round.
    ledger.record_token_usage(prompt_tokens=0, completion_tokens=3)
    assert ledger.remaining_completion_tokens() == 0
    with pytest.raises(ProviderRoundBudgetDeniedError) as ei:
        guard.before_round(request)
    assert ei.value.reason_code == "budget_exhausted_completion_tokens"


def test_budget_restore_if_revision_rejects_concurrent_advance() -> None:
    from app.assistant.policy.budgets import BudgetLedger
    from app.assistant.policy.contracts import normalize_run_budget_limits

    limits = normalize_run_budget_limits(
        operator_limits={"max_total_capability_calls": 8, "max_provider_rounds": 8}
    )
    ledger = BudgetLedger.create(limits=limits)
    snap = ledger.snapshot()
    # Concurrent advance.
    ledger.start_provider_round(is_finalization=False)
    advanced = ledger.snapshot()
    assert advanced.revision == snap.revision + 1
    # Restore expecting pre-advance revision fails.
    assert (
        ledger.restore_if_revision(snap, expected_current_revision=snap.revision) is False
    )
    # Restore expecting post-advance revision succeeds (rewind concurrent apply).
    assert (
        ledger.restore_if_revision(snap, expected_current_revision=advanced.revision)
        is True
    )
    assert ledger.snapshot().revision == snap.revision
    assert ledger.snapshot().provider_rounds_started == 0

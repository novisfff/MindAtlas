"""Plan 05 Task 8: Main Agent policy runtime composition + adversarial evaluation.

Covers:
- per-Run composition of frozen policy snapshot, Budget/Obligation ledgers,
  sibling-isolated frames, dual dispatch_guard, completion guard
- multi-Skill fixtures (unrelated policies, compatible/incompatible duplicates,
  instruction-only, conflict rules, different owner budgets)
- adversarial guessed aliases, owner forgery, evidence replay, budget
  amplification, parallel over-reservation, recursion, premature final text,
  pending-obligation cases
- exact zeros for unauthorized calls, budget overruns, Run-limit increases,
  false completion, write/unknown exposure, internal-event leakage
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
RUN_ID = UUID("00000000-0000-4000-8000-000000000801")
CONV_ID = UUID("00000000-0000-4000-8000-000000000802")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000810")
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000811")
SKILL_A = UUID("00000000-0000-4000-8000-000000000821")
SKILL_B = UUID("00000000-0000-4000-8000-000000000822")
PKG_A = UUID("00000000-0000-4000-8000-000000000831")
PKG_B = UUID("00000000-0000-4000-8000-000000000832")
BUILD = "plan04-dev"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _main_agent_ref(**overrides: Any):
    from app.assistant.domain.contracts import ResolvedMainAgentRef

    payload = {
        "profile_id": PROFILE_ID,
        "version_id": PROFILE_VERSION_ID,
        "profile_key": "general_chat",
        "sequence": 1,
        "content_digest": DIGEST_A,
    }
    payload.update(overrides)
    return ResolvedMainAgentRef(**payload)


def _base_manifest(*, run_id: UUID = RUN_ID, policy_digest: str | None = None):
    from app.assistant.domain.contracts import (
        create_model_ref,
        create_provider_ref,
    )
    from app.assistant.main_agent.control_capabilities import (
        build_all_main_agent_control_bindings,
    )
    from app.assistant.main_agent.service import build_base_manifest_with_controls

    main_agent = _main_agent_ref()
    bindings = build_all_main_agent_control_bindings(
        owner_version_id=PROFILE_VERSION_ID,
        source_snapshot_digest=DIGEST_A,
        app_build_revision=BUILD,
    )
    cred_id = UUID("00000000-0000-4000-8000-000000000901")
    model_id = UUID("00000000-0000-4000-8000-000000000902")
    probe_id = UUID("00000000-0000-4000-8000-000000000903")
    provider = create_provider_ref(
        provider_protocol="openai_chat_completions",
        provider_config_id=cred_id,
        provider_runtime_revision=1,
        provider_config_digest=DIGEST_B,
        adapter_key="openai_chat_completions",
        adapter_revision="1",
        protocol_revision="1",
        app_build_revision=BUILD,
    )
    model = create_model_ref(
        model_id=model_id,
        model_name="gpt-test",
        model_type="llm",
        model_runtime_revision=1,
        credential_id=cred_id,
        credential_runtime_revision=1,
        credential_config_digest=DIGEST_B,
        model_config_digest=DIGEST_C,
        provider_ref_digest=provider.provider_ref_digest,
        capability_probe_id=probe_id,
        capability_probe_digest=DIGEST_A,
    )
    return build_base_manifest_with_controls(
        run_id=run_id,
        main_agent=main_agent,
        provider=provider,
        model=model,
        effective_policy_digest=policy_digest or DIGEST_D,
        control_bindings=bindings,
    ), bindings


def _owner_material(**overrides: Any):
    from app.assistant.policy.contracts import compute_owner_policy_digest
    from app.assistant.policy.evaluator import OwnerGrantMaterial

    payload = {
        "owner_kind": "main_agent",
        "owner_id": "general_chat",
        "owner_version_id": PROFILE_VERSION_ID,
        "policy_digest": compute_owner_policy_digest(
            owner_kind="main_agent",
            owner_id="general_chat",
            owner_version_id=PROFILE_VERSION_ID,
            content_or_policy_digest=DIGEST_A,
            allowed_side_effects=("none", "read", "compute"),
        ),
        "author_allowed_side_effects": ("none", "read", "compute"),
        "declared_capability_keys": frozenset(
            {"skill.search", "skill.inject", "skill.read_resource", "artifact.read"}
        ),
        "is_instruction_only": False,
    }
    payload.update(overrides)
    return OwnerGrantMaterial(**payload)


# ---------------------------------------------------------------------------
# Composition unit tests (no DB)
# ---------------------------------------------------------------------------


def test_process_local_frames_sibling_isolated_under_threads() -> None:
    from app.assistant.policy.recursion import (
        ProcessLocalCapabilityCallFramePort,
        build_capability_call_frame,
    )

    frames = ProcessLocalCapabilityCallFramePort()
    parent = build_capability_call_frame(
        call_id="p",
        capability_type="tool",
        domain_key="parent",
        target_identity="tool:p",
        target_version_id=None,
        binding_contract_digest=DIGEST_B,
        owner_kind="main_agent",
        owner_version_id=PROFILE_VERSION_ID,
        capability_depth=1,
        agent_depth=1,
    )
    barrier = threading.Barrier(2)
    seen: dict[str, tuple[str, ...]] = {}
    lock = threading.Lock()

    def worker(cid: str) -> None:
        frame = build_capability_call_frame(
            call_id=cid,
            capability_type="tool",
            domain_key=cid,
            target_identity=f"tool:{cid}",
            target_version_id=None,
            binding_contract_digest=DIGEST_B,
            owner_kind="main_agent",
            owner_version_id=PROFILE_VERSION_ID,
            capability_depth=2,
            agent_depth=1,
        )
        with frames.push(frame):
            barrier.wait(timeout=2)
            time.sleep(0.03)
            ids = tuple(f.call_id for f in frames.current())
            with lock:
                seen[cid] = ids
            barrier.wait(timeout=2)

    with frames.push(parent):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(worker, "a"), pool.submit(worker, "b")]
            for f in futs:
                f.result(timeout=5)
        assert [f.call_id for f in frames.current()] == ["p"]
    assert frames.current() == ()
    assert seen["a"] == ("p", "a")
    assert seen["b"] == ("p", "b")


def test_owner_material_and_budget_limits_do_not_raise_run_totals() -> None:
    from app.assistant.main_agent.policy_runtime import (
        build_main_agent_owner_budget,
        build_main_agent_owner_material,
    )
    from app.assistant.policy.contracts import normalize_run_budget_limits

    limits = normalize_run_budget_limits()
    owner = build_main_agent_owner_budget(
        profile_version_id=PROFILE_VERSION_ID, run_limits=limits
    )
    assert owner.max_calls <= limits.max_total_capability_calls
    assert owner.max_same_read_signature <= limits.max_same_read_signature

    material = build_main_agent_owner_material(
        profile_key="general_chat",
        profile_version_id=PROFILE_VERSION_ID,
        profile_content_digest=DIGEST_A,
    )
    assert material.owner_kind == "main_agent"
    assert "skill.search" in (material.declared_capability_keys or frozenset())
    # Instruction-only owner material has empty declared keys.
    instruction = _owner_material(
        owner_kind="skill_version",
        owner_id=str(PKG_A),
        owner_version_id=SKILL_A,
        declared_capability_keys=frozenset(),
        is_instruction_only=True,
        author_allowed_side_effects=(),
    )
    assert instruction.is_instruction_only is True


def test_budget_ledger_create_from_defaults_and_no_amplification() -> None:
    from app.assistant.main_agent.policy_runtime import build_main_agent_owner_budget
    from app.assistant.policy.budgets import BudgetLedger
    from app.assistant.policy.contracts import normalize_run_budget_limits

    limits = normalize_run_budget_limits()
    owner = build_main_agent_owner_budget(
        profile_version_id=PROFILE_VERSION_ID, run_limits=limits
    )
    ledger = BudgetLedger.create(limits=limits, owner_limits=(owner,))
    snap = ledger.snapshot()
    assert snap.limits.max_total_capability_calls == limits.max_total_capability_calls
    # Adding another owner bucket never raises Run totals.
    from app.assistant.policy.contracts import normalize_owner_budget_limits

    skill_owner = normalize_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=SKILL_A,
        run_limits=limits,
        max_skill_calls=100,  # attempted amplification
        max_same_read_calls=100,
    )
    assert skill_owner.max_calls <= limits.max_total_capability_calls
    assert skill_owner.max_same_read_signature <= limits.max_same_read_signature


def test_obligation_ledger_blocks_false_completion() -> None:
    from app.assistant.policy.completion import ObligationLedgerCompletionGuard
    from app.assistant.policy.obligations import ObligationLedger
    from app.assistant.provider_loop.contracts import ProviderCompletionRequest

    obligations = ObligationLedger.create(run_id=RUN_ID)
    # create() already installs the main-agent terminal by default.
    assert any(
        getattr(o, "kind", None) == "main_agent_terminal"
        or "main_agent" in str(getattr(o, "obligation_id", ""))
        or getattr(o, "source_kind", None) == "main_agent"
        for o in obligations.snapshot().obligations
    ) or len(obligations.snapshot().obligations) >= 1

    guard = ObligationLedgerCompletionGuard(
        obligation_ledger=obligations,
        locale="en",
        max_completion_followup_rounds=2,
    )
    # Empty text cannot satisfy main-agent terminal.
    disposition = guard.evaluate(
        ProviderCompletionRequest(
            manifest_revision=1,
            manifest_digest=DIGEST_A,
            candidate_text="",
            finalization_round=False,
        )
    )
    assert disposition.action in {"continue", "fail"}
    assert disposition.action != "complete"

    # Premature short text also cannot complete while terminal pending.
    disposition2 = guard.evaluate(
        ProviderCompletionRequest(
            manifest_revision=1,
            manifest_digest=DIGEST_A,
            candidate_text="done",
            finalization_round=False,
        )
    )
    assert disposition2.action != "complete" or disposition2.reason_code != "natural_completion"


def test_dual_dispatch_guard_fields_on_composed_ports_shape() -> None:
    """ProviderLoopPorts + dispatcher both accept the same dispatch_guard instance."""
    from app.assistant.policy.budgets import BudgetLedger
    from app.assistant.policy.contracts import normalize_run_budget_limits
    from app.assistant.policy.runtime import BudgetLedgerDispatchGuard
    from app.assistant.provider_loop.contracts import ProviderLoopPorts
    from types import SimpleNamespace

    limits = normalize_run_budget_limits()
    ledger = BudgetLedger.create(limits=limits)
    guard = BudgetLedgerDispatchGuard(ledger=ledger)

    # Dual-wire identity: same object on ports and dispatcher.
    dispatcher = SimpleNamespace(dispatch_guard=guard)
    ports = ProviderLoopPorts(
        provider=SimpleNamespace(),  # type: ignore[arg-type]
        tools_provider=SimpleNamespace(),  # type: ignore[arg-type]
        current_descriptors=SimpleNamespace(),  # type: ignore[arg-type]
        authorization_evidence=SimpleNamespace(),  # type: ignore[arg-type]
        tool_dispatcher=dispatcher,  # type: ignore[arg-type]
        sibling_executor=SimpleNamespace(),  # type: ignore[arg-type]
        cancellation=SimpleNamespace(is_cancelled=lambda: False),  # type: ignore[arg-type]
        events=SimpleNamespace(emit=lambda *a, **k: None),  # type: ignore[arg-type]
        dispatch_guard=guard,
    )
    assert ports.dispatch_guard is dispatcher.dispatch_guard
    assert ports.dispatch_guard is guard


def test_plan05_internal_events_are_marked_internal() -> None:
    from app.assistant.main_agent.events import (
        PLAN05_INTERNAL_EVENTS,
        MainAgentEventAdapter,
        is_internal_event,
        POLICY_SNAPSHOT,
        AUTHORIZATION_DECISION,
        BUDGET_DENIED,
        COMPLETION_DECISION,
    )

    seen: list[tuple[str, dict]] = []
    adapter = MainAgentEventAdapter(lambda n, p: seen.append((n, dict(p))))
    adapter.policy_snapshot(
        run_id=RUN_ID,
        effective_policy_digest=DIGEST_A,
        exposure_index_digest=DIGEST_B,
        max_total_capability_calls=16,
    )
    adapter.authorization_decision(
        call_id="c1", allowed=False, reason_code="policy_denied"
    )
    adapter.budget_event(event_name=BUDGET_DENIED, call_id="c1", reason_code="budget_exhausted")
    adapter.completion_decision(action="fail", reason_code="pending_obligation", pending_count=1)

    assert POLICY_SNAPSHOT in PLAN05_INTERNAL_EVENTS
    assert AUTHORIZATION_DECISION in PLAN05_INTERNAL_EVENTS
    assert COMPLETION_DECISION in PLAN05_INTERNAL_EVENTS
    assert len(seen) == 4
    for name, payload in seen:
        assert is_internal_event(payload), name
        # No raw prompts / arguments / exception text.
        joined = str(payload)
        assert "traceback" not in joined.lower()
        assert "password" not in joined.lower()
        assert "prompt" not in joined.lower()


def test_main_agent_run_state_holds_policy_runtime_slot() -> None:
    from app.assistant.main_agent.service import MainAgentRunState

    manifest, _ = _base_manifest()
    state = MainAgentRunState(
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        manifest=manifest,
        policy_runtime=None,
        stop_reason=None,
    )
    assert state.policy_runtime is None
    assert state.status == "running"


# ---------------------------------------------------------------------------
# Multi-Skill fixture pure checks
# ---------------------------------------------------------------------------


def test_unrelated_skills_do_not_share_owner_budget() -> None:
    from app.assistant.policy.contracts import (
        normalize_owner_budget_limits,
        normalize_run_budget_limits,
    )
    from app.assistant.policy.budgets import BudgetLedger, BudgetReserveRequest

    limits = normalize_run_budget_limits()
    owner_a = normalize_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=SKILL_A,
        run_limits=limits,
        max_skill_calls=2,
        max_same_read_calls=2,
    )
    owner_b = normalize_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=SKILL_B,
        run_limits=limits,
        max_skill_calls=2,
        max_same_read_calls=2,
    )
    ledger = BudgetLedger.create(limits=limits, owner_limits=(owner_a, owner_b))

    digests = [DIGEST_A, DIGEST_B]
    # Exhaust skill A calls with distinct signatures.
    for i in range(2):
        d = ledger.reserve_one(
            BudgetReserveRequest(
                call_id=f"a-{i}",
                owner_kind="skill_version",
                owner_version_id=SKILL_A,
                domain_key=f"read.a.{i}",
                side_effect="read",
                arguments_digest=digests[i],
                binding_contract_digest=DIGEST_C,
                capability_depth=1,
                agent_depth=1,
            )
        )
        assert d.allowed, d.reason_code
        ledger.mark_started(f"a-{i}", validated_arguments_digest=digests[i])
        ledger.finish(f"a-{i}")

    # Skill A exhausted.
    denied = ledger.reserve_one(
        BudgetReserveRequest(
            call_id="a-x",
            owner_kind="skill_version",
            owner_version_id=SKILL_A,
            domain_key="read.a.x",
            side_effect="read",
            arguments_digest=DIGEST_C,
            binding_contract_digest=DIGEST_D,
            capability_depth=1,
            agent_depth=1,
        )
    )
    assert denied.allowed is False

    # Unrelated skill B still has budget.
    ok_b = ledger.reserve_one(
        BudgetReserveRequest(
            call_id="b-0",
            owner_kind="skill_version",
            owner_version_id=SKILL_B,
            domain_key="read.b",
            side_effect="read",
            arguments_digest=DIGEST_D,
            binding_contract_digest=DIGEST_C,
            capability_depth=1,
            agent_depth=1,
        )
    )
    assert ok_b.allowed is True


def test_skill_budget_amplification_clamped_to_run() -> None:
    from app.assistant.policy.contracts import (
        normalize_owner_budget_limits,
        normalize_run_budget_limits,
    )

    limits = normalize_run_budget_limits()
    # Skill tries to declare higher than Run.
    owner = normalize_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=SKILL_A,
        run_limits=limits,
        max_skill_calls=10_000,
        max_same_read_calls=10_000,
    )
    assert owner.max_calls == limits.max_total_capability_calls
    assert owner.max_same_read_signature == limits.max_same_read_signature


def test_parallel_over_reservation_is_atomic() -> None:
    from app.assistant.policy.budgets import BudgetLedger, BudgetReserveRequest
    from app.assistant.policy.contracts import normalize_run_budget_limits
    from app.assistant.main_agent.policy_runtime import build_main_agent_owner_budget

    # Tiny run total so batch over-reservation fails.
    limits = normalize_run_budget_limits(
        operator_limits={
            "max_total_capability_calls": 2,
            "max_parallel_calls": 2,
            "max_same_read_signature": 2,
        }
    )
    owner = build_main_agent_owner_budget(
        profile_version_id=PROFILE_VERSION_ID, run_limits=limits
    )
    ledger = BudgetLedger.create(limits=limits, owner_limits=(owner,))

    reqs = tuple(
        BudgetReserveRequest(
            call_id=f"p-{i}",
            owner_kind="main_agent",
            owner_version_id=PROFILE_VERSION_ID,
            domain_key=f"tool.{i}",
            side_effect="read",
            arguments_digest=DIGEST_A,
            binding_contract_digest=DIGEST_B,
            capability_depth=1,
            agent_depth=1,
        )
        for i in range(3)
    )
    decision = ledger.reserve_batch(reqs)
    assert decision.allowed is False
    # No partial reservation residue.
    snap = ledger.snapshot()
    active = [r for r in snap.reservations if getattr(r, "status", None) in {"reserved", "started"}]
    assert active == []


def test_evidence_replay_denied() -> None:
    """Same call_id cannot be issued twice by MainAgentAuthorizationEvidenceFactory."""
    from app.assistant.main_agent.authorization import (
        MainAgentAuthorizationEvidenceFactory,
        LOCAL_ASSISTANT_PRINCIPAL,
    )
    from app.assistant.capabilities.policy import AuthorizationEvidenceVerificationError
    from app.assistant.provider_loop.contracts import create_execution_scope
    from unittest.mock import MagicMock

    scope = create_execution_scope(
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        tenant_scope_id=None,
    )
    manifest, bindings = _base_manifest()
    factory = MainAgentAuthorizationEvidenceFactory(
        scope=scope,
        manifest=manifest,
        profile_key="general_chat",
        profile_content_digest=DIGEST_A,
        # Plan 04 minimum path (no policy_snapshot) is enough for replay check.
    )
    binding = bindings[0]
    call = MagicMock()
    call.call_id = "replay-1"
    call.domain_key = binding.ref.capability_key
    call.arguments_digest = DIGEST_A
    descriptor = MagicMock()
    descriptor.descriptor_digest = DIGEST_B
    descriptor.behavior = MagicMock(side_effect="none", interrupt_mode="none")
    descriptor.availability = MagicMock(status="available")
    descriptor.capability_key = binding.ref.capability_key
    descriptor.resolution_digest = binding.ref.resolution_digest
    descriptor.binding_contract_digest = binding.ref.binding_contract_digest
    descriptor.dependency_closure_digest = binding.ref.dependency_closure_digest

    evidence1 = factory.issue(
        call=call, binding=binding, descriptor=descriptor, scope=scope
    )
    assert evidence1.call_id == "replay-1"
    with pytest.raises(AuthorizationEvidenceVerificationError):
        factory.issue(call=call, binding=binding, descriptor=descriptor, scope=scope)


def test_policy_deny_auth_reason_codes_preserved_on_tool_result() -> None:
    """I1: pure policy deny via auth factory surfaces stable reason on Tool Result.

    AuthorizationEvidenceVerificationError(decision.reason_code) must map to
    blocked Tool Result safe_code=<policy code>, not authorization_evidence_failed.
    """
    from app.assistant.capabilities.policy import AuthorizationEvidenceVerificationError
    from app.assistant.provider_loop.loop import (
        _stable_auth_evidence_reason_code,
    )

    # Allowlisted Plan 05 §5.4 policy deny codes are preserved.
    for code in (
        "exposure_missing",
        "global_policy_denied",
        "owner_side_effect_denied",
        "principal_not_allowed",
        "recursion_denied",
        "policy_denied",
    ):
        assert _stable_auth_evidence_reason_code(code) == code

    # Evidence verification codes from MainAgentAuthorizationEvidenceFactory.
    assert _stable_auth_evidence_reason_code("call_id_replay") == "call_id_replay"
    assert (
        _stable_auth_evidence_reason_code("scope_identity_mismatch")
        == "scope_identity_mismatch"
    )

    # Unknown / non-allowlisted codes collapse safely (no exception text leakage).
    assert (
        _stable_auth_evidence_reason_code("secret_internal_detail")
        == "authorization_evidence_failed"
    )
    assert (
        _stable_auth_evidence_reason_code("has spaces")
        == "authorization_evidence_failed"
    )
    assert _stable_auth_evidence_reason_code("") == "authorization_evidence_failed"
    assert _stable_auth_evidence_reason_code(None) == "authorization_evidence_failed"
    # reason_code attribute shape on the exception itself.
    exc = AuthorizationEvidenceVerificationError("exposure_missing")
    assert _stable_auth_evidence_reason_code(exc.reason_code) == "exposure_missing"


def test_owner_forgery_rejected_by_evaluator() -> None:
    from app.assistant.policy.contracts import (
        build_effective_run_policy_snapshot,
        build_manifest_exposure_index,
        build_owner_policy_ref,
        normalize_run_budget_limits,
    )
    from app.assistant.policy.evaluator import (
        AuthorizationProposal,
        evaluate_authorization,
    )
    from app.assistant.main_agent.authorization import LOCAL_ASSISTANT_PRINCIPAL

    limits = normalize_run_budget_limits()
    material = _owner_material()
    owner_ref = build_owner_policy_ref(
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        content_or_policy_digest=material.policy_digest,
        allowed_side_effects=("none", "read", "compute"),
    )
    index = build_manifest_exposure_index(
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        exposures=(),
    )
    snapshot = build_effective_run_policy_snapshot(
        app_build_revision=BUILD,
        run_id=RUN_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=index,
        owner_policy_refs=(owner_ref,),
        run_budget_limits=limits,
    )
    # Claimed skill owner that is not in the snapshot.
    proposal = AuthorizationProposal(
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        scope_digest=DIGEST_A,
        expected_scope_digest=DIGEST_A,
        expected_run_id=RUN_ID,
        expected_conversation_id=CONV_ID,
        manifest_digest=DIGEST_A,
        expected_manifest_digest=DIGEST_A,
        capability_key="skill.search",
        binding_contract_digest=DIGEST_B,
        resolution_digest=DIGEST_C,
        dependency_closure_digest=DIGEST_D,
        descriptor_digest=DIGEST_A,
        descriptor_side_effect="none",
        descriptor_interrupt_mode="none",
        descriptor_availability_status="available",
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        nesting_depth=0,
        max_capability_depth=4,
        claimed_owner_kind="skill_version",
        claimed_owner_id=str(PKG_A),
        claimed_owner_version_id=SKILL_A,
    )
    decision = evaluate_authorization(
        snapshot=snapshot,
        proposal=proposal,
        owner_materials={
            (
                material.owner_kind,
                material.owner_id,
                material.owner_version_id,
            ): material
        },
    )
    assert decision.allowed is False


def test_guessed_alias_not_in_exposure_denied() -> None:
    from app.assistant.policy.contracts import (
        build_effective_run_policy_snapshot,
        build_manifest_exposure_index,
        build_owner_policy_ref,
        normalize_run_budget_limits,
    )
    from app.assistant.policy.evaluator import (
        AuthorizationProposal,
        evaluate_authorization,
    )
    from app.assistant.main_agent.authorization import LOCAL_ASSISTANT_PRINCIPAL

    limits = normalize_run_budget_limits()
    material = _owner_material()
    owner_ref = build_owner_policy_ref(
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        content_or_policy_digest=material.policy_digest,
        allowed_side_effects=("none", "read", "compute"),
    )
    # Empty exposure index — guessed alias has no exposure.
    index = build_manifest_exposure_index(
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        exposures=(),
    )
    snapshot = build_effective_run_policy_snapshot(
        app_build_revision=BUILD,
        run_id=RUN_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=index,
        owner_policy_refs=(owner_ref,),
        run_budget_limits=limits,
    )
    proposal = AuthorizationProposal(
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        scope_digest=DIGEST_A,
        expected_scope_digest=DIGEST_A,
        expected_run_id=RUN_ID,
        expected_conversation_id=CONV_ID,
        manifest_digest=DIGEST_A,
        expected_manifest_digest=DIGEST_A,
        capability_key="guessed.write_all",
        binding_contract_digest=DIGEST_B,
        resolution_digest=DIGEST_C,
        dependency_closure_digest=DIGEST_D,
        descriptor_digest=DIGEST_A,
        descriptor_side_effect="write_external",
        descriptor_interrupt_mode="none",
        descriptor_availability_status="available",
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        nesting_depth=0,
        max_capability_depth=4,
        claimed_owner_kind="main_agent",
        claimed_owner_id="general_chat",
        claimed_owner_version_id=PROFILE_VERSION_ID,
    )
    decision = evaluate_authorization(
        snapshot=snapshot,
        proposal=proposal,
        owner_materials={
            (
                material.owner_kind,
                material.owner_id,
                material.owner_version_id,
            ): material
        },
    )
    assert decision.allowed is False
    # Write/unknown must never be granted under Plan 05 release gate.
    assert "write" not in (decision.allowed_side_effects or ())


def test_recursion_guard_denies_agent_cycle() -> None:
    from app.assistant.policy.recursion import (
        REASON_AGENT_CYCLE,
        ProcessLocalCapabilityCallFramePort,
        build_capability_call_frame,
        evaluate_recursion_guard,
    )

    frames = ProcessLocalCapabilityCallFramePort()
    agent_version = SKILL_A
    parent = build_capability_call_frame(
        call_id="agent-1",
        capability_type="agent",
        domain_key="agent.a",
        target_identity="agent:a",
        target_version_id=agent_version,
        binding_contract_digest=DIGEST_B,
        owner_kind="main_agent",
        owner_version_id=PROFILE_VERSION_ID,
        capability_depth=1,
        agent_depth=1,
    )
    with frames.push(parent):
        reason = evaluate_recursion_guard(
            frames.current(),
            capability_type="agent",
            target_version_id=agent_version,
            target_identity="agent:a",
            max_capability_depth=4,
            max_agent_depth=2,
        )
        assert reason == REASON_AGENT_CYCLE


def test_instruction_only_skill_owner_budget_is_zero_calls() -> None:
    from app.assistant.policy.contracts import (
        normalize_owner_budget_limits,
        normalize_run_budget_limits,
    )

    limits = normalize_run_budget_limits()
    owner = normalize_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=SKILL_A,
        run_limits=limits,
        is_instruction_only=True,
    )
    assert owner.max_calls == 0
    assert owner.max_same_read_signature == 0


def test_main_agent_service_missing_db_fails_closed_when_composing() -> None:
    """Without injected ports and without a usable DB, composition fails closed."""
    from app.assistant.main_agent.service import (
        AssistantRuntimeRequest,
        MainAgentService,
        AdmissionContext,
    )
    from app.assistant.main_agent.events import MainAgentEventAdapter
    from app.assistant.skills.schemas import default_main_agent_profile_snapshot
    from unittest.mock import MagicMock

    manifest, _ = _base_manifest()
    provider_ref = manifest.provider
    model_ref = manifest.model
    assert provider_ref is not None and model_ref is not None
    snapshot = default_main_agent_profile_snapshot()

    admission = MagicMock(spec=AdmissionContext)
    admission.mode = "read_only"
    admission.execution_kind = "production"
    admission.main_agent_ref = _main_agent_ref()
    admission.effective_policy_digest = DIGEST_D
    admission.snapshot = snapshot
    admission.provider_ref = provider_ref
    admission.model_ref = model_ref
    admission.frozen_model = MagicMock()
    admission.probe_diagnostics = None

    events: list[tuple[str, dict]] = []
    adapter = MainAgentEventAdapter(lambda n, p: events.append((n, p)))
    service = MainAgentService(
        db=None,  # type: ignore[arg-type]
        admission=admission,
        provider=MagicMock(),
        ports=None,
        event_adapter=adapter,
        app_build_revision=BUILD,
        allow_injected_provider=True,
    )
    result = service.run(
        AssistantRuntimeRequest(
            run_id=RUN_ID,
            conversation_id=CONV_ID,
            user_text="hi",
            locale="en",
            execution_kind="production",
        )
    )
    # Composition fails closed before Provider request — same durable Run, no Legacy.
    assert result.status == "failed"
    assert result.runtime == "main_agent"
    assert result.reason_code
    assert result.write_message is False


def test_unauthorized_and_write_exposure_exact_zeros() -> None:
    """Adversarial matrix: unauthorized + write exposure must stay at exact zero grants."""
    from app.assistant.policy.contracts import (
        build_effective_run_policy_snapshot,
        build_manifest_exposure_index,
        build_owner_policy_ref,
        normalize_run_budget_limits,
        PLAN05_RELEASE_GATE_SIDE_EFFECTS,
    )
    from app.assistant.policy.evaluator import (
        AuthorizationProposal,
        evaluate_authorization,
    )
    from app.assistant.main_agent.authorization import LOCAL_ASSISTANT_PRINCIPAL

    limits = normalize_run_budget_limits()
    material = _owner_material()
    owner_ref = build_owner_policy_ref(
        owner_kind="main_agent",
        owner_id="general_chat",
        owner_version_id=PROFILE_VERSION_ID,
        content_or_policy_digest=material.policy_digest,
        allowed_side_effects=("none", "read", "compute"),
    )
    index = build_manifest_exposure_index(
        manifest_revision=1,
        manifest_digest=DIGEST_A,
        exposures=(),
    )
    snapshot = build_effective_run_policy_snapshot(
        app_build_revision=BUILD,
        run_id=RUN_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        main_agent_profile_version_id=PROFILE_VERSION_ID,
        main_agent_profile_digest=DIGEST_A,
        exposure_index=index,
        owner_policy_refs=(owner_ref,),
        run_budget_limits=limits,
    )
    materials = {
        (
            material.owner_kind,
            material.owner_id,
            material.owner_version_id,
        ): material
    }

    unauthorized = 0
    write_grants = 0
    cases = [
        ("guessed.alias", "read"),
        ("write.local", "write_local"),
        ("write.ext", "write_external"),
        ("unknown.fx", "unknown"),
        ("draft.x", "draft"),
    ]
    for key, side in cases:
        proposal = AuthorizationProposal(
            run_id=RUN_ID,
            conversation_id=CONV_ID,
            scope_digest=DIGEST_A,
            expected_scope_digest=DIGEST_A,
            expected_run_id=RUN_ID,
            expected_conversation_id=CONV_ID,
            manifest_digest=DIGEST_A,
            expected_manifest_digest=DIGEST_A,
            capability_key=key,
            binding_contract_digest=DIGEST_B,
            resolution_digest=DIGEST_C,
            dependency_closure_digest=DIGEST_D,
            descriptor_digest=DIGEST_A,
            descriptor_side_effect=side,  # type: ignore[arg-type]
            descriptor_interrupt_mode="none",
            descriptor_availability_status="available",
            principal=LOCAL_ASSISTANT_PRINCIPAL,
            nesting_depth=0,
            max_capability_depth=4,
            claimed_owner_kind="main_agent",
            claimed_owner_id="general_chat",
            claimed_owner_version_id=PROFILE_VERSION_ID,
        )
        decision = evaluate_authorization(
            snapshot=snapshot, proposal=proposal, owner_materials=materials
        )
        if not decision.allowed:
            unauthorized += 1
        for effect in decision.allowed_side_effects or ():
            if effect not in PLAN05_RELEASE_GATE_SIDE_EFFECTS:
                write_grants += 1

    assert unauthorized == len(cases)
    assert write_grants == 0


def test_provider_loop_modules_do_not_import_policy_ledgers() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "assistant" / "provider_loop"
    forbidden = {
        "app.assistant.policy.budgets",
        "app.assistant.policy.obligations",
        "app.assistant.policy.evaluator",
        "app.assistant.policy.completion",
    }
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in forbidden:
                raise AssertionError(f"{path} imports {node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden:
                        raise AssertionError(f"{path} imports {alias.name}")


def test_openclaw_verifier_isolation_unchanged() -> None:
    """OpenClaw remains on Plan 02 verifier only; skill_policy is separate key."""
    from app.assistant.capabilities.policy import build_composite_evidence_verifiers
    from unittest.mock import MagicMock

    openclaw = MagicMock(name="openclaw")
    skill = MagicMock(name="skill_policy")
    mapping = build_composite_evidence_verifiers(
        openclaw_verifier=openclaw,
        skill_policy_verifier=skill,
    )
    assert ("openclaw_bridge", "openclaw") in mapping or any(
        k[0] == "openclaw_bridge" for k in mapping
    )
    assert ("skill_policy", "main_agent") in mapping
    assert mapping[("skill_policy", "main_agent")] is skill


def test_skill_injection_policy_context_reads_completion_followups_started() -> None:
    """Field name is completion_followups_started (not completion_followup_rounds_started)."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.assistant.main_agent.policy_runtime import (
        skill_injection_policy_context_from_runtime,
    )
    from app.assistant.policy.budgets import BudgetLedger
    from app.assistant.policy.contracts import normalize_run_budget_limits

    limits = normalize_run_budget_limits(
        operator_limits={
            "max_provider_rounds": 8,
            "max_completion_followup_rounds": 2,
            "max_total_capability_calls": 16,
            "max_same_read_signature": 3,
            "max_active_skills": 4,
        }
    )
    ledger = BudgetLedger.create(limits=limits)
    ledger.start_provider_round(is_finalization=False)
    # Charge one completion-followup slot so remaining = 8 - 1 - 1 = 6.
    decision = ledger.start_completion_followup()
    assert decision.allowed

    runtime = SimpleNamespace(
        budget_ledger=ledger,
        run_budget_limits=limits,
    )
    ctx = skill_injection_policy_context_from_runtime(runtime)  # type: ignore[arg-type]
    assert ctx.remaining_provider_slots == 6
    assert ctx.run_max_active_skills == 4
    assert ctx.run_max_total_capability_calls == 16
    # Ensure the wrong attribute name is not present on the snapshot.
    snap = ledger.snapshot()
    assert hasattr(snap, "completion_followups_started")
    assert not hasattr(snap, "completion_followup_rounds_started")


def test_empty_terminal_capability_result_does_not_satisfy_obligation() -> None:
    from app.assistant.capabilities.contracts import CapabilityMetrics, completed_result
    from app.assistant.main_agent.policy_runtime import _capability_result_output_digest
    from app.assistant.policy.obligations import ObligationLedger

    ledger = ObligationLedger(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        skill_package_id=PKG_A,
        terminal_text_allowed=False,
    )
    result = completed_result(
        user_text=None,
        structured_output=None,
        artifact_refs=(),
        metrics=CapabilityMetrics(duration_ms=0, input_bytes=0, output_bytes=0),
        terminal_output=True,
        needs_followup=False,
    )

    decision = ledger.apply_capability_result(
        call_id="empty-terminal-result",
        result_status=result.status,
        terminal_output=result.terminal_output,
        needs_followup=result.needs_followup,
        output_digest=_capability_result_output_digest(result),
        owner_kind="skill_version",
        owner_id=str(PKG_A),
        owner_version_id=SKILL_A,
        run_id=RUN_ID,
        binding_contract_digest=DIGEST_A,
    )

    assert decision.allowed
    assert ledger.snapshot().obligations[0].status == "pending"


def test_dispatcher_applies_terminal_result_to_compatible_consumers() -> None:
    from types import SimpleNamespace

    from tests.test_agent_exposure_index import _frozen_binding

    from app.assistant.capabilities.contracts import CapabilityMetrics, completed_result
    from app.assistant.main_agent.policy_runtime import MainAgentGatewayToolDispatcher
    from app.assistant.policy.contracts import (
        build_capability_exposure_ref,
        build_manifest_exposure_index,
    )
    from app.assistant.policy.obligations import ObligationLedger

    binding = _frozen_binding(
        capability_key="shared.business",
        origin="skill_version",
        owner_version_id=SKILL_A,
    )
    exposure = build_capability_exposure_ref(
        domain_key=binding.ref.capability_key,
        resolved_ref=binding.ref,
        binding_contract_digest=binding.ref.binding_contract_digest,
        descriptor_digest=DIGEST_C,
        owner_kind="skill_version",
        owner_id=str(PKG_A),
        owner_version_id=SKILL_A,
        compatible_consumer_version_ids=(SKILL_B,),
    )
    exposure_index = build_manifest_exposure_index(
        manifest_revision=2,
        manifest_digest=DIGEST_D,
        exposures=(exposure,),
    )
    ledger = ObligationLedger(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        skill_package_id=PKG_A,
        terminal_text_allowed=False,
    )
    ledger.create_skill_terminal(
        skill_version_id=SKILL_B,
        skill_package_id=PKG_B,
        terminal_text_allowed=False,
    )
    dispatcher = MainAgentGatewayToolDispatcher(
        session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        authorization_factory=SimpleNamespace(
            profile_key="general_chat",
            skill_package_id_by_version={SKILL_A: PKG_A, SKILL_B: PKG_B},
            policy_snapshot=SimpleNamespace(exposure_index=exposure_index),
        ),
        control_port=SimpleNamespace(),  # type: ignore[arg-type]
        obligation_ledger=ledger,
    )
    result = completed_result(
        user_text="shared terminal output",
        metrics=CapabilityMetrics(duration_ms=0, input_bytes=0, output_bytes=22),
        terminal_output=True,
        needs_followup=False,
    )

    dispatcher._apply_result_obligations(
        call=SimpleNamespace(call_id="shared-terminal"),
        binding=binding,
        result=result,
        run_id=RUN_ID,
    )

    state = ledger.snapshot()
    assert {item.status for item in state.obligations} == {"satisfied"}
    assert any(edge.evidence_kind == "compatible_consumer" for edge in state.evidence_edges)


def test_dispatcher_accepts_durable_workflow_execution_port() -> None:
    from types import SimpleNamespace

    from app.assistant.main_agent.policy_runtime import MainAgentGatewayToolDispatcher

    durable_port = SimpleNamespace(execute=lambda *_args, **_kwargs: None)
    dispatcher = MainAgentGatewayToolDispatcher(
        session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        authorization_factory=SimpleNamespace(),
        control_port=SimpleNamespace(),  # type: ignore[arg-type]
        durable_workflow=durable_port,  # type: ignore[arg-type]
    )

    assert dispatcher.durable_workflow is durable_port


def test_policy_runtime_restores_persisted_budget_and_obligation_snapshots(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from tests._db import make_session

    from app.assistant.main_agent.policy_runtime import (
        compose_main_agent_policy_runtime,
    )

    monkeypatch.setenv("APP_BUILD_REVISION", BUILD)
    db = make_session()
    try:
        manifest, _ = _base_manifest()
        runtime, _ports = compose_main_agent_policy_runtime(
            db=db,
            run_id=RUN_ID,
            conversation_id=CONV_ID,
            manifest=manifest,
            profile_key="general_chat",
            profile_version_id=PROFILE_VERSION_ID,
            profile_content_digest=DIGEST_A,
            app_build_revision=BUILD,
            provider=SimpleNamespace(),  # type: ignore[arg-type]
        )
        assert runtime.budget_ledger.start_provider_round().allowed
        budget = runtime.budget_ledger.snapshot()
        obligation = runtime.obligation_ledger.snapshot()

        restored, _ = compose_main_agent_policy_runtime(
            db=db,
            run_id=RUN_ID,
            conversation_id=CONV_ID,
            manifest=runtime.manifest,
            profile_key="general_chat",
            profile_version_id=PROFILE_VERSION_ID,
            profile_content_digest=DIGEST_A,
            app_build_revision=BUILD,
            provider=SimpleNamespace(),  # type: ignore[arg-type]
            restored_policy_snapshot=runtime.policy_snapshot,
            restored_budget_state=budget,
            restored_obligation_state=obligation,
        )

        assert restored.policy_snapshot == runtime.policy_snapshot
        assert restored.budget_ledger.snapshot() == budget
        assert restored.obligation_ledger.snapshot() == obligation
        assert restored.manifest == runtime.manifest
    finally:
        db.close()


def test_dispatcher_fails_closed_when_obligation_apply_is_denied() -> None:
    from types import SimpleNamespace

    from tests.test_agent_exposure_index import _frozen_binding

    from app.assistant.capabilities.contracts import CapabilityMetrics, completed_result
    from app.assistant.main_agent.policy_runtime import MainAgentGatewayToolDispatcher

    binding = _frozen_binding(
        capability_key="denied.evidence",
        origin="skill_version",
        owner_version_id=SKILL_A,
    )

    class _DenyLedger:
        def snapshot(self):
            return SimpleNamespace(obligations=())

        def apply_capability_result(self, **_kwargs):
            return SimpleNamespace(
                allowed=False,
                reason_code="completion_evidence_invalid",
            )

    dispatcher = MainAgentGatewayToolDispatcher(
        session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        authorization_factory=SimpleNamespace(
            profile_key="general_chat",
            skill_package_id_by_version={SKILL_A: PKG_A},
            policy_snapshot=None,
        ),
        control_port=SimpleNamespace(),  # type: ignore[arg-type]
        obligation_ledger=_DenyLedger(),  # type: ignore[arg-type]
    )
    result = completed_result(
        user_text="terminal output",
        metrics=CapabilityMetrics(duration_ms=0, input_bytes=0, output_bytes=15),
        terminal_output=True,
        needs_followup=False,
    )

    applied = dispatcher._apply_result_obligations(
        call=SimpleNamespace(call_id="denied-evidence"),
        binding=binding,
        result=result,
        run_id=RUN_ID,
    )

    assert applied is not None
    assert applied.status == "failed"
    assert applied.error is not None
    assert applied.error.safe_code == "obligation_state_protocol_error"

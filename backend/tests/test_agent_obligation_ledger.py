"""Plan 05 Task 5: Obligation Ledger contracts, digests, transitions, concurrency."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.policy.obligations import (
    REASON_ALLOWED,
    REASON_DUPLICATE_OBLIGATION,
    REASON_EVIDENCE_INVALID,
    REASON_FOLLOWUP_LIMIT,
    REASON_OBLIGATION_NOT_FOUND,
    REASON_OBLIGATION_NOT_PENDING,
    REASON_OWNER_MISMATCH,
    REASON_UNSATISFIABLE,
    CompletionObligation,
    ObligationEvidenceEdge,
    ObligationLedger,
    SkillTerminalSatisfiabilityView,
    build_main_agent_terminal_obligation,
    build_required_followup_obligation,
    build_reserved_obligation,
    build_skill_terminal_obligation,
    compute_obligation_id,
    compute_obligation_ledger_digest,
    compute_predicate_digest,
    compute_requirement_digest,
    compute_result_evidence_digest,
    compute_text_evidence_digest,
    create_initial_obligation_ledger_state,
    deserialize_obligation_ledger_state,
    evaluate_skill_terminal_satisfiability,
    pending_blocking,
    pure_apply_capability_result_evidence,
    pure_apply_provider_text_evidence,
    pure_create_obligation,
    pure_resolve_obligation,
    pure_start_completion_followup,
    reason_code_for_pending,
    serialize_obligation_ledger_state,
)

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
SKILL_A = UUID("22222222-2222-4222-8222-222222222222")
SKILL_B = UUID("33333333-3333-4333-8333-333333333333")
SKILL_COMPAT = UUID("44444444-4444-4444-8444-444444444444")
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64


def _digest_hex(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Contracts / digests
# ---------------------------------------------------------------------------


def test_obligation_id_is_deterministic() -> None:
    a = compute_obligation_id(
        run_id=RUN_ID,
        owner_kind="main_agent",
        owner_id="main_agent",
        obligation_type="terminal_output",
    )
    b = compute_obligation_id(
        run_id=RUN_ID,
        owner_kind="main_agent",
        owner_id="main_agent",
        obligation_type="terminal_output",
    )
    assert a == b
    assert len(a) == 64
    different = compute_obligation_id(
        run_id=RUN_ID,
        owner_kind="skill_version",
        owner_id=str(SKILL_A),
        obligation_type="terminal_output",
    )
    assert different != a


def test_ledger_digest_stable_and_order_independent() -> None:
    o1 = build_main_agent_terminal_obligation(run_id=RUN_ID, revision=1)
    o2 = build_skill_terminal_obligation(
        run_id=RUN_ID, skill_version_id=SKILL_A, revision=1
    )
    # Force same created_revision for pure digest comparison.
    o1 = CompletionObligation(**{**o1.model_dump(), "created_revision": 1})
    o2 = CompletionObligation(**{**o2.model_dump(), "created_revision": 1})
    d1 = compute_obligation_ledger_digest(
        revision=1,
        obligations=(o1, o2),
        evidence_edges=(),
        followup_rounds_started=0,
    )
    d2 = compute_obligation_ledger_digest(
        revision=1,
        obligations=(o2, o1),
        evidence_edges=(),
        followup_rounds_started=0,
    )
    assert d1 == d2


def test_serialize_deserialize_round_trip() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    payload = serialize_obligation_ledger_state(ledger.snapshot())
    restored = deserialize_obligation_ledger_state(payload)
    assert restored.ledger_digest == ledger.snapshot().ledger_digest
    assert len(restored.obligations) == 1
    assert restored.obligations[0].obligation_type == "terminal_output"


def test_completion_obligation_pending_forbids_resolved_revision() -> None:
    with pytest.raises(ValidationError):
        CompletionObligation(
            obligation_id=_DIGEST_A,
            owner_kind="main_agent",
            owner_id="main_agent",
            owner_version_id=None,
            source_call_id=None,
            obligation_type="terminal_output",
            blocking=True,
            requirement_digest=_DIGEST_B,
            status="pending",
            evidence_refs=(),
            created_revision=1,
            resolved_revision=2,
        )


def test_required_followup_requires_source_call_id() -> None:
    with pytest.raises(ValidationError):
        CompletionObligation(
            obligation_id=_DIGEST_A,
            owner_kind="main_agent",
            owner_id="main_agent",
            owner_version_id=None,
            source_call_id=None,
            obligation_type="required_followup",
            blocking=True,
            requirement_digest=_DIGEST_B,
            status="pending",
            evidence_refs=(),
            created_revision=1,
            resolved_revision=None,
        )


def test_reserved_obligation_types_serializable() -> None:
    for otype in ("required_artifact", "approval", "user_input", "reconciliation"):
        ob = build_reserved_obligation(
            run_id=RUN_ID,
            obligation_type=otype,  # type: ignore[arg-type]
            owner_kind="main_agent",
            owner_id="main_agent",
            revision=0,
        )
        state = create_initial_obligation_ledger_state()
        state, decision = pure_create_obligation(state, ob)
        assert decision.allowed
        payload = serialize_obligation_ledger_state(state)
        restored = deserialize_obligation_ledger_state(payload)
        assert any(o.obligation_type == otype for o in restored.obligations)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_main_agent_terminal_at_run_start() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    snap = ledger.snapshot()
    assert snap.revision == 1
    assert len(snap.obligations) == 1
    o = snap.obligations[0]
    assert o.owner_kind == "main_agent"
    assert o.obligation_type == "terminal_output"
    assert o.status == "pending"
    assert o.blocking is True


def test_skill_terminal_when_requires_terminal_output() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    decision = ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=True,
    )
    assert decision.allowed
    pending = ledger.pending_blocking()
    assert len(pending) == 2
    skill_obs = [o for o in pending if o.owner_kind == "skill_version"]
    assert len(skill_obs) == 1
    assert skill_obs[0].owner_version_id == SKILL_A


def test_required_followup_after_needs_followup_result() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    decision = ledger.apply_capability_result(
        call_id="call-1",
        result_status="completed",
        terminal_output=False,
        needs_followup=True,
        output_digest=_DIGEST_A,
        owner_kind="main_agent",
        owner_id="main_agent",
        owner_version_id=None,
    )
    assert decision.allowed
    followups = [
        o
        for o in ledger.snapshot().obligations
        if o.obligation_type == "required_followup"
    ]
    assert len(followups) == 1
    assert followups[0].source_call_id == "call-1"
    assert followups[0].status == "pending"


def test_duplicate_obligation_id_rejected() -> None:
    state = create_initial_obligation_ledger_state()
    o = build_main_agent_terminal_obligation(run_id=RUN_ID, revision=1)
    state, d1 = pure_create_obligation(state, o)
    assert d1.allowed
    # Rebuild same id with pending status.
    dup = build_main_agent_terminal_obligation(run_id=RUN_ID, revision=2)
    state2, d2 = pure_create_obligation(state, dup)
    assert not d2.allowed
    assert d2.reason_code == REASON_DUPLICATE_OBLIGATION
    assert state2.revision == state.revision


# ---------------------------------------------------------------------------
# Satisfaction rules
# ---------------------------------------------------------------------------


def test_nonempty_text_satisfies_main_agent_terminal() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    decision = ledger.apply_provider_text("Hello, final answer.")
    assert decision.allowed
    assert pending_blocking(ledger.snapshot()) == ()
    o = ledger.snapshot().obligations[0]
    assert o.status == "satisfied"
    assert o.resolved_revision is not None
    assert len(o.evidence_refs) == 1


def test_empty_text_satisfies_nothing() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    decision = ledger.apply_provider_text("   ")
    assert not decision.allowed
    assert len(pending_blocking(ledger.snapshot())) == 1


def test_skill_terminal_requires_terminal_text_allowed() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=False,
    )
    ledger.apply_provider_text("Natural answer")
    pending = pending_blocking(ledger.snapshot())
    # Main agent satisfied; skill still pending.
    assert len(pending) == 1
    assert pending[0].owner_kind == "skill_version"

    ledger2 = ObligationLedger.create(run_id=RUN_ID)
    ledger2.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=True,
    )
    ledger2.apply_provider_text("Natural answer")
    assert pending_blocking(ledger2.snapshot()) == ()


def test_terminal_capability_result_satisfies_owner_only() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=False,
    )
    # Terminal result owned by skill A.
    decision = ledger.apply_capability_result(
        call_id="c1",
        result_status="completed",
        terminal_output=True,
        needs_followup=False,
        output_digest=_DIGEST_A,
        owner_kind="skill_version",
        owner_id=str(SKILL_A),
        owner_version_id=SKILL_A,
    )
    assert decision.allowed
    pending = pending_blocking(ledger.snapshot())
    # Main agent still pending; skill satisfied.
    assert len(pending) == 1
    assert pending[0].owner_kind == "main_agent"


def test_failed_result_satisfies_nothing() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    ledger.create_skill_terminal(
        skill_version_id=SKILL_A,
        terminal_text_allowed=False,
    )
    ledger.apply_capability_result(
        call_id="c1",
        result_status="failed",
        terminal_output=True,
        needs_followup=False,
        output_digest=_DIGEST_A,
        owner_kind="skill_version",
        owner_id=str(SKILL_A),
        owner_version_id=SKILL_A,
    )
    assert len(pending_blocking(ledger.snapshot())) == 2


def test_later_text_satisfies_required_followup() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    ledger.apply_capability_result(
        call_id="c1",
        result_status="completed",
        terminal_output=False,
        needs_followup=True,
        output_digest=_DIGEST_A,
        owner_kind="main_agent",
        owner_id="main_agent",
        owner_version_id=None,
    )
    assert any(
        o.obligation_type == "required_followup"
        for o in pending_blocking(ledger.snapshot())
    )
    ledger.apply_provider_text("Follow-up answer after the tool.")
    assert pending_blocking(ledger.snapshot()) == ()


def test_compatible_consumer_edge() -> None:
    state = create_initial_obligation_ledger_state()
    owner_ob = build_skill_terminal_obligation(
        run_id=RUN_ID, skill_version_id=SKILL_A, revision=1
    )
    consumer_ob = build_skill_terminal_obligation(
        run_id=RUN_ID, skill_version_id=SKILL_COMPAT, revision=1
    )
    state, _ = pure_create_obligation(state, owner_ob)
    state, _ = pure_create_obligation(state, consumer_ob)
    consumer_id = [
        o.obligation_id
        for o in state.obligations
        if o.owner_version_id == SKILL_COMPAT
    ][0]

    state, decision = pure_apply_capability_result_evidence(
        state,
        call_id="c1",
        result_status="completed",
        terminal_output=True,
        needs_followup=False,
        output_digest=_DIGEST_A,
        owner_kind="skill_version",
        owner_id=str(SKILL_A),
        owner_version_id=SKILL_A,
        run_id=RUN_ID,
        compatible_consumer_version_ids=(SKILL_COMPAT,),
        binding_contract_digest=_DIGEST_B,
        completion_contract_digest=_DIGEST_C,
        target_consumer_obligation_ids=(consumer_id,),
    )
    assert decision.allowed
    # Both owner and consumer terminal satisfied.
    pending = pending_blocking(state)
    assert not any(o.owner_version_id in {SKILL_A, SKILL_COMPAT} for o in pending)
    # Compatible consumer evidence edge present.
    kinds = {e.evidence_kind for e in state.evidence_edges}
    assert "compatible_consumer" in kinds
    assert "capability_result" in kinds


def test_unrelated_owner_rejection() -> None:
    state = create_initial_obligation_ledger_state()
    owner_ob = build_skill_terminal_obligation(
        run_id=RUN_ID, skill_version_id=SKILL_A, revision=1
    )
    other_ob = build_skill_terminal_obligation(
        run_id=RUN_ID, skill_version_id=SKILL_B, revision=1
    )
    state, _ = pure_create_obligation(state, owner_ob)
    state, _ = pure_create_obligation(state, other_ob)
    other_id = [
        o.obligation_id for o in state.obligations if o.owner_version_id == SKILL_B
    ][0]

    state2, decision = pure_apply_capability_result_evidence(
        state,
        call_id="c1",
        result_status="completed",
        terminal_output=True,
        needs_followup=False,
        output_digest=_DIGEST_A,
        owner_kind="skill_version",
        owner_id=str(SKILL_A),
        owner_version_id=SKILL_A,
        run_id=RUN_ID,
        compatible_consumer_version_ids=(),  # B not listed
        binding_contract_digest=_DIGEST_B,
        completion_contract_digest=_DIGEST_C,
        target_consumer_obligation_ids=(other_id,),
    )
    assert not decision.allowed
    assert decision.reason_code == REASON_OWNER_MISMATCH


def test_terminal_never_reopens() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    oid = ledger.snapshot().obligations[0].obligation_id
    edge = ObligationEvidenceEdge(
        obligation_id=oid,
        evidence_kind="provider_text",
        source_owner_version_id=None,
        source_call_id=None,
        evidence_digest=compute_text_evidence_digest(text="done"),
        predicate_digest=compute_predicate_digest(
            evidence_kind="provider_text",
            obligation_type="terminal_output",
            owner_kind="main_agent",
            terminal_text_allowed=True,
        ),
    )
    d1 = ledger.resolve(obligation_id=oid, status="satisfied", evidence_edge=edge)
    assert d1.allowed
    d2 = ledger.resolve(obligation_id=oid, status="satisfied", evidence_edge=edge)
    assert not d2.allowed
    assert d2.reason_code == REASON_OBLIGATION_NOT_PENDING


def test_waiver_and_failure_require_reason_or_edge() -> None:
    state = create_initial_obligation_ledger_state()
    o = build_main_agent_terminal_obligation(run_id=RUN_ID, revision=1)
    state, _ = pure_create_obligation(state, o)
    oid = state.obligations[0].obligation_id
    state2, d = pure_resolve_obligation(
        state, obligation_id=oid, status="waived", evidence_edge=None, waiver_reason=None
    )
    assert not d.allowed
    assert d.reason_code == REASON_EVIDENCE_INVALID

    state3, d3 = pure_resolve_obligation(
        state,
        obligation_id=oid,
        status="waived",
        evidence_edge=None,
        waiver_reason="cancelled_by_user",
    )
    assert d3.allowed
    assert state3.obligations[0].status == "waived"


def test_duplicate_evidence_rejected() -> None:
    state = create_initial_obligation_ledger_state()
    o = build_main_agent_terminal_obligation(run_id=RUN_ID, revision=1)
    state, _ = pure_create_obligation(state, o)
    oid = state.obligations[0].obligation_id
    edge = ObligationEvidenceEdge(
        obligation_id=oid,
        evidence_kind="provider_text",
        source_owner_version_id=None,
        source_call_id=None,
        evidence_digest=_DIGEST_A,
        predicate_digest=_DIGEST_B,
    )
    state, d1 = pure_resolve_obligation(
        state, obligation_id=oid, status="satisfied", evidence_edge=edge
    )
    assert d1.allowed
    # Cannot re-resolve, but also pure path rejects duplicate on pending with same edge
    # via not-pending. Create a second obligation and try double-apply same edge id.
    o2 = build_skill_terminal_obligation(
        run_id=RUN_ID, skill_version_id=SKILL_A, revision=1
    )
    state, _ = pure_create_obligation(state, o2)
    oid2 = [x.obligation_id for x in state.obligations if x.owner_kind == "skill_version"][0]
    # First apply of edge for oid2
    edge2 = ObligationEvidenceEdge(
        obligation_id=oid2,
        evidence_kind="provider_text",
        source_owner_version_id=None,
        source_call_id=None,
        evidence_digest=_DIGEST_A,
        predicate_digest=_DIGEST_B,
    )
    state, d2 = pure_resolve_obligation(
        state, obligation_id=oid2, status="satisfied", evidence_edge=edge2
    )
    assert d2.allowed
    # Same edge again on already-satisfied → not pending
    state, d3 = pure_resolve_obligation(
        state, obligation_id=oid2, status="satisfied", evidence_edge=edge2
    )
    assert not d3.allowed


def test_reserved_types_block_completion_reason_codes() -> None:
    state = create_initial_obligation_ledger_state()
    for otype, expected_reason in (
        ("required_artifact", "artifact_pending"),
        ("approval", "approval_pending"),
        ("user_input", "user_input_pending"),
        ("reconciliation", "reconciliation_pending"),
    ):
        ob = build_reserved_obligation(
            run_id=RUN_ID,
            obligation_type=otype,  # type: ignore[arg-type]
            owner_kind="main_agent",
            owner_id="main_agent",
            revision=0,
            ordinal=hash(otype) % 1000,
        )
        st = create_initial_obligation_ledger_state()
        st, _ = pure_create_obligation(st, ob)
        pending = pending_blocking(st)
        assert reason_code_for_pending(pending) == expected_reason


# ---------------------------------------------------------------------------
# Satisfiability
# ---------------------------------------------------------------------------


def test_instruction_only_text_forbidden_unsatisfiable() -> None:
    view = SkillTerminalSatisfiabilityView(
        skill_version_id=SKILL_A,
        requires_terminal_output=True,
        terminal_text_allowed=False,
        remaining_provider_slots=5,
        has_terminal_capability_exposure=False,
        terminal_capability_path_available=False,
        max_skill_calls=0,
    )
    ok, reason = evaluate_skill_terminal_satisfiability(view)
    assert not ok
    assert reason == REASON_UNSATISFIABLE


def test_no_eligible_terminal_output_binding_unsatisfiable() -> None:
    view = SkillTerminalSatisfiabilityView(
        skill_version_id=SKILL_A,
        requires_terminal_output=True,
        terminal_text_allowed=False,
        remaining_provider_slots=0,
        has_terminal_capability_exposure=False,
        terminal_capability_path_available=False,
        max_skill_calls=3,
    )
    ok, reason = evaluate_skill_terminal_satisfiability(view)
    assert not ok
    assert reason == REASON_UNSATISFIABLE


def test_capability_only_zero_allowance_unsatisfiable() -> None:
    view = SkillTerminalSatisfiabilityView(
        skill_version_id=SKILL_A,
        requires_terminal_output=True,
        terminal_text_allowed=False,
        remaining_provider_slots=0,
        has_terminal_capability_exposure=True,
        terminal_capability_path_available=True,
        max_skill_calls=0,
    )
    ok, reason = evaluate_skill_terminal_satisfiability(view)
    assert not ok
    assert reason == REASON_UNSATISFIABLE


def test_unavailable_satisfier_unsatisfiable() -> None:
    view = SkillTerminalSatisfiabilityView(
        skill_version_id=SKILL_A,
        requires_terminal_output=True,
        terminal_text_allowed=False,
        remaining_provider_slots=0,
        has_terminal_capability_exposure=True,
        terminal_capability_path_available=False,
        max_skill_calls=3,
    )
    ok, reason = evaluate_skill_terminal_satisfiability(view)
    assert not ok
    assert reason == REASON_UNSATISFIABLE


def test_text_path_no_remaining_slots_unsatisfiable() -> None:
    view = SkillTerminalSatisfiabilityView(
        skill_version_id=SKILL_A,
        requires_terminal_output=True,
        terminal_text_allowed=True,
        remaining_provider_slots=0,
        has_terminal_capability_exposure=False,
        terminal_capability_path_available=False,
        max_skill_calls=0,
    )
    ok, reason = evaluate_skill_terminal_satisfiability(view)
    assert not ok
    assert reason == REASON_UNSATISFIABLE


def test_text_path_with_slots_satisfiable() -> None:
    view = SkillTerminalSatisfiabilityView(
        skill_version_id=SKILL_A,
        requires_terminal_output=True,
        terminal_text_allowed=True,
        remaining_provider_slots=1,
        has_terminal_capability_exposure=False,
        terminal_capability_path_available=False,
        max_skill_calls=0,
    )
    ok, reason = evaluate_skill_terminal_satisfiability(view)
    assert ok
    assert reason == REASON_ALLOWED


def test_capability_path_satisfiable() -> None:
    view = SkillTerminalSatisfiabilityView(
        skill_version_id=SKILL_A,
        requires_terminal_output=True,
        terminal_text_allowed=False,
        remaining_provider_slots=0,
        has_terminal_capability_exposure=True,
        terminal_capability_path_available=True,
        max_skill_calls=2,
    )
    ok, reason = evaluate_skill_terminal_satisfiability(view)
    assert ok


def test_requires_false_always_ok() -> None:
    view = SkillTerminalSatisfiabilityView(
        skill_version_id=SKILL_A,
        requires_terminal_output=False,
        terminal_text_allowed=False,
        remaining_provider_slots=0,
        has_terminal_capability_exposure=False,
        terminal_capability_path_available=False,
        max_skill_calls=0,
    )
    ok, _ = evaluate_skill_terminal_satisfiability(view)
    assert ok


# ---------------------------------------------------------------------------
# Followup counter / concurrency
# ---------------------------------------------------------------------------


def test_completion_followup_limit() -> None:
    state = create_initial_obligation_ledger_state()
    state, d1 = pure_start_completion_followup(state, max_completion_followup_rounds=1)
    assert d1.allowed
    assert state.followup_rounds_started == 1
    state2, d2 = pure_start_completion_followup(state, max_completion_followup_rounds=1)
    assert not d2.allowed
    assert d2.reason_code == REASON_FOLLOWUP_LIMIT


def test_concurrent_create_is_serialized() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID, create_main_agent_terminal=True)

    def _add(i: int) -> str:
        skill = UUID(int=0x2000 + i)
        d = ledger.create_skill_terminal(
            skill_version_id=skill,
            terminal_text_allowed=True,
        )
        return d.reason_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_add, i) for i in range(20)]
        results = [f.result() for f in as_completed(futures)]
    assert all(r == REASON_ALLOWED for r in results)
    # main + 20 skills
    assert len(ledger.snapshot().obligations) == 21


def test_cas_rejects_stale_revision() -> None:
    ledger = ObligationLedger.create(run_id=RUN_ID)
    snap = ledger.snapshot()
    # Build a plausible next state with wrong expected revision.
    o = build_skill_terminal_obligation(
        run_id=RUN_ID, skill_version_id=SKILL_A, revision=snap.revision + 1
    )
    from app.assistant.policy.obligations import pure_create_obligation as _create

    new_state, _ = _create(snap, o)
    assert ledger.compare_and_swap(snap.revision, new_state) is True
    # Stale
    assert ledger.compare_and_swap(snap.revision, new_state) is False


def test_obligation_not_found() -> None:
    state = create_initial_obligation_ledger_state()
    _, d = pure_resolve_obligation(
        state,
        obligation_id=_DIGEST_A,
        status="failed",
        waiver_reason="x",
    )
    assert not d.allowed
    assert d.reason_code == REASON_OBLIGATION_NOT_FOUND


def test_text_evidence_digest_is_content_hash() -> None:
    d1 = compute_text_evidence_digest(text="hello")
    d2 = compute_text_evidence_digest(text="hello")
    d3 = compute_text_evidence_digest(text="hello!")
    assert d1 == d2
    assert d1 != d3
    assert d1 == hashlib.sha256(b"hello").hexdigest()


def test_result_evidence_digest_stable() -> None:
    d = compute_result_evidence_digest(
        call_id="c1",
        result_status="completed",
        terminal_output=True,
        needs_followup=False,
        output_digest=_DIGEST_A,
    )
    assert d == compute_result_evidence_digest(
        call_id="c1",
        result_status="completed",
        terminal_output=True,
        needs_followup=False,
        output_digest=_DIGEST_A,
    )


def test_requirement_digest_includes_owner() -> None:
    a = compute_requirement_digest(
        obligation_type="terminal_output",
        owner_kind="main_agent",
        owner_id="main_agent",
    )
    b = compute_requirement_digest(
        obligation_type="terminal_output",
        owner_kind="skill_version",
        owner_id=str(SKILL_A),
        owner_version_id=SKILL_A,
    )
    assert a != b

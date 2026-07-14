"""Plan 05 Task 3: revisioned budget ledger and reservation protocol."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.policy.budgets import (
    REASON_ACTIVE_SKILLS,
    REASON_AGENT_DEPTH,
    REASON_ARGUMENTS_DIGEST_MISMATCH,
    REASON_CANCELLED,
    REASON_CAPABILITY_DEPTH,
    REASON_COMPLETION_FOLLOWUPS,
    REASON_COMPLETION_TOKENS,
    REASON_DEADLINE,
    REASON_DUPLICATE_CALL_ID,
    REASON_MAIN_AGENT_CYCLES,
    REASON_OWNER_CALLS,
    REASON_OWNER_LIMITS_MISSING,
    REASON_OWNER_READ_SIGNATURE,
    REASON_PARALLEL,
    REASON_PROMPT_TOKENS,
    REASON_PROVIDER_ROUNDS,
    REASON_READ_SIGNATURE,
    REASON_RESERVATION_NOT_FOUND,
    REASON_RESERVATION_STATE_INVALID,
    REASON_TOTAL_CALLS,
    BudgetLedger,
    BudgetReserveRequest,
    DeterministicBudgetClock,
    compute_ledger_digest,
    compute_read_signature,
    create_initial_ledger_state,
    deserialize_ledger_state,
    remaining_completion_tokens,
    serialize_ledger_state,
)
from app.assistant.policy.contracts import (
    ASSISTANT_CHAT_RUN_BUDGET_DEFAULTS,
    ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS,
    MAIN_AGENT_OWNER_DEFAULT_MAX_CALLS,
    RunBudgetLimits,
    build_owner_budget_limits,
    normalize_owner_budget_limits,
    normalize_run_budget_limits,
)

PROFILE_VERSION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SKILL_VERSION_A = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
SKILL_VERSION_B = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
SKILL_VERSION_COMPAT = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")

_BINDING_A = "a" * 64
_BINDING_B = "b" * 64
_ARGS_EMPTY = sha256_canonical_json({})
_ARGS_ONE = sha256_canonical_json({"q": 1})
_ARGS_TWO = sha256_canonical_json({"q": 2})


def _digest_hex(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _limits(**overrides: Any) -> RunBudgetLimits:
    base = normalize_run_budget_limits()
    payload = base.model_dump()
    payload.update(overrides)
    return RunBudgetLimits(**payload)


def _main_owner(limits: RunBudgetLimits | None = None) -> Any:
    run = limits or normalize_run_budget_limits()
    return normalize_owner_budget_limits(
        owner_kind="main_agent",
        owner_version_id=PROFILE_VERSION_ID,
        run_limits=run,
    )


def _skill_owner(
    version_id: UUID = SKILL_VERSION_A,
    *,
    max_calls: int = 4,
    max_same: int = 2,
    limits: RunBudgetLimits | None = None,
) -> Any:
    run = limits or normalize_run_budget_limits()
    return normalize_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=version_id,
        run_limits=run,
        max_skill_calls=max_calls,
        max_same_read_calls=max_same,
    )


def _clock() -> DeterministicBudgetClock:
    return DeterministicBudgetClock(
        utc_start=datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc),
        monotonic_start_ms=1_000_000,
    )


def _ledger(
    *,
    limits: RunBudgetLimits | None = None,
    owners: list[Any] | None = None,
    clock: DeterministicBudgetClock | None = None,
    events: list[dict[str, Any]] | None = None,
) -> BudgetLedger:
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


def _req(
    call_id: str,
    *,
    owner_kind: str = "main_agent",
    owner_version_id: UUID = PROFILE_VERSION_ID,
    domain_key: str = "skill.search",
    side_effect: str = "read",
    arguments_digest: str = _ARGS_EMPTY,
    binding_contract_digest: str = _BINDING_A,
    capability_depth: int = 1,
    agent_depth: int = 1,
) -> BudgetReserveRequest:
    return BudgetReserveRequest(
        call_id=call_id,
        owner_kind=owner_kind,  # type: ignore[arg-type]
        owner_version_id=owner_version_id,
        domain_key=domain_key,
        side_effect=side_effect,  # type: ignore[arg-type]
        arguments_digest=arguments_digest,
        binding_contract_digest=binding_contract_digest,
        capability_depth=capability_depth,
        agent_depth=agent_depth,
    )


# ---------------------------------------------------------------------------
# Limit boundary / normalization (Task 1 reuse + ledger admission)
# ---------------------------------------------------------------------------


def test_defaults_and_hard_ceilings_match_plan() -> None:
    assert ASSISTANT_CHAT_RUN_BUDGET_DEFAULTS["max_provider_rounds"] == 8
    assert ASSISTANT_CHAT_RUN_BUDGET_HARD_CEILINGS["max_provider_rounds"] == 16
    limits = normalize_run_budget_limits()
    assert limits.max_provider_rounds == 8
    assert limits.max_main_agent_cycles == 1
    assert limits.max_active_skills == 4
    assert limits.max_total_capability_calls == 16
    assert limits.max_parallel_calls == 4
    assert limits.max_capability_depth == 4
    assert limits.max_agent_depth == 2
    assert limits.max_same_read_signature == 3
    assert limits.max_prompt_tokens is None
    assert limits.max_completion_tokens == 4096
    assert limits.max_wall_time_ms == 120_000
    assert limits.max_completion_followup_rounds == 2


def test_operator_and_profile_only_lower_settings() -> None:
    raised = normalize_run_budget_limits(
        operator_limits={"max_provider_rounds": 100, "max_total_capability_calls": 999}
    )
    assert raised.max_provider_rounds == 8
    assert raised.max_total_capability_calls == 16

    lowered = normalize_run_budget_limits(
        operator_limits={
            "max_provider_rounds": 3,
            "max_parallel_calls": 2,
            "max_same_read_signature": 1,
            "max_completion_tokens": 512,
            "max_wall_time_ms": 30_000,
        }
    )
    assert lowered.max_provider_rounds == 3
    assert lowered.max_parallel_calls == 2
    assert lowered.max_same_read_signature == 1
    assert lowered.max_completion_tokens == 512
    assert lowered.max_wall_time_ms == 30_000

    profile = normalize_run_budget_limits(
        profile_output_budget={
            "max_provider_rounds": 5,
            "max_total_capability_calls": 10,
            "max_parallel_calls": 2,
            "max_capability_depth": 3,
            "max_agent_depth": 1,
            "max_same_read_signature": 2,
            "max_completion_tokens": 2048,
            "max_wall_time_ms": 60_000,
            "max_completion_followup_rounds": 1,
        },
        profile_context_budget={"max_active_skills": 2},
    )
    assert profile.max_provider_rounds == 5
    assert profile.max_active_skills == 2
    assert profile.max_completion_followup_rounds == 1


def test_ledger_admits_normalized_limits_without_settings_lookup() -> None:
    limits = normalize_run_budget_limits(
        operator_limits={"max_total_capability_calls": 6, "max_parallel_calls": 2}
    )
    ledger = _ledger(limits=limits)
    snap = ledger.snapshot()
    assert snap.limits == limits
    assert snap.revision == 0
    assert snap.capability_calls_started == 0


# ---------------------------------------------------------------------------
# Immutable revision / digest / source mutation
# ---------------------------------------------------------------------------


def test_initial_state_immutable_revision_and_digest() -> None:
    clock = _clock()
    limits = normalize_run_budget_limits()
    state = create_initial_ledger_state(
        limits=limits,
        owner_limits=[_main_owner(limits)],
        started_at_utc=clock.utc_now(),
    )
    assert state.revision == 0
    assert len(state.ledger_digest) == 64
    recomputed = compute_ledger_digest(
        revision=state.revision,
        limits=state.limits,
        owner_limits=state.owner_limits,
        provider_rounds_started=state.provider_rounds_started,
        main_agent_cycles_started=state.main_agent_cycles_started,
        capability_calls_started=state.capability_calls_started,
        completion_followups_started=state.completion_followups_started,
        prompt_tokens_used=state.prompt_tokens_used,
        completion_tokens_used=state.completion_tokens_used,
        owner_calls_started=state.owner_calls_started,
        global_read_signatures=state.global_read_signatures,
        owner_read_signatures=state.owner_read_signatures,
        reservations=state.reservations,
        denial_count=state.denial_count,
        started_at_utc=state.started_at_utc,
        deadline_at_utc=state.deadline_at_utc,
    )
    assert recomputed == state.ledger_digest

    with pytest.raises(ValidationError):
        state.revision = 1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        state.capability_calls_started = 9  # type: ignore[misc]


def test_source_mutation_of_limits_mapping_does_not_affect_ledger() -> None:
    operator = {"max_provider_rounds": 3, "max_total_capability_calls": 8}
    limits = normalize_run_budget_limits(operator_limits=operator)
    ledger = _ledger(limits=limits)
    operator["max_provider_rounds"] = 99
    operator["max_total_capability_calls"] = 999
    assert ledger.snapshot().limits.max_provider_rounds == 3
    assert ledger.snapshot().limits.max_total_capability_calls == 8


def test_each_transition_bumps_revision_and_digest() -> None:
    ledger = _ledger()
    r0 = ledger.snapshot()
    d = ledger.reserve_one(_req("c1"))
    assert d.allowed
    r1 = ledger.snapshot()
    assert r1.revision == r0.revision + 1
    assert r1.ledger_digest != r0.ledger_digest
    d2 = ledger.mark_started("c1", _ARGS_EMPTY)
    assert d2.allowed
    r2 = ledger.snapshot()
    assert r2.revision == r1.revision + 1
    assert r2.ledger_digest != r1.ledger_digest
    d3 = ledger.finish("c1")
    assert d3.allowed
    r3 = ledger.snapshot()
    assert r3.revision == r2.revision + 1


# ---------------------------------------------------------------------------
# Reserve / start / finish / release lifecycle
# ---------------------------------------------------------------------------


def test_reserve_start_finish_lifecycle_consumes_at_started() -> None:
    events: list[dict[str, Any]] = []
    ledger = _ledger(events=events)
    d = ledger.reserve_one(_req("call-1", side_effect="compute", domain_key="tool.x"))
    assert d.allowed
    assert d.reservation is not None
    assert d.reservation.state == "reserved"
    snap = ledger.snapshot()
    assert snap.capability_calls_started == 0
    assert snap.owner_calls_started == ()

    d2 = ledger.mark_started("call-1", _ARGS_EMPTY)
    assert d2.allowed
    assert d2.reservation is not None
    assert d2.reservation.state == "started"
    snap2 = ledger.snapshot()
    assert snap2.capability_calls_started == 1
    assert snap2.owner_calls_started[0].calls_started == 1

    d3 = ledger.finish("call-1")
    assert d3.allowed
    assert d3.reservation is not None
    assert d3.reservation.state == "finished"
    snap3 = ledger.snapshot()
    assert snap3.capability_calls_started == 1  # remains consumed

    names = [e["event"] for e in events]
    assert "budget_reserved" in names
    assert "budget_started" in names
    assert "budget_finished" in names


def test_release_unstarted_does_not_consume_counts() -> None:
    ledger = _ledger()
    assert ledger.reserve_one(_req("r1")).allowed
    d = ledger.release_unstarted("r1")
    assert d.allowed
    assert d.reservation is not None
    assert d.reservation.state == "released"
    snap = ledger.snapshot()
    assert snap.capability_calls_started == 0
    assert snap.owner_calls_started == ()


def test_duplicate_call_id_denied() -> None:
    ledger = _ledger()
    assert ledger.reserve_one(_req("dup")).allowed
    d = ledger.reserve_one(_req("dup"))
    assert not d.allowed
    assert d.reason_code == REASON_DUPLICATE_CALL_ID
    assert ledger.snapshot().denial_count == 1


def test_arguments_digest_mismatch_blocks_start() -> None:
    ledger = _ledger()
    assert ledger.reserve_one(_req("m1", arguments_digest=_ARGS_ONE)).allowed
    d = ledger.mark_started("m1", _ARGS_TWO)
    assert not d.allowed
    assert d.reason_code == REASON_ARGUMENTS_DIGEST_MISMATCH
    # Failure before start releases the reservation (Plan §7.3 step 8)
    res = next(r for r in ledger.snapshot().reservations if r.call_id == "m1")
    assert res.state == "released"
    assert d.reservation is not None
    assert d.reservation.state == "released"
    assert ledger.snapshot().capability_calls_started == 0
    assert ledger.snapshot().denial_count == 1


def test_policy_input_denial_before_start_records_metric_without_allowance() -> None:
    ledger = _ledger()
    d = ledger.record_denial(
        reason_code="owner_side_effect_denied",
        dimension="policy",
        call_id="denied-1",
    )
    assert not d.allowed
    assert ledger.snapshot().denial_count == 1
    assert ledger.snapshot().capability_calls_started == 0
    assert ledger.snapshot().reservations == ()


def test_failure_timeout_cancellation_after_start_keeps_counts() -> None:
    ledger = _ledger()
    assert ledger.reserve_one(_req("f1", side_effect="none")).allowed
    assert ledger.mark_started("f1", _ARGS_EMPTY).allowed
    assert ledger.finish("f1").allowed  # failure/timeout/cancel after start
    assert ledger.snapshot().capability_calls_started == 1


def test_finalize_reservation_handles_unexpected_exceptions() -> None:
    ledger = _ledger()
    # Unstarted path → release
    assert ledger.reserve_one(_req("u1")).allowed
    d = ledger.finalize_reservation("u1")
    assert d.allowed
    assert d.reservation is not None
    assert d.reservation.state == "released"
    assert ledger.snapshot().capability_calls_started == 0

    # Started path → finish
    assert ledger.reserve_one(_req("u2")).allowed
    assert ledger.mark_started("u2", _ARGS_EMPTY).allowed
    d2 = ledger.finalize_reservation("u2")
    assert d2.allowed
    assert d2.reservation is not None
    assert d2.reservation.state == "finished"
    assert ledger.snapshot().capability_calls_started == 1

    # Missing
    d3 = ledger.finalize_reservation("missing")
    assert not d3.allowed
    assert d3.reason_code == REASON_RESERVATION_NOT_FOUND


def test_cannot_finish_reserved_or_start_finished() -> None:
    ledger = _ledger()
    assert ledger.reserve_one(_req("s1")).allowed
    d = ledger.finish("s1")
    assert not d.allowed
    assert d.reason_code == REASON_RESERVATION_STATE_INVALID

    assert ledger.mark_started("s1", _ARGS_EMPTY).allowed
    assert ledger.finish("s1").allowed
    d2 = ledger.mark_started("s1", _ARGS_EMPTY)
    assert not d2.allowed
    assert d2.reason_code == REASON_RESERVATION_STATE_INVALID


# ---------------------------------------------------------------------------
# Run/owner totals, controls, skill activation, compatible consumer, signatures
# ---------------------------------------------------------------------------


def test_controls_count_against_run_and_main_agent() -> None:
    limits = _limits(max_total_capability_calls=3, max_parallel_calls=2)
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)])
    for i, key in enumerate(
        ("skill.search", "skill.inject", "skill.read_resource", "artifact.read")
    ):
        d = ledger.reserve_one(
            _req(f"ctrl-{i}", domain_key=key, side_effect="read", arguments_digest=_digest_hex(f"a{i}"))
        )
        if i < 3:
            assert d.allowed, d.reason_code
            assert ledger.mark_started(f"ctrl-{i}", _digest_hex(f"a{i}")).allowed
            assert ledger.finish(f"ctrl-{i}").allowed
        else:
            assert not d.allowed
            assert d.reason_code == REASON_TOTAL_CALLS
    assert ledger.snapshot().capability_calls_started == 3
    assert ledger.snapshot().owner_calls_started[0].calls_started == 3


def test_owner_total_exhaustion_and_run_total_exhaustion() -> None:
    limits = _limits(max_total_capability_calls=10, max_parallel_calls=4)
    main = build_owner_budget_limits(
        owner_kind="main_agent",
        owner_version_id=PROFILE_VERSION_ID,
        max_calls=2,
        max_same_read_signature=3,
    )
    skill = _skill_owner(max_calls=2, limits=limits)
    ledger = _ledger(limits=limits, owners=[main, skill])

    for i in range(2):
        assert ledger.reserve_one(
            _req(f"m{i}", side_effect="compute", arguments_digest=_digest_hex(f"m{i}"))
        ).allowed
        assert ledger.mark_started(f"m{i}", _digest_hex(f"m{i}")).allowed
        assert ledger.finish(f"m{i}").allowed
    denied = ledger.reserve_one(_req("mX", side_effect="compute"))
    assert not denied.allowed
    assert denied.reason_code == REASON_OWNER_CALLS

    for i in range(2):
        assert ledger.reserve_one(
            _req(
                f"s{i}",
                owner_kind="skill_version",
                owner_version_id=SKILL_VERSION_A,
                side_effect="compute",
                arguments_digest=_digest_hex(f"s{i}"),
            )
        ).allowed
        assert ledger.mark_started(f"s{i}", _digest_hex(f"s{i}")).allowed
        assert ledger.finish(f"s{i}").allowed


def test_skill_activation_adds_owner_bucket_without_amplifying_run() -> None:
    limits = normalize_run_budget_limits()
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)])
    before = ledger.snapshot()
    skill = _skill_owner(SKILL_VERSION_A, max_calls=5, limits=limits)
    d = ledger.add_owner_limits(skill)
    assert d.allowed
    after = ledger.snapshot()
    assert after.limits == before.limits
    assert after.limits.model_dump() == before.limits.model_dump()
    assert after.capability_calls_started == before.capability_calls_started
    assert after.provider_rounds_started == before.provider_rounds_started
    assert after.deadline_at_utc == before.deadline_at_utc
    assert after.started_at_utc == before.started_at_utc
    assert any(
        o.owner_version_id == SKILL_VERSION_A for o in after.owner_limits
    )


def test_adding_many_skills_leaves_run_limits_byte_identical() -> None:
    limits = normalize_run_budget_limits()
    ledger = _ledger(limits=limits, owners=[_main_owner(limits)])
    limits_bytes = json.dumps(
        ledger.snapshot().limits.model_dump(mode="json"), sort_keys=True
    )
    for i in range(limits.max_active_skills):
        vid = UUID(int=i + 100)
        skill = normalize_owner_budget_limits(
            owner_kind="skill_version",
            owner_version_id=vid,
            run_limits=limits,
            max_skill_calls=3,
            max_same_read_calls=1,
        )
        assert ledger.add_owner_limits(skill).allowed
    after_bytes = json.dumps(
        ledger.snapshot().limits.model_dump(mode="json"), sort_keys=True
    )
    assert after_bytes == limits_bytes
    # Next skill denied by max_active_skills
    extra = normalize_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=UUID(int=999),
        run_limits=limits,
        max_skill_calls=1,
        max_same_read_calls=1,
    )
    d = ledger.add_owner_limits(extra)
    assert not d.allowed
    assert d.reason_code == REASON_ACTIVE_SKILLS


def test_compatible_consumer_does_not_get_allowance_first_owner_charged() -> None:
    """Compatible consumer has no owner bucket spend; frozen first owner is charged."""
    limits = _limits(max_total_capability_calls=8, max_parallel_calls=4)
    first = _skill_owner(SKILL_VERSION_A, max_calls=1, limits=limits)
    # Compatible consumer is not given a separate allowance for the shared exposure.
    ledger = _ledger(limits=limits, owners=[_main_owner(limits), first])
    # Call charged to first owner
    assert ledger.reserve_one(
        _req(
            "shared-1",
            owner_kind="skill_version",
            owner_version_id=SKILL_VERSION_A,
            side_effect="compute",
        )
    ).allowed
    assert ledger.mark_started("shared-1", _ARGS_EMPTY).allowed
    assert ledger.finish("shared-1").allowed
    # First owner exhausted
    d = ledger.reserve_one(
        _req(
            "shared-2",
            owner_kind="skill_version",
            owner_version_id=SKILL_VERSION_A,
            side_effect="compute",
        )
    )
    assert not d.allowed
    assert d.reason_code == REASON_OWNER_CALLS
    # Compatible consumer without owner limits cannot reserve
    d2 = ledger.reserve_one(
        _req(
            "compat-1",
            owner_kind="skill_version",
            owner_version_id=SKILL_VERSION_COMPAT,
            side_effect="compute",
        )
    )
    assert not d2.allowed
    assert d2.reason_code == REASON_OWNER_LIMITS_MISSING


def test_global_and_owner_read_signature_limits() -> None:
    limits = _limits(
        max_total_capability_calls=20,
        max_parallel_calls=4,
        max_same_read_signature=2,
    )
    main = build_owner_budget_limits(
        owner_kind="main_agent",
        owner_version_id=PROFILE_VERSION_ID,
        max_calls=10,
        max_same_read_signature=2,
    )
    ledger = _ledger(limits=limits, owners=[main])
    # Same read signature: binding + args
    for i in range(2):
        assert ledger.reserve_one(
            _req(f"rs{i}", arguments_digest=_ARGS_ONE, binding_contract_digest=_BINDING_A)
        ).allowed
        assert ledger.mark_started(f"rs{i}", _ARGS_ONE).allowed
        assert ledger.finish(f"rs{i}").allowed
    d = ledger.reserve_one(
        _req("rsX", arguments_digest=_ARGS_ONE, binding_contract_digest=_BINDING_A)
    )
    assert not d.allowed
    assert d.reason_code == REASON_READ_SIGNATURE

    # Different binding → different signature allowed
    assert ledger.reserve_one(
        _req("rsY", arguments_digest=_ARGS_ONE, binding_contract_digest=_BINDING_B)
    ).allowed

    # none/compute do not touch signature counters
    assert ledger.reserve_one(
        _req("c1", side_effect="compute", arguments_digest=_ARGS_ONE)
    ).allowed
    assert ledger.mark_started("c1", _ARGS_ONE).allowed
    snap = ledger.snapshot()
    # global sigs only for the read ones
    assert all(s.count <= 2 for s in snap.global_read_signatures)


def test_owner_read_signature_limit_independent() -> None:
    limits = _limits(
        max_total_capability_calls=20,
        max_parallel_calls=4,
        max_same_read_signature=10,
    )
    skill = build_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=SKILL_VERSION_A,
        max_calls=10,
        max_same_read_signature=1,
    )
    ledger = _ledger(limits=limits, owners=[_main_owner(limits), skill])
    assert ledger.reserve_one(
        _req(
            "ors1",
            owner_kind="skill_version",
            owner_version_id=SKILL_VERSION_A,
            arguments_digest=_ARGS_ONE,
        )
    ).allowed
    assert ledger.mark_started("ors1", _ARGS_ONE).allowed
    assert ledger.finish("ors1").allowed
    d = ledger.reserve_one(
        _req(
            "ors2",
            owner_kind="skill_version",
            owner_version_id=SKILL_VERSION_A,
            arguments_digest=_ARGS_ONE,
        )
    )
    assert not d.allowed
    assert d.reason_code == REASON_OWNER_READ_SIGNATURE


def test_read_signature_formula() -> None:
    sig = compute_read_signature(
        binding_contract_digest=_BINDING_A,
        arguments_digest=_ARGS_ONE,
    )
    expected = hashlib.sha256((_BINDING_A + _ARGS_ONE).encode("utf-8")).hexdigest()
    assert sig == expected
    assert sig != compute_read_signature(
        binding_contract_digest=_BINDING_B,
        arguments_digest=_ARGS_ONE,
    )


def test_parallel_and_depth_limits() -> None:
    limits = _limits(
        max_total_capability_calls=20,
        max_parallel_calls=2,
        max_capability_depth=2,
        max_agent_depth=1,
    )
    ledger = _ledger(limits=limits)
    assert ledger.reserve_one(_req("p1")).allowed
    assert ledger.reserve_one(_req("p2")).allowed
    d = ledger.reserve_one(_req("p3"))
    assert not d.allowed
    assert d.reason_code == REASON_PARALLEL

    ledger2 = _ledger(limits=limits)
    d2 = ledger2.reserve_one(_req("d1", capability_depth=3))
    assert not d2.allowed
    assert d2.reason_code == REASON_CAPABILITY_DEPTH
    d3 = ledger2.reserve_one(_req("d2", agent_depth=2))
    assert not d3.allowed
    assert d3.reason_code == REASON_AGENT_DEPTH


def test_reserve_batch_all_or_none() -> None:
    limits = _limits(max_total_capability_calls=10, max_parallel_calls=2)
    ledger = _ledger(limits=limits)
    d = ledger.reserve_batch(
        [
            _req("b1", arguments_digest=_digest_hex("1")),
            _req("b2", arguments_digest=_digest_hex("2")),
            _req("b3", arguments_digest=_digest_hex("3")),
        ]
    )
    assert not d.allowed
    assert d.reason_code == REASON_PARALLEL
    assert ledger.snapshot().reservations == ()  # none reserved

    d2 = ledger.reserve_batch(
        [
            _req("b1", arguments_digest=_digest_hex("1")),
            _req("b2", arguments_digest=_digest_hex("2")),
        ]
    )
    assert d2.allowed
    assert len(d2.reservations) == 2
    assert len(_active(ledger)) == 2


def _active(ledger: BudgetLedger) -> list[Any]:
    return [r for r in ledger.snapshot().reservations if r.state in ("reserved", "started")]


# ---------------------------------------------------------------------------
# Monotonic deadline
# ---------------------------------------------------------------------------


def test_monotonic_deadline_not_extended_by_utc_rollback_or_advance() -> None:
    clock = _clock()
    limits = _limits(max_wall_time_ms=5_000)
    ledger = _ledger(limits=limits, clock=clock)
    mono_deadline = ledger.mono_deadline_ms()
    assert mono_deadline == 1_000_000 + 5_000

    # UTC wall clock advances a lot — live deadline unchanged
    clock.advance_utc(seconds=3600)
    assert ledger.mono_deadline_ms() == mono_deadline
    assert ledger.reserve_one(_req("t1")).allowed

    # UTC rollback — still cannot extend mono deadline
    clock.rollback_utc(seconds=7200)
    assert ledger.mono_deadline_ms() == mono_deadline

    # Advance monotonic past deadline
    clock.advance_monotonic(milliseconds=10_000)
    d = ledger.reserve_one(_req("t2"))
    assert not d.allowed
    assert d.reason_code == REASON_DEADLINE

    # UTC still in the past relative to original start — still denied and released
    clock.set_utc(datetime(2026, 7, 14, 11, 0, 0, tzinfo=timezone.utc))
    d2 = ledger.mark_started("t1", _ARGS_EMPTY)
    assert not d2.allowed
    assert d2.reason_code == REASON_DEADLINE
    res = next(r for r in ledger.snapshot().reservations if r.call_id == "t1")
    assert res.state == "released"
    assert ledger.snapshot().capability_calls_started == 0


def test_deadline_checked_on_provider_round_and_followup() -> None:
    clock = _clock()
    limits = _limits(max_wall_time_ms=100, max_provider_rounds=4)
    ledger = _ledger(limits=limits, clock=clock)
    clock.advance_monotonic(milliseconds=200)
    assert ledger.start_provider_round().reason_code == REASON_DEADLINE
    assert ledger.start_completion_followup().reason_code == REASON_DEADLINE
    assert ledger.start_main_agent_cycle().reason_code == REASON_DEADLINE


# ---------------------------------------------------------------------------
# Provider rounds / tokens / finalization
# ---------------------------------------------------------------------------


def test_provider_rounds_and_finalization_count() -> None:
    limits = _limits(max_provider_rounds=3, max_completion_followup_rounds=1)
    ledger = _ledger(limits=limits)
    assert ledger.start_provider_round().allowed
    assert ledger.start_provider_round(is_finalization=True).allowed
    assert ledger.start_provider_round().allowed
    d = ledger.start_provider_round()
    assert not d.allowed
    assert d.reason_code == REASON_PROVIDER_ROUNDS
    assert ledger.snapshot().provider_rounds_started == 3


def test_completion_tokens_accumulate_and_block_next() -> None:
    limits = _limits(max_completion_tokens=100, max_provider_rounds=5)
    ledger = _ledger(limits=limits)
    assert ledger.start_provider_round().allowed
    assert ledger.record_token_usage(prompt_tokens=10, completion_tokens=80).allowed
    assert remaining_completion_tokens(ledger.snapshot()) == 20
    assert ledger.remaining_completion_tokens() == 20
    # Overflow does not undo completed request
    assert ledger.record_token_usage(completion_tokens=50).allowed
    assert ledger.snapshot().completion_tokens_used == 130
    # Next round blocked
    d = ledger.start_provider_round()
    assert not d.allowed
    assert d.reason_code == REASON_COMPLETION_TOKENS
    # Next capability also blocked
    d2 = ledger.reserve_one(_req("after-overflow"))
    assert not d2.allowed
    assert d2.reason_code == REASON_COMPLETION_TOKENS


def test_prompt_tokens_only_when_estimator_and_limit_present() -> None:
    # Default: max_prompt_tokens is None → estimator ignored / not enforced
    ledger = _ledger()
    d = ledger.start_provider_round(estimated_prompt_tokens=9_999_999)
    assert d.allowed

    limits = _limits(max_prompt_tokens=1000, max_provider_rounds=4)
    # Coherence: max_completion_followup must stay < max_provider_rounds
    ledger2 = _ledger(limits=limits)
    # No estimator → not enforced
    assert ledger2.start_provider_round().allowed
    # Estimator over limit
    d2 = ledger2.start_provider_round(estimated_prompt_tokens=1001)
    assert not d2.allowed
    assert d2.reason_code == REASON_PROMPT_TOKENS
    # Estimator within limit
    assert ledger2.start_provider_round(estimated_prompt_tokens=500).allowed


def test_main_agent_cycles_and_completion_followups() -> None:
    limits = _limits(max_main_agent_cycles=1, max_completion_followup_rounds=2)
    ledger = _ledger(limits=limits)
    assert ledger.start_main_agent_cycle().allowed
    d = ledger.start_main_agent_cycle()
    assert not d.allowed
    assert d.reason_code == REASON_MAIN_AGENT_CYCLES

    assert ledger.start_completion_followup().allowed
    assert ledger.start_completion_followup().allowed
    d2 = ledger.start_completion_followup()
    assert not d2.allowed
    assert d2.reason_code == REASON_COMPLETION_FOLLOWUPS


def test_cancellation_blocks_reserve_and_start() -> None:
    ledger = _ledger()
    assert ledger.reserve_one(_req("c1")).allowed
    ledger.cancel()
    assert ledger.is_cancelled()
    d = ledger.reserve_one(_req("c2"))
    assert not d.allowed
    assert d.reason_code == REASON_CANCELLED
    d2 = ledger.mark_started("c1", _ARGS_EMPTY)
    assert not d2.allowed
    assert d2.reason_code == REASON_CANCELLED
    # Cancel-before-start releases the reservation
    res = next(r for r in ledger.snapshot().reservations if r.call_id == "c1")
    assert res.state == "released"
    assert ledger.snapshot().capability_calls_started == 0
    # Idempotent release still works for cleanup
    assert ledger.release_unstarted("c1").allowed


# ---------------------------------------------------------------------------
# Thread-safe lock/CAS + safe events
# ---------------------------------------------------------------------------


def test_thread_safe_parallel_reserve_contention() -> None:
    limits = _limits(max_total_capability_calls=20, max_parallel_calls=4)
    main = build_owner_budget_limits(
        owner_kind="main_agent",
        owner_version_id=PROFILE_VERSION_ID,
        max_calls=20,
        max_same_read_signature=10,
    )
    ledger = _ledger(limits=limits, owners=[main])
    results: list[bool] = []

    def worker(i: int) -> bool:
        d = ledger.reserve_one(
            _req(f"thr-{i}", side_effect="compute", arguments_digest=_digest_hex(str(i)))
        )
        return d.allowed

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(worker, i) for i in range(12)]
        for f in as_completed(futs):
            results.append(f.result())

    assert sum(1 for r in results if r) == 4
    assert sum(1 for r in results if not r) == 8
    assert len(_active(ledger)) == 4


def test_compare_and_swap_success_and_conflict() -> None:
    ledger = _ledger()
    assert ledger.reserve_one(_req("cas1")).allowed
    snap = ledger.snapshot()
    # Build next pure state via pure_release
    from app.assistant.policy.budgets import pure_release_unstarted

    next_state, _ = pure_release_unstarted(snap, call_id="cas1")
    assert ledger.compare_and_swap(snap.revision, next_state)
    assert ledger.snapshot().revision == next_state.revision

    # Conflict: stale revision
    assert not ledger.compare_and_swap(snap.revision, next_state)

    # Cannot change limits via CAS
    bad = create_initial_ledger_state(
        limits=_limits(max_provider_rounds=3),
        owner_limits=[_main_owner()],
        started_at_utc=snap.started_at_utc,
        deadline_at_utc=snap.deadline_at_utc,
    )
    # force revision
    from app.assistant.policy.budgets import _rebuild_state

    bad2 = _rebuild_state(
        revision=ledger.snapshot().revision + 1,
        limits=_limits(max_provider_rounds=3),
        owner_limits=ledger.snapshot().owner_limits,
        provider_rounds_started=0,
        main_agent_cycles_started=0,
        capability_calls_started=0,
        completion_followups_started=0,
        prompt_tokens_used=0,
        completion_tokens_used=0,
        owner_calls_started=(),
        global_read_signatures=(),
        owner_read_signatures=(),
        reservations=(),
        denial_count=0,
        started_at_utc=ledger.snapshot().started_at_utc,
        deadline_at_utc=ledger.snapshot().deadline_at_utc,
    )
    with pytest.raises(ValueError, match="RunBudgetLimits"):
        ledger.compare_and_swap(ledger.snapshot().revision, bad2)


def test_safe_internal_events_allowlist_only() -> None:
    events: list[dict[str, Any]] = []
    ledger = _ledger(events=events)
    assert ledger.reserve_one(_req("ev1")).allowed
    assert ledger.mark_started("ev1", _ARGS_EMPTY).allowed
    assert ledger.finish("ev1").allowed
    assert ledger.record_denial(reason_code="owner_side_effect_denied").reason_code
    for event in events:
        assert event.get("_visibility") == "internal"
        # No secret/content fields
        forbidden = {
            "arguments",
            "result",
            "prompt",
            "content",
            "exception",
            "traceback",
            "secret",
            "headers",
            "body",
        }
        assert forbidden.isdisjoint(event.keys())
        # Digests/counts present when applicable
        assert "ledgerRevision" in event
        assert "ledgerDigest" in event
        assert len(str(event["ledgerDigest"])) == 64


# ---------------------------------------------------------------------------
# Serialize / deserialize
# ---------------------------------------------------------------------------


def test_serialize_deserialize_preserves_semantics() -> None:
    clock = _clock()
    events: list[dict[str, Any]] = []
    ledger = _ledger(clock=clock, events=events)
    assert ledger.reserve_one(_req("ser1")).allowed
    assert ledger.mark_started("ser1", _ARGS_EMPTY).allowed
    assert ledger.record_token_usage(prompt_tokens=11, completion_tokens=22).allowed
    skill = _skill_owner(SKILL_VERSION_A)
    assert ledger.add_owner_limits(skill).allowed

    payload = ledger.serialize()
    # JSON round-trip
    text = json.dumps(payload)
    restored_payload = json.loads(text)
    # No runtime objects / secrets
    blob = text.lower()
    assert "secret" not in blob
    assert "password" not in blob
    assert "traceback" not in blob
    assert "exception" not in blob
    # Only digests for arguments — raw args never present
    assert '"arguments"' not in text or "argumentsDigest" in text

    remaining = ledger.remaining_wall_time_ms()
    restored = BudgetLedger.deserialize(
        restored_payload,
        clock=clock,
        remaining_wall_time_ms=remaining,
    )
    s1 = ledger.snapshot()
    s2 = restored.snapshot()
    assert s1.revision == s2.revision
    assert s1.ledger_digest == s2.ledger_digest
    assert s1.capability_calls_started == s2.capability_calls_started
    assert s1.completion_tokens_used == s2.completion_tokens_used
    assert s1.limits == s2.limits
    assert len(s2.owner_limits) == len(s1.owner_limits)
    assert s2.reservations[0].state == "started"

    # Digest mismatch on tamper
    restored_payload = json.loads(text)
    restored_payload["capabilityCallsStarted"] = 99
    with pytest.raises(ValueError, match="ledger_digest"):
        deserialize_ledger_state(restored_payload)


def test_deserialize_state_helper_round_trip() -> None:
    state = create_initial_ledger_state(
        limits=normalize_run_budget_limits(),
        owner_limits=[_main_owner()],
        started_at_utc=datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc),
    )
    payload = serialize_ledger_state(state)
    back = deserialize_ledger_state(payload)
    assert back == state


def test_missing_owner_limits_denies_reserve() -> None:
    limits = normalize_run_budget_limits()
    # Empty owners
    ledger = BudgetLedger.create(limits=limits, owner_limits=(), clock=_clock())
    d = ledger.reserve_one(_req("no-owner"))
    assert not d.allowed
    assert d.reason_code == REASON_OWNER_LIMITS_MISSING


def test_idempotent_finish_and_release() -> None:
    ledger = _ledger()
    assert ledger.reserve_one(_req("id1")).allowed
    assert ledger.mark_started("id1", _ARGS_EMPTY).allowed
    assert ledger.finish("id1").allowed
    rev = ledger.snapshot().revision
    d = ledger.finish("id1")
    assert d.allowed
    assert ledger.snapshot().revision == rev  # no bump

    assert ledger.reserve_one(_req("id2")).allowed
    assert ledger.release_unstarted("id2").allowed
    rev2 = ledger.snapshot().revision
    assert ledger.release_unstarted("id2").allowed
    assert ledger.snapshot().revision == rev2


def test_reservation_digest_stable() -> None:
    from app.assistant.policy.budgets import build_reservation, compute_reservation_digest

    r = build_reservation(
        call_id="x",
        owner_kind="main_agent",
        owner_version_id=PROFILE_VERSION_ID,
        domain_key="skill.search",
        side_effect="read",
        arguments_digest=_ARGS_EMPTY,
        read_signature=compute_read_signature(
            binding_contract_digest=_BINDING_A,
            arguments_digest=_ARGS_EMPTY,
        ),
        state="reserved",
    )
    assert r.reservation_digest == compute_reservation_digest(
        call_id="x",
        owner_kind="main_agent",
        owner_version_id=PROFILE_VERSION_ID,
        domain_key="skill.search",
        side_effect="read",
        arguments_digest=_ARGS_EMPTY,
        read_signature=r.read_signature,
        state="reserved",
    )


def test_no_budget_amplification_after_skill_add_with_usage() -> None:
    limits = _limits(max_total_capability_calls=5, max_parallel_calls=3)
    ledger = _ledger(limits=limits)
    assert ledger.reserve_one(_req("a1", side_effect="compute")).allowed
    assert ledger.mark_started("a1", _ARGS_EMPTY).allowed
    assert ledger.finish("a1").allowed
    used = ledger.snapshot().capability_calls_started
    assert used == 1
    skill = _skill_owner(max_calls=100, limits=limits)  # declared high, capped by run
    assert skill.max_calls == 5  # capped
    assert ledger.add_owner_limits(skill).allowed
    # Run total remaining still 4, not amplified by skill max_calls
    started = 0
    for i in range(10):
        d = ledger.reserve_one(
            _req(
                f"s{i}",
                owner_kind="skill_version",
                owner_version_id=SKILL_VERSION_A,
                side_effect="compute",
                arguments_digest=_digest_hex(f"s{i}"),
            )
        )
        if not d.allowed:
            break
        assert ledger.mark_started(f"s{i}", _digest_hex(f"s{i}")).allowed
        assert ledger.finish(f"s{i}").allowed
        started += 1
    assert started == 4
    assert ledger.snapshot().capability_calls_started == 5


def test_projected_active_reservations_count_toward_totals() -> None:
    limits = _limits(max_total_capability_calls=3, max_parallel_calls=3)
    ledger = _ledger(limits=limits)
    # Consume two started slots, then hold one reserved (not started).
    for i in range(2):
        assert ledger.reserve_one(
            _req(f"done{i}", side_effect="compute", arguments_digest=_digest_hex(f"d{i}"))
        ).allowed
        assert ledger.mark_started(f"done{i}", _digest_hex(f"d{i}")).allowed
        assert ledger.finish(f"done{i}").allowed
    assert ledger.reserve_one(_req("held")).allowed
    # started=2 + active reserved=1 + new=1 exceeds max_total=3 (parallel still free)
    d = ledger.reserve_one(_req("pr3"))
    assert not d.allowed
    assert d.reason_code == REASON_TOTAL_CALLS


def test_started_in_flight_not_double_counted_in_total_capacity() -> None:
    """In-flight started work is already in capability_calls_started; do not re-add it.

    Under the double-count bug, started=2 + active(started)=2 + n=1 would deny total
    even though remaining legal capacity is 1. Parallel still correctly counts started.
    """
    limits = _limits(max_total_capability_calls=3, max_parallel_calls=3)
    ledger = _ledger(limits=limits)
    # Leave N=2 calls in started (unfinished); remaining total capacity is 1.
    for i in range(2):
        assert ledger.reserve_one(
            _req(f"live{i}", side_effect="compute", arguments_digest=_digest_hex(f"l{i}"))
        ).allowed
        assert ledger.mark_started(f"live{i}", _digest_hex(f"l{i}")).allowed
    assert ledger.snapshot().capability_calls_started == 2
    d = ledger.reserve_one(
        _req("more", side_effect="compute", arguments_digest=_digest_hex("more"))
    )
    assert d.allowed, d.reason_code
    assert ledger.snapshot().capability_calls_started == 2  # not consumed until start
    # Fill remaining parallel/total slot is held reserved; next must deny.
    d2 = ledger.reserve_one(
        _req("overflow", side_effect="compute", arguments_digest=_digest_hex("ov"))
    )
    assert not d2.allowed
    assert d2.reason_code in {REASON_TOTAL_CALLS, REASON_PARALLEL}


def test_main_agent_owner_default_capped_by_run() -> None:
    limits = _limits(max_total_capability_calls=4)
    main = _main_owner(limits)
    assert main.max_calls == min(MAIN_AGENT_OWNER_DEFAULT_MAX_CALLS, 4)
    assert main.max_calls == 4

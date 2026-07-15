"""Atomic skill injection + lifecycle tests (Plan 04 Task 6)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
RUN_ID = UUID("00000000-0000-4000-8000-000000000401")
PKG_1 = UUID("00000000-0000-4000-8000-000000000411")
VER_1 = UUID("00000000-0000-4000-8000-000000000421")
PKG_2 = UUID("00000000-0000-4000-8000-000000000412")
VER_2 = UUID("00000000-0000-4000-8000-000000000422")
PROFILE_VERSION = UUID("00000000-0000-4000-8000-000000000431")


def _manifest_with_controls():
    from app.assistant.domain.contracts import (
        ResolvedMainAgentRef,
        ResolvedRunManifestRevision,
        compute_manifest_digest,
    )
    from app.assistant.main_agent.control_capabilities import (
        build_all_main_agent_control_bindings,
        control_capability_refs,
    )

    bindings = build_all_main_agent_control_bindings(
        owner_version_id=PROFILE_VERSION,
        source_snapshot_digest=DIGEST_A,
        app_build_revision="plan04-dev",
    )
    refs = control_capability_refs(bindings)
    main_agent = ResolvedMainAgentRef(
        profile_id=uuid4(),
        version_id=PROFILE_VERSION,
        profile_key="default",
        sequence=1,
        content_digest=DIGEST_A,
    )
    digest = compute_manifest_digest(
        run_id=RUN_ID,
        revision=1,
        parent_digest=None,
        main_agent=main_agent,
        active_skills=(),
        capabilities=refs,
        provider=None,
        model=None,
        provider_aliases=(),
        effective_policy_digest=DIGEST_B,
    )
    manifest = ResolvedRunManifestRevision(
        run_id=RUN_ID,
        revision=1,
        parent_digest=None,
        main_agent=main_agent,
        active_skills=(),
        capabilities=refs,
        provider=None,
        model=None,
        provider_aliases=(),
        effective_policy_digest=DIGEST_B,
        manifest_digest=digest,
    )
    return manifest, bindings


def _skill_ref(**overrides):
    from app.assistant.domain.contracts import ResolvedSkillRef

    payload = dict(
        package_id=PKG_1,
        version_id=VER_1,
        canonical_name="weekly-review",
        sequence=1,
        content_digest=DIGEST_A,
        version_digest=DIGEST_B,
        requested_name_normalized=None,
        resolved_via_alias_id=None,
    )
    payload.update(overrides)
    return ResolvedSkillRef(**payload)


def _cap_ref(key: str, digest: str = DIGEST_C):
    from app.assistant.domain.contracts import ResolvedCapabilityRef

    return ResolvedCapabilityRef(
        capability_type="tool",
        capability_key=key,
        target_identity=f"system-tool:{key}",
        target_id=None,
        target_version_id=None,
        target_revision=None,
        input_schema_digest=DIGEST_A,
        output_schema_digest=DIGEST_B,
        resolution_digest=digest,
        dependency_closure_digest=DIGEST_D,
        binding_contract_digest=digest,
    )


def test_stage_inject_and_lifecycle_accept() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    events: list[dict] = []
    lifecycle.set_event_sink(events.append)

    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=100,
        resource_index_digest=DIGEST_C,
    )
    result, effect, package = stage_skill_injection(
        call_id="inj-1",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
    )
    assert result.status == "completed"
    assert effect is not None
    assert package is not None
    # Gateway success alone does not activate.
    assert lifecycle.is_skill_active(VER_1) is False
    assert events == []

    lifecycle.accept(
        call_id="inj-1",
        current_manifest=manifest,
        proposed_manifest=effect.proposed_manifest,
    )
    assert lifecycle.is_skill_active(VER_1) is True
    assert lifecycle.current_manifest is not None
    assert lifecycle.current_manifest.revision == manifest.revision + 1
    assert any(e.get("eventType") == "skill_activation_end" for e in events)


def test_lineage_reject_discards_package() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
    )
    result, effect, package = stage_skill_injection(
        call_id="inj-2",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
    )
    assert effect is not None
    lifecycle.discard(call_id="inj-2", reason_code="manifest_lineage_error")
    assert lifecycle.is_skill_active(VER_1) is False
    # Accept after discard is a no-op (package gone).
    lifecycle.accept(
        call_id="inj-2",
        current_manifest=manifest,
        proposed_manifest=effect.proposed_manifest,
    )
    assert lifecycle.is_skill_active(VER_1) is False


def test_domain_key_conflict_with_base_control() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("skill.search", digest="1" * 64),),
        instruction_char_count=10,
    )
    result, effect, package = stage_skill_injection(
        call_id="inj-3",
        current_manifest=manifest,
        candidates=[candidate],
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.safe_code == "skill_capability_conflict"
    assert effect is None
    assert package is None


def test_same_batch_domain_key_conflict() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    c1 = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("shared.tool", digest="1" * 64),),
        instruction_char_count=10,
    )
    c2 = SkillActivationCandidate(
        skill=_skill_ref(
            package_id=PKG_2,
            version_id=VER_2,
            canonical_name="other-skill",
            content_digest=DIGEST_C,
            version_digest=DIGEST_D,
        ),
        capabilities=(_cap_ref("shared.tool", digest="2" * 64),),
        instruction_char_count=10,
    )
    result, effect, package = stage_skill_injection(
        call_id="inj-4",
        current_manifest=manifest,
        candidates=[c1, c2],
    )
    assert result.status == "failed"
    assert result.error.safe_code == "skill_capability_conflict"
    assert effect is None


def test_idempotent_reinjection_no_package() -> None:
    from app.assistant.domain.contracts import append_skill_activation
    from app.assistant.main_agent.manifest_runtime import (
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    skill = _skill_ref()
    cap = _cap_ref("get_statistics")
    child = append_skill_activation(manifest, skill=skill, capabilities=(cap,))
    result, effect, package = stage_skill_injection(
        call_id="inj-5",
        current_manifest=child,
        candidates=[SkillActivationCandidate(skill=skill, capabilities=(cap,), instruction_char_count=10)],
    )
    assert result.status == "completed"
    assert result.structured_output["status"] == "noop"
    assert effect is None
    assert package is None


def test_active_limit_exceeded() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    candidates = []
    for i in range(5):
        candidates.append(
            SkillActivationCandidate(
                skill=_skill_ref(
                    package_id=UUID(int=i + 1),
                    version_id=UUID(int=i + 100),
                    canonical_name=f"skill-{i}",
                    content_digest=f"{i}" * 64,
                    version_digest=f"{i}" * 64,
                ),
                capabilities=(_cap_ref(f"tool.{i}", digest=f"{i}" * 64),),
                instruction_char_count=10,
            )
        )
    result, effect, package = stage_skill_injection(
        call_id="inj-6",
        current_manifest=manifest,
        candidates=candidates,
        max_active_skills=4,
    )
    assert result.status == "failed"
    assert result.error.safe_code == "active_skill_limit_exceeded"


def test_event_sink_failure_does_not_rewind_manifest() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)

    def boom(_event):
        raise RuntimeError("sink down")

    lifecycle.set_event_sink(boom)
    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
    )
    _result, effect, _package = stage_skill_injection(
        call_id="inj-7",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
    )
    lifecycle.accept(
        call_id="inj-7",
        current_manifest=manifest,
        proposed_manifest=effect.proposed_manifest,
    )
    assert lifecycle.is_skill_active(VER_1) is True


def test_noop_lifecycle_is_default_on_provider_ports() -> None:
    from app.assistant.provider_loop.contracts import (
        NoOpManifestEffectLifecyclePort,
        ProviderLoopPorts,
    )

    # Construction with only required fields uses no-op lifecycle.
    assert isinstance(
        NoOpManifestEffectLifecyclePort(), NoOpManifestEffectLifecyclePort
    )
    port = NoOpManifestEffectLifecyclePort()
    # no-op must not raise
    port.accept(
        call_id="x",
        current_manifest=_manifest_with_controls()[0],
        proposed_manifest=_manifest_with_controls()[0],
    )
    port.discard(call_id="x", reason_code="test")


def test_staged_package_cannot_authorize_resource_without_accept() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
    )
    from app.assistant.main_agent.resources import is_skill_version_active

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
    )
    _r, effect, _p = stage_skill_injection(
        call_id="inj-8",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
    )
    # Staged only — current manifest membership still empty.
    assert is_skill_version_active(manifest, VER_1) is False
    assert lifecycle.is_skill_active(VER_1) is False


def test_instruction_budget_counts_existing_active_skills() -> None:
    """Existing active skill chars + new candidate must not exceed budget before stage."""
    from app.assistant.main_agent.manifest_runtime import (
        SKILL_CONTEXT_BUDGET_EXCEEDED,
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)

    r1, effect1, package1 = stage_skill_injection(
        call_id="b1",
        current_manifest=manifest,
        candidates=[
            SkillActivationCandidate(
                skill=_skill_ref(
                    package_id=PKG_1,
                    version_id=VER_1,
                    canonical_name="alpha-skill",
                    content_digest=DIGEST_B,
                    version_digest=DIGEST_C,
                ),
                capabilities=(_cap_ref("get_statistics", DIGEST_C),),
                instruction_char_count=20_000,
            )
        ],
        max_active_instruction_chars=24_000,
        lifecycle=lifecycle,
    )
    assert r1.status == "completed"
    assert package1 is not None
    lifecycle.accept(
        call_id="b1",
        current_manifest=manifest,
        proposed_manifest=effect1.proposed_manifest,
    )
    assert lifecycle.accepted_instruction_chars == 20_000
    current = lifecycle.current_manifest
    assert current is not None

    r2, effect2, package2 = stage_skill_injection(
        call_id="b2",
        current_manifest=current,
        candidates=[
            SkillActivationCandidate(
                skill=_skill_ref(
                    package_id=PKG_2,
                    version_id=VER_2,
                    canonical_name="beta-skill",
                    content_digest=DIGEST_D,
                    version_digest=DIGEST_A,
                ),
                capabilities=(_cap_ref("search_entries", DIGEST_D),),
                instruction_char_count=20_000,
            )
        ],
        max_active_instruction_chars=24_000,
        lifecycle=lifecycle,
    )
    assert r2.status == "failed"
    assert r2.error is not None
    assert r2.error.safe_code == SKILL_CONTEXT_BUDGET_EXCEEDED
    assert effect2 is None
    assert package2 is None
    assert lifecycle.is_skill_active(VER_2) is False
    assert lifecycle.is_skill_active(VER_1) is True


def test_batch_multi_skill_inject_is_single_revision_step() -> None:
    """N skills in one inject produce parent.revision+1, not +N."""
    from app.assistant.domain.contracts import validate_manifest_child_link
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)

    candidates = [
        SkillActivationCandidate(
            skill=_skill_ref(
                package_id=PKG_1,
                version_id=VER_1,
                canonical_name="alpha-skill",
                content_digest=DIGEST_B,
                version_digest=DIGEST_C,
            ),
            capabilities=(_cap_ref("get_statistics", DIGEST_C),),
            instruction_char_count=100,
        ),
        SkillActivationCandidate(
            skill=_skill_ref(
                package_id=PKG_2,
                version_id=VER_2,
                canonical_name="beta-skill",
                content_digest=DIGEST_D,
                version_digest=DIGEST_A,
            ),
            capabilities=(_cap_ref("search_entries", DIGEST_D),),
            instruction_char_count=100,
        ),
    ]
    result, effect, package = stage_skill_injection(
        call_id="batch-1",
        current_manifest=manifest,
        candidates=candidates,
        lifecycle=lifecycle,
    )
    assert result.status == "completed"
    assert effect is not None
    assert package is not None
    proposed = effect.proposed_manifest
    assert proposed.revision == manifest.revision + 1
    validate_manifest_child_link(parent=manifest, child=proposed)
    assert {s.canonical_name for s in proposed.active_skills} == {
        "alpha-skill",
        "beta-skill",
    }

    lifecycle.accept(
        call_id="batch-1",
        current_manifest=manifest,
        proposed_manifest=proposed,
    )
    assert lifecycle.is_skill_active(VER_1)
    assert lifecycle.is_skill_active(VER_2)


def test_next_manifest_hook_returns_staged_child() -> None:
    from app.assistant.main_agent.control_runtime import MainAgentControlRuntime
    from app.assistant.main_agent.dispatch_hooks import next_manifest_from_control_effect
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
    )
    from app.assistant.provider_loop.contracts import (
        ProviderDispatchRequest,
        ProviderExecutionScope,
        ProviderToolCall,
    )
    from app.assistant.capabilities.contracts import CapabilityResult, completed_result

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)

    def inject_handler(call_id, validated_input, current):
        del validated_input
        res, effect, _package = stage_skill_injection(
            call_id=call_id,
            current_manifest=current,
            candidates=[
                SkillActivationCandidate(
                    skill=_skill_ref(),
                    capabilities=(_cap_ref("get_statistics"),),
                    instruction_char_count=10,
                )
            ],
            lifecycle=lifecycle,
        )
        return res, effect

    runtime = MainAgentControlRuntime(
        current_manifest=manifest,
        inject_handler=inject_handler,
    )
    result = runtime.execute(
        call_id="hook-1",
        capability_key="skill.inject",
        validated_input={"skills": [{"canonicalName": "weekly-review"}]},
    )
    assert result.status == "completed"
    effect = runtime.peek_manifest_effect(call_id="hook-1")
    assert effect is not None

    # Minimal dispatch request shape for the hook.
    class _Req:
        def __init__(self):
            self.call = type("C", (), {"call_id": "hook-1"})()
            self.current_manifest = manifest

    child = next_manifest_from_control_effect(runtime, _Req(), result)
    assert child.revision == manifest.revision + 1
    assert child.manifest_digest == effect.proposed_manifest.manifest_digest


# ---------------------------------------------------------------------------
# Plan 05 Task 6 — atomic policy-state skill activation
# ---------------------------------------------------------------------------


def test_conflict_rules_excludes_blocks_activation() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        SkillActivationCandidate,
        stage_skill_injection,
    )
    from app.assistant.skills.contracts import SkillConflictRuleV1

    manifest, _ = _manifest_with_controls()
    c1 = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
        conflict_rules=(
            SkillConflictRuleV1(kind="excludes", target_skill="other-skill"),
        ),
    )
    c2 = SkillActivationCandidate(
        skill=_skill_ref(
            package_id=PKG_2,
            version_id=VER_2,
            canonical_name="other-skill",
            content_digest=DIGEST_C,
            version_digest=DIGEST_D,
        ),
        capabilities=(_cap_ref("search_entries", DIGEST_D),),
        instruction_char_count=10,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-excludes",
        current_manifest=manifest,
        candidates=[c1, c2],
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.safe_code == "skill_conflict_excludes"
    assert effect is None
    assert package is None


def test_conflict_rules_requires_missing_target() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        SkillActivationCandidate,
        stage_skill_injection,
    )
    from app.assistant.skills.contracts import SkillConflictRuleV1

    manifest, _ = _manifest_with_controls()
    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
        conflict_rules=(
            SkillConflictRuleV1(kind="requires", target_skill="missing-dep"),
        ),
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-requires",
        current_manifest=manifest,
        candidates=[candidate],
    )
    assert result.status == "failed"
    assert result.error.safe_code in {
        "skill_conflict_requires",
        "skill_conflict_unresolved_target",
    }
    assert effect is None
    assert package is None


def test_exclusive_group_conflict_in_same_batch() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        SkillActivationCandidate,
        stage_skill_injection,
    )
    from app.assistant.skills.contracts import SkillConflictRuleV1

    manifest, _ = _manifest_with_controls()
    rule = SkillConflictRuleV1(kind="exclusive_group", group="review-family")
    c1 = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
        conflict_rules=(rule,),
    )
    c2 = SkillActivationCandidate(
        skill=_skill_ref(
            package_id=PKG_2,
            version_id=VER_2,
            canonical_name="other-skill",
            content_digest=DIGEST_C,
            version_digest=DIGEST_D,
        ),
        capabilities=(_cap_ref("search_entries", DIGEST_D),),
        instruction_char_count=10,
        conflict_rules=(rule,),
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-excl-group",
        current_manifest=manifest,
        candidates=[c1, c2],
    )
    assert result.status == "failed"
    assert result.error.safe_code == "skill_conflict_exclusive_group"
    assert package is None


def test_compatible_same_batch_duplicate_becomes_consumer() -> None:
    """§4.3: identical Domain Key declarations → owner + non-owning consumer."""
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    shared = _cap_ref("shared.tool", digest="1" * 64)
    c1 = SkillActivationCandidate(
        skill=_skill_ref(
            package_id=PKG_1,
            version_id=VER_1,
            canonical_name="alpha-skill",
            content_digest=DIGEST_B,
            version_digest=DIGEST_C,
        ),
        capabilities=(shared,),
        instruction_char_count=10,
        max_skill_calls=4,
        max_same_read_calls=2,
    )
    c2 = SkillActivationCandidate(
        skill=_skill_ref(
            package_id=PKG_2,
            version_id=VER_2,
            canonical_name="beta-skill",
            content_digest=DIGEST_D,
            version_digest=DIGEST_A,
        ),
        capabilities=(shared,),
        instruction_char_count=10,
        max_skill_calls=4,
        max_same_read_calls=2,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-compat-dup",
        current_manifest=manifest,
        candidates=[c1, c2],
        lifecycle=lifecycle,
    )
    assert result.status == "completed"
    assert package is not None
    # Deterministic owner is alpha (lower name); beta is consumer.
    assert package.candidate_compatible_consumers == (("shared.tool", VER_2),)
    # Both skills still activate (Manifest membership).
    assert set(package.activated_version_ids) == {VER_1, VER_2}
    lifecycle.accept(
        call_id="p05-compat-dup",
        current_manifest=manifest,
        proposed_manifest=effect.proposed_manifest,
    )
    assert lifecycle.is_skill_active(VER_1)
    assert lifecycle.is_skill_active(VER_2)
    assert lifecycle.compatible_consumers()["shared.tool"] == frozenset({VER_2})


def test_incompatible_duplicate_fails_before_staging() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        DUPLICATE_CAPABILITY_POLICY_CONFLICT,
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    c1 = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("shared.tool", digest="1" * 64),),
        instruction_char_count=10,
        max_skill_calls=4,
    )
    c2 = SkillActivationCandidate(
        skill=_skill_ref(
            package_id=PKG_2,
            version_id=VER_2,
            canonical_name="other-skill",
            content_digest=DIGEST_C,
            version_digest=DIGEST_D,
        ),
        capabilities=(_cap_ref("shared.tool", digest="2" * 64),),
        instruction_char_count=10,
        max_skill_calls=4,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-incompat-dup",
        current_manifest=manifest,
        candidates=[c1, c2],
        lifecycle=lifecycle,
    )
    assert result.status == "failed"
    assert result.error.safe_code == DUPLICATE_CAPABILITY_POLICY_CONFLICT
    assert effect is None
    assert package is None
    assert lifecycle.peek_package("p05-incompat-dup") is None
    assert lifecycle.is_skill_active(VER_1) is False


def test_owner_budget_limits_commit_only_on_accept() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
        SkillInjectionPolicyContext,
    )
    from app.assistant.policy.budgets import BudgetLedger
    from app.assistant.policy.contracts import RunBudgetLimits

    manifest, _ = _manifest_with_controls()
    run_limits = RunBudgetLimits(
        max_provider_rounds=8,
        max_main_agent_cycles=1,
        max_active_skills=4,
        max_total_capability_calls=16,
        max_parallel_calls=4,
        max_capability_depth=4,
        max_agent_depth=2,
        max_same_read_signature=3,
        max_prompt_tokens=None,
        max_completion_tokens=4096,
        max_wall_time_ms=120_000,
        max_completion_followup_rounds=2,
    )
    ledger = BudgetLedger.create(limits=run_limits)
    before_revision = ledger.snapshot().revision
    before_limits = ledger.snapshot().limits
    before_usage = (
        ledger.snapshot().capability_calls_started,
        ledger.snapshot().provider_rounds_started,
        ledger.snapshot().completion_tokens_used,
    )

    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    lifecycle.bind_policy_ledgers(budget_ledger=ledger)

    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
        max_skill_calls=4,
        max_same_read_calls=2,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-owner-budget",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
        policy=SkillInjectionPolicyContext(
            run_max_total_capability_calls=16,
            run_max_same_read_signature=3,
        ),
    )
    assert result.status == "completed"
    assert package is not None
    assert len(package.candidate_owner_budget_limits) == 1
    # Pre-accept: no owner bucket visible on ledger.
    assert ledger.snapshot().revision == before_revision
    assert not any(
        o.owner_version_id == VER_1 for o in ledger.snapshot().owner_limits
    )
    assert lifecycle.applied_owner_budget_digests() == {}

    lifecycle.accept(
        call_id="p05-owner-budget",
        current_manifest=manifest,
        proposed_manifest=effect.proposed_manifest,
    )
    assert lifecycle.is_skill_active(VER_1)
    assert VER_1 in lifecycle.applied_owner_budget_digests()
    assert any(o.owner_version_id == VER_1 for o in ledger.snapshot().owner_limits)
    # Run limits/usage/provider counters unchanged by activation.
    assert ledger.snapshot().limits == before_limits
    assert (
        ledger.snapshot().capability_calls_started,
        ledger.snapshot().provider_rounds_started,
        ledger.snapshot().completion_tokens_used,
    ) == before_usage


def test_discard_leaves_no_owner_bucket_or_obligation() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
        SkillInjectionPolicyContext,
    )
    from app.assistant.policy.budgets import BudgetLedger
    from app.assistant.policy.contracts import RunBudgetLimits
    from app.assistant.policy.obligations import ObligationLedger

    manifest, _ = _manifest_with_controls()
    run_limits = RunBudgetLimits(
        max_provider_rounds=8,
        max_main_agent_cycles=1,
        max_active_skills=4,
        max_total_capability_calls=16,
        max_parallel_calls=4,
        max_capability_depth=4,
        max_agent_depth=2,
        max_same_read_signature=3,
        max_prompt_tokens=None,
        max_completion_tokens=4096,
        max_wall_time_ms=120_000,
        max_completion_followup_rounds=2,
    )
    budget = BudgetLedger.create(limits=run_limits)
    obligations = ObligationLedger.create(run_id=RUN_ID)
    before_ob_rev = obligations.snapshot().revision

    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    lifecycle.bind_policy_ledgers(budget_ledger=budget, obligation_ledger=obligations)

    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
        max_skill_calls=4,
        requires_terminal_output=True,
        terminal_text_allowed=True,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-discard",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
        policy=SkillInjectionPolicyContext(remaining_provider_slots=4),
    )
    assert result.status == "completed"
    assert package is not None
    assert package.candidate_skill_terminals == ((VER_1, True, PKG_1),)

    lifecycle.discard(call_id="p05-discard", reason_code="manifest_lineage_error")
    assert lifecycle.is_skill_active(VER_1) is False
    assert lifecycle.applied_owner_budget_digests() == {}
    assert lifecycle.applied_skill_terminals() == {}
    assert not any(o.owner_version_id == VER_1 for o in budget.snapshot().owner_limits)
    assert obligations.snapshot().revision == before_ob_rev
    # Accept after discard is a no-op.
    lifecycle.accept(
        call_id="p05-discard",
        current_manifest=manifest,
        proposed_manifest=effect.proposed_manifest,
    )
    assert lifecycle.is_skill_active(VER_1) is False


def test_skill_terminal_commits_on_accept() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
        SkillInjectionPolicyContext,
    )
    from app.assistant.policy.obligations import ObligationLedger

    manifest, _ = _manifest_with_controls()
    obligations = ObligationLedger.create(run_id=RUN_ID)
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    lifecycle.bind_policy_ledgers(obligation_ledger=obligations)

    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
        max_skill_calls=4,
        requires_terminal_output=True,
        terminal_text_allowed=True,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-terminal",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
        policy=SkillInjectionPolicyContext(remaining_provider_slots=4),
    )
    assert result.status == "completed"
    assert package.candidate_skill_terminals == ((VER_1, True, PKG_1),)
    # Not yet on ledger.
    assert not any(
        o.owner_kind == "skill_version" and o.owner_version_id == VER_1
        for o in obligations.snapshot().obligations
    )
    lifecycle.accept(
        call_id="p05-terminal",
        current_manifest=manifest,
        proposed_manifest=effect.proposed_manifest,
    )
    assert lifecycle.applied_skill_terminals()[VER_1] is True
    assert any(
        o.owner_kind == "skill_version"
        and o.owner_version_id == VER_1
        and o.status == "pending"
        for o in obligations.snapshot().obligations
    )


def test_unsatisfiable_terminal_fails_before_stage() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        SKILL_COMPLETION_UNSATISFIABLE,
        SkillActivationCandidate,
        stage_skill_injection,
        SkillInjectionPolicyContext,
    )

    manifest, _ = _manifest_with_controls()
    # Instruction-only + text forbidden + requires terminal → unsatisfiable.
    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(),
        instruction_char_count=10,
        is_instruction_only=True,
        requires_terminal_output=True,
        terminal_text_allowed=False,
        max_skill_calls=0,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-unsat",
        current_manifest=manifest,
        candidates=[candidate],
        policy=SkillInjectionPolicyContext(remaining_provider_slots=0),
    )
    assert result.status == "failed"
    assert result.error.safe_code == SKILL_COMPLETION_UNSATISFIABLE
    assert effect is None
    assert package is None


def test_effective_policy_digest_changes_on_activation() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
        SkillInjectionPolicyContext,
    )

    manifest, _ = _manifest_with_controls()
    parent_policy = manifest.effective_policy_digest
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
        max_skill_calls=4,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-policy-digest",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
        policy=SkillInjectionPolicyContext(),
    )
    assert result.status == "completed"
    assert effect is not None
    assert package is not None
    proposed = effect.proposed_manifest
    assert proposed.effective_policy_digest != parent_policy
    assert package.candidate_effective_policy_digest == proposed.effective_policy_digest
    lifecycle.accept(
        call_id="p05-policy-digest",
        current_manifest=manifest,
        proposed_manifest=proposed,
    )
    assert lifecycle.current_manifest.effective_policy_digest == proposed.effective_policy_digest


def test_staged_package_not_visible_on_ledgers_until_accept() -> None:
    """Zero candidate-state visibility before accept (hard rule 10)."""
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
        SkillInjectionPolicyContext,
    )
    from app.assistant.policy.budgets import BudgetLedger
    from app.assistant.policy.contracts import RunBudgetLimits
    from app.assistant.policy.obligations import ObligationLedger

    manifest, _ = _manifest_with_controls()
    run_limits = RunBudgetLimits(
        max_provider_rounds=8,
        max_main_agent_cycles=1,
        max_active_skills=4,
        max_total_capability_calls=16,
        max_parallel_calls=4,
        max_capability_depth=4,
        max_agent_depth=2,
        max_same_read_signature=3,
        max_prompt_tokens=None,
        max_completion_tokens=4096,
        max_wall_time_ms=120_000,
        max_completion_followup_rounds=2,
    )
    budget = BudgetLedger.create(limits=run_limits)
    obligations = ObligationLedger.create(run_id=RUN_ID)
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    lifecycle.bind_policy_ledgers(budget_ledger=budget, obligation_ledger=obligations)

    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
        max_skill_calls=4,
        requires_terminal_output=True,
        terminal_text_allowed=True,
    )
    _r, effect, package = stage_skill_injection(
        call_id="p05-visibility",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
        policy=SkillInjectionPolicyContext(remaining_provider_slots=4),
    )
    assert package is not None
    # Staged only.
    assert lifecycle.is_skill_active(VER_1) is False
    assert lifecycle.applied_owner_budget_digests() == {}
    assert lifecycle.applied_skill_terminals() == {}
    assert not any(o.owner_version_id == VER_1 for o in budget.snapshot().owner_limits)
    assert not any(
        o.owner_version_id == VER_1 for o in obligations.snapshot().obligations
    )
    # Current manifest pointer unchanged.
    assert lifecycle.current_manifest.manifest_digest == manifest.manifest_digest


def test_wrong_effect_digest_discards_package() -> None:
    from app.assistant.domain.contracts import append_skill_activation
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-bad-digest",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
    )
    assert effect is not None
    # Tamper: pass a different proposed manifest.
    other = append_skill_activation(
        manifest,
        skill=_skill_ref(
            package_id=PKG_2,
            version_id=VER_2,
            canonical_name="other-skill",
            content_digest=DIGEST_C,
            version_digest=DIGEST_D,
        ),
        capabilities=(_cap_ref("search_entries", DIGEST_D),),
    )
    with pytest.raises(ValueError, match="effect_digest mismatch|proposed_manifest mismatch"):
        lifecycle.accept(
            call_id="p05-bad-digest",
            current_manifest=manifest,
            proposed_manifest=other,
        )
    assert lifecycle.is_skill_active(VER_1) is False
    assert lifecycle.peek_package("p05-bad-digest") is None


def test_reinjection_is_noop_across_policy_state() -> None:
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
        SkillInjectionPolicyContext,
    )
    from app.assistant.policy.budgets import BudgetLedger
    from app.assistant.policy.contracts import RunBudgetLimits

    manifest, _ = _manifest_with_controls()
    run_limits = RunBudgetLimits(
        max_provider_rounds=8,
        max_main_agent_cycles=1,
        max_active_skills=4,
        max_total_capability_calls=16,
        max_parallel_calls=4,
        max_capability_depth=4,
        max_agent_depth=2,
        max_same_read_signature=3,
        max_prompt_tokens=None,
        max_completion_tokens=4096,
        max_wall_time_ms=120_000,
        max_completion_followup_rounds=2,
    )
    budget = BudgetLedger.create(limits=run_limits)
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    lifecycle.bind_policy_ledgers(budget_ledger=budget)

    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
        max_skill_calls=4,
    )
    r1, e1, p1 = stage_skill_injection(
        call_id="p05-re-1",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
        policy=SkillInjectionPolicyContext(),
    )
    assert r1.status == "completed"
    lifecycle.accept(
        call_id="p05-re-1",
        current_manifest=manifest,
        proposed_manifest=e1.proposed_manifest,
    )
    current = lifecycle.current_manifest
    owners_after = list(budget.snapshot().owner_limits)
    rev_after = budget.snapshot().revision
    policy_after = current.effective_policy_digest

    r2, e2, p2 = stage_skill_injection(
        call_id="p05-re-2",
        current_manifest=current,
        candidates=[candidate],
        lifecycle=lifecycle,
        policy=SkillInjectionPolicyContext(),
    )
    assert r2.status == "completed"
    assert r2.structured_output["status"] == "noop"
    assert e2 is None
    assert p2 is None
    assert lifecycle.current_manifest.manifest_digest == current.manifest_digest
    assert lifecycle.current_manifest.effective_policy_digest == policy_after
    assert list(budget.snapshot().owner_limits) == owners_after
    assert budget.snapshot().revision == rev_after


def test_duplicate_max_skill_calls_mismatch_fails_pre_stage() -> None:
    """Identical capability refs with max_skill_calls 4 vs 8 → pre-stage fail."""
    from app.assistant.main_agent.manifest_runtime import (
        DUPLICATE_CAPABILITY_POLICY_CONFLICT,
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    shared = _cap_ref("shared.tool", digest="1" * 64)
    c1 = SkillActivationCandidate(
        skill=_skill_ref(
            package_id=PKG_1,
            version_id=VER_1,
            canonical_name="alpha-skill",
            content_digest=DIGEST_B,
            version_digest=DIGEST_C,
        ),
        capabilities=(shared,),
        instruction_char_count=10,
        max_skill_calls=4,
        max_same_read_calls=2,
    )
    c2 = SkillActivationCandidate(
        skill=_skill_ref(
            package_id=PKG_2,
            version_id=VER_2,
            canonical_name="beta-skill",
            content_digest=DIGEST_D,
            version_digest=DIGEST_A,
        ),
        capabilities=(shared,),
        instruction_char_count=10,
        max_skill_calls=8,  # policy mismatch vs owner
        max_same_read_calls=2,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-max-calls-mismatch",
        current_manifest=manifest,
        candidates=[c1, c2],
        lifecycle=lifecycle,
    )
    assert result.status == "failed"
    assert result.error.safe_code == DUPLICATE_CAPABILITY_POLICY_CONFLICT
    assert effect is None
    assert package is None
    assert lifecycle.peek_package("p05-max-calls-mismatch") is None
    assert lifecycle.is_skill_active(VER_1) is False
    assert lifecycle.is_skill_active(VER_2) is False


def test_duplicate_terminal_policy_mismatch_fails_pre_stage() -> None:
    """Same-batch path must consult requires_terminal_output / terminal_text_allowed."""
    from app.assistant.main_agent.manifest_runtime import (
        DUPLICATE_CAPABILITY_POLICY_CONFLICT,
        SkillActivationCandidate,
        stage_skill_injection,
    )

    manifest, _ = _manifest_with_controls()
    shared = _cap_ref("shared.tool", digest="1" * 64)
    c1 = SkillActivationCandidate(
        skill=_skill_ref(
            package_id=PKG_1,
            version_id=VER_1,
            canonical_name="alpha-skill",
            content_digest=DIGEST_B,
            version_digest=DIGEST_C,
        ),
        capabilities=(shared,),
        instruction_char_count=10,
        max_skill_calls=4,
        max_same_read_calls=2,
        requires_terminal_output=True,
        terminal_text_allowed=True,
    )
    c2 = SkillActivationCandidate(
        skill=_skill_ref(
            package_id=PKG_2,
            version_id=VER_2,
            canonical_name="beta-skill",
            content_digest=DIGEST_D,
            version_digest=DIGEST_A,
        ),
        capabilities=(shared,),
        instruction_char_count=10,
        max_skill_calls=4,
        max_same_read_calls=2,
        requires_terminal_output=False,  # mismatch
        terminal_text_allowed=True,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-terminal-policy-mismatch",
        current_manifest=manifest,
        candidates=[c1, c2],
    )
    assert result.status == "failed"
    assert result.error.safe_code == DUPLICATE_CAPABILITY_POLICY_CONFLICT
    assert effect is None
    assert package is None


def test_accept_validates_package_digest() -> None:
    """Accept recomputes package_digest and rejects tampered packages."""
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
        SkillInjectionPolicyContext,
    )

    manifest, _ = _manifest_with_controls()
    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
        max_skill_calls=4,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-pkg-digest",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
        policy=SkillInjectionPolicyContext(),
    )
    assert result.status == "completed"
    assert package is not None
    assert package.package_digest is not None
    # Tamper staged package digest.
    package.package_digest = "0" * 64
    with pytest.raises(ValueError, match="package_digest"):
        lifecycle.accept(
            call_id="p05-pkg-digest",
            current_manifest=manifest,
            proposed_manifest=effect.proposed_manifest,
        )
    # Fail-closed: package discarded, Manifest unchanged.
    assert lifecycle.peek_package("p05-pkg-digest") is None
    assert lifecycle.is_skill_active(VER_1) is False
    assert lifecycle.current_manifest.manifest_digest == manifest.manifest_digest


def test_accept_owner_limit_denial_is_fail_closed() -> None:
    """Owner-limit denial before Manifest advance: no partial commit."""
    from app.assistant.main_agent.manifest_runtime import (
        MainAgentManifestEffectLifecycle,
        SkillActivationCandidate,
        stage_skill_injection,
        SkillInjectionPolicyContext,
    )
    from app.assistant.policy.budgets import BudgetLedger
    from app.assistant.policy.contracts import RunBudgetLimits, build_owner_budget_limits

    manifest, _ = _manifest_with_controls()
    # max_active_skills=1 so a second owner bucket is denied at accept.
    run_limits = RunBudgetLimits(
        max_provider_rounds=8,
        max_main_agent_cycles=1,
        max_active_skills=1,
        max_total_capability_calls=16,
        max_parallel_calls=4,
        max_capability_depth=4,
        max_agent_depth=2,
        max_same_read_signature=3,
        max_prompt_tokens=None,
        max_completion_tokens=4096,
        max_wall_time_ms=120_000,
        max_completion_followup_rounds=2,
    )
    ledger = BudgetLedger.create(limits=run_limits)
    # Pre-seed one skill owner so the candidate's add_owner_limits hits the cap.
    seeded = build_owner_budget_limits(
        owner_kind="skill_version",
        owner_version_id=VER_2,
        max_calls=2,
        max_same_read_signature=1,
    )
    decision = ledger.add_owner_limits(seeded)
    assert decision.allowed is True
    before_revision = ledger.snapshot().revision
    before_owners = list(ledger.snapshot().owner_limits)

    lifecycle = MainAgentManifestEffectLifecycle()
    lifecycle.bind_current_manifest(manifest)
    lifecycle.bind_policy_ledgers(budget_ledger=ledger)

    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(_cap_ref("get_statistics"),),
        instruction_char_count=10,
        max_skill_calls=4,
        max_same_read_calls=2,
    )
    # Stage with a policy that still allows staging (stage uses its own Run caps).
    result, effect, package = stage_skill_injection(
        call_id="p05-owner-deny",
        current_manifest=manifest,
        candidates=[candidate],
        lifecycle=lifecycle,
        policy=SkillInjectionPolicyContext(
            run_max_total_capability_calls=16,
            run_max_same_read_signature=3,
            run_max_active_skills=4,  # staging budget map allows it
        ),
    )
    assert result.status == "completed"
    assert package is not None

    with pytest.raises(ValueError, match="owner_limits_denied"):
        lifecycle.accept(
            call_id="p05-owner-deny",
            current_manifest=manifest,
            proposed_manifest=effect.proposed_manifest,
        )
    # Fail-closed: Manifest not advanced; ledger rewound to pre-accept.
    assert lifecycle.is_skill_active(VER_1) is False
    assert lifecycle.current_manifest.manifest_digest == manifest.manifest_digest
    assert lifecycle.applied_owner_budget_digests() == {}
    assert ledger.snapshot().revision == before_revision
    assert list(ledger.snapshot().owner_limits) == before_owners
    assert lifecycle.peek_package("p05-owner-deny") is None


def test_stage_package_carries_frozen_bindings_and_package_map() -> None:
    """Accept rebind payload is populated from SkillActivationCandidate fields."""
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        FrozenCapabilityBinding,
        project_frozen_capability_binding,
    )
    from app.assistant.domain.contracts import (
        CapabilityCompletionContract,
        ResolvedCapabilityBinding,
    )
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant.main_agent.manifest_runtime import (
        SkillActivationCandidate,
        stage_skill_injection,
    )
    from app.assistant.skills.resolution import build_binding_snapshot

    manifest, _ = _manifest_with_controls()
    in_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    out_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    completion = CapabilityCompletionContract(terminal_output=False, needs_followup=True)
    snapshot, closure, contract = build_binding_snapshot(
        capability_type="tool",
        target_identity="system-tool:get_statistics",
        target_id=None,
        target_version_id=None,
        target_revision=None,
        input_schema=in_schema,
        output_schema=out_schema,
        completion=completion,
        config_digest=None,
        executable_revision="plan05-dev",
        resolution_digest=DIGEST_C,
        dependencies=(),
    )
    resolved = ResolvedCapabilityBinding(
        capability_type="tool",
        capability_key="get_statistics",
        target_identity="system-tool:get_statistics",
        target_id=None,
        target_version_id=None,
        resolved_tool_id=None,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=None,
        resolved_revision=None,
        input_schema=in_schema,
        output_schema=out_schema,
        input_schema_digest=binding_schema_digest(in_schema),
        output_schema_digest=binding_schema_digest(out_schema),
        completion=completion,
        config_digest=None,
        executable_revision="plan05-dev",
        resolution_digest=DIGEST_C,
        resolution_snapshot=snapshot,
        dependencies=(),
        dependency_closure_digest=closure,
        binding_contract_digest=contract,
    )
    frozen = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="skill_version",
            binding_row_id=None,
            owner_version_id=VER_1,
            source_snapshot_digest=DIGEST_A,
        ),
    )
    candidate = SkillActivationCandidate(
        skill=_skill_ref(),
        capabilities=(frozen.ref,),
        frozen_bindings=(frozen,),
        instruction_char_count=10,
        max_skill_calls=4,
    )
    result, effect, package = stage_skill_injection(
        call_id="p05-bindings",
        current_manifest=manifest,
        candidates=[candidate],
    )
    assert result.status == "completed"
    assert package is not None
    assert VER_1 in package.candidate_frozen_bindings_by_version
    assert package.candidate_frozen_bindings_by_version[VER_1][0].ref.capability_key == (
        "get_statistics"
    )
    assert package.candidate_skill_package_id_by_version[VER_1] == PKG_1
    assert package.candidate_skill_content_digest_by_version[VER_1] == DIGEST_A


def test_apply_accept_package_rebind_registers_tools_and_owners() -> None:
    """Package-aware accept hook updates tools provider + owner resolver."""
    from types import SimpleNamespace
    from uuid import uuid4

    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.domain.contracts import (
        CapabilityCompletionContract,
        ResolvedCapabilityBinding,
    )
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant.main_agent.inject_wiring import apply_accept_package_rebind
    from app.assistant.main_agent.manifest_runtime import PendingSkillActivationPackage
    from app.assistant.main_agent.policy_runtime import MainAgentGatewayToolsProvider
    from app.assistant.policy.evaluator import OwnerGrantMaterial
    from app.assistant.policy.runtime import DomainKeyOwnerResolver
    from app.assistant.skills.resolution import build_binding_snapshot

    manifest, _ = _manifest_with_controls()
    in_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    out_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    completion = CapabilityCompletionContract()
    snapshot, closure, contract = build_binding_snapshot(
        capability_type="tool",
        target_identity="system-tool:get_statistics",
        target_id=None,
        target_version_id=None,
        target_revision=None,
        input_schema=in_schema,
        output_schema=out_schema,
        completion=completion,
        config_digest=None,
        executable_revision="plan05-dev",
        resolution_digest=DIGEST_C,
        dependencies=(),
    )
    resolved = ResolvedCapabilityBinding(
        capability_type="tool",
        capability_key="get_statistics",
        target_identity="system-tool:get_statistics",
        target_id=None,
        target_version_id=None,
        resolved_tool_id=None,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=None,
        resolved_revision=None,
        input_schema=in_schema,
        output_schema=out_schema,
        input_schema_digest=binding_schema_digest(in_schema),
        output_schema_digest=binding_schema_digest(out_schema),
        completion=completion,
        config_digest=None,
        executable_revision="plan05-dev",
        resolution_digest=DIGEST_C,
        resolution_snapshot=snapshot,
        dependencies=(),
        dependency_closure_digest=closure,
        binding_contract_digest=contract,
    )
    frozen = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="skill_version",
            binding_row_id=None,
            owner_version_id=VER_1,
            source_snapshot_digest=DIGEST_A,
        ),
    )
    material = OwnerGrantMaterial(
        owner_kind="skill_version",
        owner_id=str(PKG_1),
        owner_version_id=VER_1,
        policy_digest=DIGEST_B,
        author_allowed_side_effects=("read", "compute"),
        declared_capability_keys=frozenset({"get_statistics"}),
        is_instruction_only=False,
    )
    package = PendingSkillActivationPackage(
        call_id="rebind-1",
        effect=SimpleNamespace(),  # type: ignore[arg-type]
        activated_version_ids=(VER_1,),
        noop_version_ids=(),
        candidate_frozen_bindings_by_version={VER_1: (frozen,)},
        candidate_skill_package_id_by_version={VER_1: PKG_1},
        candidate_skill_content_digest_by_version={VER_1: DIGEST_A},
        candidate_owner_materials=(material,),
    )

    class _Auth:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.skill_package_id_by_version: dict = {}
            self.skill_content_digest_by_version: dict = {}
            self.owner_materials: dict = {}
            self.policy_snapshot = None
            self.manifest = manifest

        def rebind_manifest(self, m, **kwargs):
            self.calls.append({"manifest": m, **kwargs})
            if kwargs.get("skill_package_id_by_version"):
                self.skill_package_id_by_version.update(
                    kwargs["skill_package_id_by_version"]
                )

    auth = _Auth()
    owners: dict = {}
    resolver = DomainKeyOwnerResolver(
        owners_by_domain_key=owners,
        default_owner_kind="main_agent",
        default_owner_version_id=uuid4(),
    )

    class _Runtime:
        def __init__(self) -> None:
            self.authorization_factory = auth
            self.owner_materials: dict = {}
            self.owners_by_domain_key = owners
            self._owner_resolver = resolver
            self.manifest = manifest
            self.policy_snapshot = None
            self.lifecycle = None

        def rebind_owners(self, mapping):
            self.owners_by_domain_key = dict(mapping)
            self._owner_resolver.rebind(mapping)

        def rebind_policy_snapshot(self, snap):
            self.policy_snapshot = snap

    runtime = _Runtime()
    tools = MainAgentGatewayToolsProvider(
        session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        control_bindings=(),
        control_port=SimpleNamespace(),  # type: ignore[arg-type]
        authorization_factory=auth,  # type: ignore[arg-type]
    )
    apply_accept_package_rebind(
        runtime=runtime,
        tools_provider=tools,
        ports_owner_resolver=resolver,
        manifest=manifest,
        package=package,
    )
    assert VER_1 in tools.active_bindings_by_version
    assert tools.active_bindings_by_version[VER_1][0].ref.capability_key == "get_statistics"
    assert auth.skill_package_id_by_version[VER_1] == PKG_1
    kind, vid = resolver.resolve_owner(
        call=SimpleNamespace(domain_key="get_statistics"),
        descriptor=SimpleNamespace(),
    )
    assert kind == "skill_version"
    assert vid == VER_1
    assert (
        "skill_version",
        str(PKG_1),
        VER_1,
    ) in runtime.owner_materials

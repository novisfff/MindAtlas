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

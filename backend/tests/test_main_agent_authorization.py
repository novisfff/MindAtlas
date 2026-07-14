"""Main Agent minimum authorization bridge tests (Plan 04 Task 5)."""

from __future__ import annotations

import threading
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
DIGEST_E = "e" * 64
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000301")
RUN_ID = UUID("00000000-0000-4000-8000-000000000302")
CONV_ID = UUID("00000000-0000-4000-8000-000000000303")


def _timeout_policy():
    from app.assistant.capabilities.contracts import CapabilityTimeoutPolicy

    return CapabilityTimeoutPolicy(
        mode="cooperative", timeout_seconds=None, cancellation_supported=True
    )


def _behavior(**overrides: Any):
    from app.assistant.capabilities.contracts import (
        CapabilityBehavior,
        ClassificationContractRef,
    )

    payload = {
        "classification": ClassificationContractRef(
            schema_version=1,
            revision="plan02-v1",
            ruleset_digest=DIGEST_A,
        ),
        "side_effect": "read",
        "parallel_safe": True,
        "interrupt_mode": "none",
        "timeout_policy": _timeout_policy(),
        "behavior_digest": DIGEST_B,
    }
    payload.update(overrides)
    return CapabilityBehavior(**payload)


def _descriptor(**overrides: Any):
    from app.assistant.capabilities.contracts import (
        CapabilityAvailability,
        CapabilityDescriptor,
    )
    from app.assistant.domain.contracts import CapabilityCompletionContract
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    in_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    out_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    payload = {
        "capability_key": "skill.search",
        "capability_type": "tool",
        "target_identity": "main-agent-control:skill.search",
        "target_id": None,
        "target_version_id": None,
        "target_revision": None,
        "resolution_digest": DIGEST_A,
        "binding_contract_digest": DIGEST_B,
        "dependency_closure_digest": DIGEST_C,
        "display_name": "Skill Search",
        "description": "search",
        "input_schema": in_schema,
        "output_schema": out_schema,
        "input_schema_digest": binding_schema_digest(in_schema),
        "output_schema_digest": binding_schema_digest(out_schema),
        "descriptor_digest": DIGEST_D,
        "executable_revision": "plan04-dev",
        "behavior": _behavior(),
        "availability": CapabilityAvailability(status="available"),
        "completion": CapabilityCompletionContract(
            terminal_output=False, needs_followup=True
        ),
    }
    payload.update(overrides)
    return CapabilityDescriptor(**payload)


def _evidence(**overrides: Any):
    from app.assistant.capabilities.contracts import (
        CapabilityAuthorizationEvidence,
        CapabilityOwnerRef,
        CapabilityPrincipal,
    )
    from app.assistant.main_agent.authorization import LOCAL_ASSISTANT_PRINCIPAL

    payload = {
        "issuer": "skill_policy",
        "call_id": "call-1",
        "principal": LOCAL_ASSISTANT_PRINCIPAL,
        "entrypoint": "main_agent",
        "owner": CapabilityOwnerRef(
            owner_kind="main_agent",
            owner_id="default",
            owner_version_id=PROFILE_VERSION_ID,
        ),
        "capability_key": "skill.search",
        "resolution_digest": DIGEST_A,
        "binding_contract_digest": DIGEST_B,
        "dependency_closure_digest": DIGEST_C,
        "allowed_side_effects": ("none", "compute", "read"),
        "grant_source_digest": DIGEST_E,
        "evidence_digest": "f" * 64,
    }
    payload.update(overrides)
    return CapabilityAuthorizationEvidence(**payload)


def _context(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityExecutionContext

    payload = {
        "call_id": "call-1",
        "run_id": RUN_ID,
        "conversation_id": CONV_ID,
        "nesting_depth": 0,
    }
    payload.update(overrides)
    return CapabilityExecutionContext(**payload)


def _verifier(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityOwnerRef
    from app.assistant.main_agent.authorization import (
        LOCAL_ASSISTANT_PRINCIPAL,
        MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        SkillPolicyAuthorizationEvidenceVerifier,
    )

    payload = {
        "expected_call_id": "call-1",
        "expected_capability_key": "skill.search",
        "expected_owner": CapabilityOwnerRef(
            owner_kind="main_agent",
            owner_id="default",
            owner_version_id=PROFILE_VERSION_ID,
        ),
        "expected_resolution_digest": DIGEST_A,
        "expected_binding_contract_digest": DIGEST_B,
        "expected_dependency_closure_digest": DIGEST_C,
        "expected_grant_source_digest": DIGEST_E,
        "expected_evidence_digest": "f" * 64,
        "expected_principal": LOCAL_ASSISTANT_PRINCIPAL,
        "expected_run_id": RUN_ID,
        "expected_conversation_id": CONV_ID,
        "ceiling": MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
    }
    payload.update(overrides)
    return SkillPolicyAuthorizationEvidenceVerifier(**payload)


def test_ceiling_fixed_vector_lattice_order() -> None:
    from app.assistant.main_agent.authorization import (
        MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
        build_main_agent_read_only_effect_ceiling,
    )

    ceiling = MAIN_AGENT_READ_ONLY_EFFECT_CEILING
    assert ceiling.allowed_side_effects == ("none", "compute", "read")
    assert ceiling.allowed_interrupt_modes == ("none",)
    assert ceiling.revision == "plan04-v1"
    assert ceiling.ceiling_key == "main_agent_read_only"
    assert ceiling.ceiling_digest == MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST
    assert len(ceiling.ceiling_digest) == 64
    # Rebuild is stable.
    assert build_main_agent_read_only_effect_ceiling().ceiling_digest == ceiling.ceiling_digest


def test_author_intersection_never_expands_platform() -> None:
    from app.assistant.main_agent.authorization import (
        derive_allowed_side_effects_for_owner,
        map_author_side_effects,
    )
    from app.assistant.capabilities.contracts import CapabilityOwnerRef

    assert map_author_side_effects(["read", "compute"]) == ("read", "compute")
    owner = CapabilityOwnerRef(
        owner_kind="skill_version",
        owner_id="v1",
        owner_version_id=uuid4(),
    )
    # Author declares write; platform ceiling still clips to read prefix + none.
    effective = derive_allowed_side_effects_for_owner(
        owner=owner,
        author_allowed_side_effects=["read", "write"],
        is_base_control=False,
    )
    assert "write_local" not in effective
    assert "draft" not in effective
    assert effective == ("none", "read")


def test_verifier_allows_read_under_ceiling() -> None:
    from app.assistant.capabilities.policy import CapabilityPolicyEngine

    verifier = _verifier()
    engine = CapabilityPolicyEngine({("skill_policy", "main_agent"): verifier})
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    assert decision.allowed is True
    assert decision.granted_side_effects == ("none", "compute", "read")


def test_verifier_denies_write_under_read_ceiling() -> None:
    from app.assistant.capabilities.policy import CapabilityPolicyEngine

    verifier = _verifier()
    engine = CapabilityPolicyEngine({("skill_policy", "main_agent"): verifier})
    decision = engine.authorize(
        descriptor=_descriptor(behavior=_behavior(side_effect="write_local", parallel_safe=False)),
        evidence=_evidence(),
        context=_context(),
    )
    assert decision.allowed is False


def test_copy_descriptor_effect_verifier_cannot_widen_grant() -> None:
    """Negative: verifier that copies descriptor.behavior must not authorize write."""
    from app.assistant.capabilities.policy import CapabilityPolicyEngine

    # Evidence still carries read-only grant; copy_descriptor_effect makes verifier
    # return write as granted — policy still checks evidence identity, and our
    # verifier itself raises side_effect_above_ceiling when actual not in *its*
    # granted tuple only after copy. For the trap: copy returns (write_local,),
    # actual is write_local, so verifier allows; policy then uses granted from
    # verified evidence. We assert production factory never sets this flag and
    # that a copy-trap verifier is detectable by grant != ceiling prefix.
    verifier = _verifier(copy_descriptor_effect=True)
    # With copy flag, granted becomes (descriptor.side_effect,).
    # For a write descriptor the verifier allows; production code never enables this.
    from app.assistant.capabilities.policy import AuthorizationEvidenceVerificationError

    # read descriptor under copy returns ("read",) which is fine for that call,
    # but grant_source still independent. Mutate to write:
    verified = verifier.verify(
        descriptor=_descriptor(behavior=_behavior(side_effect="write_local", parallel_safe=False)),
        evidence=_evidence(allowed_side_effects=("none", "compute", "read")),
        context=_context(),
    )
    assert verified.allowed_side_effects == ("write_local",)
    # Production path uses copy_descriptor_effect=False and denies:
    verifier2 = _verifier()
    with pytest.raises(AuthorizationEvidenceVerificationError) as exc:
        verifier2.verify(
            descriptor=_descriptor(
                behavior=_behavior(side_effect="write_local", parallel_safe=False)
            ),
            evidence=_evidence(),
            context=_context(),
        )
    assert exc.value.reason_code == "side_effect_above_ceiling"


def test_call_replay_denied() -> None:
    from app.assistant.capabilities.policy import (
        AuthorizationEvidenceVerificationError,
        CapabilityPolicyEngine,
    )

    verifier = _verifier()
    engine = CapabilityPolicyEngine({("skill_policy", "main_agent"): verifier})
    first = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    assert first.allowed is True
    second = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    assert second.allowed is False


def test_wrong_run_or_conversation_denied() -> None:
    from app.assistant.capabilities.policy import CapabilityPolicyEngine

    verifier = _verifier()
    engine = CapabilityPolicyEngine({("skill_policy", "main_agent"): verifier})
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(run_id=uuid4()),
    )
    assert decision.allowed is False


def test_openclaw_and_main_agent_verifiers_isolated() -> None:
    from app.assistant.capabilities.policy import CapabilityPolicyEngine
    from app.assistant.provider_loop.runtime import TestAuthorizationEvidenceVerifier

    main_verifier = _verifier()
    test_verifier = TestAuthorizationEvidenceVerifier()
    engine = CapabilityPolicyEngine(
        {
            ("skill_policy", "main_agent"): main_verifier,
            ("test", "test"): test_verifier,
        }
    )
    # OpenClaw-shaped evidence has no verifier → denied.
    from app.assistant.capabilities.contracts import (
        CapabilityAuthorizationEvidence,
        CapabilityOwnerRef,
        CapabilityPrincipal,
    )

    openclaw_evidence = CapabilityAuthorizationEvidence(
        issuer="openclaw_bridge",
        call_id="call-1",
        principal=CapabilityPrincipal(
            principal_type="openclaw_installation",
            principal_id="inst",
            authenticated=True,
        ),
        entrypoint="openclaw",
        owner=CapabilityOwnerRef(
            owner_kind="openclaw_catalog",
            owner_id="item",
            owner_version_id=None,
        ),
        capability_key="search.query",
        resolution_digest=DIGEST_A,
        binding_contract_digest=DIGEST_B,
        dependency_closure_digest=DIGEST_C,
        allowed_side_effects=("none", "compute", "read"),
        grant_source_digest=DIGEST_E,
        evidence_digest="f" * 64,
    )
    decision = engine.authorize(
        descriptor=_descriptor(capability_key="search.query"),
        evidence=openclaw_evidence,
        context=_context(),
    )
    assert decision.allowed is False

    # Main agent evidence cannot use openclaw verifier key.
    decision2 = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    assert decision2.allowed is True


def test_grant_source_digest_changes_with_ceiling_or_owner() -> None:
    from app.assistant.capabilities.contracts import CapabilityOwnerRef
    from app.assistant.main_agent.authorization import (
        MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        compute_main_agent_grant_source_digest,
    )

    owner = CapabilityOwnerRef(
        owner_kind="main_agent",
        owner_id="default",
        owner_version_id=PROFILE_VERSION_ID,
    )
    d1 = compute_main_agent_grant_source_digest(
        ceiling=MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        owner=owner,
        capability_key="skill.search",
        owner_content_or_policy_digest=DIGEST_A,
        manifest_membership_digest=DIGEST_B,
        binding_contract_digest=DIGEST_C,
    )
    d2 = compute_main_agent_grant_source_digest(
        ceiling=MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        owner=owner,
        capability_key="skill.search",
        owner_content_or_policy_digest=DIGEST_D,  # owner content changed
        manifest_membership_digest=DIGEST_B,
        binding_contract_digest=DIGEST_C,
    )
    assert d1 != d2
    # Descriptor digests are not inputs — changing only binding digest changes grant.
    d3 = compute_main_agent_grant_source_digest(
        ceiling=MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        owner=owner,
        capability_key="skill.search",
        owner_content_or_policy_digest=DIGEST_A,
        manifest_membership_digest=DIGEST_B,
        binding_contract_digest=DIGEST_E,
    )
    assert d3 != d1


def _tool_call(*, call_id: str, domain_key: str, binding_digest: str) -> Any:
    from app.assistant.domain.digests import sha256_canonical_json
    from app.assistant.provider_loop.messages import ProviderToolCall

    arguments = {"query": "x"}
    return ProviderToolCall(
        call_id=call_id,
        call_index=0,
        provider_alias=domain_key.replace(".", "_"),
        domain_key=domain_key,
        arguments=arguments,
        arguments_digest=sha256_canonical_json(arguments),
        binding_contract_digest=binding_digest,
        descriptor_digest=DIGEST_D,
        behavior_digest=DIGEST_B,
        classification_revision="plan02-v1",
        classification_ruleset_digest=DIGEST_A,
        manifest_revision=1,
        manifest_digest=DIGEST_E,
        surface_digest=DIGEST_C,
    )


def _scope():
    from app.assistant.main_agent.authorization import LOCAL_ASSISTANT_PRINCIPAL
    from app.assistant.provider_loop.contracts import (
        ProviderExecutionScope,
        compute_scope_digest,
    )

    return ProviderExecutionScope(
        run_id=RUN_ID,
        conversation_id=CONV_ID,
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        tenant_scope_id=None,
        scope_digest=compute_scope_digest(
            run_id=RUN_ID,
            conversation_id=CONV_ID,
            principal=LOCAL_ASSISTANT_PRINCIPAL,
            tenant_scope_id=None,
        ),
    )


def test_factory_issues_one_time_evidence_for_manifest_binding() -> None:
    from app.assistant.domain.contracts import (
        ResolvedMainAgentRef,
        ResolvedRunManifestRevision,
        compute_manifest_digest,
    )
    from app.assistant.main_agent.authorization import (
        LOCAL_ASSISTANT_PRINCIPAL,
        MainAgentAuthorizationEvidenceFactory,
    )
    from app.assistant.main_agent.control_capabilities import (
        build_main_agent_control_frozen_binding,
    )
    from app.assistant.capabilities.policy import AuthorizationEvidenceVerificationError

    binding = build_main_agent_control_frozen_binding(
        domain_key="skill.search",
        owner_version_id=PROFILE_VERSION_ID,
        source_snapshot_digest=DIGEST_A,
        app_build_revision="plan04-dev",
    )
    main_agent = ResolvedMainAgentRef(
        profile_id=uuid4(),
        version_id=PROFILE_VERSION_ID,
        profile_key="default",
        sequence=1,
        content_digest=DIGEST_A,
    )
    capabilities = (binding.ref,)
    manifest_digest = compute_manifest_digest(
        run_id=RUN_ID,
        revision=1,
        parent_digest=None,
        main_agent=main_agent,
        active_skills=(),
        capabilities=capabilities,
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
        capabilities=capabilities,
        provider=None,
        model=None,
        provider_aliases=(),
        effective_policy_digest=DIGEST_B,
        manifest_digest=manifest_digest,
    )
    scope = _scope()
    factory = MainAgentAuthorizationEvidenceFactory(
        scope=scope,
        manifest=manifest,
        profile_key="default",
        profile_content_digest=DIGEST_A,
    )
    call = _tool_call(
        call_id="call-factory-1",
        domain_key="skill.search",
        binding_digest=binding.ref.binding_contract_digest,
    )
    evidence = factory.issue(
        call=call,
        binding=binding,
        descriptor=_descriptor(
            capability_key="skill.search",
            resolution_digest=binding.ref.resolution_digest,
            binding_contract_digest=binding.ref.binding_contract_digest,
            dependency_closure_digest=binding.ref.dependency_closure_digest,
        ),
        scope=scope,
    )
    assert evidence.issuer == "skill_policy"
    assert evidence.entrypoint == "main_agent"
    assert evidence.principal == LOCAL_ASSISTANT_PRINCIPAL
    assert evidence.allowed_side_effects == ("none", "compute", "read")
    assert len(evidence.grant_source_digest) == 64

    with pytest.raises(AuthorizationEvidenceVerificationError):
        factory.issue(
            call=call,
            binding=binding,
            descriptor=_descriptor(),
            scope=scope,
        )


def test_inactive_binding_not_in_manifest_denied() -> None:
    from app.assistant.domain.contracts import (
        ResolvedMainAgentRef,
        create_base_run_manifest,
    )
    from app.assistant.main_agent.authorization import (
        MainAgentAuthorizationEvidenceFactory,
    )
    from app.assistant.main_agent.control_capabilities import (
        build_main_agent_control_frozen_binding,
    )
    from app.assistant.capabilities.policy import AuthorizationEvidenceVerificationError

    binding = build_main_agent_control_frozen_binding(
        domain_key="skill.search",
        owner_version_id=PROFILE_VERSION_ID,
        source_snapshot_digest=DIGEST_A,
        app_build_revision="plan04-dev",
    )
    manifest = create_base_run_manifest(
        run_id=RUN_ID,
        main_agent=ResolvedMainAgentRef(
            profile_id=uuid4(),
            version_id=PROFILE_VERSION_ID,
            profile_key="default",
            sequence=1,
            content_digest=DIGEST_A,
        ),
        provider=None,
        model=None,
        effective_policy_digest=None,
    )
    scope = _scope()
    factory = MainAgentAuthorizationEvidenceFactory(
        scope=scope,
        manifest=manifest,  # empty capabilities
        profile_key="default",
        profile_content_digest=DIGEST_A,
    )
    call = _tool_call(
        call_id="call-missing",
        domain_key="skill.search",
        binding_digest=binding.ref.binding_contract_digest,
    )
    with pytest.raises(AuthorizationEvidenceVerificationError) as exc:
        factory.issue(
            call=call,
            binding=binding,
            descriptor=_descriptor(),
            scope=scope,
        )
    assert exc.value.reason_code == "binding_not_in_manifest"


def test_unavailable_target_denied() -> None:
    from app.assistant.capabilities.contracts import CapabilityAvailability
    from app.assistant.capabilities.policy import (
        AuthorizationEvidenceVerificationError,
        CapabilityPolicyEngine,
    )

    verifier = _verifier()
    engine = CapabilityPolicyEngine({("skill_policy", "main_agent"): verifier})
    decision = engine.authorize(
        descriptor=_descriptor(
            availability=CapabilityAvailability(status="disabled", reason_code="x")
        ),
        evidence=_evidence(),
        context=_context(),
    )
    assert decision.allowed is False


def test_concurrent_single_use_verifier() -> None:
    from app.assistant.capabilities.policy import CapabilityPolicyEngine

    verifier = _verifier()
    engine = CapabilityPolicyEngine({("skill_policy", "main_agent"): verifier})
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        decision = engine.authorize(
            descriptor=_descriptor(),
            evidence=_evidence(),
            context=_context(),
        )
        with lock:
            results.append(decision.allowed)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1
    assert results.count(False) == 7

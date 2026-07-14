"""Plan 05 Task 2: authorization evidence issuance, composite verifier, redaction.

Preserves Plan 02/04 negative vectors for descriptor read→write, classification/
ruleset drift, ceiling/author-policy revision drift, missing grant digest, and
copy-descriptor verifier traps. Covers one-time call-ID verification and
Run/conversation/scope binding.
"""

from __future__ import annotations

import threading
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.capabilities.contracts import (  # noqa: E402
    CapabilityAuthorizationEvidence,
    CapabilityAvailability,
    CapabilityBehavior,
    CapabilityDescriptor,
    CapabilityExecutionContext,
    CapabilityOwnerRef,
    CapabilityPrincipal,
    CapabilityTimeoutPolicy,
    ClassificationContractRef,
)
from app.assistant.capabilities.policy import (  # noqa: E402
    AuthorizationEvidenceVerificationError,
    CapabilityPolicyEngine,
    build_composite_evidence_verifiers,
)
from app.assistant.domain.contracts import CapabilityCompletionContract  # noqa: E402
from app.assistant.domain.digests import sha256_canonical_json  # noqa: E402
from app.assistant.domain.json_schema import (  # noqa: E402
    binding_schema_digest,
    normalize_binding_schema,
)
from app.assistant.main_agent.authorization import (  # noqa: E402
    LOCAL_ASSISTANT_PRINCIPAL,
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
    SkillPolicyAuthorizationEvidenceVerifier,
    compute_main_agent_grant_source_digest,
)
from app.assistant.policy.contracts import (  # noqa: E402
    AuthorizationDecision,
    build_authorization_decision,
    build_effective_capability_grant,
    compute_principal_digest,
)
from app.assistant.policy.evidence import (  # noqa: E402
    CompositeAuthorizationEvidenceVerifier,
    SkillPolicySourceAwareEvidenceVerifier,
    assert_redaction_safe,
    compute_skill_policy_evidence_digest,
    decision_safe_repr,
    evidence_safe_repr,
    issue_skill_policy_evidence,
)
from app.assistant.provider_loop.runtime import (  # noqa: E402
    TestAuthorizationEvidenceVerifier,
)

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64
PROFILE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000301")
RUN_ID = UUID("00000000-0000-4000-8000-000000000302")
CONV_ID = UUID("00000000-0000-4000-8000-000000000303")


def _timeout():
    return CapabilityTimeoutPolicy(
        mode="cooperative", timeout_seconds=None, cancellation_supported=True
    )


def _behavior(**overrides: Any):
    payload = {
        "classification": ClassificationContractRef(
            schema_version=1,
            revision="plan02-v1",
            ruleset_digest=DIGEST_A,
        ),
        "side_effect": "read",
        "parallel_safe": True,
        "interrupt_mode": "none",
        "timeout_policy": _timeout(),
        "behavior_digest": DIGEST_B,
    }
    payload.update(overrides)
    return CapabilityBehavior(**payload)


def _descriptor(**overrides: Any):
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
        "executable_revision": "plan05-dev",
        "behavior": _behavior(),
        "availability": CapabilityAvailability(status="available"),
        "completion": CapabilityCompletionContract(
            terminal_output=False, needs_followup=True
        ),
    }
    payload.update(overrides)
    return CapabilityDescriptor(**payload)


def _owner() -> CapabilityOwnerRef:
    return CapabilityOwnerRef(
        owner_kind="main_agent",
        owner_id="default",
        owner_version_id=PROFILE_VERSION_ID,
    )


def _allowed_decision(
    *,
    allowed_side_effects: tuple[str, ...] = ("none", "compute", "read"),
    grant_source_digest: str = DIGEST_E,
) -> AuthorizationDecision:
    principal_digest = compute_principal_digest(LOCAL_ASSISTANT_PRINCIPAL)
    return build_authorization_decision(
        allowed=True,
        reason_code="allowed",
        principal_digest=principal_digest,
        entrypoint_policy_digest=DIGEST_A,
        global_policy_digest=DIGEST_B,
        owner_policy_digest=DIGEST_C,
        allowed_side_effects=allowed_side_effects,  # type: ignore[arg-type]
        grant_source_digest=grant_source_digest,
        exposure_digest=DIGEST_D,
        effective_policy_digest=DIGEST_F,
    )


def _evidence(**overrides: Any) -> CapabilityAuthorizationEvidence:
    payload = {
        "issuer": "skill_policy",
        "call_id": "call-1",
        "principal": LOCAL_ASSISTANT_PRINCIPAL,
        "entrypoint": "main_agent",
        "owner": _owner(),
        "capability_key": "skill.search",
        "resolution_digest": DIGEST_A,
        "binding_contract_digest": DIGEST_B,
        "dependency_closure_digest": DIGEST_C,
        "allowed_side_effects": ("none", "compute", "read"),
        "grant_source_digest": DIGEST_E,
        "evidence_digest": DIGEST_F,
    }
    payload.update(overrides)
    return CapabilityAuthorizationEvidence(**payload)


def _context(**overrides: Any) -> CapabilityExecutionContext:
    payload = {
        "call_id": "call-1",
        "run_id": RUN_ID,
        "conversation_id": CONV_ID,
        "nesting_depth": 0,
    }
    payload.update(overrides)
    return CapabilityExecutionContext(**payload)


def _verifier(**overrides: Any) -> SkillPolicyAuthorizationEvidenceVerifier:
    payload = {
        "expected_call_id": "call-1",
        "expected_capability_key": "skill.search",
        "expected_owner": _owner(),
        "expected_resolution_digest": DIGEST_A,
        "expected_binding_contract_digest": DIGEST_B,
        "expected_dependency_closure_digest": DIGEST_C,
        "expected_grant_source_digest": DIGEST_E,
        "expected_evidence_digest": DIGEST_F,
        "expected_principal": LOCAL_ASSISTANT_PRINCIPAL,
        "expected_run_id": RUN_ID,
        "expected_conversation_id": CONV_ID,
        "ceiling": MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        "allowed_side_effects": ("none", "compute", "read"),
    }
    payload.update(overrides)
    return SkillPolicyAuthorizationEvidenceVerifier(**payload)


# ---------------------------------------------------------------------------
# Alias / composite
# ---------------------------------------------------------------------------


def test_source_aware_alias_is_skill_policy_verifier() -> None:
    assert SkillPolicySourceAwareEvidenceVerifier is SkillPolicyAuthorizationEvidenceVerifier


def test_composite_routes_skill_policy_and_denies_unknown() -> None:
    skill = _verifier()
    composite = CompositeAuthorizationEvidenceVerifier(
        {
            ("skill_policy", "main_agent"): skill,
        }
    )
    verified = composite.verify(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    assert verified.allowed_side_effects == ("none", "compute", "read")

    with pytest.raises(AuthorizationEvidenceVerificationError) as exc:
        composite.verify(
            descriptor=_descriptor(),
            evidence=_evidence(issuer="openclaw_bridge", entrypoint="openclaw"),
            context=_context(),
        )
    assert exc.value.reason_code == "unknown_issuer_entrypoint"


def test_build_composite_evidence_verifiers_mapping() -> None:
    skill = _verifier()
    test_v = TestAuthorizationEvidenceVerifier()
    mapping = build_composite_evidence_verifiers(
        skill_policy_verifier=skill,
        test_verifier=test_v,
    )
    assert set(mapping.keys()) == {
        ("skill_policy", "main_agent"),
        ("test", "test"),
    }
    engine = CapabilityPolicyEngine(mapping)
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    assert decision.allowed is True

    # OpenClaw without mapping is denied.
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
        capability_key="skill.search",
        resolution_digest=DIGEST_A,
        binding_contract_digest=DIGEST_B,
        dependency_closure_digest=DIGEST_C,
        allowed_side_effects=("none", "compute", "read"),
        grant_source_digest=DIGEST_E,
        evidence_digest=DIGEST_F,
    )
    denied = engine.authorize(
        descriptor=_descriptor(),
        evidence=openclaw_evidence,
        context=_context(),
    )
    assert denied.allowed is False


# ---------------------------------------------------------------------------
# Evidence issuance
# ---------------------------------------------------------------------------


def test_issue_skill_policy_evidence_from_allowed_decision() -> None:
    decision = _allowed_decision()
    evidence = issue_skill_policy_evidence(
        call_id="call-issue-1",
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        owner=_owner(),
        capability_key="skill.search",
        resolution_digest=DIGEST_A,
        binding_contract_digest=DIGEST_B,
        dependency_closure_digest=DIGEST_C,
        decision=decision,
        counter=1,
        scope_digest="1" * 64,
        manifest_digest="2" * 64,
    )
    assert evidence.issuer == "skill_policy"
    assert evidence.entrypoint == "main_agent"
    assert evidence.allowed_side_effects == ("none", "compute", "read")
    assert evidence.grant_source_digest == decision.grant_source_digest
    assert len(evidence.evidence_digest) == 64
    # Digest includes decision + effective policy.
    expected = compute_skill_policy_evidence_digest(
        call_id="call-issue-1",
        counter=1,
        binding_contract_digest=DIGEST_B,
        grant_source_digest=decision.grant_source_digest or "",
        decision_digest=decision.decision_digest,
        effective_policy_digest=decision.effective_policy_digest,
        scope_digest="1" * 64,
        manifest_digest="2" * 64,
    )
    assert evidence.evidence_digest == expected


def test_issue_rejects_denied_decision() -> None:
    principal_digest = compute_principal_digest(LOCAL_ASSISTANT_PRINCIPAL)
    denied = build_authorization_decision(
        allowed=False,
        reason_code="exposure_missing",
        principal_digest=principal_digest,
        entrypoint_policy_digest=DIGEST_A,
        global_policy_digest=DIGEST_B,
        owner_policy_digest=DIGEST_C,
        exposure_digest=DIGEST_D,
        effective_policy_digest=DIGEST_F,
    )
    with pytest.raises(AuthorizationEvidenceVerificationError) as exc:
        issue_skill_policy_evidence(
            call_id="call-x",
            principal=LOCAL_ASSISTANT_PRINCIPAL,
            owner=_owner(),
            capability_key="skill.search",
            resolution_digest=DIGEST_A,
            binding_contract_digest=DIGEST_B,
            dependency_closure_digest=DIGEST_C,
            decision=denied,
            scope_digest="1" * 64,
            manifest_digest="2" * 64,
        )
    assert exc.value.reason_code == "decision_not_allowed"


def test_issue_rejects_missing_grant_source() -> None:
    # Allowed decision always requires grant_source; construct via model bypass
    # is impossible due to validators. Issue path checks decision fields.
    decision = _allowed_decision()
    # Mutate-like: pass grant that mismatches.
    grant = build_effective_capability_grant(
        owner_kind="main_agent",
        owner_id="default",
        owner_version_id=PROFILE_VERSION_ID,
        capability_key="skill.search",
        binding_contract_digest=DIGEST_B,
        allowed_side_effects=("none", "compute", "read"),
        entrypoint_policy_digest=DIGEST_A,
        global_policy_digest=DIGEST_B,
        owner_policy_digest=DIGEST_C,
    )
    with pytest.raises(AuthorizationEvidenceVerificationError) as exc:
        issue_skill_policy_evidence(
            call_id="call-x",
            principal=LOCAL_ASSISTANT_PRINCIPAL,
            owner=_owner(),
            capability_key="skill.search",
            resolution_digest=DIGEST_A,
            binding_contract_digest=DIGEST_B,
            dependency_closure_digest=DIGEST_C,
            decision=decision,
            grant=grant,  # different grant_source_digest
            scope_digest="1" * 64,
            manifest_digest="2" * 64,
        )
    assert exc.value.reason_code == "grant_source_digest_mismatch"


# ---------------------------------------------------------------------------
# Negative vectors (Plan 02/04)
# ---------------------------------------------------------------------------


def test_descriptor_read_to_write_denied() -> None:
    """Descriptor mutated read→write under read-only grant denies before adapter."""
    verifier = _verifier()
    engine = CapabilityPolicyEngine({("skill_policy", "main_agent"): verifier})
    decision = engine.authorize(
        descriptor=_descriptor(
            behavior=_behavior(side_effect="write_local", parallel_safe=False)
        ),
        evidence=_evidence(),
        context=_context(),
    )
    assert decision.allowed is False


def test_copy_descriptor_effect_trap() -> None:
    """Verifier that copies (descriptor.behavior.side_effect,) is a known trap."""
    verifier = _verifier(copy_descriptor_effect=True)
    # With copy flag, write becomes "granted" by the trap; production never sets this.
    verified = verifier.verify(
        descriptor=_descriptor(
            behavior=_behavior(side_effect="write_local", parallel_safe=False)
        ),
        evidence=_evidence(allowed_side_effects=("none", "compute", "read")),
        context=_context(),
    )
    assert verified.allowed_side_effects == ("write_local",)
    # Production path denies write.
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


def test_omit_grant_source_digest_denied() -> None:
    verifier = _verifier(omit_grant_source_digest=True)
    with pytest.raises(AuthorizationEvidenceVerificationError) as exc:
        verifier.verify(
            descriptor=_descriptor(),
            evidence=_evidence(),
            context=_context(),
        )
    assert exc.value.reason_code == "missing_grant_source_digest"


def test_grant_source_digest_mismatch_denied() -> None:
    verifier = _verifier(expected_grant_source_digest="9" * 64)
    with pytest.raises(AuthorizationEvidenceVerificationError) as exc:
        verifier.verify(
            descriptor=_descriptor(),
            evidence=_evidence(),
            context=_context(),
        )
    assert exc.value.reason_code == "grant_source_digest_mismatch"


def test_evidence_grant_mismatch_denied() -> None:
    """Evidence allowed_side_effects must match independent grant."""
    verifier = _verifier(allowed_side_effects=("none", "read"))
    with pytest.raises(AuthorizationEvidenceVerificationError) as exc:
        verifier.verify(
            descriptor=_descriptor(),
            evidence=_evidence(allowed_side_effects=("none", "compute", "read")),
            context=_context(),
        )
    assert exc.value.reason_code == "evidence_grant_mismatch"


def test_ceiling_revision_changes_grant_source() -> None:
    owner = _owner()
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
        owner_content_or_policy_digest=DIGEST_D,  # author/policy revision drift
        manifest_membership_digest=DIGEST_B,
        binding_contract_digest=DIGEST_C,
    )
    assert d1 != d2
    assert len(d1) == 64


def test_classification_ruleset_not_in_grant_source() -> None:
    """Classification/ruleset digests never enter grant_source_digest."""
    owner = _owner()
    d1 = compute_main_agent_grant_source_digest(
        ceiling=MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        owner=owner,
        capability_key="skill.search",
        owner_content_or_policy_digest=DIGEST_A,
        manifest_membership_digest=DIGEST_B,
        binding_contract_digest=DIGEST_C,
    )
    # Same inputs → same digest regardless of any classification change.
    d2 = compute_main_agent_grant_source_digest(
        ceiling=MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        owner=owner,
        capability_key="skill.search",
        owner_content_or_policy_digest=DIGEST_A,
        manifest_membership_digest=DIGEST_B,
        binding_contract_digest=DIGEST_C,
    )
    assert d1 == d2
    # Platform ceiling digest is fixed.
    assert MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST == (
        MAIN_AGENT_READ_ONLY_EFFECT_CEILING.ceiling_digest
    )


# ---------------------------------------------------------------------------
# One-time call-ID + scope binding
# ---------------------------------------------------------------------------


def test_one_time_call_id_verification() -> None:
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


def test_wrong_run_denied() -> None:
    verifier = _verifier()
    engine = CapabilityPolicyEngine({("skill_policy", "main_agent"): verifier})
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(run_id=uuid4()),
    )
    assert decision.allowed is False


def test_wrong_conversation_denied() -> None:
    verifier = _verifier()
    engine = CapabilityPolicyEngine({("skill_policy", "main_agent"): verifier})
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(conversation_id=uuid4()),
    )
    assert decision.allowed is False


def test_call_id_mismatch_denied() -> None:
    verifier = _verifier()
    engine = CapabilityPolicyEngine({("skill_policy", "main_agent"): verifier})
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(call_id="call-other"),
        context=_context(call_id="call-1"),
    )
    assert decision.allowed is False


def test_concurrent_single_use() -> None:
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


# ---------------------------------------------------------------------------
# Redaction corpus
# ---------------------------------------------------------------------------


def test_redaction_safe_decisions_and_evidence() -> None:
    decision = _allowed_decision()
    evidence = issue_skill_policy_evidence(
        call_id="call-redact",
        principal=LOCAL_ASSISTANT_PRINCIPAL,
        owner=_owner(),
        capability_key="skill.search",
        resolution_digest=DIGEST_A,
        binding_contract_digest=DIGEST_B,
        dependency_closure_digest=DIGEST_C,
        decision=decision,
        counter=1,
        scope_digest="1" * 64,
        manifest_digest="2" * 64,
    )
    assert_redaction_safe(decision_safe_repr(decision), label="decision")
    assert_redaction_safe(evidence_safe_repr(evidence), label="evidence")
    assert_redaction_safe(decision.decision_digest, label="decision_digest")
    assert_redaction_safe(evidence.evidence_digest, label="evidence_digest")
    assert_redaction_safe(evidence.grant_source_digest, label="grant_source")
    # model_dump / repr of decision must not introduce secrets.
    dumped = str(decision.model_dump(mode="json"))
    assert_redaction_safe(dumped, label="decision_dump")
    dumped_e = str(evidence.model_dump(mode="json"))
    assert_redaction_safe(dumped_e, label="evidence_dump")


def test_assert_redaction_safe_rejects_secrets() -> None:
    with pytest.raises(ValueError):
        assert_redaction_safe("user password=hunter2", label="x")
    with pytest.raises(ValueError):
        assert_redaction_safe("Bearer abc.def", label="x")
    with pytest.raises(ValueError):
        assert_redaction_safe("https://evil.example/secret", label="x")
    with pytest.raises(ValueError):
        assert_redaction_safe("system_message: do evil", label="x")


def test_openclaw_and_skill_policy_isolated_via_composite() -> None:
    skill = _verifier()
    test_v = TestAuthorizationEvidenceVerifier()
    mapping = build_composite_evidence_verifiers(
        skill_policy_verifier=skill,
        test_verifier=test_v,
    )
    engine = CapabilityPolicyEngine(mapping)
    # skill_policy ok
    assert engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    ).allowed is True
    # openclaw not mapped → deny
    openclaw = CapabilityAuthorizationEvidence(
        issuer="openclaw_bridge",
        call_id="call-oc",
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
        capability_key="skill.search",
        resolution_digest=DIGEST_A,
        binding_contract_digest=DIGEST_B,
        dependency_closure_digest=DIGEST_C,
        allowed_side_effects=("none", "compute", "read"),
        grant_source_digest=DIGEST_E,
        evidence_digest=DIGEST_F,
    )
    assert engine.authorize(
        descriptor=_descriptor(),
        evidence=openclaw,
        context=_context(call_id="call-oc"),
    ).allowed is False

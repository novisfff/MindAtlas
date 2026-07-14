"""Deny-by-default CapabilityPolicyEngine matrix tests (Plan 02 Task 7)."""

from __future__ import annotations

import threading
from typing import Any
from uuid import uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _timeout_policy(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityTimeoutPolicy

    payload = {
        "mode": "cooperative",
        "timeout_seconds": None,
        "cancellation_supported": True,
    }
    payload.update(overrides)
    return CapabilityTimeoutPolicy(**payload)


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


def _availability(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityAvailability

    payload = {
        "status": "available",
        "reason_code": None,
        "compatibility_only": False,
    }
    payload.update(overrides)
    return CapabilityAvailability(**payload)


def _completion(**overrides: Any):
    from app.assistant.domain.contracts import CapabilityCompletionContract

    payload = {
        "terminal_output": True,
        "needs_followup": False,
        "followup_hint": None,
    }
    payload.update(overrides)
    return CapabilityCompletionContract(**payload)


def _descriptor(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityDescriptor
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    in_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    out_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    payload = {
        "capability_key": "search.query",
        "capability_type": "tool",
        "target_identity": "system-tool:search_entries",
        "target_id": None,
        "target_version_id": None,
        "target_revision": None,
        "resolution_digest": DIGEST_A,
        "binding_contract_digest": DIGEST_B,
        "dependency_closure_digest": DIGEST_C,
        "display_name": "Search",
        "description": "search",
        "input_schema": in_schema,
        "output_schema": out_schema,
        "input_schema_digest": binding_schema_digest(in_schema),
        "output_schema_digest": binding_schema_digest(out_schema),
        "descriptor_digest": DIGEST_D,
        "executable_revision": "build-1",
        "behavior": _behavior(),
        "availability": _availability(),
        "completion": _completion(),
    }
    payload.update(overrides)
    return CapabilityDescriptor(**payload)


def _principal(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityPrincipal

    payload = {
        "principal_type": "test",
        "principal_id": "principal-1",
        "authenticated": True,
    }
    payload.update(overrides)
    return CapabilityPrincipal(**payload)


def _owner(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityOwnerRef

    payload = {
        "owner_kind": "test",
        "owner_id": "owner-1",
        "owner_version_id": None,
    }
    payload.update(overrides)
    return CapabilityOwnerRef(**payload)


def _context(call_id: str = "call-1", **overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityExecutionContext

    payload = {"call_id": call_id, "nesting_depth": 0}
    payload.update(overrides)
    return CapabilityExecutionContext(**payload)


def _evidence(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityAuthorizationEvidence

    payload = {
        "issuer": "test",
        "call_id": "call-1",
        "principal": _principal(),
        "entrypoint": "test",
        "owner": _owner(),
        "capability_key": "search.query",
        "resolution_digest": DIGEST_A,
        "binding_contract_digest": DIGEST_B,
        "dependency_closure_digest": DIGEST_C,
        "allowed_side_effects": ("none", "compute", "read"),
        "grant_source_digest": DIGEST_E,
        "evidence_digest": DIGEST_F,
    }
    payload.update(overrides)
    return CapabilityAuthorizationEvidence(**payload)


class _TestVerifier:
    """Request-scoped single-use test verifier."""

    def __init__(
        self,
        *,
        expected_call_id: str = "call-1",
        allowed_side_effects: tuple[str, ...] = ("none", "compute", "read"),
        grant_source_digest: str = DIGEST_E,
        mutate_fields: dict[str, Any] | None = None,
        fail_reason: str | None = None,
        copy_descriptor_effect: bool = False,
    ) -> None:
        self.expected_call_id = expected_call_id
        self.allowed_side_effects = allowed_side_effects
        self.grant_source_digest = grant_source_digest
        self.mutate_fields = mutate_fields or {}
        self.fail_reason = fail_reason
        self.copy_descriptor_effect = copy_descriptor_effect
        self.verifier_instance_id = str(uuid4())
        self._lock = threading.Lock()
        self._consumed = False
        self.verify_calls = 0

    def verify(self, *, descriptor, evidence, context):
        from app.assistant.capabilities.contracts import VerifiedAuthorizationEvidence
        from app.assistant.capabilities.policy import (
            AtomicSingleUseDispatchPermit,
            AuthorizationEvidenceVerificationError,
        )
        from app.assistant.domain.digests import sha256_canonical_json

        self.verify_calls += 1
        if self.fail_reason is not None:
            raise AuthorizationEvidenceVerificationError(self.fail_reason)

        with self._lock:
            if self._consumed:
                raise AuthorizationEvidenceVerificationError("evidence_already_consumed")
            self._consumed = True

        if evidence.call_id != self.expected_call_id:
            raise AuthorizationEvidenceVerificationError("call_id_mismatch")
        if context.call_id != self.expected_call_id:
            raise AuthorizationEvidenceVerificationError("call_id_mismatch")

        effects = self.allowed_side_effects
        if self.copy_descriptor_effect:
            # Malicious verifier that copies descriptor effect — still independent
            # ceiling check uses this tuple, but tests prove policy uses it as
            # independent grant, not as a free pass for any effect.
            effects = (descriptor.behavior.side_effect,)

        grant = self.grant_source_digest
        fields = {
            "call_id": evidence.call_id,
            "verifier_key": (evidence.issuer, evidence.entrypoint),
            "verifier_instance_id": self.verifier_instance_id,
            "principal": evidence.principal,
            "entrypoint": evidence.entrypoint,
            "owner": evidence.owner,
            "capability_key": evidence.capability_key,
            "resolution_digest": evidence.resolution_digest,
            "binding_contract_digest": evidence.binding_contract_digest,
            "dependency_closure_digest": evidence.dependency_closure_digest,
            "allowed_side_effects": effects,
            "grant_source_digest": grant,
            "evidence_digest": evidence.evidence_digest,
            "verification_digest": sha256_canonical_json(
                {
                    "callId": evidence.call_id,
                    "evidenceDigest": evidence.evidence_digest,
                    "verifierInstanceId": self.verifier_instance_id,
                }
            ),
            "dispatch_permit": AtomicSingleUseDispatchPermit(),
        }
        fields.update(self.mutate_fields)
        return VerifiedAuthorizationEvidence(**fields)


def _engine(verifiers: dict | None = None):
    from app.assistant.capabilities.policy import CapabilityPolicyEngine

    if verifiers is None:
        verifiers = {("test", "test"): _TestVerifier()}
    return CapabilityPolicyEngine(verifiers)


def _assert_safe_denial(decision, *, reason_code: str | None = None) -> None:
    assert decision.allowed is False
    assert decision.dispatch_permit is None
    if reason_code is not None:
        assert decision.reason_code == reason_code
    # Never include target input/config in reason.
    lowered = decision.reason_code.lower()
    for banned in ("password", "secret", "api_key", "authorization", "config=", "input="):
        assert banned not in lowered
    assert len(decision.reason_code) <= 64
    assert decision.decision_digest and len(decision.decision_digest) == 64


# ---------------------------------------------------------------------------
# Matrix: deny by default
# ---------------------------------------------------------------------------


def test_unauthenticated_principal_denied() -> None:
    engine = _engine()
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(principal=_principal(authenticated=False)),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="unauthenticated_principal")


def test_unknown_issuer_denied() -> None:
    # Issuer not in trusted mapping → deny even if entrypoint is test.
    engine = _engine(verifiers={})  # no verifiers registered
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(issuer="test", entrypoint="test"),
        context=_context(),
    )
    _assert_safe_denial(decision)
    assert decision.reason_code in {"missing_verifier", "unknown_issuer_entrypoint"}


def test_issuer_entrypoint_mismatch_denied() -> None:
    # OpenClaw issuer registered only for openclaw entrypoint.
    engine = _engine(verifiers={("openclaw_bridge", "openclaw"): _TestVerifier()})
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(issuer="openclaw_bridge", entrypoint="main_agent"),
        context=_context(),
    )
    _assert_safe_denial(decision)
    assert decision.reason_code in {
        "missing_verifier",
        "main_agent_denied",
        "unknown_issuer_entrypoint",
    }


def test_missing_verifier_denied() -> None:
    engine = _engine(verifiers={("test", "test"): _TestVerifier()})
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(issuer="system", entrypoint="test"),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="missing_verifier")


def test_forged_verifier_evidence_denied() -> None:
    verifier = _TestVerifier(
        mutate_fields={"verifier_key": ("openclaw_bridge", "openclaw")},
    )
    engine = _engine(verifiers={("test", "test"): verifier})
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="forged_verifier_evidence")


def test_call_id_mismatch_denied() -> None:
    engine = _engine()
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(call_id="call-other"),
        context=_context(call_id="call-1"),
    )
    _assert_safe_denial(decision, reason_code="call_id_mismatch")


def test_sibling_replay_and_second_use_denied() -> None:
    verifier = _TestVerifier()
    engine = _engine(verifiers={("test", "test"): verifier})
    first = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    assert first.allowed is True
    assert first.dispatch_permit is not None

    # Copy of Pydantic evidence object cannot authorize a second call.
    second = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    _assert_safe_denial(second, reason_code="evidence_already_consumed")

    # Sibling call_id with same verifier instance denied.
    sibling_verifier = _TestVerifier(expected_call_id="call-1")
    sibling_engine = _engine(verifiers={("test", "test"): sibling_verifier})
    sibling = sibling_engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(call_id="call-2"),
        context=_context(call_id="call-2"),
    )
    _assert_safe_denial(sibling, reason_code="call_id_mismatch")


def test_owner_mismatch_denied() -> None:
    verifier = _TestVerifier(
        mutate_fields={"owner": _owner(owner_id="other-owner")},
    )
    engine = _engine(verifiers={("test", "test"): verifier})
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="owner_mismatch")


def test_capability_key_mismatch_denied() -> None:
    engine = _engine()
    decision = engine.authorize(
        descriptor=_descriptor(capability_key="search.query"),
        evidence=_evidence(capability_key="other.key"),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="capability_key_mismatch")


def test_resolution_digest_mismatch_denied() -> None:
    engine = _engine()
    decision = engine.authorize(
        descriptor=_descriptor(resolution_digest=DIGEST_A),
        evidence=_evidence(resolution_digest=DIGEST_E),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="resolution_digest_mismatch")


def test_binding_contract_digest_mismatch_denied() -> None:
    engine = _engine()
    decision = engine.authorize(
        descriptor=_descriptor(binding_contract_digest=DIGEST_B),
        evidence=_evidence(binding_contract_digest=DIGEST_E),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="binding_contract_digest_mismatch")


def test_dependency_closure_digest_mismatch_denied() -> None:
    engine = _engine()
    decision = engine.authorize(
        descriptor=_descriptor(dependency_closure_digest=DIGEST_C),
        evidence=_evidence(dependency_closure_digest=DIGEST_E),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="dependency_closure_digest_mismatch")


def test_side_effect_omitted_denied() -> None:
    # Grant omits the actual classified effect.
    engine = _engine(
        verifiers={
            ("test", "test"): _TestVerifier(allowed_side_effects=("none", "compute")),
        }
    )
    decision = engine.authorize(
        descriptor=_descriptor(behavior=_behavior(side_effect="read")),
        evidence=_evidence(allowed_side_effects=("none", "compute")),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="side_effect_not_granted")


def test_unknown_explicitly_listed_denied() -> None:
    engine = _engine(
        verifiers={
            ("test", "test"): _TestVerifier(
                allowed_side_effects=("none", "compute", "read", "unknown"),
            ),
        }
    )
    decision = engine.authorize(
        descriptor=_descriptor(behavior=_behavior(side_effect="read", parallel_safe=True)),
        evidence=_evidence(
            allowed_side_effects=("none", "compute", "read", "unknown"),
        ),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="unknown_side_effect_denied")


def test_unknown_actual_effect_denied_even_under_broad_ceiling() -> None:
    from app.assistant.capabilities.policy import lattice_prefix_through

    broad = lattice_prefix_through("write_external")
    engine = _engine(
        verifiers={
            ("test", "test"): _TestVerifier(allowed_side_effects=broad),
        }
    )
    decision = engine.authorize(
        descriptor=_descriptor(
            behavior=_behavior(side_effect="unknown", parallel_safe=False),
        ),
        evidence=_evidence(allowed_side_effects=broad),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="unknown_side_effect_denied")


@pytest.mark.parametrize(
    "status,reason",
    [
        ("disabled", "target_disabled"),
        ("missing", "target_missing"),
        ("version_drift", "target_version_drift"),
        ("unsupported", "target_unavailable"),
    ],
)
def test_unavailable_target_denied(status: str, reason: str) -> None:
    engine = _engine()
    decision = engine.authorize(
        descriptor=_descriptor(
            availability=_availability(status=status, reason_code=status),
        ),
        evidence=_evidence(),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code=reason)


def test_main_agent_without_production_verifier_denied() -> None:
    engine = _engine(verifiers={("test", "test"): _TestVerifier()})
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(issuer="skill_policy", entrypoint="main_agent"),
        context=_context(),
    )
    _assert_safe_denial(decision)
    assert decision.reason_code in {"main_agent_denied", "missing_verifier"}


def test_exact_test_grant_allows() -> None:
    engine = _engine()
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    assert decision.allowed is True
    assert decision.reason_code == "allow"
    assert decision.dispatch_permit is not None
    assert decision.granted_side_effects == ("none", "compute", "read")
    assert len(decision.decision_digest) == 64


def test_exact_openclaw_grant_allows() -> None:
    from app.assistant.capabilities.policy import (
        OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS,
        grant_source_digest_for_ceiling,
        lattice_prefix_through,
    )

    ceiling = OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS["search_entries"]
    grant = grant_source_digest_for_ceiling(ceiling, exposure_digest=DIGEST_A)
    verifier = _TestVerifier(
        allowed_side_effects=ceiling.allowed_side_effects,
        grant_source_digest=grant,
    )
    engine = _engine(verifiers={("openclaw_bridge", "openclaw"): verifier})
    decision = engine.authorize(
        descriptor=_descriptor(
            capability_key="search_entries",
            behavior=_behavior(side_effect="read"),
        ),
        evidence=_evidence(
            issuer="openclaw_bridge",
            entrypoint="openclaw",
            principal=_principal(
                principal_type="openclaw_installation",
                principal_id="openclaw",
            ),
            owner=_owner(owner_kind="openclaw_catalog", owner_id="item-1"),
            capability_key="search_entries",
            allowed_side_effects=ceiling.allowed_side_effects,
            grant_source_digest=grant,
        ),
        context=_context(),
    )
    assert decision.allowed is True
    assert decision.grant_source_digest == grant
    assert "read" in decision.granted_side_effects
    # Lattice prefix is ordered, not a one-value copy.
    assert decision.granted_side_effects == lattice_prefix_through("read")


def test_nesting_depth_accepted_within_ceiling() -> None:
    from app.assistant.capabilities.policy import MAX_CAPABILITY_NESTING_DEPTH

    engine = _engine()
    for depth in range(0, MAX_CAPABILITY_NESTING_DEPTH + 1):
        # Fresh verifier per call (single-use).
        eng = _engine(verifiers={("test", "test"): _TestVerifier()})
        decision = eng.authorize(
            descriptor=_descriptor(),
            evidence=_evidence(),
            context=_context(nesting_depth=depth),
        )
        assert decision.allowed is True, depth


def test_nesting_depth_five_denied() -> None:
    from app.assistant.capabilities.policy import MAX_CAPABILITY_NESTING_DEPTH

    engine = _engine()
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(nesting_depth=MAX_CAPABILITY_NESTING_DEPTH + 1),
    )
    _assert_safe_denial(decision, reason_code="invalid_nesting_depth")


def test_openclaw_read_ceiling_denies_write_descriptor() -> None:
    from app.assistant.capabilities.policy import (
        OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS,
        grant_source_digest_for_ceiling,
    )

    ceiling = OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS["search_entries"]
    assert "write_local" not in ceiling.allowed_side_effects
    grant = grant_source_digest_for_ceiling(ceiling, exposure_digest=DIGEST_A)
    engine = _engine(
        verifiers={
            ("openclaw_bridge", "openclaw"): _TestVerifier(
                allowed_side_effects=ceiling.allowed_side_effects,
                grant_source_digest=grant,
            ),
        }
    )
    decision = engine.authorize(
        descriptor=_descriptor(
            capability_key="search_entries",
            behavior=_behavior(side_effect="write_local", parallel_safe=False),
        ),
        evidence=_evidence(
            issuer="openclaw_bridge",
            entrypoint="openclaw",
            capability_key="search_entries",
            allowed_side_effects=ceiling.allowed_side_effects,
            grant_source_digest=grant,
        ),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="side_effect_not_granted")


def test_openclaw_read_ceiling_allows_read_descriptor() -> None:
    from app.assistant.capabilities.policy import (
        OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS,
        grant_source_digest_for_ceiling,
    )

    ceiling = OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS["search_entries"]
    grant = grant_source_digest_for_ceiling(ceiling, exposure_digest=DIGEST_A)
    engine = _engine(
        verifiers={
            ("openclaw_bridge", "openclaw"): _TestVerifier(
                allowed_side_effects=ceiling.allowed_side_effects,
                grant_source_digest=grant,
            ),
        }
    )
    decision = engine.authorize(
        descriptor=_descriptor(
            capability_key="search_entries",
            behavior=_behavior(side_effect="read"),
        ),
        evidence=_evidence(
            issuer="openclaw_bridge",
            entrypoint="openclaw",
            capability_key="search_entries",
            allowed_side_effects=ceiling.allowed_side_effects,
            grant_source_digest=grant,
        ),
        context=_context(),
    )
    assert decision.allowed is True


def test_classifier_output_cannot_mutate_grant_fields() -> None:
    """Changing classified side_effect does not rewrite evidence/grant fields."""
    engine = _engine()
    evidence = _evidence(allowed_side_effects=("none", "compute", "read"))
    read_desc = _descriptor(behavior=_behavior(side_effect="read"))
    write_desc = _descriptor(
        behavior=_behavior(side_effect="write_local", parallel_safe=False),
    )

    allow = engine.authorize(
        descriptor=read_desc, evidence=evidence, context=_context()
    )
    assert allow.allowed is True
    # Evidence object is frozen; grant fields unchanged.
    assert evidence.allowed_side_effects == ("none", "compute", "read")
    assert evidence.grant_source_digest == DIGEST_E

    # Fresh verifier for second call.
    deny_engine = _engine()
    deny = deny_engine.authorize(
        descriptor=write_desc, evidence=evidence, context=_context()
    )
    _assert_safe_denial(deny, reason_code="side_effect_not_granted")
    assert evidence.allowed_side_effects == ("none", "compute", "read")
    assert evidence.grant_source_digest == DIGEST_E


def test_ceiling_revision_change_invalidates_grant_source_digest() -> None:
    from app.assistant.capabilities.policy import (
        build_openclaw_effect_ceiling,
        grant_source_digest_for_ceiling,
    )

    old = build_openclaw_effect_ceiling(
        ceiling_scope="system_item",
        ceiling_key="search_entries",
        revision="plan02-v1",
        maximum_effect="read",
        allowed_interrupt_modes=("none",),
    )
    new = build_openclaw_effect_ceiling(
        ceiling_scope="system_item",
        ceiling_key="search_entries",
        revision="plan02-v2",
        maximum_effect="read",
        allowed_interrupt_modes=("none",),
    )
    assert old.ceiling_digest != new.ceiling_digest
    old_grant = grant_source_digest_for_ceiling(old, exposure_digest=DIGEST_A)
    new_grant = grant_source_digest_for_ceiling(new, exposure_digest=DIGEST_A)
    assert old_grant != new_grant


def test_verifier_copying_descriptor_effect_fails_write_under_read_ceiling() -> None:
    """A verifier that copies descriptor.behavior into allowed_side_effects is not a bypass:
    the evidence/grant ceiling used by production OpenClaw is independent. Here we
    still require the *evidence* grant to list the effect — policy checks verified
    allowed_side_effects, so a copying verifier could allow writes. The independent
    ceiling test above is the production invariant; this documents the requirement
    that production verifiers must NOT copy descriptor effects.
    """
    from app.assistant.capabilities.policy import (
        OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS,
        grant_source_digest_for_ceiling,
    )

    ceiling = OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS["search_entries"]
    grant = grant_source_digest_for_ceiling(ceiling, exposure_digest=DIGEST_A)
    # Production-like verifier uses ceiling, not descriptor.
    engine = _engine(
        verifiers={
            ("openclaw_bridge", "openclaw"): _TestVerifier(
                allowed_side_effects=ceiling.allowed_side_effects,
                grant_source_digest=grant,
                copy_descriptor_effect=False,
            ),
        }
    )
    decision = engine.authorize(
        descriptor=_descriptor(
            capability_key="search_entries",
            behavior=_behavior(side_effect="write_local", parallel_safe=False),
        ),
        evidence=_evidence(
            issuer="openclaw_bridge",
            entrypoint="openclaw",
            capability_key="search_entries",
            allowed_side_effects=ceiling.allowed_side_effects,
            grant_source_digest=grant,
        ),
        context=_context(),
    )
    _assert_safe_denial(decision, reason_code="side_effect_not_granted")


def test_manually_constructed_evidence_without_verifier_fails() -> None:
    engine = _engine(verifiers={})
    decision = engine.authorize(
        descriptor=_descriptor(),
        evidence=_evidence(),
        context=_context(),
    )
    _assert_safe_denial(decision)


def test_permit_single_use_consume() -> None:
    from app.assistant.capabilities.policy import AtomicSingleUseDispatchPermit

    permit = AtomicSingleUseDispatchPermit()
    permit.consume(call_id="call-1", descriptor_digest=DIGEST_D)
    with pytest.raises(PermissionError):
        permit.consume(call_id="call-1", descriptor_digest=DIGEST_D)


def test_concurrent_permit_consume_admits_once() -> None:
    from app.assistant.capabilities.policy import AtomicSingleUseDispatchPermit

    permit = AtomicSingleUseDispatchPermit()
    successes = []
    errors = []

    def worker() -> None:
        try:
            permit.consume(call_id="call-1", descriptor_digest=DIGEST_D)
            successes.append(1)
        except PermissionError:
            errors.append(1)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(successes) == 1
    assert sum(errors) == 7


def test_openclaw_system_ceilings_cover_six_items() -> None:
    from app.assistant.capabilities.policy import OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS

    expected = {
        "search_entries",
        "get_entry",
        "create_relation",
        "query_knowledge_graph",
        "submit_context_capture",
        "generate_periodic_review",
    }
    assert set(OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS) == expected
    for ceiling in OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS.values():
        assert "unknown" not in ceiling.allowed_side_effects
        assert "durable" not in ceiling.allowed_interrupt_modes


def test_custom_source_ceilings_exist() -> None:
    from app.assistant.capabilities.policy import OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS

    assert set(OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS) == {"tool", "workflow", "agent"}
    assert "write_external" in OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS["tool"].allowed_side_effects
    assert "legacy_blocking" in OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS["workflow"].allowed_interrupt_modes
    assert "legacy_blocking" not in OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS["tool"].allowed_interrupt_modes

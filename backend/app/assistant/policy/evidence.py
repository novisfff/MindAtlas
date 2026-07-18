"""Plan 05 authorization evidence issuance and composite verifier.

Issues CapabilityAuthorizationEvidence from an allowed AuthorizationDecision /
EffectiveCapabilityGrant. Composite routes by issuer/entrypoint. The call-scoped
skill_policy verifier lives in main_agent.authorization (Plan 04 transport +
Plan 05 source-aware checks) and is re-exported here as
SkillPolicySourceAwareEvidenceVerifier.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.assistant.capabilities.contracts import (
    CapabilityAuthorizationEvidence,
    CapabilityDescriptor,
    CapabilityExecutionContext,
    CapabilityOwnerRef,
    CapabilityPrincipal,
    VerifiedAuthorizationEvidence,
)
from app.assistant.capabilities.policy import AuthorizationEvidenceVerificationError
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.main_agent.authorization import (
    SkillPolicyAuthorizationEvidenceVerifier,
)
from app.assistant.policy.contracts import (
    AuthorizationDecision,
    AuthorizationDecisionV2,
    AuthorizationDecisionUnion,
    EffectiveCapabilityGrant,
)

# Canonical Plan 05 name for the source-aware skill_policy verifier.
SkillPolicySourceAwareEvidenceVerifier = SkillPolicyAuthorizationEvidenceVerifier

# ---------------------------------------------------------------------------
# Redaction / safe repr helpers
# ---------------------------------------------------------------------------

_FORBIDDEN_EVIDENCE_SUBSTRINGS = (
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization:",
    "bearer ",
    "prompt",
    "system_message",
    "tool_arguments",
    "exception:",
    "traceback",
    "http://",
    "https://",
)


def assert_redaction_safe(text: str, *, label: str = "value") -> None:
    """Raise if text appears to contain forbidden policy/prose/secret material."""
    lowered = text.lower()
    for needle in _FORBIDDEN_EVIDENCE_SUBSTRINGS:
        if needle in lowered:
            raise ValueError(f"{label} contains forbidden material: {needle!r}")


def decision_safe_repr(decision: AuthorizationDecisionUnion) -> str:
    """Deterministic safe repr for decisions (no prose/user data)."""
    if isinstance(decision, AuthorizationDecisionV2):
        return (
            "AuthorizationDecisionV2("
            f"policy_allowed={decision.policy_allowed!r}, "
            f"dispatch_disposition={decision.dispatch_disposition!r}, "
            f"reason_code={decision.reason_code!r}, "
            f"principal_digest={decision.principal_digest!r}, "
            f"grant_source_digest={decision.grant_source_digest!r}, "
            f"decision_digest={decision.decision_digest!r})"
        )
    return (
        "AuthorizationDecision("
        f"allowed={decision.allowed!r}, "
        f"reason_code={decision.reason_code!r}, "
        f"principal_digest={decision.principal_digest!r}, "
        f"grant_source_digest={decision.grant_source_digest!r}, "
        f"decision_digest={decision.decision_digest!r})"
    )


def evidence_safe_repr(evidence: CapabilityAuthorizationEvidence) -> str:
    return (
        "CapabilityAuthorizationEvidence("
        f"issuer={evidence.issuer!r}, "
        f"call_id={evidence.call_id!r}, "
        f"capability_key={evidence.capability_key!r}, "
        f"grant_source_digest={evidence.grant_source_digest!r}, "
        f"evidence_digest={evidence.evidence_digest!r})"
    )


# ---------------------------------------------------------------------------
# Evidence issuance
# ---------------------------------------------------------------------------


def compute_skill_policy_evidence_digest(
    *,
    call_id: str,
    counter: int,
    binding_contract_digest: str,
    grant_source_digest: str,
    decision_digest: str,
    effective_policy_digest: str,
    scope_digest: str,
    manifest_digest: str,
) -> str:
    """Canonical evidence digest including decision/effective-policy/grant."""
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "kind": "main_agent_authorization_evidence",
            "callId": call_id,
            "counter": counter,
            "bindingDigest": binding_contract_digest,
            "grantSourceDigest": grant_source_digest,
            "decisionDigest": decision_digest,
            "effectivePolicyDigest": effective_policy_digest,
            "scopeDigest": scope_digest,
            "manifestDigest": manifest_digest,
        }
    )


def issue_skill_policy_evidence(
    *,
    call_id: str,
    principal: CapabilityPrincipal,
    owner: CapabilityOwnerRef,
    capability_key: str,
    resolution_digest: str,
    binding_contract_digest: str,
    dependency_closure_digest: str,
    decision: AuthorizationDecisionUnion,
    grant: EffectiveCapabilityGrant | None = None,
    counter: int = 1,
    scope_digest: str,
    manifest_digest: str,
) -> CapabilityAuthorizationEvidence:
    """Issue one-time skill_policy evidence from an allowed decision.

    ``allowed_side_effects`` and ``grant_source_digest`` come from the decision
    (already frozen from EffectiveCapabilityGrant), never from descriptor.
    """
    if isinstance(decision, AuthorizationDecisionV2):
        if not decision.policy_allowed or decision.dispatch_disposition == "deny":
            raise AuthorizationEvidenceVerificationError("decision_not_allowed")
    elif not decision.allowed or decision.reason_code != "allowed":
        raise AuthorizationEvidenceVerificationError("decision_not_allowed")
    if decision.grant_source_digest is None:
        raise AuthorizationEvidenceVerificationError("missing_grant_source_digest")
    if not decision.allowed_side_effects:
        raise AuthorizationEvidenceVerificationError("empty_granted_side_effects")
    if grant is not None:
        if grant.grant_source_digest != decision.grant_source_digest:
            raise AuthorizationEvidenceVerificationError("grant_source_digest_mismatch")
        if tuple(grant.allowed_side_effects) != tuple(decision.allowed_side_effects):
            raise AuthorizationEvidenceVerificationError("granted_side_effects_mismatch")
        if grant.capability_key != capability_key:
            raise AuthorizationEvidenceVerificationError("capability_key_mismatch")
        if grant.binding_contract_digest != binding_contract_digest:
            raise AuthorizationEvidenceVerificationError("binding_contract_digest_mismatch")

    evidence_digest = compute_skill_policy_evidence_digest(
        call_id=call_id,
        counter=counter,
        binding_contract_digest=binding_contract_digest,
        grant_source_digest=decision.grant_source_digest,
        decision_digest=decision.decision_digest,
        effective_policy_digest=decision.effective_policy_digest,
        scope_digest=scope_digest,
        manifest_digest=manifest_digest,
    )
    evidence = CapabilityAuthorizationEvidence(
        issuer="skill_policy",
        call_id=call_id,
        principal=principal,
        entrypoint="main_agent",
        owner=owner,
        capability_key=capability_key,
        resolution_digest=resolution_digest,
        binding_contract_digest=binding_contract_digest,
        dependency_closure_digest=dependency_closure_digest,
        allowed_side_effects=tuple(decision.allowed_side_effects),
        grant_source_digest=decision.grant_source_digest,
        evidence_digest=evidence_digest,
    )
    # Redaction corpus: digests/reason codes only — no prose.
    assert_redaction_safe(evidence.evidence_digest, label="evidence_digest")
    assert_redaction_safe(evidence.grant_source_digest, label="grant_source_digest")
    assert_redaction_safe(decision_safe_repr(decision), label="decision_repr")
    assert_redaction_safe(evidence_safe_repr(evidence), label="evidence_repr")
    return evidence


# ---------------------------------------------------------------------------
# Composite verifier (issuer/entrypoint routing)
# ---------------------------------------------------------------------------


class CompositeAuthorizationEvidenceVerifier:
    """Route verification by (issuer, entrypoint); deny unknown combinations.

    | Issuer/entrypoint            | Verifier                          |
    |------------------------------|-----------------------------------|
    | openclaw_bridge / openclaw   | Plan 02 request-scoped verifier   |
    | skill_policy / main_agent    | Plan 05 source-aware verifier     |
    | test / test                  | injected test verifier only       |
    | any other combination        | deny                              |
    """

    def __init__(
        self,
        verifiers: Mapping[tuple[str, str], Any],
    ) -> None:
        # Snapshot mapping so callers cannot later inject a permissive verifier.
        self._verifiers = dict(verifiers)

    def verify(
        self,
        *,
        descriptor: CapabilityDescriptor,
        evidence: CapabilityAuthorizationEvidence,
        context: CapabilityExecutionContext,
    ) -> VerifiedAuthorizationEvidence:
        key = (evidence.issuer, evidence.entrypoint)
        verifier = self._verifiers.get(key)
        if verifier is None:
            raise AuthorizationEvidenceVerificationError("unknown_issuer_entrypoint")
        return verifier.verify(
            descriptor=descriptor,
            evidence=evidence,
            context=context,
        )

    def mapping(self) -> dict[tuple[str, str], Any]:
        return dict(self._verifiers)


def build_composite_verifier_mapping(
    *,
    skill_policy_verifier: Any | None = None,
    openclaw_verifier: Any | None = None,
    test_verifier: Any | None = None,
) -> dict[tuple[str, str], Any]:
    """Build the explicit Plan 05 composite verifier mapping."""
    out: dict[tuple[str, str], Any] = {}
    if openclaw_verifier is not None:
        out[("openclaw_bridge", "openclaw")] = openclaw_verifier
    if skill_policy_verifier is not None:
        out[("skill_policy", "main_agent")] = skill_policy_verifier
    if test_verifier is not None:
        out[("test", "test")] = test_verifier
    return out


__all__ = [
    "CompositeAuthorizationEvidenceVerifier",
    "SkillPolicySourceAwareEvidenceVerifier",
    "assert_redaction_safe",
    "build_composite_verifier_mapping",
    "compute_skill_policy_evidence_digest",
    "decision_safe_repr",
    "evidence_safe_repr",
    "issue_skill_policy_evidence",
]

"""Deny-by-default Capability policy engine (Plan 02 Task 7).

Authorization is independent of catalog visibility/publication and of
descriptor classification: a verifier supplies an independent side-effect
ceiling, and the engine checks the classified actual effect against that
ceiling after exact identity matching.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from app.assistant.capabilities.contracts import (
    CapabilityAuthorizationEvidence,
    CapabilityDescriptor,
    CapabilityEntrypoint,
    CapabilityExecutionContext,
    CapabilityOwnerRef,
    CapabilityPolicyDecision,
    CapabilityPrincipal,
    EvidenceVerifierKey,
    FrozenContract,
    SideEffectClass,
    VerifiedAuthorizationEvidence,
)
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.workflow.engine.runtime_dependency_resolver import (
    MAX_CAPABILITY_NESTING_DEPTH,
)
from app.config import get_settings

# ---------------------------------------------------------------------------
# Side-effect lattice prefixes (ordered, no ``unknown`` / no durable interrupt)
# ---------------------------------------------------------------------------

_SIDE_EFFECT_ORDER: tuple[SideEffectClass, ...] = (
    "none",
    "compute",
    "read",
    "draft",
    "write_local",
    "write_external",
)


def lattice_prefix_through(maximum: SideEffectClass) -> tuple[SideEffectClass, ...]:
    """Return the ordered lattice prefix through ``maximum`` (exclusive of unknown)."""
    if maximum == "unknown":
        raise ValueError("unknown cannot be a ceiling maximum")
    if maximum not in _SIDE_EFFECT_ORDER:
        raise ValueError(f"unknown side-effect maximum: {maximum}")
    idx = _SIDE_EFFECT_ORDER.index(maximum)
    return _SIDE_EFFECT_ORDER[: idx + 1]


# ---------------------------------------------------------------------------
# OpenClaw independent effect ceilings (secret-free; used by Task 8 verifiers)
# ---------------------------------------------------------------------------


class OpenClawEffectCeiling(FrozenContract):
    ceiling_scope: Literal["system_item", "custom_source_type"]
    ceiling_key: str
    revision: str
    allowed_side_effects: tuple[SideEffectClass, ...]
    allowed_interrupt_modes: tuple[Literal["none", "legacy_blocking"], ...]
    ceiling_digest: str


def _ceiling_digest_payload(
    *,
    ceiling_scope: str,
    ceiling_key: str,
    revision: str,
    allowed_side_effects: Sequence[str],
    allowed_interrupt_modes: Sequence[str],
) -> dict[str, Any]:
    return {
        "ceilingScope": ceiling_scope,
        "ceilingKey": ceiling_key,
        "revision": revision,
        "allowedSideEffects": list(allowed_side_effects),
        "allowedInterruptModes": list(allowed_interrupt_modes),
    }


def build_openclaw_effect_ceiling(
    *,
    ceiling_scope: Literal["system_item", "custom_source_type"],
    ceiling_key: str,
    revision: str,
    maximum_effect: SideEffectClass,
    allowed_interrupt_modes: tuple[Literal["none", "legacy_blocking"], ...],
) -> OpenClawEffectCeiling:
    allowed = lattice_prefix_through(maximum_effect)
    if "unknown" in allowed:
        raise ValueError("ceiling must not permit unknown")
    if any(mode == "durable" for mode in allowed_interrupt_modes):  # type: ignore[comparison-overlap]
        raise ValueError("ceiling must not permit durable interrupt")
    digest = sha256_canonical_json(
        _ceiling_digest_payload(
            ceiling_scope=ceiling_scope,
            ceiling_key=ceiling_key,
            revision=revision,
            allowed_side_effects=allowed,
            allowed_interrupt_modes=allowed_interrupt_modes,
        )
    )
    return OpenClawEffectCeiling(
        ceiling_scope=ceiling_scope,
        ceiling_key=ceiling_key,
        revision=revision,
        allowed_side_effects=allowed,
        allowed_interrupt_modes=allowed_interrupt_modes,
        ceiling_digest=digest,
    )


_OPENCLAW_CEILING_REVISION = "plan02-v1"

OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS: dict[str, OpenClawEffectCeiling] = {
    key: build_openclaw_effect_ceiling(
        ceiling_scope="system_item",
        ceiling_key=key,
        revision=_OPENCLAW_CEILING_REVISION,
        maximum_effect=maximum,
        allowed_interrupt_modes=("none",),
    )
    for key, maximum in (
        ("search_entries", "read"),
        ("get_entry", "read"),
        ("query_knowledge_graph", "read"),
        ("generate_periodic_review", "read"),
    )
}

OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS: dict[str, OpenClawEffectCeiling] = {
    "tool": build_openclaw_effect_ceiling(
        ceiling_scope="custom_source_type",
        ceiling_key="tool",
        revision=_OPENCLAW_CEILING_REVISION,
        maximum_effect="write_external",
        allowed_interrupt_modes=("none",),
    ),
    "workflow": build_openclaw_effect_ceiling(
        ceiling_scope="custom_source_type",
        ceiling_key="workflow",
        revision=_OPENCLAW_CEILING_REVISION,
        maximum_effect="write_external",
        allowed_interrupt_modes=("none", "legacy_blocking"),
    ),
    "agent": build_openclaw_effect_ceiling(
        ceiling_scope="custom_source_type",
        ceiling_key="agent",
        revision=_OPENCLAW_CEILING_REVISION,
        maximum_effect="write_external",
        allowed_interrupt_modes=("none", "legacy_blocking"),
    ),
}


def grant_source_digest_for_ceiling(
    ceiling: OpenClawEffectCeiling,
    *,
    exposure_digest: str,
) -> str:
    """Digest covering ceiling revision/row plus exact catalog exposure evidence."""
    return sha256_canonical_json(
        {
            "ceilingDigest": ceiling.ceiling_digest,
            "ceilingRevision": ceiling.revision,
            "ceilingScope": ceiling.ceiling_scope,
            "ceilingKey": ceiling.ceiling_key,
            "exposureDigest": exposure_digest,
        }
    )


# ---------------------------------------------------------------------------
# Single-use dispatch permit
# ---------------------------------------------------------------------------


class AtomicSingleUseDispatchPermit:
    """Threading-locked single-use permit; copying IDs cannot recreate authority."""

    def __init__(self, *, permit_id: str | None = None) -> None:
        self.permit_id = permit_id or str(uuid.uuid4())
        self._lock = threading.Lock()
        self._consumed = False
        self._consumed_call_id: str | None = None
        self._consumed_descriptor_digest: str | None = None

    def consume(self, *, call_id: str, descriptor_digest: str) -> None:
        if not isinstance(call_id, str) or not call_id.strip():
            raise ValueError("call_id must be non-empty")
        if not isinstance(descriptor_digest, str) or len(descriptor_digest) != 64:
            raise ValueError("descriptor_digest must be a 64-char digest")
        with self._lock:
            if self._consumed:
                raise PermissionError("dispatch permit already consumed")
            self._consumed = True
            self._consumed_call_id = call_id
            self._consumed_descriptor_digest = descriptor_digest

    @property
    def consumed(self) -> bool:
        with self._lock:
            return self._consumed


# ---------------------------------------------------------------------------
# Verifier protocol
# ---------------------------------------------------------------------------


class AuthorizationEvidenceVerifier(Protocol):
    """Request-scoped trusted verifier. Mapping key alone is not authority."""

    def verify(
        self,
        *,
        descriptor: CapabilityDescriptor,
        evidence: CapabilityAuthorizationEvidence,
        context: CapabilityExecutionContext,
    ) -> VerifiedAuthorizationEvidence: ...


class AuthorizationEvidenceVerificationError(Exception):
    """Verifier rejection with a safe reason code (no secrets/input/config)."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


# ---------------------------------------------------------------------------
# Decision digests
# ---------------------------------------------------------------------------


def _principal_payload(principal: CapabilityPrincipal) -> dict[str, Any]:
    return {
        "principalType": principal.principal_type,
        "principalId": principal.principal_id,
        "authenticated": principal.authenticated,
    }


def _owner_payload(owner: CapabilityOwnerRef) -> dict[str, Any]:
    return {
        "ownerKind": owner.owner_kind,
        "ownerId": owner.owner_id,
        "ownerVersionId": (
            str(owner.owner_version_id) if owner.owner_version_id is not None else None
        ),
    }


def compute_decision_digest(
    *,
    allowed: bool,
    reason_code: str,
    call_id: str,
    principal: CapabilityPrincipal,
    entrypoint: CapabilityEntrypoint,
    owner: CapabilityOwnerRef,
    capability_key: str,
    resolution_digest: str,
    binding_contract_digest: str,
    dependency_closure_digest: str,
    descriptor_digest: str,
    actual_side_effect: SideEffectClass | None,
    classification_ruleset_digest: str,
    verifier_key: EvidenceVerifierKey | None,
    grant_source_digest: str,
    evidence_digest: str,
    granted_side_effects: tuple[SideEffectClass, ...],
) -> str:
    return sha256_canonical_json(
        {
            "allowed": allowed,
            "reasonCode": reason_code,
            "callId": call_id,
            "principal": _principal_payload(principal),
            "entrypoint": entrypoint,
            "owner": _owner_payload(owner),
            "capabilityKey": capability_key,
            "resolutionDigest": resolution_digest,
            "bindingContractDigest": binding_contract_digest,
            "dependencyClosureDigest": dependency_closure_digest,
            "descriptorDigest": descriptor_digest,
            "actualSideEffect": actual_side_effect,
            "classificationRulesetDigest": classification_ruleset_digest,
            "verifierKey": list(verifier_key) if verifier_key is not None else None,
            "grantSourceDigest": grant_source_digest,
            "evidenceDigest": evidence_digest,
            "grantedSideEffects": list(granted_side_effects),
        }
    )


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------


class CapabilityPolicyEngine:
    """Minimum deny-by-default policy. Adapters never authorize themselves."""

    def __init__(
        self,
        verifiers: Mapping[EvidenceVerifierKey, AuthorizationEvidenceVerifier],
        *,
        durable_interrupts_enabled: bool | None = None,
    ) -> None:
        # Snapshot the mapping so callers cannot later inject a permissive verifier.
        self._verifiers: dict[EvidenceVerifierKey, AuthorizationEvidenceVerifier] = dict(
            verifiers
        )
        self.durable_interrupts_enabled = (
            bool(get_settings().assistant_durable_interrupts_enabled)
            if durable_interrupts_enabled is None
            else bool(durable_interrupts_enabled)
        )

    def authorize(
        self,
        *,
        descriptor: CapabilityDescriptor,
        evidence: CapabilityAuthorizationEvidence,
        context: CapabilityExecutionContext,
    ) -> CapabilityPolicyDecision:
        # 1) Nesting depth before anything that could construct children/models.
        nesting = context.nesting_depth
        if nesting < 0 or nesting > MAX_CAPABILITY_NESTING_DEPTH:
            return self._deny(
                reason_code="invalid_nesting_depth",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=evidence.principal,
                entrypoint=evidence.entrypoint,
                owner=evidence.owner,
                verifier_key=None,
                granted_side_effects=(),
            )

        principal = evidence.principal
        if not principal.authenticated:
            return self._deny(
                reason_code="unauthenticated_principal",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=evidence.owner,
                verifier_key=None,
                granted_side_effects=(),
            )

        # 2) Unknown issuer/entrypoint combination (mapping key alone is not authority).
        verifier_key: EvidenceVerifierKey = (evidence.issuer, evidence.entrypoint)
        verifier = self._verifiers.get(verifier_key)
        if verifier is None:
            # main_agent / workflow / agent have no production verifier in Plan 02.
            reason = (
                "main_agent_denied"
                if evidence.entrypoint == "main_agent"
                else "unknown_issuer_entrypoint"
                if evidence.issuer not in {"openclaw_bridge", "test", "system", "skill_policy"}
                else "missing_verifier"
            )
            return self._deny(
                reason_code=reason,
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=evidence.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )

        # 3) call_id equality with execution context.
        if evidence.call_id != context.call_id:
            return self._deny(
                reason_code="call_id_mismatch",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=evidence.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )

        # 4) Trusted entrypoint verifier (request-scoped / single-use).
        try:
            verified = verifier.verify(
                descriptor=descriptor,
                evidence=evidence,
                context=context,
            )
        except AuthorizationEvidenceVerificationError as exc:
            return self._deny(
                reason_code=exc.reason_code,
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=evidence.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )
        except Exception:
            return self._deny(
                reason_code="evidence_verification_failed",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=evidence.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )

        # 5) Exact equality of owner / capability key / digests.
        # Policy does not invent an owner from the descriptor; the verifier's owner
        # must match the evidence owner. Capability key / digests must match descriptor.
        if (
            verified.owner.owner_kind != evidence.owner.owner_kind
            or verified.owner.owner_id != evidence.owner.owner_id
            or verified.owner.owner_version_id != evidence.owner.owner_version_id
        ):
            return self._deny(
                reason_code="owner_mismatch",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=evidence.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )
        if verified.capability_key != descriptor.capability_key:
            return self._deny(
                reason_code="capability_key_mismatch",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )
        if evidence.capability_key != descriptor.capability_key:
            return self._deny(
                reason_code="capability_key_mismatch",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )
        if (
            verified.resolution_digest != descriptor.resolution_digest
            or evidence.resolution_digest != descriptor.resolution_digest
        ):
            return self._deny(
                reason_code="resolution_digest_mismatch",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )
        if (
            verified.binding_contract_digest != descriptor.binding_contract_digest
            or evidence.binding_contract_digest != descriptor.binding_contract_digest
        ):
            return self._deny(
                reason_code="binding_contract_digest_mismatch",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )
        if (
            verified.dependency_closure_digest != descriptor.dependency_closure_digest
            or evidence.dependency_closure_digest != descriptor.dependency_closure_digest
        ):
            return self._deny(
                reason_code="dependency_closure_digest_mismatch",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )
        if verified.evidence_digest != evidence.evidence_digest:
            return self._deny(
                reason_code="evidence_digest_mismatch",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )
        if verified.call_id != context.call_id:
            return self._deny(
                reason_code="call_id_mismatch",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )
        if verified.verifier_key != verifier_key:
            return self._deny(
                reason_code="forged_verifier_evidence",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=(),
            )

        # 6) Descriptor availability.
        if descriptor.availability.status != "available":
            status = descriptor.availability.status
            reason = (
                "target_version_drift"
                if status == "version_drift"
                else "target_disabled"
                if status == "disabled"
                else "target_missing"
                if status == "missing"
                else "target_unavailable"
            )
            return self._deny(
                reason_code=reason,
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=tuple(verified.allowed_side_effects),
                grant_source_digest=verified.grant_source_digest,
            )

        actual_effect: SideEffectClass = descriptor.behavior.side_effect
        granted = tuple(verified.allowed_side_effects)

        # 8) Reject unknown even if a malformed grant lists it.
        if actual_effect == "unknown" or "unknown" in granted:
            return self._deny(
                reason_code="unknown_side_effect_denied",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=granted,
                grant_source_digest=verified.grant_source_digest,
                actual_side_effect=actual_effect,
            )

        # 7) Actual classified effect must be a member of independent ceiling.
        if actual_effect not in granted:
            return self._deny(
                reason_code="side_effect_not_granted",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=granted,
                grant_source_digest=verified.grant_source_digest,
                actual_side_effect=actual_effect,
            )

        # 9) Durable interrupts are available only to the trusted Main Agent
        # verifier and only behind the new-admission feature gate. Recovery of an
        # already-persisted v2 Run happens in the worker and does not pass here.
        if descriptor.behavior.interrupt_mode == "durable":
            durable_reason: str | None = None
            if not self.durable_interrupts_enabled:
                durable_reason = "durable_interrupts_disabled"
            elif verifier_key != ("skill_policy", "main_agent"):
                durable_reason = "durable_interrupt_denied"
            elif descriptor.capability_type not in {"workflow", "agent"}:
                durable_reason = "durable_interrupt_denied"
            elif descriptor.target_version_id is None:
                durable_reason = "durable_interrupt_unversioned"
            elif actual_effect not in {"none", "read", "compute"}:
                durable_reason = "durable_interrupt_effect_denied"
            elif descriptor.behavior.parallel_safe:
                durable_reason = "durable_interrupt_parallel_denied"
            if durable_reason is not None:
                return self._deny(
                    reason_code=durable_reason,
                    descriptor=descriptor,
                    evidence=evidence,
                    context=context,
                    principal=principal,
                    entrypoint=evidence.entrypoint,
                    owner=verified.owner,
                    verifier_key=verifier_key,
                    granted_side_effects=granted,
                    grant_source_digest=verified.grant_source_digest,
                    actual_side_effect=actual_effect,
                )

        if verified.dispatch_permit is None:
            return self._deny(
                reason_code="dispatch_permit_missing",
                descriptor=descriptor,
                evidence=evidence,
                context=context,
                principal=principal,
                entrypoint=evidence.entrypoint,
                owner=verified.owner,
                verifier_key=verifier_key,
                granted_side_effects=granted,
                grant_source_digest=verified.grant_source_digest,
                actual_side_effect=actual_effect,
            )

        # 10) Immutable allow decision.
        reason_code = "allow"
        decision_digest = compute_decision_digest(
            allowed=True,
            reason_code=reason_code,
            call_id=context.call_id,
            principal=principal,
            entrypoint=evidence.entrypoint,
            owner=verified.owner,
            capability_key=descriptor.capability_key,
            resolution_digest=descriptor.resolution_digest,
            binding_contract_digest=descriptor.binding_contract_digest,
            dependency_closure_digest=descriptor.dependency_closure_digest,
            descriptor_digest=descriptor.descriptor_digest,
            actual_side_effect=actual_effect,
            classification_ruleset_digest=descriptor.behavior.classification.ruleset_digest,
            verifier_key=verifier_key,
            grant_source_digest=verified.grant_source_digest,
            evidence_digest=evidence.evidence_digest,
            granted_side_effects=granted,
        )
        return CapabilityPolicyDecision(
            allowed=True,
            reason_code=reason_code,
            call_id=context.call_id,
            descriptor_digest=descriptor.descriptor_digest,
            classification_ruleset_digest=descriptor.behavior.classification.ruleset_digest,
            evidence_digest=evidence.evidence_digest,
            owner=verified.owner,
            granted_side_effects=granted,
            grant_source_digest=verified.grant_source_digest,
            decision_digest=decision_digest,
            dispatch_permit=verified.dispatch_permit,
        )

    def _deny(
        self,
        *,
        reason_code: str,
        descriptor: CapabilityDescriptor,
        evidence: CapabilityAuthorizationEvidence,
        context: CapabilityExecutionContext,
        principal: CapabilityPrincipal,
        entrypoint: CapabilityEntrypoint,
        owner: CapabilityOwnerRef,
        verifier_key: EvidenceVerifierKey | None,
        granted_side_effects: tuple[SideEffectClass, ...],
        grant_source_digest: str | None = None,
        actual_side_effect: SideEffectClass | None = None,
    ) -> CapabilityPolicyDecision:
        # Safe reason only — never include target input/config/secrets.
        safe_reason = reason_code if reason_code and len(reason_code) <= 64 else "denied"
        grant_digest = grant_source_digest or evidence.grant_source_digest
        decision_digest = compute_decision_digest(
            allowed=False,
            reason_code=safe_reason,
            call_id=context.call_id,
            principal=principal,
            entrypoint=entrypoint,
            owner=owner,
            capability_key=descriptor.capability_key,
            resolution_digest=descriptor.resolution_digest,
            binding_contract_digest=descriptor.binding_contract_digest,
            dependency_closure_digest=descriptor.dependency_closure_digest,
            descriptor_digest=descriptor.descriptor_digest,
            actual_side_effect=actual_side_effect or descriptor.behavior.side_effect,
            classification_ruleset_digest=descriptor.behavior.classification.ruleset_digest,
            verifier_key=verifier_key,
            grant_source_digest=grant_digest,
            evidence_digest=evidence.evidence_digest,
            granted_side_effects=granted_side_effects,
        )
        return CapabilityPolicyDecision(
            allowed=False,
            reason_code=safe_reason,
            call_id=context.call_id,
            descriptor_digest=descriptor.descriptor_digest,
            classification_ruleset_digest=descriptor.behavior.classification.ruleset_digest,
            evidence_digest=evidence.evidence_digest,
            owner=owner,
            granted_side_effects=granted_side_effects,
            grant_source_digest=grant_digest,
            decision_digest=decision_digest,
            dispatch_permit=None,
        )


def build_composite_evidence_verifiers(
    *,
    skill_policy_verifier: AuthorizationEvidenceVerifier | None = None,
    openclaw_verifier: AuthorizationEvidenceVerifier | None = None,
    test_verifier: AuthorizationEvidenceVerifier | None = None,
) -> dict[EvidenceVerifierKey, AuthorizationEvidenceVerifier]:
    """Explicit Plan 05 composite verifier mapping (issuer/entrypoint → verifier).

    | Issuer/entrypoint            | Verifier                          |
    |------------------------------|-----------------------------------|
    | openclaw_bridge / openclaw   | unchanged Plan 02 verifier        |
    | skill_policy / main_agent    | Plan 05 source-aware verifier     |
    | test / test                  | injected test verifier only       |
    | any other combination        | deny (missing mapping)            |
    """
    out: dict[EvidenceVerifierKey, AuthorizationEvidenceVerifier] = {}
    if openclaw_verifier is not None:
        out[("openclaw_bridge", "openclaw")] = openclaw_verifier
    if skill_policy_verifier is not None:
        out[("skill_policy", "main_agent")] = skill_policy_verifier
    if test_verifier is not None:
        out[("test", "test")] = test_verifier
    return out


__all__ = [
    "AtomicSingleUseDispatchPermit",
    "AuthorizationEvidenceVerificationError",
    "AuthorizationEvidenceVerifier",
    "CapabilityPolicyEngine",
    "MAX_CAPABILITY_NESTING_DEPTH",
    "OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS",
    "OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS",
    "OpenClawEffectCeiling",
    "build_composite_evidence_verifiers",
    "build_openclaw_effect_ceiling",
    "compute_decision_digest",
    "grant_source_digest_for_ceiling",
    "lattice_prefix_through",
]

"""Plan 05 pure ordered authorization evaluator.

Derives EffectiveCapabilityGrant from independent platform/entrypoint/global/
owner sources before inspecting descriptor behavior. Produces an immutable
AuthorizationDecision with stable reason codes (Plan 05 §5.4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.assistant.capabilities.contracts import (
    CapabilityDescriptor,
    CapabilityPrincipal,
    SideEffectClass,
)
from app.assistant.main_agent.authorization import (
    LOCAL_ASSISTANT_PRINCIPAL,
    MAIN_AGENT_CEILING_REVISION,
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
    map_author_side_effects,
)
from app.assistant.policy.contracts import (
    ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST,
    EMPTY_POLICY_DIGEST,
    AuthorizationDecision,
    CapabilityExposureRef,
    EffectiveCapabilityGrant,
    EffectiveRunPolicySnapshot,
    ManifestExposureIndex,
    PLAN05_RELEASE_GATE_SIDE_EFFECTS,
    PolicyOwnerKind,
    build_authorization_decision,
    build_effective_capability_grant,
    compute_principal_digest,
)

# ---------------------------------------------------------------------------
# Input views (pure; no secrets/prompts/arguments)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OwnerGrantMaterial:
    """Immutable owner declaration used to derive an independent grant.

    ``author_allowed_side_effects`` uses Plan 01 vocabulary for skill owners
    (read/compute/write/draft/control) and lattice classes for main_agent.
    ``declared_capability_keys`` is None when every exposure owned by this
    owner is considered declared (typical for main_agent Profile controls).
    """

    owner_kind: PolicyOwnerKind
    owner_id: str
    owner_version_id: UUID
    policy_digest: str
    author_allowed_side_effects: tuple[str, ...]
    declared_capability_keys: frozenset[str] | None = None
    # Instruction-only Skills declare no capability bindings.
    is_instruction_only: bool = False


@dataclass(frozen=True, slots=True)
class GlobalPolicyView:
    """Deny-by-default global policy (identity + optional named denials only)."""

    policy_digest: str
    deny_by_default: bool = True
    denied_capability_keys: frozenset[str] = frozenset()
    denied_side_effects: frozenset[str] = frozenset()
    # When deny_by_default is True, only exposures in the snapshot are admitted;
    # this view may additionally deny named keys/classes.


@dataclass(frozen=True, slots=True)
class AuthorizationProposal:
    """Identity-only proposed call facts for pure evaluation.

    Never carries prompts, arguments, results, secrets, URLs, or exception text.
    """

    run_id: UUID
    conversation_id: UUID | None
    scope_digest: str
    expected_scope_digest: str
    expected_run_id: UUID
    expected_conversation_id: UUID | None
    manifest_digest: str
    expected_manifest_digest: str
    capability_key: str
    binding_contract_digest: str
    resolution_digest: str
    dependency_closure_digest: str
    descriptor_digest: str
    descriptor_side_effect: SideEffectClass
    descriptor_interrupt_mode: str
    descriptor_availability_status: str
    principal: CapabilityPrincipal
    nesting_depth: int
    max_capability_depth: int
    # Optional claimed owner (must match exposure owner when provided).
    claimed_owner_kind: PolicyOwnerKind | None = None
    claimed_owner_id: str | None = None
    claimed_owner_version_id: UUID | None = None
    # Entrypoint identity for principal/entrypoint gates.
    entrypoint: Literal["main_agent"] = "main_agent"


# ---------------------------------------------------------------------------
# Grant derivation (before descriptor inspection)
# ---------------------------------------------------------------------------


def _intersect_lattice(
    *,
    ceiling_effects: Sequence[SideEffectClass],
    candidate: Sequence[SideEffectClass],
) -> tuple[SideEffectClass, ...]:
    """Preserve lattice order while intersecting with the platform ceiling."""
    allowed = set(candidate)
    return tuple(effect for effect in ceiling_effects if effect in allowed)


def derive_effective_capability_grant(
    *,
    owner: OwnerGrantMaterial,
    capability_key: str,
    binding_contract_digest: str,
    entrypoint_policy_digest: str = ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST,
    global_policy_digest: str,
    global_policy: GlobalPolicyView | None = None,
    denied_side_effects: frozenset[str] | Sequence[str] | None = None,
    platform_ceiling_digest: str = MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
    platform_ceiling_revision: str = MAIN_AGENT_CEILING_REVISION,
    release_gate: Sequence[str] = PLAN05_RELEASE_GATE_SIDE_EFFECTS,
) -> EffectiveCapabilityGrant | None:
    """Derive an independent grant for the exact owner/binding.

    Lattice = platform prefix ∩ entrypoint/release gate ∩ published Profile
    global policy (denied side-effect classes) ∩ owner declaration.
    ``grant_source_digest`` reflects the post-intersection grant.

    Returns None when the intersection is empty (caller maps to a deny code).
    Never reads descriptor.behavior.
    """
    ceiling = MAIN_AGENT_READ_ONLY_EFFECT_CEILING
    # Platform prefix ∩ entrypoint/release gate (identical lattice for Plan 05).
    platform_prefix = tuple(
        effect
        for effect in ceiling.allowed_side_effects
        if effect in set(release_gate) and effect in set(ceiling.allowed_side_effects)
    )
    if owner.owner_kind == "main_agent":
        # Main Agent grants: platform prefix (none|compute|read). ``none`` is
        # admitted by the explicit Main Agent entrypoint rule.
        candidate: tuple[SideEffectClass, ...] = platform_prefix
    else:
        # Skill: map author declaration into lattice, then intersect with the
        # platform prefix. ``none`` is admitted by the Main Agent entrypoint
        # rule (Plan 04 parity via intersect_with_platform_ceiling).
        if owner.is_instruction_only or not owner.author_allowed_side_effects:
            return None
        try:
            author_lattice = map_author_side_effects(owner.author_allowed_side_effects)
        except ValueError:
            return None
        author_with_none: tuple[SideEffectClass, ...] = tuple(
            dict.fromkeys(("none", *author_lattice))
        )
        candidate = _intersect_lattice(
            ceiling_effects=platform_prefix,
            candidate=author_with_none,
        )

    # Published Profile global policy ∩ candidate (Plan §5.3).
    denied: frozenset[str]
    if denied_side_effects is not None:
        denied = frozenset(denied_side_effects)
    elif global_policy is not None:
        denied = frozenset(global_policy.denied_side_effects)
    else:
        denied = frozenset()
    if denied:
        candidate = tuple(effect for effect in candidate if effect not in denied)

    if not candidate:
        return None
    return build_effective_capability_grant(
        owner_kind=owner.owner_kind,
        owner_id=owner.owner_id,
        owner_version_id=owner.owner_version_id,
        capability_key=capability_key,
        binding_contract_digest=binding_contract_digest,
        allowed_side_effects=candidate,
        allowed_interrupt_modes=ceiling.allowed_interrupt_modes,
        platform_ceiling_digest=platform_ceiling_digest,
        platform_ceiling_revision=platform_ceiling_revision,
        entrypoint_policy_digest=entrypoint_policy_digest,
        global_policy_digest=global_policy_digest,
        owner_policy_digest=owner.policy_digest,
    )


# ---------------------------------------------------------------------------
# Exposure resolution
# ---------------------------------------------------------------------------


def _find_exposures_for_key(
    index: ManifestExposureIndex,
    *,
    capability_key: str,
) -> tuple[CapabilityExposureRef, ...]:
    return tuple(
        item for item in index.exposures if item.domain_key == capability_key
    )


def _lookup_owner_material(
    owners: Mapping[tuple[PolicyOwnerKind, str, UUID], OwnerGrantMaterial],
    *,
    owner_kind: PolicyOwnerKind,
    owner_id: str,
    owner_version_id: UUID,
) -> OwnerGrantMaterial | None:
    return owners.get((owner_kind, owner_id, owner_version_id))


# ---------------------------------------------------------------------------
# Pure ordered evaluator
# ---------------------------------------------------------------------------


def evaluate_authorization(
    *,
    snapshot: EffectiveRunPolicySnapshot,
    proposal: AuthorizationProposal,
    owner_materials: Mapping[tuple[PolicyOwnerKind, str, UUID], OwnerGrantMaterial],
    global_policy: GlobalPolicyView | None = None,
) -> AuthorizationDecision:
    """Evaluate a proposed call against a frozen EffectiveRunPolicySnapshot.

    Stable deny order (Plan 05 §5.4):
    scope_mismatch → manifest_surface_mismatch → exposure_missing →
    exposure_ambiguous → owner_mismatch → principal_unauthenticated →
    principal_not_allowed → entrypoint_not_allowed → global_policy_denied →
    owner_capability_not_declared → owner_side_effect_denied →
    release_gate_denied → target_unavailable → version_or_digest_drift →
    recursion_denied → allowed.
    """
    principal_digest = compute_principal_digest(proposal.principal)
    entrypoint_digest = snapshot.entrypoint_policy_digest
    global_digest = (
        global_policy.policy_digest
        if global_policy is not None
        else snapshot.global_policy_digest
    )
    effective_digest = snapshot.effective_policy_digest

    def _deny(
        reason_code: str,
        *,
        owner_policy_digest: str = EMPTY_POLICY_DIGEST,
        exposure_digest: str = EMPTY_POLICY_DIGEST,
        allowed_side_effects: tuple[SideEffectClass, ...] = (),
        grant_source_digest: str | None = None,
    ) -> AuthorizationDecision:
        return build_authorization_decision(
            allowed=False,
            reason_code=reason_code,
            principal_digest=principal_digest,
            entrypoint_policy_digest=entrypoint_digest,
            global_policy_digest=global_digest,
            owner_policy_digest=owner_policy_digest,
            allowed_side_effects=allowed_side_effects,
            grant_source_digest=grant_source_digest,
            exposure_digest=exposure_digest,
            effective_policy_digest=effective_digest,
        )

    # 1) scope_mismatch
    if (
        proposal.scope_digest != proposal.expected_scope_digest
        or proposal.run_id != proposal.expected_run_id
        or proposal.conversation_id != proposal.expected_conversation_id
        or proposal.run_id != snapshot.run_id
    ):
        return _deny("scope_mismatch")

    # 2) manifest_surface_mismatch
    if (
        proposal.manifest_digest != proposal.expected_manifest_digest
        or proposal.manifest_digest != snapshot.exposure_index.manifest_digest
    ):
        return _deny("manifest_surface_mismatch")

    # 3/4) exposure_missing / exposure_ambiguous
    matches = _find_exposures_for_key(
        snapshot.exposure_index, capability_key=proposal.capability_key
    )
    if not matches:
        return _deny("exposure_missing")
    if len(matches) > 1:
        # Index construction rejects duplicates; treat multi-match as ambiguous.
        return _deny("exposure_ambiguous")
    exposure = matches[0]
    # Binding identity must match the exact exposed binding (else ambiguous/stale).
    if exposure.binding_contract_digest != proposal.binding_contract_digest:
        return _deny("exposure_ambiguous", exposure_digest=exposure.exposure_digest)

    # 5) owner_mismatch
    if proposal.claimed_owner_kind is not None:
        if (
            proposal.claimed_owner_kind != exposure.owner_kind
            or proposal.claimed_owner_id != exposure.owner_id
            or proposal.claimed_owner_version_id != exposure.owner_version_id
        ):
            return _deny("owner_mismatch", exposure_digest=exposure.exposure_digest)
    owner = _lookup_owner_material(
        owner_materials,
        owner_kind=exposure.owner_kind,
        owner_id=exposure.owner_id,
        owner_version_id=exposure.owner_version_id,
    )
    if owner is None:
        return _deny("owner_mismatch", exposure_digest=exposure.exposure_digest)

    # 6) principal_unauthenticated
    if not proposal.principal.authenticated:
        return _deny(
            "principal_unauthenticated",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
        )

    # 7) principal_not_allowed — only LOCAL_ASSISTANT_PRINCIPAL for assistant_chat.
    if (
        proposal.principal.principal_type != LOCAL_ASSISTANT_PRINCIPAL.principal_type
        or proposal.principal.principal_id != LOCAL_ASSISTANT_PRINCIPAL.principal_id
        or proposal.principal.authenticated is not True
    ):
        return _deny(
            "principal_not_allowed",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
        )

    # 8) entrypoint_not_allowed
    if proposal.entrypoint != "main_agent" or snapshot.entrypoint != "main_agent":
        return _deny(
            "entrypoint_not_allowed",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
        )
    if entrypoint_digest != ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST:
        return _deny(
            "entrypoint_not_allowed",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
        )

    # 9) global_policy_denied — named capability-key denials + digest match only.
    # Descriptor side-effect membership is inspected only after grant freeze.
    gp = global_policy or GlobalPolicyView(policy_digest=global_digest)
    if proposal.capability_key in gp.denied_capability_keys:
        return _deny(
            "global_policy_denied",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
        )
    # Deny-by-default: exposure membership is required (already checked).
    # Global digest must match snapshot.
    if gp.policy_digest != snapshot.global_policy_digest:
        return _deny(
            "global_policy_denied",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
        )

    # 10) owner_capability_not_declared
    if owner.is_instruction_only:
        return _deny(
            "owner_capability_not_declared",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
        )
    if owner.declared_capability_keys is not None:
        if proposal.capability_key not in owner.declared_capability_keys:
            return _deny(
                "owner_capability_not_declared",
                owner_policy_digest=owner.policy_digest,
                exposure_digest=exposure.exposure_digest,
            )
    # Owner must own this exposure (identity already matched via lookup key).
    if (
        owner.owner_kind != exposure.owner_kind
        or owner.owner_id != exposure.owner_id
        or owner.owner_version_id != exposure.owner_version_id
    ):
        return _deny(
            "owner_capability_not_declared",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
        )

    # --- Independent grant derivation (before descriptor membership tests) ---
    # Platform ∩ entrypoint ∩ release gate ∩ published global policy ∩ owner.
    grant = derive_effective_capability_grant(
        owner=owner,
        capability_key=proposal.capability_key,
        binding_contract_digest=proposal.binding_contract_digest,
        entrypoint_policy_digest=entrypoint_digest,
        global_policy_digest=global_digest,
        global_policy=gp,
    )
    if grant is None:
        # Empty intersection after global/owner clipping. Prefer global code
        # when every platform class is globally denied; else owner denial.
        if set(MAIN_AGENT_READ_ONLY_EFFECT_CEILING.allowed_side_effects) <= set(
            gp.denied_side_effects
        ):
            return _deny(
                "global_policy_denied",
                owner_policy_digest=owner.policy_digest,
                exposure_digest=exposure.exposure_digest,
            )
        return _deny(
            "owner_side_effect_denied",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
        )

    # 11) Descriptor membership against the frozen grant.
    # If effect ∉ grant and effect ∈ global denied classes → global_policy_denied;
    # else if effect ∉ grant → owner_side_effect_denied.
    actual = proposal.descriptor_side_effect
    if actual == "unknown" or actual not in grant.allowed_side_effects:
        if actual != "unknown" and actual in gp.denied_side_effects:
            return _deny(
                "global_policy_denied",
                owner_policy_digest=owner.policy_digest,
                exposure_digest=exposure.exposure_digest,
                allowed_side_effects=grant.allowed_side_effects,
                grant_source_digest=grant.grant_source_digest,
            )
        return _deny(
            "owner_side_effect_denied",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
            # Grant was derived; carry it for audit without widening membership.
            allowed_side_effects=grant.allowed_side_effects,
            grant_source_digest=grant.grant_source_digest,
        )
    interrupt = proposal.descriptor_interrupt_mode or "none"
    if interrupt not in set(grant.allowed_interrupt_modes):
        return _deny(
            "owner_side_effect_denied",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
            allowed_side_effects=grant.allowed_side_effects,
            grant_source_digest=grant.grant_source_digest,
        )

    # 12) release_gate_denied
    if actual not in set(PLAN05_RELEASE_GATE_SIDE_EFFECTS):
        return _deny(
            "release_gate_denied",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
            allowed_side_effects=grant.allowed_side_effects,
            grant_source_digest=grant.grant_source_digest,
        )

    # 13) target_unavailable
    status = proposal.descriptor_availability_status
    if status != "available":
        if status in {"disabled", "missing", "unavailable"}:
            return _deny(
                "target_unavailable",
                owner_policy_digest=owner.policy_digest,
                exposure_digest=exposure.exposure_digest,
                allowed_side_effects=grant.allowed_side_effects,
                grant_source_digest=grant.grant_source_digest,
            )
        # version_drift / config drift fall through to version_or_digest_drift
        if status == "version_drift":
            return _deny(
                "version_or_digest_drift",
                owner_policy_digest=owner.policy_digest,
                exposure_digest=exposure.exposure_digest,
                allowed_side_effects=grant.allowed_side_effects,
                grant_source_digest=grant.grant_source_digest,
            )
        return _deny(
            "target_unavailable",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
            allowed_side_effects=grant.allowed_side_effects,
            grant_source_digest=grant.grant_source_digest,
        )

    # 14) version_or_digest_drift — exposure vs proposal digests
    if (
        exposure.resolved_ref.resolution_digest != proposal.resolution_digest
        or exposure.resolved_ref.dependency_closure_digest
        != proposal.dependency_closure_digest
        or exposure.descriptor_digest != proposal.descriptor_digest
        or exposure.binding_contract_digest != proposal.binding_contract_digest
    ):
        return _deny(
            "version_or_digest_drift",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
            allowed_side_effects=grant.allowed_side_effects,
            grant_source_digest=grant.grant_source_digest,
        )

    # 15) recursion_denied
    if (
        proposal.nesting_depth < 0
        or proposal.nesting_depth > proposal.max_capability_depth
        or proposal.nesting_depth > snapshot.run_budget_limits.max_capability_depth
    ):
        return _deny(
            "recursion_denied",
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
            allowed_side_effects=grant.allowed_side_effects,
            grant_source_digest=grant.grant_source_digest,
        )

    # 16) allowed
    return build_authorization_decision(
        allowed=True,
        reason_code="allowed",
        principal_digest=principal_digest,
        entrypoint_policy_digest=entrypoint_digest,
        global_policy_digest=global_digest,
        owner_policy_digest=owner.policy_digest,
        allowed_side_effects=grant.allowed_side_effects,
        grant_source_digest=grant.grant_source_digest,
        exposure_digest=exposure.exposure_digest,
        effective_policy_digest=effective_digest,
    )


def proposal_from_descriptor(
    *,
    descriptor: CapabilityDescriptor,
    run_id: UUID,
    conversation_id: UUID | None,
    scope_digest: str,
    expected_scope_digest: str,
    expected_run_id: UUID,
    expected_conversation_id: UUID | None,
    manifest_digest: str,
    expected_manifest_digest: str,
    principal: CapabilityPrincipal,
    nesting_depth: int,
    max_capability_depth: int,
    claimed_owner_kind: PolicyOwnerKind | None = None,
    claimed_owner_id: str | None = None,
    claimed_owner_version_id: UUID | None = None,
) -> AuthorizationProposal:
    """Build an AuthorizationProposal from a classified descriptor (identity only)."""
    interrupt = str(getattr(descriptor.behavior, "interrupt_mode", "none") or "none")
    status = str(descriptor.availability.status)
    return AuthorizationProposal(
        run_id=run_id,
        conversation_id=conversation_id,
        scope_digest=scope_digest,
        expected_scope_digest=expected_scope_digest,
        expected_run_id=expected_run_id,
        expected_conversation_id=expected_conversation_id,
        manifest_digest=manifest_digest,
        expected_manifest_digest=expected_manifest_digest,
        capability_key=descriptor.capability_key,
        binding_contract_digest=descriptor.binding_contract_digest,
        resolution_digest=descriptor.resolution_digest,
        dependency_closure_digest=descriptor.dependency_closure_digest,
        descriptor_digest=descriptor.descriptor_digest,
        descriptor_side_effect=descriptor.behavior.side_effect,
        descriptor_interrupt_mode=interrupt,
        descriptor_availability_status=status,
        principal=principal,
        nesting_depth=nesting_depth,
        max_capability_depth=max_capability_depth,
        claimed_owner_kind=claimed_owner_kind,
        claimed_owner_id=claimed_owner_id,
        claimed_owner_version_id=claimed_owner_version_id,
    )


def owner_material_key(
    material: OwnerGrantMaterial,
) -> tuple[PolicyOwnerKind, str, UUID]:
    return (material.owner_kind, material.owner_id, material.owner_version_id)


__all__ = [
    "AuthorizationProposal",
    "GlobalPolicyView",
    "OwnerGrantMaterial",
    "derive_effective_capability_grant",
    "evaluate_authorization",
    "owner_material_key",
    "proposal_from_descriptor",
]

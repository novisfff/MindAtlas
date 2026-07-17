"""Plan 08 write admission: policy contract v2 for one golden write_local.

V1 AuthorizationDecision bytes/digests remain unchanged. This module never
mutates Plan 05 v1 builders. Grants are still derived via
``derive_effective_capability_grant`` with an explicit release gate that may
include ``write_local`` only when a matching GoldenWriteReleaseV1 is supplied.
"""

from __future__ import annotations

from typing import Mapping, Sequence
from uuid import UUID

from app.assistant.capabilities.contracts import SideEffectClass
from app.assistant.policy.contracts import (
    GOLDEN_WRITE_LATTICE_PREFIX,
    AuthorizationDecision,
    AuthorizationDecisionV2,
    AuthorizationDecisionUnion,
    EffectiveCapabilityGrant,
    EffectiveRunPolicySnapshot,
    GoldenWriteReleaseV1,
    PLAN05_RELEASE_GATE_SIDE_EFFECTS,
    build_authorization_decision,
    build_authorization_decision_v2,
    build_golden_write_release,
)
from app.assistant.policy.evaluator import (
    AuthorizationProposal,
    GlobalPolicyView,
    OwnerGrantMaterial,
    derive_effective_capability_grant,
    evaluate_authorization,
    _find_exposures_for_key,
)
from app.assistant.main_agent.authorization import (
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
)
from app.assistant.policy.contracts import (
    ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST,
    EMPTY_POLICY_DIGEST,
    compute_principal_digest,
)


# Domain key for the sole golden create_entry binding (Task 8 freezes exact version).
GOLDEN_CREATE_ENTRY_DOMAIN_KEY = "create_entry"


def _v2_deny(
    *,
    reason_code: str,
    principal_digest: str,
    entrypoint_digest: str,
    global_digest: str,
    effective_digest: str,
    owner_policy_digest: str = EMPTY_POLICY_DIGEST,
    exposure_digest: str = EMPTY_POLICY_DIGEST,
    allowed_side_effects: tuple[SideEffectClass, ...] = (),
    grant_source_digest: str | None = None,
    write_release_digest: str | None = None,
) -> AuthorizationDecisionV2:
    return build_authorization_decision_v2(
        policy_allowed=False,
        dispatch_disposition="deny",
        reason_code=reason_code,
        principal_digest=principal_digest,
        entrypoint_policy_digest=entrypoint_digest,
        global_policy_digest=global_digest,
        owner_policy_digest=owner_policy_digest,
        allowed_side_effects=allowed_side_effects,
        grant_source_digest=grant_source_digest,
        exposure_digest=exposure_digest,
        effective_policy_digest=effective_digest,
        write_release_digest=write_release_digest,
    )


def release_matches_proposal(
    release: GoldenWriteReleaseV1,
    *,
    principal_digest: str,
    owner_kind: str,
    owner_version_id: UUID,
    binding_contract_digest: str,
    domain_key: str,
    target_version_id: UUID | None,
    target_digest: str | None,
    execution_mode: str | None,
) -> bool:
    if release.principal_digest != principal_digest:
        return False
    if release.owner_kind != owner_kind:
        return False
    if release.owner_version_id != owner_version_id:
        return False
    if release.binding_contract_digest != binding_contract_digest:
        return False
    if release.domain_key != domain_key:
        return False
    if release.target_version_id != target_version_id:
        return False
    if target_digest is not None and release.target_digest != target_digest:
        return False
    if execution_mode is not None and execution_mode != release.required_execution_mode:
        return False
    return True


def derive_golden_write_grant(
    *,
    owner: OwnerGrantMaterial,
    capability_key: str,
    binding_contract_digest: str,
    release: GoldenWriteReleaseV1,
    entrypoint_policy_digest: str = ASSISTANT_CHAT_ENTRYPOINT_POLICY_DIGEST,
    global_policy_digest: str,
    global_policy: GlobalPolicyView | None = None,
    platform_ceiling_digest: str = MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST,
) -> EffectiveCapabilityGrant | None:
    """Independently derive a grant whose lattice may include write_local.

    Uses the golden release lattice as the release gate. Still intersects with
    platform ceiling **replaced** by the release lattice for this exact binding
    only — the platform read-only ceiling is NOT mutated globally.

    Descriptor behavior is never consulted.
    """
    # Intersect golden lattice with owner author declaration via derive helper,
    # but pass release lattice as release_gate and a synthetic platform digest
    # bound into grant_source via the release digest.
    # For golden write, the platform "prefix" is the release lattice itself.
    from app.assistant.policy.evaluator import (
        _intersect_lattice,
        map_author_side_effects,
        build_effective_capability_grant,
    )
    from app.assistant.main_agent.authorization import MAIN_AGENT_CEILING_REVISION

    release_gate = release.allowed_side_effects
    platform_prefix = tuple(release_gate)

    if owner.owner_kind == "main_agent":
        # Main agent itself never receives write grants in Plan 08.
        return None
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
    denied: frozenset[str] = frozenset()
    if global_policy is not None:
        denied = frozenset(global_policy.denied_side_effects)
    if denied:
        candidate = tuple(effect for effect in candidate if effect not in denied)
    if "write_local" not in candidate:
        return None
    if not candidate:
        return None
    return build_effective_capability_grant(
        owner_kind=owner.owner_kind,
        owner_id=owner.owner_id,
        owner_version_id=owner.owner_version_id,
        capability_key=capability_key,
        binding_contract_digest=binding_contract_digest,
        allowed_side_effects=candidate,
        allowed_interrupt_modes=("none", "durable"),
        platform_ceiling_digest=platform_ceiling_digest,
        platform_ceiling_revision=MAIN_AGENT_CEILING_REVISION,
        entrypoint_policy_digest=entrypoint_policy_digest,
        global_policy_digest=global_policy_digest,
        owner_policy_digest=owner.policy_digest,
    )


def evaluate_authorization_v2(
    *,
    snapshot: EffectiveRunPolicySnapshot,
    proposal: AuthorizationProposal,
    owner_materials: Mapping[tuple[str, str, UUID], OwnerGrantMaterial],
    global_policy: GlobalPolicyView | None = None,
    golden_write_release: GoldenWriteReleaseV1 | None = None,
    descriptor_side_effect: SideEffectClass | None = None,
    execution_mode: str | None = None,
    target_digest: str | None = None,
    target_version_id: UUID | None = None,
    policy_contract_version: int = 1,
) -> AuthorizationDecisionUnion:
    """Evaluate authorization under v1 or v2 policy contract.

    - v1 (default): delegates to Plan 05 ``evaluate_authorization`` (read-only).
    - v2 without matching golden release: same deny surface as v1 for writes.
    - v2 with exact golden match + write_local descriptor: returns
      ``awaiting_call_approval`` without executable Gateway disposition.
    """
    if policy_contract_version <= 1 or golden_write_release is None:
        # Pure v1 path — byte-compatible.
        return evaluate_authorization(
            snapshot=snapshot,
            proposal=proposal,
            owner_materials=owner_materials,  # type: ignore[arg-type]
            global_policy=global_policy,
        )

    principal_digest = compute_principal_digest(proposal.principal)
    entrypoint_digest = snapshot.entrypoint_policy_digest
    global_digest = (
        global_policy.policy_digest
        if global_policy is not None
        else snapshot.global_policy_digest
    )
    effective_digest = snapshot.effective_policy_digest

    # First apply the ordinary v1 evaluation for non-write / structural denies.
    # If the proposal is a non-write that v1 allows, return v2 dispatch.
    v1 = evaluate_authorization(
        snapshot=snapshot,
        proposal=proposal,
        owner_materials=owner_materials,  # type: ignore[arg-type]
        global_policy=global_policy,
    )

    actual_effect = descriptor_side_effect
    if actual_effect is None:
        # Without a classified effect, never upgrade to write.
        if v1.allowed:
            return build_authorization_decision_v2(
                policy_allowed=True,
                dispatch_disposition="dispatch",
                reason_code="allowed",
                principal_digest=v1.principal_digest,
                entrypoint_policy_digest=v1.entrypoint_policy_digest,
                global_policy_digest=v1.global_policy_digest,
                owner_policy_digest=v1.owner_policy_digest,
                allowed_side_effects=v1.allowed_side_effects,
                grant_source_digest=v1.grant_source_digest,
                exposure_digest=v1.exposure_digest,
                effective_policy_digest=v1.effective_policy_digest,
                write_release_digest=None,
            )
        return _v2_deny(
            reason_code=v1.reason_code,
            principal_digest=v1.principal_digest,
            entrypoint_digest=v1.entrypoint_policy_digest,
            global_digest=v1.global_policy_digest,
            effective_digest=v1.effective_policy_digest,
            owner_policy_digest=v1.owner_policy_digest,
            exposure_digest=v1.exposure_digest,
            allowed_side_effects=v1.allowed_side_effects,
            grant_source_digest=v1.grant_source_digest,
        )

    if actual_effect in set(PLAN05_RELEASE_GATE_SIDE_EFFECTS):
        # Read/compute/none still use independent v1 grant; wrap as v2 dispatch/deny.
        if v1.allowed:
            return build_authorization_decision_v2(
                policy_allowed=True,
                dispatch_disposition="dispatch",
                reason_code="allowed",
                principal_digest=v1.principal_digest,
                entrypoint_policy_digest=v1.entrypoint_policy_digest,
                global_policy_digest=v1.global_policy_digest,
                owner_policy_digest=v1.owner_policy_digest,
                allowed_side_effects=v1.allowed_side_effects,
                grant_source_digest=v1.grant_source_digest,
                exposure_digest=v1.exposure_digest,
                effective_policy_digest=v1.effective_policy_digest,
                write_release_digest=None,
            )
        return _v2_deny(
            reason_code=v1.reason_code,
            principal_digest=v1.principal_digest,
            entrypoint_digest=v1.entrypoint_policy_digest,
            global_digest=v1.global_policy_digest,
            effective_digest=v1.effective_policy_digest,
            owner_policy_digest=v1.owner_policy_digest,
            exposure_digest=v1.exposure_digest,
            allowed_side_effects=v1.allowed_side_effects,
            grant_source_digest=v1.grant_source_digest,
        )

    # Write / draft / unknown path under v2.
    if actual_effect in {"draft", "write_external", "unknown"}:
        return _v2_deny(
            reason_code="release_gate_denied",
            principal_digest=principal_digest,
            entrypoint_digest=entrypoint_digest,
            global_digest=global_digest,
            effective_digest=effective_digest,
        )

    if actual_effect != "write_local":
        return _v2_deny(
            reason_code="release_gate_denied",
            principal_digest=principal_digest,
            entrypoint_digest=entrypoint_digest,
            global_digest=global_digest,
            effective_digest=effective_digest,
        )

    # Structural gates from v1 that are independent of write release.
    if v1.reason_code in {
        "scope_mismatch",
        "manifest_surface_mismatch",
        "exposure_missing",
        "exposure_ambiguous",
        "owner_mismatch",
        "principal_unauthenticated",
        "principal_not_allowed",
        "entrypoint_not_allowed",
        "global_policy_denied",
        "owner_capability_not_declared",
        "version_or_digest_drift",
        "recursion_denied",
        "target_unavailable",
    }:
        return _v2_deny(
            reason_code=v1.reason_code,
            principal_digest=v1.principal_digest,
            entrypoint_digest=v1.entrypoint_policy_digest,
            global_digest=v1.global_policy_digest,
            effective_digest=v1.effective_policy_digest,
            owner_policy_digest=v1.owner_policy_digest,
            exposure_digest=v1.exposure_digest,
        )

    # Exact golden release match.
    owner_key = None
    owner = None
    for key, material in owner_materials.items():
        if (
            material.owner_kind == proposal.claimed_owner_kind
            or True
        ):
            # Prefer matching owner_version_id from proposal claimed fields.
            pass
    # Resolve owner material the same way as evaluate_authorization.
    matches = _find_exposures_for_key(
        snapshot.exposure_index, capability_key=proposal.capability_key
    )
    if not matches:
        return _v2_deny(
            reason_code="exposure_missing",
            principal_digest=principal_digest,
            entrypoint_digest=entrypoint_digest,
            global_digest=global_digest,
            effective_digest=effective_digest,
        )
    exposure = matches[0]
    owner_tuple = (
        exposure.owner_kind,
        exposure.owner_id,
        exposure.owner_version_id,
    )
    # owner_materials keys in evaluator are (kind, id, version)
    owner = owner_materials.get(owner_tuple)  # type: ignore[arg-type]
    if owner is None:
        # try string-coerced keys
        for k, v in owner_materials.items():
            if (
                k[0] == exposure.owner_kind
                and str(k[2]) == str(exposure.owner_version_id)
            ):
                owner = v
                break
    if owner is None:
        return _v2_deny(
            reason_code="owner_capability_not_declared",
            principal_digest=principal_digest,
            entrypoint_digest=entrypoint_digest,
            global_digest=global_digest,
            effective_digest=effective_digest,
            exposure_digest=exposure.exposure_digest,
        )

    if not release_matches_proposal(
        golden_write_release,
        principal_digest=principal_digest,
        owner_kind=str(owner.owner_kind),
        owner_version_id=owner.owner_version_id,
        binding_contract_digest=proposal.binding_contract_digest,
        domain_key=proposal.capability_key,
        target_version_id=target_version_id,
        target_digest=target_digest,
        execution_mode=execution_mode,
    ):
        return _v2_deny(
            reason_code="release_gate_denied",
            principal_digest=principal_digest,
            entrypoint_digest=entrypoint_digest,
            global_digest=global_digest,
            effective_digest=effective_digest,
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
            write_release_digest=golden_write_release.release_digest,
        )

    grant = derive_golden_write_grant(
        owner=owner,
        capability_key=proposal.capability_key,
        binding_contract_digest=proposal.binding_contract_digest,
        release=golden_write_release,
        entrypoint_policy_digest=entrypoint_digest,
        global_policy_digest=global_digest,
        global_policy=global_policy,
    )
    if grant is None or "write_local" not in grant.allowed_side_effects:
        return _v2_deny(
            reason_code="owner_side_effect_denied",
            principal_digest=principal_digest,
            entrypoint_digest=entrypoint_digest,
            global_digest=global_digest,
            effective_digest=effective_digest,
            owner_policy_digest=owner.policy_digest,
            exposure_digest=exposure.exposure_digest,
            write_release_digest=golden_write_release.release_digest,
        )

    # Exact golden write: policy allows but not executable until call approval.
    return build_authorization_decision_v2(
        policy_allowed=True,
        dispatch_disposition="awaiting_call_approval",
        reason_code="awaiting_call_approval",
        principal_digest=principal_digest,
        entrypoint_policy_digest=entrypoint_digest,
        global_policy_digest=global_digest,
        owner_policy_digest=owner.policy_digest,
        allowed_side_effects=grant.allowed_side_effects,
        grant_source_digest=grant.grant_source_digest,
        exposure_digest=exposure.exposure_digest,
        effective_policy_digest=effective_digest,
        write_release_digest=golden_write_release.release_digest,
    )


def issue_post_approval_gateway_evidence(
    *,
    frozen_decision: AuthorizationDecisionV2,
    approval_binding_digest: str,
) -> AuthorizationDecisionV2:
    """Return the same frozen decision after approval (digests unchanged).

    Approval never mints or widens a grant. Gateway evidence issuance in Task 4/5
    reuses this decision plus linked approval binding; this helper only asserts
    the preconditions and returns the immutable decision body.
    """
    if frozen_decision.dispatch_disposition != "awaiting_call_approval":
        raise ValueError("post-approval evidence requires awaiting_call_approval decision")
    if not frozen_decision.policy_allowed:
        raise ValueError("post-approval evidence requires policy_allowed")
    if not approval_binding_digest or len(approval_binding_digest) != 64:
        raise ValueError("approval_binding_digest must be a 64-char hex digest")
    # Intentionally return the same object fields (new instance, identical digests).
    return build_authorization_decision_v2(
        policy_allowed=frozen_decision.policy_allowed,
        dispatch_disposition=frozen_decision.dispatch_disposition,
        reason_code=frozen_decision.reason_code,
        principal_digest=frozen_decision.principal_digest,
        entrypoint_policy_digest=frozen_decision.entrypoint_policy_digest,
        global_policy_digest=frozen_decision.global_policy_digest,
        owner_policy_digest=frozen_decision.owner_policy_digest,
        allowed_side_effects=frozen_decision.allowed_side_effects,
        grant_source_digest=frozen_decision.grant_source_digest,
        exposure_digest=frozen_decision.exposure_digest,
        effective_policy_digest=frozen_decision.effective_policy_digest,
        write_release_digest=frozen_decision.write_release_digest,
        decision_digest=frozen_decision.decision_digest,
    )


__all__ = [
    "GOLDEN_CREATE_ENTRY_DOMAIN_KEY",
    "derive_golden_write_grant",
    "evaluate_authorization_v2",
    "issue_post_approval_gateway_evidence",
    "release_matches_proposal",
]

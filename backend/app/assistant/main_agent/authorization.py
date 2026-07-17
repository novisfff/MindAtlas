"""Main Agent authorization bridge (skill_policy transport).

Plan 04 minimum path: independent platform ceiling + exact owner/Manifest
exposure. Plan 05 source-aware path: pure evaluator-backed grant derivation
when an EffectiveRunPolicySnapshot is bound. Descriptor behavior is checked
against the grant; it never constructs the grant.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Literal, Mapping, Sequence
from uuid import UUID

from app.assistant.capabilities.contracts import (
    CapabilityAuthorizationEvidence,
    CapabilityDescriptor,
    CapabilityExecutionContext,
    CapabilityOwnerRef,
    CapabilityPrincipal,
    FrozenBindingProvenance,
    FrozenCapabilityBinding,
    SideEffectClass,
    VerifiedAuthorizationEvidence,
)
from app.assistant.capabilities.policy import (
    AtomicSingleUseDispatchPermit,
    AuthorizationEvidenceVerificationError,
    lattice_prefix_through,
)
from app.assistant.domain.contracts import (
    FrozenContract,
    ResolvedRunManifestRevision,
)
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.provider_loop.contracts import ProviderExecutionScope
from app.assistant.provider_loop.messages import ProviderToolCall

LOCAL_ASSISTANT_PRINCIPAL = CapabilityPrincipal(
    principal_type="service",
    principal_id="local-assistant",
    authenticated=True,
)

MAIN_AGENT_CEILING_REVISION: Literal["plan07-v1"] = "plan07-v1"
MAIN_AGENT_CEILING_KEY: Literal["main_agent_read_only"] = "main_agent_read_only"


class MainAgentEffectCeiling(FrozenContract):
    """Checked-in independent platform ceiling (Plan 04 §13)."""

    schema_version: Literal[1] = 1
    ceiling_key: Literal["main_agent_read_only"] = "main_agent_read_only"
    revision: Literal["plan07-v1"] = "plan07-v1"
    allowed_side_effects: tuple[SideEffectClass, ...]
    allowed_interrupt_modes: tuple[Literal["none", "durable"], ...]
    ceiling_digest: str


def _ceiling_digest_payload(
    *,
    schema_version: int,
    ceiling_key: str,
    revision: str,
    allowed_side_effects: Sequence[str],
    allowed_interrupt_modes: Sequence[str],
) -> dict[str, Any]:
    return {
        "schemaVersion": schema_version,
        "ceilingKey": ceiling_key,
        "revision": revision,
        "allowedSideEffects": list(allowed_side_effects),
        "allowedInterruptModes": list(allowed_interrupt_modes),
    }


def build_main_agent_read_only_effect_ceiling() -> MainAgentEffectCeiling:
    allowed = lattice_prefix_through("read")
    assert allowed == ("none", "compute", "read")
    digest = sha256_canonical_json(
        _ceiling_digest_payload(
            schema_version=1,
            ceiling_key=MAIN_AGENT_CEILING_KEY,
            revision=MAIN_AGENT_CEILING_REVISION,
            allowed_side_effects=allowed,
            allowed_interrupt_modes=("none", "durable"),
        )
    )
    return MainAgentEffectCeiling(
        schema_version=1,
        ceiling_key="main_agent_read_only",
        revision="plan07-v1",
        allowed_side_effects=allowed,
        allowed_interrupt_modes=("none", "durable"),
        ceiling_digest=digest,
    )


MAIN_AGENT_READ_ONLY_EFFECT_CEILING = build_main_agent_read_only_effect_ceiling()

# Fixed vector: digest covers every field except itself.
MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST = (
    MAIN_AGENT_READ_ONLY_EFFECT_CEILING.ceiling_digest
)


# Author-declared side effects (Plan 01) → Plan 02 lattice classes.
_AUTHOR_TO_LATTICE: dict[str, SideEffectClass] = {
    "read": "read",
    "compute": "compute",
    # Plan 01 "write" maps to write_local for intersection purposes; ceiling
    # will still exclude it from the effective grant.
    "write": "write_local",
    "draft": "draft",
    "control": "none",
}


def map_author_side_effects(
    author_effects: Sequence[str],
) -> tuple[SideEffectClass, ...]:
    """Map Plan 01 author declarations into the Plan 02 lattice (no unknown)."""
    out: list[SideEffectClass] = []
    seen: set[SideEffectClass] = set()
    for item in author_effects:
        mapped = _AUTHOR_TO_LATTICE.get(str(item))
        if mapped is None:
            raise ValueError(f"unsupported author side effect: {item!r}")
        if mapped not in seen:
            seen.add(mapped)
            out.append(mapped)
    return tuple(out)


def intersect_with_platform_ceiling(
    author_lattice: Sequence[SideEffectClass],
    *,
    ceiling: MainAgentEffectCeiling = MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
) -> tuple[SideEffectClass, ...]:
    """Platform prefix ∩ author lattice, preserving lattice order.

    ``none`` is admitted by the explicit Main Agent entrypoint rule even when
    the author declaration omits it.
    """
    allowed = set(ceiling.allowed_side_effects)
    author_set = set(author_lattice)
    # none is always admitted for Main Agent entrypoint controls/bindings.
    author_set.add("none")
    return tuple(effect for effect in ceiling.allowed_side_effects if effect in author_set and effect in allowed)


def compute_main_agent_grant_source_digest(
    *,
    ceiling: MainAgentEffectCeiling,
    owner: CapabilityOwnerRef,
    capability_key: str,
    owner_content_or_policy_digest: str,
    manifest_membership_digest: str,
    binding_contract_digest: str,
) -> str:
    """Independent grant source digest (never covers descriptor behavior)."""
    return sha256_canonical_json(
        {
            "ceilingRevision": ceiling.revision,
            "ceilingDigest": ceiling.ceiling_digest,
            "ownerKind": owner.owner_kind,
            "ownerId": owner.owner_id,
            "ownerVersionId": (
                str(owner.owner_version_id) if owner.owner_version_id is not None else None
            ),
            "ownerContentOrPolicyDigest": owner_content_or_policy_digest,
            "capabilityKey": capability_key,
            "manifestMembershipDigest": manifest_membership_digest,
            "bindingContractDigest": binding_contract_digest,
        }
    )


def compute_manifest_membership_digest(
    *,
    manifest: ResolvedRunManifestRevision,
    capability_key: str,
    binding_contract_digest: str,
) -> str:
    """Digest covering exact Manifest membership of one capability binding."""
    present = any(
        item.capability_key == capability_key
        and item.binding_contract_digest == binding_contract_digest
        for item in manifest.capabilities
    )
    return sha256_canonical_json(
        {
            "runId": str(manifest.run_id),
            "revision": manifest.revision,
            "manifestDigest": manifest.manifest_digest,
            "capabilityKey": capability_key,
            "bindingContractDigest": binding_contract_digest,
            "present": present,
        }
    )


def owner_ref_from_binding_provenance(
    binding: FrozenCapabilityBinding,
    *,
    profile_key: str | None = None,
    skill_package_id_by_version: Mapping[UUID, UUID] | None = None,
    skill_package_id: UUID | None = None,
) -> CapabilityOwnerRef:
    """Derive owner from frozen binding provenance (Main Agent or Skill).

    Skill ownership uses the stable package ID as ``owner_id`` (Plan 05 §4.2),
    matching ManifestExposureIndex / evaluator claimed-owner identity. Callers
    must supply ``skill_package_id`` or a version→package map for skill_version
    bindings; unresolved package identity raises.
    """
    provenance = binding.provenance
    if provenance.origin == "main_agent_profile":
        return CapabilityOwnerRef(
            owner_kind="main_agent",
            owner_id=profile_key or "main-agent",
            owner_version_id=provenance.owner_version_id,
        )
    if provenance.origin == "skill_version":
        version_id = provenance.owner_version_id
        package_id: UUID | None = skill_package_id
        if package_id is None and version_id is not None and skill_package_id_by_version is not None:
            package_id = skill_package_id_by_version.get(version_id)
        if package_id is not None:
            # Plan 05 §4.2: stable package ID is the owner_id for skill exposures.
            return CapabilityOwnerRef(
                owner_kind="skill_version",
                owner_id=str(package_id),
                owner_version_id=version_id,
            )
        if skill_package_id_by_version is not None or skill_package_id is not None:
            # Map was supplied but this version is unresolved — fail closed.
            raise AuthorizationEvidenceVerificationError("owner_version_missing")
        # Plan 04 minimum path (no package map): keep historical version_id owner_id.
        return CapabilityOwnerRef(
            owner_kind="skill_version",
            owner_id=str(version_id or binding.ref.capability_key),
            owner_version_id=version_id,
        )
    # system/test paths are not production Main Agent owners.
    return CapabilityOwnerRef(
        owner_kind="system",
        owner_id="system",
        owner_version_id=provenance.owner_version_id,
    )


def derive_allowed_side_effects_for_owner(
    *,
    owner: CapabilityOwnerRef,
    author_allowed_side_effects: Sequence[str] | None,
    is_base_control: bool,
    ceiling: MainAgentEffectCeiling = MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
) -> tuple[SideEffectClass, ...]:
    """Independent grant derivation (never reads descriptor.behavior)."""
    if is_base_control or owner.owner_kind == "main_agent":
        # Base controls: platform prefix (read/compute/none) as published exposure.
        return ceiling.allowed_side_effects
    if author_allowed_side_effects is None:
        raise ValueError("author_allowed_side_effects required for skill owners")
    author_lattice = map_author_side_effects(author_allowed_side_effects)
    effective = intersect_with_platform_ceiling(author_lattice, ceiling=ceiling)
    if not effective:
        raise ValueError("empty effective grant")
    return effective


class SkillPolicyAuthorizationEvidenceVerifier:
    """Call-scoped single-use verifier for issuer=skill_policy / entrypoint=main_agent.

    Plan 05 source-aware path: expected grant comes from independent sources;
    descriptor behavior is membership-checked only. ``copy_descriptor_effect`` and
    ``omit_grant_source_digest`` exist solely for negative tests.
    """

    def __init__(
        self,
        *,
        expected_call_id: str,
        expected_capability_key: str,
        expected_owner: CapabilityOwnerRef,
        expected_resolution_digest: str,
        expected_binding_contract_digest: str,
        expected_dependency_closure_digest: str,
        expected_grant_source_digest: str,
        expected_evidence_digest: str,
        expected_principal: CapabilityPrincipal = LOCAL_ASSISTANT_PRINCIPAL,
        expected_run_id: UUID | None = None,
        expected_conversation_id: UUID | None = None,
        ceiling: MainAgentEffectCeiling = MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        allowed_side_effects: tuple[SideEffectClass, ...] | None = None,
        copy_descriptor_effect: bool = False,
        omit_grant_source_digest: bool = False,
    ) -> None:
        self.expected_call_id = expected_call_id
        self.expected_capability_key = expected_capability_key
        self.expected_owner = expected_owner
        self.expected_resolution_digest = expected_resolution_digest
        self.expected_binding_contract_digest = expected_binding_contract_digest
        self.expected_dependency_closure_digest = expected_dependency_closure_digest
        self.expected_grant_source_digest = expected_grant_source_digest
        self.expected_evidence_digest = expected_evidence_digest
        self.expected_principal = expected_principal
        self.expected_run_id = expected_run_id
        self.expected_conversation_id = expected_conversation_id
        self.ceiling = ceiling
        self.allowed_side_effects = (
            allowed_side_effects
            if allowed_side_effects is not None
            else ceiling.allowed_side_effects
        )
        # Test-only traps: production always leaves these False.
        self.copy_descriptor_effect = copy_descriptor_effect
        self.omit_grant_source_digest = omit_grant_source_digest
        self.verifier_instance_id = str(uuid.uuid4())
        self._lock = threading.Lock()
        self._consumed = False

    def verify(
        self,
        *,
        descriptor: CapabilityDescriptor,
        evidence: CapabilityAuthorizationEvidence,
        context: CapabilityExecutionContext,
    ) -> VerifiedAuthorizationEvidence:
        if evidence.issuer != "skill_policy" or evidence.entrypoint != "main_agent":
            raise AuthorizationEvidenceVerificationError("issuer_entrypoint_mismatch")
        if not evidence.principal.authenticated:
            raise AuthorizationEvidenceVerificationError("unauthenticated_principal")
        if (
            evidence.principal.principal_type != self.expected_principal.principal_type
            or evidence.principal.principal_id != self.expected_principal.principal_id
        ):
            raise AuthorizationEvidenceVerificationError("principal_mismatch")
        if evidence.call_id != self.expected_call_id or context.call_id != self.expected_call_id:
            raise AuthorizationEvidenceVerificationError("call_id_mismatch")
        if evidence.capability_key != self.expected_capability_key:
            raise AuthorizationEvidenceVerificationError("capability_key_mismatch")
        if evidence.owner.owner_kind != self.expected_owner.owner_kind:
            raise AuthorizationEvidenceVerificationError("owner_kind_mismatch")
        if evidence.owner.owner_id != self.expected_owner.owner_id:
            raise AuthorizationEvidenceVerificationError("owner_id_mismatch")
        if evidence.owner.owner_version_id != self.expected_owner.owner_version_id:
            raise AuthorizationEvidenceVerificationError("owner_version_mismatch")
        if evidence.resolution_digest != self.expected_resolution_digest:
            raise AuthorizationEvidenceVerificationError("resolution_digest_mismatch")
        if evidence.binding_contract_digest != self.expected_binding_contract_digest:
            raise AuthorizationEvidenceVerificationError("binding_contract_digest_mismatch")
        if evidence.dependency_closure_digest != self.expected_dependency_closure_digest:
            raise AuthorizationEvidenceVerificationError("dependency_closure_digest_mismatch")
        if self.omit_grant_source_digest:
            raise AuthorizationEvidenceVerificationError("missing_grant_source_digest")
        if evidence.grant_source_digest != self.expected_grant_source_digest:
            raise AuthorizationEvidenceVerificationError("grant_source_digest_mismatch")
        if evidence.evidence_digest != self.expected_evidence_digest:
            raise AuthorizationEvidenceVerificationError("evidence_digest_mismatch")
        if self.expected_run_id is not None and context.run_id != self.expected_run_id:
            raise AuthorizationEvidenceVerificationError("run_id_mismatch")
        if (
            self.expected_conversation_id is not None
            and context.conversation_id != self.expected_conversation_id
        ):
            raise AuthorizationEvidenceVerificationError("conversation_id_mismatch")

        # Independent ceiling check — never synthesize grant from descriptor.
        granted: tuple[SideEffectClass, ...]
        if self.copy_descriptor_effect:
            granted = (descriptor.behavior.side_effect,)  # type: ignore[assignment]
        else:
            granted = self.allowed_side_effects
        actual = descriptor.behavior.side_effect
        if actual == "unknown":
            raise AuthorizationEvidenceVerificationError("unknown_side_effect")
        if actual not in granted:
            raise AuthorizationEvidenceVerificationError("side_effect_above_ceiling")
        interrupt_mode = str(getattr(descriptor.behavior, "interrupt_mode", "none") or "none")
        if interrupt_mode not in set(self.ceiling.allowed_interrupt_modes):
            raise AuthorizationEvidenceVerificationError("interrupt_mode_above_ceiling")
        if descriptor.availability.status != "available":
            raise AuthorizationEvidenceVerificationError("target_unavailable")

        # Evidence allowed_side_effects must match the independent grant (when not
        # using the copy trap). Production path refuses descriptor-derived grants.
        if not self.copy_descriptor_effect:
            if tuple(evidence.allowed_side_effects) != tuple(self.allowed_side_effects):
                raise AuthorizationEvidenceVerificationError("evidence_grant_mismatch")

        with self._lock:
            if self._consumed:
                raise AuthorizationEvidenceVerificationError("evidence_already_consumed")
            self._consumed = True

        return VerifiedAuthorizationEvidence(
            call_id=evidence.call_id,
            verifier_key=("skill_policy", "main_agent"),
            verifier_instance_id=self.verifier_instance_id,
            principal=evidence.principal,
            entrypoint=evidence.entrypoint,
            owner=evidence.owner,
            capability_key=evidence.capability_key,
            resolution_digest=evidence.resolution_digest,
            binding_contract_digest=evidence.binding_contract_digest,
            dependency_closure_digest=evidence.dependency_closure_digest,
            allowed_side_effects=granted,
            grant_source_digest=evidence.grant_source_digest,
            evidence_digest=evidence.evidence_digest,
            verification_digest=sha256_canonical_json(
                {
                    "callId": evidence.call_id,
                    "evidenceDigest": evidence.evidence_digest,
                    "verifierInstanceId": self.verifier_instance_id,
                }
            ),
            dispatch_permit=AtomicSingleUseDispatchPermit(),
        )


class MainAgentAuthorizationEvidenceFactory:
    """Issues one-time skill_policy evidence for Main Agent Gateway dispatches.

    When an ``EffectiveRunPolicySnapshot`` + owner materials are bound, issuance
    uses the Plan 05 pure evaluator (source-aware grant). Otherwise the Plan 04
    minimum path remains (independent ceiling ∩ author declaration).
    """

    def __init__(
        self,
        *,
        scope: ProviderExecutionScope,
        manifest: ResolvedRunManifestRevision,
        profile_key: str,
        profile_content_digest: str,
        skill_author_policy_by_version: Mapping[UUID, Sequence[str]] | None = None,
        skill_content_digest_by_version: Mapping[UUID, str] | None = None,
        skill_package_id_by_version: Mapping[UUID, UUID] | None = None,
        ceiling: MainAgentEffectCeiling = MAIN_AGENT_READ_ONLY_EFFECT_CEILING,
        principal: CapabilityPrincipal = LOCAL_ASSISTANT_PRINCIPAL,
        policy_snapshot: Any | None = None,
        owner_materials: Mapping[Any, Any] | None = None,
    ) -> None:
        self.scope = scope
        self.manifest = manifest
        self.profile_key = profile_key
        self.profile_content_digest = profile_content_digest
        self.skill_author_policy_by_version = dict(skill_author_policy_by_version or {})
        self.skill_content_digest_by_version = dict(skill_content_digest_by_version or {})
        # Plan 05 §4.2: version_id → stable package_id for skill owner_id.
        self.skill_package_id_by_version = dict(skill_package_id_by_version or {})
        self.ceiling = ceiling
        self.principal = principal
        self.policy_snapshot = policy_snapshot
        self.owner_materials = dict(owner_materials or {})
        self._lock = threading.Lock()
        self._issued_call_ids: set[str] = set()
        self._verifiers: dict[str, SkillPolicyAuthorizationEvidenceVerifier] = {}

    def rebind_manifest(
        self,
        manifest: ResolvedRunManifestRevision,
        *,
        skill_author_policy_by_version: Mapping[UUID, Sequence[str]] | None = None,
        skill_content_digest_by_version: Mapping[UUID, str] | None = None,
        skill_package_id_by_version: Mapping[UUID, UUID] | None = None,
        policy_snapshot: Any | None = None,
        owner_materials: Mapping[Any, Any] | None = None,
    ) -> None:
        """Point membership checks at the lifecycle-accepted Manifest.

        After skill.inject accept, new Skill bindings become dispatchable only
        when the factory sees the child Manifest. Does not clear issued call IDs.
        """
        with self._lock:
            self.manifest = manifest
            if skill_author_policy_by_version is not None:
                self.skill_author_policy_by_version.update(
                    {k: tuple(v) for k, v in skill_author_policy_by_version.items()}
                )
            if skill_content_digest_by_version is not None:
                self.skill_content_digest_by_version.update(
                    dict(skill_content_digest_by_version)
                )
            if skill_package_id_by_version is not None:
                self.skill_package_id_by_version.update(dict(skill_package_id_by_version))
            if policy_snapshot is not None:
                self.policy_snapshot = policy_snapshot
            if owner_materials is not None:
                self.owner_materials.update(dict(owner_materials))

    def issue(
        self,
        *,
        call: ProviderToolCall,
        binding: FrozenCapabilityBinding,
        descriptor: CapabilityDescriptor,
        scope: ProviderExecutionScope,
    ) -> CapabilityAuthorizationEvidence:
        if scope.scope_digest != self.scope.scope_digest:
            raise AuthorizationEvidenceVerificationError("scope_mismatch")
        if scope.run_id != self.scope.run_id or scope.conversation_id != self.scope.conversation_id:
            raise AuthorizationEvidenceVerificationError("scope_identity_mismatch")

        if self.policy_snapshot is not None:
            return self._issue_plan05(
                call=call,
                binding=binding,
                descriptor=descriptor,
                scope=scope,
            )
        return self._issue_plan04_minimum(
            call=call,
            binding=binding,
            descriptor=descriptor,
            scope=scope,
        )

    def _issue_plan05(
        self,
        *,
        call: ProviderToolCall,
        binding: FrozenCapabilityBinding,
        descriptor: CapabilityDescriptor,
        scope: ProviderExecutionScope,
    ) -> CapabilityAuthorizationEvidence:
        # Late import avoids circular load with policy.contracts → authorization.
        from app.assistant.policy.evaluator import (
            evaluate_authorization,
            proposal_from_descriptor,
        )
        from app.assistant.policy.evidence import issue_skill_policy_evidence

        snapshot = self.policy_snapshot
        assert snapshot is not None
        owner = owner_ref_from_binding_provenance(
            binding,
            profile_key=self.profile_key,
            skill_package_id_by_version=self.skill_package_id_by_version or None,
        )
        proposal = proposal_from_descriptor(
            descriptor=descriptor,
            run_id=scope.run_id,
            conversation_id=scope.conversation_id,
            scope_digest=scope.scope_digest,
            expected_scope_digest=self.scope.scope_digest,
            expected_run_id=self.scope.run_id,
            expected_conversation_id=self.scope.conversation_id,
            manifest_digest=self.manifest.manifest_digest,
            expected_manifest_digest=snapshot.exposure_index.manifest_digest,
            principal=self.principal,
            nesting_depth=0,
            max_capability_depth=snapshot.run_budget_limits.max_capability_depth,
            claimed_owner_kind=owner.owner_kind if owner.owner_kind in {"main_agent", "skill_version"} else None,  # type: ignore[arg-type]
            claimed_owner_id=owner.owner_id,
            claimed_owner_version_id=owner.owner_version_id,
            trusted_durable_plan=self._has_trusted_durable_plan(binding),
        )
        # Override capability key with the tool-call domain key for surface match.
        if proposal.capability_key != call.domain_key:
            raise AuthorizationEvidenceVerificationError("capability_key_mismatch")
        decision = evaluate_authorization(
            snapshot=snapshot,
            proposal=proposal,
            owner_materials=self.owner_materials,  # type: ignore[arg-type]
        )
        if not decision.allowed:
            raise AuthorizationEvidenceVerificationError(decision.reason_code)

        with self._lock:
            if call.call_id in self._issued_call_ids:
                raise AuthorizationEvidenceVerificationError("call_id_replay")
            self._issued_call_ids.add(call.call_id)
            counter = len(self._issued_call_ids)

        evidence = issue_skill_policy_evidence(
            call_id=call.call_id,
            principal=self.principal,
            owner=owner,
            capability_key=call.domain_key,
            resolution_digest=binding.ref.resolution_digest,
            binding_contract_digest=binding.ref.binding_contract_digest,
            dependency_closure_digest=binding.ref.dependency_closure_digest,
            decision=decision,
            counter=counter,
            scope_digest=scope.scope_digest,
            manifest_digest=self.manifest.manifest_digest,
        )
        verifier = SkillPolicyAuthorizationEvidenceVerifier(
            expected_call_id=call.call_id,
            expected_capability_key=call.domain_key,
            expected_owner=owner,
            expected_resolution_digest=binding.ref.resolution_digest,
            expected_binding_contract_digest=binding.ref.binding_contract_digest,
            expected_dependency_closure_digest=binding.ref.dependency_closure_digest,
            expected_grant_source_digest=evidence.grant_source_digest,
            expected_evidence_digest=evidence.evidence_digest,
            expected_principal=self.principal,
            expected_run_id=scope.run_id,
            expected_conversation_id=scope.conversation_id,
            ceiling=self.ceiling,
            allowed_side_effects=tuple(decision.allowed_side_effects),
        )
        with self._lock:
            self._verifiers[call.call_id] = verifier
        return evidence

    @staticmethod
    def _has_trusted_durable_plan(binding: FrozenCapabilityBinding) -> bool:
        from app.assistant.workflow.durable.planner import extract_durable_plan_digest

        snapshot = binding.resolved.resolution_snapshot
        return bool(
            extract_durable_plan_digest(snapshot if isinstance(snapshot, dict) else None)
        )

    def _issue_plan04_minimum(
        self,
        *,
        call: ProviderToolCall,
        binding: FrozenCapabilityBinding,
        descriptor: CapabilityDescriptor,
        scope: ProviderExecutionScope,
    ) -> CapabilityAuthorizationEvidence:
        del descriptor  # grant never copies descriptor behavior
        owner = owner_ref_from_binding_provenance(binding, profile_key=self.profile_key)
        is_base_control = binding.provenance.origin == "main_agent_profile"
        author_effects: Sequence[str] | None = None
        owner_digest = self.profile_content_digest
        if not is_base_control:
            version_id = binding.provenance.owner_version_id
            if version_id is None:
                raise AuthorizationEvidenceVerificationError("owner_version_missing")
            author_effects = self.skill_author_policy_by_version.get(version_id)
            if author_effects is None:
                raise AuthorizationEvidenceVerificationError("author_policy_missing")
            owner_digest = self.skill_content_digest_by_version.get(
                version_id, binding.provenance.source_snapshot_digest
            )
        try:
            allowed = derive_allowed_side_effects_for_owner(
                owner=owner,
                author_allowed_side_effects=author_effects,
                is_base_control=is_base_control,
                ceiling=self.ceiling,
            )
        except ValueError as exc:
            raise AuthorizationEvidenceVerificationError("grant_derivation_failed") from exc

        membership = compute_manifest_membership_digest(
            manifest=self.manifest,
            capability_key=binding.ref.capability_key,
            binding_contract_digest=binding.ref.binding_contract_digest,
        )
        # Membership must be present in the current Manifest.
        present = any(
            item.capability_key == binding.ref.capability_key
            and item.binding_contract_digest == binding.ref.binding_contract_digest
            for item in self.manifest.capabilities
        )
        if not present:
            raise AuthorizationEvidenceVerificationError("binding_not_in_manifest")

        grant_source = compute_main_agent_grant_source_digest(
            ceiling=self.ceiling,
            owner=owner,
            capability_key=binding.ref.capability_key,
            owner_content_or_policy_digest=owner_digest,
            manifest_membership_digest=membership,
            binding_contract_digest=binding.ref.binding_contract_digest,
        )
        with self._lock:
            if call.call_id in self._issued_call_ids:
                raise AuthorizationEvidenceVerificationError("call_id_replay")
            self._issued_call_ids.add(call.call_id)
            counter = len(self._issued_call_ids)
        evidence_digest = sha256_canonical_json(
            {
                "schemaVersion": 1,
                "kind": "main_agent_authorization_evidence",
                "callId": call.call_id,
                "counter": counter,
                "bindingDigest": binding.ref.binding_contract_digest,
                "grantSourceDigest": grant_source,
                "scopeDigest": scope.scope_digest,
                "manifestDigest": self.manifest.manifest_digest,
            }
        )
        evidence = CapabilityAuthorizationEvidence(
            issuer="skill_policy",
            call_id=call.call_id,
            principal=self.principal,
            entrypoint="main_agent",
            owner=owner,
            capability_key=call.domain_key,
            resolution_digest=binding.ref.resolution_digest,
            binding_contract_digest=binding.ref.binding_contract_digest,
            dependency_closure_digest=binding.ref.dependency_closure_digest,
            allowed_side_effects=allowed,
            grant_source_digest=grant_source,
            evidence_digest=evidence_digest,
        )
        verifier = SkillPolicyAuthorizationEvidenceVerifier(
            expected_call_id=call.call_id,
            expected_capability_key=call.domain_key,
            expected_owner=owner,
            expected_resolution_digest=binding.ref.resolution_digest,
            expected_binding_contract_digest=binding.ref.binding_contract_digest,
            expected_dependency_closure_digest=binding.ref.dependency_closure_digest,
            expected_grant_source_digest=grant_source,
            expected_evidence_digest=evidence_digest,
            expected_principal=self.principal,
            expected_run_id=scope.run_id,
            expected_conversation_id=scope.conversation_id,
            ceiling=self.ceiling,
            allowed_side_effects=allowed,
        )
        with self._lock:
            self._verifiers[call.call_id] = verifier
        return evidence

    def take_verifier(
        self, *, call_id: str
    ) -> SkillPolicyAuthorizationEvidenceVerifier:
        with self._lock:
            verifier = self._verifiers.pop(call_id, None)
        if verifier is None:
            raise AuthorizationEvidenceVerificationError("verifier_not_found")
        return verifier

    def verifier_mapping(
        self, *, call_id: str
    ) -> dict[tuple[str, str], SkillPolicyAuthorizationEvidenceVerifier]:
        """Return a one-call verifier mapping for Gateway composition."""
        return {("skill_policy", "main_agent"): self.take_verifier(call_id=call_id)}


def build_main_agent_binding_provenance(
    *,
    owner_version_id: UUID,
    source_snapshot_digest: str,
    binding_row_id: UUID | None = None,
) -> FrozenBindingProvenance:
    return FrozenBindingProvenance(
        origin="main_agent_profile",
        binding_row_id=binding_row_id,
        owner_version_id=owner_version_id,
        source_snapshot_digest=source_snapshot_digest,
    )


__all__ = [
    "LOCAL_ASSISTANT_PRINCIPAL",
    "MAIN_AGENT_CEILING_KEY",
    "MAIN_AGENT_CEILING_REVISION",
    "MAIN_AGENT_READ_ONLY_EFFECT_CEILING",
    "MAIN_AGENT_READ_ONLY_EFFECT_CEILING_DIGEST",
    "MainAgentAuthorizationEvidenceFactory",
    "MainAgentEffectCeiling",
    "SkillPolicyAuthorizationEvidenceVerifier",
    "build_main_agent_binding_provenance",
    "build_main_agent_read_only_effect_ceiling",
    "compute_main_agent_grant_source_digest",
    "compute_manifest_membership_digest",
    "derive_allowed_side_effects_for_owner",
    "intersect_with_platform_ceiling",
    "map_author_side_effects",
    "owner_ref_from_binding_provenance",
]

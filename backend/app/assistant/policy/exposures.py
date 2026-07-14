"""Build ManifestExposureIndex from Manifest + bindings/descriptors.

Pure builders only. No database, Gateway, or mutable catalog queries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.assistant.capabilities.contracts import (
    CapabilityDescriptor,
    FrozenCapabilityBinding,
    SideEffectClass,
)
from app.assistant.domain.contracts import (
    ResolvedCapabilityRef,
    ResolvedRunManifestRevision,
    ResolvedSkillRef,
)
from app.assistant.policy.conflicts import (
    SkillConflictIdentity,
    SkillConflictParticipant,
    evaluate_skill_conflicts,
)
from app.assistant.policy.contracts import (
    CapabilityExposureRef,
    ManifestExposureIndex,
    PolicyOwnerKind,
    build_capability_exposure_ref,
    build_manifest_exposure_index,
)
from app.assistant.skills.contracts import SkillConflictRuleV1


class ExposureBuildError(ValueError):
    """Raised when exposure index construction fails closed."""

    def __init__(self, reason_code: str, message: str | None = None) -> None:
        self.reason_code = reason_code
        super().__init__(message or reason_code)


@dataclass(frozen=True, slots=True)
class ExposureOwnerIdentity:
    """Stable owner identity for an exposure (Main Agent Profile or Skill package)."""

    owner_kind: PolicyOwnerKind
    owner_id: str
    owner_version_id: UUID


@dataclass(frozen=True, slots=True)
class ExposureBindingInput:
    """One frozen binding + verified descriptor used to build an exposure."""

    binding: FrozenCapabilityBinding
    descriptor: CapabilityDescriptor
    owner: ExposureOwnerIdentity
    # Optional skill-policy fields for duplicate compatibility (skill owners only).
    max_skill_calls: int | None = None
    max_same_read_calls: int | None = None
    requires_terminal_output: bool | None = None
    terminal_text_allowed: bool | None = None
    conflict_rules: tuple[SkillConflictRuleV1, ...] = ()
    # Independent grant membership for classified side effect (not from descriptor).
    grant_admits_side_effect: bool = True


@dataclass(frozen=True, slots=True)
class DuplicateCapabilityDeclaration:
    """Candidate Skill declaration for an already-exposed Domain Key."""

    domain_key: str
    resolved_ref: ResolvedCapabilityRef
    binding_contract_digest: str
    descriptor_digest: str
    side_effect: SideEffectClass
    input_schema_digest: str
    output_schema_digest: str
    dependency_closure_digest: str
    resolution_digest: str
    executable_revision: str
    timeout_mode: str
    timeout_seconds: float | None
    interrupt_mode: str
    parallel_safe: bool
    terminal_output: bool
    needs_followup: bool
    followup_hint: str | None
    max_skill_calls: int
    max_same_read_calls: int
    requires_terminal_output: bool
    terminal_text_allowed: bool
    grant_admits_side_effect: bool
    conflict_rules: tuple[SkillConflictRuleV1, ...]
    candidate_skill_version_id: UUID
    candidate_canonical_name: str
    # Optional catalog aliases for the candidate (resolved via conflicts.py).
    candidate_aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExistingExposureCompatibilityView:
    """Frozen view of an existing exposure used for strict duplicate checks."""

    domain_key: str
    resolved_ref: ResolvedCapabilityRef
    binding_contract_digest: str
    descriptor_digest: str
    side_effect: SideEffectClass
    input_schema_digest: str
    output_schema_digest: str
    dependency_closure_digest: str
    resolution_digest: str
    executable_revision: str
    timeout_mode: str
    timeout_seconds: float | None
    interrupt_mode: str
    parallel_safe: bool
    terminal_output: bool
    needs_followup: bool
    followup_hint: str | None
    max_skill_calls: int | None
    max_same_read_calls: int | None
    requires_terminal_output: bool | None
    terminal_text_allowed: bool | None
    grant_admits_side_effect: bool
    conflict_rules: tuple[SkillConflictRuleV1, ...]
    owner_version_id: UUID
    compatible_consumer_version_ids: tuple[UUID, ...]
    # Canonical owner skill identity for coexistence / conflict evaluation.
    owner_canonical_name: str = ""
    owner_aliases: tuple[str, ...] = ()


def resolve_owner_from_binding(
    binding: FrozenCapabilityBinding,
    *,
    profile_key: str,
    profile_version_id: UUID,
    skill_package_id_by_version: Mapping[UUID, UUID] | None = None,
    skill_package_id: UUID | None = None,
) -> ExposureOwnerIdentity:
    """Derive owner from binding provenance (Plan 05 §4.2).

    - main_agent_profile → owner_kind=main_agent, owner_id=profile_key
    - skill_version → owner_kind=skill_version, owner_id=stable package ID
    """
    provenance = binding.provenance
    if provenance.origin == "main_agent_profile":
        version_id = provenance.owner_version_id or profile_version_id
        if version_id is None:
            raise ExposureBuildError("owner_missing", "main agent owner_version_id missing")
        return ExposureOwnerIdentity(
            owner_kind="main_agent",
            owner_id=profile_key,
            owner_version_id=version_id,
        )
    if provenance.origin == "skill_version":
        version_id = provenance.owner_version_id
        if version_id is None:
            raise ExposureBuildError("owner_missing", "skill owner_version_id missing")
        package_id: UUID | None = skill_package_id
        if package_id is None and skill_package_id_by_version is not None:
            package_id = skill_package_id_by_version.get(version_id)
        if package_id is None:
            raise ExposureBuildError(
                "owner_missing",
                f"skill package id unresolved for version {version_id}",
            )
        return ExposureOwnerIdentity(
            owner_kind="skill_version",
            owner_id=str(package_id),
            owner_version_id=version_id,
        )
    raise ExposureBuildError(
        "owner_mismatch",
        f"unsupported binding provenance origin {provenance.origin!r}",
    )


def _descriptor_matches_binding(
    *,
    binding: FrozenCapabilityBinding,
    descriptor: CapabilityDescriptor,
) -> None:
    """Reject stale/mismatched descriptors against the frozen binding/ref."""
    ref = binding.ref
    checks = (
        ("capability_key", ref.capability_key, descriptor.capability_key),
        ("capability_type", ref.capability_type, descriptor.capability_type),
        ("target_identity", ref.target_identity, descriptor.target_identity),
        ("target_id", ref.target_id, descriptor.target_id),
        ("target_version_id", ref.target_version_id, descriptor.target_version_id),
        ("target_revision", ref.target_revision, descriptor.target_revision),
        ("resolution_digest", ref.resolution_digest, descriptor.resolution_digest),
        (
            "binding_contract_digest",
            ref.binding_contract_digest,
            descriptor.binding_contract_digest,
        ),
        (
            "dependency_closure_digest",
            ref.dependency_closure_digest,
            descriptor.dependency_closure_digest,
        ),
        ("input_schema_digest", ref.input_schema_digest, descriptor.input_schema_digest),
        (
            "output_schema_digest",
            ref.output_schema_digest,
            descriptor.output_schema_digest,
        ),
    )
    mismatches = [name for name, left, right in checks if left != right]
    if mismatches:
        raise ExposureBuildError(
            "version_or_digest_drift",
            "descriptor does not match binding/ref: " + ", ".join(mismatches),
        )


def _skill_package_map_from_manifest(
    manifest: ResolvedRunManifestRevision,
) -> dict[UUID, UUID]:
    return {skill.version_id: skill.package_id for skill in manifest.active_skills}


def _active_skill_version_ids(manifest: ResolvedRunManifestRevision) -> set[UUID]:
    return {skill.version_id for skill in manifest.active_skills}


def build_exposure_ref_from_input(item: ExposureBindingInput) -> CapabilityExposureRef:
    _descriptor_matches_binding(binding=item.binding, descriptor=item.descriptor)
    domain_key = item.binding.ref.capability_key
    return build_capability_exposure_ref(
        domain_key=domain_key,
        resolved_ref=item.binding.ref,
        binding_contract_digest=item.binding.ref.binding_contract_digest,
        descriptor_digest=item.descriptor.descriptor_digest,
        owner_kind=item.owner.owner_kind,
        owner_id=item.owner.owner_id,
        owner_version_id=item.owner.owner_version_id,
        compatible_consumer_version_ids=(),
    )


def build_manifest_exposure_index_from_inputs(
    *,
    manifest: ResolvedRunManifestRevision,
    binding_inputs: Sequence[ExposureBindingInput],
    profile_key: str,
    allow_empty: bool = True,
) -> ManifestExposureIndex:
    """Build a ManifestExposureIndex from exact Manifest capabilities + inputs.

    Rules:
    - Every Manifest capability must have exactly one matching binding input.
    - No extra inputs beyond Manifest capabilities.
    - Domain keys sorted by UTF-8 bytes.
    - Owner identity must match provenance and (for skills) active Manifest membership.
    - Descriptor must verify against the same binding/ref digests.
    """
    package_map = _skill_package_map_from_manifest(manifest)
    active_versions = _active_skill_version_ids(manifest)
    profile_version_id = manifest.main_agent.version_id

    expected_keys = {cap.capability_key: cap for cap in manifest.capabilities}
    seen_keys: dict[str, ExposureBindingInput] = {}

    for item in binding_inputs:
        domain_key = item.binding.ref.capability_key
        if domain_key in seen_keys:
            raise ExposureBuildError(
                "exposure_ambiguous",
                f"duplicate binding input for domain key {domain_key!r}",
            )
        if domain_key not in expected_keys:
            raise ExposureBuildError(
                "exposure_extra",
                f"binding input for domain key {domain_key!r} not in Manifest",
            )
        manifest_ref = expected_keys[domain_key]
        if item.binding.ref != manifest_ref and not _refs_equal(item.binding.ref, manifest_ref):
            raise ExposureBuildError(
                "version_or_digest_drift",
                f"binding ref drift for domain key {domain_key!r}",
            )
        # Re-derive owner and compare to declared owner (fail closed on mismatch).
        skill_package_id: UUID | None = None
        if item.owner.owner_kind == "skill_version":
            try:
                skill_package_id = UUID(item.owner.owner_id)
            except (TypeError, ValueError) as exc:
                raise ExposureBuildError(
                    "owner_mismatch",
                    f"skill owner_id is not a valid UUID for domain key {domain_key!r}",
                ) from exc
        derived = resolve_owner_from_binding(
            item.binding,
            profile_key=profile_key,
            profile_version_id=profile_version_id,
            skill_package_id_by_version=package_map,
            skill_package_id=skill_package_id,
        )
        if (
            derived.owner_kind != item.owner.owner_kind
            or derived.owner_id != item.owner.owner_id
            or derived.owner_version_id != item.owner.owner_version_id
        ):
            raise ExposureBuildError(
                "owner_mismatch",
                f"owner mismatch for domain key {domain_key!r}",
            )
        if item.owner.owner_kind == "skill_version":
            if item.owner.owner_version_id not in active_versions:
                raise ExposureBuildError(
                    "owner_mismatch",
                    f"skill owner version {item.owner.owner_version_id} not active",
                )
            if skill_package_id is not None and package_map.get(
                item.owner.owner_version_id
            ) != skill_package_id:
                # Prefer package map from Manifest when available.
                if item.owner.owner_version_id in package_map:
                    raise ExposureBuildError(
                        "owner_mismatch",
                        f"skill package id mismatch for version {item.owner.owner_version_id}",
                    )
        if item.owner.owner_kind == "main_agent":
            if item.owner.owner_version_id != profile_version_id:
                raise ExposureBuildError(
                    "owner_mismatch",
                    "main agent owner_version_id must equal Manifest profile version",
                )
            if item.owner.owner_id != profile_key:
                raise ExposureBuildError(
                    "owner_mismatch",
                    "main agent owner_id must equal profile_key",
                )
        _descriptor_matches_binding(binding=item.binding, descriptor=item.descriptor)
        seen_keys[domain_key] = item

    missing = set(expected_keys) - set(seen_keys)
    if missing:
        raise ExposureBuildError(
            "exposure_missing",
            f"missing binding inputs for domain keys: {sorted(missing)}",
        )
    if not expected_keys and not allow_empty and binding_inputs:
        raise ExposureBuildError("exposure_extra", "extra bindings for empty Manifest")

    exposures = [
        build_exposure_ref_from_input(seen_keys[key])
        for key in sorted(seen_keys.keys(), key=lambda k: k.encode("utf-8"))
    ]
    return build_manifest_exposure_index(
        manifest_revision=manifest.revision,
        manifest_digest=manifest.manifest_digest,
        exposures=exposures,
    )


def _refs_equal(left: ResolvedCapabilityRef, right: ResolvedCapabilityRef) -> bool:
    return (
        left.capability_type == right.capability_type
        and left.capability_key == right.capability_key
        and left.target_identity == right.target_identity
        and left.target_id == right.target_id
        and left.target_version_id == right.target_version_id
        and left.target_revision == right.target_revision
        and left.input_schema_digest == right.input_schema_digest
        and left.output_schema_digest == right.output_schema_digest
        and left.resolution_digest == right.resolution_digest
        and left.dependency_closure_digest == right.dependency_closure_digest
        and left.binding_contract_digest == right.binding_contract_digest
    )


def choose_batch_owner(
    *,
    existing_owner_version_id: UUID | None,
    candidates: Sequence[tuple[str, UUID]],
) -> UUID:
    """Deterministic batch owner choice (Plan 05 §4.3).

    Existing active owners win. Otherwise the lowest canonical Skill name/version
    UUID in canonical batch order becomes owner so caller list order cannot alter
    policy.
    """
    if existing_owner_version_id is not None:
        return existing_owner_version_id
    if not candidates:
        raise ExposureBuildError("owner_missing", "no candidates for batch owner")
    ordered = sorted(candidates, key=lambda item: (item[0], item[1].bytes))
    return ordered[0][1]


def evaluate_duplicate_capability_compatibility(
    *,
    existing: ExistingExposureCompatibilityView,
    candidate: DuplicateCapabilityDeclaration,
    catalog_skills: Sequence[SkillConflictIdentity] | None = None,
) -> tuple[Literal["compatible", "conflict"], tuple[UUID, ...]]:
    """Strict duplicate Domain Key compatibility (Plan 05 §4.3).

    Returns ("compatible", updated_consumer_ids) or raises ExposureBuildError with
    reason_code=duplicate_capability_policy_conflict.
    """
    if existing.domain_key != candidate.domain_key:
        raise ExposureBuildError(
            "duplicate_capability_policy_conflict",
            "domain key mismatch in duplicate check",
        )

    # 1. Existing owner stays frozen — consumers only append.
    # 2. Exact ref/binding/descriptor/side-effect/schema/executable/timeout/interrupt/parallel/completion.
    if not _refs_equal(existing.resolved_ref, candidate.resolved_ref):
        raise ExposureBuildError(
            "duplicate_capability_policy_conflict",
            "resolved capability ref mismatch",
        )
    field_pairs = (
        ("binding_contract_digest", existing.binding_contract_digest, candidate.binding_contract_digest),
        ("descriptor_digest", existing.descriptor_digest, candidate.descriptor_digest),
        ("side_effect", existing.side_effect, candidate.side_effect),
        ("input_schema_digest", existing.input_schema_digest, candidate.input_schema_digest),
        ("output_schema_digest", existing.output_schema_digest, candidate.output_schema_digest),
        (
            "dependency_closure_digest",
            existing.dependency_closure_digest,
            candidate.dependency_closure_digest,
        ),
        ("resolution_digest", existing.resolution_digest, candidate.resolution_digest),
        ("executable_revision", existing.executable_revision, candidate.executable_revision),
        ("timeout_mode", existing.timeout_mode, candidate.timeout_mode),
        ("timeout_seconds", existing.timeout_seconds, candidate.timeout_seconds),
        ("interrupt_mode", existing.interrupt_mode, candidate.interrupt_mode),
        ("parallel_safe", existing.parallel_safe, candidate.parallel_safe),
        ("terminal_output", existing.terminal_output, candidate.terminal_output),
        ("needs_followup", existing.needs_followup, candidate.needs_followup),
        ("followup_hint", existing.followup_hint, candidate.followup_hint),
    )
    for name, left, right in field_pairs:
        if left != right:
            raise ExposureBuildError(
                "duplicate_capability_policy_conflict",
                f"duplicate capability field mismatch: {name}",
            )

    # 3. Normalized Skill policies must match; each grant admits classified side effect.
    skill_policy_pairs = (
        ("max_skill_calls", existing.max_skill_calls, candidate.max_skill_calls),
        ("max_same_read_calls", existing.max_same_read_calls, candidate.max_same_read_calls),
        (
            "requires_terminal_output",
            existing.requires_terminal_output,
            candidate.requires_terminal_output,
        ),
        (
            "terminal_text_allowed",
            existing.terminal_text_allowed,
            candidate.terminal_text_allowed,
        ),
    )
    for name, left, right in skill_policy_pairs:
        if left is None:
            # Existing main-agent-owned exposure has no skill policy fields; skill
            # re-declaration of a main-agent control is not compatible.
            raise ExposureBuildError(
                "duplicate_capability_policy_conflict",
                f"existing exposure lacks skill policy field {name}",
            )
        if left != right:
            raise ExposureBuildError(
                "duplicate_capability_policy_conflict",
                f"skill policy mismatch: {name}",
            )
    if not existing.grant_admits_side_effect or not candidate.grant_admits_side_effect:
        raise ExposureBuildError(
            "duplicate_capability_policy_conflict",
            "grant does not admit classified side effect",
        )

    # 4. Conflict rules must permit coexistence (symmetric excludes / exclusive_group).
    if not _conflict_rules_permit_coexistence(
        existing=existing,
        candidate=candidate,
        catalog_skills=catalog_skills,
    ):
        raise ExposureBuildError(
            "duplicate_capability_policy_conflict",
            "conflict rules do not permit coexistence",
        )

    # 5. Append later Skill Version in UUID-byte order; no ownership change.
    consumers = set(existing.compatible_consumer_version_ids)
    if candidate.candidate_skill_version_id == existing.owner_version_id:
        # Same version re-declaration is a no-op consumer append.
        return "compatible", tuple(
            sorted(consumers, key=lambda item: item.bytes)
        )
    consumers.add(candidate.candidate_skill_version_id)
    ordered = tuple(sorted(consumers, key=lambda item: item.bytes))
    return "compatible", ordered


def _coexistence_relevant_rules(
    rules: Sequence[SkillConflictRuleV1],
) -> tuple[SkillConflictRuleV1, ...]:
    """Keep only excludes / exclusive_group for duplicate-owner coexistence.

    ``requires`` is an activation-graph constraint evaluated by the full skill
    conflict pass; it is not a pairwise Domain-Key coexistence veto by itself.
    """
    return tuple(rule for rule in rules if rule.kind in {"excludes", "exclusive_group"})


def _conflict_rules_permit_coexistence(
    *,
    existing: ExistingExposureCompatibilityView,
    candidate: DuplicateCapabilityDeclaration,
    catalog_skills: Sequence[SkillConflictIdentity] | None = None,
) -> bool:
    """Symmetric coexistence check via the shared conflict evaluator.

    Reuses ``evaluate_skill_conflicts`` so excludes are bidirectional, names
    resolve through the same catalog alias→canonical path, and exclusive_group
    uses the same strip semantics as ``conflicts.py``.
    """
    existing_name = (existing.owner_canonical_name or "").strip()
    candidate_name = (candidate.candidate_canonical_name or "").strip()
    if not existing_name or not candidate_name:
        # Without both identities we cannot resolve excludes/aliases safely.
        # Fall back to exclusive_group strip-normalized clash only.
        existing_groups = {
            rule.group.strip()
            for rule in existing.conflict_rules
            if rule.kind == "exclusive_group" and rule.group and rule.group.strip()
        }
        candidate_groups = {
            rule.group.strip()
            for rule in candidate.conflict_rules
            if rule.kind == "exclusive_group" and rule.group and rule.group.strip()
        }
        if existing_groups & candidate_groups:
            return False
        # Without owner names, cannot evaluate excludes either direction.
        return True

    existing_identity = SkillConflictIdentity(
        canonical_name=existing_name,
        version_id=existing.owner_version_id,
        aliases=tuple(existing.owner_aliases),
    )
    candidate_identity = SkillConflictIdentity(
        canonical_name=candidate_name,
        version_id=candidate.candidate_skill_version_id,
        aliases=tuple(candidate.candidate_aliases),
    )
    active = SkillConflictParticipant(
        identity=existing_identity,
        conflict_rules=_coexistence_relevant_rules(existing.conflict_rules),
        role="active",
    )
    cand = SkillConflictParticipant(
        identity=candidate_identity,
        conflict_rules=_coexistence_relevant_rules(candidate.conflict_rules),
        role="candidate",
    )
    catalog = catalog_skills
    if catalog is None:
        catalog = (existing_identity, candidate_identity)
    result = evaluate_skill_conflicts(
        active=(active,),
        candidates=(cand,),
        catalog_skills=catalog,
    )
    return result.allowed


def append_compatible_consumer(
    exposure: CapabilityExposureRef,
    *,
    consumer_version_id: UUID,
) -> CapabilityExposureRef:
    """Return a new exposure with consumer appended in UUID-byte order."""
    consumers = set(exposure.compatible_consumer_version_ids)
    consumers.add(consumer_version_id)
    return build_capability_exposure_ref(
        domain_key=exposure.domain_key,
        resolved_ref=exposure.resolved_ref,
        binding_contract_digest=exposure.binding_contract_digest,
        descriptor_digest=exposure.descriptor_digest,
        owner_kind=exposure.owner_kind,
        owner_id=exposure.owner_id,
        owner_version_id=exposure.owner_version_id,
        compatible_consumer_version_ids=tuple(consumers),
    )


def skill_package_ids_from_active(
    active_skills: Sequence[ResolvedSkillRef],
) -> dict[UUID, UUID]:
    return {skill.version_id: skill.package_id for skill in active_skills}


__all__ = [
    "DuplicateCapabilityDeclaration",
    "ExistingExposureCompatibilityView",
    "ExposureBindingInput",
    "ExposureBuildError",
    "ExposureOwnerIdentity",
    "append_compatible_consumer",
    "build_exposure_ref_from_input",
    "build_manifest_exposure_index_from_inputs",
    "choose_batch_owner",
    "evaluate_duplicate_capability_compatibility",
    "resolve_owner_from_binding",
    "skill_package_ids_from_active",
]

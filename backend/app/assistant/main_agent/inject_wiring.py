"""Production skill.inject wiring (Plan 05 enablement residual B/C/E).

Builds SkillActivationCandidates from published Skill versions, freezes
bindings with skill_version provenance, stages via stage_skill_injection,
and on accept registers tools / owner materials / package map / policy
snapshot so post-inject grants and tool surface work.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Callable, Mapping, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.capabilities.contracts import (
    FrozenBindingProvenance,
    FrozenCapabilityBinding,
    project_frozen_capability_binding,
)
from app.assistant.capabilities.runtime import build_capability_runtime
from app.assistant.domain.contracts import (
    CapabilityCompletionContract,
    ResolvedCapabilityBinding,
    ResolvedCapabilityDependency,
    ResolvedCapabilityRef,
    ResolvedRunManifestRevision,
    ResolvedSkillRef,
)
from app.assistant.domain.digests import JsonValue
from app.assistant.main_agent.catalog import (
    CATALOG_CHANGED,
    CatalogCandidateProjection,
    CatalogError,
    CatalogSearchState,
    SKILL_NOT_CATALOGED,
    SKILL_NOT_DISCLOSED,
    build_catalog_snapshot,
)
from app.assistant.main_agent.control_runtime import PendingManifestEffect
from app.assistant.main_agent.manifest_runtime import (
    CandidateExposureView,
    PendingSkillActivationPackage,
    SkillActivationCandidate,
    _candidate_rebind_payload_digest,
    _package_digest,
    candidate_grant_admits_side_effect,
    resolve_inject_selectors,
    stage_skill_injection,
)
from app.assistant.policy.contracts import (
    build_effective_run_policy_snapshot,
    build_owner_policy_ref,
    compute_owner_policy_digest,
)
from app.assistant.policy.evaluator import OwnerGrantMaterial
from app.assistant.policy.exposures import (
    ExposureBindingInput,
    build_manifest_exposure_index_from_inputs,
    resolve_owner_from_binding,
)
from app.assistant.skills.contracts import (
    SkillConflictRuleV1,
    SkillPolicyContract,
)
from app.assistant.skills.models import (
    AssistantSkillCapabilityBinding,
    AssistantSkillCapabilityDependency,
    AssistantSkillPackage,
    AssistantSkillVersion,
)
from app.assistant.skills.package_io import parse_mindatlas_yaml
from app.assistant.skills.resolution import reconstruct_binding_snapshot
from app.assistant.skills.schemas import SkillCatalogScopeV1

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]


def build_run_catalog_state(
    db: Session,
    *,
    scope: SkillCatalogScopeV1 | None = None,
    locale: str = "und",
) -> CatalogSearchState:
    """Project catalog-enabled published packages into a per-Run search state."""
    from app.assistant.skills.models import (
        AssistantSkillPackage,
        AssistantSkillPackageAlias,
        AssistantSkillVersion,
    )

    effective_scope = scope or SkillCatalogScopeV1(mode="all_published", package_ids=())
    q = (
        db.query(AssistantSkillPackage, AssistantSkillVersion)
        .join(
            AssistantSkillVersion,
            AssistantSkillVersion.id == AssistantSkillPackage.published_version_id,
        )
        .filter(AssistantSkillPackage.catalog_enabled.is_(True))
        .filter(AssistantSkillVersion.version_source == "publish")
    )
    if effective_scope.mode == "allowlist":
        allow = set(effective_scope.package_ids)
        if allow:
            q = q.filter(AssistantSkillPackage.id.in_(list(allow)))
        else:
            rows = []
            q = None  # type: ignore[assignment]
    if q is not None:
        rows = q.all()
    else:
        rows = []
    candidates: list[CatalogCandidateProjection] = []
    for package, version in rows:
        aliases = (
            db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == package.id)
            .order_by(AssistantSkillPackageAlias.alias.asc())
            .all()
        )
        alias_names = tuple(
            a.alias
            for a in aliases
            if a.alias and a.alias != package.canonical_name
        )
        # Prefer mindatlas routing examples when present; else empty.
        include_examples: tuple[str, ...] = ()
        exclude_examples: tuple[str, ...] = ()
        conflict_rules: tuple[Any, ...] = ()
        if version.mindatlas_yaml:
            try:
                manifest = parse_mindatlas_yaml(version.mindatlas_yaml.encode("utf-8"))
                include_examples = tuple(manifest.routing.include_examples)
                exclude_examples = tuple(manifest.routing.exclude_examples)
                conflict_rules = tuple(manifest.routing.conflict_rules)
            except Exception:
                pass
        instruction_chars = len(version.skill_md or "")
        candidates.append(
            CatalogCandidateProjection(
                package_id=package.id,
                version_id=version.id,
                canonical_name=package.canonical_name,
                display_name=package.display_name,
                description=package.description or "",
                locale="und",
                aliases=alias_names,
                include_examples=include_examples,
                exclude_examples=exclude_examples,
                content_digest=str(version.content_digest),
                version_digest=str(version.version_digest or ""),
                resource_index_digest=str(version.resource_index_digest or ""),
                binding_set_digest=str(version.binding_set_digest or ("0" * 64)),
                version_source="publish",
                catalog_enabled=bool(package.catalog_enabled),
                conflict_rules=conflict_rules,
                instruction_char_count=instruction_chars,
                bindings_eligible=True,
                resource_index_verified=True,
                binding_set_verified=True,
                ownership_verified=True,
                entrypoint_compatible=True,
                locale_compatible=True,
            )
        )
    snapshot = build_catalog_snapshot(
        candidates,
        scope=effective_scope,
        locale=locale,
    )
    return CatalogSearchState(snapshot)


def _fail_control(call_id: str, code: str, message: str):
    from app.assistant.capabilities.contracts import (
        CapabilityError,
        CapabilityMetrics,
        failed_result,
    )

    return failed_result(
        error=CapabilityError(
            error_type="execution_failed",
            safe_code=code[:64],
            safe_message=message[:256],
            retry_disposition="never",
            call_id=call_id,
        ),
        metrics=CapabilityMetrics(
            duration_ms=0.0, adapter_duration_ms=0.0, input_bytes=0, output_bytes=0
        ),
    )


def _dependency_from_row(
    dep: AssistantSkillCapabilityDependency,
) -> ResolvedCapabilityDependency:
    snap = dep.resolution_snapshot if isinstance(dep.resolution_snapshot, dict) else {}
    return ResolvedCapabilityDependency(
        ordinal=int(dep.ordinal),
        dependency_path=str(dep.dependency_path),
        dependency_type=dep.dependency_type,  # type: ignore[arg-type]
        target_identity=str(dep.target_identity or ""),
        resolved_tool_id=dep.resolved_tool_id,
        resolved_workflow_version_id=dep.resolved_workflow_version_id,
        resolved_agent_version_id=dep.resolved_agent_version_id,
        resolved_model_id=dep.resolved_model_id,
        target_revision=dep.target_revision,
        input_schema=snap.get("inputSchema"),  # type: ignore[arg-type]
        output_schema=snap.get("outputSchema"),  # type: ignore[arg-type]
        input_schema_digest=dep.input_schema_digest,
        output_schema_digest=dep.output_schema_digest,
        resolution_snapshot=snap,  # type: ignore[arg-type]
        resolution_digest=str(dep.resolution_digest or ""),
        dependency_digest=str(dep.dependency_digest or ""),
    )


def reconstruct_resolved_binding(
    binding: AssistantSkillCapabilityBinding,
    dependencies: Sequence[AssistantSkillCapabilityDependency],
) -> ResolvedCapabilityBinding:
    """Rebuild a Plan 01 ResolvedCapabilityBinding from published rows."""
    reconstructed = reconstruct_binding_snapshot(binding, list(dependencies))
    completion_raw = reconstructed.get("completion") or {}
    if not isinstance(completion_raw, dict):
        completion_raw = {}
    completion = CapabilityCompletionContract(
        terminal_output=bool(completion_raw.get("terminalOutput", False)),
        needs_followup=bool(completion_raw.get("needsFollowup", True)),
        followup_hint=completion_raw.get("followupHint"),  # type: ignore[arg-type]
    )
    input_schema = reconstructed.get("inputSchema") or {"type": "object"}
    output_schema = reconstructed.get("outputSchema") or {"type": "object"}
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object"}
    if not isinstance(output_schema, dict):
        output_schema = {"type": "object"}
    target = reconstructed.get("target") if isinstance(reconstructed.get("target"), dict) else {}
    target_id_raw = target.get("targetId")
    target_version_raw = target.get("targetVersionId")
    target_id = UUID(str(target_id_raw)) if target_id_raw else binding.resolved_tool_id
    target_version_id = (
        UUID(str(target_version_raw))
        if target_version_raw
        else (
            binding.resolved_workflow_version_id
            or binding.resolved_agent_version_id
        )
    )
    deps = tuple(_dependency_from_row(d) for d in dependencies)
    return ResolvedCapabilityBinding(
        capability_type=binding.capability_type,  # type: ignore[arg-type]
        capability_key=str(binding.capability_key),
        target_identity=str(binding.target_identity or ""),
        target_id=target_id,
        target_version_id=target_version_id,
        resolved_tool_id=binding.resolved_tool_id,
        resolved_workflow_version_id=binding.resolved_workflow_version_id,
        resolved_agent_version_id=binding.resolved_agent_version_id,
        resolved_revision=binding.resolved_revision,
        input_schema=input_schema,  # type: ignore[arg-type]
        output_schema=output_schema,  # type: ignore[arg-type]
        input_schema_digest=str(binding.input_schema_digest or ""),
        output_schema_digest=str(binding.output_schema_digest or ""),
        completion=completion,
        config_digest=binding.config_digest,
        executable_revision=binding.executable_revision,
        resolution_digest=str(binding.resolution_digest or ""),
        resolution_snapshot=reconstructed,  # type: ignore[arg-type]
        dependencies=deps,
        dependency_closure_digest=str(binding.dependency_closure_digest or ""),
        binding_contract_digest=str(binding.binding_contract_digest or ""),
    )


def freeze_skill_binding(
    *,
    resolved: ResolvedCapabilityBinding,
    skill_version_id: UUID,
    content_digest: str,
    binding_row_id: UUID | None,
) -> FrozenCapabilityBinding:
    provenance = FrozenBindingProvenance(
        origin="skill_version",
        binding_row_id=binding_row_id,
        owner_version_id=skill_version_id,
        source_snapshot_digest=content_digest,
    )
    return project_frozen_capability_binding(resolved=resolved, provenance=provenance)


def _parse_skill_policy(
    version: AssistantSkillVersion,
) -> tuple[SkillPolicyContract, tuple[SkillConflictRuleV1, ...], tuple[str, ...]]:
    """Extract policy + conflict_rules + aliases from published version YAML."""
    policy = SkillPolicyContract()
    conflict_rules: tuple[SkillConflictRuleV1, ...] = ()
    aliases: tuple[str, ...] = ()
    if version.mindatlas_yaml:
        try:
            manifest = parse_mindatlas_yaml(version.mindatlas_yaml.encode("utf-8"))
            policy = manifest.policy
            conflict_rules = tuple(manifest.routing.conflict_rules)
            aliases = tuple(manifest.legacy_aliases)
        except Exception:
            logger.debug(
                "skill policy parse failed version_id=%s", version.id, exc_info=True
            )
    return policy, conflict_rules, aliases


def load_skill_activation_candidate(
    db: Session,
    *,
    version_id: UUID,
    catalog: CatalogSearchState,
) -> SkillActivationCandidate:
    """Load one disclosed published Skill as an activation candidate."""
    if not catalog.is_disclosed(version_id):
        raise CatalogError(SKILL_NOT_DISCLOSED)
    record = catalog.snapshot.get_by_version_id(version_id)
    if record is None:
        raise CatalogError(SKILL_NOT_CATALOGED)

    version = db.get(AssistantSkillVersion, version_id)
    if version is None or version.skill_package_id != record.package_id:
        raise CatalogError(CATALOG_CHANGED)
    if str(version.version_source) != "publish":
        raise CatalogError(CATALOG_CHANGED)
    if version.version_digest != record.version_digest:
        raise CatalogError(CATALOG_CHANGED)
    if version.content_digest != record.content_digest:
        raise CatalogError(CATALOG_CHANGED)

    package = db.get(AssistantSkillPackage, record.package_id)
    if package is None or not bool(package.catalog_enabled):
        raise CatalogError(CATALOG_CHANGED)
    if package.published_version_id != version.id:
        raise CatalogError(CATALOG_CHANGED)

    policy, conflict_rules, aliases = _parse_skill_policy(version)

    binding_rows = (
        db.query(AssistantSkillCapabilityBinding)
        .filter(AssistantSkillCapabilityBinding.skill_version_id == version.id)
        .order_by(AssistantSkillCapabilityBinding.ordinal.asc())
        .all()
    )
    frozen: list[FrozenCapabilityBinding] = []
    caps: list[ResolvedCapabilityRef] = []
    for row in binding_rows:
        if str(row.resolution_status) != "resolved":
            raise CatalogError("bindings_ineligible")
        deps = (
            db.query(AssistantSkillCapabilityDependency)
            .filter(AssistantSkillCapabilityDependency.binding_id == row.id)
            .order_by(AssistantSkillCapabilityDependency.ordinal.asc())
            .all()
        )
        resolved = reconstruct_resolved_binding(row, deps)
        frozen_binding = freeze_skill_binding(
            resolved=resolved,
            skill_version_id=version.id,
            content_digest=version.content_digest,
            binding_row_id=row.id,
        )
        frozen.append(frozen_binding)
        caps.append(frozen_binding.ref)

    skill = ResolvedSkillRef(
        package_id=record.package_id,
        version_id=version.id,
        canonical_name=record.canonical_name,
        sequence=int(version.sequence_no),
        content_digest=version.content_digest,
        version_digest=str(version.version_digest or record.version_digest),
        requested_name_normalized=None,
        resolved_via_alias_id=None,
    )
    is_instruction_only = len(frozen) == 0
    author_effects = tuple(policy.allowed_side_effects) if policy.allowed_side_effects else ()

    return SkillActivationCandidate(
        skill=skill,
        capabilities=tuple(caps),
        frozen_bindings=tuple(frozen),
        instruction_char_count=int(record.instruction_char_count),
        resource_index_digest=record.resource_index_digest,
        author_allowed_side_effects=tuple(author_effects),
        conflict_rules=conflict_rules,
        max_skill_calls=int(policy.max_skill_calls),
        max_same_read_calls=int(policy.max_same_read_calls),
        requires_terminal_output=bool(policy.requires_terminal_output),
        terminal_text_allowed=bool(policy.terminal_text_allowed),
        aliases=aliases or tuple(record.aliases),
        is_instruction_only=is_instruction_only,
    )


def build_candidate_exposure_views(
    *,
    candidates: Sequence[SkillActivationCandidate],
    gateway: Any,
) -> tuple[SkillActivationCandidate, ...]:
    """Freeze descriptor identity used by strict duplicate preflight.

    Published binding rows do not carry the current classified descriptor
    digest. Production injection therefore describes every frozen binding
    before staging and retains the exact digest beside the candidate policy.
    """
    described: list[SkillActivationCandidate] = []
    for candidate in candidates:
        views: list[CandidateExposureView] = []
        for binding in candidate.frozen_bindings:
            descriptor = gateway.describe(binding)
            views.append(
                CandidateExposureView(
                    domain_key=binding.ref.capability_key,
                    resolved_ref=binding.ref,
                    binding_contract_digest=binding.ref.binding_contract_digest,
                    descriptor_digest=descriptor.descriptor_digest,
                    max_skill_calls=candidate.max_skill_calls,
                    max_same_read_calls=candidate.max_same_read_calls,
                    requires_terminal_output=candidate.requires_terminal_output,
                    terminal_text_allowed=candidate.terminal_text_allowed,
                    grant_admits_side_effect=candidate_grant_admits_side_effect(
                        candidate,
                        descriptor.behavior.side_effect,
                    ),
                    descriptor_fields_frozen=True,
                    side_effect=descriptor.behavior.side_effect,
                    executable_revision=descriptor.executable_revision,
                    timeout_mode=descriptor.behavior.timeout_policy.mode,
                    timeout_seconds=(
                        descriptor.behavior.timeout_policy.timeout_seconds
                    ),
                    interrupt_mode=descriptor.behavior.interrupt_mode,
                    parallel_safe=descriptor.behavior.parallel_safe,
                    terminal_output=descriptor.completion.terminal_output,
                    needs_followup=descriptor.completion.needs_followup,
                    followup_hint=descriptor.completion.followup_hint,
                )
            )
        described.append(replace(candidate, exposure_views=tuple(views)))
    return tuple(described)


def build_skill_owner_material(
    candidate: SkillActivationCandidate,
) -> OwnerGrantMaterial:
    """OwnerGrantMaterial for one activated Skill (package_id as owner_id)."""
    package_id = candidate.skill.package_id
    version_id = candidate.skill.version_id
    author = tuple(candidate.author_allowed_side_effects)
    # Lattice classes for policy digest / grants.
    lattice: list[str] = ["none"]
    if "read" in author or "compute" in author:
        if "read" in author:
            lattice.append("read")
        if "compute" in author:
            lattice.append("compute")
    if candidate.is_instruction_only:
        lattice = ["none"]
        declared: frozenset[str] | None = frozenset()
    else:
        declared = frozenset(c.capability_key for c in candidate.capabilities)
    policy_digest = compute_owner_policy_digest(
        owner_kind="skill_version",
        owner_id=str(package_id),
        owner_version_id=version_id,
        content_or_policy_digest=candidate.skill.content_digest,
        allowed_side_effects=tuple(lattice),
    )
    return OwnerGrantMaterial(
        owner_kind="skill_version",
        owner_id=str(package_id),
        owner_version_id=version_id,
        policy_digest=policy_digest,
        author_allowed_side_effects=tuple(author) if not candidate.is_instruction_only else (),
        declared_capability_keys=declared,
        is_instruction_only=bool(candidate.is_instruction_only),
    )


def build_candidate_policy_snapshot(
    *,
    runtime: Any,
    proposed_manifest: ResolvedRunManifestRevision,
    candidates: Sequence[SkillActivationCandidate],
    owner_materials: Sequence[OwnerGrantMaterial],
    session: Session,
    control_port: Any,
    locale: str | None = "en",
) -> Any | None:
    """Build child EffectiveRunPolicySnapshot for the proposed Manifest.

    Returns None when describe cannot complete (caller keeps parent digest path).
    """
    try:
        from app.assistant.main_agent.authorization import LOCAL_ASSISTANT_PRINCIPAL

        # Base control exposures from current runtime snapshot when present.
        base_inputs: list[ExposureBindingInput] = []
        existing_snap = getattr(runtime, "policy_snapshot", None)
        control_bindings = getattr(runtime, "control_bindings", ()) or ()
        profile_key = str(getattr(runtime, "profile_key", "main-agent"))
        profile_version_id = getattr(runtime, "profile_version_id", None)
        profile_content_digest = str(
            getattr(runtime, "profile_content_digest", "") or ""
        )
        app_build_revision = str(getattr(runtime, "app_build_revision", "dev") or "dev")
        run_budget_limits = getattr(runtime, "run_budget_limits", None)
        if run_budget_limits is None:
            return None

        gateway = build_capability_runtime(
            db=session,
            evidence_verifiers={},
            locale=locale,
            main_agent_control_port=control_port,
        )
        # Re-describe the complete proposed surface. A child Manifest is
        # cumulative, so later injections must retain bindings from previously
        # accepted Skills rather than rebuilding from only the current batch.
        package_map = {
            skill.version_id: skill.package_id
            for skill in proposed_manifest.active_skills
        }
        existing_consumer_ids: dict[str, set[UUID]] = {}
        if existing_snap is not None:
            for exposure in existing_snap.exposure_index.exposures:
                existing_consumer_ids[exposure.domain_key] = set(
                    exposure.compatible_consumer_version_ids
                )

        tools_provider = getattr(runtime, "tools_provider", None)
        existing_bindings: Sequence[FrozenCapabilityBinding] = ()
        if tools_provider is not None and hasattr(
            tools_provider, "active_bindings_for_manifest"
        ):
            existing_bindings = tools_provider.active_bindings_for_manifest(
                proposed_manifest
            )
        existing_business_domains = {
            binding.ref.capability_key for binding in existing_bindings
        }
        candidate_claims: dict[str, list[SkillActivationCandidate]] = {}
        for candidate in candidates:
            for binding in candidate.frozen_bindings:
                candidate_claims.setdefault(binding.ref.capability_key, []).append(
                    candidate
                )
        consumer_ids: dict[str, set[UUID]] = {
            key: set(value) for key, value in existing_consumer_ids.items()
        }
        for domain_key, claimants in candidate_claims.items():
            ordered = sorted(
                claimants,
                key=lambda item: (
                    item.skill.canonical_name,
                    item.skill.version_id.bytes,
                ),
            )
            consumers = ordered if domain_key in existing_business_domains else ordered[1:]
            consumer_ids.setdefault(domain_key, set()).update(
                item.skill.version_id for item in consumers
            )

        seen_domains: set[str] = set()
        for binding in control_bindings:
            descriptor = gateway.describe(binding)
            owner = resolve_owner_from_binding(
                binding,
                profile_key=profile_key,
                profile_version_id=profile_version_id,
            )
            base_inputs.append(
                ExposureBindingInput(
                    binding=binding,
                    descriptor=descriptor,
                    owner=owner,
                )
            )
            seen_domains.add(binding.ref.capability_key)

        for binding in existing_bindings:
            domain_key = binding.ref.capability_key
            if domain_key in seen_domains:
                continue
            descriptor = gateway.describe(binding)
            owner = resolve_owner_from_binding(
                binding,
                profile_key=profile_key,
                profile_version_id=profile_version_id,
                skill_package_id_by_version=package_map,
            )
            base_inputs.append(
                ExposureBindingInput(
                    binding=binding,
                    descriptor=descriptor,
                    owner=owner,
                    compatible_consumer_version_ids=tuple(
                        sorted(
                            consumer_ids.get(domain_key, ()),
                            key=lambda item: item.bytes,
                        )
                    ),
                )
            )
            seen_domains.add(domain_key)

        for candidate in sorted(
            candidates,
            key=lambda item: (item.skill.canonical_name, item.skill.version_id.bytes),
        ):
            for binding in candidate.frozen_bindings:
                domain_key = binding.ref.capability_key
                if candidate.skill.version_id in consumer_ids.get(domain_key, set()):
                    continue
                if domain_key in seen_domains:
                    # Strict duplicate compatibility has already established
                    # that this declaration is a non-owning consumer.
                    continue
                descriptor = gateway.describe(binding)
                owner = resolve_owner_from_binding(
                    binding,
                    profile_key=profile_key,
                    profile_version_id=profile_version_id,
                    skill_package_id=candidate.skill.package_id,
                    skill_package_id_by_version=package_map,
                )
                base_inputs.append(
                    ExposureBindingInput(
                        binding=binding,
                        descriptor=descriptor,
                        owner=owner,
                        compatible_consumer_version_ids=tuple(
                            sorted(
                                consumer_ids.get(domain_key, ()),
                                key=lambda item: item.bytes,
                            )
                        ),
                        max_skill_calls=candidate.max_skill_calls,
                        max_same_read_calls=candidate.max_same_read_calls,
                        requires_terminal_output=candidate.requires_terminal_output,
                        terminal_text_allowed=candidate.terminal_text_allowed,
                        conflict_rules=candidate.conflict_rules,
                    )
                )
                seen_domains.add(domain_key)

        exposure_index = build_manifest_exposure_index_from_inputs(
            manifest=proposed_manifest,
            binding_inputs=base_inputs,
            profile_key=profile_key,
            allow_empty=False,
        )
        owner_refs = []
        owner_ref_keys: set[tuple[str, str, UUID]] = set()
        # Preserve every owner ref already frozen into the Run snapshot.
        if existing_snap is not None and getattr(existing_snap, "owner_policy_refs", None):
            for ref in existing_snap.owner_policy_refs:
                owner_refs.append(ref)
                owner_ref_keys.add(
                    (ref.owner_kind, ref.owner_id, ref.owner_version_id)
                )
        main_key = ("main_agent", profile_key, profile_version_id)
        if main_key not in owner_ref_keys:
            main_ref = build_owner_policy_ref(
                owner_kind="main_agent",
                owner_id=profile_key,
                owner_version_id=profile_version_id,
                content_or_policy_digest=profile_content_digest,
                allowed_side_effects=("none", "read", "compute"),
            )
            owner_refs.append(main_ref)
            owner_ref_keys.add(main_key)

        all_materials: dict[tuple[str, str, UUID], OwnerGrantMaterial] = {
            key: value
            for key, value in dict(
                getattr(runtime, "owner_materials", {}) or {}
            ).items()
            if isinstance(value, OwnerGrantMaterial)
        }
        for material in owner_materials:
            all_materials[
                (material.owner_kind, material.owner_id, material.owner_version_id)
            ] = material
        for key in sorted(
            all_materials,
            key=lambda item: (item[0], item[1], item[2].bytes),
        ):
            if key in owner_ref_keys:
                continue
            material = all_materials[key]
            lattice = ["none"]
            for effect in material.author_allowed_side_effects:
                if effect in {"read", "compute"} and effect not in lattice:
                    lattice.append(effect)
            owner_refs.append(
                build_owner_policy_ref(
                    owner_kind="skill_version",  # type: ignore[arg-type]
                    owner_id=material.owner_id,
                    owner_version_id=material.owner_version_id,
                    content_or_policy_digest=material.policy_digest,
                    allowed_side_effects=tuple(lattice),
                )
            )
            owner_ref_keys.add(key)
        return build_effective_run_policy_snapshot(
            app_build_revision=app_build_revision,
            run_id=proposed_manifest.run_id,
            principal=LOCAL_ASSISTANT_PRINCIPAL,
            main_agent_profile_version_id=profile_version_id,
            main_agent_profile_digest=profile_content_digest,
            exposure_index=exposure_index,
            owner_policy_refs=tuple(owner_refs),
            run_budget_limits=run_budget_limits,
        )
    except Exception:
        logger.exception("candidate policy snapshot build failed")
        return None


def apply_accept_package_rebind(
    *,
    runtime: Any,
    tools_provider: Any | None,
    ports_owner_resolver: Any | None,
    manifest: ResolvedRunManifestRevision,
    package: PendingSkillActivationPackage,
) -> None:
    """Atomically rebind accepted tools/auth/owners/policy within lifecycle accept."""
    del ports_owner_resolver  # shared via runtime._owner_resolver
    consumer_pairs = set(package.candidate_compatible_consumers)
    owned_bindings_by_version: dict[UUID, tuple[FrozenCapabilityBinding, ...]] = {}
    for version_id, bindings in package.candidate_frozen_bindings_by_version.items():
        owned = tuple(
            binding
            for binding in bindings
            if (binding.ref.capability_key, version_id) not in consumer_pairs
        )
        if owned:
            owned_bindings_by_version[version_id] = owned

    auth = getattr(runtime, "authorization_factory", None)
    owner_materials_update: dict[tuple[str, str, UUID], OwnerGrantMaterial] = {}
    for material in package.candidate_owner_materials:
        if isinstance(material, OwnerGrantMaterial):
            key = (material.owner_kind, material.owner_id, material.owner_version_id)
            owner_materials_update[key] = material
    active_candidates_update = {
        candidate.skill.version_id: candidate
        for candidate in package.candidate_activation_candidates
    }
    owners = dict(getattr(runtime, "owners_by_domain_key", {}) or {})
    for version_id, bindings in owned_bindings_by_version.items():
        for binding in bindings:
            domain_key = binding.ref.capability_key
            owners[domain_key] = ("skill_version", version_id)
    snap = package.candidate_policy_snapshot
    effective_snap = snap or getattr(runtime, "policy_snapshot", None)

    # Snapshot every mutable projection before the first write. The lifecycle
    # still owns the outer lock; these copies make an unexpected dependent
    # failure reversible before the Manifest pointer is published.
    tools_before = dict(
        getattr(tools_provider, "active_bindings_by_version", {}) or {}
    )
    runtime_materials_before = dict(getattr(runtime, "owner_materials", {}) or {})
    runtime_candidates_before = dict(
        getattr(runtime, "active_skill_candidates_by_version", {}) or {}
    )
    runtime_owners_before = dict(getattr(runtime, "owners_by_domain_key", {}) or {})
    runtime_policy_before = getattr(runtime, "policy_snapshot", None)
    runtime_manifest_before = getattr(runtime, "manifest", None)
    resolver = getattr(runtime, "_owner_resolver", None)
    resolver_before = (
        dict(getattr(resolver, "owners_by_domain_key", {}) or {})
        if resolver is not None
        else None
    )
    control_runtime = getattr(runtime, "control_runtime", None)
    control_manifest_before = getattr(control_runtime, "_current_manifest", None)
    auth_before: dict[str, Any] | None = None
    if auth is not None:
        auth_before = {
            "manifest": getattr(auth, "manifest", None),
            "skill_package_id_by_version": dict(
                getattr(auth, "skill_package_id_by_version", {}) or {}
            ),
            "skill_content_digest_by_version": dict(
                getattr(auth, "skill_content_digest_by_version", {}) or {}
            ),
            "policy_snapshot": getattr(auth, "policy_snapshot", None),
            "owner_materials": dict(getattr(auth, "owner_materials", {}) or {}),
        }

    try:
        if tools_provider is not None and hasattr(
            tools_provider, "register_active_bindings"
        ):
            for version_id, bindings in owned_bindings_by_version.items():
                tools_provider.register_active_bindings(version_id, bindings)

        runtime.owner_materials.update(owner_materials_update)
        active_candidates = getattr(
            runtime, "active_skill_candidates_by_version", None
        )
        if active_candidates is not None:
            active_candidates.update(active_candidates_update)
        if auth is not None:
            auth.rebind_manifest(
                manifest,
                skill_package_id_by_version=(
                    package.candidate_skill_package_id_by_version or None
                ),
                skill_content_digest_by_version=(
                    package.candidate_skill_content_digest_by_version or None
                ),
                policy_snapshot=effective_snap,
                owner_materials=owner_materials_update or None,
            )
        if hasattr(runtime, "rebind_owners"):
            runtime.rebind_owners(owners)
        if snap is not None:
            runtime.policy_snapshot = snap
        if control_runtime is not None and hasattr(control_runtime, "bind_manifest"):
            control_runtime.bind_manifest(manifest)
        if hasattr(runtime, "manifest"):
            runtime.manifest = manifest
    except Exception:
        if tools_provider is not None:
            if hasattr(tools_provider, "restore_active_bindings"):
                tools_provider.restore_active_bindings(tools_before)
            elif hasattr(tools_provider, "active_bindings_by_version"):
                lock = getattr(tools_provider, "_lock", None)
                if lock is None:
                    tools_provider.active_bindings_by_version = tools_before
                else:
                    with lock:
                        tools_provider.active_bindings_by_version.clear()
                        tools_provider.active_bindings_by_version.update(tools_before)
        runtime.owner_materials.clear()
        runtime.owner_materials.update(runtime_materials_before)
        active_candidates = getattr(
            runtime, "active_skill_candidates_by_version", None
        )
        if active_candidates is not None:
            active_candidates.clear()
            active_candidates.update(runtime_candidates_before)
        runtime.owners_by_domain_key = runtime_owners_before
        if resolver is not None and resolver_before is not None:
            resolver.rebind(resolver_before)
        runtime.policy_snapshot = runtime_policy_before
        if hasattr(runtime, "manifest"):
            runtime.manifest = runtime_manifest_before
        if control_runtime is not None and hasattr(control_runtime, "bind_manifest"):
            control_runtime.bind_manifest(control_manifest_before)
        if auth is not None and auth_before is not None:
            lock = getattr(auth, "_lock", None)
            if lock is None:
                auth.manifest = auth_before["manifest"]
                auth.skill_package_id_by_version = auth_before[
                    "skill_package_id_by_version"
                ]
                auth.skill_content_digest_by_version = auth_before[
                    "skill_content_digest_by_version"
                ]
                auth.policy_snapshot = auth_before["policy_snapshot"]
                auth.owner_materials = auth_before["owner_materials"]
            else:
                with lock:
                    auth.manifest = auth_before["manifest"]
                    auth.skill_package_id_by_version = auth_before[
                        "skill_package_id_by_version"
                    ]
                    auth.skill_content_digest_by_version = auth_before[
                        "skill_content_digest_by_version"
                    ]
                    auth.policy_snapshot = auth_before["policy_snapshot"]
                    auth.owner_materials = auth_before["owner_materials"]
        raise


def build_production_inject_handler(
    *,
    runtime: Any,
    tools_provider: Any | None,
    session_factory: SessionFactory,
    catalog_state: CatalogSearchState | None,
    locale: str | None = "en",
) -> Callable[
    [str, dict[str, JsonValue], ResolvedRunManifestRevision],
    tuple[Any, PendingManifestEffect | None],
]:
    """Build inject_handler closing over Run policy runtime + catalog."""

    from app.assistant.main_agent.policy_runtime import (
        skill_injection_policy_context_from_runtime,
    )

    lifecycle = runtime.lifecycle

    def _handler(
        call_id: str,
        validated_input: dict[str, JsonValue],
        current_manifest: ResolvedRunManifestRevision,
    ):
        catalog = catalog_state
        if catalog is None:
            # Prefer live catalog bound on control runtime.
            control = getattr(runtime, "control_runtime", None)
            catalog = getattr(control, "_catalog_state", None) if control else None
        if catalog is None:
            return (
                _fail_control(call_id, "catalog_unavailable", "skill catalog unavailable"),
                None,
            )

        skills_input = validated_input.get("skills") or validated_input.get("Skills") or []
        if not isinstance(skills_input, list):
            return (
                _fail_control(call_id, "invalid_input", "skills must be a list"),
                None,
            )

        try:
            version_ids = resolve_inject_selectors(
                catalog=catalog,
                skills_input=skills_input,  # type: ignore[arg-type]
            )
        except CatalogError as exc:
            return (
                _fail_control(
                    call_id,
                    getattr(exc, "reason_code", None) or "invalid_input",
                    "skill inject selector failed",
                ),
                None,
            )

        session = session_factory()
        try:
            candidates: list[SkillActivationCandidate] = []
            for vid in version_ids:
                try:
                    candidates.append(
                        load_skill_activation_candidate(
                            session, version_id=vid, catalog=catalog
                        )
                    )
                except CatalogError as exc:
                    return (
                        _fail_control(
                            call_id,
                            getattr(exc, "reason_code", None) or "skill_not_cataloged",
                            "skill activation candidate failed",
                        ),
                        None,
                    )

            if any(candidate.frozen_bindings for candidate in candidates):
                try:
                    describe_gateway = build_capability_runtime(
                        db=session,
                        evidence_verifiers={},
                        locale=locale,
                        main_agent_control_port=getattr(
                            runtime, "control_runtime", None
                        ),
                    )
                    candidates = list(
                        build_candidate_exposure_views(
                            candidates=candidates,
                            gateway=describe_gateway,
                        )
                    )
                except Exception:
                    logger.exception("candidate exposure describe failed")
                    return (
                        _fail_control(
                            call_id,
                            "policy_snapshot_unavailable",
                            "candidate policy snapshot unavailable",
                        ),
                        None,
                    )

            policy_ctx = skill_injection_policy_context_from_runtime(runtime)
            max_active = int(policy_ctx.run_max_active_skills)
            result, effect, package = stage_skill_injection(
                call_id=call_id,
                current_manifest=current_manifest,
                candidates=candidates,
                max_active_skills=max_active,
                lifecycle=lifecycle,
                policy=policy_ctx,
            )
            if package is None or effect is None:
                return result, effect

            # Attach owner materials + optional child policy snapshot.
            owner_materials = tuple(
                build_skill_owner_material(c) for c in candidates if c.skill.version_id
                in set(package.activated_version_ids)
            )
            package.candidate_owner_materials = owner_materials
            package.candidate_activation_candidates = tuple(
                c
                for c in candidates
                if c.skill.version_id in set(package.activated_version_ids)
            )
            package.require_finished_reservation = bool(
                getattr(runtime, "enforce_skill_inject_reservation", False)
            )

            # Align proposed Manifest effective_policy_digest when we can build
            # a real child snapshot (otherwise keep stage-derived digest).
            child_snap = build_candidate_policy_snapshot(
                runtime=runtime,
                proposed_manifest=effect.proposed_manifest,
                candidates=[
                    c
                    for c in candidates
                    if c.skill.version_id in set(package.activated_version_ids)
                ],
                owner_materials=owner_materials,
                session=session,
                control_port=getattr(runtime, "control_runtime", None),
                locale=locale,
            )
            if child_snap is None:
                if lifecycle is not None:
                    lifecycle.discard(
                        call_id=call_id,
                        reason_code="policy_snapshot_unavailable",
                    )
                return (
                    _fail_control(
                        call_id,
                        "policy_snapshot_unavailable",
                        "candidate policy snapshot unavailable",
                    ),
                    None,
                )
            package.candidate_policy_snapshot = child_snap
            package.candidate_effective_policy_digest = (
                child_snap.effective_policy_digest
            )
            # Rebuild proposed Manifest so digest matches child snapshot.
            # Must also recompute effect_digest — accept seals against it.
            from app.assistant.domain.contracts import (
                ResolvedRunManifestRevision as RMR,
                compute_manifest_digest,
            )
            from app.assistant.main_agent.manifest_runtime import _effect_digest

            proposed = effect.proposed_manifest
            if proposed.effective_policy_digest != child_snap.effective_policy_digest:
                aligned = compute_manifest_digest(
                    run_id=proposed.run_id,
                    revision=proposed.revision,
                    parent_digest=proposed.parent_digest,
                    main_agent=proposed.main_agent,
                    active_skills=proposed.active_skills,
                    capabilities=proposed.capabilities,
                    provider=proposed.provider,
                    model=proposed.model,
                    provider_aliases=proposed.provider_aliases,
                    effective_policy_digest=child_snap.effective_policy_digest,
                )
                new_manifest = RMR(
                    run_id=proposed.run_id,
                    revision=proposed.revision,
                    parent_digest=proposed.parent_digest,
                    main_agent=proposed.main_agent,
                    active_skills=proposed.active_skills,
                    capabilities=proposed.capabilities,
                    provider=proposed.provider,
                    model=proposed.model,
                    provider_aliases=proposed.provider_aliases,
                    effective_policy_digest=child_snap.effective_policy_digest,
                    manifest_digest=aligned,
                )
                # Rebuild child snap against aligned manifest association.
                child_snap2 = build_candidate_policy_snapshot(
                    runtime=runtime,
                    proposed_manifest=new_manifest,
                    candidates=[
                        c
                        for c in candidates
                        if c.skill.version_id in set(package.activated_version_ids)
                    ],
                    owner_materials=owner_materials,
                    session=session,
                    control_port=getattr(runtime, "control_runtime", None),
                    locale=locale,
                )
                if child_snap2 is None:
                    if lifecycle is not None:
                        lifecycle.discard(
                            call_id=call_id,
                            reason_code="policy_snapshot_unavailable",
                        )
                    return (
                        _fail_control(
                            call_id,
                            "policy_snapshot_unavailable",
                            "candidate policy snapshot unavailable",
                        ),
                        None,
                    )
                package.candidate_policy_snapshot = child_snap2
                package.candidate_effective_policy_digest = (
                    child_snap2.effective_policy_digest
                )
                # If digest shifted again, re-align once more to the final snap.
                if (
                    new_manifest.effective_policy_digest
                    != child_snap2.effective_policy_digest
                ):
                    aligned = compute_manifest_digest(
                        run_id=proposed.run_id,
                        revision=proposed.revision,
                        parent_digest=proposed.parent_digest,
                        main_agent=proposed.main_agent,
                        active_skills=proposed.active_skills,
                        capabilities=proposed.capabilities,
                        provider=proposed.provider,
                        model=proposed.model,
                        provider_aliases=proposed.provider_aliases,
                        effective_policy_digest=child_snap2.effective_policy_digest,
                    )
                    new_manifest = RMR(
                        run_id=proposed.run_id,
                        revision=proposed.revision,
                        parent_digest=proposed.parent_digest,
                        main_agent=proposed.main_agent,
                        active_skills=proposed.active_skills,
                        capabilities=proposed.capabilities,
                        provider=proposed.provider,
                        model=proposed.model,
                        provider_aliases=proposed.provider_aliases,
                        effective_policy_digest=child_snap2.effective_policy_digest,
                        manifest_digest=aligned,
                    )
                effect.proposed_manifest = new_manifest
                effect.effect_digest = _effect_digest(
                    call_id=call_id,
                    parent_revision=effect.expected_parent_revision,
                    parent_digest=effect.expected_parent_digest,
                    proposed=new_manifest,
                )
                if isinstance(effect.activation_payload, dict):
                    effect.activation_payload["effectivePolicyDigest"] = (
                        new_manifest.effective_policy_digest
                    )
                package.effect = effect
            if (
                lifecycle is not None
                and package.candidate_policy_snapshot is not None
            ):
                lifecycle.register_policy_snapshot(
                    package.candidate_policy_snapshot
                )

            # The production child snapshot is computed after the pure stage
            # step. Re-seal every staged projection against the final child so
            # lifecycle.accept verifies the same policy/Manifest identity that
            # the Tool Result and post-commit events advertise.
            final_policy_digest = (
                package.candidate_effective_policy_digest
                or effect.proposed_manifest.effective_policy_digest
            )
            effect.activation_payload["effectivePolicyDigest"] = final_policy_digest

            final_events: list[dict[str, JsonValue]] = []
            for event in effect.post_commit_events:
                aligned_event = dict(event)
                if aligned_event.get("eventType") == "skill_activation_end":
                    aligned_event["manifestRevision"] = effect.proposed_manifest.revision
                    aligned_event["manifestDigest"] = (
                        effect.proposed_manifest.manifest_digest
                    )
                    aligned_event["effectivePolicyDigest"] = final_policy_digest
                elif aligned_event.get("eventType") == "manifest_revision":
                    aligned_event["revision"] = effect.proposed_manifest.revision
                    aligned_event["manifestDigest"] = (
                        effect.proposed_manifest.manifest_digest
                    )
                    aligned_event["effectivePolicyDigest"] = final_policy_digest
                final_events.append(aligned_event)
            effect.post_commit_events = tuple(final_events)
            package.post_commit_events = effect.post_commit_events
            package.effect = effect
            package.candidate_rebind_payload_digest = (
                _candidate_rebind_payload_digest(package)
            )
            final_package_digest = _package_digest(
                call_id=call_id,
                activated_version_ids=package.activated_version_ids,
                owner_budget_digests=tuple(
                    item.owner_budget_digest
                    for item in package.candidate_owner_budget_limits
                ),
                skill_terminal_ids=tuple(
                    version_id
                    for version_id, _terminal_text, _package_id in (
                        package.candidate_skill_terminals
                    )
                ),
                compatible_consumers=package.candidate_compatible_consumers,
                effective_policy_digest=final_policy_digest,
                rebind_payload_digest=package.candidate_rebind_payload_digest,
            )
            package.package_digest = final_package_digest
            effect.activation_payload["packageDigest"] = final_package_digest

            result_payload = dict(result.structured_output or {})
            result_payload["proposedManifestRevision"] = (
                effect.proposed_manifest.revision
            )
            result_payload["proposedManifestDigest"] = (
                effect.proposed_manifest.manifest_digest
            )
            result_payload["effectivePolicyDigest"] = final_policy_digest
            result_payload["packageDigest"] = final_package_digest
            from app.assistant.capabilities.contracts import completed_result

            result = completed_result(
                user_text=result.user_text,
                structured_output=result_payload,  # type: ignore[arg-type]
                artifact_refs=result.artifact_refs,
                metrics=result.metrics,
                terminal_output=bool(result.terminal_output),
                needs_followup=bool(result.needs_followup),
            )

            return result, effect
        finally:
            session.close()

    return _handler


def install_accept_rebind_hooks(
    *,
    runtime: Any,
    tools_provider: Any | None,
) -> None:
    """Register package-aware accept hook on the runtime lifecycle."""
    lifecycle = getattr(runtime, "lifecycle", None)
    if lifecycle is None or not hasattr(lifecycle, "add_on_accept_package_hook"):
        return

    def _hook(
        manifest: ResolvedRunManifestRevision,
        package: PendingSkillActivationPackage,
    ) -> None:
        apply_accept_package_rebind(
            runtime=runtime,
            tools_provider=tools_provider,
            ports_owner_resolver=getattr(runtime, "call_owner_resolver", None),
            manifest=manifest,
            package=package,
        )

    lifecycle.add_on_accept_package_hook(_hook)


__all__ = [
    "apply_accept_package_rebind",
    "build_candidate_exposure_views",
    "build_candidate_policy_snapshot",
    "build_production_inject_handler",
    "build_run_catalog_state",
    "build_skill_owner_material",
    "freeze_skill_binding",
    "install_accept_rebind_hooks",
    "load_skill_activation_candidate",
    "reconstruct_resolved_binding",
]

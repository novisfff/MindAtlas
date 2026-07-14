from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.assistant.domain.digests import JsonValue, sha256_canonical_json
from app.common.schemas import to_camel

# Pydantic cannot materialize the recursive JsonValue alias as a field annotation.
# Domain payloads still carry JSON-compatible dicts; digests enforce the narrow contract.
JsonObject = dict[str, Any]

# ---------------------------------------------------------------------------
# Locked domain vocabulary (Decision 9)
# ---------------------------------------------------------------------------

CapabilityType = Literal["tool", "workflow", "agent"]
BindingResolutionStatus = Literal["unresolved", "resolved"]
SkillPackageMigrationState = Literal["shadow", "native", "cutover"]
MainAgentMigrationState = Literal["bootstrap", "shadow", "native", "cutover"]
VersionSource = Literal["save", "publish"]
DeclaredSideEffect = Literal["read", "compute", "draft", "write", "control"]
AgentLoopStatus = Literal[
    "completed",
    "waiting_input",
    "waiting_approval",
    "needs_reconciliation",
    "failed",
    "cancelled",
]
CapabilityCallStatus = Literal[
    "pending",
    "running",
    "deferred",
    "blocked",
    "waiting_approval",
    "waiting_input",
    "completed",
    "failed",
    "cancelled",
    "unknown",
    "needs_reconciliation",
]

MAX_CAPABILITY_CLOSURE_DEPTH = 16
MAX_CAPABILITY_CLOSURE_REFS = 256
MAX_CAPABILITY_CLASSIFIED_NODES = 4096


class SkillVersionConflictError(ValueError):
    """Raised when the same canonical Skill is activated at a different version."""


class FrozenContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
    )


class ProviderRef(FrozenContract):
    schema_version: Literal[1] = 1
    provider_protocol: str
    provider_config_id: UUID | None
    provider_runtime_revision: int | None
    provider_config_digest: str | None
    adapter_key: str | None
    adapter_revision: str | None
    protocol_revision: str | None
    app_build_revision: str | None
    provider_ref_digest: str


class ModelRef(FrozenContract):
    schema_version: Literal[1] = 1
    model_id: UUID
    model_name: str
    model_type: Literal["llm", "embedding"]
    model_runtime_revision: int | None
    credential_id: UUID
    credential_runtime_revision: int | None
    credential_config_digest: str | None
    model_config_digest: str | None
    provider_ref_digest: str | None
    capability_probe_id: UUID | None
    capability_probe_digest: str | None
    model_ref_digest: str


class ResolvedProviderAliasRef(FrozenContract):
    provider_protocol: str
    domain_key: str
    provider_alias: str
    binding_contract_digest: str


class ResolvedMainAgentRef(FrozenContract):
    profile_id: UUID
    version_id: UUID
    profile_key: str
    sequence: int
    content_digest: str


class ResolvedSkillRef(FrozenContract):
    package_id: UUID
    version_id: UUID
    canonical_name: str
    sequence: int
    content_digest: str
    version_digest: str
    requested_name_normalized: str | None
    resolved_via_alias_id: UUID | None


class ResolvedCapabilityRef(FrozenContract):
    capability_type: CapabilityType
    capability_key: str
    target_identity: str
    target_id: UUID | None
    target_version_id: UUID | None
    target_revision: int | None
    input_schema_digest: str
    output_schema_digest: str
    resolution_digest: str
    dependency_closure_digest: str
    binding_contract_digest: str


class ResolvedRunManifestRevision(FrozenContract):
    schema_version: Literal[1] = 1
    run_id: UUID
    revision: int
    parent_digest: str | None
    main_agent: ResolvedMainAgentRef
    active_skills: tuple[ResolvedSkillRef, ...]
    capabilities: tuple[ResolvedCapabilityRef, ...]
    provider: ProviderRef | None
    model: ModelRef | None
    provider_aliases: tuple[ResolvedProviderAliasRef, ...] = ()
    effective_policy_digest: str | None
    manifest_digest: str


class CapabilityCompletionContract(FrozenContract):
    terminal_output: bool = False
    needs_followup: bool = True
    followup_hint: str | None = None


class ToolParamContract(FrozenContract):
    name: str
    description: str | None = None
    param_type: Literal["string", "number", "boolean", "array", "object"]
    required: bool = False
    items_type: Literal["string", "number", "boolean", "object"] | None = None


# Parser/service DTOs reserved by Decision 9 (defined here so later tasks import
# one vocabulary). Task 2 owns the package parser models that fill these shapes.


class ParsedSkillResource(FrozenContract):
    path: str
    resource_kind: Literal["scripts", "references", "assets", "other"]
    media_type: str
    content: bytes
    byte_size: int
    sha256: str
    executable: Literal[False] = False


class SkillResourceIndexEntry(FrozenContract):
    path: str
    resource_kind: Literal["scripts", "references", "assets", "other"]
    media_type: str
    byte_size: int
    sha256: str


class StoredSkillResource(FrozenContract):
    """Explicitly loaded resource bytes for export/retrieval ports.

    Never present in ordinary list/detail DTOs.
    """

    path: str
    resource_kind: Literal["scripts", "references", "assets", "other"]
    media_type: str
    byte_size: int
    sha256: str
    content: bytes


class ResolvedCapabilityDependency(FrozenContract):
    ordinal: int
    dependency_path: str
    dependency_type: Literal["system_tool", "remote_tool", "workflow", "agent", "model"]
    target_identity: str
    resolved_tool_id: UUID | None
    resolved_workflow_version_id: UUID | None
    resolved_agent_version_id: UUID | None
    resolved_model_id: UUID | None
    target_revision: int | None
    input_schema: JsonObject | None
    output_schema: JsonObject | None
    input_schema_digest: str | None
    output_schema_digest: str | None
    resolution_snapshot: JsonObject
    resolution_digest: str
    dependency_digest: str


class ResolvedCapabilityBinding(FrozenContract):
    capability_type: CapabilityType
    capability_key: str
    target_identity: str
    target_id: UUID | None
    target_version_id: UUID | None
    resolved_tool_id: UUID | None
    resolved_workflow_version_id: UUID | None
    resolved_agent_version_id: UUID | None
    resolved_revision: int | None
    input_schema: JsonObject
    output_schema: JsonObject
    input_schema_digest: str
    output_schema_digest: str
    completion: CapabilityCompletionContract
    config_digest: str | None
    executable_revision: str | None
    resolution_digest: str
    resolution_snapshot: JsonObject
    dependencies: tuple[ResolvedCapabilityDependency, ...]
    dependency_closure_digest: str
    binding_contract_digest: str


class CurrentCapabilityDependencyReference(FrozenContract):
    dependency_path: str
    target_identity: str
    target_id: UUID | None
    target_version_id: UUID | None
    target_revision: int | None
    executable_revision: str | None
    resolution_digest: str
    dependency_digest: str


class CurrentCapabilityReference(FrozenContract):
    target_identity: str
    target_id: UUID | None
    target_version_id: UUID | None
    target_revision: int | None
    executable_revision: str | None
    system_tool_contract_set_digest: str | None
    input_schema_digest: str
    output_schema_digest: str
    resolution_digest: str
    dependency_closure_digest: str
    dependencies: tuple[CurrentCapabilityDependencyReference, ...]


def _uuid_json(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _provider_payload(
    *,
    provider_protocol: str,
    provider_config_id: UUID | None,
    provider_runtime_revision: int | None,
    provider_config_digest: str | None,
    adapter_key: str | None,
    adapter_revision: str | None,
    protocol_revision: str | None,
    app_build_revision: str | None,
) -> dict[str, JsonValue]:
    return {
        "schemaVersion": 1,
        "providerProtocol": provider_protocol,
        "providerConfigId": _uuid_json(provider_config_id),
        "providerRuntimeRevision": provider_runtime_revision,
        "providerConfigDigest": provider_config_digest,
        "adapterKey": adapter_key,
        "adapterRevision": adapter_revision,
        "protocolRevision": protocol_revision,
        "appBuildRevision": app_build_revision,
    }


def create_provider_ref(
    *,
    provider_protocol: str,
    provider_config_id: UUID | None,
    provider_runtime_revision: int | None,
    provider_config_digest: str | None,
    adapter_key: str | None,
    adapter_revision: str | None,
    protocol_revision: str | None,
    app_build_revision: str | None,
) -> ProviderRef:
    payload = _provider_payload(
        provider_protocol=provider_protocol,
        provider_config_id=provider_config_id,
        provider_runtime_revision=provider_runtime_revision,
        provider_config_digest=provider_config_digest,
        adapter_key=adapter_key,
        adapter_revision=adapter_revision,
        protocol_revision=protocol_revision,
        app_build_revision=app_build_revision,
    )
    return ProviderRef(
        provider_protocol=provider_protocol,
        provider_config_id=provider_config_id,
        provider_runtime_revision=provider_runtime_revision,
        provider_config_digest=provider_config_digest,
        adapter_key=adapter_key,
        adapter_revision=adapter_revision,
        protocol_revision=protocol_revision,
        app_build_revision=app_build_revision,
        provider_ref_digest=sha256_canonical_json(payload),
    )


def _model_payload(
    *,
    model_id: UUID,
    model_name: str,
    model_type: Literal["llm", "embedding"],
    model_runtime_revision: int | None,
    credential_id: UUID,
    credential_runtime_revision: int | None,
    credential_config_digest: str | None,
    model_config_digest: str | None,
    provider_ref_digest: str | None,
    capability_probe_id: UUID | None,
    capability_probe_digest: str | None,
) -> dict[str, JsonValue]:
    return {
        "schemaVersion": 1,
        "modelId": str(model_id),
        "modelName": model_name,
        "modelType": model_type,
        "modelRuntimeRevision": model_runtime_revision,
        "credentialId": str(credential_id),
        "credentialRuntimeRevision": credential_runtime_revision,
        "credentialConfigDigest": credential_config_digest,
        "modelConfigDigest": model_config_digest,
        "providerRefDigest": provider_ref_digest,
        "capabilityProbeId": _uuid_json(capability_probe_id),
        "capabilityProbeDigest": capability_probe_digest,
    }


def create_model_ref(
    *,
    model_id: UUID,
    model_name: str,
    model_type: Literal["llm", "embedding"],
    model_runtime_revision: int | None,
    credential_id: UUID,
    credential_runtime_revision: int | None,
    credential_config_digest: str | None,
    model_config_digest: str | None,
    provider_ref_digest: str | None,
    capability_probe_id: UUID | None,
    capability_probe_digest: str | None,
) -> ModelRef:
    payload = _model_payload(
        model_id=model_id,
        model_name=model_name,
        model_type=model_type,
        model_runtime_revision=model_runtime_revision,
        credential_id=credential_id,
        credential_runtime_revision=credential_runtime_revision,
        credential_config_digest=credential_config_digest,
        model_config_digest=model_config_digest,
        provider_ref_digest=provider_ref_digest,
        capability_probe_id=capability_probe_id,
        capability_probe_digest=capability_probe_digest,
    )
    return ModelRef(
        model_id=model_id,
        model_name=model_name,
        model_type=model_type,
        model_runtime_revision=model_runtime_revision,
        credential_id=credential_id,
        credential_runtime_revision=credential_runtime_revision,
        credential_config_digest=credential_config_digest,
        model_config_digest=model_config_digest,
        provider_ref_digest=provider_ref_digest,
        capability_probe_id=capability_probe_id,
        capability_probe_digest=capability_probe_digest,
        model_ref_digest=sha256_canonical_json(payload),
    )


def _main_agent_payload(main_agent: ResolvedMainAgentRef) -> dict[str, JsonValue]:
    return {
        "profileId": str(main_agent.profile_id),
        "versionId": str(main_agent.version_id),
        "profileKey": main_agent.profile_key,
        "sequence": main_agent.sequence,
        "contentDigest": main_agent.content_digest,
    }


def _skill_payload(skill: ResolvedSkillRef) -> dict[str, JsonValue]:
    return {
        "packageId": str(skill.package_id),
        "versionId": str(skill.version_id),
        "canonicalName": skill.canonical_name,
        "sequence": skill.sequence,
        "contentDigest": skill.content_digest,
        "versionDigest": skill.version_digest,
        "requestedNameNormalized": skill.requested_name_normalized,
        "resolvedViaAliasId": _uuid_json(skill.resolved_via_alias_id),
    }


def _capability_payload(capability: ResolvedCapabilityRef) -> dict[str, JsonValue]:
    return {
        "capabilityType": capability.capability_type,
        "capabilityKey": capability.capability_key,
        "targetIdentity": capability.target_identity,
        "targetId": _uuid_json(capability.target_id),
        "targetVersionId": _uuid_json(capability.target_version_id),
        "targetRevision": capability.target_revision,
        "inputSchemaDigest": capability.input_schema_digest,
        "outputSchemaDigest": capability.output_schema_digest,
        "resolutionDigest": capability.resolution_digest,
        "dependencyClosureDigest": capability.dependency_closure_digest,
        "bindingContractDigest": capability.binding_contract_digest,
    }


def _provider_ref_payload(provider: ProviderRef) -> dict[str, JsonValue]:
    payload = _provider_payload(
        provider_protocol=provider.provider_protocol,
        provider_config_id=provider.provider_config_id,
        provider_runtime_revision=provider.provider_runtime_revision,
        provider_config_digest=provider.provider_config_digest,
        adapter_key=provider.adapter_key,
        adapter_revision=provider.adapter_revision,
        protocol_revision=provider.protocol_revision,
        app_build_revision=provider.app_build_revision,
    )
    payload["providerRefDigest"] = provider.provider_ref_digest
    return payload


def _model_ref_payload(model: ModelRef) -> dict[str, JsonValue]:
    payload = _model_payload(
        model_id=model.model_id,
        model_name=model.model_name,
        model_type=model.model_type,
        model_runtime_revision=model.model_runtime_revision,
        credential_id=model.credential_id,
        credential_runtime_revision=model.credential_runtime_revision,
        credential_config_digest=model.credential_config_digest,
        model_config_digest=model.model_config_digest,
        provider_ref_digest=model.provider_ref_digest,
        capability_probe_id=model.capability_probe_id,
        capability_probe_digest=model.capability_probe_digest,
    )
    payload["modelRefDigest"] = model.model_ref_digest
    return payload


def _alias_payload(alias: ResolvedProviderAliasRef) -> dict[str, JsonValue]:
    return {
        "providerProtocol": alias.provider_protocol,
        "domainKey": alias.domain_key,
        "providerAlias": alias.provider_alias,
        "bindingContractDigest": alias.binding_contract_digest,
    }


def build_manifest_digest_payload(
    *,
    run_id: UUID,
    revision: int,
    parent_digest: str | None,
    main_agent: ResolvedMainAgentRef,
    active_skills: tuple[ResolvedSkillRef, ...],
    capabilities: tuple[ResolvedCapabilityRef, ...],
    provider: ProviderRef | None,
    model: ModelRef | None,
    provider_aliases: tuple[ResolvedProviderAliasRef, ...],
    effective_policy_digest: str | None,
) -> dict[str, JsonValue]:
    ordered_skills = tuple(sorted(active_skills, key=lambda item: item.canonical_name))
    ordered_capabilities = tuple(
        sorted(
            capabilities,
            key=lambda item: (item.capability_type, item.capability_key),
        )
    )
    ordered_aliases = tuple(
        sorted(
            provider_aliases,
            key=lambda item: (item.provider_protocol, item.domain_key, item.provider_alias),
        )
    )
    return {
        "schemaVersion": 1,
        "runId": str(run_id),
        "revision": revision,
        "parentDigest": parent_digest,
        "mainAgent": _main_agent_payload(main_agent),
        "activeSkills": [_skill_payload(item) for item in ordered_skills],
        "capabilities": [_capability_payload(item) for item in ordered_capabilities],
        "provider": None if provider is None else _provider_ref_payload(provider),
        "model": None if model is None else _model_ref_payload(model),
        "providerAliases": [_alias_payload(item) for item in ordered_aliases],
        "effectivePolicyDigest": effective_policy_digest,
    }


def compute_manifest_digest(
    *,
    run_id: UUID,
    revision: int,
    parent_digest: str | None,
    main_agent: ResolvedMainAgentRef,
    active_skills: tuple[ResolvedSkillRef, ...],
    capabilities: tuple[ResolvedCapabilityRef, ...],
    provider: ProviderRef | None,
    model: ModelRef | None,
    provider_aliases: tuple[ResolvedProviderAliasRef, ...],
    effective_policy_digest: str | None,
) -> str:
    return sha256_canonical_json(
        build_manifest_digest_payload(
            run_id=run_id,
            revision=revision,
            parent_digest=parent_digest,
            main_agent=main_agent,
            active_skills=active_skills,
            capabilities=capabilities,
            provider=provider,
            model=model,
            provider_aliases=provider_aliases,
            effective_policy_digest=effective_policy_digest,
        )
    )


def create_base_run_manifest(
    *,
    run_id: UUID,
    main_agent: ResolvedMainAgentRef,
    provider: ProviderRef | None,
    model: ModelRef | None,
    effective_policy_digest: str | None,
) -> ResolvedRunManifestRevision:
    revision = 1
    parent_digest = None
    active_skills: tuple[ResolvedSkillRef, ...] = ()
    capabilities: tuple[ResolvedCapabilityRef, ...] = ()
    provider_aliases: tuple[ResolvedProviderAliasRef, ...] = ()
    manifest_digest = compute_manifest_digest(
        run_id=run_id,
        revision=revision,
        parent_digest=parent_digest,
        main_agent=main_agent,
        active_skills=active_skills,
        capabilities=capabilities,
        provider=provider,
        model=model,
        provider_aliases=provider_aliases,
        effective_policy_digest=effective_policy_digest,
    )
    return ResolvedRunManifestRevision(
        run_id=run_id,
        revision=revision,
        parent_digest=parent_digest,
        main_agent=main_agent,
        active_skills=active_skills,
        capabilities=capabilities,
        provider=provider,
        model=model,
        provider_aliases=provider_aliases,
        effective_policy_digest=effective_policy_digest,
        manifest_digest=manifest_digest,
    )


def _same_skill_version(left: ResolvedSkillRef, right: ResolvedSkillRef) -> bool:
    # Version identity only. requested_name_normalized / resolved_via_alias_id are
    # resolution provenance and must not affect same-version idempotency.
    return (
        left.package_id == right.package_id
        and left.version_id == right.version_id
        and left.canonical_name == right.canonical_name
        and left.sequence == right.sequence
        and left.content_digest == right.content_digest
        and left.version_digest == right.version_digest
    )


def _same_capability(left: ResolvedCapabilityRef, right: ResolvedCapabilityRef) -> bool:
    return left.model_dump() == right.model_dump()


def append_skill_activation(
    current: ResolvedRunManifestRevision,
    *,
    skill: ResolvedSkillRef,
    capabilities: tuple[ResolvedCapabilityRef, ...],
) -> ResolvedRunManifestRevision:
    existing_by_name = {
        item.canonical_name: item for item in current.active_skills
    }
    if skill.canonical_name in existing_by_name:
        existing = existing_by_name[skill.canonical_name]
        if not _same_skill_version(existing, skill):
            raise SkillVersionConflictError(
                f"skill version conflict for canonical name {skill.canonical_name!r}"
            )
        # Same skill version: require provided capabilities to be identical or empty.
        if capabilities:
            existing_caps = {
                (item.capability_type, item.capability_key): item
                for item in current.capabilities
            }
            for capability in capabilities:
                cap_id = (capability.capability_type, capability.capability_key)
                prior = existing_caps.get(cap_id)
                if prior is None:
                    raise ValueError(
                        f"reactivation cannot introduce capability "
                        f"{capability.capability_type}/{capability.capability_key!r}"
                    )
                if not _same_capability(prior, capability):
                    raise ValueError(
                        f"capability conflict for "
                        f"{capability.capability_type}/{capability.capability_key!r}"
                    )
        return current

    existing_caps = {
        (item.capability_type, item.capability_key): item
        for item in current.capabilities
    }
    for capability in capabilities:
        cap_id = (capability.capability_type, capability.capability_key)
        prior = existing_caps.get(cap_id)
        if prior is not None and not _same_capability(prior, capability):
            raise ValueError(
                f"capability conflict for "
                f"{capability.capability_type}/{capability.capability_key!r}"
            )
        existing_caps[cap_id] = capability

    merged_skills = tuple(
        sorted(
            (*current.active_skills, skill),
            key=lambda item: item.canonical_name,
        )
    )
    merged_capabilities = tuple(
        sorted(
            existing_caps.values(),
            key=lambda item: (item.capability_type, item.capability_key),
        )
    )
    revision = current.revision + 1
    parent_digest = current.manifest_digest
    manifest_digest = compute_manifest_digest(
        run_id=current.run_id,
        revision=revision,
        parent_digest=parent_digest,
        main_agent=current.main_agent,
        active_skills=merged_skills,
        capabilities=merged_capabilities,
        provider=current.provider,
        model=current.model,
        provider_aliases=current.provider_aliases,
        effective_policy_digest=current.effective_policy_digest,
    )
    return ResolvedRunManifestRevision(
        run_id=current.run_id,
        revision=revision,
        parent_digest=parent_digest,
        main_agent=current.main_agent,
        active_skills=merged_skills,
        capabilities=merged_capabilities,
        provider=current.provider,
        model=current.model,
        provider_aliases=current.provider_aliases,
        effective_policy_digest=current.effective_policy_digest,
        manifest_digest=manifest_digest,
    )


def append_skill_activations_batch(
    current: ResolvedRunManifestRevision,
    *,
    activations: tuple[tuple[ResolvedSkillRef, tuple[ResolvedCapabilityRef, ...]], ...],
) -> ResolvedRunManifestRevision:
    """Append one or more Skill activations as a single Manifest child (revision +1).

    Plan 04 multi-skill inject must produce exactly one lineage step so
    validate_manifest_child_link(parent, child) succeeds for the whole batch.
    """
    if not activations:
        return current

    existing_by_name = {item.canonical_name: item for item in current.active_skills}
    existing_caps = {
        (item.capability_type, item.capability_key): item for item in current.capabilities
    }
    new_skills: list[ResolvedSkillRef] = []

    for skill, capabilities in activations:
        if skill.canonical_name in existing_by_name:
            existing = existing_by_name[skill.canonical_name]
            if not _same_skill_version(existing, skill):
                raise SkillVersionConflictError(
                    f"skill version conflict for canonical name {skill.canonical_name!r}"
                )
            if capabilities:
                for capability in capabilities:
                    cap_id = (capability.capability_type, capability.capability_key)
                    prior = existing_caps.get(cap_id)
                    if prior is None:
                        raise ValueError(
                            f"reactivation cannot introduce capability "
                            f"{capability.capability_type}/{capability.capability_key!r}"
                        )
                    if not _same_capability(prior, capability):
                        raise ValueError(
                            f"capability conflict for "
                            f"{capability.capability_type}/{capability.capability_key!r}"
                        )
            continue

        for capability in capabilities:
            cap_id = (capability.capability_type, capability.capability_key)
            prior = existing_caps.get(cap_id)
            if prior is not None and not _same_capability(prior, capability):
                raise ValueError(
                    f"capability conflict for "
                    f"{capability.capability_type}/{capability.capability_key!r}"
                )
            existing_caps[cap_id] = capability
        existing_by_name[skill.canonical_name] = skill
        new_skills.append(skill)

    if not new_skills:
        return current

    merged_skills = tuple(
        sorted(
            (*current.active_skills, *new_skills),
            key=lambda item: item.canonical_name,
        )
    )
    merged_capabilities = tuple(
        sorted(
            existing_caps.values(),
            key=lambda item: (item.capability_type, item.capability_key),
        )
    )
    revision = current.revision + 1
    parent_digest = current.manifest_digest
    manifest_digest = compute_manifest_digest(
        run_id=current.run_id,
        revision=revision,
        parent_digest=parent_digest,
        main_agent=current.main_agent,
        active_skills=merged_skills,
        capabilities=merged_capabilities,
        provider=current.provider,
        model=current.model,
        provider_aliases=current.provider_aliases,
        effective_policy_digest=current.effective_policy_digest,
    )
    return ResolvedRunManifestRevision(
        run_id=current.run_id,
        revision=revision,
        parent_digest=parent_digest,
        main_agent=current.main_agent,
        active_skills=merged_skills,
        capabilities=merged_capabilities,
        provider=current.provider,
        model=current.model,
        provider_aliases=current.provider_aliases,
        effective_policy_digest=current.effective_policy_digest,
        manifest_digest=manifest_digest,
    )


def validate_manifest_child_link(
    *,
    parent: ResolvedRunManifestRevision,
    child: ResolvedRunManifestRevision,
) -> None:
    if child.revision != parent.revision + 1:
        raise ValueError(
            f"child revision must equal parent revision + 1 "
            f"(parent={parent.revision}, child={child.revision})"
        )
    if child.parent_digest != parent.manifest_digest:
        raise ValueError("parent_digest mismatch with parent manifest_digest")
    if child.run_id != parent.run_id:
        raise ValueError("child run_id must match parent run_id")


def _same_provider_alias(
    left: ResolvedProviderAliasRef,
    right: ResolvedProviderAliasRef,
) -> bool:
    return (
        left.provider_protocol == right.provider_protocol
        and left.domain_key == right.domain_key
        and left.provider_alias == right.provider_alias
        and left.binding_contract_digest == right.binding_contract_digest
    )


def _casefold_ascii(value: str) -> str:
    return value.encode("utf-8").decode("ascii").casefold()


def append_provider_aliases(
    current: ResolvedRunManifestRevision,
    *,
    aliases: tuple[ResolvedProviderAliasRef, ...],
) -> ResolvedRunManifestRevision:
    """Append provider alias refs without mutating existing parent aliases.

    Plan 03 final validation lives here. The v1 Manifest shape is unchanged:
    empty aliases remain an explicit empty tuple in the canonical payload.
    """
    if not isinstance(aliases, tuple):
        raise TypeError("aliases must be a tuple of ResolvedProviderAliasRef")

    existing = list(current.provider_aliases)
    existing_by_identity = {
        (item.provider_protocol, item.domain_key): item for item in existing
    }
    # Case-folded alias occupancy per protocol (ASCII only).
    occupied_aliases: dict[tuple[str, str], ResolvedProviderAliasRef] = {
        (item.provider_protocol, _casefold_ascii(item.provider_alias)): item
        for item in existing
    }
    # Domain key + binding identity occupancy per protocol.
    occupied_bindings: dict[tuple[str, str], ResolvedProviderAliasRef] = {
        (item.provider_protocol, item.domain_key): item for item in existing
    }

    additions: list[ResolvedProviderAliasRef] = []
    for alias in aliases:
        if not isinstance(alias, ResolvedProviderAliasRef):
            raise TypeError("aliases must contain ResolvedProviderAliasRef values")
        if not alias.provider_alias or not alias.domain_key or not alias.provider_protocol:
            raise ValueError("provider alias fields must be non-empty")
        if not alias.binding_contract_digest:
            raise ValueError("binding_contract_digest must be non-empty")

        identity = (alias.provider_protocol, alias.domain_key)
        prior = existing_by_identity.get(identity)
        if prior is not None:
            if not _same_provider_alias(prior, alias):
                raise ValueError(
                    f"provider alias conflict for domain key {alias.domain_key!r}: "
                    "existing aliases cannot change or map to a different binding"
                )
            # Identical existing alias is a no-op for this input item.
            continue

        # Same domain key may also appear under a different identity lookup path
        # if protocol differs; still forbid conflicting binding versions for the
        # same (protocol, domain_key) once present.
        prior_binding = occupied_bindings.get(identity)
        if prior_binding is not None and not _same_provider_alias(prior_binding, alias):
            raise ValueError(
                f"domain key {alias.domain_key!r} already bound to a different "
                "binding/version under this provider protocol"
            )

        folded = (alias.provider_protocol, _casefold_ascii(alias.provider_alias))
        collision = occupied_aliases.get(folded)
        if collision is not None and not _same_provider_alias(collision, alias):
            raise ValueError(
                f"provider alias case-fold collision for {alias.provider_alias!r}"
            )

        additions.append(alias)
        existing_by_identity[identity] = alias
        occupied_aliases[folded] = alias
        occupied_bindings[identity] = alias

    if not additions:
        # Reapplying the identical (or already-present) alias set is idempotent.
        return current

    merged = tuple(
        sorted(
            (*current.provider_aliases, *additions),
            key=lambda item: (
                item.provider_protocol,
                item.domain_key,
                item.provider_alias,
            ),
        )
    )
    revision = current.revision + 1
    parent_digest = current.manifest_digest
    manifest_digest = compute_manifest_digest(
        run_id=current.run_id,
        revision=revision,
        parent_digest=parent_digest,
        main_agent=current.main_agent,
        active_skills=current.active_skills,
        capabilities=current.capabilities,
        provider=current.provider,
        model=current.model,
        provider_aliases=merged,
        effective_policy_digest=current.effective_policy_digest,
    )
    return ResolvedRunManifestRevision(
        run_id=current.run_id,
        revision=revision,
        parent_digest=parent_digest,
        main_agent=current.main_agent,
        active_skills=current.active_skills,
        capabilities=current.capabilities,
        provider=current.provider,
        model=current.model,
        provider_aliases=merged,
        effective_policy_digest=current.effective_policy_digest,
        manifest_digest=manifest_digest,
    )


__all__ = [
    "AgentLoopStatus",
    "BindingResolutionStatus",
    "CapabilityCallStatus",
    "CapabilityCompletionContract",
    "CapabilityType",
    "CurrentCapabilityDependencyReference",
    "CurrentCapabilityReference",
    "DeclaredSideEffect",
    "FrozenContract",
    "MAX_CAPABILITY_CLASSIFIED_NODES",
    "MAX_CAPABILITY_CLOSURE_DEPTH",
    "MAX_CAPABILITY_CLOSURE_REFS",
    "MainAgentMigrationState",
    "ModelRef",
    "ParsedSkillResource",
    "ProviderRef",
    "ResolvedCapabilityBinding",
    "ResolvedCapabilityDependency",
    "ResolvedCapabilityRef",
    "ResolvedMainAgentRef",
    "ResolvedProviderAliasRef",
    "ResolvedRunManifestRevision",
    "ResolvedSkillRef",
    "SkillPackageMigrationState",
    "SkillResourceIndexEntry",
    "SkillVersionConflictError",
    "StoredSkillResource",
    "ToolParamContract",
    "VersionSource",
    "append_provider_aliases",
    "append_skill_activation",
    "append_skill_activations_batch",
    "build_manifest_digest_payload",
    "compute_manifest_digest",
    "create_base_run_manifest",
    "create_model_ref",
    "create_provider_ref",
    "validate_manifest_child_link",
]

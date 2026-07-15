"""Frozen Main Agent control capability bindings and schemas (Plan 04 Task 5).

Four code-native controls owned by the published Main Agent Profile version.
Bindings are build-revision pinned and free of mutable Tool lookup.
"""

from __future__ import annotations

from typing import Any, Literal, Sequence
from uuid import UUID

from app.assistant.capabilities.classification import MAIN_AGENT_CONTROL_CLASSIFICATIONS
from app.assistant.capabilities.contracts import (
    FrozenBindingProvenance,
    FrozenCapabilityBinding,
    project_frozen_capability_binding,
)
from app.assistant.domain.contracts import (
    CapabilityCompletionContract,
    ResolvedCapabilityBinding,
    ResolvedCapabilityRef,
)
from app.assistant.domain.digests import sha256_canonical_json
from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
from app.assistant.main_agent.authorization import build_main_agent_binding_provenance
from app.assistant.skills.resolution import (
    build_binding_snapshot,
    require_immutable_app_build_revision,
)

MAIN_AGENT_CONTROL_KEYS: tuple[str, ...] = (
    "skill.search",
    "skill.inject",
    "skill.read_resource",
    "artifact.read",
)

ControlDomainKey = Literal[
    "skill.search",
    "skill.inject",
    "skill.read_resource",
    "artifact.read",
]


def main_agent_control_target_identity(domain_key: str) -> str:
    return f"main-agent-control:{domain_key}"


def _object_schema(properties: dict[str, Any], *, required: Sequence[str] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        schema["required"] = list(required)
    return normalize_binding_schema(schema, require_object_root=True)


def control_input_schema(domain_key: str) -> dict[str, Any]:
    if domain_key == "skill.search":
        return _object_schema(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                "cursor": {"type": "string", "minLength": 1, "maxLength": 128},
            },
            required=("query",),
        )
    if domain_key == "skill.inject":
        skill_item = _object_schema(
            {
                "versionId": {"type": "string", "minLength": 36, "maxLength": 36},
                "name": {"type": "string", "minLength": 1, "maxLength": 128},
            },
        )
        return _object_schema(
            {
                "skills": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": skill_item,
                }
            },
            required=("skills",),
        )
    if domain_key == "skill.read_resource":
        return _object_schema(
            {
                "skillVersionId": {"type": "string", "minLength": 36, "maxLength": 36},
                "path": {"type": "string", "minLength": 1, "maxLength": 1024},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 65536},
            },
            required=("skillVersionId", "path"),
        )
    if domain_key == "artifact.read":
        return _object_schema(
            {
                "artifactId": {"type": "string", "minLength": 1, "maxLength": 128},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 65536},
            },
            required=("artifactId",),
        )
    raise ValueError(f"unknown control domain key: {domain_key!r}")


def control_output_schema(domain_key: str) -> dict[str, Any]:
    # Output schemas are object roots with free-form properties for provisional results.
    # Gateway validates against these; handlers return safe bounded projections only.
    if domain_key == "skill.search":
        return _object_schema(
            {
                "catalogDigest": {"type": "string", "minLength": 64, "maxLength": 64},
                "records": {"type": "array", "items": {"type": "object"}},
                "nextCursor": {"type": ["string", "null"]},
                "excludedCount": {"type": "integer", "minimum": 0},
                "semanticFallback": {"type": "boolean"},
            },
            required=("catalogDigest", "records"),
        )
    if domain_key == "skill.inject":
        return _object_schema(
            {
                "status": {"type": "string"},
                "activated": {"type": "array", "items": {"type": "object"}},
                "noop": {"type": "array", "items": {"type": "object"}},
                "proposedManifestRevision": {"type": "integer", "minimum": 0},
                "proposedManifestDigest": {
                    "type": "string",
                    "minLength": 64,
                    "maxLength": 64,
                },
                "effectivePolicyDigest": {
                    "type": "string",
                    "minLength": 64,
                    "maxLength": 64,
                },
                "packageDigest": {
                    "type": "string",
                    "minLength": 64,
                    "maxLength": 64,
                },
            },
            required=("status",),
        )
    if domain_key in {"skill.read_resource", "artifact.read"}:
        return _object_schema(
            {
                "path": {"type": "string"},
                "mediaType": {"type": "string"},
                "totalSize": {"type": "integer", "minimum": 0},
                "offset": {"type": "integer", "minimum": 0},
                "returnedBytes": {"type": "integer", "minimum": 0},
                "eof": {"type": "boolean"},
                "contentDigest": {"type": "string", "minLength": 64, "maxLength": 64},
                "encoding": {"type": "string", "enum": ["utf-8", "base64"]},
                "content": {"type": "string"},
            },
            required=(
                "totalSize",
                "offset",
                "returnedBytes",
                "eof",
                "contentDigest",
                "encoding",
                "content",
            ),
        )
    raise ValueError(f"unknown control domain key: {domain_key!r}")


def control_completion(domain_key: str) -> CapabilityCompletionContract:
    del domain_key
    return CapabilityCompletionContract(terminal_output=False, needs_followup=True)


def build_main_agent_control_resolved_binding(
    *,
    domain_key: str,
    app_build_revision: str | None = None,
) -> ResolvedCapabilityBinding:
    if domain_key not in MAIN_AGENT_CONTROL_CLASSIFICATIONS:
        raise ValueError(f"unknown control domain key: {domain_key!r}")
    build_rev = app_build_revision or require_immutable_app_build_revision()
    target_identity = main_agent_control_target_identity(domain_key)
    input_schema = control_input_schema(domain_key)
    output_schema = control_output_schema(domain_key)
    completion = control_completion(domain_key)
    input_digest = binding_schema_digest(input_schema)
    output_digest = binding_schema_digest(output_schema)
    resolution_digest = sha256_canonical_json(
        {
            "schemaVersion": 1,
            "capabilityType": "tool",
            "targetIdentity": target_identity,
            "targetId": None,
            "targetVersionId": None,
            "targetRevision": None,
            "inputSchemaDigest": input_digest,
            "outputSchemaDigest": output_digest,
            "executableRevision": build_rev,
            "configDigest": None,
            "kind": "main_agent_control",
            "domainKey": domain_key,
        }
    )
    snapshot, closure_digest, contract_digest = build_binding_snapshot(
        capability_type="tool",
        target_identity=target_identity,
        target_id=None,
        target_version_id=None,
        target_revision=None,
        input_schema=input_schema,
        output_schema=output_schema,
        completion=completion,
        config_digest=None,
        executable_revision=build_rev,
        resolution_digest=resolution_digest,
        dependencies=(),
    )
    return ResolvedCapabilityBinding(
        capability_type="tool",
        capability_key=domain_key,
        target_identity=target_identity,
        target_id=None,
        target_version_id=None,
        resolved_tool_id=None,
        resolved_workflow_version_id=None,
        resolved_agent_version_id=None,
        resolved_revision=None,
        input_schema=input_schema,
        output_schema=output_schema,
        input_schema_digest=input_digest,
        output_schema_digest=output_digest,
        completion=completion,
        config_digest=None,
        executable_revision=build_rev,
        resolution_digest=resolution_digest,
        resolution_snapshot=snapshot,
        dependencies=(),
        dependency_closure_digest=closure_digest,
        binding_contract_digest=contract_digest,
    )


def build_main_agent_control_frozen_binding(
    *,
    domain_key: str,
    owner_version_id: UUID,
    source_snapshot_digest: str,
    app_build_revision: str | None = None,
    binding_row_id: UUID | None = None,
) -> FrozenCapabilityBinding:
    resolved = build_main_agent_control_resolved_binding(
        domain_key=domain_key,
        app_build_revision=app_build_revision,
    )
    provenance = build_main_agent_binding_provenance(
        owner_version_id=owner_version_id,
        source_snapshot_digest=source_snapshot_digest,
        binding_row_id=binding_row_id,
    )
    return project_frozen_capability_binding(resolved=resolved, provenance=provenance)


def build_all_main_agent_control_bindings(
    *,
    owner_version_id: UUID,
    source_snapshot_digest: str,
    app_build_revision: str | None = None,
) -> tuple[FrozenCapabilityBinding, ...]:
    return tuple(
        build_main_agent_control_frozen_binding(
            domain_key=key,
            owner_version_id=owner_version_id,
            source_snapshot_digest=source_snapshot_digest,
            app_build_revision=app_build_revision,
        )
        for key in MAIN_AGENT_CONTROL_KEYS
    )


def control_capability_refs(
    bindings: Sequence[FrozenCapabilityBinding],
) -> tuple[ResolvedCapabilityRef, ...]:
    return tuple(item.ref for item in bindings)


def assert_all_controls_classified() -> None:
    missing = [key for key in MAIN_AGENT_CONTROL_KEYS if key not in MAIN_AGENT_CONTROL_CLASSIFICATIONS]
    if missing:
        raise AssertionError(f"unclassified main agent controls: {missing}")
    extra = sorted(set(MAIN_AGENT_CONTROL_CLASSIFICATIONS) - set(MAIN_AGENT_CONTROL_KEYS))
    if extra:
        raise AssertionError(f"unexpected main agent control classifications: {extra}")


__all__ = [
    "MAIN_AGENT_CONTROL_KEYS",
    "ControlDomainKey",
    "assert_all_controls_classified",
    "build_all_main_agent_control_bindings",
    "build_main_agent_control_frozen_binding",
    "build_main_agent_control_resolved_binding",
    "control_capability_refs",
    "control_completion",
    "control_input_schema",
    "control_output_schema",
    "main_agent_control_target_identity",
]

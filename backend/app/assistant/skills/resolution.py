"""Capability reference resolution and dependency-closure freezing (Plan 01 Task 5)."""

from __future__ import annotations

import copy
from typing import Any, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
from app.assistant.domain.contracts import (
    MAX_CAPABILITY_CLASSIFIED_NODES,
    MAX_CAPABILITY_CLOSURE_DEPTH,
    MAX_CAPABILITY_CLOSURE_REFS,
    CapabilityCompletionContract,
    CurrentCapabilityDependencyReference,
    CurrentCapabilityReference,
    ResolvedCapabilityBinding,
    ResolvedCapabilityDependency,
    ToolParamContract,
    create_model_ref,
)
from app.assistant.domain.digests import JsonValue, sha256_canonical_json
from app.assistant.domain.json_schema import (
    binding_schema_digest,
    normalize_binding_schema,
    system_tool_contract_set_digest,
    tool_params_to_binding_schema,
)
from app.assistant.skills.contracts import CapabilityDeclaration
from app.assistant.skills.models import (
    AssistantSkillCapabilityBinding,
    AssistantSkillCapabilityDependency,
    AssistantSkillVersion,
)
from app.assistant_config.models import (
    AssistantAgentProfile,
    AssistantAgentProfileVersion,
    AssistantTool,
    AssistantWorkflow,
    AssistantWorkflowVersion,
)
from app.assistant_config.registry import ToolRegistry
from app.assistant_config.workflow_contracts import (
    WorkflowContractError,
    workflow_contract_from_input,
)
from app.assistant_config.workflow_references import (
    collect_workflow_call_references,
    collect_workflow_model_usages,
    collect_workflow_tool_usages,
)
from app.assistant_config.schemas import WorkflowInput
from app.common.exceptions import ApiException
from app.config import get_settings


REMOTE_TOOL_OUTPUT_SCHEMA_RAW_STRING: dict[str, JsonValue] = {"type": "string"}
NON_IMMUTABLE_BUILD_REVISIONS = {"", "development", "unknown"}


def _publish_error(message: str, *, details: dict[str, Any] | None = None) -> ApiException:
    return ApiException(
        status_code=422,
        code=42293,
        message=message,
        details=details or {},
    )


def _drift_error(message: str, *, details: dict[str, Any] | None = None) -> ApiException:
    return ApiException(
        status_code=422,
        code=42295,
        message=message,
        details=details or {},
    )


def require_immutable_app_build_revision(
    *,
    app_env: str | None = None,
    app_build_revision: str | None = None,
) -> str:
    settings = get_settings()
    env = (app_env if app_env is not None else settings.app_env or "").strip().lower()
    revision = (
        app_build_revision
        if app_build_revision is not None
        else settings.app_build_revision
    )
    revision = (revision or "").strip()
    if env in {"development", "test", "testing"}:
        if not revision:
            raise _publish_error(
                "APP_BUILD_REVISION is required for code-native tool publication",
                details={"reason": "blank_build_revision", "appEnv": env},
            )
        return revision
    if revision.lower() in NON_IMMUTABLE_BUILD_REVISIONS or not revision:
        raise _publish_error(
            "APP_BUILD_REVISION must be deployment-immutable outside development/test",
            details={
                "reason": "non_immutable_build_revision",
                "appEnv": env,
                "appBuildRevision": revision or None,
            },
        )
    return revision


def compute_system_tool_contract_set_digest() -> str:
    ordered: list[tuple[str, str, str]] = []
    for definition in ToolRegistry.list_system_tool_definitions():
        input_schema = tool_params_to_binding_schema(
            [
                ToolParamContract(
                    name=p.name,
                    description=p.description,
                    param_type=p.param_type,  # type: ignore[arg-type]
                    required=bool(p.required),
                )
                for p in definition.input_params
            ],
            require_object_root=True,
        )
        output_schema = tool_params_to_binding_schema(
            [
                ToolParamContract(
                    name=p.name,
                    description=p.description,
                    param_type=p.param_type,  # type: ignore[arg-type]
                    required=False,
                )
                for p in definition.output_params
            ],
            require_object_root=True,
        )
        ordered.append(
            (
                definition.name,
                binding_schema_digest(input_schema),
                binding_schema_digest(output_schema),
            )
        )
    ordered.sort(key=lambda item: item[0])
    return system_tool_contract_set_digest(ordered)


def system_tool_schemas(tool_name: str) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    definitions = {
        item.name: item for item in ToolRegistry.list_system_tool_definitions()
    }
    definition = definitions.get(tool_name)
    if definition is None:
        raise _publish_error(
            f"system tool not found: {tool_name}",
            details={"capabilityKey": tool_name, "capabilityType": "tool"},
        )
    input_schema = tool_params_to_binding_schema(
        [
            ToolParamContract(
                name=p.name,
                description=p.description,
                param_type=p.param_type,  # type: ignore[arg-type]
                required=bool(p.required),
            )
            for p in definition.input_params
        ],
        require_object_root=True,
    )
    output_schema = tool_params_to_binding_schema(
        [
            ToolParamContract(
                name=p.name,
                description=p.description,
                param_type=p.param_type,  # type: ignore[arg-type]
                required=False,
            )
            for p in definition.output_params
        ],
        require_object_root=True,
    )
    return input_schema, output_schema


def remote_tool_input_schema(tool: AssistantTool) -> dict[str, JsonValue]:
    raw_params = tool.input_params if isinstance(tool.input_params, list) else []
    contracts: list[ToolParamContract] = []
    for item in raw_params:
        if not isinstance(item, Mapping):
            raise _publish_error(
                f"remote tool {tool.name!r} has invalid input_params entry",
                details={"toolId": str(tool.id)},
            )
        name = str(item.get("name") or "").strip()
        if not name:
            raise _publish_error(
                f"remote tool {tool.name!r} input param missing name",
                details={"toolId": str(tool.id)},
            )
        param_type = str(item.get("param_type") or item.get("paramType") or "string").strip()
        items_type = item.get("items_type") or item.get("itemsType")
        contracts.append(
            ToolParamContract(
                name=name,
                description=(str(item["description"]) if item.get("description") is not None else None),
                param_type=param_type,  # type: ignore[arg-type]
                required=bool(item.get("required", False)),
                items_type=str(items_type) if items_type is not None else None,  # type: ignore[arg-type]
            )
        )
    return tool_params_to_binding_schema(contracts, require_object_root=True)


def secret_free_remote_execution_snapshot(tool: AssistantTool) -> dict[str, JsonValue]:
    """Persist only safe structural remote execution metadata (no secrets)."""
    endpoint = (tool.endpoint_url or "").strip()
    parts = urlsplit(endpoint) if endpoint else None
    safe_endpoint: dict[str, JsonValue] = {
        "scheme": parts.scheme if parts else None,
        "host": parts.hostname if parts else None,
        "port": parts.port if parts else None,
        "path": parts.path if parts else None,
        # Explicitly omit userinfo/query/fragment.
    }
    header_names: list[str] = []
    if isinstance(tool.headers, Mapping):
        header_names = sorted(str(k) for k in tool.headers.keys())
    elif isinstance(tool.headers, list):
        for item in tool.headers:
            if isinstance(item, Mapping) and item.get("name") is not None:
                header_names.append(str(item.get("name")))
        header_names = sorted(set(header_names))

    query_param_names: list[str] = []
    if isinstance(tool.query_params, Mapping):
        query_param_names = sorted(str(k) for k in tool.query_params.keys())

    return {
        "schemaVersion": 1,
        "name": tool.name,
        "kind": tool.kind,
        "endpoint": safe_endpoint,
        "httpMethod": tool.http_method,
        "headerNames": header_names,
        "queryParamNames": query_param_names,
        "bodyType": tool.body_type,
        "hasBodyTemplate": bool(tool.body_content),
        "authType": tool.auth_type,
        "authHeaderName": tool.auth_header_name,
        "authScheme": tool.auth_scheme,
        "hasCredentialSlot": bool(tool.api_key_encrypted),
        "timeoutSeconds": tool.timeout_seconds,
        "payloadWrapper": tool.payload_wrapper,
        "configRevision": int(tool.config_revision or 1),
    }


def tool_execution_sensitive_payload(tool: AssistantTool | Mapping[str, Any]) -> dict[str, Any]:
    """Canonical before/after payload for remote Tool config_revision comparison.

    Sensitive values are compared in memory only; never log/return/hash this payload
    into published snapshots.
    """
    if isinstance(tool, Mapping):
        get = tool.get
    else:
        def get(key: str, default: Any = None) -> Any:
            return getattr(tool, key, default)

    return {
        "name": get("name"),
        "kind": get("kind"),
        "input_params": copy.deepcopy(get("input_params")),
        "endpoint_url": get("endpoint_url"),
        "http_method": get("http_method"),
        "headers": copy.deepcopy(get("headers")),
        "query_params": copy.deepcopy(get("query_params")),
        "body_type": get("body_type"),
        "body_content": get("body_content"),
        "auth_type": get("auth_type"),
        "auth_header_name": get("auth_header_name"),
        "auth_scheme": get("auth_scheme"),
        "api_key_encrypted": get("api_key_encrypted"),
        "timeout_seconds": get("timeout_seconds"),
        "payload_wrapper": get("payload_wrapper"),
    }


def execution_sensitive_changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    return tool_execution_sensitive_payload(before) != tool_execution_sensitive_payload(after)


def credential_runtime_sensitive_payload(cred: AiCredential | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(cred, Mapping):
        return {
            "base_url": cred.get("base_url"),
            "api_key_encrypted": cred.get("api_key_encrypted"),
        }
    return {
        "base_url": cred.base_url,
        "api_key_encrypted": cred.api_key_encrypted,
    }


def model_runtime_sensitive_payload(model: AiModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(model, Mapping):
        return {
            "name": model.get("name"),
            "model_type": model.get("model_type"),
            "credential_id": str(model.get("credential_id")) if model.get("credential_id") else None,
        }
    return {
        "name": model.name,
        "model_type": model.model_type,
        "credential_id": str(model.credential_id) if model.credential_id else None,
    }


def _completion_payload(completion: CapabilityCompletionContract) -> dict[str, JsonValue]:
    return {
        "terminalOutput": bool(completion.terminal_output),
        "needsFollowup": bool(completion.needs_followup),
        "followupHint": completion.followup_hint,
    }


def _target_resolution_digest(
    *,
    capability_type: str,
    target_identity: str,
    target_id: UUID | None,
    target_version_id: UUID | None,
    target_revision: int | None,
    input_schema_digest: str,
    output_schema_digest: str,
    executable_revision: str | None,
    config_digest: str | None,
    system_tool_contract_set_digest_value: str | None = None,
) -> str:
    payload: dict[str, JsonValue] = {
        "schemaVersion": 1,
        "capabilityType": capability_type,
        "targetIdentity": target_identity,
        "targetId": str(target_id) if target_id is not None else None,
        "targetVersionId": str(target_version_id) if target_version_id is not None else None,
        "targetRevision": target_revision,
        "inputSchemaDigest": input_schema_digest,
        "outputSchemaDigest": output_schema_digest,
        "executableRevision": executable_revision,
        "configDigest": config_digest,
        "systemToolContractSetDigest": system_tool_contract_set_digest_value,
    }
    return sha256_canonical_json(payload)


def build_binding_snapshot(
    *,
    capability_type: str,
    target_identity: str,
    target_id: UUID | None,
    target_version_id: UUID | None,
    target_revision: int | None,
    input_schema: dict[str, JsonValue],
    output_schema: dict[str, JsonValue],
    completion: CapabilityCompletionContract,
    config_digest: str | None,
    executable_revision: str | None,
    resolution_digest: str,
    dependencies: tuple[ResolvedCapabilityDependency, ...],
) -> tuple[dict[str, JsonValue], str, str]:
    ordered_deps = tuple(sorted(dependencies, key=lambda d: d.dependency_path))
    closure_index = [
        {"path": dep.dependency_path, "dependencyDigest": dep.dependency_digest}
        for dep in ordered_deps
    ]
    dependency_closure_digest = sha256_canonical_json(closure_index)
    input_digest = binding_schema_digest(input_schema)
    output_digest = binding_schema_digest(output_schema)
    payload: dict[str, JsonValue] = {
        "schemaVersion": 1,
        "target": {
            "capabilityType": capability_type,
            "targetIdentity": target_identity,
            "targetId": str(target_id) if target_id is not None else None,
            "targetVersionId": str(target_version_id) if target_version_id is not None else None,
            "targetRevision": target_revision,
            "resolutionDigest": resolution_digest,
        },
        "inputSchema": input_schema,
        "outputSchema": output_schema,
        "inputSchemaDigest": input_digest,
        "outputSchemaDigest": output_digest,
        "completion": _completion_payload(completion),
        "execution": {
            "configDigest": config_digest,
            "executableRevision": executable_revision,
        },
        "dependencyClosure": closure_index,
        "dependencyClosureDigest": dependency_closure_digest,
    }
    binding_contract_digest = sha256_canonical_json(payload)
    payload["bindingContractDigest"] = binding_contract_digest
    return payload, dependency_closure_digest, binding_contract_digest


def reconstruct_binding_snapshot(
    binding: AssistantSkillCapabilityBinding,
    dependencies: list[AssistantSkillCapabilityDependency] | tuple[AssistantSkillCapabilityDependency, ...],
) -> dict[str, JsonValue]:
    ordered = sorted(dependencies, key=lambda d: (d.ordinal, d.dependency_path))
    closure_index = [
        {"path": dep.dependency_path, "dependencyDigest": dep.dependency_digest}
        for dep in ordered
    ]
    snapshot = binding.resolution_snapshot if isinstance(binding.resolution_snapshot, dict) else {}
    payload: dict[str, JsonValue] = {
        "schemaVersion": 1,
        "target": {
            "capabilityType": binding.capability_type,
            "targetIdentity": binding.target_identity,
            "targetId": (
                str(binding.resolved_tool_id)
                if binding.resolved_tool_id is not None
                else (
                    None
                    if binding.capability_type == "tool" and (binding.target_identity or "").startswith("system-tool:")
                    else (
                        # Project domain target_id from typed FK owner when available.
                        None
                    )
                )
            ),
            "targetVersionId": (
                str(binding.resolved_workflow_version_id)
                if binding.resolved_workflow_version_id is not None
                else (
                    str(binding.resolved_agent_version_id)
                    if binding.resolved_agent_version_id is not None
                    else None
                )
            ),
            "targetRevision": binding.resolved_revision,
            "resolutionDigest": binding.resolution_digest,
        },
        "inputSchema": snapshot.get("inputSchema"),
        "outputSchema": snapshot.get("outputSchema"),
        "inputSchemaDigest": binding.input_schema_digest,
        "outputSchemaDigest": binding.output_schema_digest,
        "completion": snapshot.get("completion") or {
            "terminalOutput": False,
            "needsFollowup": True,
            "followupHint": None,
        },
        "execution": {
            "configDigest": binding.config_digest,
            "executableRevision": binding.executable_revision,
        },
        "dependencyClosure": closure_index,
        "dependencyClosureDigest": binding.dependency_closure_digest,
    }
    # Prefer stored target fields when present for lossless reconstruction.
    stored_target = snapshot.get("target") if isinstance(snapshot.get("target"), dict) else None
    if stored_target is not None:
        payload["target"] = stored_target
    digest = sha256_canonical_json(payload)
    payload["bindingContractDigest"] = digest
    return payload


def binding_set_digest_from_bindings(
    bindings: list[ResolvedCapabilityBinding] | tuple[ResolvedCapabilityBinding, ...],
) -> str:
    ordered = sorted(
        bindings,
        key=lambda b: (b.capability_type, b.capability_key),
    )
    payload = [
        {
            "capabilityType": b.capability_type,
            "key": b.capability_key,
            "bindingContractDigest": b.binding_contract_digest,
        }
        for b in ordered
    ]
    return sha256_canonical_json(payload)


def version_digest_from_parts(*, content_digest: str, binding_set_digest: str) -> str:
    return sha256_canonical_json(
        {
            "schemaVersion": 1,
            "contentDigest": content_digest,
            "bindingSetDigest": binding_set_digest,
        }
    )


def _dependency_payload_without_digest(dep: ResolvedCapabilityDependency) -> dict[str, JsonValue]:
    return {
        "schemaVersion": 1,
        "ordinal": dep.ordinal,
        "dependencyPath": dep.dependency_path,
        "dependencyType": dep.dependency_type,
        "targetIdentity": dep.target_identity,
        "resolvedToolId": str(dep.resolved_tool_id) if dep.resolved_tool_id else None,
        "resolvedWorkflowVersionId": (
            str(dep.resolved_workflow_version_id) if dep.resolved_workflow_version_id else None
        ),
        "resolvedAgentVersionId": (
            str(dep.resolved_agent_version_id) if dep.resolved_agent_version_id else None
        ),
        "resolvedModelId": str(dep.resolved_model_id) if dep.resolved_model_id else None,
        "targetRevision": dep.target_revision,
        "inputSchema": dep.input_schema,
        "outputSchema": dep.output_schema,
        "inputSchemaDigest": dep.input_schema_digest,
        "outputSchemaDigest": dep.output_schema_digest,
        "resolutionDigest": dep.resolution_digest,
        "resolutionSnapshot": dep.resolution_snapshot,
    }


def finalize_dependency(
    *,
    ordinal: int,
    dependency_path: str,
    dependency_type: str,
    target_identity: str,
    resolved_tool_id: UUID | None = None,
    resolved_workflow_version_id: UUID | None = None,
    resolved_agent_version_id: UUID | None = None,
    resolved_model_id: UUID | None = None,
    target_revision: int | None = None,
    input_schema: dict[str, JsonValue] | None = None,
    output_schema: dict[str, JsonValue] | None = None,
    resolution_snapshot: dict[str, JsonValue],
    resolution_digest: str,
) -> ResolvedCapabilityDependency:
    input_digest = binding_schema_digest(input_schema) if input_schema is not None else None
    output_digest = binding_schema_digest(output_schema) if output_schema is not None else None
    provisional = ResolvedCapabilityDependency(
        ordinal=ordinal,
        dependency_path=dependency_path,
        dependency_type=dependency_type,  # type: ignore[arg-type]
        target_identity=target_identity,
        resolved_tool_id=resolved_tool_id,
        resolved_workflow_version_id=resolved_workflow_version_id,
        resolved_agent_version_id=resolved_agent_version_id,
        resolved_model_id=resolved_model_id,
        target_revision=target_revision,
        input_schema=input_schema,
        output_schema=output_schema,
        input_schema_digest=input_digest,
        output_schema_digest=output_digest,
        resolution_snapshot=resolution_snapshot,
        resolution_digest=resolution_digest,
        dependency_digest="0" * 64,
    )
    dependency_digest = sha256_canonical_json(_dependency_payload_without_digest(provisional))
    return provisional.model_copy(update={"dependency_digest": dependency_digest})


class CapabilityReferenceResolver:
    """Resolve Skill capability declarations into frozen bindings + closures."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._app_build_revision = require_immutable_app_build_revision()
        self._system_tool_set_digest = compute_system_tool_contract_set_digest()
        self._classified_nodes = 0
        self._ref_count = 0

    def resolve_many(
        self,
        declarations: tuple[CapabilityDeclaration, ...],
    ) -> tuple[ResolvedCapabilityBinding, ...]:
        # Duplicate key detection with conflicting targets.
        seen: dict[tuple[str, str], CapabilityDeclaration] = {}
        for decl in declarations:
            key = (decl.type, decl.key)
            if key in seen and seen[key] != decl:
                raise _publish_error(
                    f"duplicate capability key with conflicting declaration: {decl.type}/{decl.key}",
                    details={"capabilityType": decl.type, "capabilityKey": decl.key},
                )
            seen[key] = decl

        resolved: list[ResolvedCapabilityBinding] = []
        for decl in sorted(declarations, key=lambda d: (d.type, d.key)):
            resolved.append(self._resolve_one(decl))
        return tuple(resolved)

    def _count_node(self) -> None:
        self._classified_nodes += 1
        if self._classified_nodes > MAX_CAPABILITY_CLASSIFIED_NODES:
            raise _publish_error(
                f"capability closure classified nodes exceed {MAX_CAPABILITY_CLASSIFIED_NODES}",
                details={"reason": "classified_node_overflow"},
            )

    def _count_ref(self) -> None:
        self._ref_count += 1
        if self._ref_count > MAX_CAPABILITY_CLOSURE_REFS:
            raise _publish_error(
                f"capability closure refs exceed {MAX_CAPABILITY_CLOSURE_REFS}",
                details={"reason": "closure_ref_overflow"},
            )

    def _resolve_one(self, decl: CapabilityDeclaration) -> ResolvedCapabilityBinding:
        if decl.type == "tool":
            return self._resolve_tool(decl)
        if decl.type == "workflow":
            return self._resolve_workflow(decl)
        if decl.type == "agent":
            return self._resolve_agent(decl)
        raise _publish_error(f"unknown capability type: {decl.type}")

    def _completion_for(self, decl: CapabilityDeclaration) -> CapabilityCompletionContract:
        if decl.contract is not None:
            return decl.contract.completion
        return CapabilityCompletionContract()

    def _resolve_tool(self, decl: CapabilityDeclaration) -> ResolvedCapabilityBinding:
        self._count_node()
        # Prefer system tool when present in registry.
        if ToolRegistry.has_system_tool(decl.key):
            return self._resolve_system_tool(decl)
        tool = (
            self.db.query(AssistantTool)
            .filter(AssistantTool.name == decl.key, AssistantTool.kind == "remote")
            .one_or_none()
        )
        if tool is None:
            raise _publish_error(
                f"tool target not found: {decl.key}",
                details={"capabilityType": "tool", "capabilityKey": decl.key},
            )
        if not bool(tool.enabled):
            raise _publish_error(
                f"tool target is disabled: {decl.key}",
                details={"capabilityType": "tool", "capabilityKey": decl.key, "toolId": str(tool.id)},
            )
        if decl.contract is None or decl.contract.output_schema is None:
            raise _publish_error(
                f"remote tool binding requires explicit output_schema: {decl.key}",
                details={"capabilityType": "tool", "capabilityKey": decl.key, "toolId": str(tool.id)},
            )
        input_schema = remote_tool_input_schema(tool)
        output_schema = normalize_binding_schema(
            decl.contract.output_schema,
            require_object_root=False,
        )
        if decl.contract.input_schema is not None:
            declared_input = normalize_binding_schema(
                decl.contract.input_schema,
                require_object_root=True,
            )
            if binding_schema_digest(declared_input) != binding_schema_digest(input_schema):
                raise _publish_error(
                    f"remote tool declared input_schema does not match target: {decl.key}",
                    details={"capabilityType": "tool", "capabilityKey": decl.key},
                )
        execution = secret_free_remote_execution_snapshot(tool)
        config_digest = sha256_canonical_json(execution)
        executable_revision = str(int(tool.config_revision or 1))
        target_identity = f"remote-tool:{tool.id}"
        input_digest = binding_schema_digest(input_schema)
        output_digest = binding_schema_digest(output_schema)
        resolution_digest = _target_resolution_digest(
            capability_type="tool",
            target_identity=target_identity,
            target_id=tool.id,
            target_version_id=None,
            target_revision=int(tool.config_revision or 1),
            input_schema_digest=input_digest,
            output_schema_digest=output_digest,
            executable_revision=executable_revision,
            config_digest=config_digest,
        )
        completion = self._completion_for(decl)
        snapshot, closure_digest, contract_digest = build_binding_snapshot(
            capability_type="tool",
            target_identity=target_identity,
            target_id=tool.id,
            target_version_id=None,
            target_revision=int(tool.config_revision or 1),
            input_schema=input_schema,
            output_schema=output_schema,
            completion=completion,
            config_digest=config_digest,
            executable_revision=executable_revision,
            resolution_digest=resolution_digest,
            dependencies=(),
        )
        return ResolvedCapabilityBinding(
            capability_type="tool",
            capability_key=decl.key,
            target_identity=target_identity,
            target_id=tool.id,
            target_version_id=None,
            resolved_tool_id=tool.id,
            resolved_workflow_version_id=None,
            resolved_agent_version_id=None,
            resolved_revision=int(tool.config_revision or 1),
            input_schema=input_schema,
            output_schema=output_schema,
            input_schema_digest=input_digest,
            output_schema_digest=output_digest,
            completion=completion,
            config_digest=config_digest,
            executable_revision=executable_revision,
            resolution_digest=resolution_digest,
            resolution_snapshot=snapshot,
            dependencies=(),
            dependency_closure_digest=closure_digest,
            binding_contract_digest=contract_digest,
        )

    def _resolve_system_tool(self, decl: CapabilityDeclaration) -> ResolvedCapabilityBinding:
        input_schema, output_schema = system_tool_schemas(decl.key)
        if decl.contract is not None:
            if decl.contract.input_schema is not None:
                declared = normalize_binding_schema(decl.contract.input_schema, require_object_root=True)
                if binding_schema_digest(declared) != binding_schema_digest(input_schema):
                    raise _publish_error(
                        f"system tool declared input_schema does not match registry: {decl.key}",
                        details={"capabilityKey": decl.key},
                    )
            if decl.contract.output_schema is not None:
                declared = normalize_binding_schema(decl.contract.output_schema, require_object_root=False)
                if binding_schema_digest(declared) != binding_schema_digest(output_schema):
                    raise _publish_error(
                        f"system tool declared output_schema does not match registry: {decl.key}",
                        details={"capabilityKey": decl.key},
                    )
        target_identity = f"system-tool:{decl.key}"
        input_digest = binding_schema_digest(input_schema)
        output_digest = binding_schema_digest(output_schema)
        config_digest = self._system_tool_set_digest
        executable_revision = self._app_build_revision
        resolution_digest = _target_resolution_digest(
            capability_type="tool",
            target_identity=target_identity,
            target_id=None,
            target_version_id=None,
            target_revision=None,
            input_schema_digest=input_digest,
            output_schema_digest=output_digest,
            executable_revision=executable_revision,
            config_digest=config_digest,
            system_tool_contract_set_digest_value=self._system_tool_set_digest,
        )
        completion = self._completion_for(decl)
        snapshot, closure_digest, contract_digest = build_binding_snapshot(
            capability_type="tool",
            target_identity=target_identity,
            target_id=None,
            target_version_id=None,
            target_revision=None,
            input_schema=input_schema,
            output_schema=output_schema,
            completion=completion,
            config_digest=config_digest,
            executable_revision=executable_revision,
            resolution_digest=resolution_digest,
            dependencies=(),
        )
        return ResolvedCapabilityBinding(
            capability_type="tool",
            capability_key=decl.key,
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
            config_digest=config_digest,
            executable_revision=executable_revision,
            resolution_digest=resolution_digest,
            resolution_snapshot=snapshot,
            dependencies=(),
            dependency_closure_digest=closure_digest,
            binding_contract_digest=contract_digest,
        )

    def _load_workflow_by_key(self, key: str) -> AssistantWorkflow:
        workflow = (
            self.db.query(AssistantWorkflow)
            .filter(AssistantWorkflow.name == key)
            .one_or_none()
        )
        if workflow is None:
            raise _publish_error(
                f"workflow target not found: {key}",
                details={"capabilityType": "workflow", "capabilityKey": key},
            )
        return workflow

    def _load_agent_by_key(self, key: str) -> AssistantAgentProfile:
        agent = (
            self.db.query(AssistantAgentProfile)
            .filter(AssistantAgentProfile.name == key)
            .one_or_none()
        )
        if agent is None:
            raise _publish_error(
                f"agent target not found: {key}",
                details={"capabilityType": "agent", "capabilityKey": key},
            )
        return agent

    def _published_workflow_version(
        self, workflow: AssistantWorkflow
    ) -> tuple[AssistantWorkflowVersion, dict[str, Any]]:
        if workflow.published_version_id is None:
            raise _publish_error(
                f"workflow has no published version: {workflow.name}",
                details={
                    "capabilityType": "workflow",
                    "capabilityKey": workflow.name,
                    "workflowId": str(workflow.id),
                },
            )
        version = (
            self.db.query(AssistantWorkflowVersion)
            .filter(
                AssistantWorkflowVersion.id == workflow.published_version_id,
                AssistantWorkflowVersion.workflow_id == workflow.id,
                AssistantWorkflowVersion.version_source == "publish",
            )
            .one_or_none()
        )
        if version is None or not isinstance(version.snapshot, dict):
            raise _publish_error(
                f"workflow published version missing or invalid: {workflow.name}",
                details={
                    "workflowId": str(workflow.id),
                    "publishedVersionId": str(workflow.published_version_id),
                },
            )
        return version, version.snapshot

    def _published_agent_version(
        self, agent: AssistantAgentProfile
    ) -> tuple[AssistantAgentProfileVersion, dict[str, Any]]:
        if agent.published_version_id is None:
            raise _publish_error(
                f"agent has no published version: {agent.name}",
                details={
                    "capabilityType": "agent",
                    "capabilityKey": agent.name,
                    "agentProfileId": str(agent.id),
                },
            )
        version = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(
                AssistantAgentProfileVersion.id == agent.published_version_id,
                AssistantAgentProfileVersion.agent_profile_id == agent.id,
                AssistantAgentProfileVersion.version_source == "publish",
            )
            .one_or_none()
        )
        if version is None or not isinstance(version.snapshot, dict):
            raise _publish_error(
                f"agent published version missing or invalid: {agent.name}",
                details={
                    "agentProfileId": str(agent.id),
                    "publishedVersionId": str(agent.published_version_id),
                },
            )
        return version, version.snapshot

    def _workflow_input_from_snapshot(self, snapshot: dict[str, Any]) -> WorkflowInput:
        return WorkflowInput.model_validate(
            {
                "nodes": snapshot.get("nodes") or [],
                "edges": snapshot.get("edges") or [],
                "viewport": snapshot.get("viewport"),
            }
            if "nodes" in snapshot
            else snapshot
        )

    def _resolve_workflow(self, decl: CapabilityDeclaration) -> ResolvedCapabilityBinding:
        self._count_node()
        workflow = self._load_workflow_by_key(decl.key)
        if not bool(workflow.enabled):
            raise _publish_error(
                f"workflow target is disabled: {decl.key}",
                details={"capabilityKey": decl.key, "workflowId": str(workflow.id)},
            )
        version, snapshot = self._published_workflow_version(workflow)
        # Never touch AssistantWorkflow.graph_snapshot (draft leakage tripwire).
        try:
            workflow_input = self._workflow_input_from_snapshot(snapshot)
            contract = workflow_contract_from_input(workflow_input)
        except (WorkflowContractError, Exception) as exc:
            raise _publish_error(
                f"workflow contract derivation failed: {decl.key}",
                details={"workflowId": str(workflow.id), "reason": str(exc)},
            ) from exc
        input_schema = normalize_binding_schema(contract.input_schema, require_object_root=True)
        output_schema = normalize_binding_schema(contract.output_schema, require_object_root=False)
        if decl.contract is not None:
            if decl.contract.input_schema is not None:
                declared = normalize_binding_schema(decl.contract.input_schema, require_object_root=True)
                if binding_schema_digest(declared) != binding_schema_digest(input_schema):
                    raise _publish_error(
                        f"workflow declared input_schema does not match target: {decl.key}",
                        details={"capabilityKey": decl.key},
                    )
            if decl.contract.output_schema is not None:
                declared = normalize_binding_schema(decl.contract.output_schema, require_object_root=False)
                if binding_schema_digest(declared) != binding_schema_digest(output_schema):
                    raise _publish_error(
                        f"workflow declared output_schema does not match target: {decl.key}",
                        details={"capabilityKey": decl.key},
                    )
        snapshot_digest = sha256_canonical_json(snapshot)
        target_identity = f"workflow:{workflow.id}"
        input_digest = binding_schema_digest(input_schema)
        output_digest = binding_schema_digest(output_schema)
        executable_revision = str(version.id)
        config_digest = snapshot_digest
        resolution_digest = _target_resolution_digest(
            capability_type="workflow",
            target_identity=target_identity,
            target_id=workflow.id,
            target_version_id=version.id,
            target_revision=None,
            input_schema_digest=input_digest,
            output_schema_digest=output_digest,
            executable_revision=executable_revision,
            config_digest=config_digest,
        )
        dependencies = self._collect_workflow_closure(
            workflow_input=workflow_input,
            path_prefix="root",
            depth=0,
            stack=frozenset({version.id}),
        )
        completion = self._completion_for(decl)
        binding_snapshot, closure_digest, contract_digest = build_binding_snapshot(
            capability_type="workflow",
            target_identity=target_identity,
            target_id=workflow.id,
            target_version_id=version.id,
            target_revision=None,
            input_schema=input_schema,
            output_schema=output_schema,
            completion=completion,
            config_digest=config_digest,
            executable_revision=executable_revision,
            resolution_digest=resolution_digest,
            dependencies=dependencies,
        )
        return ResolvedCapabilityBinding(
            capability_type="workflow",
            capability_key=decl.key,
            target_identity=target_identity,
            target_id=workflow.id,
            target_version_id=version.id,
            resolved_tool_id=None,
            resolved_workflow_version_id=version.id,
            resolved_agent_version_id=None,
            resolved_revision=None,
            input_schema=input_schema,
            output_schema=output_schema,
            input_schema_digest=input_digest,
            output_schema_digest=output_digest,
            completion=completion,
            config_digest=config_digest,
            executable_revision=executable_revision,
            resolution_digest=resolution_digest,
            resolution_snapshot=binding_snapshot,
            dependencies=dependencies,
            dependency_closure_digest=closure_digest,
            binding_contract_digest=contract_digest,
        )

    def _resolve_agent(self, decl: CapabilityDeclaration) -> ResolvedCapabilityBinding:
        self._count_node()
        if decl.contract is None or decl.contract.input_schema is None or decl.contract.output_schema is None:
            raise _publish_error(
                f"agent capability requires explicit contract schemas: {decl.key}",
                details={"capabilityType": "agent", "capabilityKey": decl.key},
            )
        agent = self._load_agent_by_key(decl.key)
        if not bool(agent.enabled):
            raise _publish_error(
                f"agent target is disabled: {decl.key}",
                details={"capabilityKey": decl.key, "agentProfileId": str(agent.id)},
            )
        version, snapshot = self._published_agent_version(agent)
        input_schema = normalize_binding_schema(decl.contract.input_schema, require_object_root=True)
        output_schema = normalize_binding_schema(decl.contract.output_schema, require_object_root=False)
        snapshot_digest = sha256_canonical_json(snapshot)
        target_identity = f"agent:{agent.id}"
        input_digest = binding_schema_digest(input_schema)
        output_digest = binding_schema_digest(output_schema)
        executable_revision = str(version.id)
        config_digest = snapshot_digest
        resolution_digest = _target_resolution_digest(
            capability_type="agent",
            target_identity=target_identity,
            target_id=agent.id,
            target_version_id=version.id,
            target_revision=None,
            input_schema_digest=input_digest,
            output_schema_digest=output_digest,
            executable_revision=executable_revision,
            config_digest=config_digest,
        )
        dependencies = self._collect_agent_closure(
            snapshot=snapshot,
            path_prefix="root",
            depth=0,
            stack=frozenset({version.id}),
        )
        completion = self._completion_for(decl)
        binding_snapshot, closure_digest, contract_digest = build_binding_snapshot(
            capability_type="agent",
            target_identity=target_identity,
            target_id=agent.id,
            target_version_id=version.id,
            target_revision=None,
            input_schema=input_schema,
            output_schema=output_schema,
            completion=completion,
            config_digest=config_digest,
            executable_revision=executable_revision,
            resolution_digest=resolution_digest,
            dependencies=dependencies,
        )
        return ResolvedCapabilityBinding(
            capability_type="agent",
            capability_key=decl.key,
            target_identity=target_identity,
            target_id=agent.id,
            target_version_id=version.id,
            resolved_tool_id=None,
            resolved_workflow_version_id=None,
            resolved_agent_version_id=version.id,
            resolved_revision=None,
            input_schema=input_schema,
            output_schema=output_schema,
            input_schema_digest=input_digest,
            output_schema_digest=output_digest,
            completion=completion,
            config_digest=config_digest,
            executable_revision=executable_revision,
            resolution_digest=resolution_digest,
            resolution_snapshot=binding_snapshot,
            dependencies=dependencies,
            dependency_closure_digest=closure_digest,
            binding_contract_digest=contract_digest,
        )

    def _collect_workflow_closure(
        self,
        *,
        workflow_input: WorkflowInput,
        path_prefix: str,
        depth: int,
        stack: frozenset[UUID],
    ) -> tuple[ResolvedCapabilityDependency, ...]:
        if depth > MAX_CAPABILITY_CLOSURE_DEPTH:
            raise _publish_error(
                f"capability closure depth exceeds {MAX_CAPABILITY_CLOSURE_DEPTH}",
                details={"reason": "closure_depth_overflow", "path": path_prefix},
            )
        deps: list[ResolvedCapabilityDependency] = []

        for path, tool_name in collect_workflow_tool_usages(
            workflow_input.nodes, path_prefix=path_prefix
        ):
            self._count_node()
            self._count_ref()
            deps.append(self._freeze_tool_dependency(path=path, tool_name=tool_name, embedded=True))

        for path, model_source, model_id in collect_workflow_model_usages(
            workflow_input.nodes, path_prefix=path_prefix
        ):
            self._count_node()
            self._count_ref()
            deps.append(
                self._freeze_model_dependency(
                    path=path,
                    model_source=model_source,
                    model_id=model_id,
                    expected_type="llm",
                )
            )

        for call in collect_workflow_call_references(workflow_input.nodes):
            self._count_node()
            self._count_ref()
            if call.binding_mode != "pinned" or call.target_published_version_id is None:
                raise _publish_error(
                    "workflow_call must be pinned to an exact published version for skill publication",
                    details={
                        "reason": "unpinned_workflow_call",
                        "targetWorkflowId": str(call.target_workflow_id),
                        "bindingMode": call.binding_mode,
                        "path": path_prefix,
                    },
                )
            if call.target_published_version_id in stack:
                raise _publish_error(
                    "workflow_call cycle detected during skill publication",
                    details={
                        "reason": "workflow_call_cycle",
                        "targetVersionId": str(call.target_published_version_id),
                        "path": path_prefix,
                    },
                )
            nested_version = (
                self.db.query(AssistantWorkflowVersion)
                .filter(
                    AssistantWorkflowVersion.id == call.target_published_version_id,
                    AssistantWorkflowVersion.workflow_id == call.target_workflow_id,
                    AssistantWorkflowVersion.version_source == "publish",
                )
                .one_or_none()
            )
            if nested_version is None or not isinstance(nested_version.snapshot, dict):
                raise _publish_error(
                    "pinned workflow_call target version not found",
                    details={
                        "targetWorkflowId": str(call.target_workflow_id),
                        "targetVersionId": str(call.target_published_version_id),
                    },
                )
            nested_path = f"{path_prefix}/workflow_call:{call.source_node_id}"
            nested_snapshot = nested_version.snapshot
            nested_digest = sha256_canonical_json(nested_snapshot)
            target_identity = f"workflow:{call.target_workflow_id}"
            resolution_digest = _target_resolution_digest(
                capability_type="workflow",
                target_identity=target_identity,
                target_id=call.target_workflow_id,
                target_version_id=nested_version.id,
                target_revision=None,
                input_schema_digest=sha256_canonical_json({"type": "object"}),
                output_schema_digest=sha256_canonical_json({"type": "object"}),
                executable_revision=str(nested_version.id),
                config_digest=nested_digest,
            )
            deps.append(
                finalize_dependency(
                    ordinal=0,
                    dependency_path=nested_path,
                    dependency_type="workflow",
                    target_identity=target_identity,
                    resolved_workflow_version_id=nested_version.id,
                    resolution_snapshot={
                        "schemaVersion": 1,
                        "targetIdentity": target_identity,
                        "targetId": str(call.target_workflow_id),
                        "targetVersionId": str(nested_version.id),
                        "snapshotDigest": nested_digest,
                    },
                    resolution_digest=resolution_digest,
                )
            )
            nested_input = self._workflow_input_from_snapshot(nested_snapshot)
            nested_deps = self._collect_workflow_closure(
                workflow_input=nested_input,
                path_prefix=nested_path,
                depth=depth + 1,
                stack=stack | {nested_version.id},
            )
            deps.extend(nested_deps)

        # Stable path order + reassign ordinals.
        deps_sorted = sorted(deps, key=lambda d: d.dependency_path)
        # Detect conflicting refs for the same path.
        by_path: dict[str, ResolvedCapabilityDependency] = {}
        for dep in deps_sorted:
            existing = by_path.get(dep.dependency_path)
            if existing is not None and existing.dependency_digest != dep.dependency_digest:
                raise _publish_error(
                    f"conflicting dependency refs at path {dep.dependency_path}",
                    details={"reason": "conflicting_dependency_path"},
                )
            by_path[dep.dependency_path] = dep
        ordered = [
            dep.model_copy(update={"ordinal": index})
            for index, dep in enumerate(by_path[path] for path in sorted(by_path))
        ]
        return tuple(ordered)

    def _collect_agent_closure(
        self,
        *,
        snapshot: dict[str, Any],
        path_prefix: str,
        depth: int,
        stack: frozenset[UUID],
    ) -> tuple[ResolvedCapabilityDependency, ...]:
        if depth > MAX_CAPABILITY_CLOSURE_DEPTH:
            raise _publish_error(
                f"capability closure depth exceeds {MAX_CAPABILITY_CLOSURE_DEPTH}",
                details={"reason": "closure_depth_overflow", "path": path_prefix},
            )
        deps: list[ResolvedCapabilityDependency] = []
        tools = snapshot.get("tools") or []
        if not isinstance(tools, list):
            raise _publish_error(
                "agent snapshot tools must be a list",
                details={"path": path_prefix},
            )
        for tool_name_raw in tools:
            if not isinstance(tool_name_raw, str) or not tool_name_raw.strip():
                raise _publish_error(
                    "agent snapshot contains dynamic or empty tool name",
                    details={"path": path_prefix},
                )
            tool_name = tool_name_raw.strip()
            self._count_node()
            self._count_ref()
            deps.append(
                self._freeze_tool_dependency(
                    path=f"{path_prefix}/tool:{tool_name}",
                    tool_name=tool_name,
                    embedded=True,
                )
            )

        model_source = str(snapshot.get("model_source") or "default").strip().lower()
        raw_model_id = snapshot.get("model_id")
        model_id = UUID(str(raw_model_id)) if raw_model_id else None
        self._count_node()
        self._count_ref()
        deps.append(
            self._freeze_model_dependency(
                path=f"{path_prefix}/model",
                model_source=model_source,
                model_id=model_id,
                expected_type="llm",
            )
        )

        kb = snapshot.get("kb_config") if isinstance(snapshot.get("kb_config"), dict) else {}
        if bool(kb.get("enabled")):
            # KB path may use embedding model binding.
            self._count_node()
            self._count_ref()
            deps.append(
                self._freeze_model_dependency(
                    path=f"{path_prefix}/kb/model",
                    model_source="default",
                    model_id=None,
                    expected_type="embedding",
                    component="lightrag",
                )
            )

        deps_sorted = sorted(deps, key=lambda d: d.dependency_path)
        by_path: dict[str, ResolvedCapabilityDependency] = {}
        for dep in deps_sorted:
            existing = by_path.get(dep.dependency_path)
            if existing is not None and existing.dependency_digest != dep.dependency_digest:
                raise _publish_error(
                    f"conflicting dependency refs at path {dep.dependency_path}",
                    details={"reason": "conflicting_dependency_path"},
                )
            by_path[dep.dependency_path] = dep
        ordered = [
            dep.model_copy(update={"ordinal": index})
            for index, dep in enumerate(by_path[path] for path in sorted(by_path))
        ]
        return tuple(ordered)

    def _freeze_tool_dependency(
        self,
        *,
        path: str,
        tool_name: str,
        embedded: bool,
    ) -> ResolvedCapabilityDependency:
        if ToolRegistry.has_system_tool(tool_name):
            input_schema, output_schema = system_tool_schemas(tool_name)
            target_identity = f"system-tool:{tool_name}"
            input_digest = binding_schema_digest(input_schema)
            output_digest = binding_schema_digest(output_schema)
            resolution_digest = _target_resolution_digest(
                capability_type="tool",
                target_identity=target_identity,
                target_id=None,
                target_version_id=None,
                target_revision=None,
                input_schema_digest=input_digest,
                output_schema_digest=output_digest,
                executable_revision=self._app_build_revision,
                config_digest=self._system_tool_set_digest,
                system_tool_contract_set_digest_value=self._system_tool_set_digest,
            )
            return finalize_dependency(
                ordinal=0,
                dependency_path=path,
                dependency_type="system_tool",
                target_identity=target_identity,
                input_schema=input_schema,
                output_schema=output_schema,
                resolution_snapshot={
                    "schemaVersion": 1,
                    "targetIdentity": target_identity,
                    "appBuildRevision": self._app_build_revision,
                    "systemToolContractSetDigest": self._system_tool_set_digest,
                    "inputSchemaDigest": input_digest,
                    "outputSchemaDigest": output_digest,
                },
                resolution_digest=resolution_digest,
            )

        tool = (
            self.db.query(AssistantTool)
            .filter(AssistantTool.name == tool_name, AssistantTool.kind == "remote")
            .one_or_none()
        )
        if tool is None:
            raise _publish_error(
                f"tool dependency not found: {tool_name}",
                details={"toolName": tool_name, "path": path},
            )
        if not bool(tool.enabled):
            raise _publish_error(
                f"tool dependency is disabled: {tool_name}",
                details={"toolName": tool_name, "toolId": str(tool.id), "path": path},
            )
        input_schema = remote_tool_input_schema(tool)
        # Embedded remote tools return raw text to agent/workflow engines.
        output_schema = normalize_binding_schema(
            REMOTE_TOOL_OUTPUT_SCHEMA_RAW_STRING,
            require_object_root=False,
        )
        execution = secret_free_remote_execution_snapshot(tool)
        config_digest = sha256_canonical_json(execution)
        target_identity = f"remote-tool:{tool.id}"
        input_digest = binding_schema_digest(input_schema)
        output_digest = binding_schema_digest(output_schema)
        resolution_digest = _target_resolution_digest(
            capability_type="tool",
            target_identity=target_identity,
            target_id=tool.id,
            target_version_id=None,
            target_revision=int(tool.config_revision or 1),
            input_schema_digest=input_digest,
            output_schema_digest=output_digest,
            executable_revision=str(int(tool.config_revision or 1)),
            config_digest=config_digest,
        )
        return finalize_dependency(
            ordinal=0,
            dependency_path=path,
            dependency_type="remote_tool",
            target_identity=target_identity,
            resolved_tool_id=tool.id,
            target_revision=int(tool.config_revision or 1),
            input_schema=input_schema,
            output_schema=output_schema,
            resolution_snapshot={
                "schemaVersion": 1,
                "targetIdentity": target_identity,
                "targetId": str(tool.id),
                "configRevision": int(tool.config_revision or 1),
                "execution": execution,
                "embeddedRawStringOutput": bool(embedded),
            },
            resolution_digest=resolution_digest,
        )

    def _freeze_model_dependency(
        self,
        *,
        path: str,
        model_source: str,
        model_id: UUID | None,
        expected_type: str,
        component: str = "assistant",
    ) -> ResolvedCapabilityDependency:
        if model_source == "default":
            binding = (
                self.db.query(AiComponentBinding)
                .filter(AiComponentBinding.component == component)
                .one_or_none()
            )
            if binding is None:
                raise _publish_error(
                    f"default model component binding missing for {component}",
                    details={"reason": "default_model_unbound", "component": component, "path": path},
                )
            selected_id = (
                binding.llm_model_id if expected_type == "llm" else binding.embedding_model_id
            )
            if selected_id is None:
                raise _publish_error(
                    f"default {expected_type} model unbound for {component}",
                    details={
                        "reason": "default_model_unbound",
                        "component": component,
                        "modelType": expected_type,
                        "path": path,
                    },
                )
            model = (
                self.db.query(AiModel)
                .options(joinedload(AiModel.credential))
                .filter(AiModel.id == selected_id)
                .one_or_none()
            )
        elif model_source == "custom":
            if model_id is None:
                raise _publish_error(
                    "custom model_id is required",
                    details={"reason": "custom_model_invalid", "path": path},
                )
            model = (
                self.db.query(AiModel)
                .options(joinedload(AiModel.credential))
                .filter(AiModel.id == model_id)
                .one_or_none()
            )
        else:
            raise _publish_error(
                f"unsupported model_source {model_source!r}",
                details={"reason": "model_source_invalid", "path": path},
            )

        if model is None:
            raise _publish_error(
                "model dependency not found",
                details={"reason": "model_missing", "path": path, "modelId": str(model_id) if model_id else None},
            )
        if model.model_type != expected_type:
            raise _publish_error(
                f"model type mismatch: expected {expected_type}, got {model.model_type}",
                details={
                    "reason": "custom_model_invalid",
                    "path": path,
                    "modelId": str(model.id),
                    "expectedType": expected_type,
                    "actualType": model.model_type,
                },
            )
        credential = model.credential
        if credential is None:
            raise _publish_error(
                "model credential missing",
                details={"reason": "credential_missing", "modelId": str(model.id), "path": path},
            )

        # Secret-free credential config digest: host shape only.
        parts = urlsplit((credential.base_url or "").strip())
        credential_config_digest = sha256_canonical_json(
            {
                "schemaVersion": 1,
                "scheme": parts.scheme or None,
                "host": parts.hostname,
                "port": parts.port,
                "path": parts.path or None,
                "runtimeRevision": int(credential.runtime_revision or 1),
            }
        )
        model_config_digest = sha256_canonical_json(
            {
                "schemaVersion": 1,
                "name": model.name,
                "modelType": model.model_type,
                "runtimeRevision": int(model.runtime_revision or 1),
            }
        )
        model_ref = create_model_ref(
            model_id=model.id,
            model_name=model.name,
            model_type=model.model_type,  # type: ignore[arg-type]
            model_runtime_revision=int(model.runtime_revision or 1),
            credential_id=credential.id,
            credential_runtime_revision=int(credential.runtime_revision or 1),
            credential_config_digest=credential_config_digest,
            model_config_digest=model_config_digest,
            provider_ref_digest=None,
            capability_probe_id=None,
            capability_probe_digest=None,
        )
        target_identity = f"model:{model.id}"
        resolution_snapshot: dict[str, JsonValue] = {
            "schemaVersion": 1,
            "targetIdentity": target_identity,
            "modelId": str(model.id),
            "modelName": model.name,
            "modelType": model.model_type,
            "modelRuntimeRevision": int(model.runtime_revision or 1),
            "credentialId": str(credential.id),
            "credentialRuntimeRevision": int(credential.runtime_revision or 1),
            "credentialConfigDigest": credential_config_digest,
            "modelConfigDigest": model_config_digest,
            "modelRefDigest": model_ref.model_ref_digest,
            "appBuildRevision": self._app_build_revision,
        }
        resolution_digest = sha256_canonical_json(
            {k: v for k, v in resolution_snapshot.items() if k != "modelRefDigest"}
        )
        return finalize_dependency(
            ordinal=0,
            dependency_path=path,
            dependency_type="model",
            target_identity=target_identity,
            resolved_model_id=model.id,
            target_revision=int(model.runtime_revision or 1),
            resolution_snapshot=resolution_snapshot,
            resolution_digest=resolution_digest,
        )


def verify_resolved_binding_is_current(
    binding: AssistantSkillCapabilityBinding,
    current_target: CurrentCapabilityReference,
    *,
    dependencies: list[AssistantSkillCapabilityDependency] | tuple[AssistantSkillCapabilityDependency, ...] | None = None,
) -> None:
    """Fail closed when a frozen binding no longer matches the current target."""
    if binding.resolution_status != "resolved":
        raise _drift_error("binding is not resolved")
    if binding.target_identity != current_target.target_identity:
        raise _drift_error(
            "target identity drift",
            details={
                "frozen": binding.target_identity,
                "current": current_target.target_identity,
            },
        )
    if binding.input_schema_digest != current_target.input_schema_digest:
        raise _drift_error("input schema digest drift")
    if binding.output_schema_digest != current_target.output_schema_digest:
        raise _drift_error("output schema digest drift")
    if binding.resolution_digest != current_target.resolution_digest:
        raise _drift_error("resolution digest drift")
    if binding.dependency_closure_digest != current_target.dependency_closure_digest:
        raise _drift_error("dependency closure digest drift")
    if binding.executable_revision != current_target.executable_revision:
        raise _drift_error(
            "executable revision drift",
            details={
                "frozen": binding.executable_revision,
                "current": current_target.executable_revision,
            },
        )
    if binding.resolved_revision is not None and binding.resolved_revision != current_target.target_revision:
        raise _drift_error(
            "config revision drift",
            details={
                "frozen": binding.resolved_revision,
                "current": current_target.target_revision,
            },
        )
    if dependencies is not None:
        reconstructed = reconstruct_binding_snapshot(binding, dependencies)
        if reconstructed.get("bindingContractDigest") != binding.binding_contract_digest:
            raise _drift_error("reconstructed binding_contract_digest mismatch")
        frozen_index = {
            dep.dependency_path: dep.dependency_digest for dep in dependencies
        }
        current_index = {
            dep.dependency_path: dep.dependency_digest for dep in current_target.dependencies
        }
        if frozen_index != current_index:
            raise _drift_error("dependency closure membership drift")


def skill_reference_conflict(
    *,
    package_id: UUID,
    version_id: UUID,
    message: str,
) -> ApiException:
    return ApiException(
        status_code=409,
        code=40994,
        message=message,
        details={
            "skillPackageId": str(package_id),
            "skillVersionId": str(version_id),
        },
    )


def find_skill_refs_for_tool(db: Session, tool_id: UUID) -> list[tuple[UUID, UUID]]:
    rows = (
        db.query(
            AssistantSkillVersion.skill_package_id,
            AssistantSkillCapabilityBinding.skill_version_id,
        )
        .join(
            AssistantSkillCapabilityBinding,
            AssistantSkillCapabilityBinding.skill_version_id == AssistantSkillVersion.id,
        )
        .filter(AssistantSkillCapabilityBinding.resolved_tool_id == tool_id)
        .all()
    )
    dep_rows = (
        db.query(
            AssistantSkillVersion.skill_package_id,
            AssistantSkillCapabilityBinding.skill_version_id,
        )
        .join(
            AssistantSkillCapabilityBinding,
            AssistantSkillCapabilityBinding.skill_version_id == AssistantSkillVersion.id,
        )
        .join(
            AssistantSkillCapabilityDependency,
            AssistantSkillCapabilityDependency.binding_id == AssistantSkillCapabilityBinding.id,
        )
        .filter(AssistantSkillCapabilityDependency.resolved_tool_id == tool_id)
        .all()
    )
    return list({(r[0], r[1]) for r in list(rows) + list(dep_rows)})


def find_skill_refs_for_model(db: Session, model_id: UUID) -> list[tuple[UUID, UUID]]:
    rows = (
        db.query(
            AssistantSkillVersion.skill_package_id,
            AssistantSkillCapabilityBinding.skill_version_id,
        )
        .join(
            AssistantSkillCapabilityBinding,
            AssistantSkillCapabilityBinding.skill_version_id == AssistantSkillVersion.id,
        )
        .join(
            AssistantSkillCapabilityDependency,
            AssistantSkillCapabilityDependency.binding_id == AssistantSkillCapabilityBinding.id,
        )
        .filter(AssistantSkillCapabilityDependency.resolved_model_id == model_id)
        .all()
    )
    return list({(r[0], r[1]) for r in rows})


def find_skill_refs_for_workflow_version(
    db: Session, version_id: UUID
) -> list[tuple[UUID, UUID]]:
    rows = (
        db.query(
            AssistantSkillVersion.skill_package_id,
            AssistantSkillCapabilityBinding.skill_version_id,
        )
        .join(
            AssistantSkillCapabilityBinding,
            AssistantSkillCapabilityBinding.skill_version_id == AssistantSkillVersion.id,
        )
        .filter(AssistantSkillCapabilityBinding.resolved_workflow_version_id == version_id)
        .all()
    )
    dep_rows = (
        db.query(
            AssistantSkillVersion.skill_package_id,
            AssistantSkillCapabilityBinding.skill_version_id,
        )
        .join(
            AssistantSkillCapabilityBinding,
            AssistantSkillCapabilityBinding.skill_version_id == AssistantSkillVersion.id,
        )
        .join(
            AssistantSkillCapabilityDependency,
            AssistantSkillCapabilityDependency.binding_id == AssistantSkillCapabilityBinding.id,
        )
        .filter(AssistantSkillCapabilityDependency.resolved_workflow_version_id == version_id)
        .all()
    )
    return list({(r[0], r[1]) for r in list(rows) + list(dep_rows)})


def find_skill_refs_for_agent_version(
    db: Session, version_id: UUID
) -> list[tuple[UUID, UUID]]:
    rows = (
        db.query(
            AssistantSkillVersion.skill_package_id,
            AssistantSkillCapabilityBinding.skill_version_id,
        )
        .join(
            AssistantSkillCapabilityBinding,
            AssistantSkillCapabilityBinding.skill_version_id == AssistantSkillVersion.id,
        )
        .filter(AssistantSkillCapabilityBinding.resolved_agent_version_id == version_id)
        .all()
    )
    dep_rows = (
        db.query(
            AssistantSkillVersion.skill_package_id,
            AssistantSkillCapabilityBinding.skill_version_id,
        )
        .join(
            AssistantSkillCapabilityBinding,
            AssistantSkillCapabilityBinding.skill_version_id == AssistantSkillVersion.id,
        )
        .join(
            AssistantSkillCapabilityDependency,
            AssistantSkillCapabilityDependency.binding_id == AssistantSkillCapabilityBinding.id,
        )
        .filter(AssistantSkillCapabilityDependency.resolved_agent_version_id == version_id)
        .all()
    )
    return list({(r[0], r[1]) for r in list(rows) + list(dep_rows)})


__all__ = [
    "CapabilityReferenceResolver",
    "REMOTE_TOOL_OUTPUT_SCHEMA_RAW_STRING",
    "binding_set_digest_from_bindings",
    "build_binding_snapshot",
    "compute_system_tool_contract_set_digest",
    "credential_runtime_sensitive_payload",
    "execution_sensitive_changed",
    "find_skill_refs_for_agent_version",
    "find_skill_refs_for_model",
    "find_skill_refs_for_tool",
    "find_skill_refs_for_workflow_version",
    "model_runtime_sensitive_payload",
    "reconstruct_binding_snapshot",
    "remote_tool_input_schema",
    "require_immutable_app_build_revision",
    "secret_free_remote_execution_snapshot",
    "skill_reference_conflict",
    "system_tool_schemas",
    "tool_execution_sensitive_payload",
    "verify_resolved_binding_is_current",
    "version_digest_from_parts",
]

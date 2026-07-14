"""OpenClaw compatibility bridge for the shared Capability Runtime (Plan 02 Task 8).

Owns external catalog Schema translation, request-frozen binding construction,
OpenClaw evidence verification, and shared-error → public code mapping.
Does not add OpenClaw imports into ``app.assistant.capabilities``.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.assistant.capabilities.contracts import (
    CapabilityAuthorizationEvidence,
    CapabilityDescriptor,
    CapabilityError,
    CapabilityExecutionContext,
    CapabilityExecutionRequest,
    CapabilityOwnerRef,
    CapabilityPrincipal,
    CapabilityResult,
    FrozenBindingProvenance,
    FrozenCapabilityBinding,
    SideEffectClass,
    VerifiedAuthorizationEvidence,
    project_frozen_capability_binding,
)
from app.assistant.capabilities.policy import (
    OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS,
    OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS,
    AtomicSingleUseDispatchPermit,
    AuthorizationEvidenceVerificationError,
    OpenClawEffectCeiling,
    grant_source_digest_for_ceiling,
)
from app.assistant.capabilities.ports import CancellationPort, CapabilityRuntimePorts
from app.assistant.capabilities.runtime import build_capability_runtime
from app.assistant.domain.contracts import (
    CapabilityCompletionContract,
    FrozenContract,
    ResolvedCapabilityBinding,
)
from app.assistant.domain.digests import JsonValue, sha256_canonical_json
from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
from app.assistant.skills.contracts import (
    CapabilityBindingContract,
    CapabilityDeclaration,
)
from app.assistant.skills.resolution import (
    REMOTE_TOOL_OUTPUT_SCHEMA_RAW_STRING,
    CapabilityReferenceResolver,
    remote_tool_input_schema,
    system_tool_schemas,
)
from app.assistant_config.models import AssistantAgentProfile, AssistantTool, AssistantWorkflow
from app.common.exceptions import ApiException
from app.lightrag.schemas import LightRagQueryResponse
from app.openclaw_integration.models import OpenClawCapabilityItem
from app.openclaw_integration.schemas import (
    OpenClawCapabilityExecuteResponse,
    OpenClawCreateRelationRequest,
    OpenClawEntryRecordResponse,
    OpenClawGetEntryRequest,
    OpenClawQueryKnowledgeGraphRequest,
    OpenClawRelationRecordResponse,
    OpenClawSearchEntriesRequest,
    OpenClawSearchEntriesResponse,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.openclaw_integration.service import (
        OpenClawIntegrationService,
        OpenClawRuntimeAuditContext,
    )

logger = logging.getLogger(__name__)


def _svc():
    """Lazy import of service helpers to avoid import cycles."""
    from app.openclaw_integration import service as s

    return s


SourceType = Literal["tool", "workflow", "agent"]
ToolResponseMode = Literal["json_schema", "text_field"]


# ---------------------------------------------------------------------------
# Contract adapters (OpenClaw external ↔ native tool args)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenClawToolContractAdapter:
    runtime_tool_name: str
    prepare_request: Callable[
        [OpenClawIntegrationService, dict[str, Any]],
        tuple[dict[str, Any], dict[str, Any]],
    ]
    build_response: Callable[[Any], dict[str, Any]]


def prepare_search_entries_request(
    service: OpenClawIntegrationService,
    raw_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = _svc()._validate_openclaw_request_model(OpenClawSearchEntriesRequest, raw_payload or {})
    normalized_payload = request.model_dump(mode="json", by_alias=True)
    return normalized_payload, {
        "keyword": request.query,
        "type_code": _svc()._resolve_openclaw_entry_type_code(service, request.entry_type),
        "tag_names": request.tag_names,
        "time_from": request.time_from.date().isoformat() if request.time_from else None,
        "time_to": request.time_to.date().isoformat() if request.time_to else None,
        "limit": request.limit,
    }


def prepare_get_entry_request(
    _service: OpenClawIntegrationService,
    raw_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    entry_id = _svc()._extract_entry_reference_id(raw_payload or {})
    request = _svc()._validate_openclaw_request_model(OpenClawGetEntryRequest, {"entryId": entry_id})
    normalized_payload = request.model_dump(mode="json", by_alias=True)
    return normalized_payload, {"entry_id": str(request.entry_id)}


def prepare_create_relation_request(
    _service: OpenClawIntegrationService,
    raw_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = _svc()._validate_openclaw_request_model(OpenClawCreateRelationRequest, raw_payload or {})
    normalized_payload = request.model_dump(mode="json", by_alias=True)
    return normalized_payload, {
        "source_entry_id": str(request.source_entry_id),
        "target_entry_id": str(request.target_entry_id),
        "relation_type": request.relation_type,
        "description": request.description,
    }


def prepare_query_knowledge_graph_request(
    _service: OpenClawIntegrationService,
    raw_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = _svc()._validate_openclaw_request_model(OpenClawQueryKnowledgeGraphRequest, raw_payload or {})
    normalized_payload = request.model_dump(mode="json", by_alias=True)
    return normalized_payload, {
        "query": request.query,
        "mode": request.mode,
        "top_k": request.top_k,
    }


def build_search_entries_response(value: Any) -> dict[str, Any]:
    payload = _svc()._load_tool_json_value(value)
    if isinstance(payload, list):
        items = payload
        total = len(items)
    elif isinstance(payload, dict):
        items = payload.get("items", [])
        total = payload.get("total", len(items) if isinstance(items, list) else 0)
    else:
        raise ApiException(
            status_code=422,
            code=_svc().OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
            message="search_entries returned invalid JSON",
        )
    if not isinstance(items, list):
        raise ApiException(
            status_code=422,
            code=_svc().OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
            message="search_entries items must be a list",
        )
    return OpenClawSearchEntriesResponse(
        total=int(total or 0),
        items=[
            OpenClawEntryRecordResponse.model_validate(_svc()._build_openclaw_entry_record(item))
            for item in items
            if isinstance(item, dict)
        ],
    ).model_dump(mode="json", by_alias=True)


def build_get_entry_response(value: Any) -> dict[str, Any]:
    payload = _svc()._load_tool_json_value(value)
    if not isinstance(payload, dict):
        raise ApiException(
            status_code=422,
            code=_svc().OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
            message="get_entry_detail returned invalid JSON",
        )
    return _svc()._build_openclaw_entry_record(payload)


def build_create_relation_response(value: Any) -> dict[str, Any]:
    payload = _svc()._load_tool_json_value(value)
    if not isinstance(payload, dict):
        raise ApiException(
            status_code=422,
            code=_svc().OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
            message="create_relation returned invalid JSON",
        )
    return _svc()._build_openclaw_relation_record(payload)


def build_query_knowledge_graph_response(value: Any) -> dict[str, Any]:
    payload = _svc()._load_tool_json_value(value)
    if not isinstance(payload, dict):
        raise ApiException(
            status_code=422,
            code=_svc().OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
            message="query_knowledge_graph returned invalid JSON",
        )
    return LightRagQueryResponse.model_validate(payload).model_dump(mode="json", by_alias=True)


OPENCLAW_TOOL_CONTRACT_ADAPTERS: dict[str, OpenClawToolContractAdapter] = {
    "search_entries": OpenClawToolContractAdapter(
        runtime_tool_name="search_entries",
        prepare_request=prepare_search_entries_request,
        build_response=build_search_entries_response,
    ),
    "get_entry_detail": OpenClawToolContractAdapter(
        runtime_tool_name="get_entry_detail",
        prepare_request=prepare_get_entry_request,
        build_response=build_get_entry_response,
    ),
    "create_relation": OpenClawToolContractAdapter(
        runtime_tool_name="create_relation",
        prepare_request=prepare_create_relation_request,
        build_response=build_create_relation_response,
    ),
    "query_knowledge_graph": OpenClawToolContractAdapter(
        runtime_tool_name="query_knowledge_graph",
        prepare_request=prepare_query_knowledge_graph_request,
        build_response=build_query_knowledge_graph_response,
    ),
}


def resolve_tool_contract_adapter(source_tool_name: str | None) -> OpenClawToolContractAdapter | None:
    canonical = _svc()._canonicalize_source_tool_name(source_tool_name)
    if canonical is None:
        return None
    return OPENCLAW_TOOL_CONTRACT_ADAPTERS.get(canonical)


# ---------------------------------------------------------------------------
# Request-frozen OpenClaw call
# ---------------------------------------------------------------------------


class OpenClawFrozenCapabilityCall(FrozenContract):
    call_id: str
    catalog_item_id: UUID
    capability_key: str
    tool_name: str
    source_type: SourceType
    source_binding_digest: str
    external_input_schema: dict[str, Any]
    external_output_schema: dict[str, Any]
    external_input_schema_digest: str
    external_output_schema_digest: str
    tool_response_mode: ToolResponseMode
    binding: FrozenCapabilityBinding
    grant_ceiling_revision: str
    grant_ceiling_digest: str
    catalog_item_revision_digest: str
    catalog_evidence_digest: str


@dataclass(frozen=True)
class OpenClawAuthenticationProof:
    """Non-serializable request-scoped authentication proof (no secret stored)."""

    principal_id: str
    authenticated: bool = True
    _token: object = field(default_factory=object, repr=False, compare=False)


def _json_copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _external_schema_digest(schema: dict[str, Any]) -> str:
    # OpenClaw external schemas may retain legacy annotations (default/examples).
    # Digest the compact OpenClaw form, not Plan 01 binding normalization.
    return sha256_canonical_json(_svc()._normalize_json_object_schema(schema, label="external"))


def _source_binding_digest(item: OpenClawCapabilityItem) -> str:
    return sha256_canonical_json(
        {
            "sourceType": item.source_type,
            "sourceToolName": _svc()._canonicalize_source_tool_name(item.source_tool_name),
            "toolId": str(item.tool_id) if item.tool_id is not None else None,
            "workflowId": str(item.workflow_id) if item.workflow_id is not None else None,
            "agentProfileId": str(item.agent_profile_id) if item.agent_profile_id is not None else None,
            "systemDefaultKey": item.system_default_key,
            "isSystemItem": bool(item.is_system_item),
        }
    )


def _catalog_item_revision_digest(
    item: OpenClawCapabilityItem,
    *,
    external_input: dict[str, Any],
    external_output: dict[str, Any],
) -> str:
    return sha256_canonical_json(
        {
            "catalogItemId": str(item.id),
            "capabilityKey": item.capability_key,
            "toolName": item.tool_name,
            "enabled": bool(item.enabled),
            "sourceType": item.source_type,
            "toolResponseMode": item.tool_response_mode or "json_schema",
            "sourceBindingDigest": _source_binding_digest(item),
            "externalInputSchema": external_input,
            "externalOutputSchema": external_output,
            "updatedAt": item.updated_at.isoformat() if getattr(item, "updated_at", None) else None,
        }
    )


def _catalog_evidence_digest(
    *,
    call_id: str,
    item: OpenClawCapabilityItem,
    source_binding_digest: str,
    external_input_schema_digest: str,
    external_output_schema_digest: str,
    tool_response_mode: str,
    binding: FrozenCapabilityBinding,
    grant_source_digest: str,
) -> str:
    return sha256_canonical_json(
        {
            "callId": call_id,
            "catalogItemId": str(item.id),
            "capabilityKey": item.capability_key,
            "enabled": bool(item.enabled),
            "sourceBindingDigest": source_binding_digest,
            "externalInputSchemaDigest": external_input_schema_digest,
            "externalOutputSchemaDigest": external_output_schema_digest,
            "toolResponseMode": tool_response_mode,
            "resolutionDigest": binding.resolved.resolution_digest,
            "bindingContractDigest": binding.resolved.binding_contract_digest,
            "dependencyClosureDigest": binding.resolved.dependency_closure_digest,
            "grantSourceDigest": grant_source_digest,
        }
    )


def _select_effect_ceiling(item: OpenClawCapabilityItem) -> OpenClawEffectCeiling:
    if item.is_system_item and item.system_default_key:
        ceiling = OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS.get(item.system_default_key)
        if ceiling is None:
            raise ApiException(
                status_code=422,
                code=_svc().OPENCLAW_INVALID_SOURCE_ERROR_CODE,
                message=f"Missing OpenClaw system effect ceiling: {item.system_default_key}",
            )
        return ceiling
    ceiling = OPENCLAW_CUSTOM_SOURCE_EFFECT_CEILINGS.get(item.source_type or "")
    if ceiling is None:
        raise ApiException(
            status_code=422,
            code=_svc().OPENCLAW_INVALID_SOURCE_ERROR_CODE,
            message=f"Missing OpenClaw custom source effect ceiling: {item.source_type}",
        )
    return ceiling


def _rekey_resolved_binding(
    resolved: ResolvedCapabilityBinding,
    *,
    capability_key: str,
) -> ResolvedCapabilityBinding:
    if resolved.capability_key == capability_key:
        return resolved
    payload = resolved.model_dump(mode="python")
    payload["capability_key"] = capability_key
    return ResolvedCapabilityBinding.model_validate(payload)


def _resolve_tool_binding(
    service: OpenClawIntegrationService,
    item: OpenClawCapabilityItem,
) -> FrozenCapabilityBinding:
    resolved_source = service._resolve_tool_source(  # noqa: SLF001
        tool_id=item.tool_id,
        source_tool_name=item.source_tool_name,
    )
    if resolved_source is None:
        raise ApiException(status_code=409, code=40961, message="Bound tool is unavailable")

    canonical_name = resolved_source.source_tool_name
    resolver = CapabilityReferenceResolver(service.db)
    response_mode = (item.tool_response_mode or "json_schema")

    if resolved_source.is_system:
        try:
            base = resolver.resolve_many(
                (CapabilityDeclaration(type="tool", key=canonical_name),)
            )[0]
        except ApiException as exc:
            raise ApiException(
                status_code=409,
                code=40961,
                message="Bound tool is unavailable",
            ) from exc
        rekeyed = _rekey_resolved_binding(base, capability_key=item.capability_key)
        return project_frozen_capability_binding(
            resolved=rekeyed,
            provenance=FrozenBindingProvenance(
                origin="openclaw_request",
                binding_row_id=None,
                owner_version_id=None,
                source_snapshot_digest=_source_binding_digest(item),
            ),
        )

    # Remote / DB tool: freeze exact tool row + catalog-owned output contract.
    tool = resolved_source.tool_model
    if tool is None or (tool.kind or "").lower() != "remote":
        # Non-remote custom tool: treat catalog schemas as compatibility binding.
        external_in = _svc()._normalize_json_object_schema(
            item.input_schema_json or _svc()._EMPTY_OBJECT_SCHEMA, label="input"
        )
        if response_mode == "text_field":
            internal_out: dict[str, Any] = {"type": "string"}
        else:
            internal_out = _svc()._normalize_json_object_schema(
                item.output_schema_json or _svc()._EMPTY_OBJECT_SCHEMA, label="output"
            )
        try:
            in_schema = normalize_binding_schema(external_in, require_object_root=True)
            out_schema = normalize_binding_schema(internal_out, require_object_root=False)
        except Exception as exc:
            raise ApiException(
                status_code=422,
                code=_svc().OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                message="Capability schema cannot be reconstructed losslessly",
            ) from exc
        contract = CapabilityBindingContract(
            input_schema=in_schema,
            output_schema=out_schema,
            completion=CapabilityCompletionContract(terminal_output=True, needs_followup=False),
        )
        # Prefer remote resolution when possible; otherwise raise unavailable.
        raise ApiException(status_code=409, code=40961, message="Bound tool is unavailable")

    try:
        in_schema = remote_tool_input_schema(tool)
        if response_mode == "text_field":
            out_schema = normalize_binding_schema(
                REMOTE_TOOL_OUTPUT_SCHEMA_RAW_STRING, require_object_root=False
            )
        else:
            out_schema = normalize_binding_schema(
                item.output_schema_json or {"type": "object"},
                require_object_root=False,
            )
        base = resolver.resolve_many(
            (
                CapabilityDeclaration(
                    type="tool",
                    key=tool.name,
                    contract=CapabilityBindingContract(
                        input_schema=in_schema,
                        output_schema=out_schema,
                        completion=CapabilityCompletionContract(
                            terminal_output=True, needs_followup=False
                        ),
                    ),
                ),
            )
        )[0]
    except ApiException as exc:
        if exc.status_code == 422 and exc.code in {42293, 42295}:
            raise ApiException(
                status_code=409,
                code=40961,
                message="Bound tool is unavailable",
            ) from exc
        raise
    rekeyed = _rekey_resolved_binding(base, capability_key=item.capability_key)
    return project_frozen_capability_binding(
        resolved=rekeyed,
        provenance=FrozenBindingProvenance(
            origin="openclaw_request",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=_source_binding_digest(item),
        ),
    )


def _resolve_workflow_binding(
    service: OpenClawIntegrationService,
    item: OpenClawCapabilityItem,
) -> FrozenCapabilityBinding:
    if item.workflow_id is None:
        raise ApiException(
            status_code=422,
            code=_svc().OPENCLAW_INVALID_SOURCE_ERROR_CODE,
            message="Workflow catalog item is missing workflow binding",
        )
    workflow = service.config_service.get_workflow(item.workflow_id)
    snapshot = service._workflow_contract_snapshot(workflow)  # noqa: SLF001
    item_input = _svc()._normalize_json_object_schema(
        item.input_schema_json or _svc()._EMPTY_OBJECT_SCHEMA, label="input"
    )
    item_output = _svc()._normalize_json_object_schema(
        item.output_schema_json or _svc()._EMPTY_OBJECT_SCHEMA, label="output"
    )
    if _svc()._schema_compact(snapshot.input_schema) != _svc()._schema_compact(item_input):
        raise ApiException(
            status_code=409,
            code=40961,
            message="Workflow contract drifted from the catalog item",
        )
    if _svc()._schema_compact(snapshot.output_schema) != _svc()._schema_compact(item_output):
        raise ApiException(
            status_code=409,
            code=40961,
            message="Workflow contract drifted from the catalog item",
        )
    if workflow.published_version_id is None:
        raise ApiException(
            status_code=409,
            code=40961,
            message="Workflow published version is unavailable",
        )
    try:
        base = CapabilityReferenceResolver(service.db).resolve_many(
            (CapabilityDeclaration(type="workflow", key=workflow.name),)
        )[0]
    except ApiException as exc:
        raise ApiException(
            status_code=409,
            code=40961,
            message="Workflow published version is unavailable",
        ) from exc
    # Freeze exact published version already in base; rekey capability.
    if base.target_version_id != workflow.published_version_id:
        raise ApiException(
            status_code=409,
            code=40961,
            message="Workflow published version is unavailable",
        )
    rekeyed = _rekey_resolved_binding(base, capability_key=item.capability_key)
    return project_frozen_capability_binding(
        resolved=rekeyed,
        provenance=FrozenBindingProvenance(
            origin="openclaw_request",
            binding_row_id=None,
            owner_version_id=workflow.published_version_id,
            source_snapshot_digest=_source_binding_digest(item),
        ),
    )


def _resolve_agent_binding(
    service: OpenClawIntegrationService,
    item: OpenClawCapabilityItem,
) -> FrozenCapabilityBinding:
    if item.agent_profile_id is None:
        raise ApiException(
            status_code=422,
            code=_svc().OPENCLAW_INVALID_SOURCE_ERROR_CODE,
            message="Agent catalog item is missing agent binding",
        )
    agent = service.config_service.get_agent_profile(item.agent_profile_id)
    draft = service.config_service._get_agent_profile_published_draft(agent)  # noqa: SLF001
    if draft is None or agent.published_version_id is None:
        raise ApiException(
            status_code=409,
            code=40961,
            message="Agent published version is unavailable",
        )
    try:
        in_schema = normalize_binding_schema(
            item.input_schema_json or _svc()._EMPTY_OBJECT_SCHEMA, require_object_root=True
        )
        out_schema = normalize_binding_schema(
            item.output_schema_json or _svc()._EMPTY_OBJECT_SCHEMA, require_object_root=False
        )
    except Exception as exc:
        raise ApiException(
            status_code=422,
            code=_svc().OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
            message="Capability schema cannot be reconstructed losslessly",
        ) from exc
    try:
        base = CapabilityReferenceResolver(service.db).resolve_many(
            (
                CapabilityDeclaration(
                    type="agent",
                    key=agent.name,
                    contract=CapabilityBindingContract(
                        input_schema=in_schema,
                        output_schema=out_schema,
                        completion=CapabilityCompletionContract(
                            terminal_output=True, needs_followup=False
                        ),
                    ),
                ),
            )
        )[0]
    except ApiException as exc:
        raise ApiException(
            status_code=409,
            code=40961,
            message="Agent published version is unavailable",
        ) from exc
    if base.target_version_id != agent.published_version_id:
        raise ApiException(
            status_code=409,
            code=40961,
            message="Agent published version is unavailable",
        )
    rekeyed = _rekey_resolved_binding(base, capability_key=item.capability_key)
    return project_frozen_capability_binding(
        resolved=rekeyed,
        provenance=FrozenBindingProvenance(
            origin="openclaw_request",
            binding_row_id=None,
            owner_version_id=agent.published_version_id,
            source_snapshot_digest=_source_binding_digest(item),
        ),
    )


def freeze_openclaw_capability_call(
    service: OpenClawIntegrationService,
    *,
    item: OpenClawCapabilityItem,
    call_id: str,
) -> OpenClawFrozenCapabilityCall:
    external_input = _svc()._normalize_json_object_schema(
        item.input_schema_json or _svc()._EMPTY_OBJECT_SCHEMA, label="input"
    )
    external_output = _svc()._normalize_json_object_schema(
        item.output_schema_json or _svc()._EMPTY_OBJECT_SCHEMA, label="output"
    )
    external_input_digest = _external_schema_digest(external_input)
    external_output_digest = _external_schema_digest(external_output)
    source_binding = _source_binding_digest(item)
    tool_response_mode: ToolResponseMode = (  # type: ignore[assignment]
        item.tool_response_mode if item.tool_response_mode in {"json_schema", "text_field"} else "json_schema"
    )
    source_type: SourceType = item.source_type  # type: ignore[assignment]

    if source_type == "tool":
        binding = _resolve_tool_binding(service, item)
    elif source_type == "workflow":
        binding = _resolve_workflow_binding(service, item)
    elif source_type == "agent":
        binding = _resolve_agent_binding(service, item)
    else:
        raise ApiException(
            status_code=422,
            code=_svc().OPENCLAW_INVALID_SOURCE_ERROR_CODE,
            message=f"Unsupported OpenClaw source type: {item.source_type}",
        )

    ceiling = _select_effect_ceiling(item)
    catalog_revision = _catalog_item_revision_digest(
        item, external_input=external_input, external_output=external_output
    )
    exposure_digest = catalog_revision
    grant_source = grant_source_digest_for_ceiling(ceiling, exposure_digest=exposure_digest)
    evidence_digest = _catalog_evidence_digest(
        call_id=call_id,
        item=item,
        source_binding_digest=source_binding,
        external_input_schema_digest=external_input_digest,
        external_output_schema_digest=external_output_digest,
        tool_response_mode=tool_response_mode,
        binding=binding,
        grant_source_digest=grant_source,
    )
    return OpenClawFrozenCapabilityCall(
        call_id=call_id,
        catalog_item_id=item.id,
        capability_key=item.capability_key,
        tool_name=item.tool_name,
        source_type=source_type,
        source_binding_digest=source_binding,
        external_input_schema=external_input,  # type: ignore[arg-type]
        external_output_schema=external_output,  # type: ignore[arg-type]
        external_input_schema_digest=external_input_digest,
        external_output_schema_digest=external_output_digest,
        tool_response_mode=tool_response_mode,
        binding=binding,
        grant_ceiling_revision=ceiling.revision,
        grant_ceiling_digest=ceiling.ceiling_digest,
        catalog_item_revision_digest=catalog_revision,
        catalog_evidence_digest=evidence_digest,
    )


# ---------------------------------------------------------------------------
# Evidence verifier
# ---------------------------------------------------------------------------


class OpenClawAuthorizationEvidenceVerifier:
    """Request/call-scoped OpenClaw verifier. Single successful verify only."""

    def __init__(
        self,
        *,
        expected_call_id: str,
        frozen_call: OpenClawFrozenCapabilityCall,
        auth_proof: OpenClawAuthenticationProof,
        ceiling: OpenClawEffectCeiling,
        grant_source_digest: str,
        service: OpenClawIntegrationService,
        locale: str,
    ) -> None:
        self.expected_call_id = expected_call_id
        self.frozen_call = frozen_call
        self._auth_proof = auth_proof
        self.ceiling = ceiling
        self.grant_source_digest = grant_source_digest
        self._service = service
        self._locale = locale
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
        if evidence.issuer != "openclaw_bridge" or evidence.entrypoint != "openclaw":
            raise AuthorizationEvidenceVerificationError("issuer_entrypoint_mismatch")
        if not self._auth_proof.authenticated:
            raise AuthorizationEvidenceVerificationError("unauthenticated_principal")
        if evidence.call_id != self.expected_call_id or context.call_id != self.expected_call_id:
            raise AuthorizationEvidenceVerificationError("call_id_mismatch")
        if evidence.capability_key != self.frozen_call.capability_key:
            raise AuthorizationEvidenceVerificationError("capability_key_mismatch")
        if evidence.evidence_digest != self.frozen_call.catalog_evidence_digest:
            raise AuthorizationEvidenceVerificationError("evidence_digest_mismatch")
        if evidence.grant_source_digest != self.grant_source_digest:
            raise AuthorizationEvidenceVerificationError("grant_source_digest_mismatch")
        if evidence.resolution_digest != self.frozen_call.binding.resolved.resolution_digest:
            raise AuthorizationEvidenceVerificationError("resolution_digest_mismatch")
        if (
            evidence.binding_contract_digest
            != self.frozen_call.binding.resolved.binding_contract_digest
        ):
            raise AuthorizationEvidenceVerificationError("binding_contract_digest_mismatch")
        if (
            evidence.dependency_closure_digest
            != self.frozen_call.binding.resolved.dependency_closure_digest
        ):
            raise AuthorizationEvidenceVerificationError("dependency_closure_digest_mismatch")

        # Re-read catalog exposure from the current DB state (not identity-map cache).
        item = self._service._get_catalog_item_by_capability_key(  # noqa: SLF001
            self.frozen_call.capability_key
        )
        if item is None or item.id != self.frozen_call.catalog_item_id:
            raise AuthorizationEvidenceVerificationError("catalog_item_missing")
        try:
            self._service.db.refresh(item)
        except Exception:
            item = (
                self._service.db.query(type(item))
                .filter(type(item).id == item.id)
                .populate_existing()
                .one_or_none()
            )
            if item is None or item.id != self.frozen_call.catalog_item_id:
                raise AuthorizationEvidenceVerificationError("catalog_item_missing")
        retired, _reason = self._service._retired_catalog_item_state(item, locale=self._locale)  # noqa: SLF001
        if retired or not bool(item.enabled):
            raise AuthorizationEvidenceVerificationError("catalog_item_not_exposed")
        if _source_binding_digest(item) != self.frozen_call.source_binding_digest:
            raise AuthorizationEvidenceVerificationError("source_binding_mismatch")
        # Full catalog revision must still match the request-frozen digest.
        # Recompute with the same external schemas used at freeze time so we
        # detect enabled/response-mode/source/updated_at drift. Schema body
        # drift is detected via live row compact compare against freeze inputs.
        current_revision = _catalog_item_revision_digest(
            item,
            external_input=self.frozen_call.external_input_schema,
            external_output=self.frozen_call.external_output_schema,
        )
        if current_revision != self.frozen_call.catalog_item_revision_digest:
            raise AuthorizationEvidenceVerificationError("catalog_item_revision_mismatch")
        # Detect live schema body changes that share the same freeze-time external
        # snapshot only if the stored JSON differs from what freeze normalized.
        try:
            live_input_raw = item.input_schema_json or _svc()._EMPTY_OBJECT_SCHEMA
            live_output_raw = item.output_schema_json or _svc()._EMPTY_OBJECT_SCHEMA
            live_input = _svc()._normalize_json_object_schema(live_input_raw, label="input")
            live_output = _svc()._normalize_json_object_schema(live_output_raw, label="output")
            if _external_schema_digest(live_input) != self.frozen_call.external_input_schema_digest:
                raise AuthorizationEvidenceVerificationError("catalog_item_revision_mismatch")
            if _external_schema_digest(live_output) != self.frozen_call.external_output_schema_digest:
                raise AuthorizationEvidenceVerificationError("catalog_item_revision_mismatch")
        except AuthorizationEvidenceVerificationError:
            raise
        except Exception:
            # Normalization failure on live schemas is treat as exposure drift.
            raise AuthorizationEvidenceVerificationError("catalog_item_revision_mismatch")

        actual = descriptor.behavior.side_effect
        if actual == "unknown":
            raise AuthorizationEvidenceVerificationError("unknown_side_effect")
        if actual not in self.ceiling.allowed_side_effects:
            raise AuthorizationEvidenceVerificationError("side_effect_above_ceiling")
        interrupt_mode = str(getattr(descriptor.behavior, "interrupt_mode", "none") or "none")
        if interrupt_mode not in set(self.ceiling.allowed_interrupt_modes):
            raise AuthorizationEvidenceVerificationError("interrupt_mode_above_ceiling")

        with self._lock:
            if self._consumed:
                raise AuthorizationEvidenceVerificationError("evidence_already_consumed")
            self._consumed = True

        return VerifiedAuthorizationEvidence(
            call_id=evidence.call_id,
            verifier_key=("openclaw_bridge", "openclaw"),
            verifier_instance_id=self.verifier_instance_id,
            principal=evidence.principal,
            entrypoint=evidence.entrypoint,
            owner=evidence.owner,
            capability_key=evidence.capability_key,
            resolution_digest=evidence.resolution_digest,
            binding_contract_digest=evidence.binding_contract_digest,
            dependency_closure_digest=evidence.dependency_closure_digest,
            allowed_side_effects=self.ceiling.allowed_side_effects,
            grant_source_digest=self.grant_source_digest,
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


# ---------------------------------------------------------------------------
# External / internal boundary helpers
# ---------------------------------------------------------------------------


def _drop_null_values(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip null optionals so Gateway binding schemas (no null type) accept the object."""
    return {key: value for key, value in payload.items() if value is not None}


def prepare_internal_input(
    service: OpenClawIntegrationService,
    *,
    frozen: OpenClawFrozenCapabilityCall,
    raw_payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate external input, apply adapter when present, return internal input."""
    external_schema = frozen.external_input_schema
    adapter = None
    if frozen.source_type == "tool":
        # Look up adapter from catalog item source tool name via frozen source binding.
        item = service._get_catalog_item_by_capability_key(frozen.capability_key)  # noqa: SLF001
        source_tool_name = item.source_tool_name if item is not None else None
        adapter = resolve_tool_contract_adapter(source_tool_name)
        if adapter is not None:
            resolved = service._resolve_tool_source(  # noqa: SLF001
                tool_id=item.tool_id if item is not None else None,
                source_tool_name=source_tool_name,
            )
            if resolved is not None and resolved.is_system:
                normalized_external, tool_args = adapter.prepare_request(
                    service, raw_payload or {}
                )
                _svc()._validate_value_against_schema(
                    external_schema, normalized_external, label="input"
                )
                return _drop_null_values(dict(tool_args))

    _svc()._validate_value_against_schema(external_schema, raw_payload or {}, label="input")
    return _drop_null_values(dict(raw_payload or {}))


def transform_internal_output(
    service: OpenClawIntegrationService,
    *,
    frozen: OpenClawFrozenCapabilityCall,
    internal_output: Any,
) -> dict[str, Any]:
    """Transform Gateway canonical output to OpenClaw external shape and validate."""
    adapter = None
    if frozen.source_type == "tool":
        item = service._get_catalog_item_by_capability_key(frozen.capability_key)  # noqa: SLF001
        source_tool_name = item.source_tool_name if item is not None else None
        adapter = resolve_tool_contract_adapter(source_tool_name)
        if adapter is not None:
            resolved = service._resolve_tool_source(  # noqa: SLF001
                tool_id=item.tool_id if item is not None else None,
                source_tool_name=source_tool_name,
            )
            if resolved is not None and resolved.is_system:
                external = adapter.build_response(internal_output)
                _svc()._validate_value_against_schema(
                    frozen.external_output_schema, external, label="output"
                )
                return external

    if frozen.tool_response_mode == "text_field":
        from app.assistant.workflow.engine.runtime_helpers import stringify

        properties = (
            frozen.external_output_schema.get("properties")
            if isinstance(frozen.external_output_schema.get("properties"), dict)
            else {}
        )
        field_name = next(iter(properties.keys()), "text") if properties else "text"
        # Internal validation already happened in Gateway against binding output schema.
        external = {field_name: stringify(internal_output)}
        _svc()._validate_value_against_schema(
            frozen.external_output_schema, external, label="output"
        )
        return external

    external = _svc()._normalize_result_object(internal_output)
    _svc()._validate_value_against_schema(frozen.external_output_schema, external, label="output")
    return external


# ---------------------------------------------------------------------------
# Shared error translation
# ---------------------------------------------------------------------------


def translate_capability_error(error: CapabilityError) -> ApiException:
    """Map a shared CapabilityError to the characterized OpenClaw public envelope."""
    error_type = error.error_type
    if error_type == "not_found":
        return ApiException(
            status_code=404,
            code=_svc().OPENCLAW_CAPABILITY_NOT_FOUND_ERROR_CODE,
            message="Unknown OpenClaw capability",
        )
    if error_type in {"unavailable", "version_drift", "unsupported_interrupt"}:
        return ApiException(
            status_code=409,
            code=40961,
            message="Capability is currently unavailable",
        )
    if error_type == "unauthorized":
        return ApiException(
            status_code=403,
            code=_svc().OPENCLAW_CAPABILITY_DISABLED_ERROR_CODE,
            message="Capability is not exposed to OpenClaw",
        )
    if error_type in {"invalid_input", "invalid_output"}:
        return ApiException(
            status_code=422,
            code=_svc().OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
            message="Capability input or output failed schema validation",
        )
    if error_type in {"timeout", "execution_failed", "protocol_error", "cancelled"}:
        # Preserve characterized safe runtime failure envelope; no new public codes.
        return ApiException(
            status_code=409,
            code=40961,
            message="Capability is currently unavailable",
        )
    return ApiException(
        status_code=409,
        code=40961,
        message="Capability is currently unavailable",
    )


def _gateway_result_to_external(
    service: OpenClawIntegrationService,
    *,
    frozen: OpenClawFrozenCapabilityCall,
    result: CapabilityResult,
) -> dict[str, Any]:
    if result.status == "completed":
        # Prefer structured_output; fall back to user_text for text-like targets.
        internal = result.structured_output
        if internal is None:
            internal = result.user_text
        return transform_internal_output(service, frozen=frozen, internal_output=internal)
    if result.error is not None:
        raise translate_capability_error(result.error)
    raise ApiException(
        status_code=409,
        code=40961,
        message="Capability is currently unavailable",
    )


# ---------------------------------------------------------------------------
# Null ports
# ---------------------------------------------------------------------------


class _NeverCancelled(CancellationPort):
    def is_cancelled(self) -> bool:
        return False

    def raise_if_cancelled(self) -> None:
        return None


class _NullEventSink:
    def emit(self, event: Any) -> None:  # noqa: ANN401
        return None


# ---------------------------------------------------------------------------
# Shared Capability Runtime execute
# ---------------------------------------------------------------------------


def execute_shared_capability(
    service: OpenClawIntegrationService,
    *,
    item: OpenClawCapabilityItem,
    raw_payload: dict[str, Any],
    audit_context: OpenClawRuntimeAuditContext,
    locale: str,
    auth_proof: OpenClawAuthenticationProof,
    cancellation: CancellationPort | None = None,
) -> OpenClawCapabilityExecuteResponse:
    call_id = str(uuid.uuid4())
    frozen = freeze_openclaw_capability_call(
        service,
        item=item,
        call_id=call_id,
    )
    ceiling = _select_effect_ceiling(item)
    grant_source = grant_source_digest_for_ceiling(
        ceiling, exposure_digest=frozen.catalog_item_revision_digest
    )

    internal_input = prepare_internal_input(
        service, frozen=frozen, raw_payload=raw_payload or {}
    )

    principal = CapabilityPrincipal(
        principal_type="openclaw_installation",
        principal_id=auth_proof.principal_id,
        authenticated=True,
    )
    owner = CapabilityOwnerRef(
        owner_kind="openclaw_catalog",
        owner_id=str(item.id),
        owner_version_id=None,
    )
    evidence = CapabilityAuthorizationEvidence(
        issuer="openclaw_bridge",
        call_id=call_id,
        principal=principal,
        entrypoint="openclaw",
        owner=owner,
        capability_key=item.capability_key,
        resolution_digest=frozen.binding.resolved.resolution_digest,
        binding_contract_digest=frozen.binding.resolved.binding_contract_digest,
        dependency_closure_digest=frozen.binding.resolved.dependency_closure_digest,
        allowed_side_effects=ceiling.allowed_side_effects,
        grant_source_digest=grant_source,
        evidence_digest=frozen.catalog_evidence_digest,
    )
    context = CapabilityExecutionContext(
        call_id=call_id,
        locale=locale,
        request_source=audit_context.source,
        request_channel=audit_context.channel,
        request_session=audit_context.session,
        request_tool=audit_context.tool,
        nesting_depth=0,
    )
    request = CapabilityExecutionRequest(
        binding=frozen.binding,
        input=internal_input,  # type: ignore[arg-type]
        context=context,
        authorization=evidence,
    )

    verifier = OpenClawAuthorizationEvidenceVerifier(
        expected_call_id=call_id,
        frozen_call=frozen,
        auth_proof=auth_proof,
        ceiling=ceiling,
        grant_source_digest=grant_source,
        service=service,
        locale=locale,
    )
    gateway = build_capability_runtime(
        db=service.db,
        evidence_verifiers={("openclaw_bridge", "openclaw"): verifier},
        locale=locale,
    )
    ports = CapabilityRuntimePorts(
        cancellation=cancellation or _NeverCancelled(),
        events=_NullEventSink(),  # type: ignore[arg-type]
    )
    result = gateway.execute(request, ports=ports)
    external = _gateway_result_to_external(service, frozen=frozen, result=result)
    return OpenClawCapabilityExecuteResponse(
        capability_key=item.capability_key,
        tool_name=item.tool_name,
        result=external,
    )


__all__ = [
    "OPENCLAW_TOOL_CONTRACT_ADAPTERS",
    "OpenClawAuthenticationProof",
    "OpenClawAuthorizationEvidenceVerifier",
    "OpenClawFrozenCapabilityCall",
    "OpenClawToolContractAdapter",
    "build_create_relation_response",
    "build_get_entry_response",
    "build_query_knowledge_graph_response",
    "build_search_entries_response",
    "execute_shared_capability",
    "freeze_openclaw_capability_call",
    "prepare_create_relation_request",
    "prepare_get_entry_request",
    "prepare_internal_input",
    "prepare_query_knowledge_graph_request",
    "prepare_search_entries_request",
    "resolve_tool_contract_adapter",
    "transform_internal_output",
    "translate_capability_error",
]

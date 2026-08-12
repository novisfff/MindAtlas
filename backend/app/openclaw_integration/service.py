from __future__ import annotations

import copy
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import UUID, uuid4

from fastapi import Request
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.ai_provider.crypto import api_key_hint, decrypt_api_key, encrypt_api_key
from app.assistant.skill_catalog.base import (
    ConditionExpression,
    SkillDefinition,
    SkillKBConfig,
    WorkflowEdgeDefinition,
    WorkflowNodeDefinition,
)
from app.assistant.tools import __all__ as assistant_tool_names
from app.assistant_config.models import AssistantAgentProfile, AssistantTool, AssistantWorkflow
from app.assistant_config.registry import ToolRegistry
from app.assistant_config.schemas import AgentPublishDraftInput, WorkflowInput
from app.assistant_config.workflow_contracts import WorkflowContractError, workflow_contract_from_input
from app.assistant_config.service import AssistantConfigService
from app.common.exceptions import ApiException
from app.common.request_context import get_request_id
from app.common.time import utcnow
from app.entry_type.models import EntryType
from app.lightrag.schemas import LightRagQueryResponse
from app.openclaw_integration.models import OpenClawCapabilityItem
from app.openclaw_integration.registry import (
    OpenClawSystemItemDefinition,
    get_openclaw_system_item_definition,
    get_openclaw_system_item_definition_by_source_tool_name,
    list_openclaw_system_item_definitions,
)
from app.openclaw_integration.schemas import (
    OpenClawCapabilityCatalogResponse,
    OpenClawCapabilityExecuteResponse,
    OpenClawCapabilityItemCreateRequest,
    OpenClawCapabilityItemResponse,
    OpenClawCapabilityItemUpdateRequest,
    OpenClawCapabilitySourceType,
    OpenClawCatalogSourceListResponse,
    OpenClawCatalogSourceResponse,
    OpenClawCatalogSourceType,
    OpenClawEntryRecordResponse,
    OpenClawGetEntryRequest,
    OpenClawIntegrationSettingsResponse,
    OpenClawIntegrationUpdateRequest,
    OpenClawQueryKnowledgeGraphRequest,
    OpenClawRotateSecretResponse,
    OpenClawRuntimeCapabilityResponse,
    OpenClawSearchEntriesRequest,
    OpenClawSearchEntriesResponse,
    OpenClawToolResponseMode,
)
from app.system_settings.models import AppSetting
from app.system_settings.runtime_config_service import resolve_runtime_knowledge_graph_config
from app.system_settings.service import resolve_system_locale

logger = logging.getLogger(__name__)

OPENCLAW_INTEGRATION_CONFIG_KEY = "openclaw_integration_config"
OPENCLAW_CAPABILITY_KEY_RE = re.compile(r"^[a-z0-9_]+$")
OPENCLAW_SCHEMA_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
OPENCLAW_SYSTEM_ITEM_VERSION = 11
OPENCLAW_CAPTURE_RETIRED_SOURCE_TOOL_NAMES = frozenset({"openclaw_capture_entry"})
OPENCLAW_RELATION_RETIRED_SOURCE_TOOL_NAMES = frozenset(
    {"create_relation", "openclaw_create_relation"}
)
OPENCLAW_PERIODIC_REVIEW_RETIRED_SOURCE_TOOL_NAMES = frozenset(
    {
        "generate_weekly_report",
        "generate_monthly_report",
        "openclaw_generate_weekly_report",
        "openclaw_generate_monthly_report",
    }
)
OPENCLAW_RETIRED_SOURCE_TOOL_NAMES = (
    OPENCLAW_CAPTURE_RETIRED_SOURCE_TOOL_NAMES
    | OPENCLAW_RELATION_RETIRED_SOURCE_TOOL_NAMES
    | OPENCLAW_PERIODIC_REVIEW_RETIRED_SOURCE_TOOL_NAMES
)
OPENCLAW_SOURCE_TOOL_ALIAS_MAP: dict[str, str] = {
    "openclaw_search_entries": "search_entries",
    "openclaw_get_entry": "get_entry_detail",
    "openclaw_query_knowledge_graph": "query_knowledge_graph",
}

OPENCLAW_AUTH_ERROR_CODE = 40161
OPENCLAW_DISABLED_ERROR_CODE = 40361
OPENCLAW_CAPABILITY_DISABLED_ERROR_CODE = 40362
OPENCLAW_CAPABILITY_NOT_FOUND_ERROR_CODE = 40461
OPENCLAW_SECRET_REQUIRED_ERROR_CODE = 40061
OPENCLAW_INVALID_CAPABILITY_ERROR_CODE = 40062
OPENCLAW_INVALID_SCHEMA_ERROR_CODE = 42261
OPENCLAW_INVALID_SOURCE_ERROR_CODE = 42262
_EMPTY_OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class OpenClawRuntimeAuditContext:
    source: str | None = None
    channel: str | None = None
    session: str | None = None
    tool: str | None = None


@dataclass(frozen=True)
class _CatalogItemAvailability:
    available: bool
    reason: str | None
    source_name: str | None
    source_description: str | None
    source_is_system: bool
    source_enabled: bool | None
    published_version_id: UUID | None
    implementation_type: str


@dataclass(frozen=True)
class _ResolvedToolSource:
    source_tool_name: str
    source_name: str
    source_description: str
    is_system: bool
    enabled: bool
    tool_model: AssistantTool | None
    tool_runtime: Any | None


@dataclass(frozen=True)
class _WorkflowContractSnapshot:
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    input_summary: str
    output_summary: str


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _canonicalize_source_tool_name(source_tool_name: str | None) -> str | None:
    normalized = _normalize_optional_text(source_tool_name)
    if normalized is None:
        return None
    return OPENCLAW_SOURCE_TOOL_ALIAS_MAP.get(normalized, normalized)


def _validate_openclaw_request_model(model_cls: type[Any], payload: dict[str, Any]) -> Any:
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise ApiException(
            status_code=422,
            code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
            message="Capability input payload is invalid",
            details={"errors": exc.errors()},
        ) from exc


def _localized_message(locale: str, *, zh: str, en: str) -> str:
    return zh if locale == "zh" else en


def _slugify_identifier(value: str, *, default_prefix: str) -> str:
    lowered = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower())
    lowered = re.sub(r"_+", "_", lowered).strip("_")
    if not lowered:
        lowered = default_prefix
    if lowered[0].isdigit():
        lowered = f"{default_prefix}_{lowered}"
    return lowered[:128]


def _schema_compact(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _schema_type_from_param_type(param_type: str | None) -> str:
    normalized = str(param_type or "string").strip().lower()
    if normalized == "number":
        return "number"
    if normalized == "integer":
        return "integer"
    if normalized == "boolean":
        return "boolean"
    if normalized == "array":
        return "array"
    if normalized == "object":
        return "object"
    return "string"


def _schema_from_field_definitions(
    fields: list[dict[str, Any]],
    *,
    required_key: str = "required",
    allow_nullable: bool = False,
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []

    for raw_field in fields:
        if not isinstance(raw_field, dict):
            continue
        name = str(raw_field.get("name", "") or "").strip()
        if not name:
            continue
        field_type = _schema_type_from_param_type(raw_field.get("type"))
        field_schema: dict[str, Any] = {"type": field_type}
        description = _normalize_optional_text(raw_field.get("description"))
        if description:
            field_schema["description"] = description
        if field_type == "array":
            items_type = _schema_type_from_param_type(raw_field.get("items_type", raw_field.get("itemsType")))
            field_schema["items"] = {"type": items_type or "string"}
        if allow_nullable and bool(raw_field.get("nullable", False)):
            field_schema["nullable"] = True
        if isinstance(raw_field.get("enum"), list) and raw_field["enum"]:
            field_schema["enum"] = [str(item) for item in raw_field["enum"]]
        properties[name] = field_schema
        if bool(raw_field.get(required_key, False)) or (allow_nullable and not bool(raw_field.get("nullable", False))):
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _schema_from_tool_params(params: list[dict[str, Any]] | None) -> dict[str, Any]:
    raw_fields = []
    for param in params or []:
        if not isinstance(param, dict):
            continue
        raw_fields.append(
            {
                "name": param.get("name"),
                "type": param.get("param_type", param.get("type")),
                "required": bool(param.get("required", False)),
                "description": param.get("description"),
            }
        )
    return _schema_from_field_definitions(raw_fields, required_key="required")


def _text_field_output_schema(field_name: str = "text") -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            field_name: {
                "type": "string",
            }
        },
        "required": [field_name],
        "additionalProperties": False,
    }


def _default_agent_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": True,
    }


def _schema_summary(schema: dict[str, Any], *, locale: str = "en") -> str:
    if not isinstance(schema, dict):
        return ""
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return _localized_message(locale, zh="无结构化字段", en="No structured fields")
    parts: list[str] = []
    for name, raw_field in properties.items():
        if not isinstance(raw_field, dict):
            continue
        field_type = str(raw_field.get("type", "string") or "string").strip().lower()
        if field_type == "array":
            item_type = str(
                ((raw_field.get("items") or {}) if isinstance(raw_field.get("items"), dict) else {}).get("type", "string")
            ).strip().lower()
            field_type = f"array[{item_type}]"
        parts.append(f"{name} ({field_type})")
    return ", ".join(parts)


def _normalize_json_object_schema(schema: dict[str, Any] | None, *, label: str) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise ApiException(status_code=422, code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE, message=f"{label} schema must be an object")

    def _resolve_schema_reference(root_schema: dict[str, Any], ref: str, *, path: str) -> dict[str, Any]:
        if not ref.startswith("#/$defs/"):
            raise ApiException(
                status_code=422,
                code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                message=f"{path} schema reference is unsupported: {ref}",
            )
        defs = root_schema.get("$defs")
        ref_key = ref.split("/", 2)[-1]
        if not isinstance(defs, dict) or not isinstance(defs.get(ref_key), dict):
            raise ApiException(
                status_code=422,
                code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                message=f"{path} schema reference is invalid: {ref}",
            )
        return dict(defs[ref_key])

    def _normalize_schema_fragment(raw_schema: dict[str, Any], *, root_schema: dict[str, Any], path: str) -> dict[str, Any]:
        if not isinstance(raw_schema, dict):
            raise ApiException(
                status_code=422,
                code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                message=f"{path} schema must be an object",
            )

        working = dict(raw_schema)
        nullable = False
        while True:
            progressed = False

            ref = working.get("$ref")
            if isinstance(ref, str):
                resolved = _resolve_schema_reference(root_schema, ref, path=path)
                resolved.update({key: value for key, value in working.items() if key != "$ref"})
                working = resolved
                progressed = True

            nullable = nullable or bool(working.get("nullable", False))
            any_of = working.get("anyOf")
            if isinstance(any_of, list) and any_of:
                saw_null = False
                non_null_variants: list[dict[str, Any]] = []
                for variant in any_of:
                    if not isinstance(variant, dict):
                        continue
                    if str(variant.get("type", "")).strip().lower() == "null":
                        saw_null = True
                        continue
                    non_null_variants.append(variant)
                if len(non_null_variants) != 1:
                    raise ApiException(
                        status_code=422,
                        code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                        message=f"{path} schema anyOf is unsupported",
                    )
                merged = dict(non_null_variants[0])
                merged.update({key: value for key, value in working.items() if key != "anyOf"})
                working = merged
                nullable = nullable or saw_null
                progressed = True

            if not progressed:
                break

        field_type_raw = working.get("type")
        if field_type_raw is None and isinstance(working.get("properties"), dict):
            field_type = "object"
        elif field_type_raw is None and isinstance(working.get("items"), dict):
            field_type = "array"
        else:
            field_type = _schema_type_from_param_type(field_type_raw)

        normalized: dict[str, Any] = {"type": field_type}
        description = _normalize_optional_text(working.get("description"))
        if description:
            normalized["description"] = description
        if nullable:
            normalized["nullable"] = True
        schema_format = _normalize_optional_text(working.get("format"))
        if schema_format:
            normalized["format"] = schema_format
        if "default" in working:
            normalized["default"] = working.get("default")
        examples = working.get("examples")
        if isinstance(examples, list) and examples:
            normalized["examples"] = [item for item in examples]
        minimum = working.get("minimum")
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
            normalized["minimum"] = minimum
        maximum = working.get("maximum")
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
            normalized["maximum"] = maximum
        min_length = working.get("minLength")
        if isinstance(min_length, int) and not isinstance(min_length, bool):
            normalized["minLength"] = min_length
        max_length = working.get("maxLength")
        if isinstance(max_length, int) and not isinstance(max_length, bool):
            normalized["maxLength"] = max_length
        enum_values = working.get("enum")
        if isinstance(enum_values, list) and enum_values:
            normalized["enum"] = [item for item in enum_values]

        if field_type == "array":
            items = working.get("items")
            normalized["items"] = _normalize_schema_fragment(
                items if isinstance(items, dict) else {"type": "string"},
                root_schema=root_schema,
                path=f"{path}[]",
            )
            return normalized

        if field_type != "object":
            return normalized

        properties = working.get("properties")
        if properties is None:
            properties = {}
        if not isinstance(properties, dict):
            raise ApiException(
                status_code=422,
                code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                message=f"{path} schema properties must be an object",
            )
        required = working.get("required", [])
        if required is None:
            required = []
        if not isinstance(required, list):
            raise ApiException(
                status_code=422,
                code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                message=f"{path} schema required must be a list",
            )

        normalized_properties: dict[str, Any] = {}
        normalized_required: list[str] = []
        for raw_name, raw_prop in properties.items():
            name = str(raw_name or "").strip()
            if not name or not OPENCLAW_SCHEMA_FIELD_NAME_RE.fullmatch(name):
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                    message=f"{label} schema field name is invalid: {raw_name}",
                )
            normalized_properties[name] = _normalize_schema_fragment(
                raw_prop if isinstance(raw_prop, dict) else {},
                root_schema=root_schema,
                path=f"{path}.{name}",
            )

        for item in required:
            name = str(item or "").strip()
            if name and name in normalized_properties:
                normalized_required.append(name)

        normalized["properties"] = normalized_properties
        normalized["required"] = normalized_required
        normalized["additionalProperties"] = (
            bool(working.get("additionalProperties"))
            if "additionalProperties" in working
            else not bool(normalized_properties)
        )
        return normalized

    normalized_schema = _normalize_schema_fragment(schema, root_schema=schema, path=label)
    if normalized_schema.get("type") != "object":
        raise ApiException(status_code=422, code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE, message=f"{label} schema root type must be object")
    return normalized_schema


def _validate_value_against_schema(schema: dict[str, Any], value: Any, *, label: str) -> None:
    schema_type = str(schema.get("type", "object") or "object").strip().lower()
    if schema_type == "object":
        if not isinstance(value, dict):
            raise ApiException(status_code=422, code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE, message=f"{label} must be an object")
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        additional_allowed = bool(schema.get("additionalProperties", False))
        if not isinstance(properties, dict):
            raise ApiException(status_code=422, code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE, message=f"{label} schema is invalid")
        for required_name in required:
            if required_name not in value:
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                    message=f"{label} is missing required field: {required_name}",
                )
        for key, item in value.items():
            if key not in properties:
                if additional_allowed:
                    continue
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                    message=f"{label} contains unknown field: {key}",
                )
            _validate_value_against_schema(properties[key], item, label=f"{label}.{key}")
        return

    if value is None and bool(schema.get("nullable", False)):
        return

    if schema_type == "string":
        if not isinstance(value, str):
            raise ApiException(status_code=422, code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE, message=f"{label} must be string")
        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and enum_values and value not in enum_values:
            raise ApiException(
                status_code=422,
                code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                message=f"{label} must be one of the allowed enum values",
            )
        return
    if schema_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ApiException(status_code=422, code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE, message=f"{label} must be number")
        return
    if schema_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ApiException(status_code=422, code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE, message=f"{label} must be integer")
        return
    if schema_type == "boolean":
        if not isinstance(value, bool):
            raise ApiException(status_code=422, code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE, message=f"{label} must be boolean")
        return
    if schema_type == "array":
        if not isinstance(value, list):
            raise ApiException(status_code=422, code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE, message=f"{label} must be array")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(value):
                _validate_value_against_schema(item_schema, item, label=f"{label}[{idx}]")
        return
    raise ApiException(
        status_code=422,
        code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
        message=f"{label} schema type is unsupported: {schema_type}",
    )


def _normalize_result_object(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(by_alias=True, mode="json")
        if isinstance(dumped, dict):
            return dumped
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            from app.assistant.workflow.engine.runtime_helpers import extract_json_object

            parsed = extract_json_object(raw)
            if isinstance(parsed, dict):
                return parsed
    raise ApiException(
        status_code=422,
        code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
        message="Tool or target did not return a valid JSON object",
    )


def _extract_entry_reference_id(payload: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        payload.get("entryId"),
        payload.get("entry_id"),
        payload.get("id"),
    ]
    for container_key in ("entry", "item", "record", "result"):
        nested = payload.get(container_key)
        if isinstance(nested, dict):
            candidates.extend(
                [
                    nested.get("entryId"),
                    nested.get("entry_id"),
                    nested.get("id"),
                ]
            )

    for candidate in candidates:
        if isinstance(candidate, UUID):
            return str(candidate)
        normalized = _normalize_optional_text(candidate)
        if normalized:
            return normalized
    return None


def _load_tool_json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            from app.assistant.workflow.engine.runtime_helpers import extract_json_object

            parsed = extract_json_object(raw)
            if parsed is not None:
                return parsed
    raise ApiException(
        status_code=422,
        code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
        message="Tool or target did not return valid JSON content",
    )


def _build_openclaw_entry_record(payload: dict[str, Any]) -> dict[str, Any]:
    return OpenClawEntryRecordResponse.model_validate(
        {
            "id": payload.get("id"),
            "title": payload.get("title"),
            "summary": payload.get("summary"),
            "content": payload.get("content"),
            "entryTypeCode": payload.get("type_code", payload.get("entryTypeCode")),
            "entryTypeName": payload.get("type", payload.get("entryTypeName")),
            "tagNames": payload.get("tags", payload.get("tagNames", [])),
            "timeMode": payload.get("time_mode", payload.get("timeMode", "NONE")),
            "timeAt": payload.get("time_at", payload.get("timeAt")),
            "timeFrom": payload.get("time_from", payload.get("timeFrom")),
            "timeTo": payload.get("time_to", payload.get("timeTo")),
            "createdAt": payload.get("created_at", payload.get("createdAt")),
            "updatedAt": payload.get("updated_at", payload.get("updatedAt")),
        }
    ).model_dump(mode="json", by_alias=True)


def _resolve_openclaw_entry_type_code(service: "OpenClawIntegrationService", entry_type: str | None) -> str | None:
    normalized = _normalize_optional_text(entry_type)
    if normalized is None:
        return None
    row = (
        service.db.query(EntryType)
        .filter(
            EntryType.enabled.is_(True),
            (func.lower(EntryType.code) == normalized.lower()) | (func.lower(EntryType.name) == normalized.lower()),
        )
        .first()
    )
    if row is None:
        raise ApiException(status_code=400, code=40064, message=f"Unknown entry type: {normalized}")
    return row.code


# OpenClaw-only contract adapters live in capability_adapter.py (shared Capability Runtime bridge).
from app.openclaw_integration.capability_adapter import (  # noqa: E402
    OPENCLAW_TOOL_CONTRACT_ADAPTERS,
    OpenClawToolContractAdapter as _OpenClawToolContractAdapter,
    build_get_entry_response as _build_get_entry_response,
    build_query_knowledge_graph_response as _build_query_knowledge_graph_response,
    build_search_entries_response as _build_search_entries_response,
    execute_shared_capability,
    prepare_get_entry_request as _prepare_get_entry_request,
    prepare_query_knowledge_graph_request as _prepare_query_knowledge_graph_request,
    prepare_search_entries_request as _prepare_search_entries_request,
    resolve_tool_contract_adapter,
)


class OpenClawIntegrationService:
    def __init__(self, db: Session):
        self.db = db
        self.config_service = AssistantConfigService(db)
        self.tool_registry = ToolRegistry(db)

    def _get_setting(self) -> AppSetting | None:
        return (
            self.db.query(AppSetting)
            .filter(AppSetting.key == OPENCLAW_INTEGRATION_CONFIG_KEY)
            .first()
        )

    def _get_payload(self) -> dict[str, Any]:
        setting = self._get_setting()
        if setting is None or not isinstance(setting.value_json, dict):
            return {}
        return dict(setting.value_json)

    def _upsert_payload(self, payload: dict[str, Any]) -> None:
        setting = self._get_setting()
        if setting is None:
            setting = AppSetting(key=OPENCLAW_INTEGRATION_CONFIG_KEY, value_json=payload)
            self.db.add(setting)
        else:
            setting.value_json = payload
        self.db.flush()

    def _current_locale(self, preferred_locale: str | None = None) -> str:
        return resolve_system_locale(self.db, preferred_locale=preferred_locale)

    @staticmethod
    def _system_item_version(payload: dict[str, Any]) -> int:
        try:
            return int(payload.get("systemItemVersion") or payload.get("systemPresetVersion") or 0)
        except Exception:
            return 0

    def _secret_state(self, payload: dict[str, Any]) -> tuple[str | None, str | None, datetime | None]:
        encrypted = _normalize_optional_text(payload.get("secretEncrypted"))
        secret_hint = _normalize_optional_text(payload.get("secretHint"))
        rotated_at_raw = payload.get("secretLastRotatedAt")
        rotated_at: datetime | None = None
        if isinstance(rotated_at_raw, str):
            try:
                rotated_at = datetime.fromisoformat(rotated_at_raw)
            except ValueError:
                rotated_at = None
        if not encrypted:
            return None, secret_hint, rotated_at
        try:
            return decrypt_api_key(encrypted), secret_hint, rotated_at
        except Exception:
            return None, secret_hint, rotated_at

    def _resolve_workflow_system_item_contract(
        self,
        definition: OpenClawSystemItemDefinition,
        *,
        locale: str,
    ) -> tuple[AssistantWorkflow, _WorkflowContractSnapshot]:
        if not definition.workflow_asset_key:
            raise ApiException(status_code=500, code=50038, message=f"System workflow item is incomplete: {definition.key}")
        workflow = self.config_service.ensure_standalone_system_workflow_asset(
            definition.workflow_asset_key,
            locale=locale,
        )
        return workflow, self._workflow_contract_snapshot(workflow, locale=locale)

    def _workflow_source_display_name(self, workflow: AssistantWorkflow, *, locale: str | None = None) -> str:
        return self.config_service.display_workflow_name(workflow, locale=locale)

    def _agent_source_display_name(
        self,
        agent_profile: AssistantAgentProfile,
        *,
        locale: str | None = None,
    ) -> str:
        return self.config_service.display_agent_profile_name(agent_profile, locale=locale)

    def _tool_name_exists(self, tool_name: str, *, exclude_item_id: UUID | None = None) -> bool:
        query = self.db.query(OpenClawCapabilityItem.id).filter(
            func.lower(OpenClawCapabilityItem.tool_name) == str(tool_name or "").strip().lower()
        )
        if exclude_item_id is not None:
            query = query.filter(OpenClawCapabilityItem.id != exclude_item_id)
        return query.first() is not None

    def _capability_key_exists(self, capability_key: str, *, exclude_item_id: UUID | None = None) -> bool:
        query = self.db.query(OpenClawCapabilityItem.id).filter(
            func.lower(OpenClawCapabilityItem.capability_key) == str(capability_key or "").strip().lower()
        )
        if exclude_item_id is not None:
            query = query.filter(OpenClawCapabilityItem.id != exclude_item_id)
        return query.first() is not None

    def _next_available_capability_key(self, base_value: str, *, exclude_item_id: UUID | None = None) -> str:
        base = _slugify_identifier(base_value, default_prefix="mindatlas_capability")
        if not self._capability_key_exists(base, exclude_item_id=exclude_item_id):
            return base
        index = 2
        while True:
            candidate = _slugify_identifier(f"{base}_{index}", default_prefix="mindatlas_capability")
            if not self._capability_key_exists(candidate, exclude_item_id=exclude_item_id):
                return candidate
            index += 1

    def _normalize_openclaw_tool_name(self, value: str, *, exclude_item_id: UUID | None = None) -> str:
        normalized = _slugify_identifier(value, default_prefix="mindatlas_tool")
        if self._tool_name_exists(normalized, exclude_item_id=exclude_item_id):
            raise ApiException(status_code=409, code=40971, message=f"OpenClaw tool name already exists: {normalized}")
        return normalized

    def _system_tool_definition_map(self, *, locale: str | None = None) -> dict[str, Any]:
        return {
            definition.name: definition
            for definition in ToolRegistry.list_system_tool_definitions(locale=locale)
            if getattr(definition, "name", None)
        }

    @staticmethod
    def _resolve_tool_contract_adapter(source_tool_name: str | None) -> _OpenClawToolContractAdapter | None:
        return resolve_tool_contract_adapter(source_tool_name)

    @staticmethod
    def _is_retired_source_tool_name(source_tool_name: str | None) -> bool:
        normalized = _normalize_optional_text(source_tool_name)
        return bool(normalized and normalized.lower() in OPENCLAW_RETIRED_SOURCE_TOOL_NAMES)

    def _migrate_legacy_source_tool_bindings(self) -> bool:
        legacy_names = tuple(OPENCLAW_SOURCE_TOOL_ALIAS_MAP.keys())
        if not legacy_names:
            return False

        changed = False
        items = (
            self.db.query(OpenClawCapabilityItem)
            .filter(
                OpenClawCapabilityItem.source_type == "tool",
                OpenClawCapabilityItem.source_tool_name.in_(legacy_names),
            )
            .all()
        )
        for item in items:
            canonical_name = _canonicalize_source_tool_name(item.source_tool_name)
            if canonical_name and canonical_name != item.source_tool_name:
                item.source_tool_name = canonical_name
                changed = True
        return changed

    def _retired_source_reason(self, source_tool_name: str | None, *, locale: str) -> str:
        normalized = _normalize_optional_text(source_tool_name)
        lowered = normalized.lower() if normalized else ""
        if lowered in OPENCLAW_CAPTURE_RETIRED_SOURCE_TOOL_NAMES:
            return _localized_message(
                locale,
                zh="这个字段级创建记录来源已从 OpenClaw 官方能力目录中退役。请重新绑定到受支持的只读来源。",
                en="This field-level entry creation source has been retired from the official OpenClaw capability catalog. Rebind this item to a supported read-only source.",
            )
        if lowered in OPENCLAW_RELATION_RETIRED_SOURCE_TOOL_NAMES:
            return _localized_message(
                locale,
                zh="创建关联不再是 OpenClaw Agent 能力。请重新绑定到受支持的只读来源。",
                en="Creating relations is no longer an OpenClaw Agent capability. Rebind this item to a supported read-only source.",
            )
        if lowered in OPENCLAW_PERIODIC_REVIEW_RETIRED_SOURCE_TOOL_NAMES:
            return _localized_message(
                locale,
                zh="旧的周报/月报来源已从 OpenClaw 官方能力目录中移除。请改用“时间范围回顾（generate_periodic_review）”系统能力，或重新绑定到其他来源。",
                en="The legacy weekly/monthly report sources have been removed from the official OpenClaw capability catalog. Use the periodic review system capability instead, or rebind this item to another source.",
            )
        return _localized_message(
            locale,
            zh="这个来源已从 OpenClaw 官方能力目录中退役。请重新绑定到其他来源。",
            en="This source has been retired from the official OpenClaw capability catalog. Rebind this item to another source.",
        )

    @staticmethod
    def _unsupported_system_tool_source_reason(*, locale: str) -> str:
        return _localized_message(
            locale,
            zh="该系统工具不受 OpenClaw 支持；只允许绑定显式适配的只读系统工具。",
            en="This system tool is not supported by OpenClaw; only explicitly adapted read-only system tools may be bound.",
        )

    def _retired_catalog_item_state(
        self,
        item: OpenClawCapabilityItem,
        *,
        locale: str,
    ) -> tuple[bool, str | None]:
        if item.source_type != "tool":
            return False, None
        if self._is_retired_source_tool_name(item.source_tool_name):
            return True, self._retired_source_reason(item.source_tool_name, locale=locale)
        resolved = self._resolve_tool_source(
            tool_id=item.tool_id,
            source_tool_name=item.source_tool_name,
            locale=locale,
        )
        if (
            resolved is not None
            and resolved.is_system
            and self._resolve_tool_contract_adapter(resolved.source_tool_name) is None
        ):
            return True, self._unsupported_system_tool_source_reason(locale=locale)
        return False, None

    def _resolve_tool_source(
        self,
        *,
        tool_id: UUID | None,
        source_tool_name: str | None,
        locale: str | None = None,
    ) -> _ResolvedToolSource | None:
        if tool_id is not None:
            tool_model = (
                self.db.query(AssistantTool)
                .filter(AssistantTool.id == tool_id)
                .first()
            )
            if tool_model is not None:
                runtime_tool = self.tool_registry.resolve(tool_model.name) if tool_model.enabled else None
                return _ResolvedToolSource(
                    source_tool_name=tool_model.name,
                    source_name=tool_model.name,
                    source_description=tool_model.description or "",
                    is_system=bool(tool_model.is_system),
                    enabled=bool(tool_model.enabled),
                    tool_model=tool_model,
                    tool_runtime=runtime_tool,
                )

        resolved_source_tool_name = _canonicalize_source_tool_name(source_tool_name)
        if resolved_source_tool_name:
            system_map = self._system_tool_definition_map(locale=locale)
            if resolved_source_tool_name in system_map:
                definition = system_map[resolved_source_tool_name]
                runtime_tool = self.tool_registry.resolve(resolved_source_tool_name)
                return _ResolvedToolSource(
                    source_tool_name=definition.name,
                    source_name=definition.display_name,
                    source_description=definition.display_description or definition.description,
                    is_system=True,
                    enabled=runtime_tool is not None,
                    tool_model=None,
                    tool_runtime=runtime_tool,
                )

            tool_model = (
                self.db.query(AssistantTool)
                .filter(AssistantTool.name == resolved_source_tool_name)
                .first()
            )
            if tool_model is not None:
                runtime_tool = self.tool_registry.resolve(tool_model.name) if tool_model.enabled else None
                return _ResolvedToolSource(
                    source_tool_name=tool_model.name,
                    source_name=tool_model.name,
                    source_description=tool_model.description or "",
                    is_system=bool(tool_model.is_system),
                    enabled=bool(tool_model.enabled),
                    tool_model=tool_model,
                    tool_runtime=runtime_tool,
                )

        return None

    def _workflow_contract_snapshot(self, workflow: AssistantWorkflow, *, locale: str = "en") -> _WorkflowContractSnapshot:
        workflow_display_name = self._workflow_source_display_name(workflow, locale=locale)
        published_input = self.config_service._get_workflow_published_input(workflow)  # noqa: SLF001
        if published_input is None:
            raise ApiException(
                status_code=422,
                code=OPENCLAW_INVALID_SOURCE_ERROR_CODE,
                message=f"Workflow has no published version: {workflow_display_name}",
            )
        try:
            contract = workflow_contract_from_input(published_input)
        except WorkflowContractError as exc:
            raise ApiException(
                status_code=422,
                code=OPENCLAW_INVALID_SOURCE_ERROR_CODE,
                message=f"{exc.message}: {workflow_display_name}",
            ) from exc
        return _WorkflowContractSnapshot(
            input_schema=contract.input_schema,
            output_schema=contract.output_schema,
            input_summary=_schema_summary(contract.input_schema, locale=locale),
            output_summary=_schema_summary(contract.output_schema, locale=locale),
        )

    def _build_workflow_skill_definition(
        self,
        *,
        workflow: AssistantWorkflow,
        workflow_input: WorkflowInput,
    ) -> SkillDefinition:
        workflow_nodes = [
            WorkflowNodeDefinition(
                node_id=node.node_id,
                node_type=node.node_type,
                label=node.label,
                position_x=node.position_x,
                position_y=node.position_y,
                config=node.config or {},
            )
            for node in workflow_input.nodes
        ]
        workflow_edges: list[WorkflowEdgeDefinition] = []
        for edge in workflow_input.edges:
            condition_expr = None
            if edge.condition_expr is not None:
                condition_expr = ConditionExpression(
                    id=edge.condition_expr.id,
                    variable=edge.condition_expr.variable,
                    operator=edge.condition_expr.operator,
                    value=edge.condition_expr.value,
                    handle=edge.condition_expr.handle,
                )
            workflow_edges.append(
                WorkflowEdgeDefinition(
                    edge_id=edge.edge_id,
                    source_node_id=edge.source_node_id,
                    target_node_id=edge.target_node_id,
                    source_handle=edge.source_handle,
                    target_handle=edge.target_handle,
                    condition_type=edge.condition_type,
                    condition_expr=condition_expr,
                    label=edge.label,
                )
            )
        tool_names = sorted(AssistantConfigService._collect_workflow_tool_names(workflow_nodes))  # noqa: SLF001
        return SkillDefinition(
            name=f"openclaw__{workflow.name}__workflow",
            description=workflow.description or "",
            intent_examples=[],
            tools=tool_names,
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            workflow_id=str(workflow.id),
            workflow_version_id=(
                str(workflow.published_version_id) if workflow.published_version_id is not None else None
            ),
            workflow_nodes=workflow_nodes,
            workflow_edges=workflow_edges,
        )

    def _build_agent_skill_definition(
        self,
        *,
        agent_profile: AssistantAgentProfile,
        draft: AgentPublishDraftInput,
        output_schema: dict[str, Any],
        locale: str,
    ) -> SkillDefinition:
        output_schema_json = json.dumps(output_schema, ensure_ascii=False, sort_keys=True)
        base_prompt = (draft.system_prompt or "").strip()
        contract_prompt = _localized_message(
            locale,
            zh=(
                "你正在以 OpenClaw 能力的形式执行 MindAtlas 智能体。\n"
                "请先遵循下面的基础提示词，然后严格遵守能力契约。\n\n"
                f"{base_prompt}\n\n"
                "额外规则：\n"
                "- 用户输入会以 JSON 对象给出。\n"
                f"- 最终必须返回一个 JSON 对象，并且严格匹配这个输出 schema：{output_schema_json}\n"
                "- 不要输出 Markdown 代码块，不要输出额外解释。"
            ),
            en=(
                "You are executing a MindAtlas agent as an OpenClaw capability.\n"
                "Follow the base prompt first, then obey the capability contract.\n\n"
                f"{base_prompt}\n\n"
                "Additional rules:\n"
                "- The user input will be provided as a JSON object.\n"
                f"- Your final answer must be a JSON object that strictly matches this output schema: {output_schema_json}\n"
                "- Do not wrap the result in Markdown or add extra explanation."
            ),
        )
        normalized_kb = draft.kb_config if isinstance(draft.kb_config, dict) else {"enabled": False}
        return SkillDefinition(
            name=f"openclaw__{agent_profile.name}__agent",
            description=agent_profile.description or "",
            intent_examples=[],
            tools=list(draft.tools or []),
            mode="langgraph",
            langgraph_pattern="agent_loop",
            model_source=draft.model_source,
            model_id=str(draft.model_id) if draft.model_id is not None else None,
            system_prompt=contract_prompt,
            kb=SkillKBConfig(enabled=bool(normalized_kb.get("enabled", False))),
            workflow_nodes=[],
            workflow_edges=[],
        )

    def _build_engine(self, skill: SkillDefinition) -> LangGraphEngine:
        from app.assistant.workflow.engine.engine import LangGraphEngine

        if skill.langgraph_pattern == "agent_loop" and getattr(skill, "model_source", "default") == "custom":
            from app.ai_registry.runtime import resolve_openai_compat_config_by_model_id

            selected_model_id = getattr(skill, "model_id", None)
            cfg = resolve_openai_compat_config_by_model_id(
                self.db,
                model_id=selected_model_id or "",
                model_type="llm",
            )
        else:
            from app.ai_registry.runtime import resolve_openai_compat_config

            cfg = resolve_openai_compat_config(self.db, component="assistant", model_type="llm")

        if cfg is None:
            raise ApiException(
                status_code=409,
                code=40965,
                message="No available model configuration for OpenClaw capability execution",
            )
        return LangGraphEngine(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            model=cfg.model,
            db=self.db,
        )

    def _ensure_system_items(self, *, preferred_locale: str | None = None, commit: bool = True) -> None:
        locale = self._current_locale(preferred_locale)
        payload = self._get_payload()
        legacy_enabled_map = payload.get("capabilities") if isinstance(payload.get("capabilities"), dict) else {}
        migrated = bool(payload.get("catalogMigrated", False))
        item_version = self._system_item_version(payload)
        should_seed_missing = item_version < OPENCLAW_SYSTEM_ITEM_VERSION
        changed = self._migrate_legacy_source_tool_bindings()

        definitions = list_openclaw_system_item_definitions(locale)
        definition_keys = {definition.key for definition in definitions}
        existing_system_items = (
            self.db.query(OpenClawCapabilityItem)
            .filter(
                OpenClawCapabilityItem.system_default_key.isnot(None),
                OpenClawCapabilityItem.is_system_item.is_(True),
            )
            .all()
        )
        existing_by_key = {
            item.system_default_key: item
            for item in existing_system_items
            if item.system_default_key
        }

        for definition in definitions:
            input_schema = definition.input_schema or _EMPTY_OBJECT_SCHEMA
            output_schema = definition.output_schema or _EMPTY_OBJECT_SCHEMA
            input_summary = definition.input_summary or ""
            output_summary = definition.output_summary or ""
            source_tool_name = definition.source_tool_name
            tool_id: UUID | None = None
            workflow_id: UUID | None = None
            agent_profile_id: UUID | None = None

            if definition.source_type == "workflow":
                workflow, snapshot = self._resolve_workflow_system_item_contract(definition, locale=locale)
                workflow_id = workflow.id
                input_schema = snapshot.input_schema
                output_schema = snapshot.output_schema
                input_summary = definition.input_summary or snapshot.input_summary
                output_summary = definition.output_summary or snapshot.output_summary
            else:
                input_schema = _normalize_json_object_schema(input_schema, label="input")
                output_schema = _normalize_json_object_schema(output_schema, label="output")

            item = existing_by_key.get(definition.key)
            enabled_value = bool(definition.enabled_by_default)
            if not migrated and definition.key in legacy_enabled_map:
                enabled_value = bool(legacy_enabled_map[definition.key])

            if item is None:
                if not should_seed_missing:
                    continue
                self.db.add(
                    OpenClawCapabilityItem(
                        capability_key=definition.key,
                        tool_name=definition.tool_name,
                        title=definition.title,
                        description=definition.description,
                        source_type=definition.source_type,
                        system_default_key=definition.key,
                        source_tool_name=source_tool_name,
                        tool_id=tool_id,
                        workflow_id=workflow_id,
                        agent_profile_id=agent_profile_id,
                        enabled=enabled_value,
                        is_system_item=True,
                        input_schema_json=input_schema,
                        output_schema_json=output_schema,
                        input_summary=input_summary,
                        output_summary=output_summary,
                        tool_response_mode="json_schema",
                    )
                )
                changed = True
                continue

            desired_enabled = item.enabled
            if not migrated and definition.key in legacy_enabled_map:
                desired_enabled = bool(legacy_enabled_map[definition.key])

            if not bool(item.is_system_item):
                item.is_system_item = True
                changed = True
            if item.system_default_key != definition.key:
                item.system_default_key = definition.key
                changed = True
            if item.capability_key != definition.key:
                item.capability_key = definition.key
                changed = True
            if item.tool_name != definition.tool_name:
                item.tool_name = definition.tool_name
                changed = True
            if item.title != definition.title:
                item.title = definition.title
                changed = True
            if item.description != definition.description:
                item.description = definition.description
                changed = True
            if item.source_type != definition.source_type:
                item.source_type = definition.source_type
                changed = True
            if item.source_tool_name != source_tool_name:
                item.source_tool_name = source_tool_name
                changed = True
            if item.tool_id != tool_id:
                item.tool_id = tool_id
                changed = True
            if item.workflow_id != workflow_id:
                item.workflow_id = workflow_id
                changed = True
            if item.agent_profile_id != agent_profile_id:
                item.agent_profile_id = agent_profile_id
                changed = True
            if _schema_compact(item.input_schema_json or _EMPTY_OBJECT_SCHEMA) != _schema_compact(input_schema):
                item.input_schema_json = input_schema
                changed = True
            if _schema_compact(item.output_schema_json or _EMPTY_OBJECT_SCHEMA) != _schema_compact(output_schema):
                item.output_schema_json = output_schema
                changed = True
            if item.input_summary != input_summary:
                item.input_summary = input_summary
                changed = True
            if item.output_summary != output_summary:
                item.output_summary = output_summary
                changed = True
            if item.tool_response_mode != "json_schema":
                item.tool_response_mode = "json_schema"
                changed = True
            if item.enabled != desired_enabled:
                item.enabled = desired_enabled
                changed = True

        for item in existing_system_items:
            if item.system_default_key and item.system_default_key not in definition_keys:
                self.db.delete(item)
                changed = True

        if not migrated and legacy_enabled_map:
            payload["catalogMigrated"] = True
            changed = True
        if item_version < OPENCLAW_SYSTEM_ITEM_VERSION:
            payload["systemItemVersion"] = OPENCLAW_SYSTEM_ITEM_VERSION
            changed = True
        if changed:
            self._upsert_payload(payload)

        if changed and commit:
            self.db.commit()

    def _list_catalog_items(self) -> list[OpenClawCapabilityItem]:
        return (
            self.db.query(OpenClawCapabilityItem)
            .options(
                joinedload(OpenClawCapabilityItem.tool),
                joinedload(OpenClawCapabilityItem.workflow),
                joinedload(OpenClawCapabilityItem.agent_profile),
            )
            .order_by(OpenClawCapabilityItem.is_system_item.desc(), OpenClawCapabilityItem.created_at.asc())
            .all()
        )

    def _list_enabled_entry_type_codes(self) -> list[str]:
        rows = (
            self.db.query(EntryType.code)
            .filter(EntryType.enabled.is_(True))
            .order_by(func.lower(EntryType.code))
            .all()
        )
        return [str(row[0]).strip() for row in rows if row and str(row[0]).strip()]

    def _get_catalog_item(self, item_id: UUID) -> OpenClawCapabilityItem:
        item = (
            self.db.query(OpenClawCapabilityItem)
            .options(
                joinedload(OpenClawCapabilityItem.tool),
                joinedload(OpenClawCapabilityItem.workflow),
                joinedload(OpenClawCapabilityItem.agent_profile),
            )
            .filter(OpenClawCapabilityItem.id == item_id)
            .first()
        )
        if item is None:
            raise ApiException(status_code=404, code=40462, message=f"OpenClaw capability item not found: {item_id}")
        return item

    def _get_catalog_item_by_capability_key(self, capability_key: str) -> OpenClawCapabilityItem | None:
        return (
            self.db.query(OpenClawCapabilityItem)
            .options(
                joinedload(OpenClawCapabilityItem.tool),
                joinedload(OpenClawCapabilityItem.workflow),
                joinedload(OpenClawCapabilityItem.agent_profile),
            )
            .filter(OpenClawCapabilityItem.capability_key == capability_key)
            .first()
        )

    def _availability_for_system_item_definition(
        self,
        definition: OpenClawSystemItemDefinition,
        *,
        locale: str,
    ) -> tuple[bool, str | None]:
        if definition.key == "query_knowledge_graph":
            config = resolve_runtime_knowledge_graph_config()
            if not config.enabled:
                return False, _localized_message(
                    locale,
                    zh="LightRAG 当前未启动。",
                    en="LightRAG is not started.",
                )
            if not config.configured:
                return False, _localized_message(
                    locale,
                    zh="LightRAG 配置尚未完整就绪。",
                    en="LightRAG configuration is still incomplete.",
                )
        if definition.key in {"search_entries", "get_entry"}:
            has_entry_type = self.db.query(EntryType.id).filter(EntryType.enabled.is_(True)).first() is not None
            if not has_entry_type:
                return False, _localized_message(
                    locale,
                    zh="系统里还没有可用的记录类型。",
                    en="No enabled entry types are available yet.",
                )
        return True, None

    def _availability_for_item(
        self,
        item: OpenClawCapabilityItem,
        *,
        locale: str,
    ) -> _CatalogItemAvailability:
        retired, retirement_reason = self._retired_catalog_item_state(item, locale=locale)
        if retired:
            return _CatalogItemAvailability(
                available=False,
                reason=retirement_reason,
                source_name=item.source_tool_name,
                source_description=item.description or "",
                source_is_system=True,
                source_enabled=False,
                published_version_id=None,
                implementation_type="entry",
            )

        if item.source_type == "tool":
            resolved = self._resolve_tool_source(
                tool_id=item.tool_id,
                source_tool_name=item.source_tool_name,
                locale=locale,
            )
            if resolved is None:
                return _CatalogItemAvailability(
                    available=False,
                    reason=_localized_message(
                        locale,
                        zh="绑定的 Tool 已不存在。",
                        en="The bound tool no longer exists.",
                    ),
                    source_name=item.source_tool_name,
                    source_description=None,
                    source_is_system=False,
                    source_enabled=None,
                    published_version_id=None,
                    implementation_type="tool",
                )
            if not resolved.enabled or resolved.tool_runtime is None:
                return _CatalogItemAvailability(
                    available=False,
                    reason=_localized_message(
                        locale,
                        zh="绑定的 Tool 当前已禁用。",
                        en="The bound tool is currently disabled.",
                    ),
                    source_name=resolved.source_name,
                    source_description=resolved.source_description,
                    source_is_system=resolved.is_system,
                    source_enabled=False,
                    published_version_id=None,
                    implementation_type="tool",
                )
            system_definition = get_openclaw_system_item_definition_by_source_tool_name(
                resolved.source_tool_name,
                locale=locale,
            )
            if system_definition is not None:
                available, reason = self._availability_for_system_item_definition(system_definition, locale=locale)
                if not available:
                    return _CatalogItemAvailability(
                        available=False,
                        reason=reason,
                        source_name=resolved.source_name,
                        source_description=resolved.source_description,
                        source_is_system=resolved.is_system,
                        source_enabled=True,
                        published_version_id=None,
                        implementation_type=system_definition.implementation_type,
                    )
            return _CatalogItemAvailability(
                available=True,
                reason=None,
                source_name=resolved.source_name,
                source_description=resolved.source_description,
                source_is_system=resolved.is_system,
                source_enabled=True,
                published_version_id=None,
                implementation_type=(
                    system_definition.implementation_type if system_definition is not None else "tool"
                ),
            )

        if item.source_type == "workflow":
            workflow = (
                item.workflow
                or (self.db.query(AssistantWorkflow).filter(AssistantWorkflow.id == item.workflow_id).first() if item.workflow_id else None)
            )
            if workflow is None:
                return _CatalogItemAvailability(
                    available=False,
                    reason=_localized_message(locale, zh="绑定的 Workflow 已不存在。", en="The bound workflow no longer exists."),
                    source_name=None,
                    source_description=None,
                    source_is_system=False,
                    source_enabled=None,
                    published_version_id=None,
                    implementation_type="workflow",
                )
            if not workflow.enabled:
                workflow_name = self._workflow_source_display_name(workflow, locale=locale)
                return _CatalogItemAvailability(
                    available=False,
                    reason=_localized_message(locale, zh="绑定的 Workflow 已禁用。", en="The bound workflow is disabled."),
                    source_name=workflow_name,
                    source_description=workflow.description,
                    source_is_system=bool(workflow.is_system),
                    source_enabled=False,
                    published_version_id=workflow.published_version_id,
                    implementation_type="workflow",
                )
            if item.system_default_key:
                system_definition = get_openclaw_system_item_definition(item.system_default_key, locale=locale)
                if system_definition is not None:
                    available, reason = self._availability_for_system_item_definition(system_definition, locale=locale)
                    if not available:
                        return _CatalogItemAvailability(
                            available=False,
                            reason=reason,
                            source_name=self._workflow_source_display_name(workflow, locale=locale),
                            source_description=workflow.description,
                            source_is_system=True,
                            source_enabled=True,
                            published_version_id=workflow.published_version_id,
                            implementation_type=system_definition.implementation_type,
                        )
            try:
                snapshot = self._workflow_contract_snapshot(workflow)
            except ApiException as exc:
                return _CatalogItemAvailability(
                    available=False,
                    reason=exc.message,
                    source_name=self._workflow_source_display_name(workflow, locale=locale),
                    source_description=workflow.description,
                    source_is_system=bool(workflow.is_system),
                    source_enabled=True,
                    published_version_id=workflow.published_version_id,
                    implementation_type="workflow",
                )
            if _schema_compact(snapshot.input_schema) != _schema_compact(item.input_schema_json or _EMPTY_OBJECT_SCHEMA):
                return _CatalogItemAvailability(
                    available=False,
                    reason=_localized_message(
                        locale,
                        zh="Workflow 的 published 输入契约已发生变化，请重新同步目录项。",
                        en="The workflow published input contract has changed. Please resync the catalog item.",
                    ),
                    source_name=self._workflow_source_display_name(workflow, locale=locale),
                    source_description=workflow.description,
                    source_is_system=bool(workflow.is_system),
                    source_enabled=True,
                    published_version_id=workflow.published_version_id,
                    implementation_type="workflow",
                )
            if _schema_compact(snapshot.output_schema) != _schema_compact(item.output_schema_json or _EMPTY_OBJECT_SCHEMA):
                return _CatalogItemAvailability(
                    available=False,
                    reason=_localized_message(
                        locale,
                        zh="Workflow 的 published 输出契约已发生变化，请重新同步目录项。",
                        en="The workflow published output contract has changed. Please resync the catalog item.",
                    ),
                    source_name=self._workflow_source_display_name(workflow, locale=locale),
                    source_description=workflow.description,
                    source_is_system=bool(workflow.is_system),
                    source_enabled=True,
                    published_version_id=workflow.published_version_id,
                    implementation_type="workflow",
                )
            return _CatalogItemAvailability(
                available=True,
                reason=None,
                source_name=self._workflow_source_display_name(workflow, locale=locale),
                source_description=workflow.description,
                source_is_system=bool(workflow.is_system),
                source_enabled=True,
                published_version_id=workflow.published_version_id,
                implementation_type="workflow",
            )

        agent = (
            item.agent_profile
            or (
                self.db.query(AssistantAgentProfile).filter(AssistantAgentProfile.id == item.agent_profile_id).first()
                if item.agent_profile_id
                else None
            )
        )
        if agent is None:
            return _CatalogItemAvailability(
                available=False,
                reason=_localized_message(locale, zh="绑定的 Agent 已不存在。", en="The bound agent no longer exists."),
                source_name=None,
                source_description=None,
                source_is_system=False,
                source_enabled=None,
                published_version_id=None,
                implementation_type="agent",
            )
        if not agent.enabled:
            return _CatalogItemAvailability(
                available=False,
                reason=_localized_message(locale, zh="绑定的 Agent 已禁用。", en="The bound agent is disabled."),
                source_name=self._agent_source_display_name(agent, locale=locale),
                source_description=agent.description,
                source_is_system=bool(agent.is_system),
                source_enabled=False,
                published_version_id=agent.published_version_id,
                implementation_type="agent",
            )
        if agent.published_version_id is None or self.config_service._get_agent_profile_published_draft(agent) is None:  # noqa: SLF001
            return _CatalogItemAvailability(
                available=False,
                reason=_localized_message(
                    locale,
                    zh="绑定的 Agent 没有可用的 published 版本。",
                    en="The bound agent does not have an available published version.",
                ),
                source_name=self._agent_source_display_name(agent, locale=locale),
                source_description=agent.description,
                source_is_system=bool(agent.is_system),
                source_enabled=True,
                published_version_id=agent.published_version_id,
                implementation_type="agent",
            )
        return _CatalogItemAvailability(
            available=True,
            reason=None,
            source_name=self._agent_source_display_name(agent, locale=locale),
            source_description=agent.description,
            source_is_system=bool(agent.is_system),
            source_enabled=True,
            published_version_id=agent.published_version_id,
            implementation_type="agent",
        )

    def _enrich_runtime_input_schema(
        self,
        item: OpenClawCapabilityItem,
        *,
        input_schema: dict[str, Any],
    ) -> dict[str, Any]:
        enriched = copy.deepcopy(input_schema)
        if item.source_type != "tool":
            return enriched

        properties = enriched.get("properties")
        if not isinstance(properties, dict):
            return enriched

        source_tool_name = _canonicalize_source_tool_name(item.source_tool_name)
        if source_tool_name == "search_entries":
            entry_type_schema = properties.get("entryType")
            if isinstance(entry_type_schema, dict):
                entry_type_codes = self._list_enabled_entry_type_codes()
                if entry_type_codes:
                    entry_type_schema["enum"] = entry_type_codes
        return enriched

    def _serialize_catalog_item(
        self,
        item: OpenClawCapabilityItem,
        *,
        locale: str,
    ) -> OpenClawCapabilityItemResponse:
        availability = self._availability_for_item(item, locale=locale)
        retired, retirement_reason = self._retired_catalog_item_state(item, locale=locale)
        input_schema = self._enrich_runtime_input_schema(
            item,
            input_schema=_normalize_json_object_schema(item.input_schema_json or _EMPTY_OBJECT_SCHEMA, label="input"),
        )
        output_schema = _normalize_json_object_schema(item.output_schema_json or _EMPTY_OBJECT_SCHEMA, label="output")
        return OpenClawCapabilityItemResponse(
            id=item.id,
            capability_key=item.capability_key,
            tool_name=item.tool_name,
            title=item.title,
            description=item.description or "",
            source_type=item.source_type,
            implementation_type=availability.implementation_type,
            system_default_key=item.system_default_key,
            source_tool_name=item.source_tool_name,
            tool_id=item.tool_id,
            workflow_id=item.workflow_id,
            agent_profile_id=item.agent_profile_id,
            source_name=availability.source_name,
            source_description=availability.source_description,
            source_is_system=availability.source_is_system,
            source_enabled=availability.source_enabled,
            published_version_id=availability.published_version_id,
            enabled=bool(item.enabled),
            is_system_item=bool(item.is_system_item),
            retired=retired,
            retirement_reason=retirement_reason,
            available=availability.available,
            availability_reason=availability.reason,
            schema_editable=item.source_type in {"tool", "agent"},
            input_summary=item.input_summary or "",
            output_summary=item.output_summary or "",
            input_schema=input_schema,
            output_schema=output_schema,
            tool_response_mode=item.tool_response_mode or "json_schema",
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def get_settings_response(self, *, preferred_locale: str | None = None) -> OpenClawIntegrationSettingsResponse:
        locale = self._current_locale(preferred_locale)
        sync_warning: str | None = None
        try:
            self._ensure_system_items(preferred_locale=locale, commit=True)
        except ApiException as exc:
            self.db.rollback()
            sync_warning = _localized_message(
                locale,
                zh=f"系统项同步未完成：{exc.message}。当前页面已回退到最近一次可用配置。",
                en=f"System item sync did not complete: {exc.message}. The page has fallen back to the most recent usable configuration.",
            )
            logger.warning("OpenClaw settings sync skipped after API error: %s", exc.message)
        except Exception:
            self.db.rollback()
            sync_warning = _localized_message(
                locale,
                zh="系统项同步未完成。当前页面已回退到最近一次可用配置，请稍后重试或检查后端日志。",
                en="System item sync did not complete. The page has fallen back to the most recent usable configuration. Please retry later or inspect backend logs.",
            )
            logger.exception("OpenClaw settings sync failed unexpectedly")
        payload = self._get_payload()
        secret, secret_hint, rotated_at = self._secret_state(payload)
        items = self._list_catalog_items()
        return OpenClawIntegrationSettingsResponse(
            enabled=bool(payload.get("enabled")),
            secret_configured=secret is not None,
            secret_hint=secret_hint,
            secret_last_rotated_at=rotated_at,
            sync_warning=sync_warning,
            catalog_items=[self._serialize_catalog_item(item, locale=locale) for item in items],
        )

    def update_settings(
        self,
        request: OpenClawIntegrationUpdateRequest,
        *,
        preferred_locale: str | None = None,
    ) -> OpenClawIntegrationSettingsResponse:
        locale = self._current_locale(preferred_locale)
        payload = self._get_payload()
        payload["enabled"] = bool(request.enabled)

        secret, _secret_hint, _rotated_at = self._secret_state(payload)
        if request.enabled and not secret:
            raise ApiException(
                status_code=400,
                code=OPENCLAW_SECRET_REQUIRED_ERROR_CODE,
                message=_localized_message(
                    locale,
                    zh="启用 OpenClaw 集成前请先生成集成密钥。",
                    en="Generate an integration secret before enabling OpenClaw integration.",
                ),
            )

        self._upsert_payload(payload)
        self.db.commit()
        return self.get_settings_response(preferred_locale=locale)

    def rotate_secret(
        self,
        *,
        preferred_locale: str | None = None,
    ) -> OpenClawRotateSecretResponse:
        locale = self._current_locale(preferred_locale)
        payload = self._get_payload()
        secret = secrets.token_urlsafe(32)
        payload["secretEncrypted"] = encrypt_api_key(secret)
        payload["secretHint"] = api_key_hint(secret)
        payload["secretLastRotatedAt"] = utcnow().isoformat()
        payload.setdefault("enabled", False)
        self._upsert_payload(payload)
        self.db.commit()
        return OpenClawRotateSecretResponse(
            secret=secret,
            settings=self.get_settings_response(preferred_locale=locale),
        )

    def _default_source_contract_for_tool(
        self,
        *,
        tool_id: UUID | None,
        source_tool_name: str | None,
        locale: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str, str, OpenClawToolResponseMode]:
        resolved = self._resolve_tool_source(
            tool_id=tool_id,
            source_tool_name=source_tool_name,
            locale=locale,
        )
        if resolved is None:
            raise ApiException(status_code=422, code=OPENCLAW_INVALID_SOURCE_ERROR_CODE, message="Tool source not found")
        if not resolved.enabled:
            raise ApiException(
                status_code=422,
                code=OPENCLAW_INVALID_SOURCE_ERROR_CODE,
                message=f"Tool source is disabled: {resolved.source_name}",
            )

        system_item_definition = get_openclaw_system_item_definition_by_source_tool_name(
            resolved.source_tool_name,
            locale=locale,
        )
        if system_item_definition is not None:
            input_schema = _normalize_json_object_schema(system_item_definition.input_schema, label="input")
            output_schema = _normalize_json_object_schema(system_item_definition.output_schema, label="output")
            return (
                input_schema,
                output_schema,
                system_item_definition.input_summary or _schema_summary(input_schema, locale=locale),
                system_item_definition.output_summary or _schema_summary(output_schema, locale=locale),
                "json_schema",
            )

        system_definition = self._system_tool_definition_map(locale=locale).get(resolved.source_tool_name)
        if system_definition is not None:
            input_schema = system_definition.json_schema or _schema_from_tool_params(
                [
                    {
                        "name": param.name,
                        "param_type": param.param_type,
                        "required": param.required,
                        "description": param.description,
                    }
                    for param in system_definition.input_params
                ]
            )
            output_schema = _schema_from_tool_params(
                [
                    {
                        "name": param.name,
                        "param_type": param.param_type,
                        "required": True,
                        "description": param.description,
                    }
                    for param in system_definition.output_params
                ]
            )
            return (
                _normalize_json_object_schema(input_schema, label="input"),
                _normalize_json_object_schema(output_schema, label="output"),
                _schema_summary(input_schema, locale=locale),
                _schema_summary(output_schema, locale=locale),
                "json_schema",
            )

        input_schema = _schema_from_tool_params(resolved.tool_model.input_params if resolved.tool_model is not None else None)
        output_schema = _text_field_output_schema()
        return (
            _normalize_json_object_schema(input_schema, label="input"),
            output_schema,
            _schema_summary(input_schema, locale=locale),
            "text (string)",
            "text_field",
        )

    def _default_source_contract_for_agent(
        self,
        *,
        locale: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str, str, OpenClawToolResponseMode]:
        input_schema = _normalize_json_object_schema(_default_agent_input_schema(), label="input")
        output_schema = _text_field_output_schema()
        return (
            input_schema,
            output_schema,
            _localized_message(locale, zh="允许任意对象输入", en="Any JSON object input"),
            _localized_message(locale, zh="text（string）", en="text (string)"),
            "text_field",
        )

    def _merge_item_request(
        self,
        item: OpenClawCapabilityItem,
        request: OpenClawCapabilityItemUpdateRequest,
    ) -> OpenClawCapabilityItemCreateRequest:
        fields_set = set(request.model_fields_set)
        source_binding_fields = {"source_type", "source_tool_name", "tool_id", "workflow_id", "agent_profile_id"}
        source_binding_changed = bool(fields_set & source_binding_fields)
        payload = {
            "source_type": request.source_type if "source_type" in fields_set else item.source_type,
            "tool_name": request.tool_name if "tool_name" in fields_set else item.tool_name,
            "title": request.title if "title" in fields_set else item.title,
            "description": request.description if "description" in fields_set else item.description,
            "enabled": request.enabled if "enabled" in fields_set else item.enabled,
            "source_tool_name": request.source_tool_name if "source_tool_name" in fields_set else item.source_tool_name,
            "tool_id": request.tool_id if "tool_id" in fields_set else item.tool_id,
            "workflow_id": request.workflow_id if "workflow_id" in fields_set else item.workflow_id,
            "agent_profile_id": request.agent_profile_id if "agent_profile_id" in fields_set else item.agent_profile_id,
        }
        if "input_summary" in fields_set:
            payload["input_summary"] = request.input_summary
        elif not source_binding_changed:
            payload["input_summary"] = item.input_summary
        if "output_summary" in fields_set:
            payload["output_summary"] = request.output_summary
        elif not source_binding_changed:
            payload["output_summary"] = item.output_summary
        if "input_schema" in fields_set:
            payload["input_schema"] = request.input_schema
        elif not source_binding_changed:
            payload["input_schema"] = item.input_schema_json
        if "output_schema" in fields_set:
            payload["output_schema"] = request.output_schema
        elif not source_binding_changed:
            payload["output_schema"] = item.output_schema_json
        if "tool_response_mode" in fields_set:
            payload["tool_response_mode"] = request.tool_response_mode
        elif not source_binding_changed:
            payload["tool_response_mode"] = item.tool_response_mode
        return OpenClawCapabilityItemCreateRequest.model_validate(payload)

    def _apply_user_item_request(
        self,
        item: OpenClawCapabilityItem | None,
        request: OpenClawCapabilityItemCreateRequest,
        *,
        locale: str,
    ) -> OpenClawCapabilityItem:
        normalized_tool_name = self._normalize_openclaw_tool_name(
            request.tool_name,
            exclude_item_id=item.id if item is not None else None,
        )
        capability_key = (
            item.capability_key
            if item is not None
            else self._next_available_capability_key(normalized_tool_name)
        )

        if request.source_type == "workflow":
            workflow = self.config_service.get_workflow(request.workflow_id)  # type: ignore[arg-type]
            if not workflow.enabled:
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SOURCE_ERROR_CODE,
                    message=f"Workflow is disabled: {self._workflow_source_display_name(workflow, locale=locale)}",
                )
            snapshot = self._workflow_contract_snapshot(workflow)
            input_schema = snapshot.input_schema
            output_schema = snapshot.output_schema
            input_summary = snapshot.input_summary
            output_summary = snapshot.output_summary
            tool_response_mode: OpenClawToolResponseMode = "json_schema"
            source_tool_name = None
            tool_id = None
            workflow_id = workflow.id
            agent_profile_id = None
        elif request.source_type == "agent":
            agent_profile = self.config_service.get_agent_profile(request.agent_profile_id)  # type: ignore[arg-type]
            if not agent_profile.enabled:
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SOURCE_ERROR_CODE,
                    message=f"Agent is disabled: {self._agent_source_display_name(agent_profile, locale=locale)}",
                )
            if agent_profile.published_version_id is None or self.config_service._get_agent_profile_published_draft(agent_profile) is None:  # noqa: SLF001
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SOURCE_ERROR_CODE,
                    message=f"Agent has no published version: {self._agent_source_display_name(agent_profile, locale=locale)}",
                )
            default_input_schema, default_output_schema, default_input_summary, default_output_summary, default_mode = (
                self._default_source_contract_for_agent(locale=locale)
            )
            input_schema = _normalize_json_object_schema(
                request.input_schema if request.input_schema is not None else default_input_schema,
                label="input",
            )
            output_schema = _normalize_json_object_schema(
                request.output_schema if request.output_schema is not None else default_output_schema,
                label="output",
            )
            input_summary = str(request.input_summary or "").strip() or default_input_summary
            output_summary = str(request.output_summary or "").strip() or default_output_summary
            tool_response_mode = request.tool_response_mode or default_mode
            source_tool_name = None
            tool_id = None
            workflow_id = None
            agent_profile_id = agent_profile.id
        else:
            requested_source_tool_name = (
                request.source_tool_name
                if request.source_tool_name is not None
                else (item.source_tool_name if item is not None else None)
            )
            keeps_existing_retired_binding = bool(
                item is not None
                and item.source_type == "tool"
                and self._is_retired_source_tool_name(item.source_tool_name)
                and requested_source_tool_name == item.source_tool_name
            )
            if self._is_retired_source_tool_name(requested_source_tool_name) and not keeps_existing_retired_binding:
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SOURCE_ERROR_CODE,
                    message=self._retired_source_reason(requested_source_tool_name, locale=locale),
                )
            resolved = self._resolve_tool_source(
                tool_id=request.tool_id,
                source_tool_name=request.source_tool_name,
                locale=locale,
            )
            if resolved is None:
                raise ApiException(status_code=422, code=OPENCLAW_INVALID_SOURCE_ERROR_CODE, message="Tool source not found")
            if not resolved.enabled:
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SOURCE_ERROR_CODE,
                    message=f"Tool is disabled: {resolved.source_name}",
                )
            if self._is_retired_source_tool_name(resolved.source_tool_name) and not keeps_existing_retired_binding:
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SOURCE_ERROR_CODE,
                    message=self._retired_source_reason(resolved.source_tool_name, locale=locale),
                )
            if resolved.is_system and self._resolve_tool_contract_adapter(resolved.source_tool_name) is None:
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SOURCE_ERROR_CODE,
                    message=self._unsupported_system_tool_source_reason(locale=locale),
                )
            default_input_schema, default_output_schema, default_input_summary, default_output_summary, default_mode = (
                self._default_source_contract_for_tool(
                    tool_id=request.tool_id,
                    source_tool_name=request.source_tool_name,
                    locale=locale,
                )
            )
            input_schema = _normalize_json_object_schema(
                request.input_schema if request.input_schema is not None else default_input_schema,
                label="input",
            )
            output_schema = _normalize_json_object_schema(
                request.output_schema if request.output_schema is not None else default_output_schema,
                label="output",
            )
            input_summary = str(request.input_summary or "").strip() or default_input_summary
            output_summary = str(request.output_summary or "").strip() or default_output_summary
            tool_response_mode = request.tool_response_mode or default_mode
            source_tool_name = resolved.source_tool_name
            tool_id = resolved.tool_model.id if resolved.tool_model is not None else None
            workflow_id = None
            agent_profile_id = None

        if tool_response_mode == "text_field":
            properties = output_schema.get("properties") if isinstance(output_schema.get("properties"), dict) else {}
            if len(properties) != 1:
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                    message="text_field mode requires exactly one output field",
                )
            field_schema = next(iter(properties.values()))
            if not isinstance(field_schema, dict) or str(field_schema.get("type", "")).strip().lower() != "string":
                raise ApiException(
                    status_code=422,
                    code=OPENCLAW_INVALID_SCHEMA_ERROR_CODE,
                    message="text_field mode requires a single string output field",
                )

        next_item = item or OpenClawCapabilityItem(
            capability_key=capability_key,
            source_type=request.source_type,
            is_system_item=False,
            input_schema_json=input_schema,
            output_schema_json=output_schema,
        )
        next_item.capability_key = capability_key
        next_item.tool_name = normalized_tool_name
        next_item.title = request.title
        next_item.description = request.description or ""
        next_item.source_type = request.source_type
        if not bool(next_item.is_system_item):
            next_item.system_default_key = None
        next_item.source_tool_name = source_tool_name
        next_item.tool_id = tool_id
        next_item.workflow_id = workflow_id
        next_item.agent_profile_id = agent_profile_id
        next_item.enabled = bool(request.enabled)
        next_item.input_schema_json = input_schema
        next_item.output_schema_json = output_schema
        next_item.input_summary = input_summary
        next_item.output_summary = output_summary
        next_item.tool_response_mode = tool_response_mode
        return next_item

    def list_catalog_sources(
        self,
        source_type: OpenClawCatalogSourceType,
        *,
        preferred_locale: str | None = None,
    ) -> OpenClawCatalogSourceListResponse:
        self._ensure_system_items(preferred_locale=preferred_locale, commit=False)
        locale = self._current_locale(preferred_locale)
        items: list[OpenClawCatalogSourceResponse] = []

        if source_type == "tool":
            system_defs = ToolRegistry.list_system_tool_definitions(locale=locale)
            disabled_tool_names = {
                name
                for name, enabled in self.db.query(AssistantTool.name, AssistantTool.enabled)
                .filter(AssistantTool.name.in_(list(assistant_tool_names)))
                .all()
                if name and not enabled
            }
            for definition in system_defs:
                if self._resolve_tool_contract_adapter(definition.name) is None:
                    continue
                if self._is_retired_source_tool_name(definition.name):
                    continue
                is_disabled = definition.name in disabled_tool_names
                system_item_definition = get_openclaw_system_item_definition_by_source_tool_name(definition.name, locale=locale)
                default_input_schema = definition.json_schema or _schema_from_tool_params(
                    [
                        {
                            "name": param.name,
                            "param_type": param.param_type,
                            "required": param.required,
                            "description": param.description,
                        }
                        for param in definition.input_params
                    ]
                )
                default_output_schema = _schema_from_tool_params(
                    [
                        {
                            "name": param.name,
                            "param_type": param.param_type,
                            "required": True,
                            "description": param.description,
                        }
                        for param in definition.output_params
                    ]
                )
                title = definition.display_name
                description = definition.display_description or definition.description
                unavailable_reason = (
                    _localized_message(locale, zh="Tool 已禁用。", en="Tool is disabled.")
                    if is_disabled
                    else None
                )
                bindable = not is_disabled
                default_input_summary = _schema_summary(default_input_schema, locale=locale)
                default_output_summary = _schema_summary(default_output_schema, locale=locale)
                default_tool_response_mode: OpenClawToolResponseMode = "json_schema"

                if system_item_definition is not None:
                    default_input_schema = system_item_definition.input_schema or default_input_schema
                    default_output_schema = system_item_definition.output_schema or default_output_schema
                    default_input_summary = system_item_definition.input_summary or default_input_summary
                    default_output_summary = system_item_definition.output_summary or default_output_summary
                    available, reason = self._availability_for_system_item_definition(system_item_definition, locale=locale)
                    if not is_disabled and not available:
                        bindable = False
                        unavailable_reason = reason

                items.append(
                    OpenClawCatalogSourceResponse(
                        source_type="tool",
                        source_key=f"system:{definition.name}",
                        title=title,
                        description=description,
                        source_name=title,
                        source_description=description,
                        is_system=True,
                        enabled=not is_disabled,
                        bindable=bindable,
                        unavailable_reason=unavailable_reason,
                        schema_mode="editable",
                        source_tool_name=definition.name,
                        default_input_schema=_normalize_json_object_schema(default_input_schema, label="input"),
                        default_output_schema=_normalize_json_object_schema(default_output_schema, label="output"),
                        default_input_summary=default_input_summary,
                        default_output_summary=default_output_summary,
                        default_tool_response_mode=default_tool_response_mode,
                    )
                )

            remote_tools = (
                self.db.query(AssistantTool)
                .filter(AssistantTool.kind == "remote")
                .order_by(AssistantTool.created_at.desc())
                .all()
            )
            for tool in remote_tools:
                default_input_schema = _schema_from_tool_params(tool.input_params)
                default_output_schema = _text_field_output_schema()
                items.append(
                    OpenClawCatalogSourceResponse(
                        source_type="tool",
                        source_key=f"tool:{tool.id}",
                        title=tool.name,
                        description=tool.description or "",
                        source_name=tool.name,
                        source_description=tool.description or "",
                        is_system=False,
                        enabled=bool(tool.enabled),
                        bindable=bool(tool.enabled),
                        unavailable_reason=(
                            None
                            if tool.enabled
                            else _localized_message(locale, zh="Tool 已禁用。", en="Tool is disabled.")
                        ),
                        schema_mode="editable",
                        source_tool_name=tool.name,
                        tool_id=tool.id,
                        default_input_schema=default_input_schema,
                        default_output_schema=default_output_schema,
                        default_input_summary=_schema_summary(default_input_schema, locale=locale),
                        default_output_summary=_localized_message(locale, zh="text（string）", en="text (string)"),
                        default_tool_response_mode="text_field",
                    )
                )

        elif source_type == "workflow":
            workflows = self.config_service.list_workflows(include_disabled=True)
            for workflow in workflows:
                workflow_name = self._workflow_source_display_name(workflow, locale=locale)
                bindable = True
                reason = None
                default_input_schema = None
                default_output_schema = None
                default_input_summary = ""
                default_output_summary = ""
                try:
                    snapshot = self._workflow_contract_snapshot(workflow, locale=locale)
                    default_input_schema = snapshot.input_schema
                    default_output_schema = snapshot.output_schema
                    default_input_summary = snapshot.input_summary
                    default_output_summary = snapshot.output_summary
                except ApiException as exc:
                    bindable = False
                    reason = exc.message
                items.append(
                    OpenClawCatalogSourceResponse(
                        source_type="workflow",
                        source_key=f"workflow:{workflow.id}",
                        title=workflow_name,
                        description=workflow.description or "",
                        source_name=workflow_name,
                        source_description=workflow.description or "",
                        is_system=bool(workflow.is_system),
                        enabled=bool(workflow.enabled),
                        bindable=bindable and bool(workflow.enabled),
                        unavailable_reason=(
                            reason
                            if workflow.enabled
                            else _localized_message(locale, zh="Workflow 已禁用。", en="Workflow is disabled.")
                        ),
                        schema_mode="readonly",
                        workflow_id=workflow.id,
                        published_version_id=workflow.published_version_id,
                        default_input_schema=default_input_schema,
                        default_output_schema=default_output_schema,
                        default_input_summary=default_input_summary,
                        default_output_summary=default_output_summary,
                        default_tool_response_mode="json_schema",
                    )
                )

        else:
            (
                default_agent_input_schema,
                default_agent_output_schema,
                default_agent_input_summary,
                default_agent_output_summary,
                default_agent_mode,
            ) = self._default_source_contract_for_agent(locale=locale)
            agents = self.config_service.list_agent_profiles(include_disabled=True)
            for agent in agents:
                agent_name = self._agent_source_display_name(agent, locale=locale)
                has_published = agent.published_version_id is not None and self.config_service._get_agent_profile_published_draft(agent) is not None  # noqa: SLF001
                bindable = bool(agent.enabled and has_published)
                reason = None
                if not agent.enabled:
                    reason = _localized_message(locale, zh="Agent 已禁用。", en="Agent is disabled.")
                elif not has_published:
                    reason = _localized_message(
                        locale,
                        zh="Agent 没有 published 版本。",
                        en="Agent has no published version.",
                    )
                items.append(
                    OpenClawCatalogSourceResponse(
                        source_type="agent",
                        source_key=f"agent:{agent.id}",
                        title=agent_name,
                        description=agent.description or "",
                        source_name=agent_name,
                        source_description=agent.description or "",
                        is_system=bool(agent.is_system),
                        enabled=bool(agent.enabled),
                        bindable=bindable,
                        unavailable_reason=reason,
                        schema_mode="editable",
                        agent_profile_id=agent.id,
                        published_version_id=agent.published_version_id,
                        default_input_schema=default_agent_input_schema,
                        default_output_schema=default_agent_output_schema,
                        default_input_summary=default_agent_input_summary,
                        default_output_summary=default_agent_output_summary,
                        default_tool_response_mode=default_agent_mode,
                    )
                )

        return OpenClawCatalogSourceListResponse(items=items)

    def create_catalog_item(
        self,
        request: OpenClawCapabilityItemCreateRequest,
        *,
        preferred_locale: str | None = None,
    ) -> OpenClawCapabilityItemResponse:
        self._ensure_system_items(preferred_locale=preferred_locale, commit=False)
        locale = self._current_locale(preferred_locale)
        item = self._apply_user_item_request(None, request, locale=locale)
        self.db.add(item)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40972, message="Create OpenClaw capability item failed") from exc
        return self._serialize_catalog_item(self._get_catalog_item(item.id), locale=locale)

    def update_catalog_item(
        self,
        item_id: UUID,
        request: OpenClawCapabilityItemUpdateRequest,
        *,
        preferred_locale: str | None = None,
    ) -> OpenClawCapabilityItemResponse:
        self._ensure_system_items(preferred_locale=preferred_locale, commit=False)
        locale = self._current_locale(preferred_locale)
        item = self._get_catalog_item(item_id)
        merged = self._merge_item_request(item, request)
        self._apply_user_item_request(item, merged, locale=locale)

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException(status_code=409, code=40973, message="Update OpenClaw capability item failed") from exc
        return self._serialize_catalog_item(self._get_catalog_item(item.id), locale=locale)

    def delete_catalog_item(self, item_id: UUID) -> None:
        item = self._get_catalog_item(item_id)
        self.db.delete(item)
        self.db.commit()

    def reset_system_items(
        self,
        *,
        preferred_locale: str | None = None,
    ) -> OpenClawIntegrationSettingsResponse:
        locale = self._current_locale(preferred_locale)
        definitions = list_openclaw_system_item_definitions(locale)
        definition_keys = {definition.key for definition in definitions}
        existing_system_items = (
            self.db.query(OpenClawCapabilityItem)
            .filter(
                OpenClawCapabilityItem.system_default_key.isnot(None),
                OpenClawCapabilityItem.is_system_item.is_(True),
            )
            .all()
        )
        existing_by_key = {
            item.system_default_key: item
            for item in existing_system_items
            if item.system_default_key
        }
        for definition in definitions:
            input_schema = definition.input_schema or _EMPTY_OBJECT_SCHEMA
            output_schema = definition.output_schema or _EMPTY_OBJECT_SCHEMA
            input_summary = definition.input_summary or ""
            output_summary = definition.output_summary or ""
            source_tool_name = definition.source_tool_name
            tool_id: UUID | None = None
            workflow_id: UUID | None = None
            agent_profile_id: UUID | None = None

            if definition.source_type == "workflow":
                workflow, snapshot = self._resolve_workflow_system_item_contract(definition, locale=locale)
                workflow_id = workflow.id
                input_schema = snapshot.input_schema
                output_schema = snapshot.output_schema
                input_summary = definition.input_summary or snapshot.input_summary
                output_summary = definition.output_summary or snapshot.output_summary
            else:
                input_schema = _normalize_json_object_schema(input_schema, label="input")
                output_schema = _normalize_json_object_schema(output_schema, label="output")

            item = existing_by_key.get(definition.key)
            if item is None:
                item = OpenClawCapabilityItem(
                    capability_key=definition.key,
                    system_default_key=definition.key,
                    source_type=definition.source_type,
                    is_system_item=True,
                    enabled=bool(definition.enabled_by_default),
                    source_tool_name=source_tool_name,
                    tool_id=tool_id,
                    workflow_id=workflow_id,
                    agent_profile_id=agent_profile_id,
                    input_schema_json=input_schema,
                    output_schema_json=output_schema,
                    input_summary=input_summary,
                    output_summary=output_summary,
                    tool_response_mode="json_schema",
                    title=definition.title,
                    description=definition.description,
                    tool_name=definition.tool_name,
                )
                self.db.add(item)
                continue
            item.capability_key = definition.key
            item.tool_name = definition.tool_name
            item.title = definition.title
            item.description = definition.description
            item.source_type = definition.source_type
            item.system_default_key = definition.key
            item.source_tool_name = source_tool_name
            item.tool_id = tool_id
            item.workflow_id = workflow_id
            item.agent_profile_id = agent_profile_id
            item.enabled = bool(definition.enabled_by_default)
            item.is_system_item = True
            item.input_schema_json = input_schema
            item.output_schema_json = output_schema
            item.input_summary = input_summary
            item.output_summary = output_summary
            item.tool_response_mode = "json_schema"
        for item in existing_system_items:
            if item.system_default_key and item.system_default_key not in definition_keys:
                self.db.delete(item)
        payload = self._get_payload()
        payload["catalogMigrated"] = True
        payload["systemItemVersion"] = OPENCLAW_SYSTEM_ITEM_VERSION
        self._upsert_payload(payload)
        self.db.commit()
        return self.get_settings_response(preferred_locale=locale)

    def reset_system_presets(
        self,
        *,
        preferred_locale: str | None = None,
    ) -> OpenClawIntegrationSettingsResponse:
        return self.reset_system_items(preferred_locale=preferred_locale)

    def authorize_runtime_headers(
        self,
        *,
        authorization_header: str | None,
        preferred_locale: str | None = None,
        source_header: str | None = None,
        channel_header: str | None = None,
        session_header: str | None = None,
        tool_header: str | None = None,
    ) -> OpenClawRuntimeAuditContext:
        locale = self._current_locale(preferred_locale)
        payload = self._get_payload()
        if not bool(payload.get("enabled")):
            raise ApiException(
                status_code=403,
                code=OPENCLAW_DISABLED_ERROR_CODE,
                message=_localized_message(
                    locale,
                    zh="OpenClaw 集成尚未启用。",
                    en="OpenClaw integration is not enabled.",
                ),
            )

        expected_secret, _secret_hint, _rotated_at = self._secret_state(payload)
        if not expected_secret:
            raise ApiException(
                status_code=401,
                code=OPENCLAW_AUTH_ERROR_CODE,
                message=_localized_message(
                    locale,
                    zh="OpenClaw 集成密钥尚未配置。",
                    en="OpenClaw integration secret is not configured.",
                ),
            )

        authorization = authorization_header or ""
        scheme, _, presented_secret = authorization.partition(" ")
        if scheme.lower() != "bearer" or not presented_secret.strip():
            raise ApiException(
                status_code=401,
                code=OPENCLAW_AUTH_ERROR_CODE,
                message=_localized_message(
                    locale,
                    zh="缺少有效的 OpenClaw Bearer 凭证。",
                    en="Missing a valid OpenClaw bearer credential.",
                ),
            )

        if not secrets.compare_digest(presented_secret.strip(), expected_secret):
            raise ApiException(
                status_code=401,
                code=OPENCLAW_AUTH_ERROR_CODE,
                message=_localized_message(
                    locale,
                    zh="OpenClaw 集成密钥无效。",
                    en="Invalid OpenClaw integration secret.",
                ),
            )

        return OpenClawRuntimeAuditContext(
            source=_normalize_optional_text(source_header),
            channel=_normalize_optional_text(channel_header),
            session=_normalize_optional_text(session_header),
            tool=_normalize_optional_text(tool_header),
        )

    def authorize_runtime_request(
        self,
        request: Request,
    ) -> OpenClawRuntimeAuditContext:
        return self.authorize_runtime_headers(
            authorization_header=request.headers.get("authorization"),
            preferred_locale=request.headers.get("x-mindatlas-locale"),
            source_header=request.headers.get("x-openclaw-source"),
            channel_header=request.headers.get("x-openclaw-channel"),
            session_header=request.headers.get("x-openclaw-session"),
            tool_header=request.headers.get("x-openclaw-tool"),
        )

    def get_runtime_catalog(
        self,
        *,
        preferred_locale: str | None = None,
    ) -> OpenClawCapabilityCatalogResponse:
        self._ensure_system_items(preferred_locale=preferred_locale, commit=False)
        locale = self._current_locale(preferred_locale)
        capabilities = []
        for item in self._list_catalog_items():
            if not item.enabled:
                continue
            serialized = self._serialize_catalog_item(item, locale=locale)
            if serialized.retired:
                continue
            capabilities.append(
                OpenClawRuntimeCapabilityResponse(
                    capability_key=serialized.capability_key,
                    tool_name=serialized.tool_name,
                    title=serialized.title,
                    description=serialized.description,
                    source_type=serialized.source_type,
                    implementation_type=serialized.implementation_type,
                    available=serialized.available,
                    availability_reason=serialized.availability_reason,
                    input_summary=serialized.input_summary,
                    output_summary=serialized.output_summary,
                    input_schema=serialized.input_schema,
                    output_schema=serialized.output_schema,
                    tool_response_mode=serialized.tool_response_mode,
                )
            )
        return OpenClawCapabilityCatalogResponse(
            integration_name="MindAtlas",
            capabilities=capabilities,
        )

    def _ensure_capability_exposed(
        self,
        *,
        capability_key: str,
        locale: str,
    ) -> OpenClawCapabilityItem:
        item = self._get_catalog_item_by_capability_key(capability_key)
        if item is None:
            raise ApiException(
                status_code=404,
                code=OPENCLAW_CAPABILITY_NOT_FOUND_ERROR_CODE,
                message=f"Unknown OpenClaw capability: {capability_key}",
            )
        retired, retirement_reason = self._retired_catalog_item_state(item, locale=locale)
        if retired:
            raise ApiException(
                status_code=403,
                code=OPENCLAW_CAPABILITY_DISABLED_ERROR_CODE,
                message=retirement_reason or _localized_message(
                    locale,
                    zh=f"能力已从 OpenClaw 目录退役：{capability_key}",
                    en=f"Capability has been retired from the OpenClaw catalog: {capability_key}",
                ),
            )
        if not item.enabled:
            raise ApiException(
                status_code=403,
                code=OPENCLAW_CAPABILITY_DISABLED_ERROR_CODE,
                message=_localized_message(
                    locale,
                    zh=f"能力未对 OpenClaw 暴露：{capability_key}",
                    en=f"Capability is not exposed to OpenClaw: {capability_key}",
                ),
            )
        return item

    def execute_capability_in_worker(
        self,
        *,
        capability_key: str,
        raw_payload: dict[str, Any],
        audit_context: OpenClawRuntimeAuditContext,
        preferred_locale: str | None = None,
        auth_proof: Any,
        cancellation: Any | None = None,
        request_id: str | None = None,
    ) -> OpenClawCapabilityExecuteResponse:
        """Sync execute path used inside the bounded worker Session (shared-only)."""
        self._ensure_system_items(preferred_locale=preferred_locale, commit=False)
        locale = self._current_locale(preferred_locale)
        item = self._ensure_capability_exposed(capability_key=capability_key, locale=locale)
        availability = self._availability_for_item(item, locale=locale)
        if not availability.available:
            raise ApiException(
                status_code=409,
                code=40961,
                message=availability.reason or "Capability is currently unavailable",
            )

        rid = request_id or get_request_id()
        start = time.perf_counter()
        status = "success"
        invocation_started = False
        try:
            invocation_started = True
            return execute_shared_capability(
                self,
                item=item,
                raw_payload=raw_payload or {},
                audit_context=audit_context,
                locale=locale,
                auth_proof=auth_proof,
                cancellation=cancellation,
            )
        except Exception:
            status = "failed"
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            logger.info(
                "openclaw_capability_execution request_id=%s capability=%s tool=%s source=%s channel=%s session=%s status=%s mode=shared invocation_started=%s duration_ms=%.2f",
                rid,
                item.capability_key,
                item.tool_name,
                audit_context.source,
                audit_context.channel,
                audit_context.session,
                status,
                invocation_started,
                duration_ms,
            )

    async def execute_capability(
        self,
        *,
        capability_key: str,
        raw_payload: dict[str, Any],
        audit_context: OpenClawRuntimeAuditContext,
        preferred_locale: str | None = None,
        auth_proof: Any | None = None,
    ) -> OpenClawCapabilityExecuteResponse:
        """Compatibility entry used by tests that still call the service directly.

        Production HTTP execute uses ``runtime_worker.execute_openclaw_capability_in_worker``.
        """
        from app.openclaw_integration.capability_adapter import OpenClawAuthenticationProof

        proof = auth_proof or OpenClawAuthenticationProof(principal_id="openclaw")
        return self.execute_capability_in_worker(
            capability_key=capability_key,
            raw_payload=raw_payload or {},
            audit_context=audit_context,
            preferred_locale=preferred_locale,
            auth_proof=proof,
        )

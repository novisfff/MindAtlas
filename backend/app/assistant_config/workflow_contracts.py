from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.assistant_config.schemas import WorkflowInput


@dataclass(frozen=True)
class WorkflowContractField:
    name: str
    param_type: str
    required: bool
    description: str | None = None
    nullable: bool = False
    items_type: str | None = None
    enum: list[str] | None = None

    def to_param_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "param_type": self.param_type,
            "required": bool(self.required),
            "description": self.description,
            "nullable": bool(self.nullable),
            "items_type": self.items_type,
            "enum": list(self.enum or []),
        }
        return payload


@dataclass(frozen=True)
class WorkflowContractSnapshot:
    input_fields: list[WorkflowContractField]
    output_fields: list[WorkflowContractField]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class WorkflowContractError(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def schema_type_from_param_type(param_type: str | None) -> str:
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


def schema_compact(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def schema_from_field_definitions(
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
        field_type = schema_type_from_param_type(raw_field.get("type"))
        field_schema: dict[str, Any] = {"type": field_type}
        description = normalize_optional_text(raw_field.get("description"))
        if description:
            field_schema["description"] = description
        if field_type == "array":
            items_type = schema_type_from_param_type(raw_field.get("items_type", raw_field.get("itemsType")))
            field_schema["items"] = {"type": items_type or "string"}
        if allow_nullable and bool(raw_field.get("nullable", False)):
            field_schema["nullable"] = True
        if isinstance(raw_field.get("enum"), list) and raw_field["enum"]:
            field_schema["enum"] = [str(item) for item in raw_field["enum"]]
        properties[name] = field_schema
        if bool(raw_field.get(required_key, False)):
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def schema_summary(schema: dict[str, Any], *, locale: str = "en") -> str:
    if not isinstance(schema, dict):
        return ""
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return "无结构化字段" if locale == "zh" else "No structured fields"
    parts: list[str] = []
    for name, raw_field in properties.items():
        if not isinstance(raw_field, dict):
            continue
        field_type = str(raw_field.get("type", "string") or "string").strip().lower()
        if field_type == "array":
            items = raw_field.get("items") if isinstance(raw_field.get("items"), dict) else {}
            item_type = str(items.get("type", "string") or "string").strip().lower()
            field_type = f"array[{item_type}]"
        parts.append(f"{name} ({field_type})")
    return ", ".join(parts)


def field_specs_to_params(fields: list[WorkflowContractField]) -> list[dict[str, Any]]:
    return [field.to_param_dict() for field in fields]


def workflow_contract_from_input(workflow_input: WorkflowInput) -> WorkflowContractSnapshot:
    start_node_cfg: dict[str, Any] | None = None
    for node in workflow_input.nodes:
        if node.node_type != "start":
            continue
        start_node_cfg = node.config if isinstance(node.config, dict) else {}
        break

    if start_node_cfg is None:
        raise WorkflowContractError("missing_start", "Workflow has no start node")

    raw_input_mode = str(start_node_cfg.get("input_mode", start_node_cfg.get("inputMode", "text")) or "text").strip().lower()
    if raw_input_mode != "structured":
        raise WorkflowContractError("structured_start_required", "Workflow does not use structured start input")

    start_fields_raw = start_node_cfg.get("structured_fields", start_node_cfg.get("structuredFields"))
    if not isinstance(start_fields_raw, list) or not start_fields_raw:
        raise WorkflowContractError("missing_structured_start_fields", "Workflow structured start input has no fields")

    input_fields: list[WorkflowContractField] = []
    for raw_field in start_fields_raw:
        if not isinstance(raw_field, dict):
            continue
        name = str(raw_field.get("name", "") or "").strip()
        if not name:
            continue
        input_fields.append(
            WorkflowContractField(
                name=name,
                param_type=schema_type_from_param_type(raw_field.get("type")),
                required=bool(raw_field.get("required", False)),
                description=normalize_optional_text(raw_field.get("description")),
                items_type=schema_type_from_param_type(raw_field.get("items_type", raw_field.get("itemsType")))
                if schema_type_from_param_type(raw_field.get("type")) == "array"
                else None,
                enum=[str(item) for item in raw_field.get("enum", [])] if isinstance(raw_field.get("enum"), list) else None,
            )
        )

    input_schema = schema_from_field_definitions(start_fields_raw, required_key="required")

    structured_outputs: list[tuple[list[WorkflowContractField], dict[str, Any]]] = []
    for node in workflow_input.nodes:
        if node.node_type != "output":
            continue
        config = node.config if isinstance(node.config, dict) else {}
        raw_mode = str(config.get("output_mode", config.get("outputMode", "text")) or "text").strip().lower()
        output_mode = "structured" if raw_mode in {"structured", "json"} else "text"
        if output_mode != "structured":
            continue
        raw_fields = config.get("output_fields", config.get("outputFields"))
        if not isinstance(raw_fields, list) or not raw_fields:
            continue
        output_fields: list[WorkflowContractField] = []
        for raw_field in raw_fields:
            if not isinstance(raw_field, dict):
                continue
            name = str(raw_field.get("name", "") or "").strip()
            if not name:
                continue
            field_type = schema_type_from_param_type(raw_field.get("type"))
            output_fields.append(
                WorkflowContractField(
                    name=name,
                    param_type=field_type,
                    required=not bool(raw_field.get("nullable", False)),
                    description=normalize_optional_text(raw_field.get("description")),
                    nullable=bool(raw_field.get("nullable", False)),
                    items_type=schema_type_from_param_type(raw_field.get("items_type", raw_field.get("itemsType")))
                    if field_type == "array"
                    else None,
                    enum=[str(item) for item in raw_field.get("enum", [])] if isinstance(raw_field.get("enum"), list) else None,
                )
            )
        structured_outputs.append(
            (
                output_fields,
                schema_from_field_definitions(raw_fields, allow_nullable=True),
            )
        )

    if not structured_outputs:
        raise WorkflowContractError("missing_structured_output", "Workflow does not expose a structured output contract")

    unique_output_schemas: dict[str, tuple[list[WorkflowContractField], dict[str, Any]]] = {}
    for output_fields, output_schema in structured_outputs:
        unique_output_schemas[schema_compact(output_schema)] = (output_fields, output_schema)

    if len(unique_output_schemas) > 1:
        raise WorkflowContractError("ambiguous_structured_output", "Workflow exposes ambiguous structured output contracts")

    output_fields, output_schema = next(iter(unique_output_schemas.values()))
    return WorkflowContractSnapshot(
        input_fields=input_fields,
        output_fields=output_fields,
        input_schema=input_schema,
        output_schema=output_schema,
    )

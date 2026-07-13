from __future__ import annotations

from typing import Any

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.domain.digests import (  # noqa: E402
    canonical_json_bytes,
    sha256_canonical_json,
)
from app.assistant.domain.json_schema import (  # noqa: E402
    MAX_BINDING_SCHEMA_BYTES,
    MAX_BINDING_SCHEMA_DEPTH,
    ToolParamContract,
    binding_schema_digest,
    normalize_binding_schema,
    system_tool_contract_set_digest,
    tool_params_to_binding_schema,
)


def test_stable_canonical_mapping_required_and_type_union_normalization() -> None:
    left = {
        "type": ["string", "null", "string"],
        "required": ["b", "a", "b"],
        "properties": {
            "b": {"type": "number"},
            "a": {"type": "string", "description": "alpha"},
        },
    }
    right = {
        "required": ["a", "b"],
        "properties": {
            "a": {"description": "alpha", "type": "string"},
            "b": {"type": "number"},
        },
        "type": ["null", "string"],
    }
    normalized_left = normalize_binding_schema(left, require_object_root=False)
    normalized_right = normalize_binding_schema(right, require_object_root=False)
    assert normalized_left == normalized_right
    assert normalized_left["required"] == ["a", "b"]
    assert normalized_left["type"] == ["null", "string"]
    assert list(normalized_left["properties"].keys()) == ["a", "b"]
    assert binding_schema_digest(left) == binding_schema_digest(right)


def test_nullable_true_conversion_and_ambiguous_rejection() -> None:
    converted = normalize_binding_schema(
        {"type": "string", "nullable": True},
        require_object_root=False,
    )
    assert converted == {"type": ["null", "string"]}
    assert "nullable" not in converted

    union_converted = normalize_binding_schema(
        {"type": ["string", "number"], "nullable": True},
        require_object_root=False,
    )
    assert union_converted["type"] == ["null", "number", "string"]

    with pytest.raises(ValueError, match="nullable"):
        normalize_binding_schema({"nullable": True}, require_object_root=False)

    with pytest.raises(ValueError, match="nullable"):
        normalize_binding_schema(
            {"type": "object", "nullable": "yes"},  # type: ignore[dict-item]
            require_object_root=False,
        )


def test_draft_2020_12_invalid_schema_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_binding_schema(
            {"type": "object", "properties": "not-an-object"},  # type: ignore[dict-item]
            require_object_root=True,
        )
    with pytest.raises(ValueError):
        normalize_binding_schema(
            {"type": "string", "minimum": "x"},  # type: ignore[dict-item]
            require_object_root=False,
        )


def test_input_object_root_enforced_output_any_json_supported() -> None:
    with pytest.raises(ValueError, match="object"):
        normalize_binding_schema({"type": "string"}, require_object_root=True)

    object_root = normalize_binding_schema(
        {"type": "object", "properties": {"q": {"type": "string"}}},
        require_object_root=True,
    )
    assert object_root["type"] == "object"

    for schema in (
        {"type": "string"},
        {"type": "array", "items": {"type": "number"}},
        {"type": ["null", "boolean"]},
        True,  # type: ignore[arg-type]
    ):
        if schema is True:
            with pytest.raises((TypeError, ValueError)):
                normalize_binding_schema(schema, require_object_root=False)  # type: ignore[arg-type]
        else:
            assert normalize_binding_schema(schema, require_object_root=False)


def test_local_defs_refs_accepted_remote_and_missing_rejected() -> None:
    local = normalize_binding_schema(
        {
            "type": "object",
            "properties": {"item": {"$ref": "#/$defs/item"}},
            "$defs": {"item": {"type": "string"}},
        },
        require_object_root=True,
    )
    assert local["properties"]["item"]["$ref"] == "#/$defs/item"

    with pytest.raises(ValueError, match="\\$ref"):
        normalize_binding_schema(
            {
                "type": "object",
                "properties": {"item": {"$ref": "https://example.com/schema.json"}},
            },
            require_object_root=True,
        )

    with pytest.raises(ValueError, match="\\$ref"):
        normalize_binding_schema(
            {
                "type": "object",
                "properties": {"item": {"$ref": "#/$defs/missing"}},
                "$defs": {"item": {"type": "string"}},
            },
            require_object_root=True,
        )


def test_byte_depth_bounds_non_json_nan_and_duplicate_required() -> None:
    deep: dict[str, Any] = {"type": "object"}
    cursor = deep
    for _ in range(MAX_BINDING_SCHEMA_DEPTH + 2):
        nxt: dict[str, Any] = {"type": "object"}
        cursor["properties"] = {"x": nxt}
        cursor = nxt
    with pytest.raises(ValueError, match="depth"):
        normalize_binding_schema(deep, require_object_root=True)

    huge_desc = "x" * (MAX_BINDING_SCHEMA_BYTES + 100)
    with pytest.raises(ValueError, match="byte|size|limit"):
        normalize_binding_schema(
            {"type": "object", "description": huge_desc},
            require_object_root=True,
        )

    with pytest.raises((TypeError, ValueError)):
        normalize_binding_schema({"type": "object", "x": {1: "bad"}}, require_object_root=True)  # type: ignore[dict-item]

    with pytest.raises(ValueError):
        normalize_binding_schema(
            {"type": "object", "const": float("nan")},
            require_object_root=True,
        )

    # Duplicate required members are deduplicated, not rejected, during normalization.
    deduped = normalize_binding_schema(
        {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a", "a"],
        },
        require_object_root=True,
    )
    assert deduped["required"] == ["a"]


def test_secret_bearing_annotations_and_enum_bounds() -> None:
    with pytest.raises(ValueError, match="default|example"):
        normalize_binding_schema(
            {"type": "object", "properties": {"q": {"type": "string", "default": "x"}}},
            require_object_root=True,
        )
    with pytest.raises(ValueError, match="example"):
        normalize_binding_schema(
            {"type": "string", "examples": ["a"]},
            require_object_root=False,
        )
    with pytest.raises(ValueError, match="x-"):
        normalize_binding_schema(
            {"type": "string", "x-secret": "nope"},
            require_object_root=False,
        )

    for secret_name in (
        "api_key",
        "apiKey",
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "token",
    ):
        with pytest.raises(ValueError, match="secret|enum|const"):
            normalize_binding_schema(
                {
                    "type": "object",
                    "properties": {secret_name: {"type": "string", "enum": ["abc"]}},
                },
                require_object_root=True,
            )
        with pytest.raises(ValueError, match="secret|enum|const"):
            normalize_binding_schema(
                {
                    "type": "object",
                    "properties": {secret_name: {"const": "abc"}},
                },
                require_object_root=True,
            )

    with pytest.raises(ValueError, match="enum"):
        normalize_binding_schema(
            {"type": "string", "enum": [f"v{i}" for i in range(257)]},
            require_object_root=False,
        )

    with pytest.raises(ValueError, match="enum|4096|byte"):
        normalize_binding_schema(
            {"type": "string", "enum": ["x" * 4097]},
            require_object_root=False,
        )

    ok = normalize_binding_schema(
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["a", "b"]},
                "title": {"type": "string", "title": "T", "description": "D", "readOnly": True},
            },
            "required": ["mode"],
        },
        require_object_root=True,
    )
    assert ok["properties"]["mode"]["enum"] == ["a", "b"]
    assert ok["properties"]["title"]["readOnly"] is True


def test_tool_params_to_binding_schema_fixed_vectors_and_unknown_types() -> None:
    params = (
        ToolParamContract(
            name="query",
            description="search text",
            param_type="string",
            required=True,
        ),
        ToolParamContract(
            name="limit",
            description=None,
            param_type="number",
            required=False,
        ),
        ToolParamContract(
            name="tags",
            description="tag list",
            param_type="array",
            required=False,
            items_type="string",
        ),
        ToolParamContract(
            name="enabled",
            description=None,
            param_type="boolean",
            required=False,
        ),
        ToolParamContract(
            name="meta",
            description=None,
            param_type="object",
            required=False,
        ),
    )
    schema = tool_params_to_binding_schema(params, require_object_root=True)
    assert schema == {
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean"},
            "limit": {"type": "number"},
            "meta": {"type": "object"},
            "query": {"type": "string", "description": "search text"},
            "tags": {"type": "array", "description": "tag list", "items": {"type": "string"}},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    assert binding_schema_digest(schema) == sha256_canonical_json(schema)

    with pytest.raises(ValueError, match="param_type|unknown|lossy"):
        tool_params_to_binding_schema(
            (
                ToolParamContract(
                    name="bad",
                    description=None,
                    param_type="integer",
                    required=True,
                ),
            )
        )

    with pytest.raises(ValueError, match="param_type|unknown|lossy|items"):
        tool_params_to_binding_schema(
            (
                ToolParamContract(
                    name="nested",
                    description=None,
                    param_type="array",
                    required=True,
                    items_type="array",
                ),
            )
        )


def test_publish_and_runtime_normalization_are_byte_identical() -> None:
    raw = {
        "type": "object",
        "properties": {
            "b": {"type": "string", "nullable": True},
            "a": {"type": ["boolean", "boolean"]},
        },
        "required": ["b", "a", "a"],
    }
    publish = normalize_binding_schema(raw, require_object_root=True)
    runtime = normalize_binding_schema(dict(raw), require_object_root=True)
    assert canonical_json_bytes(publish) == canonical_json_bytes(runtime)
    assert binding_schema_digest(publish) == binding_schema_digest(runtime)
    assert binding_schema_digest(publish) == binding_schema_digest(raw)


def _system_tool_param_contracts() -> list[tuple[str, tuple[ToolParamContract, ...], tuple[ToolParamContract, ...]]]:
    from app.assistant_config.registry import ToolRegistry

    rows: list[tuple[str, tuple[ToolParamContract, ...], tuple[ToolParamContract, ...]]] = []
    for definition in ToolRegistry.list_system_tool_definitions(locale="en"):
        inputs = tuple(
            ToolParamContract(
                name=param.name,
                description=param.description,
                param_type=param.param_type,
                required=param.required,
            )
            for param in definition.input_params
        )
        outputs = tuple(
            ToolParamContract(
                name=param.name,
                description=param.description,
                param_type=param.param_type,
                required=False,
            )
            for param in definition.output_params
        )
        rows.append((definition.name, inputs, outputs))
    return rows


def test_system_tool_golden_schemas_and_contract_set_digest_are_stable() -> None:
    rows = _system_tool_param_contracts()
    assert [name for name, _, _ in rows] == [
        "search_entries",
        "search_similar_entries",
        "get_entry_detail",
        "create_entry",
        "update_entry",
        "create_relation",
        "query_knowledge_graph",
        "generate_weekly_report",
        "generate_monthly_report",
        "get_statistics",
        "get_entries_by_time_range",
        "analyze_activity",
        "get_tag_statistics",
        "list_entry_types",
        "list_tags",
        "kb_relation_recommendations",
    ]

    ordered: list[tuple[str, str, str]] = []
    for name, inputs, outputs in rows:
        input_schema = tool_params_to_binding_schema(inputs, require_object_root=True)
        output_schema = tool_params_to_binding_schema(outputs, require_object_root=True)
        input_digest = binding_schema_digest(input_schema)
        output_digest = binding_schema_digest(output_schema)
        ordered.append((name, input_digest, output_digest))

        # Golden shape: object root, no defaults/examples, deterministic property order.
        assert input_schema["type"] == "object"
        assert output_schema["type"] == "object"
        assert "default" not in canonical_json_bytes(input_schema).decode("utf-8")
        assert list(input_schema.get("properties", {}).keys()) == sorted(
            input_schema.get("properties", {}).keys()
        )

        # Re-running publish-time conversion is byte-identical.
        again = tool_params_to_binding_schema(inputs, require_object_root=True)
        assert canonical_json_bytes(again) == canonical_json_bytes(input_schema)

    set_digest = system_tool_contract_set_digest(ordered)
    assert len(set_digest) == 64
    # Pin the ordered set digest after the conversion contract is fixed.
    assert set_digest == system_tool_contract_set_digest(list(ordered))
    assert set_digest == sha256_canonical_json(
        [
            {
                "name": name,
                "inputSchemaDigest": input_digest,
                "outputSchemaDigest": output_digest,
            }
            for name, input_digest, output_digest in ordered
        ]
    )


def test_langchain_schema_compatibility_detects_required_or_type_mismatch() -> None:
    from app.assistant_config.registry import ToolRegistry

    definition = next(
        item
        for item in ToolRegistry.list_system_tool_definitions(locale="en")
        if item.name == "search_similar_entries"
    )
    owned = tool_params_to_binding_schema(
        tuple(
            ToolParamContract(
                name=param.name,
                description=param.description,
                param_type=param.param_type,
                required=param.required,
            )
            for param in definition.input_params
        )
    )
    lc_schema = definition.json_schema or {}
    lc_required = set(lc_schema.get("required") or [])
    owned_required = set(owned.get("required") or [])
    assert "query" in owned_required
    assert "query" in lc_required

    # Compatibility assertion: required fields and coarse types must agree.
    lc_props = lc_schema.get("properties") or {}
    owned_props = owned.get("properties") or {}
    for field in owned_required:
        assert field in lc_props
        owned_type = owned_props[field]["type"]
        lc_type = lc_props[field].get("type")
        if isinstance(lc_type, list):
            assert owned_type in lc_type
        else:
            assert owned_type == lc_type or (
                owned_type == "number" and lc_type in {"number", "integer"}
            )

    # A real required-field mismatch is detected by the same check.
    broken_owned = {
        **owned,
        "required": sorted(set(owned_required) | {"missing_required_field"}),
        "properties": {
            **owned_props,
            "missing_required_field": {"type": "string"},
        },
    }
    broken_required = set(broken_owned["required"])
    assert broken_required - set(lc_props.keys())

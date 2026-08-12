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


# Checked-in golden digests for every system tool's project-owned param conversion.
# Bare registry arrays currently map to items:{} (no items_type in registry grammar).
SYSTEM_TOOL_GOLDEN_DIGESTS: dict[str, tuple[str, str]] = {
    "search_entries": (
        "85893d44714794565728ecd5ed3553f9f2a18d566b903b7f22a50e01f0e23a17",
        "cd4fe913641c2a8b46c4c19b0f32e6db828decb1770fd679223a0faf855693f4",
    ),
    "search_similar_entries": (
        "0570532888bdf14818f29b6d806d88033c376249b071e22f17ca2d99f2fd5f58",
        "aeb6462823a38e601f4aa9f0005a0bb139e7c35ca971d6a294dba1a79a128aa3",
    ),
    "get_entry_detail": (
        "af89ea1dc96e41c7fdf916768c2ca46ca14b580ef53fff1040098c0bddde89c7",
        "0d20ad4d75d04f1fe9ffd366b89d77f0bce8de00088bc714bb44a8392b7e7f5d",
    ),
    "create_entry": (
        "b77249cf8ba259c0b5482a4886f64c3877e306e0f97663e572ebb7d07e566091",
        "e468374fea087a54ad782602c8cf15e387e1d06977198fc0c3d870349249adba",
    ),
    "query_knowledge_graph": (
        "e5e366e05faddde4c4263b8c738ce7ad86408f0018bf41da2ced53b37399059e",
        "bf89bb7480489f550e86ee7a3cbb446c53fc1ff3a4eb1a274cff757ace9efaab",
    ),
    "get_statistics": (
        "e23292601e9b7bc5964b6fb0a10871d437f22b8f4f344600ebfbe4ed3397e0dd",
        "5b2980329943609c470e2c3b0ea24c6a8023a28d55f7684da03b0398a6c78af8",
    ),
    "get_entries_by_time_range": (
        "1c8e61f1dd484fcc322daf51376037f5e0b0125d12470c394b2ea773156aa56e",
        "ec3b2e203ac6701a30d623afe86557a63c3e383f7b90af0570c994dc3939670f",
    ),
    "analyze_activity": (
        "0ea9099f90b11dc418164b39cbe3c322d990af52f18918795df50801fb64d72e",
        "3c5f9062d724572f5c8835f8cdb65956037dc78a2cdfc0d93095c6031a346d3c",
    ),
    "get_tag_statistics": (
        "af5258f38ff5f7865cbbd47a69205b7fb58d944c5d564b6e9952fa884f358ce7",
        "c2e053fe69a306605f05052f90a53d6d3b88fb41609b43a63b857e56ef6328fb",
    ),
    "list_entry_types": (
        "99334726611ccf58a148b0814696bfa6fe08c1b2d027e946beccf5a74331c9aa",
        "812e3362f15aac9078bbc23563f087f3da3dd528877853c152881b82e2db88df",
    ),
    "list_tags": (
        "99334726611ccf58a148b0814696bfa6fe08c1b2d027e946beccf5a74331c9aa",
        "67820ecd5b4f768386ea77e7742d0bda76fb3e025eaeb30592bbf099c093ee57",
    ),
    "kb_relation_recommendations": (
        "755e5ba2490952d8b0eb0d28e7c193ce9e37c2c6adfc1aeea5b33aa7daf08d1a",
        "e9c8b281dd439f3b047021b5200c0166390101c9f56b3e9ca31bb1894ba3891e",
    ),
}

SYSTEM_TOOL_CONTRACT_SET_DIGEST = (
    "fb14255be369d04b575425f8e0eb0edcc8f358bd14343a45072da23e37c34448"
)


def test_system_tool_golden_schemas_and_contract_set_digest_are_stable() -> None:
    rows = _system_tool_param_contracts()
    expected_names = list(SYSTEM_TOOL_GOLDEN_DIGESTS.keys())
    assert [name for name, _, _ in rows] == expected_names

    ordered: list[tuple[str, str, str]] = []
    for name, inputs, outputs in rows:
        input_schema = tool_params_to_binding_schema(inputs, require_object_root=True)
        output_schema = tool_params_to_binding_schema(outputs, require_object_root=True)
        input_digest = binding_schema_digest(input_schema)
        output_digest = binding_schema_digest(output_schema)
        ordered.append((name, input_digest, output_digest))

        # Pin every tool's input/output digests to checked-in fixed hex vectors.
        expected_input, expected_output = SYSTEM_TOOL_GOLDEN_DIGESTS[name]
        assert input_digest == expected_input
        assert output_digest == expected_output

        # Golden shape: object root, no defaults/examples, deterministic property order.
        assert input_schema["type"] == "object"
        assert output_schema["type"] == "object"
        assert "default" not in canonical_json_bytes(input_schema).decode("utf-8")
        assert list(input_schema.get("properties", {}).keys()) == sorted(
            input_schema.get("properties", {}).keys()
        )

        # Bare array params from the registry freeze as items:{} (no items_type field).
        for prop in input_schema.get("properties", {}).values():
            if isinstance(prop, dict) and prop.get("type") == "array":
                assert prop.get("items") == {}
        for prop in output_schema.get("properties", {}).values():
            if isinstance(prop, dict) and prop.get("type") == "array":
                assert prop.get("items") == {}

        # Re-running publish-time conversion is byte-identical.
        again = tool_params_to_binding_schema(inputs, require_object_root=True)
        assert canonical_json_bytes(again) == canonical_json_bytes(input_schema)

    set_digest = system_tool_contract_set_digest(ordered)
    assert set_digest == SYSTEM_TOOL_CONTRACT_SET_DIGEST
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

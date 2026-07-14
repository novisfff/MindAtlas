from __future__ import annotations

import logging
import re
import threading
from typing import Any

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

from app.assistant.domain.digests import canonical_json_bytes, sha256_canonical_json  # noqa: E402
from app.assistant.domain.json_schema import (  # noqa: E402
    binding_schema_digest,
    normalize_binding_schema,
)


# Fixed digest vectors (Plan 01 normalization dialect).
SIMPLE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
    "additionalProperties": False,
}
SIMPLE_OUTPUT_SCHEMA = {"type": "string"}
SIMPLE_INPUT_DIGEST = "10798ac8d2df2a3150d204e10be2e4fb12cc89650fb68e3a6d3b4b38e1914e26"
SIMPLE_OUTPUT_DIGEST = "00404e686415370f1711c4d7acfa2905444d3cf23cef2e10c47d445ebe690f96"


FAKE_BEARER = "Bearer sk-live-secret-token-ABCDEF123456"
FAKE_COOKIE = "sessionid=super-secret-cookie-value"
FAKE_API_KEY = "api_key=sk_test_secret_key_xyz"
FAKE_PASSWORD = "password=hunter2-super-secret"
FAKE_REMOTE_BODY = '{"error":"upstream","token":"remote-response-body-secret"}'


def _compile(schema: dict[str, Any], *, require_object_root: bool):
    from app.assistant.capabilities.json_schema import compile_binding_schema

    normalized = normalize_binding_schema(schema, require_object_root=require_object_root)
    digest = binding_schema_digest(normalized)
    return compile_binding_schema(
        normalized,
        expected_digest=digest,
        require_object_root=require_object_root,
    )


def test_fixed_digest_vectors() -> None:
    assert binding_schema_digest(SIMPLE_INPUT_SCHEMA) == SIMPLE_INPUT_DIGEST
    assert binding_schema_digest(SIMPLE_OUTPUT_SCHEMA) == SIMPLE_OUTPUT_DIGEST
    normalized_in = normalize_binding_schema(SIMPLE_INPUT_SCHEMA, require_object_root=True)
    normalized_out = normalize_binding_schema(SIMPLE_OUTPUT_SCHEMA, require_object_root=False)
    assert binding_schema_digest(normalized_in) == SIMPLE_INPUT_DIGEST
    assert binding_schema_digest(normalized_out) == SIMPLE_OUTPUT_DIGEST


def test_plan01_helpers_imported_without_rename() -> None:
    import app.assistant.capabilities.json_schema as runtime_schema
    import app.assistant.domain.json_schema as plan01_schema

    assert runtime_schema.normalize_binding_schema is plan01_schema.normalize_binding_schema
    assert runtime_schema.binding_schema_digest is plan01_schema.binding_schema_digest


def test_object_input_and_arbitrary_output_schema() -> None:
    from app.assistant.capabilities.json_schema import validate_json_value

    input_compiled = _compile(SIMPLE_INPUT_SCHEMA, require_object_root=True)
    validate_json_value(input_compiled, {"query": "hello"}, label="input")
    with pytest.raises(Exception):
        validate_json_value(input_compiled, "not-object", label="input")  # type: ignore[arg-type]

    output_compiled = _compile(SIMPLE_OUTPUT_SCHEMA, require_object_root=False)
    validate_json_value(output_compiled, "ok", label="output")
    validate_json_value(
        _compile({"type": ["null", "boolean"]}, require_object_root=False),
        True,
        label="output",
    )
    validate_json_value(
        _compile({"type": "array", "items": {"type": "number"}}, require_object_root=False),
        [1, 2.5],
        label="output",
    )


def test_properties_required_additional_properties() -> None:
    from app.assistant.capabilities.errors import CapabilitySchemaValidationError
    from app.assistant.capabilities.json_schema import validate_json_value

    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "number"},
        },
        "required": ["a"],
        "additionalProperties": False,
    }
    compiled = _compile(schema, require_object_root=True)
    validate_json_value(compiled, {"a": "x", "b": 1}, label="input")
    with pytest.raises(CapabilitySchemaValidationError) as missing:
        validate_json_value(compiled, {"b": 1}, label="input")
    with pytest.raises(CapabilitySchemaValidationError) as extra:
        validate_json_value(compiled, {"a": "x", "c": True}, label="input")
    assert missing.value.issues
    assert extra.value.issues


def test_arrays_nested_objects_enums_const_combinators_and_bounds() -> None:
    from app.assistant.capabilities.errors import CapabilitySchemaValidationError
    from app.assistant.capabilities.json_schema import validate_json_value

    schema = {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 8},
                "minItems": 1,
                "maxItems": 3,
            },
            "nested": {
                "type": "object",
                "properties": {
                    "flag": {"const": True},
                    "mode": {"enum": ["a", "b"]},
                    "score": {"type": "number", "minimum": 0, "maximum": 10},
                },
                "required": ["flag", "mode"],
                "additionalProperties": False,
            },
            "choice": {
                "anyOf": [
                    {"type": "string", "const": "left"},
                    {"type": "string", "const": "right"},
                ]
            },
            "one": {
                "oneOf": [
                    {"type": "integer", "const": 1},
                    {"type": "integer", "const": 2},
                ]
            },
            "all": {
                "allOf": [
                    {"type": "object"},
                    {"properties": {"x": {"type": "string"}}, "required": ["x"]},
                ]
            },
        },
        "required": ["tags", "nested"],
        "additionalProperties": False,
    }
    compiled = _compile(schema, require_object_root=True)
    validate_json_value(
        compiled,
        {
            "tags": ["alpha"],
            "nested": {"flag": True, "mode": "a", "score": 3},
            "choice": "left",
            "one": 1,
            "all": {"x": "ok"},
        },
        label="input",
    )
    with pytest.raises(CapabilitySchemaValidationError):
        validate_json_value(
            compiled,
            {
                "tags": [""],
                "nested": {"flag": False, "mode": "z", "score": 99},
            },
            label="input",
        )


def test_local_defs_and_remote_ref_rejection() -> None:
    from app.assistant.capabilities.json_schema import compile_binding_schema, validate_json_value

    local = {
        "type": "object",
        "properties": {"item": {"$ref": "#/$defs/item"}},
        "$defs": {"item": {"type": "string", "minLength": 1}},
        "required": ["item"],
        "additionalProperties": False,
    }
    compiled = _compile(local, require_object_root=True)
    validate_json_value(compiled, {"item": "x"}, label="input")

    with pytest.raises(ValueError):
        normalize_binding_schema(
            {
                "type": "object",
                "properties": {"item": {"$ref": "https://example.com/schema.json"}},
            },
            require_object_root=True,
        )
    # Compiler must also refuse digest mismatch / invalid body.
    normalized = normalize_binding_schema(local, require_object_root=True)
    with pytest.raises(ValueError):
        compile_binding_schema(
            normalized,
            expected_digest="0" * 64,
            require_object_root=True,
        )


def test_nullable_true_normalization_parity() -> None:
    raw = {"type": "string", "nullable": True}
    publish = normalize_binding_schema(raw, require_object_root=False)
    runtime = normalize_binding_schema(dict(raw), require_object_root=False)
    assert publish == runtime == {"type": ["null", "string"]}
    assert canonical_json_bytes(publish) == canonical_json_bytes(runtime)
    assert binding_schema_digest(publish) == binding_schema_digest(runtime)


def test_invalid_regex_and_invalid_schema_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_binding_schema(
            {"type": "string", "pattern": "["},
            require_object_root=False,
        )
    with pytest.raises(ValueError):
        normalize_binding_schema(
            {"type": "object", "properties": "nope"},  # type: ignore[dict-item]
            require_object_root=True,
        )


def test_no_default_application() -> None:
    from app.assistant.capabilities.errors import CapabilitySchemaValidationError
    from app.assistant.capabilities.json_schema import validate_json_value

    # Defaults are rejected by Plan 01 normalization; ensure no silent fill-in path.
    with pytest.raises(ValueError):
        normalize_binding_schema(
            {
                "type": "object",
                "properties": {"q": {"type": "string", "default": "x"}},
            },
            require_object_root=True,
        )
    compiled = _compile(
        {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )
    with pytest.raises(CapabilitySchemaValidationError):
        validate_json_value(compiled, {}, label="input")


def test_dictionary_order_does_not_change_digest_but_semantics_do() -> None:
    left = {
        "type": "object",
        "properties": {
            "b": {"type": "number"},
            "a": {"type": "string"},
        },
        "required": ["b", "a"],
    }
    right = {
        "required": ["a", "b"],
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "number"},
        },
        "type": "object",
    }
    assert binding_schema_digest(left) == binding_schema_digest(right)
    changed = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "integer"},
        },
        "required": ["a", "b"],
    }
    assert binding_schema_digest(left) != binding_schema_digest(changed)


def test_deterministic_error_ordering_cap_and_pointer_escaping() -> None:
    from app.assistant.capabilities.errors import CapabilitySchemaValidationError
    from app.assistant.capabilities.json_schema import validate_json_value

    props = {f"f{i:02d}": {"type": "string", "minLength": 2} for i in range(25)}
    schema = {
        "type": "object",
        "properties": {
            **props,
            "a/b": {"type": "string", "minLength": 2},
            "c~d": {"type": "string", "minLength": 2},
        },
        "required": [f"f{i:02d}" for i in range(25)] + ["a/b", "c~d"],
        "additionalProperties": False,
    }
    compiled = _compile(schema, require_object_root=True)
    bad = {f"f{i:02d}": "x" for i in range(25)}
    bad["a/b"] = "x"
    bad["c~d"] = "x"
    with pytest.raises(CapabilitySchemaValidationError) as exc_info:
        validate_json_value(compiled, bad, label="input")
    issues = exc_info.value.issues
    assert len(issues) <= 20
    pointers = [issue.instance_pointer for issue in issues]
    assert pointers == sorted(pointers)
    joined = " ".join(
        f"{issue.instance_pointer}|{issue.schema_pointer}|{issue.keyword}|{issue.safe_message}"
        for issue in issues
    )
    # JSON Pointer escaping for / and ~
    assert any("~1" in issue.instance_pointer or "/a~1b" in issue.instance_pointer for issue in issues) or any(
        "a~1b" in p for p in pointers
    )
    assert any("~0" in p or "c~0d" in p for p in pointers)
    assert "x" not in joined  # rejected value not echoed
    assert FAKE_BEARER not in joined


def test_errors_do_not_echo_enum_pattern_defaults_examples_or_descriptions() -> None:
    from app.assistant.capabilities.errors import CapabilitySchemaValidationError
    from app.assistant.capabilities.json_schema import validate_json_value

    schema = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["alpha-secret", "beta-secret"],
                "description": "desc-secret-should-not-leak",
                "pattern": "alpha-secret|beta-secret",
                "examples": ["alpha-secret"],
            }
        },
        "required": ["mode"],
        "additionalProperties": False,
    }
    # examples rejected by normalizer; build a valid normalized schema without them.
    normalized = normalize_binding_schema(
        {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["alpha-secret", "beta-secret"],
                    "description": "desc-secret-should-not-leak",
                    "pattern": "alpha-secret|beta-secret",
                }
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )
    from app.assistant.capabilities.json_schema import compile_binding_schema

    compiled = compile_binding_schema(
        normalized,
        expected_digest=binding_schema_digest(normalized),
        require_object_root=True,
    )
    with pytest.raises(CapabilitySchemaValidationError) as exc_info:
        validate_json_value(compiled, {"mode": "gamma-secret"}, label="input")
    text = repr(exc_info.value) + " " + " ".join(
        f"{i.safe_message}|{i.keyword}|{i.instance_pointer}|{i.schema_pointer}"
        for i in exc_info.value.issues
    )
    assert "alpha-secret" not in text
    assert "beta-secret" not in text
    assert "gamma-secret" not in text
    assert "desc-secret-should-not-leak" not in text
    assert "alpha-secret|beta-secret" not in text
    # keep schema unused-var lint quiet if needed
    assert schema["type"] == "object"


def test_format_annotation_does_not_reject_openclaw_date_strings() -> None:
    from app.assistant.capabilities.json_schema import validate_json_value

    schema = {
        "type": "object",
        "properties": {
            "startDate": {"type": "string", "format": "date"},
            "endDate": {"type": "string", "format": "date"},
        },
        "required": ["startDate", "endDate"],
        "additionalProperties": False,
    }
    compiled = _compile(schema, require_object_root=True)
    # Current OpenClaw date strings (YYYY-MM-DD) must remain accepted.
    validate_json_value(
        compiled,
        {"startDate": "2026-07-13", "endDate": "2026-07-14"},
        label="input",
    )


def test_compile_cache_is_thread_safe_and_schema_only() -> None:
    from app.assistant.capabilities.json_schema import compile_binding_schema, validate_json_value

    normalized = normalize_binding_schema(SIMPLE_INPUT_SCHEMA, require_object_root=True)
    digest = binding_schema_digest(normalized)
    results: list[Any] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            compiled = compile_binding_schema(
                normalized,
                expected_digest=digest,
                require_object_root=True,
            )
            validate_json_value(compiled, {"query": "ok"}, label="input")
            results.append(compiled.digest)
        except BaseException as exc:  # pragma: no cover - collected for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert results and all(item == digest for item in results)


def test_secret_safety_in_validation_errors_and_public_surfaces(caplog: pytest.LogCaptureFixture) -> None:
    from app.assistant.capabilities.contracts import (
        CapabilityError,
        CapabilityEventMetadata,
        CapabilityRuntimeEvent,
        completed_result,
        failed_result,
    )
    from app.assistant.capabilities.errors import (
        CapabilitySchemaValidationError,
        sanitize_unexpected_exception,
    )
    from app.assistant.capabilities.json_schema import compile_binding_schema, validate_json_value

    secrets = [FAKE_BEARER, FAKE_COOKIE, FAKE_API_KEY, FAKE_PASSWORD, FAKE_REMOTE_BODY]
    schema = {
        "type": "object",
        "properties": {"token": {"type": "string", "minLength": 1000}},
        "required": ["token"],
        "additionalProperties": False,
    }
    normalized = normalize_binding_schema(schema, require_object_root=True)
    compiled = compile_binding_schema(
        normalized,
        expected_digest=binding_schema_digest(normalized),
        require_object_root=True,
    )

    with pytest.raises(CapabilitySchemaValidationError) as exc_info:
        validate_json_value(
            compiled,
            {
                "token": FAKE_BEARER,
                "cookie": FAKE_COOKIE,
                "apiKey": FAKE_API_KEY,
                "password": FAKE_PASSWORD,
                "body": FAKE_REMOTE_BODY,
            },
            label="input",
        )
    err = exc_info.value
    surfaces = [
        repr(err),
        str(err),
        " ".join(issue.safe_message for issue in err.issues),
        " ".join(issue.instance_pointer for issue in err.issues),
    ]
    from app.assistant.capabilities.contracts import CapabilityMetrics

    public_error = CapabilityError(
        error_type="invalid_input",
        safe_code="invalid_input",
        safe_message="input failed schema validation",
        retry_disposition="never",
        validation_issues=err.issues,
    )
    metrics = CapabilityMetrics(duration_ms=1.0, input_bytes=0, output_bytes=0)
    result = failed_result(error=public_error, metrics=metrics)
    _ = completed_result(metrics=metrics, user_text=None, structured_output=None)
    event = CapabilityRuntimeEvent(
        event_type="capability.failed",
        call_id="call-1",
        capability_key="k",
        target_identity="t",
        capability_type="tool",
        safe_status="failed",
        metadata=CapabilityEventMetadata(),
    )
    surfaces.extend(
        [
            repr(public_error),
            str(public_error.model_dump(mode="json")),
            repr(result),
            str(result.model_dump(mode="json")),
            repr(event),
            str(event.model_dump(mode="json")),
        ]
    )

    class Boom(Exception):
        pass

    secret_exc = Boom(f"upstream failed with {FAKE_BEARER} cookie={FAKE_COOKIE}")
    with caplog.at_level(logging.INFO):
        safe = sanitize_unexpected_exception(
            secret_exc,
            call_id="call-1",
            target_identity="t",
            stage="adapter",
        )
    surfaces.append(repr(safe))
    surfaces.append(str(safe))
    joined = "\n".join(surfaces) + "\n".join(r.message for r in caplog.records)
    for secret in secrets:
        assert secret not in joined
    # Shared path must not log with exc_info / traceback containing secrets.
    assert all(getattr(record, "exc_info", None) in (None, False, (None, None, None)) for record in caplog.records)
    for record in caplog.records:
        assert FAKE_BEARER not in record.getMessage()
        assert FAKE_COOKIE not in record.getMessage()


def test_requirements_declare_jsonschema_range() -> None:
    from pathlib import Path

    # Resolve from this test file so the check works both from repo root and
    # from backend/ (GitHub Backend Tests job cwd).
    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    text = requirements.read_text(encoding="utf-8")
    assert re.search(r"(?m)^jsonschema>=4\.23,<5(\.0)?$", text)


def test_publish_runtime_parity_uses_plan01_helper_directly() -> None:
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
    assert sha256_canonical_json(publish) == binding_schema_digest(publish)

    from app.assistant.capabilities.json_schema import compile_binding_schema, validate_json_value

    compiled = compile_binding_schema(
        runtime,
        expected_digest=binding_schema_digest(runtime),
        require_object_root=True,
    )
    validate_json_value(compiled, {"a": True, "b": None}, label="input")

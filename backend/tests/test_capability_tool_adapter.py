"""Tool capability adapter and secret-safe remote boundary tests (Plan 02 Task 4)."""

from __future__ import annotations

import io
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_BUILD_REVISION", "test-build-c25d03f")
os.environ.setdefault("APP_ENV", "test")


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeCancellation:
    cancelled: bool = False

    def is_cancelled(self) -> bool:
        return self.cancelled

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError("cancelled")


@dataclass
class _RecordingEventSink:
    events: list[Any] = field(default_factory=list)

    def emit(self, event: Any) -> None:
        self.events.append(event)


class _FakeTool:
    def __init__(self, result: Any = None, *, raise_exc: BaseException | None = None, name: str = "fake"):
        self.name = name
        self.description = "fake tool"
        self.args_schema = None
        self.calls: list[dict[str, Any]] = []
        self._result = result
        self._raise = raise_exc

    def func(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        if self._raise is not None:
            raise self._raise
        return self._result


def _metrics_ok():
    from app.assistant.capabilities.contracts import CapabilityMetrics

    return CapabilityMetrics(duration_ms=0.0, input_bytes=0, output_bytes=0)


def _timeout_policy(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityTimeoutPolicy

    payload = {
        "mode": "cooperative",
        "timeout_seconds": None,
        "cancellation_supported": True,
    }
    payload.update(overrides)
    return CapabilityTimeoutPolicy(**payload)


def _behavior(**overrides: Any):
    from app.assistant.capabilities.contracts import (
        CapabilityBehavior,
        ClassificationContractRef,
    )

    payload = {
        "classification": ClassificationContractRef(
            schema_version=1,
            revision="plan02-v1",
            ruleset_digest=DIGEST_A,
        ),
        "side_effect": "read",
        "parallel_safe": True,
        "interrupt_mode": "none",
        "timeout_policy": _timeout_policy(),
        "behavior_digest": DIGEST_B,
    }
    payload.update(overrides)
    return CapabilityBehavior(**payload)


def _availability(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityAvailability

    payload = {
        "status": "available",
        "reason_code": None,
        "compatibility_only": False,
    }
    payload.update(overrides)
    return CapabilityAvailability(**payload)


def _completion(**overrides: Any):
    from app.assistant.domain.contracts import CapabilityCompletionContract

    payload = {
        "terminal_output": True,
        "needs_followup": False,
        "followup_hint": None,
    }
    payload.update(overrides)
    return CapabilityCompletionContract(**payload)


def _descriptor(
    *,
    capability_key: str = "fake.tool",
    target_identity: str = "system-tool:fake",
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    input_schema_digest: str | None = None,
    output_schema_digest: str | None = None,
    availability: Any | None = None,
    behavior: Any | None = None,
    completion: Any | None = None,
):
    from app.assistant.capabilities.contracts import CapabilityDescriptor
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    in_schema = input_schema if input_schema is not None else {"type": "object"}
    out_schema = output_schema if output_schema is not None else {"type": "object"}
    in_norm = normalize_binding_schema(in_schema, require_object_root=_schema_root_is_object(in_schema))
    out_norm = normalize_binding_schema(out_schema, require_object_root=_schema_root_is_object(out_schema))
    in_digest = input_schema_digest or binding_schema_digest(in_norm)
    out_digest = output_schema_digest or binding_schema_digest(out_norm)
    return CapabilityDescriptor(
        capability_key=capability_key,
        capability_type="tool",
        target_identity=target_identity,
        target_id=None,
        target_version_id=None,
        target_revision=None,
        resolution_digest=DIGEST_A,
        binding_contract_digest=DIGEST_B,
        dependency_closure_digest=DIGEST_C,
        display_name="Fake",
        description="fake tool",
        input_schema=in_norm,
        output_schema=out_norm,
        input_schema_digest=in_digest,
        output_schema_digest=out_digest,
        descriptor_digest=DIGEST_D,
        executable_revision="build-1",
        behavior=behavior or _behavior(),
        availability=availability or _availability(),
        completion=completion or _completion(),
    )


def _schema_root_is_object(schema: dict) -> bool:
    return schema.get("type") == "object"


def _decision():
    from app.assistant.capabilities.policy import AtomicSingleUseDispatchPermit
    from app.assistant.capabilities.contracts import (
        CapabilityOwnerRef,
        CapabilityPolicyDecision,
    )

    return CapabilityPolicyDecision(
        allowed=True,
        reason_code="allow",
        call_id="call-1",
        descriptor_digest=DIGEST_D,
        classification_ruleset_digest=DIGEST_A,
        evidence_digest=DIGEST_E,
        owner=CapabilityOwnerRef(
            owner_kind="test",
            owner_id="test-owner",
            owner_version_id=None,
        ),
        granted_side_effects=("read",),
        grant_source_digest=DIGEST_A,
        decision_digest=DIGEST_B,
        dispatch_permit=AtomicSingleUseDispatchPermit(),
    )


def _context(call_id: str = "call-1"):
    from app.assistant.capabilities.contracts import CapabilityExecutionContext

    return CapabilityExecutionContext(call_id=call_id)


def _target(
    tool_obj: Any,
    *,
    target_identity: str = "system-tool:fake",
    is_system: bool = True,
    tool_id=None,
    config_revision=None,
    config_digest=None,
    descriptor=None,
):
    from app.assistant.capabilities.ports import (
        ExecutableToolTarget,
        ResolvedCapabilityTarget,
    )

    desc = descriptor or _descriptor(target_identity=target_identity)
    # Adapter does not re-resolve binding; keep an opaque placeholder.
    binding = SimpleNamespace(
        resolved=SimpleNamespace(
            capability_type="tool",
            capability_key=desc.capability_key,
            target_identity=target_identity,
            binding_contract_digest=desc.binding_contract_digest,
            dependency_closure_digest=desc.dependency_closure_digest,
        ),
        provenance=SimpleNamespace(origin="test"),
    )
    executable = ExecutableToolTarget(
        target_identity=target_identity,
        tool_id=tool_id,
        config_revision=config_revision,
        config_digest=config_digest or DIGEST_A,
        is_system=is_system,
        tool_object_or_record=tool_obj,
    )
    closure = SimpleNamespace(
        binding_contract_digest=desc.binding_contract_digest,
        dependency_closure_digest=desc.dependency_closure_digest,
        bind_authorized=lambda **kwargs: SimpleNamespace(),
    )
    return ResolvedCapabilityTarget(
        descriptor=desc,
        binding=binding,  # type: ignore[arg-type]
        executable=executable,
        execution_closure=closure,  # type: ignore[arg-type]
    )


def _request(tool_obj: Any, validated_input: dict, **target_kwargs: Any):
    from app.assistant.capabilities.ports import CapabilityAdapterRequest

    target = _target(tool_obj, **target_kwargs)
    return CapabilityAdapterRequest(
        target=target,
        validated_input=validated_input,
        context=_context(),
        decision=_decision(),
    )


def _ports(cancelled: bool = False):
    from app.assistant.capabilities.ports import CapabilityRuntimePorts

    cancel = _FakeCancellation(cancelled=cancelled)
    sink = _RecordingEventSink()
    return CapabilityRuntimePorts(cancellation=cancel, events=sink), cancel, sink


# ---------------------------------------------------------------------------
# normalize_tool_result_value unit tests
# ---------------------------------------------------------------------------


def test_normalize_preserves_dict_list_scalar_string() -> None:
    from app.assistant.capabilities.adapters.tool import normalize_tool_result_value

    obj_schema = {"type": "object"}
    assert normalize_tool_result_value({"a": 1}, output_schema=obj_schema) == {"a": 1}
    assert normalize_tool_result_value([1, 2], output_schema={"type": "array"}) == [1, 2]
    assert normalize_tool_result_value(3, output_schema={"type": "number"}) == 3
    assert normalize_tool_result_value(True, output_schema={"type": "boolean"}) is True
    assert normalize_tool_result_value(None, output_schema={"type": "null"}) is None
    assert normalize_tool_result_value("hello", output_schema={"type": "string"}) == "hello"


def test_normalize_parses_complete_json_string_only_for_structured_schema() -> None:
    from app.assistant.capabilities.adapters.tool import normalize_tool_result_value

    parsed = normalize_tool_result_value('{"a":1}', output_schema={"type": "object"})
    assert parsed == {"a": 1}
    arr = normalize_tool_result_value("[1,2]", output_schema={"type": "array"})
    assert arr == [1, 2]
    # String schema keeps plain text; no guessing.
    assert normalize_tool_result_value('{"a":1}', output_schema={"type": "string"}) == '{"a":1}'
    assert normalize_tool_result_value("not-json", output_schema={"type": "string"}) == "not-json"


def test_normalize_rejects_partial_fenced_and_non_serializable() -> None:
    from app.assistant.capabilities.adapters.tool import normalize_tool_result_value

    with pytest.raises((ValueError, json.JSONDecodeError)):
        normalize_tool_result_value('{"a":', output_schema={"type": "object"})
    with pytest.raises(ValueError):
        normalize_tool_result_value('```json\n{"a":1}\n```', output_schema={"type": "object"})
    with pytest.raises(TypeError):
        normalize_tool_result_value(datetime(2026, 1, 1), output_schema={"type": "object"})
    with pytest.raises(TypeError):
        normalize_tool_result_value(b"bytes", output_schema={"type": "string"})
    with pytest.raises(TypeError):
        normalize_tool_result_value({1, 2}, output_schema={"type": "array"})
    with pytest.raises(TypeError):
        normalize_tool_result_value(object(), output_schema={"type": "object"})


# ---------------------------------------------------------------------------
# System tool adapter tests
# ---------------------------------------------------------------------------


def test_system_tool_validated_args_reach_exact_resolved_tool() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    tool = _FakeTool(result={"ok": True})
    out_schema = normalize_binding_schema(
        {"type": "object", "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False},
        require_object_root=True,
    )
    desc = _descriptor(
        target_identity="system-tool:fake",
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "additionalProperties": False},
    )
    req = _request(tool, {"q": "hello"}, target_identity="system-tool:fake", descriptor=desc)
    ports, _, sink = _ports()

    # Guard: ToolRegistry.resolve must never be called.
    with patch("app.assistant_config.registry.ToolRegistry.resolve", side_effect=AssertionError("resolve")):
        with patch(
            "app.assistant_config.registry.ToolRegistry.resolve_system_tool",
            side_effect=AssertionError("resolve_system_tool"),
        ):
            result = ToolCapabilityAdapter().execute(req, ports=ports)

    assert result.status == "completed"
    assert result.structured_output == {"ok": True}
    assert tool.calls == [{"q": "hello"}]
    types = [e.event_type for e in sink.events]
    assert types == ["capability.started", "capability.completed"]


def test_create_entry_exact_resolved_identity_returns_nonwriting_proposal() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.tools import create_entry
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
        },
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
        },
        "additionalProperties": False,
    }
    desc = _descriptor(
        capability_key="create_entry",
        target_identity="system-tool:create_entry",
        input_schema=input_schema,
        output_schema=output_schema,
    )
    req = _request(
        create_entry,
        {"title": "  adapter title  ", "content": "  adapter body  "},
        target_identity="system-tool:create_entry",
        descriptor=desc,
    )
    ports, _, _ = _ports()
    closed: list[bool] = []

    class _Session:
        def close(self) -> None:
            closed.append(True)

    with patch(
        "app.assistant.workflow.engine.runtime_helpers.sessionmaker",
        return_value=lambda **_kwargs: _Session(),
    ):
        result = ToolCapabilityAdapter().execute(req, ports=ports)

    assert result.status == "completed"
    assert result.structured_output == {
        "title": "adapter title",
        "content": "adapter body",
    }
    assert closed == [True]


def test_create_entry_different_resolved_identity_fails_gateway_closed() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.tools import create_entry
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
        },
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
        },
        "additionalProperties": False,
    }
    desc = _descriptor(
        capability_key="create_entry",
        target_identity="system-tool:search_entries",
        input_schema=input_schema,
        output_schema=output_schema,
    )
    req = _request(
        create_entry,
        {"title": "must not receive marker", "content": "adapter body"},
        target_identity="system-tool:search_entries",
        descriptor=desc,
    )
    ports, _, _ = _ports()

    class _Session:
        def close(self) -> None:
            return None

    with patch(
        "app.assistant.workflow.engine.runtime_helpers.sessionmaker",
        return_value=lambda **_kwargs: _Session(),
    ):
        result = ToolCapabilityAdapter().execute(req, ports=ports)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.safe_code == "capability_gateway_required"


def test_system_tool_wrap_tool_with_db_creates_and_closes_session() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    tool = _FakeTool(result={"ok": True})
    out_schema = normalize_binding_schema(
        {"type": "object", "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False},
        require_object_root=True,
    )
    desc = _descriptor(
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
    )
    req = _request(tool, {}, descriptor=desc)
    ports, _, _ = _ports()

    closed: list[bool] = []

    class _Sess:
        def close(self):
            closed.append(True)

    class _SM:
        def __call__(self, **kwargs):
            return _Sess()

    with patch("app.assistant.workflow.engine.runtime_helpers.sessionmaker", return_value=_SM()):
        with patch("app.assistant.workflow.engine.runtime_helpers.set_current_db", return_value="tok"):
            with patch("app.assistant.workflow.engine.runtime_helpers.reset_current_db"):
                result = ToolCapabilityAdapter().execute(req, ports=ports)

    assert result.status == "completed"
    assert closed == [True]


def test_system_tool_wrap_closes_session_on_failure() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    tool = _FakeTool(raise_exc=RuntimeError("secret-token-XYZ"))
    out_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    desc = _descriptor(
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
    )
    req = _request(tool, {}, descriptor=desc)
    ports, _, _ = _ports()

    closed: list[bool] = []

    class _Sess:
        def close(self):
            closed.append(True)

    class _SM:
        def __call__(self, **kwargs):
            return _Sess()

    with patch("app.assistant.workflow.engine.runtime_helpers.sessionmaker", return_value=_SM()):
        with patch("app.assistant.workflow.engine.runtime_helpers.set_current_db", return_value="tok"):
            with patch("app.assistant.workflow.engine.runtime_helpers.reset_current_db"):
                result = ToolCapabilityAdapter().execute(req, ports=ports)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "execution_failed"
    assert "secret-token-XYZ" not in (result.error.safe_message or "")
    assert closed == [True]


def test_system_tool_exception_no_secret_log_leakage(caplog: pytest.LogCaptureFixture) -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    secret = "sk-live-SUPER-SECRET-KEY-12345"
    tool = _FakeTool(raise_exc=RuntimeError(secret))
    out_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    desc = _descriptor(
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
    )
    req = _request(tool, {}, descriptor=desc)
    ports, _, sink = _ports()

    with caplog.at_level(logging.DEBUG):
        result = ToolCapabilityAdapter().execute(req, ports=ports)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "execution_failed"
    blob = " ".join(r.getMessage() for r in caplog.records)
    assert secret not in blob
    assert secret not in str(result.error.safe_message)
    assert secret not in json.dumps(result.model_dump(mode="json"), default=str)
    types = [e.event_type for e in sink.events]
    assert types == ["capability.started", "capability.failed"]


def test_disabled_target_never_invokes() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter

    tool = _FakeTool(result={"ok": True})
    desc = _descriptor(availability=_availability(status="disabled", reason_code="tool_disabled"))
    req = _request(tool, {}, descriptor=desc)
    ports, _, sink = _ports()
    result = ToolCapabilityAdapter().execute(req, ports=ports)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "unavailable"
    assert tool.calls == []
    assert all(e.event_type != "capability.started" for e in sink.events)


def test_cancellation_before_invocation_never_invokes() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    tool = _FakeTool(result={"ok": True})
    out_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    desc = _descriptor(
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
    )
    req = _request(tool, {}, descriptor=desc)
    ports, _, sink = _ports(cancelled=True)
    result = ToolCapabilityAdapter().execute(req, ports=ports)
    assert result.status == "cancelled"
    assert tool.calls == []
    assert [e.event_type for e in sink.events] == ["capability.cancelled"]


def test_cancellation_after_pure_read_returns_cancelled() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    tool = _FakeTool(result={"ok": True})
    out_schema = normalize_binding_schema(
        {"type": "object", "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False},
        require_object_root=True,
    )
    desc = _descriptor(
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
        behavior=_behavior(side_effect="read"),
    )
    req = _request(tool, {}, descriptor=desc)
    ports, cancel, sink = _ports(cancelled=False)

    original_invoke = ToolCapabilityAdapter._invoke_tool

    def _invoke_and_cancel(self, **kwargs):  # noqa: ANN001
        out = original_invoke(self, **kwargs)
        cancel.cancelled = True
        return out

    with patch.object(ToolCapabilityAdapter, "_invoke_tool", _invoke_and_cancel):
        result = ToolCapabilityAdapter().execute(req, ports=ports)

    assert result.status == "cancelled"
    assert tool.calls  # invoked once
    assert [e.event_type for e in sink.events] == ["capability.started", "capability.cancelled"]


def test_cancellation_after_write_success_keeps_completed() -> None:
    """Write/draft/unknown success must not be rewritten to cancelled after invoke."""
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    tool = _FakeTool(result={"ok": True})
    out_schema = normalize_binding_schema(
        {"type": "object", "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False},
        require_object_root=True,
    )
    desc = _descriptor(
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
        behavior=_behavior(side_effect="write_local", parallel_safe=False),
    )
    req = _request(tool, {}, descriptor=desc)
    ports, cancel, sink = _ports(cancelled=False)

    original_invoke = ToolCapabilityAdapter._invoke_tool

    def _invoke_and_cancel(self, **kwargs):  # noqa: ANN001
        out = original_invoke(self, **kwargs)
        cancel.cancelled = True
        return out

    with patch.object(ToolCapabilityAdapter, "_invoke_tool", _invoke_and_cancel):
        result = ToolCapabilityAdapter().execute(req, ports=ports)

    assert result.status == "completed"
    assert tool.calls
    assert result.structured_output == {"ok": True}
    assert [e.event_type for e in sink.events] == ["capability.started", "capability.completed"]


def test_output_schema_mismatch_is_invalid_output() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    tool = _FakeTool(result={"wrong": 1})
    out_schema = normalize_binding_schema(
        {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        require_object_root=True,
    )
    desc = _descriptor(
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
    )
    req = _request(tool, {}, descriptor=desc)
    ports, _, _ = _ports()
    result = ToolCapabilityAdapter().execute(req, ports=ports)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "invalid_output"


def test_non_serializable_results_are_invalid_output() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    out_schema = normalize_binding_schema({"type": "object"}, require_object_root=True)
    digest = binding_schema_digest(out_schema)
    ports, _, _ = _ports()

    for bad in (datetime(2026, 1, 1), b"\x00\x01", {1, 2}, object(), SimpleNamespace(a=1)):
        tool = _FakeTool(result=bad)
        desc = _descriptor(output_schema=out_schema, output_schema_digest=digest)
        req = _request(tool, {}, descriptor=desc)
        result = ToolCapabilityAdapter().execute(req, ports=ports)
        assert result.status == "failed", bad
        assert result.error is not None
        assert result.error.error_type == "invalid_output"


def test_string_result_for_string_schema_completes() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    tool = _FakeTool(result="plain text")
    out_schema = normalize_binding_schema({"type": "string"}, require_object_root=False)
    desc = _descriptor(
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
    )
    req = _request(tool, {}, descriptor=desc)
    ports, _, _ = _ports()
    result = ToolCapabilityAdapter().execute(req, ports=ports)
    assert result.status == "completed"
    assert result.structured_output == "plain text"
    assert result.user_text == "plain text"


# ---------------------------------------------------------------------------
# Remote tool security tests
# ---------------------------------------------------------------------------


def test_remote_no_decrypt_during_policy_denial_or_disabled() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant_config.remote_tool import RemoteTool

    remote = RemoteTool(
        name="r",
        description=None,
        endpoint_url="https://api.example.com/x",
        http_method="POST",
        auth_type="bearer",
        api_key_encrypted="enc-secret",
        timeout_seconds=5,
    )
    desc = _descriptor(
        target_identity="remote-tool:11111111-1111-1111-1111-111111111111",
        availability=_availability(status="disabled", reason_code="tool_disabled"),
        output_schema={"type": "string"},
        behavior=_behavior(side_effect="write_external", parallel_safe=False),
    )
    req = _request(
        remote,
        {"q": "1"},
        target_identity=desc.target_identity,
        is_system=False,
        tool_id=uuid4(),
        config_revision=1,
        descriptor=desc,
    )
    ports, _, _ = _ports()
    with patch("app.assistant_config.remote_tool.decrypt_api_key", side_effect=AssertionError("decrypt")):
        result = ToolCapabilityAdapter().execute(req, ports=ports)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "unavailable"


def test_remote_decrypts_once_immediately_before_request() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant_config.remote_tool import RemoteTool
    import app.assistant_config.remote_tool as remote_mod
    import app.common.ssrf as ssrf_mod
    import socket

    decrypt_calls: list[str] = []

    def _decrypt(value: str) -> str:
        decrypt_calls.append(value)
        return "token-ABC"

    captured: dict[str, Any] = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok":true}'

    class _Opener:
        def open(self, req, timeout=0):
            captured["req"] = req
            captured["timeout"] = timeout
            return _FakeResp()

    remote = RemoteTool(
        name="r",
        description=None,
        endpoint_url="https://api.example.com/run",
        http_method="POST",
        auth_type="bearer",
        auth_header_name="Authorization",
        auth_scheme="Bearer",
        api_key_encrypted="enc-secret",
        timeout_seconds=9,
        body_type="json",
        body_content="",
    )
    out_schema = normalize_binding_schema(
        {"type": "object", "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False},
        require_object_root=True,
    )
    desc = _descriptor(
        target_identity="remote-tool:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
        behavior=_behavior(
            side_effect="write_external",
            parallel_safe=False,
            timeout_policy=_timeout_policy(mode="native", timeout_seconds=9.0, cancellation_supported=False),
        ),
    )
    # Skip recheck path by using tool_id=None (already resolved RemoteTool).
    req = _request(
        remote,
        {"q": "hi"},
        target_identity=desc.target_identity,
        is_system=False,
        tool_id=None,
        config_revision=1,
        descriptor=desc,
    )
    ports, _, sink = _ports()
    safe_dns = [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
    with (
        patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns),
        patch.object(remote_mod, "decrypt_api_key", side_effect=_decrypt),
        patch.object(remote_mod, "build_opener", return_value=_Opener()),
    ):
        result = ToolCapabilityAdapter().execute(req, ports=ports)

    assert result.status == "completed"
    assert result.structured_output == {"ok": True}
    assert decrypt_calls == ["enc-secret"]
    assert captured["timeout"] == 9
    headers = {k.lower(): v for k, v in captured["req"].header_items()}
    assert headers.get("authorization") == "Bearer token-ABC"
    # Secret never in result metadata / events.
    dumped = json.dumps(result.model_dump(mode="json"), default=str)
    assert "token-ABC" not in dumped
    assert "enc-secret" not in dumped
    for event in sink.events:
        assert "token-ABC" not in json.dumps(event.model_dump(mode="json"), default=str)


def test_remote_revision_recheck_fails_without_network_io() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant_config.remote_tool import RemoteTool

    tool_id = uuid4()
    remote = RemoteTool(
        name="r",
        description=None,
        endpoint_url="https://api.example.com/run",
        http_method="POST",
        auth_type="bearer",
        api_key_encrypted="enc-secret",
        timeout_seconds=5,
    )
    out_schema = normalize_binding_schema({"type": "string"}, require_object_root=False)
    desc = _descriptor(
        target_identity=f"remote-tool:{tool_id}",
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
        behavior=_behavior(side_effect="write_external", parallel_safe=False),
    )
    req = _request(
        remote,
        {"q": "1"},
        target_identity=desc.target_identity,
        is_system=False,
        tool_id=tool_id,
        config_revision=1,
        descriptor=desc,
    )
    ports, _, _ = _ports()

    fake_row = SimpleNamespace(
        id=tool_id,
        config_revision=2,  # rotated
        enabled=True,
        name="r",
        description=None,
        input_params=None,
        endpoint_url="https://api.example.com/run",
        http_method="POST",
        headers=None,
        query_params=None,
        body_type="json",
        body_content=None,
        auth_type="bearer",
        auth_header_name="Authorization",
        auth_scheme="Bearer",
        api_key_encrypted="enc-secret",
        timeout_seconds=5,
        payload_wrapper=None,
        kind="remote",
    )

    class _Q:
        def filter(self, *a, **k):
            return self

        def one_or_none(self):
            return fake_row

    class _Sess:
        def query(self, *a, **k):
            return _Q()

        def close(self):
            return None

    network_calls: list[str] = []

    def _boom_opener(*a, **k):
        network_calls.append("open")
        raise AssertionError("network I/O must not occur after revision drift")

    with (
        patch("app.assistant.capabilities.adapters.tool.SessionLocal", return_value=_Sess()),
        patch("app.assistant_config.remote_tool.decrypt_api_key", side_effect=AssertionError("decrypt")),
        patch("app.assistant_config.remote_tool.build_opener", side_effect=_boom_opener),
    ):
        result = ToolCapabilityAdapter().execute(req, ports=ports)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "version_drift"
    assert result.error.safe_code == "config_revision_drift"
    assert network_calls == []


def test_remote_http_error_body_not_returned(caplog: pytest.LogCaptureFixture) -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant_config.remote_tool import RemoteTool
    import app.assistant_config.remote_tool as remote_mod
    import app.common.ssrf as ssrf_mod
    import socket
    from urllib.error import HTTPError

    secret_body = b'{"error":"api_key=sk-live-LEAKED","authorization":"Bearer leaked"}'
    err = HTTPError(
        url="https://api.example.com/run",
        code=500,
        msg="Internal",
        hdrs=None,
        fp=io.BytesIO(secret_body),
    )

    class _ErrorOpener:
        def open(self, req, timeout=0):
            raise err

    remote = RemoteTool(
        name="r",
        description=None,
        endpoint_url="https://api.example.com/run",
        http_method="POST",
        auth_type="bearer",
        api_key_encrypted="enc-secret",
        timeout_seconds=5,
    )
    out_schema = normalize_binding_schema({"type": "string"}, require_object_root=False)
    desc = _descriptor(
        target_identity="remote-tool:bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
        behavior=_behavior(side_effect="write_external", parallel_safe=False),
    )
    req = _request(
        remote,
        {},
        target_identity=desc.target_identity,
        is_system=False,
        tool_id=None,
        descriptor=desc,
    )
    ports, _, _ = _ports()
    safe_dns = [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
    with caplog.at_level(logging.DEBUG):
        with (
            patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_mod, "decrypt_api_key", return_value="token-ABC"),
            patch.object(remote_mod, "build_opener", return_value=_ErrorOpener()),
        ):
            result = ToolCapabilityAdapter().execute(req, ports=ports)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "execution_failed"
    dumped = json.dumps(result.model_dump(mode="json"), default=str)
    assert "sk-live-LEAKED" not in dumped
    assert "token-ABC" not in dumped
    assert "Bearer leaked" not in dumped
    blob = " ".join(r.getMessage() for r in caplog.records)
    assert "sk-live-LEAKED" not in blob
    assert "token-ABC" not in blob


def test_remote_connection_and_timeout_map_safely(caplog: pytest.LogCaptureFixture) -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant_config.remote_tool import RemoteTool
    import app.assistant_config.remote_tool as remote_mod
    import app.common.ssrf as ssrf_mod
    import socket
    from urllib.error import URLError

    out_schema = normalize_binding_schema({"type": "string"}, require_object_root=False)
    digest = binding_schema_digest(out_schema)
    safe_dns = [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]

    class _ConnOpener:
        def open(self, req, timeout=0):
            raise URLError("secret-reason-body-XYZ")

    class _TimeoutOpener:
        def open(self, req, timeout=0):
            raise URLError(TimeoutError("secret-timeout"))

    remote = RemoteTool(
        name="r",
        description=None,
        endpoint_url="https://api.example.com/run",
        http_method="GET",
        timeout_seconds=3,
    )

    for opener, expected_type, expected_code in (
        (_ConnOpener(), "execution_failed", "remote_connection_failed"),
        (_TimeoutOpener(), "timeout", "remote_timeout"),
    ):
        desc = _descriptor(
            target_identity="remote-tool:cccccccc-cccc-cccc-cccc-cccccccccccc",
            output_schema=out_schema,
            output_schema_digest=digest,
            behavior=_behavior(side_effect="write_external", parallel_safe=False),
        )
        req = _request(
            remote,
            {},
            target_identity=desc.target_identity,
            is_system=False,
            tool_id=None,
            descriptor=desc,
        )
        ports, _, _ = _ports()
        with caplog.at_level(logging.DEBUG):
            with (
                patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns),
                patch.object(remote_mod, "build_opener", return_value=opener),
            ):
                result = ToolCapabilityAdapter().execute(req, ports=ports)
        assert result.status == "failed"
        assert result.error is not None
        assert result.error.error_type == expected_type
        assert result.error.safe_code == expected_code
        dumped = json.dumps(result.model_dump(mode="json"), default=str)
        assert "secret-reason-body-XYZ" not in dumped
        assert "secret-timeout" not in dumped


def test_remote_no_automatic_retry_for_write_methods() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant_config.remote_tool import RemoteTool
    import app.assistant_config.remote_tool as remote_mod
    import app.common.ssrf as ssrf_mod
    import socket
    from urllib.error import HTTPError

    opens: list[int] = []

    class _OnceOpener:
        def open(self, req, timeout=0):
            opens.append(1)
            raise HTTPError(
                url="https://api.example.com/run",
                code=503,
                msg="Unavailable",
                hdrs=None,
                fp=io.BytesIO(b"retry-me"),
            )

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        opens.clear()
        remote = RemoteTool(
            name="r",
            description=None,
            endpoint_url="https://api.example.com/run",
            http_method=method,
            timeout_seconds=2,
        )
        out_schema = normalize_binding_schema({"type": "string"}, require_object_root=False)
        desc = _descriptor(
            target_identity="remote-tool:dddddddd-dddd-dddd-dddd-dddddddddddd",
            output_schema=out_schema,
            output_schema_digest=binding_schema_digest(out_schema),
            behavior=_behavior(side_effect="write_external", parallel_safe=False),
        )
        req = _request(
            remote,
            {},
            target_identity=desc.target_identity,
            is_system=False,
            tool_id=None,
            descriptor=desc,
        )
        ports, _, _ = _ports()
        safe_dns = [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
        with (
            patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_mod, "build_opener", return_value=_OnceOpener()),
        ):
            result = ToolCapabilityAdapter().execute(req, ports=ports)
        assert result.status == "failed"
        assert len(opens) == 1


def test_remote_malformed_output_fails_safely() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant_config.remote_tool import RemoteTool
    import app.assistant_config.remote_tool as remote_mod
    import app.common.ssrf as ssrf_mod
    import socket

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'```json\n{"a":1}\n```'

    class _Opener:
        def open(self, req, timeout=0):
            return _FakeResp()

    remote = RemoteTool(
        name="r",
        description=None,
        endpoint_url="https://api.example.com/run",
        http_method="GET",
        timeout_seconds=5,
    )
    out_schema = normalize_binding_schema(
        {"type": "object", "properties": {"a": {"type": "number"}}, "additionalProperties": False},
        require_object_root=True,
    )
    desc = _descriptor(
        target_identity="remote-tool:eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
        behavior=_behavior(side_effect="write_external", parallel_safe=False),
    )
    req = _request(
        remote,
        {},
        target_identity=desc.target_identity,
        is_system=False,
        tool_id=None,
        descriptor=desc,
    )
    ports, _, _ = _ports()
    safe_dns = [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
    with (
        patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns),
        patch.object(remote_mod, "build_opener", return_value=_Opener()),
    ):
        result = ToolCapabilityAdapter().execute(req, ports=ports)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "invalid_output"


def test_nested_legacy_remote_string_contract() -> None:
    """Nested Legacy Workflow/Agent remote Tool retains frozen string contract."""
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema
    from app.assistant_config.remote_tool import RemoteTool
    import app.assistant_config.remote_tool as remote_mod
    import app.common.ssrf as ssrf_mod
    import socket

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"legacy-plain-response"

    class _Opener:
        def open(self, req, timeout=0):
            return _FakeResp()

    remote = RemoteTool(
        name="r",
        description=None,
        endpoint_url="https://api.example.com/run",
        http_method="GET",
        timeout_seconds=5,
    )
    out_schema = normalize_binding_schema({"type": "string"}, require_object_root=False)
    desc = _descriptor(
        target_identity="remote-tool:ffffffff-ffff-ffff-ffff-ffffffffffff",
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
        behavior=_behavior(side_effect="write_external", parallel_safe=False),
    )
    req = _request(
        remote,
        {},
        target_identity=desc.target_identity,
        is_system=False,
        tool_id=None,
        descriptor=desc,
    )
    ports, _, _ = _ports()
    safe_dns = [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
    with (
        patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns),
        patch.object(remote_mod, "build_opener", return_value=_Opener()),
    ):
        result = ToolCapabilityAdapter().execute(req, ports=ports)
    assert result.status == "completed"
    assert result.structured_output == "legacy-plain-response"


def test_adapter_does_not_spawn_timeout_thread() -> None:
    from app.assistant.capabilities.adapters.tool import ToolCapabilityAdapter
    from app.assistant.domain.json_schema import binding_schema_digest, normalize_binding_schema

    tool = _FakeTool(result={"ok": True})
    out_schema = normalize_binding_schema(
        {"type": "object", "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False},
        require_object_root=True,
    )
    desc = _descriptor(
        output_schema=out_schema,
        output_schema_digest=binding_schema_digest(out_schema),
    )
    req = _request(tool, {}, descriptor=desc)
    ports, _, _ = _ports()

    with patch("threading.Thread", side_effect=AssertionError("no timeout thread")):
        result = ToolCapabilityAdapter().execute(req, ports=ports)
    assert result.status == "completed"


def test_coerce_tool_args_compatibility_via_wrap() -> None:
    """Current coerce_tool_args behavior remains compatible through wrap_tool_with_db."""
    from app.assistant.workflow.engine.runtime_helpers import coerce_tool_args
    from pydantic import BaseModel, Field

    class Args(BaseModel):
        q: str = Field(...)
        limit: int = 10

    tool = SimpleNamespace(args_schema=Args, name="t")
    out = coerce_tool_args(tool, {"q": "x", "limit": 3})
    assert out == {"q": "x", "limit": 3}

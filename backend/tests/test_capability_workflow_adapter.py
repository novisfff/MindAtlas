"""Exact published Workflow capability adapter tests (Plan 02 Task 5)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
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


def _pure_snapshot(
    *,
    input_mode: str = "text",
    output_template: str = "{{start.user_input}}",
    structured_fields: list[dict[str, Any]] | None = None,
    human: bool = False,
) -> dict[str, Any]:
    start_cfg: dict[str, Any] = {"input_mode": input_mode}
    if input_mode == "structured":
        start_cfg["structured_fields"] = structured_fields or [
            {"name": "query", "type": "string", "required": True}
        ]
    nodes: list[dict[str, Any]] = [
        {
            "node_id": "start",
            "node_type": "start",
            "label": "Start",
            "position_x": 0,
            "position_y": 0,
            "config": start_cfg,
        }
    ]
    edges: list[dict[str, Any]] = []
    prev = "start"
    if human:
        nodes.append(
            {
                "node_id": "hitl",
                "node_type": "human_in_loop",
                "label": "HITL",
                "position_x": 80,
                "position_y": 0,
                "config": {
                    "instruction": "approve please",
                    "fields": [{"name": "note", "type": "string"}],
                },
            }
        )
        edges.append(
            {
                "edge_id": "e_start_hitl",
                "source_node_id": "start",
                "target_node_id": "hitl",
            }
        )
        prev = "hitl"
    nodes.append(
        {
            "node_id": "end",
            "node_type": "output",
            "label": "End",
            "position_x": 200,
            "position_y": 0,
            "config": {
                "output_mode": "text",
                "text_template": output_template if not human else "{{hitl.note}}",
            },
        }
    )
    edges.append(
        {
            "edge_id": f"e_{prev}_end",
            "source_node_id": prev,
            "target_node_id": "end",
        }
    )
    return {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}}


@pytest.fixture()
def db():
    reset_caches()
    os.environ["APP_BUILD_REVISION"] = "test-build-c25d03f"
    os.environ["APP_ENV"] = "test"
    from app.config import get_settings

    get_settings.cache_clear()
    from tests._db import make_session
    from app.assistant_config.models import AssistantWorkflow

    session = make_session()
    original = AssistantWorkflow.graph_snapshot

    def _boom(_self):  # noqa: ANN001
        raise AssertionError("AssistantWorkflow.graph_snapshot must not be accessed")

    AssistantWorkflow.graph_snapshot = property(_boom)  # type: ignore[assignment]
    try:
        yield session
    finally:
        AssistantWorkflow.graph_snapshot = original  # type: ignore[assignment]
        session.close()
        get_settings.cache_clear()


def _freeze_workflow(db, *, name: str = "wf_cap", snapshot: dict[str, Any] | None = None):
    from tests.agent_skill_test_support import (
        create_default_model_binding,
        create_published_workflow,
    )
    from app.assistant.capabilities.contracts import (
        FrozenBindingProvenance,
        project_frozen_capability_binding,
    )
    from app.assistant.skills.contracts import CapabilityDeclaration
    from app.assistant.skills.resolution import CapabilityReferenceResolver

    create_default_model_binding(db)
    create_default_model_binding(
        db,
        component="lightrag",
        model_name="embed-test",
        model_type="embedding",
        credential_name="cred-embed",
    )
    workflow, version = create_published_workflow(
        db,
        name=name,
        snapshot=snapshot or _pure_snapshot(),
    )
    db.commit()
    resolved = CapabilityReferenceResolver(db).resolve_many(
        (CapabilityDeclaration(type="workflow", key=name),)
    )[0]
    frozen = project_frozen_capability_binding(
        resolved=resolved,
        provenance=FrozenBindingProvenance(
            origin="test",
            binding_row_id=None,
            owner_version_id=None,
            source_snapshot_digest=DIGEST_A,
        ),
    )
    return workflow, version, frozen


def _decision(*, call_id: str = "call-1", owner_kind: str = "test"):
    from app.assistant.capabilities.contracts import (
        CapabilityOwnerRef,
        CapabilityPolicyDecision,
    )

    return CapabilityPolicyDecision(
        allowed=True,
        reason_code="allow",
        call_id=call_id,
        descriptor_digest=DIGEST_D,
        classification_ruleset_digest=DIGEST_A,
        evidence_digest=DIGEST_E,
        owner=CapabilityOwnerRef(
            owner_kind=owner_kind,  # type: ignore[arg-type]
            owner_id="test-owner",
            owner_version_id=None,
        ),
        granted_side_effects=("read", "write_local", "draft", "unknown"),
        grant_source_digest=DIGEST_A,
        decision_digest=DIGEST_B,
        dispatch_permit=object(),
    )


def _context(**overrides: Any):
    from app.assistant.capabilities.contracts import CapabilityExecutionContext
    from uuid import UUID

    payload = {
        "call_id": "call-wf-1",
        "run_id": UUID(int=1),
        "conversation_id": UUID(int=2),
        "locale": "en",
        "request_source": "unit-test",
        "request_channel": "cli",
        "request_session": "session-1",
        "request_tool": "tool-1",
        "nesting_depth": 0,
    }
    payload.update(overrides)
    return CapabilityExecutionContext(**payload)


def _ports(cancelled: bool = False):
    from app.assistant.capabilities.ports import CapabilityRuntimePorts

    cancel = _FakeCancellation(cancelled=cancelled)
    sink = _RecordingEventSink()
    return CapabilityRuntimePorts(cancellation=cancel, events=sink), cancel, sink


def _resolve_target(db, frozen):
    from app.assistant.capabilities.registry import CapabilityRegistry

    return CapabilityRegistry(db).resolve(frozen)


# ---------------------------------------------------------------------------
# Helpers unit tests
# ---------------------------------------------------------------------------


def test_build_workflow_runtime_definition_uses_frozen_version_id() -> None:
    from app.assistant.capabilities.adapters.workflow import build_workflow_runtime_definition
    from app.assistant_config.schemas import WorkflowEdgeInput, WorkflowInput, WorkflowNodeInput

    workflow_id = uuid4()
    version_id = uuid4()
    published = WorkflowInput(
        nodes=[
            WorkflowNodeInput(
                node_id="start",
                node_type="start",
                label="S",
                position_x=0,
                position_y=0,
                config={"input_mode": "text"},
            ),
            WorkflowNodeInput(
                node_id="end",
                node_type="output",
                label="E",
                position_x=1,
                position_y=0,
                config={"output_mode": "text", "text_template": "{{start.user_input}}"},
            ),
        ],
        edges=[
            WorkflowEdgeInput(
                edge_id="e1",
                source_node_id="start",
                target_node_id="end",
            )
        ],
    )
    skill = build_workflow_runtime_definition(
        workflow_id=workflow_id,
        version_id=version_id,
        name="demo",
        description="d",
        published_input=published,
    )
    assert skill.workflow_id == str(workflow_id)
    assert skill.workflow_version_id == str(version_id)
    assert skill.langgraph_pattern == "workflow_dag"


def test_normalize_workflow_output_object_and_empty() -> None:
    from app.assistant.capabilities.adapters.workflow import normalize_workflow_output_value

    obj_schema = {
        "type": "object",
        "properties": {"response": {"type": "string"}},
        "required": ["response"],
    }
    assert normalize_workflow_output_value(
        '{"response":"hi"}', output_schema=obj_schema
    ) == {"response": "hi"}
    # Canonical Plan 01 text-output envelope wraps plain engine stream text.
    assert normalize_workflow_output_value(
        "plain",
        output_schema={
            "type": "object",
            "properties": {"response": {"type": "string"}},
            "required": ["response"],
            "additionalProperties": False,
        },
    ) == {"response": "plain"}
    with pytest.raises(ValueError):
        normalize_workflow_output_value("", output_schema=obj_schema)
    with pytest.raises(ValueError):
        normalize_workflow_output_value(
            "not-json",
            output_schema={
                "type": "object",
                "properties": {"a": {"type": "number"}},
                "required": ["a"],
            },
        )
    # string schema preserves string
    assert (
        normalize_workflow_output_value(
            "plain",
            output_schema={"type": "string"},
        )
        == "plain"
    )


# ---------------------------------------------------------------------------
# Exact version execution
# ---------------------------------------------------------------------------


def test_adapter_executes_exact_v1_not_draft_or_later_publish(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.workflow import WorkflowCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant_config.models import AssistantWorkflow, AssistantWorkflowVersion
    from app.assistant_config.service import AssistantConfigService
    from app.assistant_config.registry import ToolRegistry
    from app.ai_registry import runtime as ai_runtime
    from app.assistant.domain.digests import sha256_canonical_json

    workflow, version, frozen = _freeze_workflow(db, name="wf_exact_v1")
    # Draft V2 (version_source=save) and later published V3 must not redirect.
    draft = AssistantWorkflowVersion(
        workflow_id=workflow.id,
        sequence_no=2,
        version_name="draft-v2",
        version_source="save",
        snapshot=_pure_snapshot(output_template="DRAFT"),
    )
    db.add(draft)
    db.flush()
    workflow.draft_version_id = draft.id
    later = AssistantWorkflowVersion(
        workflow_id=workflow.id,
        sequence_no=3,
        version_name="v3",
        version_source="publish",
        snapshot=_pure_snapshot(output_template="V3"),
    )
    db.add(later)
    db.flush()
    workflow.published_version_id = later.id
    db.commit()

    # Tripwires
    monkeypatch.setattr(
        AssistantConfigService,
        "_get_workflow_published_input",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call latest published helper")),
    )
    monkeypatch.setattr(
        ToolRegistry,
        "resolve",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ToolRegistry.resolve under capability scope")),
    )
    monkeypatch.setattr(
        ai_runtime,
        "resolve_openai_compat_config",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("resolve_openai_compat_config under scope")),
    )

    target = _resolve_target(db, frozen)
    assert target.executable.version_id == version.id
    assert target.executable.snapshot_digest == sha256_canonical_json(version.snapshot)

    ports, _cancel, sink = _ports()
    request = CapabilityAdapterRequest(
        target=target,
        validated_input={"user_input": "hello-v1"},
        context=_context(),
        decision=_decision(),
    )
    result = WorkflowCapabilityAdapter().execute(request, ports=ports)
    assert result.status == "completed"
    assert result.structured_output == {"response": "hello-v1"}

    # Events include digests and never raw payloads.
    event_types = [e.event_type for e in sink.events]
    assert "capability.started" in event_types
    assert "capability.completed" in event_types
    for event in sink.events:
        blob = json.dumps(event.model_dump(mode="json"), default=str)
        assert "hello-v1" not in blob or event.event_type in {
            # structured output is only in CapabilityResult, not events
        }
        if event.metadata.binding_contract_digest:
            assert event.metadata.binding_contract_digest == target.descriptor.binding_contract_digest
        if event.metadata.dependency_closure_digest:
            assert (
                event.metadata.dependency_closure_digest
                == target.descriptor.dependency_closure_digest
            )
        assert "DRAFT" not in blob
        assert "V3" not in blob


def test_adapter_structured_input_and_context_propagation(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.workflow import WorkflowCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.workflow.engine.engine import LangGraphEngine

    workflow, version, frozen = _freeze_workflow(
        db,
        name="wf_struct",
        snapshot=_pure_snapshot(
            input_mode="structured",
            structured_fields=[{"name": "query", "type": "string", "required": True}],
            output_template="{{start.query}}",
        ),
    )
    target = _resolve_target(db, frozen)

    captured: dict[str, Any] = {}
    original_execute = LangGraphEngine.execute

    def _spy(self, *args, **kwargs):  # noqa: ANN001
        captured["runtime_context"] = kwargs.get("runtime_context")
        captured["execution_scope"] = self.execution_scope
        captured["skill"] = kwargs.get("skill") or (args[0] if args else None)
        yield from original_execute(self, *args, **kwargs)

    monkeypatch.setattr(LangGraphEngine, "execute", _spy)

    ports, _cancel, sink = _ports()
    request = CapabilityAdapterRequest(
        target=target,
        validated_input={"query": "structured-q"},
        context=_context(locale="zh", request_source="unit", request_channel="cli"),
        decision=_decision(),
    )
    result = WorkflowCapabilityAdapter().execute(request, ports=ports)
    assert result.status == "completed"
    assert result.structured_output == {"response": "structured-q"}

    ctx = captured["runtime_context"]
    assert ctx["structured_input"] == {"query": "structured-q"}
    assert ctx["channel_type"] == "capability_runtime"
    assert ctx["workflow_id"] == str(workflow.id)
    assert ctx["workflow_version_id"] == str(version.id)
    assert ctx["locale"] == "zh"
    assert ctx["request_source"] == "unit"
    assert ctx["request_channel"] == "cli"
    assert captured["execution_scope"] is not None
    assert captured["execution_scope"].allow_ambient_memory is False
    assert captured["execution_scope"].allow_global_graph_cache is False
    assert captured["execution_scope"].safe_diagnostics is True
    assert captured["skill"].workflow_version_id == str(version.id)


def test_cancelled_before_engine_creation(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.workflow import WorkflowCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.workflow.engine.engine import LangGraphEngine

    _workflow, _version, frozen = _freeze_workflow(db, name="wf_cancel")
    target = _resolve_target(db, frozen)

    def _boom(*_a, **_k):  # noqa: ANN001
        raise AssertionError("engine must not be constructed when already cancelled")

    monkeypatch.setattr(LangGraphEngine, "__init__", _boom)

    ports, cancel, sink = _ports(cancelled=True)
    request = CapabilityAdapterRequest(
        target=target,
        validated_input={"user_input": "x"},
        context=_context(),
        decision=_decision(),
    )
    result = WorkflowCapabilityAdapter().execute(request, ports=ports)
    assert result.status == "cancelled"
    assert any(e.event_type == "capability.cancelled" for e in sink.events)
    _ = cancel


def test_human_loop_non_openclaw_is_unsupported_interrupt(db) -> None:
    from app.assistant.capabilities.adapters.workflow import WorkflowCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest

    _workflow, _version, frozen = _freeze_workflow(
        db,
        name="wf_hitl",
        snapshot=_pure_snapshot(human=True),
    )
    target = _resolve_target(db, frozen)
    # Force descriptor interrupt mode for the adapter gate (classification may already set it).
    if target.descriptor.behavior.interrupt_mode == "none":
        # Classification may mark human graphs as legacy_blocking; if not, still assert gate.
        from app.assistant.capabilities.contracts import CapabilityBehavior

        patched = target.descriptor.model_copy(
            update={
                "behavior": target.descriptor.behavior.model_copy(
                    update={"interrupt_mode": "legacy_blocking"}
                )
            }
        )
        target = target.__class__(
            descriptor=patched,
            binding=target.binding,
            executable=target.executable,
            execution_closure=target.execution_closure,
        )

    ports, _cancel, sink = _ports()
    request = CapabilityAdapterRequest(
        target=target,
        validated_input={"user_input": "x"},
        context=_context(request_source="main_agent"),
        decision=_decision(owner_kind="test"),
    )
    result = WorkflowCapabilityAdapter().execute(request, ports=ports)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "unsupported_interrupt"
    assert result.continuation is None
    assert result.status != "waiting"


def test_invalid_output_fails_safely(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.workflow import WorkflowCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.workflow.engine.engine import LangGraphEngine

    _workflow, _version, frozen = _freeze_workflow(db, name="wf_bad_out")
    target = _resolve_target(db, frozen)
    # Use a non-canonical object schema so plain text is not auto-wrapped.
    patched_desc = target.descriptor.model_copy(
        update={
            "output_schema": {
                "type": "object",
                "properties": {"score": {"type": "number"}},
                "required": ["score"],
            },
            "output_schema_digest": "f" * 64,
        }
    )
    target = target.__class__(
        descriptor=patched_desc,
        binding=target.binding,
        executable=target.executable,
        execution_closure=target.execution_closure,
    )

    def _bad_execute(self, *args, **kwargs):  # noqa: ANN001
        yield "not-a-json-object"

    monkeypatch.setattr(LangGraphEngine, "execute", _bad_execute)

    ports, _cancel, sink = _ports()
    request = CapabilityAdapterRequest(
        target=target,
        validated_input={"user_input": "x"},
        context=_context(),
        decision=_decision(),
    )
    result = WorkflowCapabilityAdapter().execute(request, ports=ports)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "invalid_output"
    for event in sink.events:
        assert "not-a-json-object" not in json.dumps(event.model_dump(mode="json"), default=str)


def test_secret_exception_never_leaks_to_events_or_logs(db, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    from app.assistant.capabilities.adapters.workflow import WorkflowCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.workflow.engine.engine import LangGraphEngine

    _workflow, _version, frozen = _freeze_workflow(db, name="wf_secret")
    target = _resolve_target(db, frozen)
    secret = "SUPER_SECRET_API_KEY_xyz"

    def _explode(self, *args, **kwargs):  # noqa: ANN001
        raise RuntimeError(f"provider failed with {secret}")
        yield  # pragma: no cover

    monkeypatch.setattr(LangGraphEngine, "execute", _explode)

    ports, _cancel, sink = _ports()
    request = CapabilityAdapterRequest(
        target=target,
        validated_input={"user_input": "x"},
        context=_context(),
        decision=_decision(),
    )
    with caplog.at_level(logging.INFO):
        result = WorkflowCapabilityAdapter().execute(request, ports=ports)
    assert result.status == "failed"
    assert result.error is not None
    assert secret not in (result.error.safe_message or "")
    assert secret not in (result.error.safe_code or "")
    for event in sink.events:
        blob = json.dumps(event.model_dump(mode="json"), default=str)
        assert secret not in blob
    for record in caplog.records:
        assert secret not in record.getMessage()


def test_ambient_memory_disabled_even_with_conversation_uuid(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.workflow import WorkflowCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant import memory_service as mem_mod

    _workflow, _version, frozen = _freeze_workflow(db, name="wf_nomem")
    target = _resolve_target(db, frozen)

    def _boom(*_a, **_k):  # noqa: ANN001
        raise AssertionError("AssistantMemoryService must not be used in capability mode")

    monkeypatch.setattr(mem_mod.AssistantMemoryService, "get_l1_summary", _boom)
    monkeypatch.setattr(mem_mod.AssistantMemoryService, "get_l2_facts", _boom)
    monkeypatch.setattr(mem_mod.AssistantMemoryService, "get_workflow_call_memory", _boom)
    monkeypatch.setattr(mem_mod.AssistantMemoryService, "upsert_workflow_call_memory", _boom)

    ports, _cancel, _sink = _ports()
    request = CapabilityAdapterRequest(
        target=target,
        validated_input={"user_input": "memory-off"},
        context=_context(),
        decision=_decision(),
    )
    result = WorkflowCapabilityAdapter().execute(request, ports=ports)
    assert result.status == "completed"
    assert result.structured_output == {"response": "memory-off"}


def test_graph_cache_bypassed_across_scope_revisions(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.workflow import WorkflowCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.workflow.engine import workflow_graph_cache as cache_mod

    calls: list[Any] = []

    original = cache_mod.get_or_compile_graph

    def _spy(key, compile_fn):  # noqa: ANN001
        calls.append(key)
        return original(key, compile_fn)

    monkeypatch.setattr(cache_mod, "get_or_compile_graph", _spy)

    _workflow, _version, frozen = _freeze_workflow(db, name="wf_cache")
    target = _resolve_target(db, frozen)
    ports, _cancel, _sink = _ports()
    request = CapabilityAdapterRequest(
        target=target,
        validated_input={"user_input": "c1"},
        context=_context(call_id="call-cache-1"),
        decision=_decision(call_id="call-cache-1"),
    )
    result1 = WorkflowCapabilityAdapter().execute(request, ports=ports)
    assert result1.status == "completed"

    ports2, _c2, _s2 = _ports()
    request2 = CapabilityAdapterRequest(
        target=target,
        validated_input={"user_input": "c2"},
        context=_context(call_id="call-cache-2"),
        decision=_decision(call_id="call-cache-2"),
    )
    result2 = WorkflowCapabilityAdapter().execute(request2, ports=ports2)
    assert result2.status == "completed"
    # Capability mode must not consult the global graph cache.
    assert calls == []


def test_unavailable_descriptor_never_invokes_engine(db, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.capabilities.adapters.workflow import WorkflowCapabilityAdapter
    from app.assistant.capabilities.ports import CapabilityAdapterRequest
    from app.assistant.workflow.engine.engine import LangGraphEngine

    _workflow, _version, frozen = _freeze_workflow(db, name="wf_disabled")
    target = _resolve_target(db, frozen)
    disabled = target.descriptor.model_copy(
        update={
            "availability": target.descriptor.availability.model_copy(
                update={"status": "disabled", "reason_code": "workflow_disabled"}
            )
        }
    )
    target = target.__class__(
        descriptor=disabled,
        binding=target.binding,
        executable=target.executable,
        execution_closure=target.execution_closure,
    )

    monkeypatch.setattr(
        LangGraphEngine,
        "__init__",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not build engine")),
    )
    ports, _cancel, sink = _ports()
    result = WorkflowCapabilityAdapter().execute(
        CapabilityAdapterRequest(
            target=target,
            validated_input={"user_input": "x"},
            context=_context(),
            decision=_decision(),
        ),
        ports=ports,
    )
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.error_type == "unavailable"
    assert any(e.event_type == "capability.failed" for e in sink.events)

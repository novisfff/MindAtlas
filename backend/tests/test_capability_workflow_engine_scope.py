"""Workflow engine execution_scope propagation tests (Plan 02 Task 5)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_BUILD_REVISION", "test-build-c25d03f")
os.environ.setdefault("APP_ENV", "test")


@dataclass
class _FakeResolver:
    tools: dict[str, Any]
    workflows: dict[str, Any]
    models: dict[str, Any]
    tool_calls: list[tuple[str, str]]
    workflow_calls: list[tuple[str, str, str]]
    model_calls: list[tuple[str, Any]]

    def require_tool(self, *, source_locator: str, tool_name: str) -> Any:
        self.tool_calls.append((source_locator, tool_name))
        if source_locator not in self.tools and tool_name not in {
            getattr(v, "name", None) for v in self.tools.values()
        }:
            # also allow lookup by tool name across values
            for key, value in self.tools.items():
                if key.endswith(f"/tool:{tool_name}") or getattr(value, "name", None) == tool_name:
                    return SimpleNamespace(tool_object_or_record=value)
            raise KeyError(source_locator)
        value = self.tools.get(source_locator)
        if value is None:
            raise KeyError(source_locator)
        return SimpleNamespace(tool_object_or_record=value)

    def require_workflow_version(
        self,
        *,
        source_locator: str,
        workflow_id,
        version_id,
    ) -> Any:
        self.workflow_calls.append((source_locator, str(workflow_id), str(version_id)))
        key = source_locator
        if key not in self.workflows:
            raise KeyError(source_locator)
        return self.workflows[key]

    def require_model(self, *, source_locator: str, requested_model_id) -> Any:
        self.model_calls.append((source_locator, requested_model_id))
        if source_locator not in self.models:
            raise KeyError(source_locator)
        return self.models[source_locator]


def _scope(resolver: _FakeResolver, *, depth: int = 0):
    from app.assistant.workflow.engine.runtime_dependency_resolver import (
        WorkflowEngineExecutionScope,
    )

    return WorkflowEngineExecutionScope(
        dependency_resolver=resolver,
        binding_contract_digest="b" * 64,
        dependency_closure_digest="c" * 64,
        nesting_depth=depth,
        safe_diagnostics=True,
        allow_ambient_memory=False,
        allow_global_graph_cache=False,
    )


def test_workflow_node_builder_deps_carries_execution_scope() -> None:
    from app.assistant.workflow.engine.workflow_dag_assembler import WorkflowNodeBuilderDeps
    from app.assistant.workflow.engine.runtime_dependency_resolver import (
        WorkflowEngineExecutionScope,
    )

    fields = {f.name for f in WorkflowNodeBuilderDeps.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    assert "execution_scope" in fields
    # Default documentation: scope is explicit and may be None for Legacy.
    assert WorkflowEngineExecutionScope.__dataclass_fields__["allow_ambient_memory"].default is False
    assert WorkflowEngineExecutionScope.__dataclass_fields__["allow_global_graph_cache"].default is False


def test_engine_scope_none_uses_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.workflow.engine.engine import LangGraphEngine
    from app.assistant_config.registry import ToolRegistry

    calls: list[str] = []

    def _resolve(self, name):  # noqa: ANN001
        calls.append(name)
        return SimpleNamespace(name=name)

    monkeypatch.setattr(ToolRegistry, "resolve", _resolve)
    engine = LangGraphEngine(api_key="k", base_url="http://x", model="m", db=SimpleNamespace(get_bind=lambda: None))
    # Force db path
    engine.db = SimpleNamespace(get_bind=lambda: None)
    tool = engine._get_tool("search_entries")
    assert tool is not None
    assert calls == ["search_entries"]


def test_engine_scope_active_never_calls_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.workflow.engine.engine import LangGraphEngine
    from app.assistant_config.registry import ToolRegistry

    tool_obj = SimpleNamespace(name="search_entries")
    resolver = _FakeResolver(
        tools={"root/tool:search_entries": tool_obj},
        workflows={},
        models={},
        tool_calls=[],
        workflow_calls=[],
        model_calls=[],
    )
    scope = _scope(resolver)

    def _boom(*_a, **_k):  # noqa: ANN001
        raise AssertionError("ToolRegistry.resolve must not be called under capability scope")

    monkeypatch.setattr(ToolRegistry, "resolve", _boom)
    monkeypatch.setattr(ToolRegistry, "resolve_system_tool", _boom)

    engine = LangGraphEngine(
        api_key="k",
        base_url="http://x",
        model="m",
        db=None,
        execution_scope=scope,
    )
    tool = engine._get_tool("search_entries", source_locator="root/tool:search_entries")
    assert tool is tool_obj
    assert resolver.tool_calls == [("root/tool:search_entries", "search_entries")]


def test_engine_skips_ambient_memory_when_scope_disallows(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.workflow.engine.engine import LangGraphEngine
    from app.assistant import memory_service as mem_mod

    resolver = _FakeResolver(tools={}, workflows={}, models={}, tool_calls=[], workflow_calls=[], model_calls=[])
    scope = _scope(resolver)
    engine = LangGraphEngine(
        api_key="k",
        base_url="http://x",
        model="m",
        db=object(),
        execution_scope=scope,
    )

    def _boom(*_a, **_k):  # noqa: ANN001
        raise AssertionError("memory must not be read under capability scope")

    monkeypatch.setattr(mem_mod.AssistantMemoryService, "get_l1_summary", _boom)
    monkeypatch.setattr(mem_mod.AssistantMemoryService, "get_l2_facts", _boom)

    assert engine._load_l1_summary(conversation_id_uuid=uuid4()) == ""
    text, facts = engine._load_l2_text(conversation_id_uuid=uuid4(), skill_name="x")
    assert text == ""
    assert facts == []
    assert engine._load_runtime_memory_overrides(raw_context={"session_memory": {"conversation_summary": "S"}}) == (
        None,
        None,
        {},
    )


def test_engine_legacy_null_scope_still_loads_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.workflow.engine.engine import LangGraphEngine
    from app.assistant import memory_service as mem_mod

    engine = LangGraphEngine(api_key="k", base_url="http://x", model="m", db=object(), execution_scope=None)
    called: list[str] = []

    def _l1(self, conversation_id):  # noqa: ANN001
        called.append("l1")
        return "summary"

    def _truncate(summary, max_chars=2000):  # noqa: ANN001
        return summary

    monkeypatch.setattr(mem_mod.AssistantMemoryService, "get_l1_summary", _l1)
    monkeypatch.setattr(mem_mod.AssistantMemoryService, "truncate_summary", staticmethod(_truncate))
    assert engine._load_l1_summary(conversation_id_uuid=uuid4()) == "summary"
    assert called == ["l1"]


def test_assembler_passes_scope_into_tool_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.workflow.engine.workflow_dag_assembler import (
        WorkflowNodeBuilderDeps,
        _build_workflow_node_fn,
    )

    seen: dict[str, Any] = {}

    def _build_tool(node_id, node_cfg, tool_map, args_llm, db_bind, execution_scope=None):  # noqa: ANN001
        seen["execution_scope"] = execution_scope
        seen["node_id"] = node_id
        return lambda state: {"node_outputs": {}}

    resolver = _FakeResolver(tools={}, workflows={}, models={}, tool_calls=[], workflow_calls=[], model_calls=[])
    scope = _scope(resolver)
    deps = WorkflowNodeBuilderDeps(
        llm=SimpleNamespace(),
        args_llm=SimpleNamespace(),
        tool_map={},
        db_bind=None,
        node_llms=None,
        execution_scope=scope,
        build_start_node=lambda cfg: (lambda s: {}),
        build_dag_llm_node=lambda *a, **k: (lambda s: {}),
        build_dag_agent_node=lambda *a, **k: (lambda s: {}),
        build_output_node=lambda *a, **k: (lambda s: {}),
        build_dag_tool_node=_build_tool,
        build_code_executor_node=lambda *a, **k: (lambda s: {}),
        build_http_request_node=lambda *a, **k: (lambda s: {}),
        build_variable_assign_node=lambda *a, **k: (lambda s: {}),
        build_human_in_loop_node=lambda *a, **k: (lambda s: {}),
        build_workflow_call_node=lambda *a, **k: (lambda s: {}),
        build_if_else_node=lambda *a, **k: (lambda s: {}),
        build_param_extractor_node=lambda *a, **k: (lambda s: {}),
        build_kr_node=lambda *a, **k: (lambda s: {}),
        build_iteration_node=lambda *a, **k: (lambda s: {}),
        build_loop_node=lambda *a, **k: (lambda s: {}),
    )
    fn = _build_workflow_node_fn(
        node_id="tool_0",
        node_type="tool",
        node_cfg={"tool_name": "x"},
        deps=deps,
    )
    assert callable(fn)
    assert seen["execution_scope"] is scope
    assert seen["node_id"] == "tool_0"


def test_max_capability_nesting_depth_constant() -> None:
    from app.assistant.workflow.engine.runtime_dependency_resolver import (
        MAX_CAPABILITY_NESTING_DEPTH,
    )

    assert MAX_CAPABILITY_NESTING_DEPTH == 4


def test_workflow_call_depth_five_denied() -> None:
    from app.assistant.workflow.engine.node_builders.workflow_call_node import (
        build_workflow_call_node,
    )

    resolver = _FakeResolver(tools={}, workflows={}, models={}, tool_calls=[], workflow_calls=[], model_calls=[])
    scope = _scope(resolver, depth=4)  # next child would be 5
    node = build_workflow_call_node(
        "call_0",
        {
            "target_workflow_id": str(uuid4()),
            "target_published_version_id": str(uuid4()),
            "binding_mode": "pinned",
            "input_bindings": {"user_input": "x"},
        },
        llm=SimpleNamespace(),
        args_llm=SimpleNamespace(),
        tool_map={},
        db_bind=None,
        execution_scope=scope,
    )
    with pytest.raises(RuntimeError, match="nesting depth denied"):
        node(
            {
                "metadata": {},
                "node_outputs": {},
                "sys_vars": {},
                "env_vars": {},
                "memory_context": {},
            }
        )


def test_scope_absent_from_serializable_state_keys() -> None:
    """Regression: scope must not be a WorkflowState field name."""
    from app.assistant.workflow.engine import state as state_mod
    import typing

    # WorkflowState is a TypedDict-like structure; ensure no execution_scope key is declared.
    annotations = getattr(state_mod.WorkflowState, "__annotations__", {})
    assert "execution_scope" not in annotations
    assert "dependency_resolver" not in annotations


def test_body_tool_resolves_container_frozen_locator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Body tool under iteration must resolve root/node:{container}/body/node:{child}/tool:{name}."""
    from app.assistant.workflow.engine.node_builders.tool_node import build_dag_tool_node

    tool_obj = SimpleNamespace(name="search_entries")
    body_locator = "root/node:iter_0/body/node:tool_body/tool:search_entries"
    resolver = _FakeResolver(
        tools={body_locator: tool_obj},
        workflows={},
        models={},
        tool_calls=[],
        workflow_calls=[],
        model_calls=[],
    )
    scope = _scope(resolver)

    # Avoid real tool execution; only assert resolve path.
    monkeypatch.setattr(
        "app.assistant.workflow.engine.engine._wrap_tool_with_db",
        lambda tool, _db: (lambda **_kwargs: {"ok": True}),
    )
    monkeypatch.setattr(
        "app.assistant.workflow.engine.engine._coerce_tool_args",
        lambda _tool, args: dict(args or {}),
    )
    monkeypatch.setattr(
        "app.assistant.workflow.engine.engine._resolve_tool_output_param_names",
        lambda *_a, **_k: [],
    )

    node = build_dag_tool_node(
        "tool_body",
        {
            "tool_name": "search_entries",
            "input_bindings": {},
            "__container_node_id": "iter_0",
        },
        tool_map={},
        args_llm=SimpleNamespace(),
        db_bind=None,
        execution_scope=scope,
        container_node_id="iter_0",
    )
    result = node(
        {
            "metadata": {},
            "node_outputs": {},
            "sys_vars": {},
            "env_vars": {},
        }
    )
    assert "tool_body" in result["node_outputs"]
    assert resolver.tool_calls
    assert resolver.tool_calls[0][0] == body_locator
    assert resolver.tool_calls[0][1] == "search_entries"


def test_nested_workflow_call_resolves_container_locator() -> None:
    """Nested workflow_call under container must try root/workflow_call:{container}::{node}."""
    from app.assistant.workflow.engine.node_builders.workflow_call_node import (
        build_workflow_call_node,
    )

    child_wf = uuid4()
    child_ver = uuid4()
    locator = f"root/workflow_call:iter_0::call_body"
    published = SimpleNamespace(
        nodes=[],
        edges=[],
    )
    # Minimal contract surface via workflow_contract_from_input is exercised later;
    # force require_workflow_version hit and fail on missing published contract fields.
    resolver = _FakeResolver(
        tools={},
        workflows={
            locator: SimpleNamespace(parsed_published_input=None),
        },
        models={},
        tool_calls=[],
        workflow_calls=[],
        model_calls=[],
    )
    scope = _scope(resolver)
    node = build_workflow_call_node(
        "call_body",
        {
            "target_workflow_id": str(child_wf),
            "target_published_version_id": str(child_ver),
            "binding_mode": "pinned",
            "input_bindings": {"user_input": "x"},
            "__container_node_id": "iter_0",
        },
        llm=SimpleNamespace(),
        args_llm=SimpleNamespace(),
        tool_map={},
        db_bind=None,
        execution_scope=scope,
        container_node_id="iter_0",
    )
    with pytest.raises(RuntimeError, match="published input missing"):
        node(
            {
                "metadata": {},
                "node_outputs": {},
                "sys_vars": {},
                "env_vars": {},
                "memory_context": {},
            }
        )
    assert resolver.workflow_calls
    assert resolver.workflow_calls[0][0] == locator


def test_http_request_safe_diagnostics_hides_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.assistant.workflow.engine.node_builders.http_request_node import (
        build_http_request_node,
    )

    resolver = _FakeResolver(tools={}, workflows={}, models={}, tool_calls=[], workflow_calls=[], model_calls=[])
    scope = _scope(resolver)

    def _boom(**_kwargs):  # noqa: ANN001
        raise ConnectionError("secret host 10.0.0.5 refused token=abc")

    monkeypatch.setattr(
        "app.assistant.workflow.engine.node_builders.http_request_node.execute_http_request",
        _boom,
    )
    node = build_http_request_node(
        "http_0",
        {"method": "GET", "url": "https://example.invalid/x"},
        execution_scope=scope,
    )
    with pytest.raises(RuntimeError) as exc_info:
        node({"metadata": {}, "node_outputs": {}, "sys_vars": {}, "env_vars": {}})
    message = str(exc_info.value)
    assert "http_request failed" in message
    assert "10.0.0.5" not in message
    assert "token=abc" not in message


def test_dag_agent_resolves_body_tool_and_skips_ambient_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.assistant.workflow.engine.node_builders.dag_agent_node import build_dag_agent_node
    from app.assistant.workflow.engine.agent_execution_core import AgentExecutionResult

    tool_obj = SimpleNamespace(name="search_entries")
    body_locator = "root/node:iter_0/body/node:agent_body/tool:search_entries"
    resolver = _FakeResolver(
        tools={body_locator: tool_obj},
        workflows={},
        models={},
        tool_calls=[],
        workflow_calls=[],
        model_calls=[],
    )
    scope = _scope(resolver)

    captured: dict[str, Any] = {}

    def _run(request):  # noqa: ANN001
        captured["recent_dialogue_injection"] = request.recent_dialogue_injection
        captured["conversation_messages"] = list(request.conversation_messages)
        captured["bound_tools"] = list(request.bound_tools)
        return AgentExecutionResult(
            final_text="ok",
            round_count=1,
            used_tools=[],
            stopped_by="final_answer",
        )

    monkeypatch.setattr(
        "app.assistant.workflow.engine.node_builders.dag_agent_node.run_agent_execution",
        _run,
    )
    monkeypatch.setattr(
        "app.assistant.workflow.engine.engine._wrap_tool_with_db",
        lambda tool, _db: (lambda **_kwargs: "ok"),
    )

    node = build_dag_agent_node(
        "agent_body",
        {
            "tool_names": ["search_entries"],
            "system_prompt": "do it",
            "user_input": "hello",
            "__container_node_id": "iter_0",
        },
        llm=SimpleNamespace(),
        tool_map={},
        db_bind=None,
        execution_scope=scope,
        container_node_id="iter_0",
    )
    result = node(
        {
            "metadata": {},
            "node_outputs": {},
            "sys_vars": {},
            "env_vars": {},
            "memory_mode": "auto",
            "memory_context": {
                "l0_messages": [{"role": "user", "content": "secret prior"}],
                "l1_text": "should not inject",
                "l2_facts": ["secret fact"],
            },
            "stream_output_enabled": False,
            "output_stream_source_node_id": "",
        }
    )
    assert result["node_outputs"]["agent_body"]["text"] == "ok"
    assert resolver.tool_calls
    assert resolver.tool_calls[0][0] == body_locator
    assert captured["recent_dialogue_injection"] == "none"
    # Ambient L0 must not be injected into conversation under capability scope.
    roles = [m.get("role") for m in captured["conversation_messages"] if isinstance(m, dict)]
    assert "assistant" not in roles or all(
        (m.get("content") != "secret prior")
        for m in captured["conversation_messages"]
        if isinstance(m, dict)
    )
    assert all(
        "secret prior" not in str(m.get("content", ""))
        for m in captured["conversation_messages"]
        if isinstance(m, dict)
    )
    assert captured["bound_tools"]

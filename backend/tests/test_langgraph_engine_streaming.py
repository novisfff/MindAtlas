from __future__ import annotations

import json
import unittest
from uuid import uuid4
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class LangGraphEngineStreamingTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_agent_node_streaming_emits_content_without_invoke(self) -> None:
        from app.assistant.skill_catalog.base import SkillDefinition
        from app.assistant.workflow.engine.engine import _build_agent_node

        skill = SkillDefinition(
            name="s",
            description="d",
            intent_examples=[],
            tools=[],
            mode="langgraph",
            langgraph_pattern="agent_loop",
        )

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content
                self.tool_calls = []
                self.additional_kwargs = {}
                self.response_metadata = {}

            def __add__(self, other: "_Chunk") -> "_Chunk":
                out = _Chunk((self.content or "") + (other.content or ""))
                out.tool_calls = list(self.tool_calls or []) + list(other.tool_calls or [])
                return out

        class _LLMWithTools:
            def stream(self, _messages):
                yield _Chunk("你")
                yield _Chunk("好")

        class _LLM:
            def bind_tools(self, _tools, parallel_tool_calls=False):
                return _LLMWithTools()

        emitted: list[str] = []
        node = _build_agent_node(skill, _LLM(), tools=[object()])
        out = node(
            {
                "messages": [],
                "metadata": {"on_content_delta": lambda chunk: emitted.append(chunk)},
                "iteration_count": 0,
            }
        )

        self.assertEqual(emitted, ["你", "好"])
        self.assertEqual(out["messages"][0].content, "你好")

    def test_execute_stream_forwards_runtime_content_delta_immediately(self) -> None:
        from app.assistant.skill_catalog.base import SkillDefinition
        from app.assistant.workflow.engine.engine import LangGraphEngine

        class _FakeChunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _FakeLLM:
            def stream(self, _messages):
                yield _FakeChunk("A")
                yield _FakeChunk("B")

        class _FakeCompiled:
            def stream(self, state):
                node = state["metadata"].get("on_content_delta")
                if callable(node):
                    node("A")
                    node("B")
                yield {"step": 1}

        skill = SkillDefinition(
            name="s",
            description="d",
            intent_examples=[],
            tools=[],
            mode="langgraph",
            langgraph_pattern="agent_loop",
            system_prompt="sys",
        )

        with patch("app.assistant.workflow.engine.engine.ChatOpenAI", return_value=_FakeLLM()):
            engine = LangGraphEngine(api_key="k", base_url="https://x", model="m", db=None)

        with patch(
            "app.assistant.workflow.engine.engine._get_or_compile_graph",
            return_value=_FakeCompiled(),
        ):
            out = list(engine.execute(skill=skill, user_input="u", history=[]))

        tokens = [x for x in out if x]
        self.assertGreaterEqual(len(tokens), 2)
        self.assertEqual(tokens[0], "A")
        self.assertEqual(tokens[1], "B")

    def test_workflow_llm_node_without_stream_passthrough_does_not_emit_content_delta(self) -> None:
        from app.assistant.workflow.engine.engine import _build_dag_llm_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def stream(self, _messages):
                yield _Chunk("A")
                yield _Chunk("B")

        content_emitted: list[str] = []
        node_delta_emitted: list[str] = []

        node = _build_dag_llm_node(
            "llm_1",
            {
                "system_prompt": "reply",
                "output_mode": "text",
                "user_input": "{{start.user_input}}",
            },
            _LLM(),
        )

        out = node(
            {
                "user_input": "hello",
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                },
                "stream_output_enabled": True,
                "output_stream_source_node_id": "",
                "metadata": {
                    "on_content_delta": lambda chunk: content_emitted.append(chunk),
                    "on_node_output_delta": lambda node_id, delta: node_delta_emitted.append(delta),
                },
            }
        )

        self.assertEqual(content_emitted, [])
        self.assertEqual(node_delta_emitted, ["A", "B"])
        self.assertEqual(out["node_outputs"]["llm_1"]["text"], "AB")
        self.assertEqual(out["node_outputs"]["llm_1"]["json_fields"]["response"], "AB")

    def test_workflow_llm_node_with_stream_passthrough_emits_content_delta(self) -> None:
        from app.assistant.workflow.engine.engine import _build_dag_llm_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def stream(self, _messages):
                yield _Chunk("A")
                yield _Chunk("B")

        content_emitted: list[str] = []

        node = _build_dag_llm_node(
            "llm_1",
            {
                "system_prompt": "reply",
                "output_mode": "text",
                "user_input": "{{start.user_input}}",
            },
            _LLM(),
        )

        node(
            {
                "user_input": "hello",
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                },
                "stream_output_enabled": True,
                "output_stream_source_node_id": "llm_1",
                "metadata": {
                    "on_content_delta": lambda chunk: content_emitted.append(chunk),
                },
            }
        )

        self.assertEqual(content_emitted, ["A", "B"])

    def test_output_node_text_single_ref_skips_duplicate_emit_when_passthrough(self) -> None:
        from app.assistant.workflow.engine.engine import _build_output_node

        content_emitted: list[str] = []
        node = _build_output_node(
            "output_1",
            {
                "output_mode": "text",
                "text_template": "{{llm_1.response}}",
            },
        )

        out = node(
            {
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                    "llm_1": {"json_fields": {"response": "AB"}, "text": "AB", "status": "ok"},
                },
                "workflow_node_types": {"llm_1": "llm", "output_1": "output"},
                "stream_output_enabled": True,
                "output_stream_source_node_id": "llm_1",
                "metadata": {"on_content_delta": lambda chunk: content_emitted.append(chunk)},
            }
        )

        self.assertEqual(content_emitted, [])
        self.assertEqual(out["node_outputs"]["output_1"]["text"], "AB")
        self.assertEqual(out["node_outputs"]["output_1"]["json_fields"]["response"], "AB")

    def test_output_node_text_non_passthrough_emits_once(self) -> None:
        from app.assistant.workflow.engine.engine import _build_output_node

        content_emitted: list[str] = []
        node = _build_output_node(
            "output_1",
            {
                "output_mode": "text",
                "text_template": "Result: {{llm_1.response}}",
            },
        )

        out = node(
            {
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                    "llm_1": {"json_fields": {"response": "AB"}, "text": "AB", "status": "ok"},
                },
                "workflow_node_types": {"llm_1": "llm", "output_1": "output"},
                "stream_output_enabled": True,
                "output_stream_source_node_id": "llm_1",
                "metadata": {"on_content_delta": lambda chunk: content_emitted.append(chunk)},
            }
        )

        self.assertEqual(content_emitted, ["Result: AB"])
        self.assertEqual(out["node_outputs"]["output_1"]["text"], "Result: AB")

    def test_output_node_structured_emits_json_once(self) -> None:
        from app.assistant.workflow.engine.engine import _build_output_node

        content_emitted: list[str] = []
        node = _build_output_node(
            "output_1",
            {
                "output_mode": "structured",
                "output_fields": [
                    {"name": "answer", "type": "string", "value": "{{llm_1.response}}"},
                    {"name": "count", "type": "integer", "value": "2"},
                ],
            },
        )

        out = node(
            {
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                    "llm_1": {"json_fields": {"response": "AB"}, "text": "AB", "status": "ok"},
                },
                "workflow_node_types": {"llm_1": "llm", "output_1": "output"},
                "stream_output_enabled": True,
                "output_stream_source_node_id": "llm_1",
                "metadata": {"on_content_delta": lambda chunk: content_emitted.append(chunk)},
            }
        )

        self.assertEqual(len(content_emitted), 1)
        parsed = json.loads(content_emitted[0])
        self.assertEqual(parsed["answer"], "AB")
        self.assertEqual(parsed["count"], 2)
        self.assertEqual(out["node_outputs"]["output_1"]["json_fields"]["count"], 2)

    def test_stream_runtime_separates_output_segments_by_source_switch(self) -> None:
        from app.assistant.workflow.engine.stream_runtime import (
            RuntimeEventHandlers,
            dispatch_runtime_event,
        )

        buffered: list[str] = []
        segment_state = {"last_output_source_node_id": ""}
        handlers = RuntimeEventHandlers()

        _, out_a = dispatch_runtime_event(
            event_name="content_delta",
            payload={
                "chunk": "A",
                "source_node_id": "output_a",
                "source_node_type": "output",
            },
            handlers=handlers,
            stream_output_enabled=True,
            buffered_content_chunks=buffered,
            content_segment_state=segment_state,
        )
        _, out_b = dispatch_runtime_event(
            event_name="content_delta",
            payload={
                "chunk": "B",
                "source_node_id": "output_b",
                "source_node_type": "output",
            },
            handlers=handlers,
            stream_output_enabled=True,
            buffered_content_chunks=buffered,
            content_segment_state=segment_state,
        )

        self.assertEqual(out_a, ["A"])
        self.assertEqual(out_b, ["\n\n", "B"])

    def test_stream_runtime_buffers_output_separator_when_stream_disabled(self) -> None:
        from app.assistant.workflow.engine.stream_runtime import (
            RuntimeEventHandlers,
            dispatch_runtime_event,
        )

        buffered: list[str] = []
        segment_state = {"last_output_source_node_id": ""}
        handlers = RuntimeEventHandlers()

        dispatch_runtime_event(
            event_name="content_delta",
            payload={
                "chunk": "A",
                "source_node_id": "output_a",
                "source_node_type": "output",
            },
            handlers=handlers,
            stream_output_enabled=False,
            buffered_content_chunks=buffered,
            content_segment_state=segment_state,
        )
        dispatch_runtime_event(
            event_name="content_delta",
            payload={
                "chunk": "B",
                "source_node_id": "output_b",
                "source_node_type": "output",
            },
            handlers=handlers,
            stream_output_enabled=False,
            buffered_content_chunks=buffered,
            content_segment_state=segment_state,
        )

        self.assertEqual("".join(buffered), "A\n\nB")

    def test_output_field_integer_array_rejects_boolean_items(self) -> None:
        from app.assistant.workflow.engine.engine import _coerce_output_field_value

        with self.assertRaises(ValueError):
            _coerce_output_field_value(
                "values",
                "[1, true, 3]",
                {"type": "array", "itemsType": "integer"},
            )

    def test_workflow_llm_structured_retry_success(self) -> None:
        from app.assistant.workflow.engine.engine import _build_dag_llm_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def __init__(self) -> None:
                self.calls = 0

            def stream(self, _messages):
                self.calls += 1
                if self.calls == 1:
                    yield _Chunk("{bad json")
                    return
                yield _Chunk('{"title":"ok"}')

        llm = _LLM()
        content_emitted: list[str] = []

        node = _build_dag_llm_node(
            "llm_structured",
            {
                "system_prompt": "extract",
                "output_mode": "structured",
                "output_fields": [{"name": "title"}],
                "user_input": "{{start.user_input}}",
            },
            llm,
        )

        out = node(
            {
                "user_input": "hello",
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                },
                "metadata": {
                    "on_content_delta": lambda chunk: content_emitted.append(chunk),
                },
            }
        )

        self.assertEqual(llm.calls, 2)
        self.assertEqual(out["node_outputs"]["llm_structured"]["json_fields"]["title"], "ok")
        self.assertEqual(out["node_outputs"]["llm_structured"]["json_fields"]["response"], '{"title": "ok"}')
        self.assertEqual(content_emitted, ['{"title": "ok"}'])

    def test_workflow_llm_structured_retry_failure_raises(self) -> None:
        from app.assistant.workflow.engine.engine import _build_dag_llm_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def stream(self, _messages):
                yield _Chunk("{bad json")

        node = _build_dag_llm_node(
            "llm_structured",
            {
                "system_prompt": "extract",
                "output_mode": "structured",
                "output_fields": [{"name": "title"}],
                "user_input": "{{start.user_input}}",
            },
            _LLM(),
        )

        with self.assertRaises(RuntimeError):
            node(
                {
                    "user_input": "hello",
                    "node_outputs": {
                        "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                    },
                    "metadata": {},
                }
            )

    def test_workflow_tool_input_bindings_override_legacy_args(self) -> None:
        from app.assistant.workflow.engine.engine import _build_dag_tool_node

        captured_args: dict[str, str] = {}

        class _Tool:
            name = "fake_tool"

            @staticmethod
            def func(**kwargs):
                captured_args.update(kwargs)
                return {"echo": kwargs.get("question"), "ok": True}

        with patch(
            "app.assistant.workflow.engine.engine._wrap_tool_with_db",
            side_effect=lambda tool, _db_bind: (lambda **kwargs: tool.func(**kwargs)),
        ), patch(
            "app.assistant.workflow.engine.engine._resolve_tool_output_param_names",
            return_value=["echo"],
        ):
            node = _build_dag_tool_node(
                "tool_1",
                {
                    "tool_name": "fake_tool",
                    "input_bindings": {
                        "question": "Q: {{start.user_input}}",
                        "literal": "fixed",
                    },
                    "args_from": "custom",
                    "args_template": "legacy_should_not_be_used",
                },
                {"fake_tool": _Tool()},
                args_llm=object(),
                db_bind=object(),
            )

            out = node(
                {
                    "user_input": "hello",
                    "node_outputs": {
                        "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                    },
                    "metadata": {},
                }
            )

        self.assertEqual(captured_args["question"], "Q: hello")
        self.assertEqual(captured_args["literal"], "fixed")
        tool_out = out["node_outputs"]["tool_1"]
        self.assertEqual(tool_out["json_fields"]["echo"], "Q: hello")
        self.assertEqual(tool_out["json_fields"]["result"]["echo"], "Q: hello")

    def test_workflow_tool_without_declared_output_only_has_result(self) -> None:
        from app.assistant.workflow.engine.engine import _build_dag_tool_node

        class _Tool:
            name = "fake_tool"

            @staticmethod
            def func(**_kwargs):
                return {"foo": "bar"}

        with patch(
            "app.assistant.workflow.engine.engine._wrap_tool_with_db",
            side_effect=lambda tool, _db_bind: (lambda **kwargs: tool.func(**kwargs)),
        ), patch(
            "app.assistant.workflow.engine.engine._resolve_tool_output_param_names",
            return_value=[],
        ):
            node = _build_dag_tool_node(
                "tool_1",
                {"tool_name": "fake_tool", "input_bindings": {}},
                {"fake_tool": _Tool()},
                args_llm=object(),
                db_bind=object(),
            )
            out = node(
                {
                    "node_outputs": {
                        "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                    },
                    "metadata": {},
                }
            )

        self.assertEqual(set(out["node_outputs"]["tool_1"]["json_fields"].keys()), {"result"})

    def test_if_else_branch_logic_and_order_with_sys_vars(self) -> None:
        from app.assistant.workflow.engine.engine import _build_if_else_node

        node = _build_if_else_node(
            "if_1",
            {
                "branches": [
                    {
                        "id": "if_main",
                        "label": "IF",
                        "logic": "and",
                        "conditions": [
                            {
                                "id": "c1",
                                "variable": "start.user_input",
                                "operator": "contains",
                                "value": "hello",
                            },
                            {
                                "id": "c2",
                                "variable": "sys.conversation_id",
                                "operator": "is",
                                "value": "conv-1",
                            },
                        ],
                    },
                    {
                        "id": "elif_1",
                        "label": "ELIF 1",
                        "logic": "or",
                        "conditions": [
                            {
                                "id": "c3",
                                "variable": "start.user_input",
                                "operator": "starts_with",
                                "value": "bye",
                            },
                            {
                                "id": "c4",
                                "variable": "start.user_input",
                                "operator": "contains",
                                "value": "hello",
                            },
                        ],
                    },
                ],
                "else_handle": "else",
            },
        )

        out_if = node(
            {
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "Hello world"}, "text": "Hello world", "status": "ok"},
                },
                "sys_vars": {"conversation_id": "conv-1"},
                "metadata": {},
            }
        )
        self.assertEqual(out_if["branch_decisions"]["if_1"], "if_main")

        out_elif = node(
            {
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "hello world"}, "text": "hello world", "status": "ok"},
                },
                "sys_vars": {"conversation_id": "conv-x"},
                "metadata": {},
            }
        )
        self.assertEqual(out_elif["branch_decisions"]["if_1"], "elif_1")

        out_else = node(
            {
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "nothing"}, "text": "nothing", "status": "ok"},
                },
                "sys_vars": {"conversation_id": "conv-x"},
                "metadata": {},
            }
        )
        self.assertEqual(out_else["branch_decisions"]["if_1"], "else")

    def test_if_else_condition_value_template_supports_node_and_sys_vars(self) -> None:
        from app.assistant.workflow.engine.engine import _build_if_else_node

        node = _build_if_else_node(
            "if_1",
            {
                "branches": [
                    {
                        "id": "if_main",
                        "label": "IF",
                        "logic": "and",
                        "conditions": [
                            {
                                "id": "c1",
                                "variable": "sys.date",
                                "operator": "is",
                                "value": "{{start.user_input}}",
                            }
                        ],
                    }
                ],
                "else_handle": "else",
            },
        )

        out = node(
            {
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "2026-02-12"}, "text": "2026-02-12", "status": "ok"},
                },
                "sys_vars": {"date": "2026-02-12"},
                "metadata": {},
            }
        )
        self.assertEqual(out["branch_decisions"]["if_1"], "if_main")

    def test_execute_passes_runtime_context_into_workflow_sys_vars(self) -> None:
        from app.assistant.skill_catalog.base import SkillDefinition
        from app.assistant.workflow.engine.engine import LangGraphEngine

        class _FakeChunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _FakeLLM:
            def stream(self, _messages):
                yield _FakeChunk("x")

        captured_state: dict[str, object] = {}

        class _FakeCompiled:
            def stream(self, state):
                captured_state.update(state)
                cb = state["metadata"].get("on_content_delta")
                if callable(cb):
                    cb("ok")
                yield {"step": 1}

        skill = SkillDefinition(
            name="wf_skill",
            description="d",
            intent_examples=[],
            tools=[],
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            workflow_nodes=[{"node_id": "start", "node_type": "start", "config": {}}],
            workflow_edges=[],
        )

        with patch("app.assistant.workflow.engine.engine.ChatOpenAI", return_value=_FakeLLM()):
            engine = LangGraphEngine(api_key="k", base_url="https://x", model="m", db=None)

        with patch(
            "app.assistant.workflow.engine.engine._get_or_compile_graph",
            return_value=_FakeCompiled(),
        ):
            _ = list(
                engine.execute(
                    skill=skill,
                    user_input="u",
                    history=[],
                    runtime_context={"conversation_id": "conv-abc"},
                )
            )

        self.assertIn("sys_vars", captured_state)
        sys_vars = captured_state.get("sys_vars", {})
        self.assertEqual(sys_vars.get("conversation_id"), "conv-abc")
        self.assertTrue(sys_vars.get("date"))
        self.assertTrue(sys_vars.get("datetime"))

    def test_execute_output_passthrough_source_skips_structured_llm(self) -> None:
        from app.assistant.skill_catalog.base import SkillDefinition
        from app.assistant.workflow.engine.engine import LangGraphEngine

        class _FakeChunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _FakeLLM:
            def stream(self, _messages):
                yield _FakeChunk("x")

        captured_state: dict[str, object] = {}

        class _FakeCompiled:
            def stream(self, state):
                captured_state.update(state)
                cb = state["metadata"].get("on_content_delta")
                if callable(cb):
                    cb("ok")
                yield {"step": 1}

        skill = SkillDefinition(
            name="wf_skill_structured_passthrough",
            description="d",
            intent_examples=[],
            tools=[],
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            workflow_nodes=[
                {"node_id": "start", "node_type": "start", "config": {}},
                {
                    "node_id": "llm_1",
                    "node_type": "llm",
                    "config": {
                        "outputMode": "structured",
                        "outputFields": [{"name": "title"}],
                    },
                },
                {
                    "node_id": "output_1",
                    "node_type": "output",
                    "config": {
                        "outputMode": "text",
                        "textTemplate": "{{llm_1.response}}",
                    },
                },
            ],
            workflow_edges=[
                {"source_node_id": "start", "target_node_id": "llm_1"},
                {"source_node_id": "llm_1", "target_node_id": "output_1"},
            ],
        )

        with patch("app.assistant.workflow.engine.engine.ChatOpenAI", return_value=_FakeLLM()):
            engine = LangGraphEngine(api_key="k", base_url="https://x", model="m", db=None)

        with patch(
            "app.assistant.workflow.engine.engine._get_or_compile_graph",
            return_value=_FakeCompiled(),
        ):
            _ = list(
                engine.execute(
                    skill=skill,
                    user_input="u",
                    history=[],
                    runtime_context={"conversation_id": "conv-abc", "stream_output": True},
                )
            )

        self.assertEqual(captured_state.get("output_stream_source_node_id"), "")

    def test_workflow_parallel_branches_no_current_node_conflict(self) -> None:
        from app.assistant.skill_catalog.base import (
            SkillDefinition,
            WorkflowEdgeDefinition,
            WorkflowNodeDefinition,
        )
        from app.assistant.workflow.engine.engine import build_workflow_dag_subgraph

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def stream(self, _messages):
                yield _Chunk("ok")

        class _ToolA:
            name = "tool_a"

            @staticmethod
            def func(**_kwargs):
                return {"value": "A"}

        class _ToolB:
            name = "tool_b"

            @staticmethod
            def func(**_kwargs):
                return {"value": "B"}

        skill = SkillDefinition(
            name="parallel_wf",
            description="d",
            intent_examples=[],
            tools=["tool_a", "tool_b"],
            mode="langgraph",
            langgraph_pattern="workflow_dag",
        )
        nodes = [
            WorkflowNodeDefinition(node_id="start", node_type="start", label="Start"),
            WorkflowNodeDefinition(
                node_id="tool_a_1",
                node_type="tool",
                label="A",
                config={"toolName": "tool_a", "inputBindings": {}},
            ),
            WorkflowNodeDefinition(
                node_id="tool_b_1",
                node_type="tool",
                label="B",
                config={"toolName": "tool_b", "inputBindings": {}},
            ),
            WorkflowNodeDefinition(
                node_id="llm_output",
                node_type="llm",
                label="Out",
                config={
                    "systemPrompt": "summarize",
                    "userInput": "{{tool_a_1.result}}\\n{{tool_b_1.result}}",
                    "outputMode": "text",
                },
            ),
        ]
        edges = [
            WorkflowEdgeDefinition(edge_id="e1", source_node_id="start", target_node_id="tool_a_1"),
            WorkflowEdgeDefinition(edge_id="e2", source_node_id="start", target_node_id="tool_b_1"),
            WorkflowEdgeDefinition(edge_id="e3", source_node_id="tool_a_1", target_node_id="llm_output"),
            WorkflowEdgeDefinition(edge_id="e4", source_node_id="tool_b_1", target_node_id="llm_output"),
        ]

        with patch(
            "app.assistant.workflow.engine.engine._wrap_tool_with_db",
            side_effect=lambda tool, _db_bind: (lambda **kwargs: tool.func(**kwargs)),
        ):
            compiled = build_workflow_dag_subgraph(
                skill=skill,
                nodes=nodes,
                edges=edges,
                llm=_LLM(),
                args_llm=_LLM(),
                tool_map={"tool_a": _ToolA(), "tool_b": _ToolB()},
                db_bind=object(),
            )
            events = list(
                compiled.stream(
                    {
                        "messages": [],
                        "skill_name": "parallel_wf",
                        "user_input": "u",
                        "kb_enabled": False,
                        "metadata": {},
                        "node_outputs": {},
                        "execution_trace": [],
                        "branch_decisions": {},
                    }
                )
            )

        self.assertGreaterEqual(len(events), 1)

    def test_kr_node_uses_override_params_and_outputs_structured_fields(self) -> None:
        from app.assistant.workflow.engine.engine import _build_kr_node

        captured_args: dict[str, object] = {}

        class _KBTool:
            name = "kb_search"

            @staticmethod
            def func(**kwargs):
                captured_args.update(kwargs)
                return json.dumps(
                    {
                        "result": "hit",
                        "mode": "local",
                        "references": [{"index": 1}, {"index": 2}],
                    },
                    ensure_ascii=False,
                )

        with patch(
            "app.assistant.workflow.engine.engine._wrap_tool_with_db",
            side_effect=lambda tool, _db_bind: (lambda **kwargs: tool.func(**kwargs)),
        ):
            node = _build_kr_node(
                "kr_1",
                {"query": "Q: {{start.user_input}}", "mode": "local", "topK": 3},
                {"kb_search": _KBTool()},
                db_bind=object(),
            )
            out = node(
                {
                    "node_outputs": {
                        "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                    },
                    "metadata": {},
                }
            )

        self.assertEqual(captured_args["query"], "Q: hello")
        self.assertEqual(captured_args["mode"], "local")
        self.assertEqual(captured_args["top_k"], 3)
        kr_out = out["node_outputs"]["kr_1"]
        self.assertEqual(kr_out["json_fields"]["result"], "hit")
        self.assertEqual(kr_out["json_fields"]["query"], "Q: hello")
        self.assertEqual(kr_out["json_fields"]["mode"], "local")
        self.assertEqual(kr_out["json_fields"]["references_count"], 2)

    def test_llm_knowledge_injection_references_only(self) -> None:
        from app.assistant.workflow.engine.engine import _build_dag_llm_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def __init__(self) -> None:
                self.calls: list[list[dict[str, str]]] = []

            def stream(self, messages):
                self.calls.append(messages)
                yield _Chunk("ok")

        llm = _LLM()
        node = _build_dag_llm_node(
            "llm_1",
            {
                "system_prompt": "reply",
                "output_mode": "text",
                "user_input": "{{start.user_input}}",
                "knowledge_enabled": True,
                "knowledge_source_node_ids": ["kr_1"],
                "knowledge_inject_mode": "references_only",
                "knowledge_max_refs": 10,
            },
            llm,
        )

        node(
            {
                "user_input": "hello",
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                    "kr_1": {
                        "status": "ok",
                        "text": "KR TEXT",
                        "raw": {"result": "KR", "references": [{"index": 1}]},
                        "json_fields": {
                            "result": "KR",
                            "query": "hello",
                            "mode": "hybrid",
                            "references": [{"index": 1}],
                            "references_count": 1,
                        },
                    },
                },
                "workflow_node_types": {"start": "start", "kr_1": "knowledge_retrieval", "llm_1": "llm"},
                "metadata": {},
            }
        )

        self.assertEqual(len(llm.calls), 1)
        messages = llm.calls[0]
        context_msg = next(m for m in messages if m.get("content", "").startswith("上下文数据"))
        context_payload = json.loads(context_msg["content"].split("\n", 1)[1])
        self.assertNotIn("kr_1", context_payload)

        injected = next(m for m in messages if "\"inject_mode\"" in m.get("content", ""))
        payload = json.loads(injected["content"])
        self.assertEqual(payload["inject_mode"], "references_only")
        self.assertEqual(payload["sources"][0]["node_id"], "kr_1")
        self.assertIn("references", payload["sources"][0])
        self.assertNotIn("result", payload["sources"][0])

    def test_llm_knowledge_injection_full_payload(self) -> None:
        from app.assistant.workflow.engine.engine import _build_dag_llm_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def __init__(self) -> None:
                self.calls: list[list[dict[str, str]]] = []

            def stream(self, messages):
                self.calls.append(messages)
                yield _Chunk("ok")

        llm = _LLM()
        node = _build_dag_llm_node(
            "llm_1",
            {
                "system_prompt": "reply",
                "output_mode": "text",
                "user_input": "{{start.user_input}}",
                "knowledge_enabled": True,
                "knowledge_source_node_ids": ["kr_1"],
                "knowledge_inject_mode": "full_payload",
                "knowledge_max_refs": 10,
            },
            llm,
        )

        node(
            {
                "user_input": "hello",
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                    "kr_1": {
                        "status": "ok",
                        "text": "KR TEXT",
                        "raw": {"result": "KR", "mode": "hybrid", "query": "hello", "references": [{"index": 1}]},
                        "json_fields": {
                            "result": "KR",
                            "query": "hello",
                            "mode": "hybrid",
                            "references": [{"index": 1}],
                            "references_count": 1,
                        },
                    },
                },
                "workflow_node_types": {"start": "start", "kr_1": "knowledge_retrieval", "llm_1": "llm"},
                "metadata": {},
            }
        )

        injected = next(m for m in llm.calls[0] if "\"inject_mode\"" in m.get("content", ""))
        payload = json.loads(injected["content"])
        self.assertEqual(payload["inject_mode"], "full_payload")
        self.assertEqual(payload["sources"][0]["result"], "KR")

    def test_workflow_llm_node_uses_runtime_node_llm_override(self) -> None:
        from app.assistant.workflow.engine.engine import _build_dag_llm_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def __init__(self, text: str) -> None:
                self.text = text

            def stream(self, _messages):
                yield _Chunk(self.text)

        default_llm = _LLM("default")
        custom_llm = _LLM("custom")
        node = _build_dag_llm_node(
            "llm_1",
            {
                "system_prompt": "reply",
                "output_mode": "text",
                "user_input": "{{start.user_input}}",
            },
            default_llm,
        )

        out = node(
            {
                "user_input": "hello",
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                },
                "node_llms": {"llm_1": custom_llm},
                "metadata": {},
            }
        )
        self.assertEqual(out["node_outputs"]["llm_1"]["text"], "custom")

    def test_parameter_extractor_node_uses_runtime_node_llm_override(self) -> None:
        from app.assistant.workflow.engine.engine import _build_param_extractor_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def __init__(self, text: str) -> None:
                self.text = text

            def stream(self, _messages):
                yield _Chunk(self.text)

        default_llm = _LLM('{"city":"beijing"}')
        custom_llm = _LLM('{"city":"shanghai"}')
        node = _build_param_extractor_node(
            "extract_1",
            {
                "instruction": "extract city",
                "output_fields": [{"name": "city"}],
            },
            default_llm,
        )

        out = node(
            {
                "user_input": "上海天气",
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "上海天气"}, "text": "上海天气", "status": "ok"},
                },
                "node_llms": {"extract_1": custom_llm},
                "metadata": {},
            }
        )
        self.assertEqual(out["node_outputs"]["extract_1"]["json_fields"]["city"], "shanghai")

    def test_parameter_extractor_node_structured_output_success(self) -> None:
        from app.assistant.workflow.engine.engine import _build_param_extractor_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def __init__(self) -> None:
                self.calls: list[list[dict[str, str]]] = []

            def stream(self, messages):
                self.calls.append(messages)
                yield _Chunk('{"city":"shanghai","intent":"weather"}')

        llm = _LLM()
        node = _build_param_extractor_node(
            "extract_1",
            {
                "input_content": "用户输入={{start.user_input}}；日期={{sys.date}}",
                "instruction": "抽取城市与意图",
                "output_fields": [{"name": "city"}, {"name": "intent"}],
            },
            llm,
        )

        out = node(
            {
                "user_input": "上海天气",
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "上海天气"}, "text": "上海天气", "status": "ok"},
                },
                "sys_vars": {"date": "2026-02-13"},
                "metadata": {},
            }
        )

        node_out = out["node_outputs"]["extract_1"]
        self.assertEqual(node_out["json_fields"]["city"], "shanghai")
        self.assertEqual(node_out["json_fields"]["intent"], "weather")
        self.assertEqual(node_out["raw"]["city"], "shanghai")
        self.assertEqual(json.loads(node_out["text"])["intent"], "weather")
        self.assertIn("用户输入=上海天气；日期=2026-02-13", llm.calls[0][1]["content"])

    def test_parameter_extractor_node_invalid_json_raises(self) -> None:
        from app.assistant.workflow.engine.engine import _build_param_extractor_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def stream(self, _messages):
                yield _Chunk("not-json")

        node = _build_param_extractor_node(
            "extract_1",
            {
                "instruction": "extract city",
                "output_fields": [{"name": "city"}],
            },
            _LLM(),
        )

        with self.assertRaises(RuntimeError):
            node(
                {
                    "user_input": "上海天气",
                    "node_outputs": {
                        "start": {"json_fields": {"user_input": "上海天气"}, "text": "上海天气", "status": "ok"},
                    },
                    "metadata": {},
                }
            )

    def test_parameter_extractor_node_missing_output_field_raises(self) -> None:
        from app.assistant.workflow.engine.engine import _build_param_extractor_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def stream(self, _messages):
                yield _Chunk('{"city":"shanghai"}')

        node = _build_param_extractor_node(
            "extract_1",
            {
                "instruction": "extract city and intent",
                "output_fields": [{"name": "city"}, {"name": "intent"}],
            },
            _LLM(),
        )

        with self.assertRaises(RuntimeError):
            node(
                {
                    "user_input": "上海天气",
                    "node_outputs": {
                        "start": {"json_fields": {"user_input": "上海天气"}, "text": "上海天气", "status": "ok"},
                    },
                    "metadata": {},
                }
            )

    def test_custom_model_llm_client_is_cached_by_model_id(self) -> None:
        from app.assistant.workflow.engine.engine import LangGraphEngine

        class _FakeLLM:
            def stream(self, _messages):
                return iter(())

        class _Cfg:
            def __init__(self) -> None:
                self.api_key = "sk-test"
                self.base_url = "https://example.com/v1"
                self.model = "gpt-4.1-mini"
                self.model_id = uuid4()

        with patch("app.assistant.workflow.engine.engine.ChatOpenAI", return_value=_FakeLLM()) as mocked_chat:
            engine = LangGraphEngine(api_key="k", base_url="https://x", model="m", db=object())
            with patch(
                "app.assistant.workflow.engine.engine.resolve_openai_compat_config_by_model_id",
                return_value=_Cfg(),
            ):
                llm_a = engine._resolve_node_custom_llm("model-1", node_id="llm_1")
                llm_b = engine._resolve_node_custom_llm("model-1", node_id="llm_2")

        self.assertIs(llm_a, llm_b)
        # 2 calls from engine init (self.llm/self.args_llm) + 1 custom model client
        self.assertEqual(mocked_chat.call_count, 3)

    def test_resolve_workflow_node_llms_raises_when_custom_model_unavailable(self) -> None:
        from app.assistant.skill_catalog.base import SkillDefinition
        from app.assistant.workflow.engine.engine import LangGraphEngine

        class _FakeLLM:
            def stream(self, _messages):
                return iter(())

        skill = SkillDefinition(
            name="wf",
            description="d",
            intent_examples=[],
            tools=[],
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            workflow_nodes=[
                {
                    "node_id": "start",
                    "node_type": "start",
                    "label": "Start",
                    "config": {},
                },
                {
                    "node_id": "llm_1",
                    "node_type": "llm",
                    "label": "LLM",
                    "config": {
                        "modelSource": "custom",
                        "modelId": str(uuid4()),
                    },
                },
            ],
            workflow_edges=[
                {
                    "edge_id": "e1",
                    "source_node_id": "start",
                    "target_node_id": "llm_1",
                    "source_handle": "output",
                    "target_handle": "input",
                }
            ],
        )

        with patch("app.assistant.workflow.engine.engine.ChatOpenAI", return_value=_FakeLLM()):
            engine = LangGraphEngine(api_key="k", base_url="https://x", model="m", db=object())
        with patch(
            "app.assistant.workflow.engine.engine.resolve_openai_compat_config_by_model_id",
            return_value=None,
        ):
            with self.assertRaises(RuntimeError):
                engine._resolve_workflow_node_llms(skill)

    def test_resolve_workflow_node_llms_supports_container_body_custom_model(self) -> None:
        from app.assistant.skill_catalog.base import SkillDefinition
        from app.assistant.workflow.engine.engine import LangGraphEngine

        class _FakeLLM:
            def stream(self, _messages):
                return iter(())

        class _Cfg:
            def __init__(self) -> None:
                self.api_key = "sk-test"
                self.base_url = "https://example.com/v1"
                self.model = "gpt-4.1-mini"
                self.model_id = uuid4()

        body_model_id = str(uuid4())
        skill = SkillDefinition(
            name="wf",
            description="d",
            intent_examples=[],
            tools=[],
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            workflow_nodes=[
                {
                    "node_id": "start",
                    "node_type": "start",
                    "label": "Start",
                    "config": {},
                },
                {
                    "node_id": "iter_1",
                    "node_type": "iteration",
                    "label": "Iteration",
                    "config": {
                        "inputSource": "{{start.user_input}}",
                        "outputVariable": "items",
                        "outputSelector": "{{llm_body.response}}",
                        "bodyNodes": [
                            {"nodeId": "start", "nodeType": "start", "label": "Start", "config": {}},
                            {
                                "nodeId": "llm_body",
                                "nodeType": "llm",
                                "label": "Body LLM",
                                "config": {
                                    "modelSource": "custom",
                                    "modelId": body_model_id,
                                    "userInput": "{{container.item}}",
                                },
                            },
                        ],
                        "bodyEdges": [
                            {"sourceNodeId": "start", "targetNodeId": "llm_body", "sourceHandle": "output"},
                        ],
                    },
                },
            ],
            workflow_edges=[
                {
                    "edge_id": "e1",
                    "source_node_id": "start",
                    "target_node_id": "iter_1",
                    "source_handle": "output",
                    "target_handle": "input",
                }
            ],
        )

        with patch("app.assistant.workflow.engine.engine.ChatOpenAI", return_value=_FakeLLM()):
            engine = LangGraphEngine(api_key="k", base_url="https://x", model="m", db=object())
        with patch(
            "app.assistant.workflow.engine.engine.resolve_openai_compat_config_by_model_id",
            return_value=_Cfg(),
        ):
            node_llms = engine._resolve_workflow_node_llms(skill)

        self.assertIn("iter_1::llm_body", node_llms)

    def test_iteration_node_aggregates_body_outputs(self) -> None:
        from app.assistant.workflow.engine.engine import _build_iteration_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def stream(self, messages):
                payload = messages[-1]["content"] if messages else ""
                yield _Chunk(str(payload))

        node = _build_iteration_node(
            "iter_1",
            {
                "input_source": "{{start.user_input}}",
                "output_variable": "items",
                "output_selector": "{{llm_1.response}}",
                "parallel_mode": False,
                "error_strategy": "fail_fast",
                "flatten_output": False,
                "body_nodes": [
                    {"node_id": "start", "node_type": "start", "config": {}},
                    {
                        "node_id": "llm_1",
                        "node_type": "llm",
                        "config": {
                            "system_prompt": "echo",
                            "output_mode": "text",
                            "user_input": "{{container.item}}",
                        },
                    },
                ],
                "body_edges": [
                    {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
                ],
            },
            _LLM(),
            _LLM(),
            {},
            object(),
            node_llms=None,
        )

        out = node(
            {
                "user_input": '["a", "b"]',
                "node_outputs": {
                    "start": {
                        "status": "ok",
                        "text": '["a", "b"]',
                        "raw": ["a", "b"],
                        "json_fields": {"user_input": '["a", "b"]'},
                    }
                },
                "metadata": {},
            }
        )

        node_out = out["node_outputs"]["iter_1"]
        self.assertIn("items", node_out["json_fields"])
        self.assertEqual(len(node_out["json_fields"]["items"]), 2)

    def test_loop_node_terminates_by_container_condition(self) -> None:
        from app.assistant.workflow.engine.engine import _build_loop_node

        class _LLM:
            def stream(self, _messages):
                return iter(())

        node = _build_loop_node(
            "loop_1",
            {
                "max_iterations": 10,
                "termination_logic": "and",
                "termination_conditions": [
                    {
                        "id": "cond_1",
                        "variable": "container.index",
                        "operator": "gte",
                        "value": "2",
                    }
                ],
                "body_nodes": [{"node_id": "start", "node_type": "start", "config": {}}],
                "body_edges": [],
            },
            _LLM(),
            _LLM(),
            {},
            object(),
            node_llms=None,
        )

        out = node(
            {
                "user_input": "x",
                "node_outputs": {
                    "start": {"status": "ok", "text": "x", "raw": "x", "json_fields": {"user_input": "x"}},
                },
                "metadata": {},
            }
        )
        node_out = out["node_outputs"]["loop_1"]
        self.assertTrue(node_out["json_fields"]["terminated"])
        self.assertEqual(node_out["json_fields"]["iterations"], 3)


if __name__ == "__main__":
    unittest.main()

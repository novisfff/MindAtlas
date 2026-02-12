from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class LangGraphEngineStreamingTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def test_agent_node_streaming_emits_content_without_invoke(self) -> None:
        from app.assistant.skills.base import SkillDefinition
        from app.assistant.skills.langgraph_engine import _build_agent_node

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
        from app.assistant.skills.base import SkillDefinition
        from app.assistant.skills.langgraph_engine import LangGraphEngine

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

        with patch("app.assistant.skills.langgraph_engine.ChatOpenAI", return_value=_FakeLLM()):
            engine = LangGraphEngine(api_key="k", base_url="https://x", model="m", db=None)

        with patch(
            "app.assistant.skills.langgraph_engine._get_or_compile_graph",
            return_value=_FakeCompiled(),
        ):
            out = list(engine.execute(skill=skill, user_input="u", history=[]))

        tokens = [x for x in out if x]
        self.assertGreaterEqual(len(tokens), 2)
        self.assertEqual(tokens[0], "A")
        self.assertEqual(tokens[1], "B")

    def test_workflow_llm_node_is_output_false_does_not_emit_content_delta(self) -> None:
        from app.assistant.skills.langgraph_engine import _build_dag_llm_node

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
                "is_output": False,
            },
            _LLM(),
        )

        out = node(
            {
                "user_input": "hello",
                "node_outputs": {
                    "start": {"json_fields": {"user_input": "hello"}, "text": "hello", "status": "ok"},
                },
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

    def test_workflow_llm_node_is_output_true_emits_content_delta(self) -> None:
        from app.assistant.skills.langgraph_engine import _build_dag_llm_node

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
                "is_output": True,
            },
            _LLM(),
        )

        node(
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

        self.assertEqual(content_emitted, ["A", "B"])

    def test_workflow_llm_structured_retry_success(self) -> None:
        from app.assistant.skills.langgraph_engine import _build_dag_llm_node

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
                "is_output": True,
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
        from app.assistant.skills.langgraph_engine import _build_dag_llm_node

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
                "is_output": True,
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
        from app.assistant.skills.langgraph_engine import _build_dag_tool_node

        captured_args: dict[str, str] = {}

        class _Tool:
            name = "fake_tool"

            @staticmethod
            def func(**kwargs):
                captured_args.update(kwargs)
                return {"echo": kwargs.get("question"), "ok": True}

        with patch(
            "app.assistant.skills.langgraph_engine._wrap_tool_with_db",
            side_effect=lambda tool, _db_bind: (lambda **kwargs: tool.func(**kwargs)),
        ), patch(
            "app.assistant.skills.langgraph_engine._resolve_tool_output_param_names",
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
        from app.assistant.skills.langgraph_engine import _build_dag_tool_node

        class _Tool:
            name = "fake_tool"

            @staticmethod
            def func(**_kwargs):
                return {"foo": "bar"}

        with patch(
            "app.assistant.skills.langgraph_engine._wrap_tool_with_db",
            side_effect=lambda tool, _db_bind: (lambda **kwargs: tool.func(**kwargs)),
        ), patch(
            "app.assistant.skills.langgraph_engine._resolve_tool_output_param_names",
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
        from app.assistant.skills.langgraph_engine import _build_if_else_node

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
        from app.assistant.skills.langgraph_engine import _build_if_else_node

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
        from app.assistant.skills.base import SkillDefinition
        from app.assistant.skills.langgraph_engine import LangGraphEngine

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

        with patch("app.assistant.skills.langgraph_engine.ChatOpenAI", return_value=_FakeLLM()):
            engine = LangGraphEngine(api_key="k", base_url="https://x", model="m", db=None)

        with patch(
            "app.assistant.skills.langgraph_engine._get_or_compile_graph",
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

    def test_workflow_parallel_branches_no_current_node_conflict(self) -> None:
        from app.assistant.skills.base import (
            SkillDefinition,
            WorkflowEdgeDefinition,
            WorkflowNodeDefinition,
        )
        from app.assistant.skills.langgraph_engine import build_workflow_dag_subgraph

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
                    "isOutput": True,
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
            "app.assistant.skills.langgraph_engine._wrap_tool_with_db",
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


if __name__ == "__main__":
    unittest.main()

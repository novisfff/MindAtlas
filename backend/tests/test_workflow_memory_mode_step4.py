from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class WorkflowMemoryModeStep4Tests(unittest.TestCase):
    @staticmethod
    def _base_workflow(
        *,
        start_config: dict,
        llm_user_input: str,
    ) -> tuple[list[dict], list[dict]]:
        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": start_config},
            {
                "node_id": "llm_1",
                "node_type": "llm",
                "label": "LLM",
                "config": {
                    "systemPrompt": "reply",
                    "outputMode": "text",
                    "userInput": llm_user_input,
                },
            },
            {
                "node_id": "output_1",
                "node_type": "output",
                "label": "Output",
                "config": {"outputMode": "text", "textTemplate": "{{llm_1.response}}"},
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
            {"source_node_id": "llm_1", "target_node_id": "output_1", "source_handle": "output"},
        ]
        return nodes, edges

    def test_validator_allows_semantic_memory_refs_in_structured_mode(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes, edges = self._base_workflow(
            start_config={"inputMode": "text", "memoryMode": "structured"},
            llm_user_input="{{start.memory_conversation_summary}}",
        )
        result = validate_workflow(nodes, edges)
        self.assertTrue(result.valid, [e.message for e in result.errors])

    def test_validator_rejects_semantic_memory_refs_in_auto_and_off_modes(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        for mode in ("auto", "off"):
            with self.subTest(mode=mode):
                nodes, edges = self._base_workflow(
                    start_config={"inputMode": "text", "memoryMode": mode},
                    llm_user_input="{{start.memory_recent_dialogue}}",
                )
                result = validate_workflow(nodes, edges)
                self.assertFalse(result.valid)
                self.assertTrue(
                    any("unsupported start field: start.memory_recent_dialogue" in e.message for e in result.errors),
                    [e.message for e in result.errors],
                )

    def test_validator_rejects_legacy_memory_field_refs(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes, edges = self._base_workflow(
            start_config={"inputMode": "text", "memoryMode": "structured"},
            llm_user_input="{{start.memory_l1}}",
        )
        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("unsupported start field: start.memory_l1" in e.message for e in result.errors),
            [e.message for e in result.errors],
        )

    def test_validator_rejects_reserved_start_structured_field_names(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        for reserved_name in ("memory_recent_dialogue", "memory_l0"):
            with self.subTest(reserved_name=reserved_name):
                nodes, edges = self._base_workflow(
                    start_config={
                        "inputMode": "structured",
                        "memoryMode": "structured",
                        "structuredFields": [
                            {"name": reserved_name, "type": "string", "required": True},
                        ],
                    },
                    llm_user_input="{{start.memory_conversation_summary}}",
                )
                result = validate_workflow(nodes, edges)
                self.assertFalse(result.valid)
                self.assertTrue(
                    any(f"start structured field name '{reserved_name}' is reserved" in e.message for e in result.errors),
                    [e.message for e in result.errors],
                )

    def test_start_node_exposes_semantic_memory_fields_in_structured_mode(self) -> None:
        from app.assistant.workflow.engine.node_builders.start_node import build_start_node

        start_node = build_start_node(
            {
                "input_mode": "structured",
                "memory_mode": "structured",
                "structured_fields": [{"name": "title", "type": "string", "required": True}],
            }
        )
        result = start_node(
            {
                "structured_input": {"title": "hello"},
                "memory_mode": "structured",
                "memory_context": {
                    "l0_text": "U: hi",
                    "l1_text": "summary",
                    "l2_text": "- fact",
                },
            }
        )
        start_fields = result["node_outputs"]["start"]["json_fields"]
        self.assertEqual(start_fields["title"], "hello")
        self.assertEqual(start_fields["memory_recent_dialogue"], "U: hi")
        self.assertEqual(start_fields["memory_conversation_summary"], "summary")
        self.assertEqual(start_fields["memory_skill_facts"], "- fact")

    def test_start_node_uses_state_memory_mode_for_visibility(self) -> None:
        from app.assistant.workflow.engine.node_builders.start_node import build_start_node

        start_node = build_start_node(
            {
                "input_mode": "text",
                "memory_mode": "structured",
            }
        )
        result = start_node(
            {
                "user_input": "hello",
                "memory_mode": "off",
                "memory_context": {
                    "l0_text": "U: hi",
                    "l1_text": "summary",
                    "l2_text": "- fact",
                },
            }
        )
        start_fields = result["node_outputs"]["start"]["json_fields"]
        self.assertEqual(start_fields["user_input"], "hello")
        self.assertNotIn("memory_recent_dialogue", start_fields)
        self.assertNotIn("memory_conversation_summary", start_fields)
        self.assertNotIn("memory_skill_facts", start_fields)

    def test_llm_node_injects_memory_block_only_in_auto_mode(self) -> None:
        from app.assistant.workflow.engine.node_builders.llm_node import build_dag_llm_node

        class _Chunk:
            def __init__(self, content: str) -> None:
                self.content = content

        class _LLM:
            def __init__(self) -> None:
                self.calls: list[list[dict[str, str]]] = []

            def stream(self, messages):
                self.calls.append(messages)
                yield _Chunk("ok")

        for mode, should_inject in (("auto", True), ("off", False), ("structured", False)):
            with self.subTest(mode=mode):
                llm = _LLM()
                node = build_dag_llm_node(
                    "llm_1",
                    {
                        "system_prompt": "reply",
                        "output_mode": "text",
                        "user_input": "{{start.user_input}}",
                    },
                    llm,
                )
                with patch(
                    "app.assistant.workflow.engine.node_builders.llm_node.get_settings",
                    return_value=SimpleNamespace(assistant_memory_injection_max_chars=4000),
                ):
                    node(
                        {
                            "memory_mode": mode,
                            "memory_context": {
                                "l0_text": "User: hi",
                                "l0_messages": [
                                    {"role": "user", "content": "hi"},
                                    {"role": "assistant", "content": "hello"},
                                ],
                                "l1_text": "summary",
                                "l2_text": "- fact",
                            },
                            "node_outputs": {
                                "start": {
                                    "json_fields": {"user_input": "hello"},
                                    "text": "hello",
                                    "status": "ok",
                                },
                            },
                            "workflow_node_types": {"start": "start"},
                            "metadata": {},
                        }
                    )
                llm_messages = llm.calls[0]
                system_prompt = llm_messages[0]["content"]
                if should_inject:
                    self.assertIn("## Short-Term Memory", system_prompt)
                    self.assertIn("### Conversation Summary", system_prompt)
                    self.assertIn("### Skill Facts", system_prompt)
                    self.assertNotIn("### Recent Dialogue", system_prompt)
                    self.assertEqual(llm_messages[1], {"role": "user", "content": "hi"})
                    self.assertEqual(llm_messages[2], {"role": "assistant", "content": "hello"})
                else:
                    self.assertNotIn("## Short-Term Memory", system_prompt)
                    self.assertEqual(llm_messages[1], {"role": "user", "content": "hello"})

    def test_agent_loop_uses_default_memory_mode_for_injection(self) -> None:
        from app.assistant.skill_catalog.base import SkillDefinition
        from app.assistant.workflow.engine.engine import LangGraphEngine

        class _FakeLLM:
            def stream(self, _messages):
                return iter(())

        captured_state: dict[str, object] = {}

        class _FakeCompiled:
            def stream(self, state):
                captured_state.update(state)
                cb = state["metadata"].get("on_content_delta")
                if callable(cb):
                    cb("ok")
                yield {"step": 1}

        skill = SkillDefinition(
            name="agent_skill",
            description="d",
            intent_examples=[],
            tools=[],
            mode="langgraph",
            langgraph_pattern="agent_loop",
        )

        with patch("app.assistant.workflow.engine.engine.ChatOpenAI", return_value=_FakeLLM()):
            engine = LangGraphEngine(api_key="k", base_url="https://x", model="m", db=None)

        with patch(
            "app.assistant.workflow.engine.engine._get_or_compile_graph",
            return_value=_FakeCompiled(),
        ), patch(
            "app.assistant.workflow.engine.engine.get_settings",
            return_value=SimpleNamespace(
                assistant_memory_mode_default="auto",
                assistant_memory_l0_turns=2,
                assistant_memory_l0_max_chars=1200,
                assistant_memory_injection_max_chars=2000,
            ),
        ), patch.object(
            engine,
            "_load_l1_summary",
            return_value="summary",
        ), patch.object(
            engine,
            "_load_l2_text",
            return_value=("- fact", 1),
        ):
            _ = list(
                engine.execute(
                    skill=skill,
                    user_input="current",
                    history=[
                        {"role": "user", "content": "before"},
                        {"role": "assistant", "content": "done"},
                    ],
                )
            )

        system_prompt = captured_state["messages"][0].content
        self.assertIn("## Short-Term Memory", system_prompt)
        self.assertIn("### Conversation Summary", system_prompt)
        self.assertIn("### Skill Facts", system_prompt)
        self.assertNotIn("### Recent Dialogue", system_prompt)

        captured_state.clear()
        with patch(
            "app.assistant.workflow.engine.engine._get_or_compile_graph",
            return_value=_FakeCompiled(),
        ), patch(
            "app.assistant.workflow.engine.engine.get_settings",
            return_value=SimpleNamespace(
                assistant_memory_mode_default="off",
                assistant_memory_l0_turns=2,
                assistant_memory_l0_max_chars=1200,
                assistant_memory_injection_max_chars=2000,
            ),
        ), patch.object(
            engine,
            "_load_l1_summary",
            return_value="summary",
        ), patch.object(
            engine,
            "_load_l2_text",
            return_value=("- fact", 1),
        ):
            _ = list(
                engine.execute(
                    skill=skill,
                    user_input="current",
                    history=[
                        {"role": "user", "content": "before"},
                        {"role": "assistant", "content": "done"},
                    ],
                )
            )
        system_prompt = captured_state["messages"][0].content
        self.assertNotIn("## Short-Term Memory", system_prompt)


if __name__ == "__main__":
    unittest.main()

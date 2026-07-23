from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Callable
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class _StubRouter:
    decision = None
    last_history = None
    last_runtime_context = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    def route(self, _user_input: str, history=None, runtime_context: dict | None = None):
        _StubRouter.last_history = history
        _StubRouter.last_runtime_context = runtime_context
        return _StubRouter.decision


class _StubLangGraphEngine:
    def __init__(self, *args, **kwargs) -> None:
        pass


class _FakeSkillEngine:
    def __init__(
        self,
        outputs: list[str] | None = None,
        error: Exception | None = None,
        before_outputs: Callable[[dict], None] | None = None,
    ) -> None:
        self.outputs = outputs or []
        self.error = error
        self.before_outputs = before_outputs
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if callable(self.before_outputs):
            self.before_outputs(kwargs)
        for chunk in self.outputs:
            yield chunk


@unittest.skip('legacy Supervisor/IntentRouter removed (Plan 10 B2)')
class SupervisorGraphRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def _make_agent(self, decision):
        _StubRouter.decision = decision
        _StubRouter.last_history = None
        _StubRouter.last_runtime_context = None
        with patch("app.assistant.orchestration.agent_runtime.SkillRouter", new=_StubRouter), patch(
            "app.assistant.orchestration.agent_runtime.LangGraphEngine", new=_StubLangGraphEngine
        ):
            from app.assistant.orchestration.agent_runtime import AssistantAgent  # noqa: E402

            return AssistantAgent(api_key="k", base_url="https://x", model="m", db=None)

    def test_selected_skill_executes_once(self) -> None:
        from app.assistant.orchestration.intent_router import RouteDecision  # noqa: E402

        agent = self._make_agent(
            RouteDecision(
                skill="quick_stats",
                reason="match",
                selected_skill="quick_stats",
            )
        )
        fake_engine = _FakeSkillEngine(outputs=["A", "B"])
        fake_skill = SimpleNamespace(name="quick_stats", langgraph_pattern="workflow_dag", model_source="default", model_id=None)

        skill_events: list[tuple[str, str, bool]] = []
        skill_end_events: list[tuple[str, str]] = []

        with patch.object(agent, "_resolve_skill_definition", return_value=fake_skill) as mocked_resolve, patch.object(
            agent,
            "_resolve_engine_for_skill",
            return_value=fake_engine,
        ):
            out = list(
                agent.stream(
                    history=[],
                    user_input="u",
                    on_skill_start=lambda sid, name, hidden: skill_events.append((sid, name, hidden)),
                    on_skill_end=lambda sid, status: skill_end_events.append((sid, status)),
                )
            )

        tokens = [x for x in out if x]
        self.assertEqual(tokens, ["A", "B"])
        self.assertEqual(mocked_resolve.call_count, 1)
        self.assertEqual(mocked_resolve.call_args.args[1], "quick_stats")
        self.assertEqual(fake_engine.calls[0]["skill"].name, "quick_stats")
        self.assertEqual(len(skill_events), 1)
        self.assertEqual(skill_events[0][1], "quick_stats")
        self.assertEqual(skill_end_events[0][1], "completed")

    def test_invalid_or_empty_routes_to_general_chat(self) -> None:
        from app.assistant.orchestration.intent_router import RouteDecision  # noqa: E402

        agent = self._make_agent(
            RouteDecision(
                skill="",
                reason="missing",
                selected_skill="general_chat",
                fallback_reason="missing_skill",
            )
        )
        fake_engine = _FakeSkillEngine(outputs=["ok"])

        resolve_calls: list[str] = []

        def _resolve(_db, skill_name: str):
            resolve_calls.append(skill_name)
            return SimpleNamespace(name=skill_name, langgraph_pattern="agent_loop", model_source="default", model_id=None)

        with patch.object(agent, "_resolve_skill_definition", side_effect=_resolve), patch.object(
            agent,
            "_resolve_engine_for_skill",
            return_value=fake_engine,
        ):
            out = list(agent.stream(history=[], user_input="u"))

        self.assertEqual([x for x in out if x], ["ok"])
        self.assertEqual(resolve_calls, ["general_chat"])

    def test_skill_failure_does_not_fallback(self) -> None:
        from app.assistant.orchestration.intent_router import RouteDecision  # noqa: E402

        agent = self._make_agent(
            RouteDecision(
                skill="quick_stats",
                reason="match",
                selected_skill="quick_stats",
            )
        )
        fake_engine = _FakeSkillEngine(error=RuntimeError("boom"))
        resolved_skills: list[str] = []
        skill_end_events: list[tuple[str, str]] = []

        def _resolve(_db, skill_name: str):
            resolved_skills.append(skill_name)
            return SimpleNamespace(name=skill_name, langgraph_pattern="workflow_dag", model_source="default", model_id=None)

        with patch.object(agent, "_resolve_skill_definition", side_effect=_resolve), patch.object(
            agent,
            "_resolve_engine_for_skill",
            return_value=fake_engine,
        ):
            with self.assertRaises(RuntimeError):
                _ = list(
                    agent.stream(
                        history=[],
                        user_input="u",
                        on_skill_end=lambda sid, status: skill_end_events.append((sid, status)),
                    )
                )

        self.assertEqual(resolved_skills, ["quick_stats"])
        self.assertEqual(skill_end_events[0][1], "error")

    def test_default_unavailable_fails_before_execution(self) -> None:
        from app.assistant.orchestration.intent_router import RouteDecision  # noqa: E402

        agent = self._make_agent(
            RouteDecision(
                skill="",
                reason="router failed",
                selected_skill="",
                fallback_reason="default_skill_unavailable",
            )
        )

        with patch.object(agent, "_resolve_skill_definition") as mocked_resolve:
            with self.assertRaises(RuntimeError):
                _ = list(agent.stream(history=[], user_input="u"))

        mocked_resolve.assert_not_called()

    def test_empty_delta_is_forwarded_for_event_flush(self) -> None:
        from app.assistant.orchestration.intent_router import RouteDecision  # noqa: E402

        agent = self._make_agent(
            RouteDecision(
                skill="smart_capture",
                reason="match",
                selected_skill="smart_capture",
            )
        )

        approval_events: list[dict] = []

        def _emit_approval(kwargs: dict) -> None:
            callback = kwargs.get("on_human_approval_requested")
            if callable(callback):
                callback({"id": "approval_1", "status": "pending"})

        fake_engine = _FakeSkillEngine(outputs=[""], before_outputs=_emit_approval)
        fake_skill = SimpleNamespace(name="smart_capture", langgraph_pattern="workflow_dag", model_source="default", model_id=None)

        with patch.object(agent, "_resolve_skill_definition", return_value=fake_skill), patch.object(
            agent,
            "_resolve_engine_for_skill",
            return_value=fake_engine,
        ):
            out = list(
                agent.stream(
                    history=[],
                    user_input="u",
                    on_human_approval_requested=lambda payload: approval_events.append(payload),
                )
            )

        self.assertEqual(len(approval_events), 1)
        self.assertIn("", out)

    def test_route_node_passes_history_to_router(self) -> None:
        from app.assistant.orchestration.intent_router import RouteDecision  # noqa: E402

        agent = self._make_agent(
            RouteDecision(
                skill="quick_stats",
                reason="match",
                selected_skill="quick_stats",
            )
        )
        fake_engine = _FakeSkillEngine(outputs=["ok"])
        fake_skill = SimpleNamespace(name="quick_stats", langgraph_pattern="workflow_dag", model_source="default", model_id=None)
        history = [
            {"role": "user", "content": "上一轮输入"},
            {"role": "assistant", "content": "上一轮输出"},
        ]

        with patch.object(agent, "_resolve_skill_definition", return_value=fake_skill), patch.object(
            agent,
            "_resolve_engine_for_skill",
            return_value=fake_engine,
        ):
            _ = list(agent.stream(history=history, user_input="继续这个"))

        self.assertEqual(_StubRouter.last_history, history)

    def test_execute_skill_passes_node_callbacks_with_labels(self) -> None:
        from app.assistant.orchestration.intent_router import RouteDecision  # noqa: E402

        agent = self._make_agent(
            RouteDecision(
                skill="smart_capture",
                reason="match",
                selected_skill="smart_capture",
            )
        )

        node_start_events: list[tuple[str, str, str]] = []
        node_end_events: list[tuple[str, str, str]] = []

        def _emit_node_events(kwargs: dict) -> None:
            on_node_start = kwargs.get("on_node_start")
            on_node_end = kwargs.get("on_node_end")
            if callable(on_node_start):
                on_node_start("start", "start")
                on_node_start("llm_1", "llm")
            if callable(on_node_end):
                on_node_end("start", "ok")
                on_node_end("llm_1", "ok")

        fake_engine = _FakeSkillEngine(outputs=["ok"], before_outputs=_emit_node_events)
        fake_skill = SimpleNamespace(
            name="smart_capture",
            langgraph_pattern="workflow_dag",
            model_source="default",
            model_id=None,
            workflow_nodes=[
                SimpleNamespace(node_id="start", label="开始"),
                SimpleNamespace(node_id="llm_1", label="LLM生成"),
            ],
        )

        with patch.object(agent, "_resolve_skill_definition", return_value=fake_skill), patch.object(
            agent,
            "_resolve_engine_for_skill",
            return_value=fake_engine,
        ):
            _ = list(
                agent.stream(
                    history=[],
                    user_input="创建记录",
                    on_node_start=lambda node_id, node_type, node_label: node_start_events.append(
                        (node_id, node_type, node_label)
                    ),
                    on_node_end=lambda node_id, status, node_label: node_end_events.append(
                        (node_id, status, node_label)
                    ),
                )
            )

        execute_kwargs = fake_engine.calls[0]
        self.assertTrue(callable(execute_kwargs.get("on_node_start")))
        self.assertTrue(callable(execute_kwargs.get("on_node_end")))
        self.assertEqual(
            node_start_events,
            [("start", "start", "开始"), ("llm_1", "llm", "LLM生成")],
        )
        self.assertEqual(
            node_end_events,
            [("start", "ok", "开始"), ("llm_1", "ok", "LLM生成")],
        )


if __name__ == "__main__":
    unittest.main()

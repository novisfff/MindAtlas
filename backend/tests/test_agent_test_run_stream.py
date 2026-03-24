from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


def _parse_sse_events(chunks: list[bytes]) -> list[tuple[str, dict]]:
    raw = b"".join(chunks).decode("utf-8")
    blocks = [block.strip() for block in raw.split("\n\n") if block.strip()]
    events: list[tuple[str, dict]] = []
    for block in blocks:
        event_name = ""
        data_json = "{}"
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_name = line[7:]
            elif line.startswith("data: "):
                data_json = line[6:]
        if not event_name:
            continue
        events.append((event_name, json.loads(data_json)))
    return events


class AgentTestRunServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _create_agent_profile(self):
        from app.assistant_config.schemas import AssistantAgentProfileCreateRequest
        from app.assistant_config.service import AssistantConfigService

        service = AssistantConfigService(self.db)
        return service.create_agent_profile(
            AssistantAgentProfileCreateRequest(
                name="agent_test_runner",
                description="agent test",
                system_prompt="You are a helpful assistant",
                tools=[],
                kb_config={"enabled": False},
                enabled=True,
            )
        )

    @staticmethod
    def _valid_request(stream_output: bool = True):
        from app.assistant_config.schemas import AgentTestRunDraftInput, AgentTestRunRequest

        return AgentTestRunRequest(
            draft=AgentTestRunDraftInput(
                system_prompt="You are a test agent",
                tools=[],
                kb_config={"enabled": False},
            ),
            user_input="hello",
            stream_output=stream_output,
        )

    def test_prepare_rejects_unavailable_tool(self) -> None:
        from app.assistant_config.agent_test_service import AgentTestRunService
        from app.common.exceptions import ApiException

        profile = self._create_agent_profile()
        service = AgentTestRunService(self.db)
        req = self._valid_request(stream_output=True)
        req.draft.tools = ["missing_tool"]

        with patch("app.assistant_config.agent_test_service.ToolRegistry.list_system_tools", return_value=[]):
            with self.assertRaises(ApiException) as ctx:
                service.prepare(profile.id, req)

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Agent references unavailable tools", ctx.exception.message)

    def test_stream_emits_run_and_trace_events(self) -> None:
        from app.assistant_config.agent_test_service import AgentTestRunService

        profile = self._create_agent_profile()
        service = AgentTestRunService(self.db)
        prepared = service.prepare(profile.id, self._valid_request(stream_output=True))

        class _FakeEngine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                kwargs["on_analysis_start"]("analysis_1")
                kwargs["on_analysis_delta"]("analysis_1", "thinking")
                kwargs["on_analysis_end"]("analysis_1")
                kwargs["on_tool_call_start"](
                    "tool_1",
                    "search_entries",
                    {"q": "hello"},
                    agent_round=1,
                    tool_call_index=1,
                    tool_kind="tool",
                )
                kwargs["on_tool_call_end"](
                    "tool_1",
                    "completed",
                    "{}",
                    agent_round=1,
                    tool_call_index=1,
                    tool_kind="tool",
                )
                yield "A"
                yield "B"

        with patch(
            "app.assistant_config.agent_test_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ), patch(
            "app.assistant_config.agent_test_service.AgentTestRunService._build_engine",
            return_value=_FakeEngine(),
        ):
            chunks = list(service.stream(prepared))

        events = _parse_sse_events(chunks)
        event_names = [name for name, _ in events]
        self.assertIn("run_start", event_names)
        self.assertIn("analysis_start", event_names)
        self.assertIn("analysis_delta", event_names)
        self.assertIn("analysis_end", event_names)
        self.assertIn("tool_call_start", event_names)
        self.assertIn("tool_call_end", event_names)
        self.assertIn("content_delta", event_names)
        self.assertIn("run_end", event_names)

        tool_start_payload = next(payload for name, payload in events if name == "tool_call_start")
        self.assertEqual(tool_start_payload["toolCallId"], "tool_1")
        self.assertEqual(tool_start_payload["agentRound"], 1)
        self.assertEqual(tool_start_payload["toolCallIndex"], 1)
        self.assertEqual(tool_start_payload["toolKind"], "tool")
        self.assertIn("startedAt", tool_start_payload)

        tool_end_payload = next(payload for name, payload in events if name == "tool_call_end")
        self.assertEqual(tool_end_payload["toolCallId"], "tool_1")
        self.assertEqual(tool_end_payload["toolKind"], "tool")
        self.assertIn("startedAt", tool_end_payload)
        self.assertIn("endedAt", tool_end_payload)
        self.assertIn("durationMs", tool_end_payload)

        run_end_payload = next(payload for name, payload in events if name == "run_end")
        self.assertEqual(run_end_payload["status"], "completed")
        self.assertEqual(run_end_payload["finalText"], "AB")

    def test_stream_aggregates_high_frequency_delta_events(self) -> None:
        from app.assistant_config.agent_test_service import AgentTestRunService

        profile = self._create_agent_profile()
        service = AgentTestRunService(self.db)
        prepared = service.prepare(profile.id, self._valid_request(stream_output=True))
        token_count = 200

        class _FakeEngine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                for _ in range(token_count):
                    yield "x"

        with patch(
            "app.assistant_config.agent_test_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ), patch(
            "app.assistant_config.agent_test_service.AgentTestRunService._build_engine",
            return_value=_FakeEngine(),
        ):
            chunks = list(service.stream(prepared))

        events = _parse_sse_events(chunks)
        content_deltas = [payload["delta"] for name, payload in events if name == "content_delta"]

        self.assertGreater(len(content_deltas), 0)
        self.assertLess(len(content_deltas), token_count)
        self.assertEqual("".join(content_deltas), "x" * token_count)

        run_end_payload = next(payload for name, payload in events if name == "run_end")
        self.assertEqual(run_end_payload["finalText"], "x" * token_count)

    def test_stream_emits_bootstrap_error_when_no_model_config(self) -> None:
        from app.assistant_config.agent_test_service import AgentTestRunService

        profile = self._create_agent_profile()
        service = AgentTestRunService(self.db)
        prepared = service.prepare(profile.id, self._valid_request(stream_output=False))

        with patch(
            "app.assistant_config.agent_test_service.resolve_openai_compat_config",
            return_value=None,
        ):
            chunks = list(service.stream(prepared))

        events = _parse_sse_events(chunks)
        event_names = [name for name, _ in events]
        self.assertIn("run_start", event_names)
        self.assertIn("run_error", event_names)
        self.assertIn("run_end", event_names)

        error_payload = next(payload for name, payload in events if name == "run_error")
        self.assertEqual(error_payload["stage"], "bootstrap")

    def test_stream_uses_custom_model_config_when_requested(self) -> None:
        from app.assistant_config.agent_test_service import AgentTestRunService
        from app.assistant_config.schemas import AgentTestRunDraftInput, AgentTestRunRequest

        profile = self._create_agent_profile()
        service = AgentTestRunService(self.db)
        model_id = uuid4()
        request = AgentTestRunRequest(
            draft=AgentTestRunDraftInput(
                system_prompt="You are a test agent",
                tools=[],
                kb_config={"enabled": False},
                model_source="custom",
                model_id=model_id,
            ),
            user_input="hello",
            stream_output=True,
        )
        prepared = service.prepare(profile.id, request)

        class _FakeEngine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                yield "ok"

        with patch(
            "app.assistant_config.agent_test_service.resolve_openai_compat_config_by_model_id",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-custom"),
        ) as by_id_mock, patch(
            "app.assistant_config.agent_test_service.resolve_openai_compat_config",
            return_value=None,
        ), patch(
            "app.assistant_config.agent_test_service.AgentTestRunService._build_engine",
            return_value=_FakeEngine(),
        ):
            chunks = list(service.stream(prepared))

        events = _parse_sse_events(chunks)
        run_end_payload = next(payload for name, payload in events if name == "run_end")
        self.assertEqual(run_end_payload["status"], "completed")
        self.assertEqual(run_end_payload["finalText"], "ok")
        by_id_mock.assert_called_once()

    def test_stream_passes_history_to_engine(self) -> None:
        from app.assistant_config.agent_test_service import AgentTestRunService
        from app.assistant_config.schemas import AgentTestRunDraftInput, AgentTestRunRequest

        profile = self._create_agent_profile()
        service = AgentTestRunService(self.db)
        request = AgentTestRunRequest(
            draft=AgentTestRunDraftInput(
                system_prompt="You are a test agent",
                tools=[],
                kb_config={"enabled": False},
            ),
            user_input="follow up",
            history=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
            stream_output=True,
        )
        prepared = service.prepare(profile.id, request)
        captured_history = None

        class _FakeEngine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                nonlocal captured_history
                captured_history = kwargs.get("history")
                yield "ok"

        with patch(
            "app.assistant_config.agent_test_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ), patch(
            "app.assistant_config.agent_test_service.AgentTestRunService._build_engine",
            return_value=_FakeEngine(),
        ):
            chunks = list(service.stream(prepared))

        events = _parse_sse_events(chunks)
        run_end_payload = next(payload for name, payload in events if name == "run_end")
        self.assertEqual(run_end_payload["status"], "completed")
        self.assertEqual(captured_history, request.history)

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


class WorkflowTestRunServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _create_workflow_skill(self):
        from app.assistant_config.schemas import AssistantSkillCreateRequest
        from app.assistant_config.service import AssistantConfigService

        service = AssistantConfigService(self.db)
        return service.create_skill(
            AssistantSkillCreateRequest(
                name="wf_test_runner",
                description="workflow test",
                intent_examples=[],
                tools=[],
                mode="langgraph",
                langgraph_pattern="workflow_dag",
                enabled=True,
            )
        )

    @staticmethod
    def _valid_request(stream_output: bool = True):
        from app.assistant_config.schemas import (
            WorkflowEdgeInput,
            WorkflowInput,
            WorkflowNodeInput,
            WorkflowTestRunRequest,
        )

        workflow = WorkflowInput(
            nodes=[
                WorkflowNodeInput(node_id="start", node_type="start", label="Start", config={}),
                WorkflowNodeInput(
                    node_id="llm_1",
                    node_type="llm",
                    label="LLM",
                    config={
                        "outputMode": "text",
                        "userInput": "{{start.user_input}}",
                    },
                ),
                WorkflowNodeInput(
                    node_id="output_1",
                    node_type="output",
                    label="Output",
                    config={
                        "outputMode": "text",
                        "textTemplate": "{{llm_1.response}}",
                    },
                ),
            ],
            edges=[
                WorkflowEdgeInput(
                    edge_id="e1",
                    source_node_id="start",
                    target_node_id="llm_1",
                    source_handle="output",
                    target_handle="input",
                ),
                WorkflowEdgeInput(
                    edge_id="e2",
                    source_node_id="llm_1",
                    target_node_id="output_1",
                    source_handle="output",
                    target_handle="input",
                ),
            ],
        )
        return WorkflowTestRunRequest(
            workflow=workflow,
            user_input="hello",
            stream_output=stream_output,
        )

    def test_prepare_rejects_invalid_workflow(self) -> None:
        from app.assistant_config.schemas import WorkflowInput, WorkflowNodeInput, WorkflowTestRunRequest
        from app.assistant_config.workflow_test_service import WorkflowTestRunService
        from app.common.exceptions import ApiException

        skill = self._create_workflow_skill()
        service = WorkflowTestRunService(self.db)

        bad_request = WorkflowTestRunRequest(
            workflow=WorkflowInput(
                nodes=[
                    WorkflowNodeInput(node_id="start", node_type="start", label="Start", config={}),
                    WorkflowNodeInput(node_id="llm_1", node_type="llm", label="LLM", config={}),
                ],
                edges=[],
            ),
            user_input="hello",
            stream_output=True,
        )

        with self.assertRaises(ApiException) as ctx:
            service.prepare(skill.id, bad_request)

        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("Invalid workflow topology", ctx.exception.message)

    def test_stream_emits_run_and_trace_events(self) -> None:
        from app.assistant_config.workflow_test_service import (
            PreparedWorkflowSessionMemory,
            WorkflowTestRunService,
        )

        skill = self._create_workflow_skill()
        service = WorkflowTestRunService(self.db)
        prepared = service.prepare(skill.id, self._valid_request(stream_output=True))

        class _FakeEngine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                kwargs["on_node_start"]("start", "start", node_execution_id="exec_start")
                kwargs["on_node_end"]("start", "ok", node_execution_id="exec_start")
                kwargs["on_node_start"]("llm_1", "agent", node_execution_id="exec_agent_1")
                kwargs["on_node_output_delta"]("llm_1", "A", node_execution_id="exec_agent_1")
                kwargs["on_tool_call_start"](
                    "tool_1",
                    "search_entries",
                    {"q": "hello"},
                    node_id="llm_1",
                    node_type="agent",
                    node_execution_id="exec_agent_1",
                    agent_round=1,
                    tool_call_index=1,
                    tool_kind="tool",
                )
                kwargs["on_tool_call_end"](
                    "tool_1",
                    "completed",
                    "{}",
                    node_id="llm_1",
                    node_type="agent",
                    node_execution_id="exec_agent_1",
                    agent_round=1,
                    tool_call_index=1,
                    tool_kind="tool",
                )
                yield "A"
                kwargs["on_node_output_delta"]("llm_1", "B", node_execution_id="exec_agent_1")
                yield "B"
                kwargs["on_node_end"]("llm_1", "ok", node_execution_id="exec_agent_1")
                kwargs["on_node_snapshot"](
                    "llm_1",
                    "agent",
                    "ok",
                    {"userInput": "hello"},
                    {"response": "AB"},
                    None,
                    False,
                    node_execution_id="exec_agent_1",
                )

        with patch(
            "app.assistant_config.workflow_test_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ), patch(
            "app.assistant_config.workflow_test_service.WorkflowTestRunService._build_engine",
            return_value=_FakeEngine(),
        ), patch.object(
            service,
            "_compute_next_session_memory",
            return_value=PreparedWorkflowSessionMemory(conversation_summary="", skill_facts=[]),
        ):
            chunks = list(service.stream(prepared))

        events = _parse_sse_events(chunks)
        event_names = [name for name, _ in events]
        self.assertIn("run_start", event_names)
        self.assertIn("node_start", event_names)
        self.assertIn("node_output_delta", event_names)
        self.assertIn("tool_call_start", event_names)
        self.assertIn("tool_call_end", event_names)
        self.assertIn("node_snapshot", event_names)
        self.assertIn("content_delta", event_names)
        self.assertIn("run_end", event_names)

        run_end_payload = next(payload for name, payload in events if name == "run_end")
        self.assertEqual(run_end_payload["status"], "completed")
        self.assertEqual(run_end_payload["finalText"], "AB")
        snapshot_payload = next(payload for name, payload in events if name == "node_snapshot")
        self.assertEqual(snapshot_payload["nodeId"], "llm_1")
        self.assertEqual(snapshot_payload["nodeType"], "agent")
        self.assertEqual(snapshot_payload["status"], "ok")
        self.assertEqual(snapshot_payload["input"], {"userInput": "hello"})
        self.assertEqual(snapshot_payload["output"], {"response": "AB"})
        self.assertEqual(snapshot_payload["nodeExecutionId"], "exec_agent_1")

        node_start_payload = next(payload for name, payload in events if name == "node_start" and payload["nodeId"] == "llm_1")
        self.assertEqual(node_start_payload["nodeExecutionId"], "exec_agent_1")

        tool_start_payload = next(payload for name, payload in events if name == "tool_call_start")
        self.assertEqual(tool_start_payload["nodeId"], "llm_1")
        self.assertEqual(tool_start_payload["nodeType"], "agent")
        self.assertEqual(tool_start_payload["nodeExecutionId"], "exec_agent_1")
        self.assertEqual(tool_start_payload["agentRound"], 1)
        self.assertEqual(tool_start_payload["toolCallIndex"], 1)
        self.assertEqual(tool_start_payload["toolKind"], "tool")
        self.assertIn("startedAt", tool_start_payload)

        tool_end_payload = next(payload for name, payload in events if name == "tool_call_end")
        self.assertEqual(tool_end_payload["nodeExecutionId"], "exec_agent_1")
        self.assertIn("endedAt", tool_end_payload)
        self.assertIn("durationMs", tool_end_payload)

    def test_stream_emits_env_snapshot_payload(self) -> None:
        from app.assistant_config.workflow_test_service import (
            PreparedWorkflowSessionMemory,
            WorkflowTestRunService,
        )

        skill = self._create_workflow_skill()
        service = WorkflowTestRunService(self.db)
        prepared = service.prepare(skill.id, self._valid_request(stream_output=False))

        class _FakeEngine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                kwargs["on_node_start"]("start", "start")
                kwargs["on_node_end"]("start", "ok")
                kwargs["on_node_snapshot"](
                    "assign_1",
                    "variable_assign",
                    "ok",
                    {
                        "variableName": "counter",
                        "operation": "increment",
                        "resolvedValuePreview": "2",
                        "currentEnvValue": 1,
                    },
                    {
                        "variable": "counter",
                        "operation": "increment",
                        "before": 1,
                        "after": 3,
                    },
                    None,
                    False,
                )
                yield ""

        with patch(
            "app.assistant_config.workflow_test_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ), patch(
            "app.assistant_config.workflow_test_service.WorkflowTestRunService._build_engine",
            return_value=_FakeEngine(),
        ), patch.object(
            service,
            "_compute_next_session_memory",
            return_value=PreparedWorkflowSessionMemory(conversation_summary="", skill_facts=[]),
        ):
            chunks = list(service.stream(prepared))

        events = _parse_sse_events(chunks)
        snapshot_payload = next(payload for name, payload in events if name == "node_snapshot")
        self.assertEqual(snapshot_payload["nodeType"], "variable_assign")
        self.assertEqual(snapshot_payload["input"]["variableName"], "counter")
        self.assertEqual(snapshot_payload["output"]["after"], 3)

    def test_stream_emits_human_approval_events(self) -> None:
        from app.assistant_config.workflow_test_service import (
            PreparedWorkflowSessionMemory,
            WorkflowTestRunService,
        )

        skill = self._create_workflow_skill()
        service = WorkflowTestRunService(self.db)
        prepared = service.prepare(skill.id, self._valid_request(stream_output=False))

        class _FakeEngine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                approval = {
                    "id": "approval_1",
                    "runId": kwargs["runtime_context"]["run_id"],
                    "channelType": "workflow_test",
                    "conversationId": None,
                    "messageId": None,
                    "workflowId": kwargs["runtime_context"]["workflow_id"],
                    "skillId": kwargs["runtime_context"]["skill_id"],
                    "nodeId": "hitl_1",
                    "nodeLabel": "Confirm",
                    "status": "pending",
                    "requestPayload": {"instruction": "confirm"},
                    "fieldSchema": [{"name": "title", "type": "string", "required": True}],
                    "initialValues": {"title": "draft"},
                    "submittedValues": {},
                    "decision": None,
                    "comment": None,
                    "resolvedAt": None,
                    "createdAt": "2026-01-01T00:00:00+00:00",
                    "updatedAt": "2026-01-01T00:00:00+00:00",
                }
                kwargs["on_human_approval_requested"](approval)
                resolved = {**approval, "status": "approved", "decision": "approved", "submittedValues": {"title": "ok"}}
                kwargs["on_human_approval_resolved"](resolved)
                yield ""

        with patch(
            "app.assistant_config.workflow_test_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ), patch(
            "app.assistant_config.workflow_test_service.WorkflowTestRunService._build_engine",
            return_value=_FakeEngine(),
        ), patch.object(
            service,
            "_compute_next_session_memory",
            return_value=PreparedWorkflowSessionMemory(conversation_summary="", skill_facts=[]),
        ):
            chunks = list(service.stream(prepared))

        events = _parse_sse_events(chunks)
        event_names = [name for name, _ in events]
        self.assertIn("human_approval_requested", event_names)
        self.assertIn("human_approval_resolved", event_names)

        requested = next(payload for name, payload in events if name == "human_approval_requested")
        resolved = next(payload for name, payload in events if name == "human_approval_resolved")
        self.assertEqual(requested["approval"]["status"], "pending")
        self.assertEqual(resolved["approval"]["status"], "approved")

    def test_stream_aggregates_high_frequency_delta_events(self) -> None:
        from app.assistant_config.workflow_test_service import (
            PreparedWorkflowSessionMemory,
            WorkflowTestRunService,
        )

        skill = self._create_workflow_skill()
        service = WorkflowTestRunService(self.db)
        prepared = service.prepare(skill.id, self._valid_request(stream_output=True))
        token_count = 200

        class _FakeEngine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                kwargs["on_node_start"]("llm_1", "llm")
                for _ in range(token_count):
                    kwargs["on_node_output_delta"]("llm_1", "x")
                    yield "x"
                kwargs["on_node_end"]("llm_1", "ok")

        with patch(
            "app.assistant_config.workflow_test_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ), patch(
            "app.assistant_config.workflow_test_service.WorkflowTestRunService._build_engine",
            return_value=_FakeEngine(),
        ), patch.object(
            service,
            "_compute_next_session_memory",
            return_value=PreparedWorkflowSessionMemory(conversation_summary="", skill_facts=[]),
        ):
            chunks = list(service.stream(prepared))

        events = _parse_sse_events(chunks)
        content_deltas = [payload["delta"] for name, payload in events if name == "content_delta"]
        node_deltas = [
            payload["delta"]
            for name, payload in events
            if name == "node_output_delta" and payload.get("nodeId") == "llm_1"
        ]

        self.assertGreater(len(content_deltas), 0)
        self.assertGreater(len(node_deltas), 0)
        self.assertLess(len(content_deltas), token_count)
        self.assertLess(len(node_deltas), token_count)
        self.assertEqual("".join(content_deltas), "x" * token_count)
        self.assertEqual("".join(node_deltas), "x" * token_count)

        run_end_payload = next(payload for name, payload in events if name == "run_end")
        self.assertEqual(run_end_payload["finalText"], "x" * token_count)

    def test_stream_emits_bootstrap_error_when_no_model_config(self) -> None:
        from app.assistant_config.workflow_test_service import WorkflowTestRunService

        skill = self._create_workflow_skill()
        service = WorkflowTestRunService(self.db)
        prepared = service.prepare(skill.id, self._valid_request(stream_output=False))

        with patch(
            "app.assistant_config.workflow_test_service.resolve_openai_compat_config",
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

    def test_stream_emits_bootstrap_error_when_config_resolution_raises(self) -> None:
        from app.assistant_config.workflow_test_service import WorkflowTestRunService

        skill = self._create_workflow_skill()
        service = WorkflowTestRunService(self.db)
        prepared = service.prepare(skill.id, self._valid_request(stream_output=True))

        with patch(
            "app.assistant_config.workflow_test_service.resolve_openai_compat_config",
            side_effect=RuntimeError("bootstrap failure"),
        ):
            chunks = list(service.stream(prepared))

        events = _parse_sse_events(chunks)
        run_error_payload = next(payload for name, payload in events if name == "run_error")
        run_end_payload = next(payload for name, payload in events if name == "run_end")

        self.assertEqual(run_error_payload["stage"], "bootstrap")
        self.assertEqual(run_end_payload["status"], "error")

    def test_prepare_rejects_structured_history_and_session_memory(self) -> None:
        from app.assistant_config.schemas import (
            WorkflowEdgeInput,
            WorkflowInput,
            WorkflowNodeInput,
            WorkflowTestRunRequest,
        )

        workflow = WorkflowInput(
            nodes=[
                WorkflowNodeInput(
                    node_id="start",
                    node_type="start",
                    label="Start",
                    config={
                        "inputMode": "structured",
                        "structuredFields": [
                            {"name": "title", "type": "string", "required": True},
                        ],
                    },
                ),
                WorkflowNodeInput(node_id="output_1", node_type="output", label="Output", config={"textTemplate": "ok"}),
            ],
            edges=[
                WorkflowEdgeInput(
                    edge_id="e1",
                    source_node_id="start",
                    target_node_id="output_1",
                    source_handle="output",
                    target_handle="input",
                ),
            ],
        )

        with self.assertRaises(ValueError):
            WorkflowTestRunRequest(
                workflow=workflow,
                structured_input={"title": "hello"},
                history=[{"role": "user", "content": "old turn"}],
                session_memory={"conversationSummary": "summary", "skillFacts": ["fact"]},
            )

    def test_stream_passes_history_and_session_memory_to_engine(self) -> None:
        from app.assistant_config.schemas import WorkflowTestRunRequest
        from app.assistant_config.workflow_test_service import (
            PreparedWorkflowSessionMemory,
            WorkflowTestRunService,
        )

        skill = self._create_workflow_skill()
        service = WorkflowTestRunService(self.db)
        base_request = self._valid_request(stream_output=True)
        request = WorkflowTestRunRequest(
            workflow=base_request.workflow,
            user_input=base_request.user_input,
            stream_output=base_request.stream_output,
            session_id=uuid4(),
            history=[
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
            ],
            session_memory={
                "conversationSummary": "旧摘要",
                "skillFacts": ["事实A"],
            },
        )
        prepared = service.prepare(skill.id, request)
        captured_kwargs: dict[str, object] = {}

        class _FakeEngine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                captured_kwargs.update(kwargs)
                yield "AB"

        with patch(
            "app.assistant_config.workflow_test_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ), patch(
            "app.assistant_config.workflow_test_service.WorkflowTestRunService._build_engine",
            return_value=_FakeEngine(),
        ), patch.object(
            service,
            "_compute_next_session_memory",
            return_value=PreparedWorkflowSessionMemory(
                conversation_summary="新摘要",
                skill_facts=["事实A", "事实B"],
            ),
        ):
            chunks = list(service.stream(prepared))

        self.assertEqual(
            captured_kwargs["history"],
            [
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
            ],
        )
        runtime_context = captured_kwargs["runtime_context"]
        assert isinstance(runtime_context, dict)
        self.assertEqual(runtime_context["session_memory"], {"conversationSummary": "旧摘要", "skillFacts": ["事实A"]})
        self.assertTrue(str(runtime_context["conversation_id"]).startswith("workflow_test_session:"))

        events = _parse_sse_events(chunks)
        run_end_payload = next(payload for name, payload in events if name == "run_end")
        self.assertEqual(
            run_end_payload["sessionMemory"],
            {"conversationSummary": "新摘要", "skillFacts": ["事实A", "事实B"]},
        )

    def test_stream_memory_compute_fail_open_keeps_previous_session_memory(self) -> None:
        from app.assistant_config.schemas import WorkflowTestRunRequest
        from app.assistant_config.workflow_test_service import WorkflowTestRunService

        skill = self._create_workflow_skill()
        service = WorkflowTestRunService(self.db)
        base_request = self._valid_request(stream_output=False)
        request = WorkflowTestRunRequest(
            workflow=base_request.workflow,
            user_input=base_request.user_input,
            stream_output=base_request.stream_output,
            session_memory={
                "conversationSummary": "旧摘要",
                "skillFacts": ["事实A"],
            },
        )
        prepared = service.prepare(skill.id, request)

        class _FakeEngine:
            def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                yield "AB"

        with patch(
            "app.assistant_config.workflow_test_service.resolve_openai_compat_config",
            return_value=SimpleNamespace(api_key="k", base_url="https://api.example.com", model="gpt-test"),
        ), patch(
            "app.assistant_config.workflow_test_service.WorkflowTestRunService._build_engine",
            return_value=_FakeEngine(),
        ), patch.object(
            service._memory_computation_service,
            "compute_next_l1_summary",
            side_effect=RuntimeError("l1 failed"),
        ), patch.object(
            service._memory_computation_service,
            "compute_next_l2_facts",
            side_effect=RuntimeError("l2 failed"),
        ):
            chunks = list(service.stream(prepared))

        events = _parse_sse_events(chunks)
        run_end_payload = next(payload for name, payload in events if name == "run_end")
        self.assertEqual(run_end_payload["status"], "completed")
        self.assertEqual(
            run_end_payload["sessionMemory"],
            {"conversationSummary": "旧摘要", "skillFacts": ["事实A"]},
        )


if __name__ == "__main__":
    unittest.main()

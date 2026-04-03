from __future__ import annotations

import json
import unittest
from collections import deque

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


def _sse(event: str, data: dict) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _decode_event(raw: bytes) -> tuple[str, dict]:
    text = raw.decode("utf-8")
    lines = [line for line in text.splitlines() if line]
    event = lines[0].split("event: ", 1)[1]
    payload = lines[1].split("data: ", 1)[1]
    return event, json.loads(payload)


class ChatEventAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from app.assistant.orchestration.chat_events import ChatEventAdapter  # noqa: E402

        self.queue: deque[bytes] = deque()
        self.adapter = ChatEventAdapter(_sse, self.queue)

    def _last_event(self) -> tuple[str, dict]:
        self.assertTrue(self.queue)
        return _decode_event(self.queue[-1])

    def test_node_start_emits_workflow_steps_snapshot(self) -> None:
        self.adapter.on_node_start("start", "start", "开始")

        event, payload = self._last_event()
        self.assertEqual(event, "workflow_steps")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["steps"][0]["nodeId"], "start")
        self.assertEqual(payload["steps"][0]["nodeType"], "start")
        self.assertEqual(payload["steps"][0]["nodeLabel"], "开始")

    def test_parallel_node_start_updates_count(self) -> None:
        self.adapter.on_node_start("start", "start", "开始")
        self.adapter.on_node_start("llm_1", "llm", "生成草稿")

        event, payload = self._last_event()
        self.assertEqual(event, "workflow_steps")
        self.assertEqual(payload["count"], 2)
        node_ids = {item["nodeId"] for item in payload["steps"]}
        self.assertSetEqual(node_ids, {"start", "llm_1"})

    def test_node_end_removes_only_target_node(self) -> None:
        self.adapter.on_node_start("start", "start", "开始")
        self.adapter.on_node_start("llm_1", "llm", "生成草稿")
        self.adapter.on_node_end("start", "ok", "开始")

        event, payload = self._last_event()
        self.assertEqual(event, "workflow_steps")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["steps"][0]["nodeId"], "llm_1")

    def test_skill_end_clears_workflow_steps(self) -> None:
        self.adapter.on_skill_start("skill_1", "smart_capture", False)
        self.adapter.on_node_start("start", "start", "开始")
        self.adapter.on_skill_end("skill_1", "completed")

        event, payload = self._last_event()
        self.assertEqual(event, "workflow_steps")
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["steps"], [])

    def test_tool_call_events_preserve_agent_trace_context(self) -> None:
        self.adapter.on_tool_call_start(
            "tool_1",
            "list_tags",
            {"query": "roadmap"},
            node_id="agent_1",
            node_type="agent",
            node_execution_id="exec_1",
            agent_round=2,
            tool_call_index=1,
            tool_kind="knowledge",
            started_at="2026-03-13T10:00:00+00:00",
        )
        self.adapter.on_tool_call_end(
            "tool_1",
            "completed",
            "{\"ok\":true}",
            node_id="agent_1",
            node_type="agent",
            node_execution_id="exec_1",
            agent_round=2,
            tool_call_index=1,
            tool_kind="knowledge",
            started_at="2026-03-13T10:00:00+00:00",
            ended_at="2026-03-13T10:00:01+00:00",
            duration_ms=1000,
        )

        self.assertEqual(self.adapter.tool_calls_data[0]["nodeId"], "agent_1")
        self.assertEqual(self.adapter.tool_calls_data[0]["nodeType"], "agent")
        self.assertEqual(self.adapter.tool_calls_data[0]["nodeExecutionId"], "exec_1")
        self.assertEqual(self.adapter.tool_calls_data[0]["agentRound"], 2)
        self.assertEqual(self.adapter.tool_calls_data[0]["toolKind"], "knowledge")
        self.assertEqual(self.adapter.tool_results_data[0]["durationMs"], 1000)
        self.assertEqual(self.adapter.tool_results_data[0]["endedAt"], "2026-03-13T10:00:01+00:00")

        event, payload = self._last_event()
        self.assertEqual(event, "tool_call_end")
        self.assertEqual(payload["nodeId"], "agent_1")
        self.assertEqual(payload["nodeExecutionId"], "exec_1")
        self.assertEqual(payload["toolKind"], "knowledge")
        self.assertEqual(payload["durationMs"], 1000)

    def test_tool_call_events_fill_timing_when_not_provided(self) -> None:
        self.adapter.on_tool_call_start("tool_2", "list_tags", {"query": "roadmap"})
        self.adapter.on_tool_call_end("tool_2", "completed", "{}")

        self.assertIn("startedAt", self.adapter.tool_calls_data[0])
        self.assertIn("startedAt", self.adapter.tool_results_data[0])
        self.assertIn("endedAt", self.adapter.tool_results_data[0])
        self.assertIn("durationMs", self.adapter.tool_results_data[0])
        self.assertIsInstance(self.adapter.tool_results_data[0]["durationMs"], int)

    def test_tool_call_start_accepts_tool_name_keyword(self) -> None:
        self.adapter.on_tool_call_start(
            tool_call_id="tool_3",
            tool_name="list_tags",
            args={"query": "roadmap"},
            node_id="agent_2",
            unexpected_field="ignored",
        )

        self.assertEqual(self.adapter.tool_calls_data[0]["name"], "list_tags")
        self.assertEqual(self.adapter.tool_calls_data[0]["nodeId"], "agent_2")

        event, payload = self._last_event()
        self.assertEqual(event, "tool_call_start")
        self.assertEqual(payload["name"], "list_tags")
        self.assertEqual(payload["toolCallId"], "tool_3")


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

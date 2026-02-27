from __future__ import annotations

from collections import deque
from typing import Callable


class ChatEventAdapter:
    def __init__(self, sse: Callable[[str, dict], bytes], event_queue: deque[bytes]):
        self._sse = sse
        self._event_queue = event_queue
        self._tool_calls_data: list[dict] = []
        self._tool_results_data: list[dict] = []
        self._skill_calls_data: list[dict] = []
        self._analysis_steps: list[dict] = []

    @property
    def tool_calls_data(self) -> list[dict]:
        return self._tool_calls_data

    @property
    def tool_results_data(self) -> list[dict]:
        return self._tool_results_data

    @property
    def skill_calls_data(self) -> list[dict]:
        return self._skill_calls_data

    @property
    def analysis_steps(self) -> list[dict]:
        return self._analysis_steps

    def _emit(self, event: str, data: dict) -> None:
        self._event_queue.append(self._sse(event, data))

    def _ensure_analysis_step(self, analysis_id: str) -> dict:
        for step in self._analysis_steps:
            if step.get("id") == analysis_id:
                return step
        step = {"id": analysis_id, "content": "", "status": "running"}
        self._analysis_steps.append(step)
        return step

    def on_tool_call_start(self, tool_call_id: str, name: str, args: dict, hidden: bool = False) -> None:
        hidden = bool(hidden)
        self._tool_calls_data.append({"id": tool_call_id, "name": name, "args": args, "hidden": hidden})
        self._emit("tool_call_start", {"toolCallId": tool_call_id, "name": name, "args": args, "hidden": hidden})

    def on_tool_call_end(self, tool_call_id: str, status: str, result: str) -> None:
        self._tool_results_data.append({"id": tool_call_id, "status": status, "result": result})
        self._emit("tool_call_end", {"toolCallId": tool_call_id, "status": status, "result": result})

    def on_skill_start(self, skill_id: str, name: str, hidden: bool) -> None:
        self._skill_calls_data.append({"id": skill_id, "name": name, "status": "running", "hidden": hidden})
        self._emit("skill_start", {"id": skill_id, "name": name, "hidden": hidden})

    def on_skill_end(self, skill_id: str, status: str) -> None:
        for skill_call in self._skill_calls_data:
            if skill_call["id"] == skill_id:
                skill_call["status"] = status
                break
        self._emit("skill_end", {"id": skill_id, "status": status})

    def on_analysis_start(self, analysis_id: str) -> None:
        self._ensure_analysis_step(analysis_id)
        self._emit("analysis_start", {"id": analysis_id})

    def on_analysis_delta(self, analysis_id: str, delta: str) -> None:
        step = self._ensure_analysis_step(analysis_id)
        step["content"] += delta
        self._emit("analysis_delta", {"id": analysis_id, "delta": delta})

    def on_analysis_end(self, analysis_id: str) -> None:
        step = self._ensure_analysis_step(analysis_id)
        step["status"] = "completed"
        self._emit("analysis_end", {"id": analysis_id})

    def on_human_approval_requested(self, approval: dict) -> None:
        self._emit("human_approval_requested", {"approval": approval})

    def on_human_approval_resolved(self, approval: dict) -> None:
        self._emit("human_approval_resolved", {"approval": approval})


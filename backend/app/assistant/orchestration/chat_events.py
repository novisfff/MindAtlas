from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import time
from typing import Any, Callable


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatEventAdapter:
    def __init__(
        self,
        emit_or_sse: Callable[[str, dict], None] | Callable[[str, dict], bytes],
        event_queue: deque[bytes] | None = None,
    ):
        if event_queue is None:
            self._emit_cb = emit_or_sse  # type: ignore[assignment]
        else:
            self._emit_cb = lambda event, data: event_queue.append(emit_or_sse(event, data))  # type: ignore[misc]
        self._tool_calls_data: list[dict] = []
        self._tool_results_data: list[dict] = []
        self._skill_calls_data: list[dict] = []
        self._analysis_steps: list[dict] = []
        self._active_workflow_steps: dict[str, dict[str, str]] = {}
        self._tool_started_at: dict[str, str] = {}
        self._tool_started_perf: dict[str, float] = {}

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
        self._emit_cb(event, data)

    def _emit_workflow_steps_snapshot(self) -> None:
        steps = list(self._active_workflow_steps.values())
        self._emit("workflow_steps", {"steps": steps, "count": len(steps)})

    def _ensure_analysis_step(self, analysis_id: str) -> dict:
        for step in self._analysis_steps:
            if step.get("id") == analysis_id:
                return step
        step = {"id": analysis_id, "content": "", "status": "running"}
        self._analysis_steps.append(step)
        return step

    def on_tool_call_start(
        self,
        tool_call_id: str,
        tool_name: str,
        args: dict,
        hidden: bool = False,
        node_id: str = "",
        node_type: str = "",
        node_execution_id: str = "",
        agent_round: int | None = None,
        tool_call_index: int | None = None,
        tool_kind: str = "",
        started_at: str | None = None,
        name: str = "",
        **_extra: Any,
    ) -> None:
        effective_name = str(tool_name or name or "").strip()
        hidden = bool(hidden)
        effective_started_at = started_at or _utc_iso_now()
        self._tool_started_at[tool_call_id] = effective_started_at
        self._tool_started_perf[tool_call_id] = time.perf_counter()

        payload = {
            "id": tool_call_id,
            "name": effective_name,
            "args": args,
            "hidden": hidden,
            "startedAt": effective_started_at,
        }
        node_id = str(node_id or "").strip()
        node_type = str(node_type or "").strip()
        node_execution_id = str(node_execution_id or "").strip()
        tool_kind = str(tool_kind or "").strip().lower()
        if node_id:
            payload["nodeId"] = node_id
        if node_type:
            payload["nodeType"] = node_type
        if node_execution_id:
            payload["nodeExecutionId"] = node_execution_id
        if isinstance(agent_round, int):
            payload["agentRound"] = agent_round
        if isinstance(tool_call_index, int):
            payload["toolCallIndex"] = tool_call_index
        if tool_kind:
            payload["toolKind"] = tool_kind
        self._tool_calls_data.append(payload)
        self._emit(
            "tool_call_start",
            {
                "toolCallId": tool_call_id,
                "name": effective_name,
                "args": args,
                "hidden": hidden,
                "startedAt": effective_started_at,
                **({ "nodeId": node_id } if node_id else {}),
                **({ "nodeType": node_type } if node_type else {}),
                **({ "nodeExecutionId": node_execution_id } if node_execution_id else {}),
                **({ "agentRound": agent_round } if isinstance(agent_round, int) else {}),
                **({ "toolCallIndex": tool_call_index } if isinstance(tool_call_index, int) else {}),
                **({ "toolKind": tool_kind } if tool_kind else {}),
            },
        )

    def on_tool_call_end(
        self,
        tool_call_id: str,
        status: str,
        result: str,
        node_id: str = "",
        node_type: str = "",
        node_execution_id: str = "",
        agent_round: int | None = None,
        tool_call_index: int | None = None,
        tool_kind: str = "",
        started_at: str | None = None,
        ended_at: str | None = None,
        duration_ms: int | None = None,
        **_extra: Any,
    ) -> None:
        effective_started_at = started_at or self._tool_started_at.pop(tool_call_id, None)
        started_perf = self._tool_started_perf.pop(tool_call_id, None)
        effective_ended_at = ended_at or _utc_iso_now()
        effective_duration_ms = duration_ms
        if not isinstance(effective_duration_ms, int) and isinstance(started_perf, (int, float)):
            effective_duration_ms = max(0, int((time.perf_counter() - started_perf) * 1000))

        payload = {
            "id": tool_call_id,
            "status": status,
            "result": result,
        }
        node_id = str(node_id or "").strip()
        node_type = str(node_type or "").strip()
        node_execution_id = str(node_execution_id or "").strip()
        tool_kind = str(tool_kind or "").strip().lower()
        if node_id:
            payload["nodeId"] = node_id
        if node_type:
            payload["nodeType"] = node_type
        if node_execution_id:
            payload["nodeExecutionId"] = node_execution_id
        if isinstance(agent_round, int):
            payload["agentRound"] = agent_round
        if isinstance(tool_call_index, int):
            payload["toolCallIndex"] = tool_call_index
        if tool_kind:
            payload["toolKind"] = tool_kind
        if effective_started_at:
            payload["startedAt"] = effective_started_at
        if effective_ended_at:
            payload["endedAt"] = effective_ended_at
        if isinstance(effective_duration_ms, int):
            payload["durationMs"] = effective_duration_ms
        self._tool_results_data.append(payload)
        self._emit(
            "tool_call_end",
            {
                "toolCallId": tool_call_id,
                "status": status,
                "result": result,
                **({ "nodeId": node_id } if node_id else {}),
                **({ "nodeType": node_type } if node_type else {}),
                **({ "nodeExecutionId": node_execution_id } if node_execution_id else {}),
                **({ "agentRound": agent_round } if isinstance(agent_round, int) else {}),
                **({ "toolCallIndex": tool_call_index } if isinstance(tool_call_index, int) else {}),
                **({ "toolKind": tool_kind } if tool_kind else {}),
                **({ "startedAt": effective_started_at } if effective_started_at else {}),
                **({ "endedAt": effective_ended_at } if effective_ended_at else {}),
                **({ "durationMs": effective_duration_ms } if isinstance(effective_duration_ms, int) else {}),
            },
        )

    def on_skill_start(self, skill_id: str, name: str, hidden: bool) -> None:
        self._skill_calls_data.append({"id": skill_id, "name": name, "status": "running", "hidden": hidden})
        self._emit("skill_start", {"id": skill_id, "name": name, "hidden": hidden})

    def on_skill_end(self, skill_id: str, status: str) -> None:
        for skill_call in self._skill_calls_data:
            if skill_call["id"] == skill_id:
                skill_call["status"] = status
                break
        self._emit("skill_end", {"id": skill_id, "status": status})
        self._active_workflow_steps.clear()
        self._emit_workflow_steps_snapshot()

    def on_node_start(self, node_id: str, node_type: str, node_label: str = "") -> None:
        key = str(node_id or "").strip()
        if not key:
            return
        self._active_workflow_steps[key] = {
            "nodeId": key,
            "nodeType": str(node_type or "").strip(),
            "nodeLabel": str(node_label or "").strip(),
        }
        self._emit_workflow_steps_snapshot()

    def on_node_end(self, node_id: str, status: str, node_label: str = "") -> None:
        del status
        del node_label
        key = str(node_id or "").strip()
        if not key:
            return
        self._active_workflow_steps.pop(key, None)
        self._emit_workflow_steps_snapshot()

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

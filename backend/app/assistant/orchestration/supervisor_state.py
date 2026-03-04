from __future__ import annotations

from queue import Queue
from typing import Any, Callable, Literal, TypedDict

ExecutionStatus = Literal["pending", "running", "completed", "failed"]


class SupervisorState(TypedDict, total=False):
    history: list[dict[str, Any]]
    user_input: str
    runtime_context: dict[str, Any]

    route_skill: str
    route_reason: str

    selected_skill: str
    selected_skill_hidden: bool

    execution_status: ExecutionStatus
    error_code: str
    error_message: str

    # stream_queue 用于在图节点内部向外部流式转发内容片段。
    stream_queue: Queue[str]

    on_tool_call_start: Callable[[str, str, dict], None] | None
    on_tool_call_end: Callable[[str, str, str], None] | None
    on_skill_start: Callable[[str, str, bool], None] | None
    on_skill_end: Callable[[str, str], None] | None
    on_analysis_start: Callable[[str], None] | None
    on_analysis_delta: Callable[[str, str], None] | None
    on_analysis_end: Callable[[str], None] | None
    on_node_start: Callable[[str, str, str], None] | None
    on_node_end: Callable[[str, str, str], None] | None
    on_human_approval_requested: Callable[[dict], None] | None
    on_human_approval_resolved: Callable[[dict], None] | None
    cancel_checker: Callable[[], bool] | None

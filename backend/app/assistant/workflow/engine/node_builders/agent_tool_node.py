from __future__ import annotations

import uuid
from typing import Any, Callable

from langchain_core.messages import ToolMessage

from app.assistant.workflow.engine import engine as engine_runtime
from app.assistant.workflow.engine.runtime_helpers import (
    emit,
    logger,
    stringify,
)
from app.assistant.workflow.engine.state import AssistantState

def build_tool_node(
    tools: list,
    db_bind: Any,
) -> Callable[[AssistantState], dict]:
    """Task 3.2: 构建 agent_loop 的 tool 节点。"""
    tool_map = {getattr(t, "name", ""): t for t in tools}

    def tool_node(state: AssistantState) -> dict:
        metadata = state.get("metadata", {})
        messages = state.get("messages", [])
        last_msg = messages[-1] if messages else None
        tool_calls = getattr(last_msg, "tool_calls", []) if last_msg else []

        new_messages: list[ToolMessage] = []
        for tc in tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("args", {})
            tool_call_id = tc.get("id", f"tool_{uuid.uuid4().hex[:8]}")

            emit(metadata, "on_tool_call_start",
                  tool_call_id=tool_call_id, tool_name=tool_name, args=tool_args)

            tool = tool_map.get(tool_name)
            status = "completed"
            result = ""
            if not tool:
                status = "error"
                result = f"工具 {tool_name} 不存在"
            else:
                wrapped = engine_runtime._wrap_tool_with_db(tool, db_bind)
                try:
                    result = wrapped(**tool_args)
                except Exception as e:
                    logger.error("Tool %s failed: %s", tool_name, e)
                    status = "error"
                    result = f"工具执行失败: {e}"

            result_str = stringify(result)
            emit(metadata, "on_tool_call_end",
                  tool_call_id=tool_call_id, status=status, result=result_str)
            new_messages.append(ToolMessage(content=result_str, tool_call_id=tool_call_id))

        return {"messages": new_messages}

    return tool_node

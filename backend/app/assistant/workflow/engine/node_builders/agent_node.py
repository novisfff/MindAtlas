from __future__ import annotations

from typing import Callable

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI

from app.assistant.skill_catalog.base import SkillDefinition
from app.assistant.workflow.engine.runtime_helpers import AGENT_MAX_ITERATIONS, emit
from app.assistant.workflow.engine.state import AssistantState

def build_agent_node(
    skill: SkillDefinition,
    llm: ChatOpenAI,
    tools: list,
) -> Callable[[AssistantState], dict]:
    """Task 3.1: 构建 agent_loop 的 agent 节点。"""
    llm_with_tools = llm.bind_tools(tools, parallel_tool_calls=False) if tools else llm

    def agent_node(state: AssistantState) -> dict:
        metadata = state.get("metadata", {})
        iteration = state.get("iteration_count", 0)

        if iteration >= AGENT_MAX_ITERATIONS:
            return {
                "messages": [AIMessage(content="工具调用次数过多，未能完成任务。请尝试缩小问题范围或换一种问法。")],
                "iteration_count": iteration,
            }

        merged: AIMessageChunk | None = None
        final_chunks: list[str] = []
        for chunk in llm_with_tools.stream(state["messages"]):
            if merged is None:
                merged = chunk
            else:
                merged = merged + chunk
            if chunk.content:
                final_chunks.append(chunk.content)
                emit(metadata, "on_content_delta", chunk=chunk.content)

        if merged is None:
            return {
                "messages": [AIMessage(content="")],
                "iteration_count": iteration + 1,
            }

        if getattr(merged, "tool_calls", None):
            return {
                "messages": [
                    AIMessage(
                        content=merged.content or "",
                        tool_calls=merged.tool_calls,
                        additional_kwargs=merged.additional_kwargs,
                        response_metadata=merged.response_metadata,
                    )
                ],
                "iteration_count": iteration + 1,
            }

        final_text = "".join(final_chunks)
        return {
            "messages": [AIMessage(content=final_text)],
            "iteration_count": iteration + 1,
        }

    return agent_node

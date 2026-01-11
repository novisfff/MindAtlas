"""LangChain Agent 配置"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Callable, Iterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from sqlalchemy.orm import Session, sessionmaker

from app.assistant.tools import (
    search_entries,
    get_entry_detail,
    create_entry,
    get_statistics,
    get_entries_by_time_range,
    analyze_activity,
    list_entry_types,
    list_tags,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是 MindAtlas 的 AI 助手，专门帮助用户管理个人知识库和经历记录。

## 你的核心能力

你可以通过工具直接操作用户的数据：

### 记录管理
- **search_entries(keyword, type_code, limit)** - 搜索记录
  - 用户说"找一下..."、"有没有..."、"查查..." → 调用此工具
- **get_entry_detail(entry_id)** - 获取记录详情
  - 用户想看某条记录的完整内容 → 调用此工具
- **create_entry(title, content, type_code)** - 创建新记录
  - 用户说"记录一下..."、"帮我添加..."、"创建..." → 调用此工具

### 统计分析
- **get_statistics()** - 获取整体统计
  - 用户问"我有多少记录"、"统计一下" → 调用此工具
- **get_entries_by_time_range(start_date, end_date)** - 按时间查询
  - 用户问"上周..."、"这个月..."、"去年..." → 调用此工具
- **analyze_activity(period)** - 活动分析
  - 用户问"最近活跃度"、"记录频率" → 调用此工具

### 辅助查询
- **list_entry_types()** - 列出可用类型
- **list_tags()** - 列出所有标签

## 行为准则

1. **主动执行**: 用户表达意图后，直接调用工具完成，不要只是回复文字
2. **信息补全**: 创建记录时如果缺少类型，先调用 list_entry_types 获取可用类型，然后选择最合适的
3. **简洁回复**: 工具执行成功后，用一句话确认结果，不要重复工具返回的全部内容
4. **友好语气**: 用自然、亲切的中文回复

## 示例

用户: "帮我记录一下，今天学了 Python 装饰器"
- 调用 list_entry_types()
→ 调用 create_entry(title="学习 Python 装饰器", content="今天学习了 Python 装饰器的用法", type_code="knowledge")
→ 回复: "已为你创建记录「学习 Python 装饰器」✓"

用户: "找找关于 React 的笔记"
→ 调用 search_entries(keyword="React")
→ 回复: "找到 3 条相关记录：..."

当前日期：{current_date}
"""


def get_tools():
    """获取所有工具"""
    return [
        search_entries,
        get_entry_detail,
        create_entry,
        get_statistics,
        get_entries_by_time_range,
        analyze_activity,
        list_entry_types,
        list_tags,
    ]


class AssistantAgent:
    """AI 助手 Agent"""

    def __init__(self, api_key: str, base_url: str, model: str, db=None):
        self.llm = ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            streaming=True,
        )
        self.db = db  # 保存数据库会话引用
        self.tools = get_tools()
        self.llm_with_tools = self.llm.bind_tools(self.tools)

    def _new_tool_db_session(self) -> Session:
        """为工具调用创建新的数据库会话，避免跨线程复用同一个 Session。"""
        if self.db is not None:
            bind = self.db.get_bind()
            return sessionmaker(
                bind=bind,
                autocommit=False,
                autoflush=False,
                future=True,
            )()

        from app.database import SessionLocal
        return SessionLocal()

    def _build_messages(self, history: list[dict], user_input: str):
        """构建消息列表"""
        messages = [
            SystemMessage(content=SYSTEM_PROMPT.format(
                current_date=date.today().isoformat()
            ))
        ]
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))
        messages.append(HumanMessage(content=user_input))
        return messages

    def _get_tool_by_name(self, name: str):
        """根据名称获取工具"""
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

    def stream(
        self,
        history: list[dict],
        user_input: str,
        on_tool_call_start: Callable[[str, str, dict], None] | None = None,
        on_tool_call_end: Callable[[str, str, str], None] | None = None,
    ) -> Iterator[str]:
        """流式生成回复"""
        from app.assistant.tools._context import reset_current_db, set_current_db

        messages = self._build_messages(history, user_input)
        logger.info("Starting stream with %d tools bound", len(self.tools))

        # 流式调用 LLM，收集完整响应以检测工具调用
        collected_content: list[str] = []
        collected_tool_calls: list[dict] = []
        full_response: AIMessage | None = None

        for chunk in self.llm_with_tools.stream(messages):
            # 收集内容片段并立即输出
            if chunk.content:
                collected_content.append(chunk.content)
                yield chunk.content

            # 收集工具调用信息
            if hasattr(chunk, "tool_call_chunks") and chunk.tool_call_chunks:
                logger.debug("Received tool_call_chunks: %s", chunk.tool_call_chunks)
                for tc_chunk in chunk.tool_call_chunks:
                    idx = tc_chunk.get("index", 0)
                    while len(collected_tool_calls) <= idx:
                        collected_tool_calls.append({"id": "", "name": "", "args": ""})
                    if tc_chunk.get("id"):
                        collected_tool_calls[idx]["id"] = tc_chunk["id"]
                    if tc_chunk.get("name"):
                        collected_tool_calls[idx]["name"] = tc_chunk["name"]
                    if tc_chunk.get("args"):
                        collected_tool_calls[idx]["args"] += tc_chunk["args"]

            # 保存最后的响应用于构建 AIMessage
            full_response = chunk

        # 日志：检查是否收集到工具调用
        logger.info("Stream finished. Collected %d tool calls: %s",
                    len(collected_tool_calls), collected_tool_calls)

        # 如果有工具调用，执行工具并继续对话
        if collected_tool_calls and any(tc.get("name") for tc in collected_tool_calls):
            # 构建 AIMessage 用于消息历史
            tool_calls_parsed = []
            for tc in collected_tool_calls:
                if tc.get("name"):
                    try:
                        args = json.loads(tc["args"]) if tc["args"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls_parsed.append({
                        "id": tc["id"] or f"tool_call_{len(tool_calls_parsed)}",
                        "name": tc["name"],
                        "args": args,
                    })

            ai_msg = AIMessage(
                content="".join(collected_content),
                tool_calls=tool_calls_parsed,
            )
            messages.append(ai_msg)

            # 执行每个工具调用
            for tc in tool_calls_parsed:
                tool_call_id = tc["id"]
                tool_name = tc["name"]
                tool_args = tc["args"]

                if on_tool_call_start:
                    on_tool_call_start(tool_call_id, tool_name, tool_args)
                    yield ""

                status = "completed"
                tool = self._get_tool_by_name(tool_name)
                if not tool:
                    status = "error"
                    result: Any = f"未知工具: {tool_name}"
                else:
                    tool_db: Session | None = None
                    token = None
                    try:
                        tool_db = self._new_tool_db_session()
                        token = set_current_db(tool_db)

                        tool_func = getattr(tool, "func", None)
                        if callable(tool_func):
                            result = tool_func(**tool_args)
                        else:
                            result = tool.invoke(tool_args)
                    except Exception as e:
                        logger.error("Tool %s failed: %s", tool_name, e, exc_info=True)
                        status = "error"
                        result = f"工具执行失败: {tool_name} - {e}"
                    finally:
                        if tool_db is not None:
                            try:
                                tool_db.close()
                            except Exception:
                                logger.warning("Failed to close tool DB session", exc_info=True)
                        if token is not None:
                            reset_current_db(token)

                if not isinstance(result, str):
                    result = str(result)

                messages.append(ToolMessage(content=result, tool_call_id=tool_call_id))

                if on_tool_call_end:
                    on_tool_call_end(tool_call_id, status, result)
                    yield ""

            # 再次调用 LLM 生成最终回复
            for chunk in self.llm.stream(messages):
                if chunk.content:
                    yield chunk.content

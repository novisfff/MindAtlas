# Change: Add Assistant L0/L1/L2 Short Memory And Start Memory Modes

## Why
当前 supervisor 已能基于最近历史完成路由，但 `workflow_dag` 中多数 `llm` 节点仍以 `start.user_input` 与节点快照为主输入，跨轮上下文在 skill 内感知不足。连续对话中的省略表达、追问与上下文承接在 skill 执行层容易退化为“只看本轮”，影响任务连续性与准确率。

## What Changes
- 新增 Assistant 短期记忆分层：L0/L1/L2。
  - L0：最近对话原文窗口（不持久化）。
  - L1：会话级增量摘要（持久化，按 conversation）。
  - L2：会话+skill 事实记忆（持久化，按 conversation+skill）。
- 为 workflow start 配置新增 `memoryMode`：`auto | off | structured`（默认 `auto`）。
  - `auto`：自动向 eligible LLM 节点注入短期记忆（`workflow_dag` 采用 L0 消息流 + L1/L2 系统块；`agent_loop` 仅 L1/L2 系统块）。
  - `off`：完全禁用记忆注入与 memory 字段暴露。
  - `structured`：不自动注入，只暴露 `start.memory_*` 供模板手动引用。
- 在 `structured` 模式下新增固定可引用字段：`start.memory_recent_dialogue`、`start.memory_conversation_summary`、`start.memory_skill_facts`。
- 旧命名 `start.memory_l0/l1/l2` 不提供兼容别名并视为非法引用。
- 记忆更新采用非阻塞策略：更新失败仅记录日志，不影响主回复流程。
- 保持现有聊天 API 与 SSE 协议兼容，不新增用户可见控制命令。

## Impact
- Affected specs: `assistant-orchestration`
- Affected code (for later implementation):
  - Backend: `app/assistant/{models.py, service.py, run_service.py}` memory persistence/update path
  - Orchestration: `app/assistant/orchestration/agent_runtime.py`
  - Workflow engine: start/llm runtime and validation modules
  - Assistant config: schema + workflow editor start settings UI

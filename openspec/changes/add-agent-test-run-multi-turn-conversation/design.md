## Context
Agent 编辑器中的草稿试运行本质上是一个轻量聊天沙盒，但当前实现仍按“单次运行结果面板”建模：
- 后端固定把 `history=[]` 传给引擎
- 前端只维护当前输入和当前结果
- 工具调用链路只绑定当前一轮，不适合连续对话呈现

## Goals / Non-Goals
- Goals:
  - 让 Agent 草稿试运行支持连续多轮对话。
  - 每一轮继续复用共享 Agent 执行内核与现有流式 SSE。
  - 多轮下每条助手消息继续展示自己的 Tool Chain。
- Non-Goals:
  - 不修改 workflow test-run。
  - 不引入数据库持久化。
  - 不修改正式 assistant chat 的会话协议。

## Decisions

### 1) Backend History Contract
- `AgentTestRunRequest` 新增可选 `history`。
- `history` 只允许 `user|assistant` 两种角色，内容必须是非空文本。
- Agent test-run service 在调用 `LangGraphEngine.execute(...)` 时透传 `history`。

### 2) Frontend Conversation Model
- Agent test-run store 维护消息列表，而不是单条最终结果。
- 每次发送时立即追加一条 user 消息和一条 running assistant 占位消息。
- `content_delta`、`tool_call_start/end`、`run_end` 持续回填到当前 assistant 消息。

### 3) History Source For Next Turn
- 下一轮请求的 `history` 来自前端已完成的 user/assistant 消息列表。
- running assistant 占位消息不进入下一轮 history。

### 4) Tool Chain Placement
- Tool Chain 作为对应 assistant 消息的前置信息展示在回复内容上方。
- 多轮对话下，各轮工具链路彼此独立。

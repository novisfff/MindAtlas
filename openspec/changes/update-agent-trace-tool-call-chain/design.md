## Context
`workflow_dag.agent` 已支持自主工具循环与知识库检索，但当前 trace 模型仍然偏“扁平事件流”。这导致 workflow test 难以把工具调用挂到正确的 agent 执行步骤下，也导致正式会话刷新回放时缺失工具调用的父节点上下文。

## Goals / Non-Goals
- Goals:
  - 用统一 trace 上下文把 agent 工具调用归属到具体 workflow 节点执行实例。
  - 在 workflow test trace 中可视化 `agent -> tool` 的嵌套链路。
  - 让 assistant chat 的流式阶段和刷新回放共享同一份工具 trace 上下文字段。
- Non-Goals:
  - 不新增 SSE 事件名。
  - 不新增数据库迁移。
  - 不在 chat UI 本轮新增复杂层级 trace 面板。

## Decisions

### 1) Unified Trace Context
- `node_start` / `node_end` / `node_snapshot` 扩展可选字段：`nodeExecutionId`。
- `tool_call_start` / `tool_call_end` 对 agent 来源扩展：
  - `nodeId`
  - `nodeType`
  - `nodeExecutionId`
  - `agentRound`
  - `toolCallIndex`
  - `toolKind`
  - `startedAt` / `endedAt` / `durationMs`
- 非 agent 场景允许缺省这些扩展字段，保持兼容。

### 2) Runtime Generation Strategy
- `nodeExecutionId` 由 workflow 节点统一 wrapper 在每次节点执行时生成，而不是由各个节点自行生成。
- 通过 runtime metadata 透传 trace context，让 `rt.emit(...)` 在节点执行期间自动补齐 `nodeId` / `nodeType` / `nodeExecutionId`。
- `containerId::innerNodeId` scoped nodeId 规则保持不变；`nodeExecutionId` 独立生成，不与 `nodeId` 复用。

### 3) Agent Tool Call Semantics
- `dag_agent_node` 在 `tool_call_start/end` 时必须补齐：
  - `agentRound`
  - `toolCallIndex=1`
  - `toolKind='knowledge'` for `kb_search`，其余为 `tool`
- 多 tool call 仍只执行第一个，不新增“被丢弃 tool call”事件。

### 4) Workflow Test Trace Presentation
- 前端 workflow test store 改为按 `executionKey = nodeExecutionId || nodeId` 聚合 trace。
- `agent` 节点卡片下展示 `Tool Chain`：
  - 默认摘要：`Round 1 · list_tags · completed · 42ms`
  - 可展开查看 `args/result`
  - `kb_search` 使用 `Knowledge` 标识
- 画布定位仍按 `nodeId`，不改定位协议。

### 5) Assistant Chat Persistence
- `ChatEventAdapter` 在 `tool_calls` / `tool_results` JSON 中持久化相同 trace 上下文。
- 复用现有 `assistant_message.tool_calls` 与 `assistant_message.tool_results` 列，不做 schema migration。
- 刷新加载 conversation 时，前端回放映射保留这些字段，但 chat UI 本轮仅保证数据不丢。

## Risks / Trade-offs
- 风险：同一 `nodeId` 多次执行时 trace 被覆盖或串链。
  - 缓解：workflow test store 统一以 `nodeExecutionId` 为主键聚合。
- 风险：正式会话工具事件若不记录时间信息，刷新回放缺少耗时。
  - 缓解：`ChatEventAdapter` 在缺省情况下自动补 `startedAt/endedAt/durationMs`。
- 风险：扩展 payload 可能影响旧回调签名。
  - 缓解：runtime callback dispatch 通过兼容包装，仅向可接收的回调传递新增字段。

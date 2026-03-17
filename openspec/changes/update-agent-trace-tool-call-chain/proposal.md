# Change: Update Agent Trace Tool Call Chain

## Why
当前 workflow test trace 里的节点执行轨迹和 `tool_call_*` 事件是分离的，`agent` 节点无法直观看到“节点执行 -> 工具调用”的完整链路。正式会话里即使流式阶段收到工具事件，刷新后也无法稳定回放这些工具调用属于哪个 workflow 节点执行实例。

## What Changes
- 为 workflow runtime trace 增加统一父节点上下文字段：`nodeExecutionId`、`nodeId`、`nodeType`、`agentRound`、`toolCallIndex`、`toolKind`。
- 保持现有 SSE 事件名不变，仅扩展 `node_*` / `tool_call_*` payload，并在 workflow 节点统一 wrapper 中生成和透传 `nodeExecutionId`。
- workflow test trace 前端改为按“节点执行实例”聚合，给 `agent` 节点展示嵌套 `Tool Chain`，支持 loop / iteration 中同一 `nodeId` 多次执行不串链。
- assistant chat 的 `tool_calls` / `tool_results` JSON 持久化复用现有列，保存相同 trace 上下文，保证刷新回放不丢链路信息。
- chat UI 继续沿用现有 `ToolCallDisplay`，本次不新增复杂层级视图。

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - Backend workflow runtime event dispatch / workflow test SSE service
  - Backend assistant chat event adapter and persisted tool call JSON payloads
  - Frontend workflow test run store / trace panel / runtime node highlighting
  - Frontend assistant chat tool call types and replay mapping

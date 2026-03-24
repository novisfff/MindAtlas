## 1. Backend Runtime And Persistence
- [x] 1.1 在 workflow 节点统一 wrapper 中生成 `nodeExecutionId` 并自动透传到 `node_*` / `tool_call_*` 事件。
- [x] 1.2 为 `dag_agent_node` 的工具事件补齐 `nodeId/nodeType/nodeExecutionId/agentRound/toolCallIndex/toolKind`。
- [x] 1.3 扩展 workflow test SSE payload，补充 agent 工具 trace 上下文与 `startedAt/endedAt/durationMs`。
- [x] 1.4 扩展 `ChatEventAdapter`，将工具 trace 上下文写入 `tool_calls/tool_results` JSON，并在缺省时自动补齐时序字段。

## 2. Frontend Trace Presentation
- [x] 2.1 扩展 workflow test run 事件与 store 数据模型，按 `nodeExecutionId` 聚合节点执行实例。
- [x] 2.2 在 workflow test trace 面板为 `agent` 节点展示嵌套 `Tool Chain`，支持查看 args/result。
- [x] 2.3 修正 workflow 画布运行态高亮映射，兼容 execution-key 聚合后的 trace 数据。
- [x] 2.4 扩展 assistant chat tool call 类型与 conversation 回放映射，保证新字段不丢失。

## 3. Verification
- [x] 3.1 新增/扩展后端测试，覆盖 agent 工具 trace 上下文、workflow test SSE 扩展、chat event persistence。
- [x] 3.2 执行关键后端 pytest、前端 `npm run build` 与 OpenSpec strict validate。

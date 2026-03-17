# Change: Add Workflow DAG Agent Node

## Why
`workflow_dag` 目前只有 `llm` 节点和显式 `tool` 节点，无法在单个节点内完成“由模型自主决定是否调用工具”的循环执行。这导致一些需要动态工具决策的编排必须拆成多节点，配置复杂、可维护性差，也不利于在容器 body 子流中复用。

## What Changes
- 新增 `agent` 节点类型（主 DAG 与 `iteration/loop` body 均可用）。
- `agent` 节点能力固定为文本输出：`response`。
- 工具权限固定为节点白名单 `agent.toolNames`，仅允许调用白名单工具。
- 调用策略固定为串行单工具循环：每轮最多执行一个 tool call；模型返回多个 tool call 时仅执行第一个并记录告警。
- 新增 `agent` 节点配置项：
  - `systemPrompt?: string`
  - `userInput?: string`（默认 `{{start.user_input}}`）
  - `toolNames: string[]`（至少 1 个）
  - `maxIterations?: number`（默认 6，范围 1~20）
  - `modelSource?: 'default' | 'custom'`
  - `modelId?: string`（`custom` 必填 UUID）
- 运行时失败策略固定为终止：配置非法、工具异常、达到上限均直接抛错并走现有 run 失败收尾。
- 保持现有对外 chat/run/SSE 协议不变。

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - Backend workflow runtime/node builders/validator/model resolver
  - assistant-config dependency validation and node type catalog API
  - Frontend workflow editor node palette/property panel/variable references

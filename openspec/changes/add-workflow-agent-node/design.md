## Context
当前工作流编排已支持 `llm/tool/if_else/iteration/loop/...`，但缺少“节点内 agent 循环”能力。用户希望在 `workflow_dag` 内使用一个节点完成模型决策 + 工具调用 + 结果收敛，并与既有内存模式、流式输出、容器子流机制一致。

## Goals / Non-Goals
- Goals:
  - 在 `workflow_dag` 新增 `agent` 节点，支持主流程与容器 body。
  - `agent` 节点可自动决定是否调用工具，并按串行循环收敛文本结果。
  - 节点级模型选择能力与 `llm/parameter_extractor` 一致。
  - 失败策略、流式语义、依赖校验与现有体系对齐。
- Non-Goals:
  - 本期不支持 `agent` 结构化/json 输出字段。
  - 不引入并发多工具执行。
  - 不新增对外 API 或 SSE 事件类型。

## Decisions

### 1) Node Contract
- 新增节点类型：`agent`。
- 输出固定：`json_fields.response` + `text/raw` 文本。
- 支持配置：`systemPrompt/userInput/toolNames/maxIterations/modelSource/modelId`。

### 2) Tool Access Control
- 仅允许调用 `agent.toolNames` 白名单内工具。
- 不继承 skill 全量工具，不支持自动全量授权模式。

### 3) Agent Loop Strategy
- 每轮调用 LLM 判断是否产生 tool call。
- 每轮最多执行一个 tool call（取第一个）；多个 tool call 仅首个执行并记录 warning。
- 有 tool call 时执行工具并把结果回填消息后继续下一轮。
- 无 tool call 时结束并返回最终文本。
- 达到 `maxIterations` 仍未收敛则直接抛错。

### 4) Failure Semantics
- 工具配置非法、工具不存在、工具执行异常、迭代超限均抛 `RuntimeError`。
- 节点失败即 run 失败，复用现有失败收尾链路（不静默降级）。

### 5) Memory Semantics
- 遵循现有 `memoryMode`：
  - `auto`：注入 L1/L2 系统记忆块；并注入 L0 消息流（与 workflow llm 一致）。
  - `off/structured`：不自动注入。
- 不改变 `structured` 模式的 `start.memory_*` 暴露逻辑。

### 6) Streaming Semantics
- `agent` 每轮 LLM 文本增量都发 `on_node_output_delta`。
- 当 `stream_output_enabled + output_stream_source_node_id==agent_node_id` 时，转发 `on_content_delta`。
- `output` 节点 passthrough 去重源类型扩展到 `{llm, agent}`，避免重复输出。

### 7) Model Selection
- `agent` 支持 `modelSource/default|custom + modelId`。
- 主 DAG 与容器 body (`containerId::nodeId`) 都参与 custom model 解析。
- 保存/编译校验与依赖收集均覆盖 `agent`（主流程 + 容器）。

## Risks / Trade-offs
- 风险：多轮工具调用可能拉长节点耗时。
  - 缓解：`maxIterations` 上限 + 失败快速暴露。
- 风险：白名单配置遗漏导致运行时报错。
  - 缓解：save/compile 阶段校验 `toolNames` 与工具存在性。
- 风险：流式重复输出。
  - 缓解：output passthrough 去重支持 `agent` 源。

## Migration Plan
1. 扩展后端节点类型、校验、依赖收集与运行时节点构建器。
2. 扩展前端编辑器节点目录、配置面板与变量引用。
3. 增加测试覆盖（validator/runtime/config service）。
4. 更新 OpenSpec 并执行 strict validate。

## Open Questions
- None (MVP 范围与策略已锁定)。

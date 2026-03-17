## Context
现有 `workflow_dag.agent` 已具备工具白名单、自主工具循环、模型选择与 memoryMode 语义，但知识库检索仍然是工作流级显式能力，无法让 agent 在节点内部自主决定何时检索知识库。

## Goals / Non-Goals
- Goals:
  - 给 `workflow_dag.agent` 增加节点内建 KB 检索能力。
  - 不把 `kb_search` 混入普通 `toolNames` 语义。
  - 让主 DAG 与容器 body 中的 `agent` 都支持相同的 KB 行为。
  - 复用现有 `kb_search` 实现、`tool_call_*` 事件协议与引用提示词。
- Non-Goals:
  - 不修改现有 `knowledge_retrieval` 节点与 `llm` 显式知识绑定语义。
  - 不新增对外 API / SSE 事件。
  - 不为 `agent` 增加结构化 KB 输出字段。

## Decisions

### 1) Node Contract
- `agent` 新增可选配置：
  - `knowledgeEnabled?: boolean`
  - `knowledgeMode?: 'naive' | 'local' | 'global' | 'hybrid' | 'mix'`
  - `knowledgeTopK?: number`
- `knowledgeEnabled=true` 时允许 `toolNames` 为空；节点至少需要“普通工具或 KB 能力”之一。

### 2) Built-in KB Tool
- 运行时为节点创建内部 `kb_search` 工具，不显示在编辑器普通工具列表中。
- 模型可见的工具入参只保留 `query`。
- 节点配置中的 `knowledgeMode/knowledgeTopK` 固定注入到底层真实 `kb_search(query, mode, top_k)` 调用。
- 工具事件仍沿用 `tool_call_start/end`，工具名保持 `kb_search`。

### 3) Prompt + Citation Rules
- `knowledgeEnabled=true` 时，在 `agent` 系统提示词中追加与 `agent_loop` 一致的 KB 引用规则。
- 明确要求：
  - 当问题可能依赖已有记录/知识时优先调用 `kb_search`
  - 使用 KB 结果时必须输出 `[^n]`
  - 引用编号只能来自 `kb_search.references`
- `knowledgeEnabled=false` 时不注入任何 KB 特殊提示。

### 4) Validation + Dependency Collection
- `agent.toolNames` 仍只允许普通工具名；出现 `kb_search` 直接报错。
- `knowledgeEnabled` 必须是布尔值。
- `knowledgeMode` 必须属于固定枚举。
- `knowledgeTopK` 必须为 `1..50`。
- workflow 依赖采集把以下情况都视为隐式 `kb_search` 依赖：
  - `knowledge_retrieval` 节点
  - `knowledgeEnabled=true` 的 `agent` 节点（主流程 + 容器 body）

### 5) Snapshot + Editor
- 节点快照输入增加 `knowledge` 配置块，便于测试运行和问题排查。
- 前端 `AgentNodeSettings` 新增 KB 配置区，但普通工具选择器继续不展示 `kb_search`。

## Risks / Trade-offs
- 风险：KB-only agent 与旧版 “toolNames 必填” 语义冲突。
  - 缓解：save/compile/runtime 一起切换为“工具或 KB 至少一种能力”。
- 风险：隐式依赖若未被 workflow 依赖采集覆盖，会导致运行时缺少 `kb_search`。
  - 缓解：对 `knowledge_retrieval` 与 KB-enabled `agent` 都统一补入依赖，并允许内部工具名通过依赖校验。
- 风险：模型未按要求输出引用。
  - 缓解：复用现有 KB 引用提示词，并把使用规范写进节点系统提示词。

## Open Questions
- None. Node-level KB controls、引用语义、依赖策略和失败策略已锁定。

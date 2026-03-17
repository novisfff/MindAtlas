# Change: Update Workflow Agent Node Knowledge Retrieval

## Why
`workflow_dag.agent` 已经支持节点级工具自主调用，但知识库检索仍然只能通过显式 `knowledge_retrieval` 节点或把 `kb_search` 当作普通工具处理。这会让编辑器语义混乱，也会把内部 KB 能力错误地混进 `toolNames` 白名单。

## What Changes
- 为 `workflow_dag.agent` 增加内建自主知识库检索能力，不再要求把 `kb_search` 暴露成普通工具。
- `agent` 新增节点级 KB 配置：`knowledgeEnabled`、`knowledgeMode`、`knowledgeTopK`。
- `knowledgeEnabled=true` 时，运行时向该节点注入内部 `kb_search` 工具；模型仅可传入 `query`，节点配置固定覆盖 `mode/topK`。
- `agent.toolNames` 继续只表示普通外部工具白名单；若包含 `kb_search`，save/compile 校验直接报错并提示使用 `knowledgeEnabled`。
- workflow 依赖采集把 KB 使用视为隐式 `kb_search` 依赖，覆盖主 DAG 与 `iteration/loop` body。
- `agent` 启用 KB 时在系统提示词中追加强制引用规则，要求使用 `[^n]` 引用 `kb_search.references`。
- 保持 chat/run/SSE 对外协议不变。

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - Backend DAG agent runtime / validator / dependency collection / snapshot input
  - Frontend workflow editor agent node settings / types / i18n
  - OpenSpec change history for workflow agent node capability

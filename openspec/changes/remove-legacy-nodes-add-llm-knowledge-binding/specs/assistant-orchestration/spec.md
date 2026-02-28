## ADDED Requirements

### Requirement: Workflow Removed Node Types Must Be Rejected Explicitly
系统 SHALL 将 `template` 与 `variable_aggregator` 视为下线节点类型，并在校验、编译或执行前返回明确错误信息。

#### Scenario: Reject removed node in workflow validation
- **WHEN** workflow 包含 `template` 或 `variable_aggregator` 节点
- **THEN** 系统 SHALL 返回该节点类型已下线的错误
- **AND** 错误信息 SHALL 指导用户改用受支持节点手工重构

#### Scenario: Reject removed node in runtime conversion
- **WHEN** 存量 workflow 在转换为 SkillDefinition 时包含下线节点
- **THEN** 系统 SHALL 失败并给出可读错误
- **AND** SHALL NOT 进行隐式替换或自动迁移

### Requirement: Knowledge Retrieval Node Supports Override Parameters and Structured Output
`knowledge_retrieval` 节点 SHALL 支持 `query`、可选 `mode` 与可选 `topK`，并输出稳定结构化字段用于下游引用。

#### Scenario: Fallback to system defaults
- **WHEN** KR 节点仅提供 `query`
- **THEN** 系统 SHALL 使用知识库工具默认 `mode/top_k`

#### Scenario: Node-level override applies
- **WHEN** KR 节点同时提供 `query + mode + topK`
- **THEN** 系统 SHALL 使用节点参数覆盖默认配置执行检索

#### Scenario: Structured retrieval payload is available
- **WHEN** KR 节点执行完成
- **THEN** 节点输出 SHALL 至少包含 `result`、`query`、`mode`、`references`、`references_count`
- **AND** `text` 字段 SHALL 保持可读摘要

### Requirement: LLM Node Knowledge Injection Must Be Explicit and Configurable
`llm` 节点 SHALL 通过显式配置绑定上游 KR 节点，不再自动接收所有 KR 上下文。

#### Scenario: Knowledge injection disabled
- **WHEN** `knowledgeEnabled=false` 或未配置 `knowledgeSourceNodeIds`
- **THEN** LLM 节点 SHALL NOT 注入 KR 知识消息

#### Scenario: Inject only selected KR sources
- **WHEN** `knowledgeEnabled=true` 且配置了 `knowledgeSourceNodeIds`
- **THEN** 系统 SHALL 仅注入被选择且合法的 KR 节点输出

#### Scenario: Injection mode controls payload shape
- **WHEN** `knowledgeInjectMode=references_only`
- **THEN** 系统 SHALL 仅注入引用列表信息
- **WHEN** `knowledgeInjectMode=full_payload`
- **THEN** 系统 SHALL 注入完整检索 payload（受 `knowledgeMaxRefs` 上限约束）

### Requirement: LLM Knowledge Source Binding Must Be Validated
系统 SHALL 校验 `knowledgeSourceNodeIds` 的合法性，确保来源节点存在、类型正确且位于当前 LLM 节点上游。

#### Scenario: Reject non-existent knowledge source
- **WHEN** `knowledgeSourceNodeIds` 包含不存在的节点 ID
- **THEN** workflow 校验 SHALL 失败

#### Scenario: Reject non-KR source type
- **WHEN** `knowledgeSourceNodeIds` 指向非 `knowledge_retrieval` 节点
- **THEN** workflow 校验 SHALL 失败

#### Scenario: Reject non-upstream source
- **WHEN** `knowledgeSourceNodeIds` 指向当前 LLM 的非上游节点
- **THEN** workflow 校验 SHALL 失败

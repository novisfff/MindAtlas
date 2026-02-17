## ADDED Requirements

### Requirement: Workflow LLM-like Nodes Must Support Node-Level Model Source Selection
系统 SHALL 支持 `llm` 与 `parameter_extractor` 节点配置模型来源：`default`（系统默认 assistant LLM）或 `custom`（指定 `modelId`）。

#### Scenario: Default model source fallback
- **WHEN** 节点未配置 `modelSource/modelId`
- **THEN** 系统 SHALL 按 `modelSource=default` 处理
- **AND** 节点 SHALL 使用系统默认 assistant LLM

#### Scenario: Custom model source uses selected model
- **WHEN** 节点配置 `modelSource=custom` 且 `modelId` 有效
- **THEN** 系统 SHALL 使用该 `modelId` 对应的 `llm` 模型执行节点

### Requirement: Node-Level Model Binding Must Be Validated Before Persist
系统 SHALL 在 workflow 校验/保存阶段校验 `llm` 与 `parameter_extractor` 的模型绑定配置，阻止非法数据入库。

#### Scenario: Reject custom source without modelId
- **WHEN** `modelSource=custom` 但 `modelId` 缺失
- **THEN** workflow 校验 SHALL 失败

#### Scenario: Reject invalid modelId format
- **WHEN** `modelId` 不是合法 UUID
- **THEN** workflow 校验 SHALL 失败

#### Scenario: Reject default source with modelId
- **WHEN** `modelSource=default` 且同时提供 `modelId`
- **THEN** workflow 校验 SHALL 失败

#### Scenario: Reject missing or non-llm model binding at save time
- **WHEN** `modelSource=custom` 对应的 `modelId` 不存在或模型类型非 `llm`
- **THEN** workflow 保存 SHALL 失败并返回明确错误

### Requirement: Runtime Must Route LLM-like Nodes To Resolved Model And Fail Explicitly On Invalid Binding
运行时 SHALL 根据节点配置解析并路由模型实例；同一 `modelId` 在单个执行器内应复用客户端，且配置失效时应显式失败。

#### Scenario: Reuse model client by modelId
- **WHEN** 多个节点引用相同 `modelId`
- **THEN** 系统 SHALL 复用同一模型客户端实例

#### Scenario: Runtime explicit failure when bound model becomes unavailable
- **WHEN** 节点配置 `modelSource=custom` 且运行时无法解析对应模型
- **THEN** 系统 SHALL 抛出明确错误
- **AND** SHALL NOT 静默回退到默认模型

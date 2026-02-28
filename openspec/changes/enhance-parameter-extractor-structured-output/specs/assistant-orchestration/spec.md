## ADDED Requirements

### Requirement: Parameter Extractor Node Must Support Configurable Input Template and Structured Output Schema
系统 SHALL 支持 `parameter_extractor` 节点配置 `inputContent`、可选 `instruction` 与非空 `outputFields`，并允许模板变量引用上游节点与合法 `sys.*` 字段。

#### Scenario: Input template rendering uses upstream values
- **WHEN** `inputContent` 包含 `{{upstream_node.field}}` 或 `{{start.user_input}}`
- **THEN** 系统 SHALL 在节点执行前完成模板渲染
- **AND** 渲染语义 SHALL 与其他模板型节点一致

#### Scenario: Save fails when output schema is empty
- **WHEN** `parameter_extractor.outputFields` 缺失、非数组或为空
- **THEN** workflow 校验/保存 SHALL 失败并返回明确错误

### Requirement: Parameter Extractor Runtime Must Enforce Strict Structured Output
`parameter_extractor` 运行时 SHALL 始终使用内置结构化提取提示词，并对模型输出执行严格 JSON 校验。

#### Scenario: Structured extraction succeeds
- **WHEN** 模型返回合法 JSON 且覆盖全部配置字段
- **THEN** 节点输出 SHALL 包含 `text`（JSON 字符串）、`raw`（对象）、`json_fields`（按 `outputFields` 过滤）

#### Scenario: Non-JSON output fails fast
- **WHEN** 模型返回内容不是可解析 JSON 对象
- **THEN** 节点 SHALL 抛出明确错误并中断执行
- **AND** SHALL NOT 静默降级为原始文本

#### Scenario: Missing configured field fails fast
- **WHEN** 模型返回 JSON 但缺失任一 `outputFields.name`
- **THEN** 节点 SHALL 抛出明确错误并中断执行

### Requirement: Parameter Extractor Output Fields Must Follow LLM Structured Field Semantics
`parameter_extractor.outputFields` 字段定义 SHALL 与 LLM 结构化字段语义一致（`name/type/nullable/itemsType/enum`）。

#### Scenario: Invalid field schema is rejected
- **WHEN** 字段名非法、type 非法、array 缺少/非法 `itemsType` 或 `enum` 非字符串数组
- **THEN** workflow 校验 SHALL 失败并返回可读错误

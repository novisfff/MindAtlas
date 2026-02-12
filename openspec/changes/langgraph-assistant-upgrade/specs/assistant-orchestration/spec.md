## ADDED Requirements

### Requirement: LangGraph-Only Skill Execution
系统 SHALL 仅通过 LangGraph 引擎执行技能。`assistant_skill.mode` SHALL 固定为 `langgraph`，不再支持 `steps` 或 legacy `agent` 执行路径。

#### Scenario: Execute supported skill
- **WHEN** 技能配置为 `mode="langgraph"` 且 `langgraph_pattern` 为合法值
- **THEN** 系统 SHALL 通过 `LangGraphEngine` 执行
- **AND** SHALL NOT 调用 legacy `SkillExecutor` 作为回退

#### Scenario: Reject legacy mode
- **WHEN** 读到 `mode="steps"` 或 `mode="agent"` 的技能记录
- **THEN** 系统 SHALL 将其视为非法配置并拒绝执行

### Requirement: LangGraph Pattern Is Restricted
`assistant_skill.langgraph_pattern` SHALL 仅允许 `agent_loop` 与 `workflow_dag`。

#### Scenario: Reject sequential pattern
- **WHEN** 请求或存量数据使用 `langgraph_pattern="sequential"`
- **THEN** 系统 SHALL 返回校验错误或在迁移中清理为合法值

#### Scenario: Require pattern on create
- **WHEN** 创建技能时缺少 `langgraph_pattern`
- **THEN** 系统 SHALL 返回 422 校验错误

### Requirement: Workflow Data Model Is DAG-Only
系统 SHALL 仅使用 `assistant_skill_node` 与 `assistant_skill_edge` 持久化工作流结构，`assistant_skill_step` SHALL 被移除。

#### Scenario: Migration drops step table
- **WHEN** 迁移执行完成
- **THEN** 数据库中 SHALL 不存在 `assistant_skill_step` 表

#### Scenario: Workflow dag validation
- **WHEN** `langgraph_pattern="workflow_dag"` 且缺失 workflow
- **THEN** 系统 SHALL 返回校验错误

### Requirement: Skill Editor Exposes Only Two Modes
前端技能编辑器 SHALL 仅暴露两种模式语义：
- `workflow_dag` 显示为“工作流模式”
- `agent_loop` 显示为“Agent模式”

#### Scenario: Create skill default
- **WHEN** 用户新建技能
- **THEN** 默认模式 SHALL 为 `agent_loop`

#### Scenario: Workflow editing entry
- **WHEN** 技能模式为 `workflow_dag` 且技能已创建
- **THEN** UI SHALL 展示“编辑工作流”入口

### Requirement: Legacy Data Is Aggressively Cleaned
系统迁移 SHALL 删除 `mode in ('steps','agent')` 的历史技能记录，并将保留技能的 `mode` 固定为 `langgraph`。

#### Scenario: Cleanup after migration
- **WHEN** 迁移完成
- **THEN** 保留技能记录中的 `mode` 全部为 `langgraph`
- **AND** legacy 技能记录不再存在

### Requirement: Workflow DAG Layout Is Horizontal
系统 SHALL 采用统一的工作流横向布局语义：主链从左到右，分支在上下展开。

#### Scenario: Editor handles follow horizontal direction
- **WHEN** 在 workflow 编辑器中渲染普通节点与 `if_else` 节点
- **THEN** 输入句柄 SHALL 位于左侧
- **AND** 输出句柄 SHALL 位于右侧（`if_else` 多分支在右侧按纵向分布）

#### Scenario: Empty workflow uses horizontal default template
- **WHEN** workflow_dag 技能没有任何 nodes/edges
- **THEN** 默认模板 SHALL 使用横向 `start -> llm` 布局

#### Scenario: Official system workflows default to horizontal coordinates
- **WHEN** 使用系统默认定义（如首次同步或 reset 系统技能）
- **THEN** `quick_stats`、`smart_capture`、`periodic_review` 的边方向 SHALL 体现主链 X 轴递增
- **AND** 并行分支目标节点 SHALL 在 Y 轴上有差异

### Requirement: If/Else Node Uses Branch Model
`if_else` 节点 SHALL 使用结构化分支模型：`branches[] + else`。每个分支拥有独立的 `logic(and|or)` 和 `conditions[]`，并按 IF -> ELIF 顺序求值，首个命中分支即停止评估。

#### Scenario: Branch evaluation order
- **WHEN** IF 与 ELIF 条件同时满足
- **THEN** 系统 SHALL 命中 IF 分支
- **AND** SHALL NOT 继续评估后续 ELIF

#### Scenario: Else is mandatory
- **WHEN** `if_else` 节点缺少 `else` 连线
- **THEN** workflow 校验 SHALL 失败并拒绝保存/编译

#### Scenario: Branch logic support
- **WHEN** 分支 `logic="and"`
- **THEN** 该分支所有条件都为真时才命中
- **WHEN** 分支 `logic="or"`
- **THEN** 该分支任一条件为真时命中

### Requirement: If/Else Operators and Variable Paths
`if_else` 条件操作符 SHALL 限定为：`contains`、`not_contains`、`starts_with`、`ends_with`、`is`、`is_not`、`is_empty`、`is_not_empty`。变量路径 SHALL 支持 `node_id.field` 与 `sys.xxx`。

#### Scenario: Value requirement by operator
- **WHEN** 操作符是 `is_empty` 或 `is_not_empty`
- **THEN** 条件值 MAY 为空
- **WHEN** 操作符为其他受支持值
- **THEN** 条件值 SHALL 必填

#### Scenario: Sys variable path validation
- **WHEN** 条件变量使用 `sys.date`、`sys.datetime` 或 `sys.conversation_id`
- **THEN** 系统 SHALL 视为合法变量路径
- **WHEN** 条件变量使用未知 `sys` 字段
- **THEN** 系统 SHALL 返回校验错误

### Requirement: Workflow Runtime Provides Sys Variables
workflow 执行态 SHALL 注入最小系统变量集合：`sys.date`、`sys.datetime`、`sys.conversation_id`，并可在条件右值模板和其他模板解析中引用。

#### Scenario: Condition template references sys and node vars
- **WHEN** 条件右值包含 `{{start.user_input}}` 或 `{{sys.date}}`
- **THEN** 系统 SHALL 在比较前完成模板渲染

#### Scenario: Case-insensitive string comparison
- **WHEN** 操作符涉及字符串比较（如 `contains`/`starts_with`/`is`）
- **THEN** 系统 SHALL 默认按不区分大小写执行比较

### Requirement: Workflow References Are Label-First in UI and ID-First in Storage
工作流编辑器 SHALL 在用户可见层统一使用 `Label.field` 引用语义；持久化与执行层 SHALL 继续使用 `node_id.field`。

#### Scenario: Display references in editor
- **WHEN** 用户在节点配置或条件值中插入变量
- **THEN** UI SHALL 插入 `{{Label.field}}`（或条件左值 `Label.field`）
- **AND** 变量面板 SHALL 不暴露 `node_id`

#### Scenario: Convert references before save
- **WHEN** 用户保存 workflow
- **THEN** 系统 SHALL 将显示层 `Label.field` 转换为存储层 `node_id.field`
- **AND** 运行时模板解析仍基于 `node_id.field`

### Requirement: Workflow Node Labels Must Be Valid and Unique
同一 workflow 内的节点 Label SHALL 必填、大小写不敏感唯一，且 SHALL NOT 包含 `.`。

#### Scenario: Reject invalid labels
- **WHEN** 任一节点 Label 为空、包含 `.` 或与其他节点大小写不敏感重名
- **THEN** workflow 校验 SHALL 失败并返回对应节点错误

#### Scenario: Repair legacy duplicated labels on load
- **WHEN** 读取到旧 workflow 存在重复 Label
- **THEN** 编辑器 SHALL 自动补 `#N` 生成唯一 Label
- **AND** 用户下次保存时 SHALL 持久化修复结果

### Requirement: Workflow Variable Picker Uses Two-Level Grouping
变量引用面板 SHALL 以两级结构展示可引用项：第一级为“节点/系统变量”，第二级为字段列表。

#### Scenario: Show grouped variables
- **WHEN** 打开变量插入菜单
- **THEN** 系统 SHALL 先展示节点 Label（或系统变量组）
- **AND** 展开后展示对应字段（如 `response`、`result` 等）

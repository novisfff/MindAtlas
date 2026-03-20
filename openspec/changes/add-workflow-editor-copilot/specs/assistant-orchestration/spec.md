## ADDED Requirements

### Requirement: Workflow Editor SHALL Support Proposal-Based AI Copilot
工作流编辑器 SHALL 提供一个提案式 AI Copilot，用于生成局部流程、修改当前选区、修复校验问题和分析 test-run。

#### Scenario: Generate proposal from natural language
- **WHEN** 用户在 workflow editor 中提交自然语言编辑请求
- **THEN** 系统 SHALL 返回包含 `status`、`message` 和可选 `proposal` 的 Copilot 响应
- **AND** `proposal` SHALL 以结构化 `operations` 描述改动，而不是直接返回原始整图覆盖结果

#### Scenario: Selection-scoped proposal stays in scope
- **WHEN** Copilot 请求带有 `selection.scope=selection|container`
- **THEN** 后端 SHALL 拒绝越过当前 scope 的节点或边修改
- **AND** `container` scope SHALL 仅允许修改指定 `containerId` 的 body 子流

#### Scenario: Edit-selection copilot highlights the selected target
- **WHEN** 用户从节点属性面板发起“让 AI 帮我改这个节点”
- **THEN** Copilot 面板 SHALL 显示当前编辑目标的节点名、节点类型、节点 ID，以及必要时的容器信息
- **AND** 后端 SHALL 在 `edit_selection` prompt 上下文中提供 `primaryTarget` 与明确的 selection intent，要求模型优先围绕该目标节点修改

### Requirement: Workflow Editor Copilot SHALL Return Simulated Proposal Output
Copilot proposal SHALL 在当前草稿上由后端模拟应用，并返回规范化后的 `proposedWorkflow` 和 validation 结果。

#### Scenario: Proposal includes draft hashes and validation
- **WHEN** Copilot 返回可执行 proposal
- **THEN** 响应 SHALL 包含 `baseDraftHash`、`proposedDraftHash`、`proposedWorkflow`、`validation`、`affectedNodeIds`
- **AND** 前端 SHALL 使用这些数据做预览与并发保护

#### Scenario: Invalid proposal is surfaced, not silently applied
- **WHEN** 模拟应用后的 workflow 校验失败
- **THEN** 系统 SHALL 仍返回 proposal 和 validation 结果
- **AND** 前端 SHALL 将校验状态展示给用户，而不是静默忽略

### Requirement: Workflow Editor SHALL Require Explicit User Confirmation Before Applying Copilot Changes
Copilot 产生的任何图修改 SHALL 先预览，再由用户确认应用。

#### Scenario: User applies proposal against unchanged draft
- **WHEN** 用户点击应用 proposal 且当前草稿 hash 与 `baseDraftHash` 一致
- **THEN** 前端 SHALL 将 `proposedWorkflow` 应用到当前草稿
- **AND** 若 `layoutRecommendation=autolayout`，前端 SHALL 在应用后复用现有自动布局流程

#### Scenario: Draft changed before apply
- **WHEN** 用户点击应用 proposal 时当前草稿已发生变化
- **THEN** 前端 SHALL 拒绝应用该 proposal
- **AND** 系统 SHALL 提示用户重新生成 proposal

### Requirement: Workflow Editor Copilot SHALL Support Canvas Preview and Immediate Undo
Copilot proposal SHALL 支持画布叠层效果图预览，并在 apply 后提供一次性显式撤销能力。

#### Scenario: User compares current graph and proposed graph before apply
- **WHEN** Copilot 返回可执行 proposal
- **THEN** 前端 SHALL 打开只读画布预览
- **AND** 用户 SHALL 能在 `当前图` 与 `提案图` 之间切换查看

#### Scenario: Preview mode locks editor mutations
- **WHEN** Copilot 预览叠层处于打开状态
- **THEN** 工作流编辑器 SHALL 进入只读预览态
- **AND** 用户 SHALL 不能在预览期间直接修改底层主画布

#### Scenario: User undoes applied proposal while state is still current
- **WHEN** proposal 已被应用，且当前草稿仍等于刚应用后的结果
- **THEN** Copilot 面板 SHALL 提供“撤销本次应用”入口
- **AND** 该入口 SHALL 复用现有 workflow editor undo 语义恢复应用前草稿

#### Scenario: Copilot undo becomes unavailable after further edits
- **WHEN** proposal 应用后用户又继续编辑，导致当前草稿不再等于刚应用后的结果
- **THEN** Copilot 面板 SHALL 不再允许直接撤销本次 apply
- **AND** 前端 SHALL 提示用户改用全局 Undo

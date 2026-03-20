## Context
工作流编辑器已有完整的图编辑、校验、试运行和追踪能力，但没有面向编辑行为的 AI 入口。现有 assistant / agent runtime 适合执行工作流，不适合直接用于编辑器草稿修改，因此需要一条新的“提案 -> 模拟应用 -> 用户确认”的编辑链路。

## Goals / Non-Goals
- Goals:
  - 支持自然语言驱动的局部工作流编辑提案
  - 统一生成、选区修改、校验修复、test-run 分析四类入口
  - 所有实质改动都通过结构化 operation 和人工确认落地
- Non-Goals:
  - 不做自动保存或自动发布
  - 不做整图黑盒重写
  - 不做后台持久化 Copilot 会话

## Decisions
- Decision: 模型只输出结构化 `operations`
  - Why: 防止整图覆盖失控，便于后端做 scope 校验和模拟应用
- Decision: 后端返回 `proposedWorkflow`
  - Why: 前端无需重复实现 operation 解释器，只负责预览和确认应用
- Decision: 前端 apply 使用 `baseDraftHash` 并发保护
  - Why: 避免用户在 proposal 生成后继续手改草稿导致错误覆盖
- Decision: `autolayout` 只作为 layout recommendation / operation hint
  - Why: 具体布局仍复用前端现有布局算法，避免后端复制一套布局逻辑
- Decision: proposal 先进入画布叠层预览，再决定是否 apply
  - Why: 相比只看文字操作列表，直接看工作流效果图更容易判断 proposal 是否符合预期，也能把“review before apply”做成更明确的交互
- Decision: apply 后只提供“当前 proposal 的一次性显式撤销”
  - Why: 复用现有 workflow editor snapshot history 即可满足立即回退场景，不需要为 Copilot 再引入第二套历史系统

## Preview / Undo Notes
- 预览真相源是前端派生的 `previewWorkflow`，必须与实际 apply 结果一致：
  - 普通 proposal 直接使用 `proposal.proposedWorkflow`
  - `layoutRecommendation=autolayout` 时先走现有前端自动布局算法，再用于预览与 apply
- 叠层预览打开期间，编辑器进入只读预览态：
  - 主画布进入只读
  - palette / property panel / 顶部改图操作被叠层遮罩
  - Copilot 面板继续可交互
- Copilot 专属撤销本质调用既有 `store.undo()`：
  - 仅当当前草稿 hash 仍等于刚应用后的结果时可用
  - 若用户后续继续编辑、redo 或切换到其他 proposal，则提示改用全局 Undo

## Risks / Trade-offs
- LLM 仍可能生成不完整或不合理的 operations
  - Mitigation: 后端强制 scope 校验、依赖校验、validator 校验，并把 invalid proposal 明确回显给用户
- `proposedWorkflow` 与最终应用后的自动布局结果不完全一致
  - Mitigation: 把 `layoutRecommendation` 显式返回，并在前端 apply 后再次 validate

## Migration Plan
- 无数据库迁移
- 新能力只作用于 workflow editor 草稿编辑，不影响 chat/run/SSE 协议

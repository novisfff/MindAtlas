# Change: Add Workflow Editor Copilot

## Why
工作流编辑器已经具备节点配置、校验、测试运行和 trace 能力，但用户仍然需要手工完成大量图编辑工作，尤其是在“从自然语言生成局部流程”“根据校验问题修图”“根据试运行结果定位改法”这三类场景下，编辑成本很高。

## What Changes
- 为工作流编辑器新增提案式 `Workflow Copilot`，统一承接生成、局部编辑、校验修复、test-run 分析四类 AI 辅助场景。
- 新增后端 `POST /api/assistant-config/workflows/{id}/copilot/respond` 接口，要求模型只返回结构化编辑操作，不允许直接覆盖整份 workflow。
- 后端在当前草稿上模拟应用 proposal，返回规范化后的 `proposedWorkflow`、draft hash、validation 结果和风险警告。
- 前端新增 Copilot 侧边面板，并在编辑器顶部、属性面板、校验清单、测试运行面板四处接入统一入口。
- 从属性面板发起“让 AI 帮我改这个节点”时，Copilot 会显式展示当前编辑目标；后端 prompt 会把该目标提升为 `primaryTarget`，要求模型优先围绕它修改。
- 应用 proposal 时必须人工确认；前端基于 `baseDraftHash` 做并发保护，应用后自动复用现有 validate / autolayout 流程。
- Copilot proposal 新增画布叠层效果图预览，支持 `当前图 / 提案图` 切换，并在预览态锁定编辑器改图操作。
- proposal 应用后，Copilot 面板保留“撤销本次应用”入口；仅当当前草稿仍等于刚应用后的结果时允许直接撤销。

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant_config/router.py`
  - `backend/app/assistant_config/workflow_copilot_service.py`
- Affected frontend code:
  - `frontend/src/features/assistant-config/pages/WorkflowEditorPage.tsx`
  - `frontend/src/features/assistant-config/components/workflow/WorkflowCopilotPanel.tsx`
  - `frontend/src/features/assistant-config/components/workflow/*`
  - `frontend/src/features/assistant-config/api/workflow.ts`
  - `frontend/src/features/assistant-config/api/workflows.ts`
  - `frontend/src/features/assistant-config/stores/workflow-editor-store.ts`
  - `frontend/src/locales/zh/common.json`
  - `frontend/src/locales/en/common.json`

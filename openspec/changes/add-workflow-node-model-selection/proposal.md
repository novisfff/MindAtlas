# Change: Workflow 节点级模型选择（LLM + 参数提取）

## Why
当前 workflow_dag 中 `llm` 与 `parameter_extractor` 节点只能使用系统默认 assistant 模型，无法在节点级按任务特点选择更合适的模型，导致成本与效果不可控。

## What Changes
- 为 `llm` 与 `parameter_extractor` 节点新增模型来源配置：
  - `modelSource`: `default | custom`
  - `modelId`: 仅当 `modelSource=custom` 时生效
- **BREAKING（配置约束）**：保存/校验阶段对非法模型配置进行阻断（而非运行时静默回退）。
- 运行时支持按节点路由到绑定模型，并在同一执行器内复用相同 `modelId` 的客户端实例。
- 前端属性面板支持在“默认模型（系统设置）/指定模型（系统模型列表）”之间切换。

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/ai_registry/runtime.py`
  - `backend/app/assistant/skills/workflow_validator.py`
  - `backend/app/assistant_config/service.py`
  - `backend/app/assistant_config/router.py`
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `frontend/src/features/assistant-config/api/workflow.ts`
  - `frontend/src/features/assistant-config/components/workflow/PropertyPanel.tsx`
  - `frontend/src/features/assistant-config/components/workflow/property-panel/nodes/LlmNodeSettings.tsx`
  - `frontend/src/features/assistant-config/components/workflow/property-panel/nodes/OtherNodeSettings.tsx`
  - `frontend/src/features/assistant-config/components/workflow/FlowCanvas.tsx`
  - `frontend/src/locales/zh/common.json`
  - `frontend/src/locales/en/common.json`

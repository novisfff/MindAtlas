# Change: Parameter Extractor 节点增强为“内置提示词 + 可配置输入 + 强结构化输出”

## Why
当前 `parameter_extractor` 节点仍是“instruction + 尽力解析 JSON”的宽松模式，输出可预测性不足，且缺少输入模板与完整结构化字段配置，导致下游编排引用不稳定。

## What Changes
- 为 `parameter_extractor` 增加完整配置能力：
  - 节点模型（沿用 `modelSource/modelId`）
  - `inputContent`（支持模板变量）
  - `instruction`（可选额外说明）
  - `outputFields`（结构化字段定义，必填）
  - 输出参数列表（前端派生展示）
- **BREAKING（运行语义）**：参数提取节点改为严格结构化模式。
  - 保存阶段 `outputFields` 为空直接失败
  - 运行阶段若模型输出非 JSON 或缺字段，节点直接报错中断，不再静默降级
- 内置稳定系统提示词始终生效，用户说明作为附加约束。

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `backend/app/assistant/skills/workflow_validator.py`
  - `backend/tests/test_langgraph_engine_streaming.py`
  - `backend/tests/test_workflow_validator.py`
  - `frontend/src/features/assistant-config/api/workflow.ts`
  - `frontend/src/features/assistant-config/components/workflow/FlowCanvas.tsx`
  - `frontend/src/features/assistant-config/components/workflow/WorkflowNode.tsx`
  - `frontend/src/features/assistant-config/components/workflow/property-panel/nodes/OtherNodeSettings.tsx`
  - `frontend/src/features/assistant-config/components/workflow/variableReferences.ts`
  - `frontend/src/features/assistant-config/components/workflow/referenceTransform.ts`
  - `frontend/src/locales/zh/common.json`
  - `frontend/src/locales/en/common.json`

# Change: Add Workflow Start Input Modes And Structured Binding Guard

## Why
Workflow start node currently only supports plain text input and has no explicit start-level schema. This blocks workflows that need deterministic structured input for testing and execution. At the same time, skills currently can bind any workflow, which conflicts with structured-input workflows that are not conversation-like.

## What Changes
- Add start node input configuration for workflow DAG:
  - `inputMode`: `text` | `structured`
  - `structuredFields` (flat schema only)
- Add start-node property panel UI for:
  - workflow description editing
  - input mode switch
  - structured field schema editing
- Extend workflow test-run payload to support structured input:
  - `user_input?: string`
  - `structured_input?: object`
- Update runtime start-node behavior:
  - text mode outputs `start.user_input`
  - structured mode outputs `start.<field>` only
- Add strict skill-binding guard:
  - structured-input workflows cannot be bound to skills
  - workflows already referenced by skills cannot be switched to structured mode
- Add validation and variable-reference alignment for start fields.

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant_config/service.py`
  - `backend/app/assistant_config/router.py`
  - `backend/app/assistant_config/workflow_test_service.py`
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `backend/app/assistant/skills/workflow_validator.py`
- Affected frontend code:
  - `frontend/src/features/assistant-config/api/workflow.ts`
  - `frontend/src/features/assistant-config/api/workflows.ts`
  - `frontend/src/features/assistant-config/components/workflow/startNodeConfig.ts`
  - `frontend/src/features/assistant-config/components/workflow/property-panel/nodes/StartNodeSettings.tsx`
  - `frontend/src/features/assistant-config/components/workflow/PropertyPanel.tsx`
  - `frontend/src/features/assistant-config/components/workflow/variableReferences.ts`
  - `frontend/src/features/assistant-config/components/workflow/nodeFactory.ts`
  - `frontend/src/features/assistant-config/components/workflow/serialization.ts`
  - `frontend/src/features/assistant-config/components/workflow/WorkflowTestRunPanel.tsx`
  - `frontend/src/features/assistant-config/stores/workflow-test-run-store.ts`
  - `frontend/src/features/assistant-config/components/skillTargetOptions.ts`
  - `frontend/src/features/assistant-config/components/SkillRowEditor.tsx`
  - `frontend/src/features/assistant-config/components/useSkillForm.ts`
  - `frontend/src/features/assistant-config/pages/WorkflowEditorPage.tsx`
  - `frontend/src/locales/zh/common.json`
  - `frontend/src/locales/en/common.json`

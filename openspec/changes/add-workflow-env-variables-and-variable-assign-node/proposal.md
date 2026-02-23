# Change: Add Workflow ENV Variables and Variable Assign Node

## Why
Workflow DAG currently lacks a built-in mutable session state. Users cannot define workflow-level variables, update them during execution, or reference those values consistently across nodes and container bodies.

## What Changes
- Add workflow session ENV variable definitions on the main graph `start` node config (`sessionVars` / `session_vars`).
- Add new node type `variable_assign` with operations:
  - `set`
  - `increment`
  - `append`
- Add runtime ENV state initialization per run and `env.<name>` reference support.
- Support ENV read/write in both main graph and `iteration`/`loop` container bodies.
- Add validator rules for:
  - ENV var schema and type/default compatibility
  - `env.<name>` reference existence
  - `variable_assign` config completeness and operation/type compatibility
- Add workflow editor ENV management UI and `variable_assign` property panel.
- Add i18n keys for ENV panel and variable assign node.

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant/skills/workflow_env_vars.py`
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `backend/app/assistant/skills/workflow_validator.py`
  - `backend/app/assistant/skills/base.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant_config/router.py`
- Affected frontend code:
  - `frontend/src/features/assistant-config/api/workflow.ts`
  - `frontend/src/features/assistant-config/components/workflow/*`
  - `frontend/src/features/assistant-config/pages/WorkflowEditorPage.tsx`
  - `frontend/src/locales/zh/common.json`
  - `frontend/src/locales/en/common.json`

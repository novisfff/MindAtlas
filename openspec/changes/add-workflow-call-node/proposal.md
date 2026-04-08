# Change: Add Workflow Call Node

## Why
Workflow DAG currently cannot reuse another workflow as a first-class callable node. Users need a dedicated `workflow_call` node so a workflow can reference published child workflows with explicit contract binding, versioning, validation, and runtime isolation.

## What Changes
- Add OpenSpec change `add-workflow-call-node` under `assistant-orchestration`.
- Add a new workflow node type `workflow_call` for main graph and container body subflows.
- Add callable-workflow contract listing APIs that expose:
  - eligible child workflows
  - callable published versions
  - contract-derived input/output params
- Add node config with:
  - `targetWorkflowId`
  - `bindingMode`
  - `targetPublishedVersionId`
  - `inputBindings`
- Reuse shared published-workflow contract parsing logic so OpenClaw capability validation and workflow-call validation stay aligned.
- Add backend save/compile/dependency validation for callable targets, version binding, input bindings, and cross-workflow cycle detection.
- Add runtime execution that invokes child workflows with scoped trace events and shared human-approval lifecycle handling.
- Add delete/degrade protection when a workflow or published version is still referenced by `workflow_call`.
- Add frontend editor support:
  - separate `Workflows` palette section
  - quick-add workflows section
  - `workflow_call` node rendering
  - property panel for workflow/version/input binding selection
  - output-field references in variable mention builder

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant_config/service.py`
  - `backend/app/assistant_config/router.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant/skill_catalog/base.py`
  - `backend/app/assistant/workflow/validation/*`
  - `backend/app/assistant/workflow/engine/*`
  - `backend/app/openclaw_integration/service.py`
- Affected frontend code:
  - `frontend/src/features/assistant-config/api/*`
  - `frontend/src/features/assistant-config/components/workflow/*`
  - `frontend/src/features/assistant-config/pages/WorkflowEditorPage.tsx`
  - `frontend/src/features/assistant-config/stores/*`
  - `frontend/src/locales/en/common.json`
  - `frontend/src/locales/zh/common.json`

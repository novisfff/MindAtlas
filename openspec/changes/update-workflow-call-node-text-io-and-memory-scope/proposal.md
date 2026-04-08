# Change: Update Workflow Call Node Text I/O And Memory Scope

## Why
`workflow_call` currently only supports callable child workflows with structured start input and unique structured output. That blocks text-first workflows from being reused, and nested workflow executions do not yet have a dedicated child-memory scope that can accumulate context across repeated calls.

## What Changes
- Add follow-up OpenSpec change `update-workflow-call-node-text-io-and-memory-scope` under `assistant-orchestration`.
- Expand callable workflow contracts from "structured only" to "text or structured":
  - text input normalizes to required `user_input: string`
  - text output normalizes to canonical `response`
  - callable filtering accepts `text->text`, `text->structured`, `structured->text`, and `structured->structured`
- Extend callable workflow API payloads with `inputMode` and `outputMode`.
- Keep `workflow_call` as a dedicated node type, but update validation/runtime/input binding rules so text child workflows bind through `user_input`.
- Add stable nested workflow identity metadata (`workflowId`, `workflowVersionId`) in runtime skill/state plumbing.
- Add workflow-call child memory scopes:
  - child workflow reads parent memory plus child-scope memory merged by call-node scope
  - assistant chat persists child-scope memory in a dedicated table
  - workflow test run round-trips child-scope memory through `sessionMemory.workflowCallScopes`
  - no conversation/session scope means read-only temporary child memory without persistence
- Update frontend callable workflow UI and workflow test panel to handle text child workflows and hidden nested memory scope round-trip.

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant_config/workflow_contracts.py`
  - `backend/app/assistant_config/service.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant_config/workflow_test_service.py`
  - `backend/app/assistant/workflow/engine/*`
  - `backend/app/assistant/memory_service.py`
  - `backend/app/assistant/models.py`
  - `backend/alembic/versions/*`
- Affected frontend code:
  - `frontend/src/features/assistant-config/api/*`
  - `frontend/src/features/assistant-config/components/workflow/*`
  - `frontend/src/features/assistant-config/stores/*`
  - `frontend/src/locales/en/common.json`
  - `frontend/src/locales/zh/common.json`

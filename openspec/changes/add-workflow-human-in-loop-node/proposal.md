# Change: Add Workflow Human-in-Loop Node

## Why
Workflow DAG currently cannot pause for explicit human confirmation before continuing high-impact actions (such as creating records). Users need a built-in approval gate with editable fields and deterministic branch routing.

## What Changes
- Add new workflow node type `human_in_loop` for both main graph and `iteration`/`loop` container body.
- Add `human_in_loop` node config for title/instruction/fields/approve-reject labels/reject-comment policy.
- Add validator rules for `human_in_loop` config and outgoing branch handles:
  - exactly one `approved` edge
  - exactly one `rejected` edge
- Add runtime pause-and-resume execution with persisted approval records.
- Add approval decision APIs for workflow test runs and assistant conversations.
- Add SSE events:
  - `human_approval_requested`
  - `human_approval_resolved`
- Add shared frontend HITL approval card components and integrate into:
  - workflow test-run panel
  - assistant chat message stream
- Add i18n keys for node editor and approval UI.

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `backend/app/assistant/skills/workflow_validator.py`
  - `backend/app/assistant/skills/human_loop_runtime.py`
  - `backend/app/assistant/service.py`
  - `backend/app/assistant/router.py`
  - `backend/app/assistant_config/workflow_test_service.py`
  - `backend/app/assistant_config/router.py`
  - `backend/app/assistant_config/models.py`
- Affected frontend code:
  - `frontend/src/features/assistant-config/components/workflow/*`
  - `frontend/src/features/assistant-config/stores/workflow-test-run-store.ts`
  - `frontend/src/features/assistant/*`
  - `frontend/src/features/shared/hitl/*`
  - `frontend/src/locales/zh/common.json`
  - `frontend/src/locales/en/common.json`

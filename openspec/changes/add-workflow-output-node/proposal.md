# Change: Add Dedicated Output Node And Caller-Controlled Streaming

## Why
The current workflow terminal behavior is coupled to `llm.isOutput`, which makes terminal output semantics ambiguous and hard to validate across complex graphs. We also need streaming behavior to be chosen by API callers (`streamOutput`) instead of node config so the same workflow can serve both streaming and non-streaming consumers.

## What Changes
- Add a first-class `output` node type for workflow DAG terminal output.
- Replace legacy terminal rule (`llm.isOutput`) with strict rule: workflow MUST have exactly one `output` node.
- Add two output modes on `output` node:
  - `text`: render `textTemplate`
  - `structured`: render and type-check `outputFields[*].value`
- Make streaming caller-controlled through chat request `streamOutput`/runtime `stream_output`.
- Implement output streaming behavior:
  - token passthrough from upstream LLM for single-variable output template
  - fallback one-shot output emission when passthrough is not applicable
  - structured output always one-shot JSON emission
- Remove LLM-side "final output" UI/runtime responsibility.
- Add one-time migration script to convert legacy `llm.isOutput` workflows to `output` node workflows.

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `backend/app/assistant/skills/workflow_validator.py`
  - `backend/app/assistant/schemas.py`
  - `backend/app/assistant/router.py`
  - `backend/app/assistant/service.py`
  - `backend/app/assistant/skills/base.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant/skills/definitions.py`
  - `backend/scripts/migrate_workflow_output_nodes.py`
  - `frontend/src/features/assistant-config/components/workflow/*`
  - `frontend/src/features/assistant-config/api/workflow.ts`
  - `frontend/src/locales/*/common.json`

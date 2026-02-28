# Change: Add Iteration/Loop Container Nodes for Workflow DAG

## Why
Current workflow DAG cannot model array iteration or stateful loops as first-class control nodes. Users need Dify-like control components to express repeated execution with explicit, debuggable semantics.

## What Changes
- Add new workflow node types: `iteration` and `loop`.
- Add container subflow config (`bodyNodes`/`bodyEdges`) embedded in node config (no DB schema migration).
- Add save-time validation for container subflow topology, forbidden nesting, and config constraints.
- Add runtime execution for:
  - `iteration`: array traversal, optional parallel mode, error strategy, flatten output.
  - `loop`: variable init/update, IF/ELSE-style termination conditions, max iteration guard.
- Add frontend palette, node rendering, settings panel, variable references, and i18n for the new nodes.

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant/skills/base.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant/skills/workflow_validator.py`
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `backend/app/assistant_config/service.py`
  - `frontend/src/features/assistant-config/components/workflow/*`
  - `frontend/src/features/assistant-config/api/workflow.ts`
  - `frontend/src/locales/*/common.json`

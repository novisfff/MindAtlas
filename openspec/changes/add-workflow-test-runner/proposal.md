# Change: Add Workflow Test Runner In Editor

## Why
Workflow DAG editing currently lacks a closed-loop test experience for draft graphs. Users must save and trigger external flows to validate behavior, which slows iteration and makes node-level debugging difficult.

## What Changes
- Add a workflow test-run endpoint in `assistant-config` that executes draft workflow input without persistence.
- Add full SSE trace protocol for run lifecycle, node events, branch decisions, tool calls, and output deltas.
- Add per-node I/O snapshot event (`node_snapshot`) so each executed node can be inspected in trace.
- Add editor-side test panel with input, run/cancel, stream toggle, result/trace/raw tabs.
- Add runtime node highlight in canvas based on incoming test trace events.
- Keep test runs isolated from assistant conversation storage.

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant_config/router.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant_config/workflow_test_service.py`
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `frontend/src/features/assistant-config/api/workflow.ts`
  - `frontend/src/features/assistant-config/pages/WorkflowEditorPage.tsx`
  - `frontend/src/features/assistant-config/components/workflow/WorkflowTestRunPanel.tsx`
  - `frontend/src/features/assistant-config/components/workflow/FlowCanvas.tsx`
  - `frontend/src/features/assistant-config/components/workflow/WorkflowNode.tsx`
  - `frontend/src/features/assistant-config/stores/workflow-test-run-store.ts`
  - `frontend/src/lib/sse/SSEParser.ts`

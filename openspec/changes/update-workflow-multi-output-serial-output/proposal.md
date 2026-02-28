# Change: update-workflow-multi-output-serial-output

## Why
Current workflow output semantics require exactly one `output` node. This blocks valid branch-heavy workflows where multiple terminal branches must each produce a response payload. Product requires multi-output support while preserving a single chat message stream.

## What Changes
- Relax workflow topology rule from `exactly one output` to `at least one output`.
- Keep `output` node terminal-only rule (no outgoing edge).
- Allow multiple `output` nodes in editor (drag-drop and quick-add).
- Keep output node shape as input-only (no source handle).
- Add output-source metadata to runtime `content_delta` events.
- Serialize multi-output chunks into a single chat stream in completion order, with `\n\n` segment separators when output source switches.
- Preserve LLM passthrough optimization only for single-output workflows.
- Keep multi-output structured mode as JSON-text segments (no array aggregation contract).

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant/workflow/validation/rules/context_rules.py`
  - `backend/app/assistant/workflow/validation/validator.py`
  - `backend/app/assistant/workflow/engine/node_builders/output_node.py`
  - `backend/app/assistant/workflow/engine/stream_runtime.py`
  - `frontend/src/features/assistant-config/components/workflow/FlowCanvas.tsx`
  - `frontend/src/features/assistant-config/components/workflow/workflowValidation.ts`

# Change: update-workflow-human-in-loop-field-widgets

## Why
Current `human_in_loop` fields only support basic scalar types with text/boolean-like rendering. Product now needs richer, schema-driven approval controls so workflow configuration can directly drive approval UI in workflow test-run and assistant chat.

## What Changes
- Extend HITL field schema with `widget`, `options`, `allowCustom`, and `placeholder`.
- Add additional field type support for `array` to represent tag selector values (`string[]`).
- Enforce widget/type compatibility and option constraints in workflow validator.
- Enforce decision payload coercion and validation using combined type + widget semantics.
- Render approval forms by widget in shared frontend HITL components (workflow test-run + assistant chat).
- Keep backward compatibility for old HITL fields without `widget` metadata.

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant/skills/workflow_validator.py`
  - `backend/app/assistant/skills/langgraph_engine.py`
  - `backend/app/assistant/skills/human_loop_runtime.py`
  - `frontend/src/features/assistant-config/components/workflow/property-panel/nodes/HumanInLoopNodeSettings.tsx`
  - `frontend/src/features/shared/hitl/HumanApprovalFieldForm.tsx`
  - `frontend/src/features/shared/hitl/HumanApprovalCard.tsx`

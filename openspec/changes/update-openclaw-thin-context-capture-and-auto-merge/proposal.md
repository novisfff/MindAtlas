# Change: Update OpenClaw Thin-Context Capture And Auto-Merge

## Why
The shipped OpenClaw `submit_context_capture` capability already routes through a workflow, but its public contract still asks OpenClaw to provide multiple capture-side hints that should instead be owned by OpenClaw metadata and MindAtlas extraction logic. At the same time, the workflow can only create a new entry, so repeated milestone captures are more likely to create duplicates than to converge on one durable record.

## What Changes
- Reduce the shipped `submit_context_capture` public input contract to a single required `context` field.
- Bridge OpenClaw request metadata (`source`, `channel`, `session`, `tool`) into workflow runtime `sys` variables so system workflows can use request context without exposing more public fields.
- Rebuild the shipped `openclaw_context_capture` workflow so it extracts final fields from thin context, searches recent candidates, makes a conservative create-vs-merge decision, and rewrites matched entries through a new internal `update_entry` tool.
- Keep `submit_context_capture` as the only official OpenClaw entry-write capability and do not add a second public merge or field-level capture path.
- Update shipped OpenClaw metadata and plugin skills so OpenClaw is explicitly guided to send one high-value context block instead of hand-assembling final entry fields.

## Impact
- Affected specs:
  - `external-agent-integration`
  - `openclaw-plugin-package`
  - `assistant-orchestration`
- Affected code:
  - `backend/app/openclaw_integration/*`
  - `backend/app/assistant/workflow/engine/*`
  - `backend/app/assistant/tools/*`
  - `backend/app/assistant/skill_catalog/system_defaults/workflows/openclaw_context_capture*.json`
  - `integrations/openclaw-mindatlas/*`
  - `backend/tests/test_openclaw_integration.py`
  - `integrations/openclaw-mindatlas/tests/*.test.ts`

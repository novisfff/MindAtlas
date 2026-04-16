# Change: Upgrade smart_capture into guided create-or-merge with relation follow-up

## Why
`smart_capture` currently stops at a single human-confirmed create step. That is too weak for assistant-first capture: we need early duplicate triage, human-selected merge targets, a second write gate, and a post-write relation follow-up that improves graph quality without changing the OpenClaw-facing `context_capture` contract.

## What Changes
- Redefine `smart_capture` as a staged human-guided create-or-merge workflow:
  - pre-search similar entries from raw input
  - conditional human triage for `create_new` vs `merge_existing`
  - materialize payload after that choice
  - split write confirmation for create vs merge
  - recommend relations after successful persistence
  - batch human confirmation for selected relations
- Extend `human_in_loop` with a reusable `checkbox_group` widget and object-style options `{ value, label, description? }` while keeping string-list options backward compatible.
- Keep `context_capture` unchanged as the OpenClaw-facing thin-context automatic create/merge workflow.
- Update system asset descriptions so `smart_capture` is described as system-skill / in-app-assistant first, while remaining a reusable general workflow.

## Impact
- Affected specs:
  - `assistant-orchestration`
- Affected code:
  - `backend/app/assistant/workflow/human_fields.py`
  - `backend/app/assistant/workflow/human_approval_runtime.py`
  - `backend/app/assistant/workflow/engine/runtime_helpers.py`
  - `backend/app/assistant/workflow/engine/node_builders/human_in_loop_node.py`
  - `backend/app/assistant/workflow/engine/snapshot_input_resolvers.py`
  - `backend/app/assistant/workflow/system_assets/workflows/smart_capture*.json`
  - `backend/app/assistant/workflow/system_assets/registry.py`
  - related workflow/HITL regression tests
  - frontend HITL and workflow-editor field option rendering

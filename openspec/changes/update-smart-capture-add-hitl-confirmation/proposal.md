# Change: Add Human Confirmation Gate to `smart_capture` System Workflow

## Why
The current `smart_capture` system workflow creates entries immediately after model extraction. For record creation, users need a final human-in-the-loop approval step to review/edit payload fields before write operations.

## What Changes
- Insert a `human_in_loop` node (`human_confirm`) between `llm_time` and `tool_create` in the system default `smart_capture` workflow preset.
- Route `approved` to `tool_create`, and route `rejected` to a cancel-response branch that ends without calling `create_entry`.
- Bind `tool_create.inputBindings` to `human_confirm.*` values so final write payload comes from approved/edited human inputs.
- Include all persisted create fields in confirmation form (`title`, `summary`, `content`, `type_code`, `tags`, `time_mode`, `time_at`, `time_from`, `time_to`), with `tags` edited as comma-separated text.
- Add regression tests for preset topology, references, and baseline loading.

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant/skills/system_defaults/workflows/smart_capture.json`
  - `backend/tests/test_system_workflow_layout_presets.py`
  - `backend/tests/test_system_skill_workflow_refs.py`
  - `backend/tests/test_system_defaults_loader.py`
- No API route changes.
- No DB migration.
- Existing persisted system workflows remain unchanged until reset/rebind to system defaults.

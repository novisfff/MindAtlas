# Change: Update System Default Workflow Layout Presets

## Why
Current system default workflows can appear visually unbalanced on first open, with unnecessary zig-zag branches and weak horizontal readability. This increases cognitive load during initial onboarding and after reset.

## What Changes
- Update hard-coded node coordinates for system workflow definitions:
  - `quick_stats`
  - `smart_capture`
  - `periodic_review`
- Keep behavior scope strict:
  - apply to first-time system target creation
  - apply to reset-to-default flows
  - do not overwrite existing persisted system workflow coordinates during normal sync
- Add regression tests for layout preset creation/reset/non-overwrite behavior.

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant/skills/definitions.py`
  - `backend/app/assistant_config/service.py`
  - `backend/tests/test_system_workflow_layout_presets.py`
- No API, schema, or migration changes.

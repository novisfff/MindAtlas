# Change: Add OpenClaw Context Submission Capture Workflow

## Why
The first OpenClaw integration phase should not keep using field-level `capture_entry` as the default recording path. OpenClaw is better suited to submit thin, high-level context, while MindAtlas should own record materialization through an internal workflow that can evolve without exposing unstable entry fields to the external agent.

## What Changes
- Add a canonical system workflow asset for OpenClaw recording materialization.
- Extend OpenClaw system presets so a preset can bind to a workflow-backed system asset instead of only `system_adapter`.
- Seed a new workflow-backed default recording preset that accepts thin context input.
- Keep the legacy `capture_entry` runtime adapter for compatibility, but seed and reset it as disabled by default.
- Migrate existing OpenClaw system preset state so older `capture_entry` presets are automatically downgraded.
- Keep the OpenClaw runtime/plugin contract stable by continuing to serve catalog metadata from the existing `/api/integrations/openclaw/*` routes.

## Impact
- Affected specs: `external-agent-integration`
- Affected code:
  - `backend/app/openclaw_integration/*`
  - `backend/app/assistant_config/service.py`
  - `backend/app/assistant/skill_catalog/system_defaults/workflows/*`
  - `backend/app/assistant/workflow/*`
  - `backend/alembic/versions/*`
  - `backend/tests/test_openclaw_integration.py`
  - `docs/openclaw/*`
  - `integrations/openclaw-mindatlas/skills/mindatlas-overview/SKILL.md`

# Change: Add OpenClaw Capability Gateway Integration

## Why
MindAtlas already has rich internal tools, workflows, and agents, but the original OpenClaw integration prototype exposed only a fixed backend-owned capability list. That made the integration too rigid: administrators could not publish their own reusable executables, and OpenClaw could not discover a curated capability catalog that reflects how a specific MindAtlas instance is configured.

## What Changes
- Keep the dedicated `/api/integrations/openclaw/*` runtime facade and app-level bearer-secret authentication.
- Replace the fixed capability toggle list with a persisted capability catalog made of independent catalog items.
- Allow catalog items to bind to three MindAtlas source types in v1: `Assistant Tool`, `Assistant Workflow`, and `Assistant Agent Profile`.
- Auto-seed a set of system preset catalog items for MindAtlas core capabilities, while still allowing admins to add, edit, disable, and delete custom catalog items.
- Keep system presets non-deletable but resettable, and migrate old fixed capability enable flags into the new catalog model.
- Rebuild the OpenClaw settings page around catalog management instead of a fixed registry toggle list.
- Update OpenClaw integration docs so the external plugin consumes dynamic catalog metadata rather than a hard-coded capability list.

## Impact
- Affected specs: `external-agent-integration`
- Affected code:
  - `backend/app/openclaw_integration/*`
  - `backend/alembic/versions/*`
  - `backend/tests/test_openclaw_integration.py`
  - `frontend/src/features/settings/*`
  - `frontend/src/locales/*/common.json`
  - `docs/openclaw/*`

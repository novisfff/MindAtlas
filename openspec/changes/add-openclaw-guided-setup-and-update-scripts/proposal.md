# Change: Add OpenClaw Guided Setup And Update Scripts

## Why
OpenClaw MindAtlas integration still asks operators to manually piece together plugin install, config editing, skill sync, legacy cleanup, and Gateway restart steps. In practice this causes repeated upgrade failures, stale shipped skills, and inconsistent instructions between the settings page, README, and shipped skills.

## What Changes
- Add guided `setup:openclaw` and `update:openclaw` operator scripts for the `openclaw-mindatlas` plugin.
- Centralize OpenClaw config mutation, legacy MindAtlas cleanup, conflicting skill backup, and Gateway restart into one shared script management layer.
- Change the settings page, README, and shipped skills to recommend the guided scripts first while keeping the manual CLI flow as a fallback.

## Impact
- Affected specs: `external-agent-integration`
- Affected code:
  - `integrations/openclaw-mindatlas/scripts/*`
  - `integrations/openclaw-mindatlas/package.json`
  - `integrations/openclaw-mindatlas/README.md`
  - `integrations/openclaw-mindatlas/skills/*`
  - `integrations/openclaw-mindatlas/tests/*`
  - `frontend/src/features/settings/pages/OpenClawIntegrationSettings.tsx`
  - `frontend/src/locales/en/common.json`
  - `frontend/src/locales/zh/common.json`

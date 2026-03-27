# Change: Add OpenClaw MindAtlas Native Plugin Package

## Why
MindAtlas now exposes a stable OpenClaw integration facade, but the repository still ships only the MindAtlas side of that contract. There is no real OpenClaw plugin package that administrators can install, configure, and run against the dynamic capability catalog.

## What Changes
- Add a native `openclaw-mindatlas` plugin package under `integrations/` for local install and future npm publishing.
- Discover MindAtlas capabilities dynamically and register one OpenClaw tool per exposed catalog item.
- Forward tool execution to MindAtlas runtime APIs with stable error mapping and audit headers.
- Bundle the `MindAtlas Overview` skill as a real shipped plugin asset.
- Add plugin-focused documentation, tests, and OpenClaw setup guidance in MindAtlas docs and settings copy.

## Impact
- Affected specs: `openclaw-plugin-package`
- Affected code:
  - `integrations/openclaw-mindatlas/*`
  - `docs/openclaw/*`
  - `frontend/src/features/settings/pages/OpenClawIntegrationSettings.tsx`
  - `frontend/src/locales/*/common.json`

# Change: Refactor `openclaw-mindatlas` To The Official OpenClaw SDK Entry

## Why
`openclaw-mindatlas` currently logs successful MindAtlas tool registration, yet fresh OpenClaw sessions still fail to receive any `mindatlas_*` tools. The runtime mismatch is that the plugin registers a background service during `register()` and then performs `registerTool(...)` later inside `service.start()`. Official OpenClaw SDK guidance for `2026.4.1+` expects non-channel plugins to use `definePluginEntry(...)` and register tools from the plugin entry's `register(api)` phase.

At the same time, the package still carries an installation-time compatibility hack that writes `tools.profile` and `tools.allow` into OpenClaw config. That path is now both unofficial and misleading, because current OpenClaw surfaces still treat those entries as unknown when the plugin entry shape is wrong.

## What Changes
- Rebuild `openclaw-mindatlas` on top of the official `definePluginEntry(...)` SDK entry and move first-pass MindAtlas tool registration into `register(api)`.
- Keep the live MindAtlas catalog as the only source of truth, but restrict background refresh to state drift detection and availability updates instead of late-registering tools after startup.
- Remove the install-time `tools.profile/tools.allow` compatibility mutation; `configure:skills` will only keep the shipped MindAtlas skills visible and warn on legacy tool-policy remnants.
- Update package metadata, tests, docs, and settings copy so they reflect the official required-tool registration path on OpenClaw `2026.4.1+`.

## Impact
- Affected specs:
  - `openclaw-plugin-package`
- Affected code:
  - `integrations/openclaw-mindatlas/src/index.ts`
  - `integrations/openclaw-mindatlas/package.json`
  - `integrations/openclaw-mindatlas/scripts/configure-openclaw-skills.mjs`
  - `integrations/openclaw-mindatlas/tests/*.test.ts`
  - `integrations/openclaw-mindatlas/README.md`
  - `docs/openclaw/README.md`
  - `frontend/src/locales/en/common.json`
  - `frontend/src/locales/zh/common.json`

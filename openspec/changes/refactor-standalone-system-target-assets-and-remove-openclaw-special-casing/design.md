## Context
System workflows currently come from two first-class sources:
- system skills, which own canonical `__workflow` / `__agent` targets
- system AI behaviors, which own canonical default targets such as `system_weekly_report__workflow`

`submit_context_capture` is the remaining outlier. It is already persisted as a real workflow target, but its canonical name, preset lookup, and lifecycle still live in OpenClaw integration code instead of assistant-config.

## Goals
- Make the context capture workflow a first-class assistant-config system workflow asset.
- Remove OpenClaw-owned preset materialization for workflow-backed system items.
- Preserve existing OpenClaw runtime identity and contracts.
- Keep system workflow visibility, immutability, copy-first customization, and callable-workflow behavior aligned with the rest of assistant-config.

## Non-Goals
- Refactor temporary runtime wrappers such as `openclaw__...__agent` or `__agent_test`.
- Change `submit_context_capture` schemas, tool names, capability keys, or execution semantics.
- Add standalone system agent assets in this change.

## Decisions

### 1. Assistant-config owns standalone persistent system workflow assets
- Add a dedicated standalone system target registry under assistant-config.
- v1 only supports workflow assets.
- Each definition provides `assetKey`, `canonicalName`, localized display name, localized description, preset file, enabled default, and optional legacy canonical names.

### 2. Context capture becomes the first standalone system workflow asset
- The asset key is `context_capture`.
- The new canonical workflow name is `system_context_capture__workflow`.
- The old canonical name `system_openclaw_context_capture__workflow` becomes a legacy alias used only for in-place migration and compatibility lookup during sync.
- The preset files are renamed to `system_context_capture.json` and `system_context_capture.en.json`.

### 3. OpenClaw binds to shared system workflow assets, not private workflow presets
- OpenClaw system item definitions keep their current metadata but replace workflow preset fields with a shared workflow asset key.
- `submit_context_capture` resolves its workflow binding through assistant-config's standalone system workflow asset path.
- OpenClaw settings and catalog responses must surface the assistant-config display name for workflow and agent sources instead of raw canonical names.

### 4. Sync and migration preserve target identity
- Standalone system workflow sync reuses an existing `is_system=true` workflow row with the new canonical name when present.
- Otherwise it reuses a legacy `is_system=true` row with a declared legacy canonical name and renames it in place.
- If a non-system workflow already owns the new canonical name, sync fails with a clear conflict instead of creating a second hidden system workflow.
- A migration runs assistant-config sync plus OpenClaw system-item sync so existing databases converge automatically.

### 5. Tests need an explicit origin audit
- Add a test helper that audits persisted `is_system=true` workflows and agents.
- A system target is valid only if it belongs to exactly one source class:
  - system skill target
  - system behavior canonical default target
  - standalone system target registry entry

## Risks / Trade-offs
- Renaming a canonical workflow touches persistence, display, and OpenClaw lookup paths at once.
  - Mitigation: keep the workflow row ID stable and drive all lookup through sync plus a migration.
- Making the workflow normally visible means it can appear in callable workflow listings.
  - Mitigation: rely on the existing callable workflow contract gates instead of adding a new exclusion.

## Migration Plan
1. Add the standalone system workflow registry and sync path in assistant-config.
2. Rename the preset files and switch all baseline lookup to the new registry.
3. Add an Alembic migration that syncs system skills, standalone system targets, system behaviors, and OpenClaw system items.
4. Remove OpenClaw-owned workflow preset materialization and bind `submit_context_capture` through the shared asset key.
5. Update tests and validate the new OpenSpec change.

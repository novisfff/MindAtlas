# Change: Improve OpenClaw MindAtlas Dynamic Runtime And Plugin Skill Injection

## Why
The current `openclaw-mindatlas` package correctly exposes MindAtlas tools from the live capability catalog, but operators still run into two practical gaps:

- dynamic tool registration is harder to diagnose than a static tool surface because startup success, zero-tool states, and metadata drift are not summarized clearly
- the plugin ships 4 MindAtlas skills in its manifest, but current OpenClaw builds may drop plugin `skills` metadata before registry resolution, so those skills never become visible in `openclaw skills list` or new sessions

## What Changes
- Keep the catalog-driven `mindatlas_*` tool model as the only source of truth.
- Add a plugin-side fallback that syncs the 4 shipped MindAtlas skills into the active OpenClaw custom skills directory with idempotent, plugin-owned markers.
- Add richer catalog refresh observability, including success summaries, explicit zero-registration warnings, and clearer reload messaging for session hot-refresh limitations.
- Update installation and settings docs so they distinguish between runtime-discovered tools and bundled guidance skills, and explain the restart / new-session expectations.

## Impact
- Affected specs:
  - `openclaw-plugin-package`
- Affected code:
  - `integrations/openclaw-mindatlas/*`
  - `docs/openclaw/*`
  - `frontend/src/locales/*/common.json`

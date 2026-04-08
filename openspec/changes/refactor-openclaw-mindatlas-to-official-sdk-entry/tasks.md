## 1. Implementation
- [x] 1.1 Replace the custom `openclaw-mindatlas` entry with the official `definePluginEntry(...)` SDK entry and move first-pass tool registration into `register(api)`.
- [x] 1.2 Keep dynamic TTL refresh, but remove late-registration so post-start changes only update runtime state and reload guidance.
- [x] 1.3 Update `configure:skills` to manage only `skills.load.extraDirs` and warn on legacy MindAtlas `tools.allow/tools.profile` remnants.
- [x] 1.4 Update package metadata, docs, and settings copy to the OpenClaw `2026.4.1+` official required-tool model.
- [x] 1.5 Refresh plugin tests so they validate registration timing, startup failure handling, no late-register behavior, and the new `configure:skills` contract.

## 2. Validation
- [x] 2.1 Run `npm --prefix integrations/openclaw-mindatlas test`.
- [x] 2.2 Run `npm --prefix integrations/openclaw-mindatlas run build`.
- [x] 2.3 Run `openspec validate refactor-openclaw-mindatlas-to-official-sdk-entry --strict --no-interactive`.

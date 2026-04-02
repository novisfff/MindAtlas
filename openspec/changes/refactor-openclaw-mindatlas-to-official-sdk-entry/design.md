## Context

The MindAtlas capability catalog and the shipped MindAtlas skills are already doing the right conceptual work: they expose a live `mindatlas_*` tool surface and guide OpenClaw toward durable memory, retrieval, recap, relation, and graph tasks. The breakage is at the plugin integration seam.

Official OpenClaw `2026.4.1+` documentation for non-channel plugins describes:
- `definePluginEntry(...)` from `openclaw/plugin-sdk/plugin-entry`
- a `register(api)` entrypoint as the canonical place for `api.registerTool(...)`
- plugin package metadata that declares the SDK and Gateway compatibility range in `package.json`

The current `openclaw-mindatlas` package still uses a custom runtime object that waits until `registerService(...).start()` before it calls `registerTool(...)`. That is late relative to the official registry lifecycle, so OpenClaw can log the registration attempt without actually promoting the tools into the session-visible tool surface.

## Goals
- Align `openclaw-mindatlas` with the official OpenClaw SDK entry contract for `2026.4.1+`.
- Register available MindAtlas tools during the plugin entry `register(api)` phase, not later from a background service start hook.
- Keep the live catalog model, current `mindatlas_*` tool names, current backend API contract, current TTL refresh, and current shipped skills.
- Remove the unofficial `tools.profile/tools.allow` compatibility write path from installation.

## Non-Goals
- No new router tool or explicit catalog tool
- No change to `/api/integrations/openclaw/capabilities` or `/execute`
- No OpenClaw upstream hot-refresh fix
- No compatibility fallback for pre-`2026.4.1` SDK entry contracts

## Design Decisions

### 1. Move first-pass registration into the official SDK entry

The plugin entry becomes:
- `export default definePluginEntry({...})`
- `register(api)` as the only registration surface

During `register(api)` the plugin will:
- sync the shipped MindAtlas skills into the OpenClaw-managed skills directory
- validate plugin configuration
- fetch the live capability catalog once
- register every `available=true` capability with `api.registerTool(...)`
- register a background refresh service

This matches the official SDK model and keeps the first visible MindAtlas tool surface inside the same lifecycle where OpenClaw builds the registry.

### 2. Keep runtime refresh, but narrow it to state tracking

The background refresh service still polls the live catalog on TTL, but it no longer late-registers tools that were missing at startup.

Refresh is limited to:
- updating the in-memory capability mapping for already registered tools
- tracking availability changes for already registered tools
- detecting newly available unregistered tools
- detecting tool-name drift
- detecting metadata drift
- raising `reloadRequired` and emitting clear reload guidance

If startup registration fails or an unregistered capability later becomes available, the operator must reload the Gateway and start a new session. The service can observe and log the condition, but it must not mutate the registry shape after startup.

### 3. Keep skills compatibility, remove tool-policy compatibility

The package continues to keep the shipped MindAtlas skills visible through `skills.load.extraDirs` and runtime skill sync, because plugin-manifest skills are still inconsistent in some OpenClaw builds.

However, `configure:skills` stops writing:
- `tools.profile`
- `tools.allow`

Those fields are no longer treated as the MindAtlas tool exposure mechanism. If the script detects old MindAtlas-specific remnants in `tools.allow` or the paired `tools.profile: full` setting, it warns but leaves user config untouched.

### 4. Update package metadata to the official SDK contract

The plugin package metadata now declares:
- `openclaw.compat.pluginApi`
- `openclaw.compat.minGatewayVersion`
- `openclaw.build.openclawVersion`
- `openclaw.build.pluginSdkVersion`

The target is explicitly OpenClaw `2026.4.1+`, matching the official SDK entry guidance this refactor adopts.

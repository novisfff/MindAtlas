## Context
MindAtlas already ships the capability gateway and admin UI for OpenClaw integration, but not the OpenClaw-side plugin package that consumes the runtime catalog. The missing package is now the main gap between “integration-ready” and “actually installable”.

## Goals
- Ship a real `openclaw-mindatlas` plugin package from this repository.
- Keep the plugin aligned with the MindAtlas runtime catalog instead of hard-coding capability lists.
- Support local install now and npm publishing later without changing runtime behavior.
- Keep the plugin package lightweight and source-loadable.

## Non-Goals
- Add new MindAtlas backend runtime APIs.
- Auto-reload the OpenClaw Gateway when tool structure changes.
- Export MindAtlas skills directly as OpenClaw tools.

## Decisions

### 1. Package Shape
- The plugin lives at `integrations/openclaw-mindatlas/`.
- It ships TypeScript source directly and avoids runtime npm dependencies.
- It uses `openclaw.plugin.json` plus `package.json` `openclaw.extensions`.

### 2. Dynamic Registration
- The plugin discovers the MindAtlas catalog from `/api/integrations/openclaw/capabilities`.
- It registers only `available=true` items as tools.
- Each registered tool name comes directly from the remote catalog item `toolName`.

### 3. TTL Refresh
- A background service refreshes the remote catalog on a TTL.
- If only metadata changes under the same tool-name set, the plugin updates its in-memory execute mapping.
- If the tool-name set changes, the plugin records `reloadRequired` and warns, but does not auto-reload or auto-delete tools.

### 4. Failure Semantics
- If the first catalog load fails, plugin startup does not block Gateway startup.
- No tools are registered until a catalog refresh succeeds.
- Runtime API errors are mapped to short user-friendly failures.

# `openclaw-mindatlas`

`openclaw-mindatlas` is a native OpenClaw agent-tools plugin that discovers the MindAtlas capability catalog and exposes each catalog item as an OpenClaw tool.

## What It Does

- Reads `GET /api/integrations/openclaw/capabilities` from MindAtlas
- Registers one OpenClaw tool per exposed catalog item
- Forwards tool execution to `POST /api/integrations/openclaw/capabilities/{capabilityKey}/execute`
- Bundles the `MindAtlas Overview` skill
- Refreshes the remote catalog on a TTL
- Warns when catalog structure changes require a Gateway / plugin reload

## Local Install

Quick path:

```bash
openclaw plugins install ./integrations/openclaw-mindatlas
```

Development link mode:

```bash
openclaw plugins install -l ./integrations/openclaw-mindatlas
```

After installation, restart the OpenClaw Gateway so the plugin can load and register tools.

## Configuration

Configure the plugin under `plugins.entries.openclaw-mindatlas.config`.

```json
{
  "plugins": {
    "entries": {
      "openclaw-mindatlas": {
        "enabled": true,
        "config": {
          "baseUrl": "http://127.0.0.1:8000",
          "integrationSecret": "paste-the-secret-from-mindatlas",
          "requestTimeoutMs": 15000,
          "catalogRefreshTtlSec": 300
        }
      }
    }
  }
}
```

Notes:

- `baseUrl` should point to the MindAtlas backend origin. A URL ending in `/api` is also accepted.
- `integrationSecret` is generated from `MindAtlas > Settings > OpenClaw Integration`.
- `catalogRefreshTtlSec` controls how often the plugin refreshes the remote capability catalog.

## MindAtlas Side Setup

1. Open MindAtlas `Settings > OpenClaw Integration`.
2. Generate or rotate the integration secret.
3. Enable the integration switch.
4. Review system preset capabilities.
5. Add custom Tool / Workflow / Agent catalog items if needed.
6. Make sure the items you want OpenClaw to see are marked as exposed.

## Runtime Behavior

- The plugin only registers capabilities that are `available = true`.
- `available = false` items are skipped and logged.
- On refresh, availability state and execute targets are updated in memory for unchanged tools.
- If the remote tool-name set changes because of add / delete / rename, the plugin marks `reloadRequired` and logs a warning.
- If an existing tool keeps the same `toolName` but its exported title, description, summaries, or input schema changes, the plugin also marks `reloadRequired`.
- Existing unchanged tools continue to use the latest capability mapping.
- Stale tools whose remote `toolName` disappeared start returning a clear “reload the plugin” error.
- Stale tools whose exported metadata drifted under the same `toolName` also return a clear “reload the plugin” error.
- The plugin does not auto-reload Gateway or auto-re-register renamed tools in v1.

## Development

Run checks from the plugin directory:

```bash
npm install --no-package-lock
npm run build
npm test
```

This package intentionally avoids runtime npm dependencies so it can be loaded directly from source by OpenClaw via `jiti`.

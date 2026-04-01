# `openclaw-mindatlas`

`openclaw-mindatlas` is a native OpenClaw agent-tools plugin that discovers the MindAtlas capability catalog and exposes each catalog item as an OpenClaw tool.

## What It Does

- Reads `GET /api/integrations/openclaw/capabilities` from MindAtlas
- Registers one OpenClaw tool per exposed catalog item
- Forwards tool execution to `POST /api/integrations/openclaw/capabilities/{capabilityKey}/execute`
- Bundles 4 shipped MindAtlas skills that help OpenClaw understand when and how to use the catalog
- Syncs those shipped skills into the active OpenClaw custom skills directory as a compatibility fallback when plugin-manifest skills are not surfaced by the current OpenClaw build
- Refreshes the remote catalog on a TTL
- Logs catalog refresh summaries and warns when catalog structure changes require a Gateway / plugin reload

## Shipped Skills

The plugin ships these 4 skills together:

- `mindatlas-overview`: high-level positioning for what MindAtlas is and when OpenClaw should prefer it
- `mindatlas-auto-capture`: capture policy for durable memory and context submission
- `mindatlas-retrieval`: retrieval routing across search, detail lookup, and graph-style queries
- `mindatlas-summary`: summary routing for weekly, monthly, and topic-oriented reviews

These skills ship with the plugin package. They guide OpenClaw's decision-making, while the actual callable tools still come from the live MindAtlas capability catalog.

Current OpenClaw releases do not always surface plugin-manifest `skills` into `openclaw skills list`. To keep the shipped MindAtlas skills usable, the plugin also syncs them into the active custom skills directory on startup. Existing sessions still may need a new session or Gateway reload before the refreshed skill surface appears.

## Local Install

Quick path:

```bash
openclaw plugins install ./integrations/openclaw-mindatlas
npm --prefix ./integrations/openclaw-mindatlas run configure:skills
```

Development link mode:

```bash
openclaw plugins install -l ./integrations/openclaw-mindatlas
npm --prefix ./integrations/openclaw-mindatlas run configure:skills
```

Or from the plugin directory:

```bash
cd integrations/openclaw-mindatlas
npm run install:openclaw
```

The install command only registers the plugin package. The additional `configure:skills` step writes the installed plugin's `skills` directory into OpenClaw's `skills.load.extraDirs`, which aligns with the current OpenClaw docs and keeps `openclaw skills list` plus new sessions able to see the shipped MindAtlas skills even on builds where plugin-manifest skills are not surfaced consistently.

You can install first and fill in `baseUrl` / `integrationSecret` afterwards.

After you add the plugin config, restart the OpenClaw Gateway so the plugin can register tools from the current MindAtlas catalog. The plugin still syncs its shipped skills into the active OpenClaw custom skills directory as an extra compatibility fallback, but `configure:skills` is the primary install-time path for making those skills visible to the official skills loader.

## Configuration

Configure the plugin under `plugins.entries.openclaw-mindatlas.config`.

```json
{
  "plugins": {
    "entries": {
      "openclaw-mindatlas": {
        "enabled": true,
        "config": {
          "baseUrl": "http://your-mindatlas-host",
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

- `baseUrl` should point to the MindAtlas backend origin or reverse-proxy origin that the OpenClaw host can actually reach. A URL ending in `/api` is also accepted.
- `integrationSecret` is generated from `MindAtlas > Settings > OpenClaw Integration`.
- `catalogRefreshTtlSec` controls how often the plugin refreshes the remote capability catalog.
- If you install the plugin before adding config, that is expected to work. The plugin will simply log that configuration is still missing and wait for `baseUrl` plus `integrationSecret`.

## MindAtlas Side Setup

1. Open MindAtlas `Settings > OpenClaw Integration`.
2. Generate or rotate the integration secret.
3. Enable the integration switch.
4. Review system items.
5. Add custom Tool / Workflow / Agent catalog items if needed.
6. Make sure the items you want OpenClaw to see are marked as exposed.

The capability catalog controls which tools OpenClaw can call.
The shipped skills control how OpenClaw should think about MindAtlas positioning, capture policy, retrieval routing, and summary routing.

## Runtime Behavior

- The plugin only registers capabilities that are `available = true`.
- `available = false` items are skipped and logged.
- After each successful refresh, the plugin logs a summary with discovered capability counts, available counts, and registered tool names.
- If a refresh succeeds but registers zero tools, the plugin logs whether the catalog was empty or all discovered capabilities were unavailable.
- On refresh, availability state and execute targets are updated in memory for unchanged tools.
- If the remote tool-name set changes because of add / delete / rename, the plugin marks `reloadRequired` and logs a warning.
- If an existing tool keeps the same `toolName` but its exported title, description, summaries, or input schema changes, the plugin also marks `reloadRequired`.
- Existing unchanged tools continue to use the latest capability mapping.
- Stale tools whose remote `toolName` disappeared start returning a clear “start a new session or reload the plugin” error.
- Stale tools whose exported metadata drifted under the same `toolName` also return a clear “start a new session or reload the plugin” error.
- The plugin does not auto-reload Gateway or auto-re-register renamed tools in v1.

## Development

Run checks from the plugin directory:

```bash
npm install --no-package-lock
npm run build
npm test
```

This package intentionally avoids runtime npm dependencies so it can be loaded directly from source by OpenClaw via `jiti`.

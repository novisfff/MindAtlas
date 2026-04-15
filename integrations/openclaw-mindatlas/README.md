# `openclaw-mindatlas`

`openclaw-mindatlas` is a native OpenClaw agent-tools plugin that discovers the MindAtlas capability catalog and exposes each catalog item as an OpenClaw tool.

## What It Does

- Reads `GET /api/integrations/openclaw/capabilities` from MindAtlas
- Registers one OpenClaw tool per exposed catalog item
- Registers two stable dispatcher tools: `mindatlas_list_capabilities` and `mindatlas_run_capability`
- Forwards tool execution to `POST /api/integrations/openclaw/capabilities/{capabilityKey}/execute`
- Bundles 5 shipped MindAtlas skills that help OpenClaw understand when and how to use the catalog
- Syncs those shipped skills into the active OpenClaw custom skills directory as a compatibility fallback when plugin-manifest skills are not surfaced by the current OpenClaw build
- Refreshes the remote catalog on a TTL
- Logs catalog refresh summaries and warns when catalog structure changes require a Gateway / plugin reload

## Shipped Skills

The plugin ships these 5 skills together:

- `mindatlas-overview`: high-level positioning for what MindAtlas is and when OpenClaw should prefer it
- `mindatlas-dispatcher`: dynamic capability discovery and fallback execution for newly exposed custom capabilities
- `mindatlas-auto-capture`: capture policy for durable memory and context submission
- `mindatlas-retrieval`: retrieval routing across search, detail lookup, and graph-style queries
- `mindatlas-summary`: summary routing for weekly, monthly, and topic-oriented reviews

These skills ship with the plugin package. They guide OpenClaw's decision-making across both the dedicated built-in `mindatlas_*` tools and any custom capabilities that administrators expose through the live MindAtlas catalog.

Current OpenClaw releases do not always surface plugin-manifest `skills` into `openclaw skills list`. To keep the shipped MindAtlas skills usable, the plugin also syncs them into the active custom skills directory on startup. Existing sessions still may need a new session or Gateway reload before the refreshed skill surface appears.

## Local Install

Recommended guided setup:

```bash
npm --prefix ./integrations/openclaw-mindatlas run setup:openclaw
```

This guided script runs on the OpenClaw host and:

- Prompts for `baseUrl`, `integrationSecret`, `requestTimeoutMs`, and `catalogRefreshTtlSec`
- Links the local `openclaw-mindatlas` plugin source into OpenClaw so local repo updates are reused directly
- Writes `plugins.entries.openclaw-mindatlas.config` into `openclaw.json`
- Preserves or restores `plugins.allow` for `openclaw-mindatlas` when plugin allowlist mode is active, and removes MindAtlas-only legacy `tools.allow` / empty `tools.profile` remnants
- Backs up same-named old MindAtlas custom skill folders under `~/.openclaw/skills-backup-<timestamp>/`
- Re-runs `configure:skills`
- Calls `openclaw gateway restart`

The guided host-management scripts intentionally live outside the plugin package directory, so `openclaw plugins install ./integrations/openclaw-mindatlas` only scans runtime plugin code instead of also scanning local maintenance helpers.

Open a brand-new OpenClaw session after the script completes so the refreshed skill and tool surface actually enters the prompt.

Manual fallback:

```bash
openclaw plugins install ./integrations/openclaw-mindatlas
npm --prefix ./integrations/openclaw-mindatlas run configure:skills
```

Development link mode fallback:

```bash
openclaw plugins install -l ./integrations/openclaw-mindatlas
npm --prefix ./integrations/openclaw-mindatlas run configure:skills
```

The lower-level `configure:skills` command only writes the installed plugin's `skills` directory into `skills.load.extraDirs`, which is the primary path for making the shipped MindAtlas skills visible to the official OpenClaw skills loader and to new sessions even on builds where plugin-manifest skills are not surfaced consistently.

If you use `configure:skills` by itself and it detects old MindAtlas-specific `tools.allow` or `tools.profile` compatibility remnants, it prints a warning but does not delete user config. The guided `setup:openclaw` and `update:openclaw` scripts handle that cleanup automatically.

You can install first and fill in `baseUrl` / `integrationSecret` afterwards.

After you add or update the plugin config, restart the OpenClaw Gateway so the plugin can register tools from the current MindAtlas catalog. The guided scripts already do this for you. The plugin still syncs its shipped skills into the active OpenClaw custom skills directory as an extra compatibility fallback, but `configure:skills` remains the primary install-time path for making those skills visible to the official skills loader. Start a fresh session after the restart so the updated skill and tool surface actually enters the prompt.

## Upgrade Or Reinstall

Use the same install path again when either of these is true:

- You upgraded the MindAtlas repository or deployed a newer MindAtlas system version
- You upgraded the `openclaw-mindatlas` plugin package
- You upgraded the shipped MindAtlas skills and want OpenClaw to pick up the refreshed guidance
- You changed exposed MindAtlas capability metadata and want a clean OpenClaw tool / skill surface before validating

Recommended guided update:

```bash
npm --prefix ./integrations/openclaw-mindatlas run update:openclaw
```

The guided update script:

- Reads the existing `plugins.entries.openclaw-mindatlas.config` first
- Reuses the existing config by default and only prompts for missing fields
- Temporarily repairs `plugins.allow` before uninstall when plugin allowlist mode is active
- Attempts `openclaw plugins uninstall openclaw-mindatlas --force`
- Removes the lingering copied install path if uninstall does not fully clean it up
- Clears stale MindAtlas plugin config remnants before reinstall so OpenClaw sees a clean install target
- Re-links the local plugin source path
- Rewrites the preserved plugin config
- Re-runs `configure:skills`
- Backs up conflicting same-named old MindAtlas custom skills
- Preserves or restores `plugins.allow` for `openclaw-mindatlas` when plugin allowlist mode is active, and removes MindAtlas-only legacy `tools.allow` / empty `tools.profile` remnants
- Calls `openclaw gateway restart`

After the script completes, open a brand-new OpenClaw session and verify from there.

Manual fallback for an already-installed tracked plugin:

```bash
# after pulling or deploying the upgraded MindAtlas version
openclaw plugins update openclaw-mindatlas
npm --prefix ./integrations/openclaw-mindatlas run configure:skills
```

Manual full reinstall fallback:

```bash
openclaw plugins uninstall openclaw-mindatlas --force
openclaw plugins install ./integrations/openclaw-mindatlas
npm --prefix ./integrations/openclaw-mindatlas run configure:skills
```

If logs show `plugin already exists`, use the guided `update:openclaw` path above or uninstall before reinstalling; a plain repeated `openclaw plugins install ...` will not overwrite the existing install path.

## Shipped Skill Upgrade Notes

The guided `setup:openclaw` and `update:openclaw` scripts already back up conflicting same-named MindAtlas custom skills before rerunning `configure:skills`.

If you are using the lower-level manual path instead, `configure:skills` can still be blocked by pre-existing custom skills with the same id under `~/.openclaw/skills/`.

If plugin logs say:

- `Skipping shipped MindAtlas skill because an existing custom skill with the same id is not plugin-managed`

then OpenClaw is preserving those user-owned custom skill directories instead of overwriting them. To let the shipped skills upgrade:

1. Back up or move the conflicting directories under `~/.openclaw/skills/`
2. Re-run `npm --prefix ./integrations/openclaw-mindatlas run configure:skills`
3. Restart the OpenClaw Gateway
4. Open a brand-new session

Example:

```bash
BACKUP_DIR=$HOME/.openclaw/skills-backup-$(date +%F-%H%M%S)
mkdir -p "$BACKUP_DIR"
for skill in mindatlas-overview mindatlas-auto-capture mindatlas-retrieval mindatlas-summary mindatlas-dispatcher; do
  [ -d "$HOME/.openclaw/skills/$skill" ] && mv "$HOME/.openclaw/skills/$skill" "$BACKUP_DIR"/
done
npm --prefix ./integrations/openclaw-mindatlas run configure:skills
```

Also review old compatibility remnants in `~/.openclaw/openclaw.json`. If `tools.allow` or `tools.profile` still contain removed names like `mindatlas_generate_weekly_report` or `mindatlas_generate_monthly_report`, remove those legacy entries and rely on the official plugin registration path instead. The guided setup and update scripts do this cleanup automatically for MindAtlas-owned remnants.

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
- The package now follows the official OpenClaw `definePluginEntry(...)` SDK path for `2026.4.1+`, so live MindAtlas tools should be exposed through `api.registerTool(...)` during plugin registration rather than through `tools.allow` or `tools.profile`.

## MindAtlas Side Setup

1. Open MindAtlas `Settings > OpenClaw Integration`.
2. Generate or rotate the integration secret.
3. Enable the integration switch.
4. Review system items.
5. Add custom Tool / Workflow / Agent catalog items if needed.
6. Make sure the items you want OpenClaw to see are marked as exposed.

The capability catalog controls which dedicated tools OpenClaw can call.
The shipped skills control how OpenClaw should think about MindAtlas positioning, capture policy, retrieval routing, summary routing, and dynamic fallback to custom exposed capabilities. The built-in guidance is not limited to system preset capabilities; administrators can expose custom workflows, agents, and tool-backed capabilities that the overview and dispatcher skills should also treat as first-class MindAtlas routes.

## Runtime Behavior

- The plugin only registers capabilities that are `available = true`.
- The plugin always registers `mindatlas_list_capabilities` plus `mindatlas_run_capability` when config is valid, even if no catalog-backed dedicated tools are available yet.
- `available = false` items are skipped and logged.
- After each successful refresh, the plugin logs a summary with discovered capability counts, available counts, and registered tool names.
- If a refresh succeeds but registers zero dedicated catalog tools, the plugin logs that only dispatcher tools are currently available.
- On refresh, availability state and execute targets are updated in memory for unchanged tools.
- `mindatlas_list_capabilities` and `mindatlas_run_capability` proactively refresh the catalog before they act, so newly exposed custom capabilities can be discovered and called without waiting for a dedicated tool to appear in the current session.
- If startup catalog registration fails, the plugin keeps Gateway startup alive but requires a Gateway reload after the config or network issue is fixed.
- If a previously unavailable capability becomes available later, the plugin logs that a Gateway reload is required instead of late-registering the new tool.
- If the remote tool-name set changes because of add / delete / rename, the plugin marks `reloadRequired` and logs a warning.
- If an existing tool keeps the same `toolName` but its exported title, description, summaries, or input schema changes, the plugin also marks `reloadRequired`.
- Existing unchanged tools continue to use the latest capability mapping.
- Stale tools whose remote `toolName` disappeared start returning a clear “start a new session or reload the plugin” error.
- Stale tools whose exported metadata drifted under the same `toolName` also return a clear “start a new session or reload the plugin” error.
- The plugin does not auto-reload Gateway or late-register newly discovered tools in v1.

## Development

Run checks from the plugin directory:

```bash
npm install --no-package-lock
npm run build
npm test
```

This package intentionally avoids runtime npm dependencies so it can be loaded directly from source by OpenClaw via `jiti`.

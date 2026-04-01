## Context

`openclaw-mindatlas` is intentionally dynamic: it discovers the live MindAtlas catalog and exposes only the currently available `mindatlas_*` tools. That flexibility is still the right model because administrators can change the capability surface through MindAtlas without rebuilding the plugin.

The weak point is not the dynamic contract itself. The weak points are:

- operators lack enough runtime logging to distinguish a healthy empty catalog from an auth/config/network failure
- OpenClaw plugin-manifest skills are currently not reliably carried into the skill registry, so bundled MindAtlas skills are present in the package but absent at runtime

## Decision

### Keep dynamic tools

Do not add a static fallback tool list in the plugin. The live MindAtlas catalog remains the only source of truth for exposed tools.

### Add a local compatibility layer for skills

The plugin will sync its 4 shipped skills into the active OpenClaw custom skills directory when the plugin service starts.

This fallback follows three rules:

- resolve the target from active OpenClaw config semantics first, preferring `OPENCLAW_CONFIG_PATH`, then `OPENCLAW_STATE_DIR`, then the default user config home
- write a plugin-owned marker into managed skill directories so repeated starts can update them safely
- never overwrite a same-named skill directory that does not already carry the plugin marker

This keeps shipped skills usable now while leaving room to retire the fallback later when OpenClaw fixes plugin-manifest `skills` loading upstream.

### Improve runtime observability

After each successful catalog refresh, the plugin will log:

- total discovered capabilities
- available vs unavailable counts
- currently registered tool names
- reload-required state

If the refresh succeeds but no tools are registered, it will emit a warning that distinguishes:

- empty catalog
- all capabilities unavailable

Structural or metadata drift will keep the existing reload-required behavior, but the warning and execution error text will explicitly say that active sessions do not hot-refresh and the operator should start a new session or reload the Gateway/plugin.

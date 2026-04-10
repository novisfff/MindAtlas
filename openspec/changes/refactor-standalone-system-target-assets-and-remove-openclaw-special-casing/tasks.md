## 1. Assistant-config
- [x] 1.1 Add a standalone system workflow target registry and sync path under assistant-config.
- [x] 1.2 Rename the context capture system workflow asset to `system_context_capture__workflow` and rename its preset files to `system_context_capture*.json`.
- [x] 1.3 Reuse the standalone registry for baseline resolution, display-name resolution, list/get/copy lifecycle, and callable workflow visibility.
- [x] 1.4 Add a system target audit helper and tests for expected system workflow/agent origins.

## 2. OpenClaw Integration
- [x] 2.1 Replace OpenClaw-owned workflow preset metadata with a shared standalone workflow asset binding for `submit_context_capture`.
- [x] 2.2 Keep `submit_context_capture` runtime schemas, tool name, capability key, and execution semantics unchanged.
- [x] 2.3 Update OpenClaw source-picker and bound-source responses to use assistant-config display names for workflow and agent sources.

## 3. Migration And Validation
- [x] 3.1 Add a migration that syncs standalone system targets and renames the legacy context capture workflow in place.
- [x] 3.2 Update backend tests for standalone sync, legacy rename, display names, OpenClaw binding, and source listing.
- [x] 3.3 Run targeted backend tests, `openspec validate refactor-standalone-system-target-assets-and-remove-openclaw-special-casing --strict --no-interactive`, and report outcomes.

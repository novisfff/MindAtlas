## Context
OpenClaw integration originally introduced a mixed model: custom catalog items bound to real tools, workflows, or agents, while shipped defaults lived behind a separate `system_adapter` branch and “system preset” terminology. Subsequent product iterations already moved default capture toward a workflow-backed item, which made the split feel increasingly artificial.

## Goals
- Make every OpenClaw catalog item bind to one real executable source: `tool`, `workflow`, or `agent`.
- Keep shipped defaults and user-created items in the same catalog model.
- Preserve existing OpenClaw-facing `capabilityKey` and `toolName` values for shipped defaults.
- Keep reset limited to restoring shipped system item defaults.

## Non-Goals
- Rename existing shipped OpenClaw capability keys or tool names.
- Change plugin discovery mechanics away from catalog-first runtime discovery.
- Rebuild or overwrite the underlying system tools, workflows, or agents when reset runs.

## Decisions

### 1. Shipped defaults become system-tagged catalog items
- `is_system_preset` becomes `is_system_item`.
- `system_capability_key` becomes `system_default_key`.
- The system tag only marks that a catalog item corresponds to a shipped default and can be restored by reset.
- System items and custom items otherwise share the same edit, delete, and rebinding behavior.

### 2. A single shipped system item registry replaces layered preset registries
- Keep one registry of shipped OpenClaw system item definitions.
- Each definition declares the default binding source, OpenClaw-facing copy, schemas, summaries, enabled state, and stable `system_default_key`.
- `submit_context_capture` stays workflow-backed.
- The remaining shipped defaults are exposed through thin wrapper system tools that reuse existing business services.

### 3. Runtime dispatch only follows real source types
- Remove the private `system_adapter` execution branch.
- Runtime execution now dispatches through the same source model used by ordinary catalog items:
  - `tool`
  - `workflow`
  - `agent`
- Wrapper tools preserve the current OpenClaw contracts so external callers do not need a migration.

### 4. Reset restores exposure defaults, not source objects
- Reset reconciles the shipped system item catalog entries by `system_default_key`.
- Missing shipped items are recreated.
- Existing shipped items are restored to the shipped binding, copy, schemas, tool name, and enabled state.
- User-created items are untouched.
- Underlying system tools, workflows, and agents are not recreated or overwritten by reset.

## Risks / Trade-offs
- Wrapper tools add a small translation layer for shipped defaults.
  - Mitigation: keep them thin and route directly into existing services.
- Existing deployments may have customized shipped items.
  - Mitigation: normal seeding preserves customized rows; only explicit reset reapplies shipped defaults.

## Migration Plan
1. Rename the catalog’s system fields to `is_system_item` and `system_default_key`.
2. Convert legacy `system_adapter` rows into real tool or workflow bindings.
3. Seed shipped defaults from the new unified registry without overwriting admin customizations.
4. Remove adapter-only runtime dispatch and keep plugin/runtime contracts stable.
5. Update settings copy, docs, and tests to use “system item” terminology.

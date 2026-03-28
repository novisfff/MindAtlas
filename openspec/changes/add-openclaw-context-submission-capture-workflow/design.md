## Context
OpenClaw now consumes a dynamic capability catalog from MindAtlas. That solved the fixed-registry problem, but the default recording entry still conceptually came from a field-level system adapter. For Phase 1, the product direction is different: OpenClaw should hand over thin context, and MindAtlas should decide how to materialize a durable record.

## Goals
- Make the default OpenClaw recording preset workflow-backed.
- Preserve the existing runtime metadata and execute routes.
- Keep the legacy `capture_entry` adapter callable for compatibility.
- Avoid a large OpenClaw settings page redesign in this step.

## Non-Goals
- Remove `capture_entry` from the backend entirely.
- Introduce deterministic automatic capture hooks.
- Add multi-user OpenClaw identity mapping.
- Generalize every system preset source type beyond what this step needs.

## Decisions

### 1. Add A Canonical Standalone System Workflow Asset
- The new OpenClaw recording workflow is a system workflow asset with a fixed canonical name.
- It is not bound to an `AssistantSkill`, because the current skill binding flow rejects structured-input workflows.
- The asset is loaded from a JSON workflow preset and published automatically when the OpenClaw system presets are ensured or reset.

### 2. Keep System Preset Keys, But Allow Workflow-Backed Presets
- `openclaw_capability_item.system_capability_key` is reused as the stable key for all system presets, not only `system_adapter` presets.
- `source_type='workflow'` plus `is_system_preset=true` is now valid when the row is bound to a canonical system workflow asset.
- Existing `system_adapter` presets remain unchanged for search, relation, graph, and report capabilities.

### 3. Default Recording Input Uses Thin Context
- The new default capture preset accepts high-level fields:
  - `intent`
  - `context`
  - `source`
  - `session`
  - `channel`
  - `taskHint`
  - `timeHint`
  - `tagHints`
- The internal workflow turns those clues into final entry fields and persists the entry through existing MindAtlas tooling.

### 4. Legacy `capture_entry` Remains Compatible But Is No Longer Default
- The `capture_entry` runtime adapter and schemas stay in place.
- New systems seed `capture_entry` as a disabled system preset.
- Existing systems are migrated once so the old preset is downgraded to disabled, while the new workflow-backed preset is added and enabled.
- `reset-system-presets` restores the new workflow-backed preset as enabled and the legacy field-level preset as disabled.

## Risks / Trade-offs
- The new workflow asset is system-managed, so ensuring presets may realign the workflow contract if it drifts.
  - Mitigation: only republish when the stored published graph differs from the preset.
- Structured start input now supports array fields because `tagHints` needs it.
  - Mitigation: the change is limited to workflow validation/runtime handling for start structured fields.

## Migration Plan
1. Add workflow-backed system preset support to the catalog constraint and service layer.
2. Add the canonical OpenClaw context-capture workflow preset JSON.
3. Ensure or recreate the workflow asset when OpenClaw system presets are synchronized.
4. Seed the new workflow-backed preset and disable the legacy `capture_entry` preset by default.
5. Keep runtime execution unchanged so the plugin continues to rely on catalog-first discovery.

## Context
MindAtlas currently exposes both canonical tool names and OpenClaw-specific wrapper names through the same system-tool registry. That makes ordinary assistant tool management show adapter-only tools, while OpenClaw system items still bind to legacy wrapper names even though their public `mindatlas_*` capability names are already a separate contract layer.

## Goals
- Establish one canonical system tool surface for normal assistant usage.
- Keep legacy `openclaw_*` names executable for backward compatibility.
- Move OpenClaw request/response contract differences into one adapter map owned by the integration runtime.
- Auto-migrate persisted OpenClaw source bindings to canonical tool names.

## Non-Goals
- Renaming OpenClaw-facing `mindatlas_*` capability names.
- Merging or deleting active system workflows and system skills.
- Redesigning the OpenClaw catalog data model.

## Decisions

### Canonical Versus Hidden Tool Names
- Canonical tools stay in assistant tool discovery and in `/api/assistant-config/system-tools/definitions`.
- Hidden compatibility aliases remain importable and executable through tool resolution, but they are omitted from normal system-tool listing APIs.
- Validation paths for workflows and agents must treat hidden aliases as resolvable system tools, otherwise existing persisted definitions would fail save or publish checks.

### OpenClaw Adapter Ownership
- OpenClaw system items bind to canonical tool names.
- The OpenClaw integration runtime owns a source-tool adapter map that can recognize both canonical and legacy alias names.
- Each adapter normalizes OpenClaw payloads into canonical tool inputs and reshapes canonical tool results back into the existing OpenClaw output contract.
- Existing one-off logic such as the `openclaw_get_entry` top-level `id` fallback is folded into this adapter map.

### Migration Strategy
- System item sync updates shipped items to the new canonical `source_tool_name` values automatically.
- The same sync path also upgrades custom OpenClaw catalog items whose `source_tool_name` still references a legacy `openclaw_*` alias.
- `openclaw_capture_entry` remains a retired source: it is not migrated into an active canonical tool binding and it stays hidden from runtime discovery and new bindings.

## Risks And Mitigations
- Risk: removing wrapper names from visible tool lists breaks old workflows and agents during validation.
  - Mitigation: validation checks resolve against runtime-available system tool names, not only the visible tool list.
- Risk: OpenClaw runtime contracts drift when moving from wrappers to canonical tools.
  - Mitigation: keep schema snapshots unchanged and centralize payload/result transforms in adapter tests.

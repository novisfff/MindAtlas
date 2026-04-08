## Context
OpenClaw is the chat entrypoint and agent orchestrator. MindAtlas remains the capability backend and system of record. The original integration prototype proved the runtime boundary and authentication model, but its fixed capability registry became a product limitation as soon as we wanted to expose user-created executables.

## Goals
- Preserve the dedicated OpenClaw runtime facade and app-level bearer authentication.
- Replace the fixed registry with a persisted catalog that administrators can manage from settings.
- Support three exportable source types in v1: Tool, Workflow, Agent.
- Keep system presets available by default without preventing admins from creating custom catalog items.
- Make runtime metadata agent-friendly and stable even when underlying MindAtlas objects evolve.

## Non-Goals
- Expose MindAtlas skills directly to OpenClaw.
- Let OpenClaw execute arbitrary workflows, agents, or tools without a catalog item.
- Introduce per-user OpenClaw credentials or per-channel identity mapping.

## Decisions

### 1. Capability Catalog Is A First-Class Persisted Model
- Add `openclaw_capability_item` as the authoritative catalog table.
- Each row owns its OpenClaw-facing identity:
  - `capability_key`
  - `tool_name`
  - `title`
  - `description`
  - `enabled`
- Each row also binds to exactly one source:
  - `system_adapter`
  - `tool`
  - `workflow`
  - `agent`

### 2. System Presets Stay In Code, But Exposure Lives In The Catalog
- Keep system preset capability definitions in code so their behavior, schemas, and localization remain versioned.
- On first access, seed system preset catalog items from those definitions.
- Mark preset rows as `is_system_preset = true`.
- Presets are non-deletable, but admins can disable them and edit outward-facing title/description/tool name.
- `reset-system-presets` restores them to the current localized default state.

### 3. Source-Type Rules
- `tool`
  - Can bind to system tools or user-created remote tools.
  - OpenClaw-facing input/output schema lives on the catalog item.
  - `tool_response_mode` supports `json_schema` and `text_field`.
- `workflow`
  - Must be enabled and have a published version.
  - Published start input must be structured.
  - Published output contract must resolve to a single structured schema.
  - Catalog item schema is derived from the published workflow and kept read-only in the UI.
- `agent`
  - Must be enabled and have a published version.
  - Because the current agent profile model does not persist a native structured I/O contract, the catalog item owns the OpenClaw-facing structured schema while runtime still validates the agent’s published availability.
  - Runtime wraps the published agent prompt with a contract-enforcement layer and validates the JSON response against the catalog schema.

### 4. Runtime Availability Is Computed, Not Persisted
- `GET /api/integrations/openclaw/capabilities` returns only `enabled=true` items.
- Each runtime item includes:
  - `available`
  - `availabilityReason`
- Availability is recomputed from current source state:
  - system adapter readiness
  - tool existence/enabled state
  - workflow published contract drift
  - agent published availability

### 5. Migration Strategy
- Keep using `app_setting.openclaw_integration_config` for:
  - global enable flag
  - encrypted secret
- Migrate old `capabilities` enable flags from that payload into matching system preset rows on first catalog sync.
- Mark the migration as complete in the same setting payload to avoid reapplying legacy flags on later reads.

## Risks / Trade-offs
- Agent contracts are catalog-owned in v1 rather than natively persisted on the agent profile model.
  - Mitigation: keep the contract explicit, structured, and runtime-validated; revisit native agent contracts later.
- Workflow contract drift can make a catalog item unavailable.
  - Mitigation: surface `available=false` with a clear reason and allow admins to resave the item to resync.

## Migration Plan
1. Add the `openclaw_capability_item` table.
2. Seed system presets on first settings/runtime access.
3. Migrate old fixed enable flags into preset rows.
4. Switch runtime metadata and execute paths from fixed registry lookup to catalog lookup.
5. Replace the settings UI with catalog management.

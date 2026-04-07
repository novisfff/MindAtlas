## MODIFIED Requirements

### Requirement: OpenClaw Capability Execution SHALL Reuse MindAtlas Business Services
Each exposed catalog item SHALL route into existing MindAtlas services or runners instead of duplicating business logic.

#### Scenario: Shipped OpenClaw tool capabilities bind to canonical tool names
- **WHEN** shipped OpenClaw system items are seeded or resynced
- **THEN** entry, relation, knowledge-graph, and report capabilities SHALL bind to canonical MindAtlas tool names
- **AND** the public OpenClaw-facing `mindatlas_*` capability names SHALL remain unchanged

#### Scenario: OpenClaw runtime adapts contracts on top of canonical tools
- **WHEN** OpenClaw executes a tool-backed capability whose stored contract uses OpenClaw camelCase payloads and response schemas
- **THEN** the runtime SHALL translate that payload into the bound canonical tool input contract
- **AND** it SHALL translate the canonical tool result back into the stored OpenClaw output contract before validation

#### Scenario: Legacy OpenClaw source bindings are migrated automatically
- **WHEN** an existing OpenClaw catalog item still stores a legacy `openclaw_*` `source_tool_name`
- **THEN** system sync or catalog loading SHALL migrate that binding to the canonical tool name
- **AND** it SHALL preserve the capability key, exposed tool name, and stored OpenClaw schema metadata

#### Scenario: Retired capture source stays retired
- **WHEN** a catalog item is still bound to `openclaw_capture_entry`
- **THEN** the system SHALL keep that source in a retired state
- **AND** it SHALL not reintroduce it into visible system tool discovery or active OpenClaw runtime exposure

## MODIFIED Requirements

### Requirement: Capability Catalog Items SHALL Bind To Supported MindAtlas Sources
The system SHALL allow capability catalog items to bind to `system_adapter`, `tool`, `workflow`, or `agent` sources, with source-specific contract and availability rules.

#### Scenario: System presets can be workflow-backed
- **WHEN** MindAtlas seeds or resets OpenClaw system presets
- **THEN** a system preset SHALL be allowed to bind to a canonical system workflow asset
- **AND** that workflow-backed preset SHALL remain marked as `is_system_preset = true`

#### Scenario: Workflow source requires a published structured contract
- **WHEN** an administrator tries to create or update a workflow-backed catalog item
- **THEN** the system SHALL require the selected workflow to be enabled, published, and to expose a valid structured input and output contract
- **AND** the catalog item SHALL snapshot that structured contract for OpenClaw-facing metadata

#### Scenario: Tool source owns its OpenClaw-facing contract
- **WHEN** an administrator creates or updates a tool-backed catalog item
- **THEN** the catalog item SHALL store its own OpenClaw-facing input schema, output schema, summaries, and response mode
- **AND** runtime tool execution SHALL validate the request and response against that stored contract

#### Scenario: Agent source requires published availability and structured catalog schemas
- **WHEN** an administrator creates or updates an agent-backed catalog item
- **THEN** the system SHALL require the selected agent profile to be enabled and published
- **AND** the catalog item SHALL carry the OpenClaw-facing structured input and output contract used for runtime validation

### Requirement: OpenClaw Default Recording SHALL Use Thin Context Submission
The default MindAtlas recording preset exposed to OpenClaw SHALL be a workflow-backed system preset that accepts thin context rather than full entry fields.

#### Scenario: Fresh systems seed the workflow-backed recording preset
- **WHEN** a fresh system first reads OpenClaw integration settings or runtime metadata
- **THEN** MindAtlas SHALL create or recover a canonical system capture workflow asset
- **AND** it SHALL expose an enabled workflow-backed system preset for thin context submission

#### Scenario: Legacy field-level capture preset is downgraded
- **WHEN** an existing system upgrades to the thin-context capture workflow version
- **THEN** the legacy `capture_entry` system preset SHALL remain present only for compatibility
- **AND** that preset SHALL be disabled by default

#### Scenario: Thin-context capture executes through the canonical workflow
- **WHEN** OpenClaw executes the default recording preset with thin context fields
- **THEN** MindAtlas SHALL run the canonical published workflow
- **AND** the workflow SHALL materialize the final entry fields internally before creating the record

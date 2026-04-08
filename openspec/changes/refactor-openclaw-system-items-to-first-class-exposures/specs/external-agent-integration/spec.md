## MODIFIED Requirements

### Requirement: Capability Catalog Items SHALL Bind To Supported MindAtlas Sources
The system SHALL allow capability catalog items to bind to `tool`, `workflow`, or `agent` sources, with source-specific contract and availability rules.

#### Scenario: Shipped system items are seeded automatically
- **WHEN** a new system first accesses OpenClaw integration settings or runtime metadata
- **THEN** the system SHALL auto-seed a default set of shipped system items
- **AND** those shipped items SHALL be marked as `is_system_item = true`
- **AND** each shipped item SHALL bind to a real `tool`, `workflow`, or `agent` source

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

### Requirement: Shipped OpenClaw System Items SHALL Behave As First-Class Catalog Items
The system SHALL treat shipped OpenClaw defaults as first-class catalog items rather than a private adapter layer.

#### Scenario: System item edits use the same flow as custom items
- **WHEN** an administrator edits a shipped system item
- **THEN** the system SHALL allow updating its title, description, tool name, source binding, enabled state, and any source-type-editable OpenClaw contract fields
- **AND** the remaining restrictions SHALL come only from the bound source type rules

#### Scenario: System item delete is allowed and reset restores it
- **WHEN** an administrator deletes a shipped system item
- **THEN** the delete SHALL succeed like any other catalog item
- **AND** a later system-item reset SHALL recreate the shipped item from its `system_default_key`

#### Scenario: Reset restores shipped defaults without touching custom items
- **WHEN** an administrator triggers system item reset
- **THEN** the system SHALL restore missing or modified shipped system items to the current localized default definitions
- **AND** existing custom catalog items SHALL remain untouched

#### Scenario: Legacy fixed capability flags are migrated once
- **WHEN** an older system still stores fixed capability enable flags in the legacy integration payload
- **THEN** the system SHALL migrate those flags into the matching seeded shipped system items
- **AND** the migration SHALL only be applied once

### Requirement: OpenClaw Capability Execution SHALL Reuse MindAtlas Business Services
Each exposed catalog item SHALL route into existing MindAtlas services or runners instead of duplicating business logic.

#### Scenario: Shipped entry and graph capabilities run through real tool or workflow bindings
- **WHEN** OpenClaw executes a shipped system item such as `submit_context_capture`, `search_entries`, `get_entry`, `create_relation`, `query_knowledge_graph`, or the report capabilities
- **THEN** MindAtlas SHALL dispatch through the catalog item’s bound `tool` or `workflow`
- **AND** the bound executable SHALL reuse the existing MindAtlas business services internally

#### Scenario: Tool catalog item execution
- **WHEN** OpenClaw executes a tool-backed catalog item
- **THEN** MindAtlas SHALL resolve the bound Assistant Tool runtime and execute it through the existing tool execution path

#### Scenario: Workflow catalog item execution
- **WHEN** OpenClaw executes a workflow-backed catalog item
- **THEN** MindAtlas SHALL execute the current published workflow version through the existing workflow engine

#### Scenario: Agent catalog item execution
- **WHEN** OpenClaw executes an agent-backed catalog item
- **THEN** MindAtlas SHALL execute the current published agent through the existing agent runtime
- **AND** it SHALL wrap and validate the result against the catalog item’s structured output contract

### Requirement: OpenClaw Integration SHALL Be Configurable From A Dedicated Settings Page
The application SHALL provide a dedicated settings page for OpenClaw integration management.

#### Scenario: Settings home entry
- **WHEN** the user opens the Settings home page
- **THEN** the page SHALL include an `OpenClaw Integration` entry

#### Scenario: Configure capability exposure
- **WHEN** the user opens the OpenClaw integration settings page
- **THEN** the page SHALL allow enabling or disabling the integration
- **AND** it SHALL allow generating or rotating the integration secret
- **AND** it SHALL display system items and custom catalog items in one catalog list with clear badges
- **AND** it SHALL allow creating catalog items from Tool, Workflow, or Agent sources
- **AND** it SHALL allow editing, enabling, disabling, deleting, and rebinding both shipped system items and custom catalog items
- **AND** it SHALL allow resetting shipped system items back to defaults

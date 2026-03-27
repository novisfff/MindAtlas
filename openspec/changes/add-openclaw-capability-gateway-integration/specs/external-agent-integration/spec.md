## ADDED Requirements

### Requirement: MindAtlas SHALL Expose A Dedicated OpenClaw Integration Facade
The system SHALL provide a dedicated integration-facing API surface for OpenClaw instead of reusing UI-oriented REST endpoints directly.

#### Scenario: Admin reads integration settings
- **WHEN** an administrator requests `/api/system-settings/openclaw-integration`
- **THEN** the system SHALL return the current integration enabled state, whether a secret is configured, and the full configurable capability catalog

#### Scenario: OpenClaw reads runtime metadata
- **WHEN** OpenClaw requests `/api/integrations/openclaw/capabilities`
- **THEN** the system SHALL return only capability metadata for capabilities that are currently exposed
- **AND** the response SHALL be shaped for agent tool registration rather than frontend pagination UI

### Requirement: OpenClaw Runtime Calls SHALL Use App-Level Bearer Authentication
The OpenClaw runtime facade SHALL require a single app-level bearer secret configured from system settings.

#### Scenario: Missing or invalid bearer secret
- **WHEN** a caller accesses an OpenClaw runtime endpoint without the configured bearer secret
- **THEN** the request SHALL be rejected with an explicit unauthorized error

#### Scenario: Integration disabled
- **WHEN** OpenClaw accesses a runtime endpoint while the integration is disabled
- **THEN** the system SHALL reject the request even if the secret is otherwise valid

### Requirement: OpenClaw Exposure SHALL Use A Configurable Capability Catalog
The system SHALL expose OpenClaw tools through a persisted capability catalog of independent catalog items rather than a hard-coded fixed registry.

#### Scenario: Settings response returns catalog items
- **WHEN** an administrator requests `/api/system-settings/openclaw-integration`
- **THEN** the system SHALL return both integration state and the full capability catalog item list
- **AND** each item SHALL expose its OpenClaw-facing identity, source binding, enabled state, availability state, summaries, and schemas

#### Scenario: Disabled catalog item is hidden from runtime metadata
- **WHEN** a catalog item is disabled in OpenClaw integration settings
- **THEN** it SHALL NOT appear in runtime capability metadata
- **AND** direct execution attempts for that capability key SHALL return a disabled or not-exposed error

#### Scenario: Arbitrary passthrough outside the catalog is blocked
- **WHEN** a caller attempts to execute a workflow, skill, agent, or tool that is not represented by a catalog item
- **THEN** the system SHALL reject the request

### Requirement: Capability Catalog Items SHALL Bind To Supported MindAtlas Sources
The system SHALL allow capability catalog items to bind to `system_adapter`, `tool`, `workflow`, or `agent` sources, with source-specific contract and availability rules.

#### Scenario: System presets are seeded automatically
- **WHEN** a new system first accesses OpenClaw integration settings or runtime metadata
- **THEN** the system SHALL auto-seed a default set of system preset catalog items
- **AND** those preset items SHALL be marked as system presets

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

### Requirement: System Preset Catalog Items SHALL Be Non-Deletable But Resettable
The system SHALL treat built-in preset catalog items as resettable defaults instead of ordinary user-created items.

#### Scenario: System preset delete is rejected
- **WHEN** an administrator attempts to delete a system preset catalog item
- **THEN** the system SHALL reject the request with a clear error

#### Scenario: Reset restores current preset defaults
- **WHEN** an administrator triggers system preset reset
- **THEN** the system SHALL restore missing or modified preset items to the current localized default definitions
- **AND** existing user-created catalog items SHALL remain untouched

#### Scenario: Legacy fixed capability flags are migrated
- **WHEN** an older system still stores fixed capability enable flags in the legacy integration payload
- **THEN** the system SHALL migrate those flags into the matching seeded system preset catalog items
- **AND** the migration SHALL only be applied once

### Requirement: OpenClaw Capability Execution SHALL Reuse MindAtlas Business Services
Each exposed catalog item SHALL route into existing MindAtlas services or runners instead of duplicating business logic.

#### Scenario: Weekly report system preset execution
- **WHEN** OpenClaw executes the catalog item whose runtime capability key resolves to `generate_weekly_report`
- **THEN** MindAtlas SHALL execute the existing weekly report service flow
- **AND** that report generation SHALL continue to use the system AI behavior runner internally

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
- **AND** it SHALL display system preset and custom catalog items separately
- **AND** it SHALL allow creating catalog items from Tool, Workflow, or Agent sources
- **AND** it SHALL allow editing, enabling, disabling, and deleting user-created catalog items
- **AND** it SHALL allow disabling and resetting system preset catalog items

### Requirement: OpenClaw Integration Metadata SHALL Teach The External Agent What MindAtlas Is
The repository SHALL define documentation for the external OpenClaw plugin contract and a `MindAtlas Overview` skill that explains MindAtlas positioning and tool usage guidance.

#### Scenario: Plugin contract documentation
- **WHEN** developers implement the external `openclaw-mindatlas` plugin
- **THEN** the repository SHALL provide implementation documentation covering auth, metadata discovery, tool naming, execution headers, and expected error semantics

#### Scenario: Overview skill guidance
- **WHEN** the external agent loads the `MindAtlas Overview` skill
- **THEN** the skill SHALL explain that MindAtlas is a system for recording, connecting, retrieving, and analyzing personal knowledge and experiences
- **AND** it SHALL guide the agent to discover the currently exposed capability catalog first
- **AND** it SHALL teach the agent to use MindAtlas capture, organization, retrieval, relation, report, or administrator-curated custom capabilities when appropriate

### Requirement: OpenClaw Runtime Execution SHALL Emit Basic Audit Context
Each OpenClaw capability execution SHALL record basic non-sensitive execution context for observability.

#### Scenario: Capability execution logging
- **WHEN** OpenClaw executes any exposed capability
- **THEN** the system SHALL log the capability key, request identifier, source, channel, session, status, and duration
- **AND** it SHALL NOT write raw secrets or normal payload content to standard info logs

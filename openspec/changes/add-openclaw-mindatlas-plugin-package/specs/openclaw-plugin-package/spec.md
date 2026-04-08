## ADDED Requirements

### Requirement: The Repository SHALL Ship A Real OpenClaw MindAtlas Plugin Package
The repository SHALL include an installable `openclaw-mindatlas` plugin package instead of only documenting the intended integration contract.

#### Scenario: Local plugin install
- **WHEN** an operator installs the plugin from the repository path
- **THEN** OpenClaw SHALL be able to discover the package as `openclaw-mindatlas`
- **AND** the package SHALL include plugin manifest metadata, runtime entrypoint, and bundled skills

### Requirement: The Plugin SHALL Discover The MindAtlas Capability Catalog Dynamically
The plugin SHALL fetch the MindAtlas runtime catalog and register OpenClaw tools from the returned catalog items rather than from a hard-coded list.

#### Scenario: Register only currently available capabilities
- **WHEN** the plugin loads a MindAtlas catalog containing both available and unavailable items
- **THEN** it SHALL register tools only for `available=true` items
- **AND** it SHALL skip unavailable items without creating placeholder tools

#### Scenario: Startup catalog fetch fails
- **WHEN** the first catalog fetch fails during plugin startup
- **THEN** the plugin SHALL not block Gateway startup
- **AND** it SHALL register no MindAtlas tools until a later refresh succeeds

### Requirement: The Plugin SHALL Forward Tool Execution To MindAtlas Runtime APIs
Each registered OpenClaw tool SHALL execute by calling the MindAtlas runtime execute endpoint for the bound `capabilityKey`.

#### Scenario: Successful capability execution
- **WHEN** a registered OpenClaw tool is invoked
- **THEN** the plugin SHALL send the tool argument object to `/api/integrations/openclaw/capabilities/{capabilityKey}/execute`
- **AND** it SHALL return the MindAtlas `result` payload back to OpenClaw

#### Scenario: Stable error mapping
- **WHEN** MindAtlas returns integration auth, disabled, hidden, or missing-capability errors
- **THEN** the plugin SHALL map those failures into short, user-readable tool errors

### Requirement: The Plugin SHALL Refresh Catalog State On A TTL
The plugin SHALL refresh the remote MindAtlas catalog on a configurable TTL.

#### Scenario: Non-structural change
- **WHEN** the refreshed catalog changes metadata or availability without changing the tool-name set
- **THEN** the plugin SHALL update its in-memory catalog state
- **AND** it SHALL continue using the existing registered tool set

#### Scenario: Structural drift requires reload
- **WHEN** the refreshed catalog changes the tool-name set by add, delete, or rename
- **THEN** the plugin SHALL mark that a reload is required
- **AND** it SHALL log a clear warning instead of attempting automatic Gateway reload

### Requirement: The Plugin SHALL Ship The MindAtlas Overview Skill
The plugin package SHALL bundle a `MindAtlas Overview` skill so OpenClaw agents know what MindAtlas is for and when to use it.

#### Scenario: Skill asset present
- **WHEN** the plugin is installed
- **THEN** OpenClaw SHALL be able to load the bundled `MindAtlas Overview` skill from the plugin package

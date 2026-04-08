## MODIFIED Requirements

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

#### Scenario: Successful refresh with no registered tools
- **WHEN** a catalog refresh succeeds but yields no registered MindAtlas tools
- **THEN** the plugin SHALL emit an explicit warning
- **AND** it SHALL distinguish between an empty catalog and a catalog where all discovered capabilities are currently unavailable

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
- **AND** the warning SHALL explain that active OpenClaw sessions do not hot-refresh and the operator should start a new session or reload the Gateway/plugin

### Requirement: The Plugin SHALL Ship The MindAtlas Overview Skill
The plugin package SHALL bundle a `MindAtlas Overview` skill so OpenClaw agents know what MindAtlas is for and when to use it.

#### Scenario: Skill asset present
- **WHEN** the plugin is installed
- **THEN** the package SHALL include the bundled `MindAtlas Overview` skill source

#### Scenario: Current OpenClaw build does not surface plugin-manifest skills
- **WHEN** the plugin service starts on an OpenClaw build that does not surface manifest-declared plugin skills
- **THEN** the plugin SHALL sync its 4 shipped MindAtlas skills into the active OpenClaw custom skills directory
- **AND** repeated starts SHALL remain idempotent
- **AND** same-named skill directories that are not already plugin-managed SHALL NOT be overwritten

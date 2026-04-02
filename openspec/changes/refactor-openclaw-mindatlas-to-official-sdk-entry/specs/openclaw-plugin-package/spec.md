## MODIFIED Requirements

### Requirement: The Plugin SHALL Discover The MindAtlas Capability Catalog Dynamically
The plugin SHALL fetch the MindAtlas runtime catalog and register OpenClaw tools from the returned catalog items rather than from a hard-coded list, and it SHALL do that registration from the official OpenClaw SDK plugin entry `register(api)` phase.

#### Scenario: Register available tools during plugin entry registration
- **WHEN** OpenClaw loads `openclaw-mindatlas` through the official SDK entry on OpenClaw `2026.4.1+`
- **THEN** the plugin SHALL fetch the MindAtlas catalog during `register(api)`
- **AND** it SHALL call `api.registerTool(...)` for each `available=true` capability before background services start
- **AND** fresh sessions SHALL rely on that entry-time registration instead of a later service-start injection path

#### Scenario: Startup catalog fetch fails
- **WHEN** the first catalog fetch fails during plugin startup
- **THEN** the plugin SHALL not block Gateway startup
- **AND** it SHALL register no MindAtlas tools for that Gateway process
- **AND** it SHALL log that operators must fix the failure and reload the Gateway before fresh sessions can see MindAtlas tools

### Requirement: The Plugin SHALL Refresh Catalog State On A TTL
The plugin SHALL refresh the remote MindAtlas catalog on a configurable TTL without mutating the registered tool set after startup.

#### Scenario: Post-start newly available tool requires reload
- **WHEN** a later refresh finds a MindAtlas capability that is now `available=true` but was not registered during startup
- **THEN** the plugin SHALL not late-register that tool
- **AND** it SHALL mark that a Gateway reload is required
- **AND** it SHALL log clear guidance that a new session plus Gateway reload is needed to expose the updated tool surface

#### Scenario: Structural drift requires reload
- **WHEN** the refreshed catalog changes the tool-name set by add, delete, or rename, or changes registration metadata for an already registered tool
- **THEN** the plugin SHALL mark that a reload is required
- **AND** it SHALL log a clear warning instead of attempting automatic Gateway reload or hot registry mutation

### Requirement: The Plugin SHALL Ship A Real OpenClaw MindAtlas Plugin Package
The repository SHALL include an installable `openclaw-mindatlas` plugin package that follows the official OpenClaw SDK entry contract and package metadata requirements for the supported Gateway range.

#### Scenario: Package metadata declares the official SDK contract
- **WHEN** an operator or tool inspects the `openclaw-mindatlas` package metadata
- **THEN** it SHALL declare the plugin entry path under `openclaw.extensions`
- **AND** it SHALL declare the supported `pluginApi`, `minGatewayVersion`, `openclawVersion`, and `pluginSdkVersion` values for the official SDK path

### Requirement: The Plugin SHALL Ship The MindAtlas Overview Skill
The plugin package SHALL bundle a `MindAtlas Overview` skill so OpenClaw agents know what MindAtlas is for and when to use it.

#### Scenario: Skill compatibility setup only manages skill visibility
- **WHEN** an operator runs the plugin's `configure:skills` helper
- **THEN** it SHALL write `skills.load.extraDirs` for the shipped MindAtlas skills
- **AND** it SHALL not write `tools.profile` or `tools.allow` as the MindAtlas tool exposure mechanism
- **AND** if legacy MindAtlas-specific tool-policy remnants are detected, it SHALL warn without deleting user config

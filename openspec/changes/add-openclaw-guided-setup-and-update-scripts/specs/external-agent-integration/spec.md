## ADDED Requirements
### Requirement: OpenClaw MindAtlas Operator Scripts SHALL Provide Guided Setup And Update Flows
The OpenClaw MindAtlas integration SHALL provide operator-facing setup and update scripts that collect or reuse plugin config, clean MindAtlas-specific legacy remnants, sync shipped skills, and restart the Gateway.

#### Scenario: Guided first-time setup
- **WHEN** an operator runs the guided setup flow for `openclaw-mindatlas`
- **THEN** the script SHALL prompt for `baseUrl`, `integrationSecret`, `requestTimeoutMs`, and `catalogRefreshTtlSec`
- **AND** it SHALL install the local plugin package, write the plugin config into `openclaw.json`, run the shipped-skill sync path, clean MindAtlas-only legacy config remnants, restart the OpenClaw Gateway, and remind the operator to validate from a new session

#### Scenario: Guided update reuses existing config
- **WHEN** an operator runs the guided update flow for an existing `openclaw-mindatlas` installation
- **THEN** the script SHALL reuse the existing plugin config by default and only prompt for missing fields
- **AND** it SHALL preserve the plugin config across uninstall or reinstall work
- **AND** it SHALL remove the lingering install path if uninstall does not cleanly remove it before reinstalling

#### Scenario: Conflicting custom skills are backed up before sync
- **WHEN** the active OpenClaw custom skills directory already contains same-named MindAtlas skill folders
- **THEN** the guided setup or update flow SHALL move those directories into a timestamped backup location before rerunning shipped-skill sync

### Requirement: OpenClaw MindAtlas Guidance SHALL Prefer Guided Scripts Over Manual Install Steps
The OpenClaw MindAtlas operator guidance surface SHALL promote the guided setup and update scripts as the primary path while keeping detailed manual steps available as a fallback.

#### Scenario: Settings page shows script-first guidance
- **WHEN** an administrator opens the MindAtlas OpenClaw integration settings page
- **THEN** the install step SHALL present guided setup and update script commands as the primary actions
- **AND** detailed manual OpenClaw install and config steps SHALL remain available in an expandable fallback section

#### Scenario: README and shipped skills describe upgrade recovery with guided scripts
- **WHEN** operators or OpenClaw sessions consult the shipped documentation and skills
- **THEN** the README and shipped MindAtlas skills SHALL recommend the guided setup or update scripts for plugin or shipped-skill refreshes
- **AND** they SHALL continue to tell operators to validate from a brand-new OpenClaw session after the Gateway restarts

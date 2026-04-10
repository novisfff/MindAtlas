## ADDED Requirements

### Requirement: Assistant-config SHALL Support Standalone Persistent System Workflow Assets
The system SHALL allow assistant-config to own persistent `is_system=true` workflow assets that are not bound to a system skill or a system AI behavior canonical default target.

#### Scenario: Standalone system workflow asset is synced
- **WHEN** assistant-config syncs built-in system targets
- **THEN** it SHALL create or recover each registered standalone system workflow asset as a normal `is_system=true` workflow
- **AND** that workflow SHALL remain read-only, copyable, and visible in workflow listings like other system workflows
- **AND** it SHALL not require a system skill wrapper

#### Scenario: Legacy standalone canonical name is renamed in place
- **WHEN** a registered standalone system workflow asset declares a legacy canonical name and a persisted `is_system=true` workflow still uses that legacy name
- **THEN** sync SHALL rename that same workflow row to the new canonical name
- **AND** it SHALL preserve the workflow ID and published/draft heads

#### Scenario: Custom name conflict blocks standalone sync
- **WHEN** a non-system workflow already uses the canonical name of a standalone system workflow asset
- **THEN** sync SHALL fail with a clear conflict
- **AND** it SHALL not create a second hidden system workflow

### Requirement: System Workflow Display Names SHALL Cover Standalone Assets
The system SHALL resolve user-facing workflow names for standalone system workflow assets through assistant-config's localized display-name registry.

#### Scenario: Standalone workflow appears in workflow APIs and callable listings
- **WHEN** a client lists workflows or callable workflows
- **THEN** a standalone system workflow asset SHALL use its localized display name in the response
- **AND** it SHALL not expose its raw canonical name as the user-facing name

### Requirement: Persisted System Targets SHALL Belong To One Canonical Source Class
Every persisted `is_system=true` workflow or agent SHALL belong to exactly one built-in source class.

#### Scenario: System target origin audit detects an unexpected target
- **WHEN** assistant-config audits persisted system targets for tests
- **THEN** each system workflow or agent SHALL match exactly one of the supported source classes
- **AND** any unclassified or multiply-classified target SHALL be reported as invalid

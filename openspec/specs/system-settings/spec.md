# Capability: System Settings

## Requirements

### Requirement: Runtime Config Updates SHALL Apply Shared Finalization
Runtime config writes SHALL use a shared post-update flow for commit, cache invalidation, and optional scheduler synchronization.

#### Scenario: Storage settings change
- **WHEN** storage runtime config is updated with `commit=true`
- **THEN** the service SHALL commit and clear runtime caches through the shared finalization flow

#### Scenario: Automation settings change
- **WHEN** automation runtime config is updated with `commit=true`
- **THEN** the service SHALL commit, clear runtime caches, and synchronize the scheduler through the shared finalization flow

### Requirement: Runtime Config Responses SHALL Reflect Effective State
Runtime config mutation APIs SHALL return the latest resolved response after persistence side effects complete.

#### Scenario: Knowledge graph update
- **WHEN** knowledge graph config is updated successfully
- **THEN** the response SHALL reflect the effective runtime settings after model selection and cache refresh

#### Scenario: Document parsing update
- **WHEN** document parsing config is updated successfully
- **THEN** the response SHALL reflect the effective runtime settings after persistence

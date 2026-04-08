## ADDED Requirements

### Requirement: System Workflows And Agents SHALL Behave As Immutable Baselines
The system SHALL treat shipped system workflows and system agent profiles as read-only baselines that can be viewed, inspected, validated or test-run, and copied, but not modified in place.

#### Scenario: Reject system workflow write operations
- **WHEN** a client attempts to update, publish, rollback, delete a version, clear versions, or delete an `is_system=true` workflow
- **THEN** the system SHALL reject the request with a copy-first error
- **AND** the existing system workflow target SHALL remain unchanged

#### Scenario: Reject system agent write operations
- **WHEN** a client attempts to update, publish, rollback, delete a version, clear versions, or delete an `is_system=true` agent profile
- **THEN** the system SHALL reject the request with a copy-first error
- **AND** the existing system agent target SHALL remain unchanged

#### Scenario: System target editors are read-only but runnable
- **WHEN** a user opens a system workflow or system agent editor
- **THEN** the UI SHALL render the target in read-only mode
- **AND** it SHALL keep test-run actions available
- **AND** it SHALL present `Copy As Duplicate` as the primary customization action

### Requirement: Workflow And Agent Copy SHALL Produce Editable User-Owned Duplicates
The system SHALL provide copy operations for workflows and agent profiles that create immediately bindable, non-system duplicates.

#### Scenario: Copy a system workflow
- **WHEN** a user copies an `is_system=true` workflow
- **THEN** the new workflow SHALL be created from the canonical shipped baseline content
- **AND** it SHALL be stored as `is_system=false`
- **AND** it SHALL receive an initial published version with draft and published heads aligned

#### Scenario: Copy a custom agent
- **WHEN** a user copies an `is_system=false` agent profile
- **THEN** the new agent SHALL be created from the source agent's current draft state
- **AND** it SHALL be stored as `is_system=false`
- **AND** it SHALL receive an initial published version with draft and published heads aligned

#### Scenario: Copy results are ready for rebinding
- **WHEN** a workflow or agent copy completes
- **THEN** the duplicate SHALL be immediately eligible for skill, system AI behavior, or OpenClaw binding
- **AND** the original target's references SHALL remain unchanged

### Requirement: Shipped System Targets SHALL Reconcile Back To Canonical Defaults
The system SHALL restore shipped system workflows and agent profiles to canonical defaults during sync, reset, or migration while preserving target identity.

#### Scenario: Restore mutated system workflow baseline
- **WHEN** sync or reset detects that a shipped system workflow's entity state or published snapshot differs from the canonical baseline
- **THEN** the workflow content, positions, description, and enabled state SHALL be restored to the canonical baseline
- **AND** the workflow target ID SHALL be preserved
- **AND** draft and published heads SHALL collapse to one published baseline version

#### Scenario: Restore mutated system agent baseline
- **WHEN** sync or reset detects that a shipped system agent's entity state or published snapshot differs from the canonical baseline
- **THEN** the agent prompt, tools, KB/model configuration, description, and enabled state SHALL be restored to the canonical baseline
- **AND** the agent target ID SHALL be preserved
- **AND** draft and published heads SHALL collapse to one published baseline version

### Requirement: System Binding Layers SHALL Follow Copy-First Customization
System skills and system AI behaviors SHALL continue to support rebinding while directing users to copy immutable system targets before customization.

#### Scenario: Rebind a system skill after copying a target
- **WHEN** a user copies a system workflow or agent and rebinds a system skill to that duplicate
- **THEN** the skill SHALL update to the copied target
- **AND** the canonical system target SHALL remain immutable

#### Scenario: Create a system AI behavior example workflow
- **WHEN** a user creates an example workflow from a system AI behavior
- **THEN** the example SHALL be produced through the shared workflow copy path from the canonical system target
- **AND** the copied workflow SHALL remain independent from later system target resets

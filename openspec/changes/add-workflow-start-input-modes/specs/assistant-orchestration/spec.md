## ADDED Requirements

### Requirement: Workflow Start Node SHALL Support Input Modes
The workflow start node SHALL support explicit input mode configuration with `text` and `structured`.

#### Scenario: Default text input mode
- **WHEN** a workflow start node has no explicit start config
- **THEN** the system SHALL treat it as `text` mode
- **AND** SHALL expose `start.user_input` as the start output field

#### Scenario: Structured input mode contract
- **WHEN** a workflow start node is configured with `inputMode=structured`
- **THEN** the system SHALL require at least one structured field definition
- **AND** each field SHALL satisfy name/type constraints

### Requirement: Start Field References SHALL Be Mode-Aware
Template references to `start.<field>` SHALL be validated against start input mode and configured fields.

#### Scenario: Text mode reference
- **WHEN** a workflow is in `text` mode
- **THEN** `start.user_input` SHALL be valid
- **AND** any other `start.*` reference SHALL be rejected

#### Scenario: Structured mode reference
- **WHEN** a workflow is in `structured` mode
- **THEN** only configured structured field names SHALL be valid in `start.*` references
- **AND** `start.user_input` SHALL be rejected

### Requirement: Workflow Test-Run SHALL Accept Structured Input Payload
Workflow test-run API SHALL support structured input payload and enforce mode consistency.

#### Scenario: Structured mode test-run
- **WHEN** client calls workflow test-run for a structured workflow
- **THEN** request SHALL provide `structured_input`
- **AND** missing required fields, unknown fields, or type mismatch SHALL be rejected

#### Scenario: Text mode test-run
- **WHEN** client calls workflow test-run for a text workflow
- **THEN** request SHALL provide non-empty `user_input`
- **AND** `structured_input` SHALL be rejected

### Requirement: Structured Workflow SHALL Not Be Skill-Bindable
Structured-input workflows SHALL NOT be bindable to skills.

#### Scenario: Reject skill binding to structured workflow
- **WHEN** a skill create/update request binds a structured-input workflow
- **THEN** backend SHALL reject with validation error

#### Scenario: Block switching referenced workflow to structured
- **WHEN** a workflow already referenced by skills is saved or published with structured start mode
- **THEN** backend SHALL reject and keep existing binding/running state unchanged

### Requirement: Workflow Description SHALL Be Editable From Start Configuration Flow
Workflow description updates from start settings SHALL persist through save and publish operations.

#### Scenario: Save workflow with updated description
- **WHEN** user edits workflow description via start settings and clicks save
- **THEN** workflow metadata description SHALL be updated together with draft save

#### Scenario: Publish workflow with updated description
- **WHEN** user edits workflow description and clicks save-and-publish
- **THEN** workflow metadata description SHALL be updated in the same publish transaction

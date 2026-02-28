## ADDED Requirements
### Requirement: Workflow Editor SHALL Provide Continuous Validation Feedback
Workflow editor SHALL continuously validate the in-memory workflow draft with debounce and present current validation state to users.

#### Scenario: Debounced auto-validation during editing
- **WHEN** nodes, edges, or node configuration change semantically
- **THEN** editor SHALL trigger validation after a debounce interval
- **AND** editor SHALL use backend validation results as error-level issues

#### Scenario: Ignore stale validation responses
- **WHEN** multiple validation requests are in flight
- **THEN** editor SHALL ignore stale responses from older requests
- **AND** latest request result SHALL be the only source of truth in checklist state

### Requirement: Workflow Editor SHALL Expose Validation Checklist UI
Workflow editor SHALL provide a checklist panel with grouped error/warning issues and detailed messages.

#### Scenario: Open checklist and inspect issues
- **WHEN** user clicks validation toolbar action
- **THEN** editor SHALL open checklist panel
- **AND** panel SHALL display grouped errors and warnings with detail text
- **AND** panel SHALL support manual refresh

#### Scenario: Validation request failure
- **WHEN** validation request fails
- **THEN** panel SHALL display request failure status
- **AND** previous successful checklist content SHALL remain visible

### Requirement: Toolbar Badge SHALL Count Errors Only
Validation toolbar badge SHALL display only error-level issue count.

#### Scenario: Warning-only workflow
- **WHEN** checklist contains warnings but no errors
- **THEN** toolbar badge count SHALL be zero

#### Scenario: Mixed severity issues
- **WHEN** checklist contains both errors and warnings
- **THEN** toolbar badge count SHALL equal error issue count only

### Requirement: Workflow Editor SHALL Provide Non-Blocking Reachability Warnings
Workflow editor SHALL emit warning-level issues for non-output nodes that do not reach the single output node.

#### Scenario: Node does not reach output
- **WHEN** workflow has exactly one output node
- **AND** a non-output node has no path to output
- **THEN** editor SHALL add a warning issue for that node
- **AND** this warning SHALL not block save-time backend validation

### Requirement: Checklist Issues SHALL Support Node Locate Routing
Checklist issues with node context SHALL support locate behavior for both main graph and subflow nodes.

#### Scenario: Locate main graph node issue
- **WHEN** user clicks locate on issue referencing a main graph node
- **THEN** editor SHALL select that node
- **AND** canvas SHALL center on that node

#### Scenario: Locate subflow node issue
- **WHEN** issue references container node and includes body node identifier
- **THEN** editor SHALL select subflow node context under that container
- **AND** canvas SHALL center on the container node

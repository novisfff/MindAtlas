# Capability: External Agent Integration

## Requirements

### Requirement: OpenClaw Capability Execution SHALL Run Published Targets
External capability execution SHALL resolve the published target and execute the matching tool, workflow, or agent path.

#### Scenario: Workflow-backed capability runs
- **WHEN** an exposed capability maps to a published workflow target
- **THEN** integration runtime SHALL execute the workflow with validated structured input

#### Scenario: Agent-backed capability runs
- **WHEN** an exposed capability maps to a published agent target
- **THEN** integration runtime SHALL execute the agent with validated structured input and output schema enforcement

### Requirement: OpenClaw Capability Availability SHALL Be Enforced
Capability execution SHALL fail fast when the resolved capability is unavailable.

#### Scenario: Capability disabled or unavailable
- **WHEN** a request targets a capability whose availability check fails
- **THEN** the integration API SHALL return a conflict response instead of attempting execution

### Requirement: OpenClaw Catalog SHALL Stay In Sync With System Items
The external integration catalog SHALL ensure system-backed items are available before capability execution.

#### Scenario: First execution after system item changes
- **WHEN** runtime settings or system targets changed since the last catalog snapshot
- **THEN** the integration service SHALL reconcile system items before resolving the requested capability

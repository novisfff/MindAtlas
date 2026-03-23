## ADDED Requirements

### Requirement: System AI Behaviors SHALL Bind To Reusable Assistant Targets
The system SHALL support built-in system AI behaviors that bind independently to reusable workflow or agent targets, separate from skill bindings.

#### Scenario: Weekly report behavior uses dedicated binding
- **WHEN** the system resolves execution for `weekly_report_generation`
- **THEN** it SHALL read that behavior's own binding record
- **AND** it SHALL NOT reuse assistant default model binding or skill target binding

#### Scenario: Monthly report behavior uses dedicated binding
- **WHEN** the system resolves execution for `monthly_report_generation`
- **THEN** it SHALL read that behavior's own binding record
- **AND** it SHALL support binding to either a workflow or an agent target

### Requirement: System AI Behavior Execution SHALL Use Published Target Snapshots
System AI behavior execution SHALL only run published workflow or agent target versions.

#### Scenario: Workflow binding without published version
- **WHEN** a workflow is selected as a system AI behavior target but has no published version
- **THEN** binding save SHALL be rejected

#### Scenario: Runtime fallback for unavailable bound target
- **WHEN** the currently bound workflow or agent is disabled, missing, unpublished, or otherwise not executable at runtime
- **THEN** the system SHALL execute that behavior's canonical system default target
- **AND** it SHALL NOT fallback to legacy direct prompt/model logic

### Requirement: Report System AI Behaviors SHALL Enforce Fixed Structured Contracts
Weekly and monthly report system AI behaviors SHALL use a fixed structured input and output contract regardless of target type.

#### Scenario: Workflow target validation
- **WHEN** a workflow is bound to a report system AI behavior
- **THEN** the workflow SHALL declare structured start input containing `periodType`, `periodStart`, `periodEnd`, and `entryCount`
- **AND** it SHALL expose structured output containing `summary`, `suggestions`, and `trends`

#### Scenario: Agent target runtime validation
- **WHEN** an agent is bound to a report system AI behavior
- **THEN** the system SHALL wrap execution with behavior-specific contract instructions
- **AND** the final output SHALL be parsed and validated as JSON with `summary`, `suggestions`, and `trends`
- **AND** invalid JSON or missing fields SHALL mark the report run as failed

### Requirement: Report Generation SHALL Use System AI Behavior Runner
Weekly and monthly report generation APIs and scheduler jobs SHALL share the same system AI behavior execution path.

#### Scenario: Weekly report generation
- **WHEN** `/api/reports/weekly/generate` or the weekly scheduler job runs
- **THEN** report content generation SHALL execute through the system AI behavior runner for `weekly_report_generation`

#### Scenario: Monthly report generation
- **WHEN** `/api/reports/monthly/generate` or the monthly scheduler job runs
- **THEN** report content generation SHALL execute through the system AI behavior runner for `monthly_report_generation`

### Requirement: System AI Behaviors SHALL Have Canonical System Default Targets
Each supported system AI behavior SHALL have a canonical system-owned default target with persisted versions and deterministic reset/fallback behavior.

#### Scenario: First access initializes canonical defaults
- **WHEN** system AI behavior bindings are first listed or otherwise ensured
- **THEN** the system SHALL create canonical system default targets and binding records for `weekly_report_generation` and `monthly_report_generation` if missing

#### Scenario: Canonical targets are protected
- **WHEN** a delete request targets a canonical system default workflow or agent for a system AI behavior
- **THEN** the delete request SHALL be rejected

### Requirement: System AI Behavior Settings SHALL Be Managed From A Dedicated Page
The application SHALL provide a dedicated settings page for system AI behavior bindings.

#### Scenario: Settings entry
- **WHEN** the user opens the Settings home page
- **THEN** the page SHALL include a `System AI Behaviors` entry separate from Assistant Targets and AI Providers

#### Scenario: Behavior card rendering
- **WHEN** the user opens the system AI behaviors settings page
- **THEN** the page SHALL render a card for each supported behavior
- **AND** each card SHALL show the current binding, canonical system default target, contract summary, reset action, and open-target action

### Requirement: System AI Behavior References SHALL Have Independent Delete Semantics
Workflow and agent delete flows SHALL treat system AI behavior references separately from skill references.

#### Scenario: Delete target referenced by skills
- **WHEN** a workflow or agent is still referenced by one or more skills
- **THEN** delete SHALL remain hard-blocked

#### Scenario: Delete target referenced only by system AI behaviors
- **WHEN** a user-created workflow or agent is referenced only by system AI behaviors
- **THEN** the first delete request SHALL return a conflict describing the impacted behavior keys
- **AND** a confirmed delete SHALL atomically rebind those behaviors to canonical defaults before deleting the target

### Requirement: System AI Behavior UI SHALL Be Localized
System AI behavior settings and delete-confirm flows SHALL render localized labels rather than raw keys.

#### Scenario: Render system AI behaviors page
- **WHEN** the system AI behaviors page is rendered in any supported locale
- **THEN** page title, descriptions, actions, contract text, and delete/rebind prompts SHALL use localized strings

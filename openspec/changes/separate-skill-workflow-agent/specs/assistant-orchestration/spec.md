## ADDED Requirements

### Requirement: Skill SHALL Bind Exactly One Executable Target
The system SHALL persist each skill as routing metadata bound to exactly one executable target: either a workflow or an agent profile.

#### Scenario: Create workflow-bound skill
- **WHEN** client creates a skill with `targetType=workflow` and a valid `workflowId`
- **THEN** skill SHALL be persisted with `workflow_id` set and `agent_profile_id` null

#### Scenario: Reject missing target binding
- **WHEN** client creates or updates a skill without workflow or agent target
- **THEN** request SHALL be rejected by validation/constraint

### Requirement: Workflow and Agent SHALL Be Independently Managed
The system SHALL provide independent CRUD APIs for reusable workflows and agent profiles.

#### Scenario: Manage workflow independently
- **WHEN** user updates a workflow via canonical workflow API
- **THEN** update SHALL apply to the workflow entity without requiring skill update payload

#### Scenario: Manage agent profile independently
- **WHEN** user updates an agent profile via canonical agent API
- **THEN** bound skills SHALL use updated agent configuration at runtime

### Requirement: Referenced Targets SHALL Be Protected From Deletion
The system SHALL reject deletion of workflows/agents that are currently referenced by any skill.

#### Scenario: Delete referenced workflow
- **WHEN** delete request targets a workflow bound by one or more skills
- **THEN** API SHALL return conflict error and preserve workflow

### Requirement: Legacy Skill Workflow Routes SHALL Remain Temporarily Compatible
During transition, legacy skill workflow endpoints SHALL remain available for workflow-bound skills.

#### Scenario: Forward legacy workflow update route
- **WHEN** client calls `/skills/{id}/workflow` for a workflow-bound skill
- **THEN** system SHALL apply update to the skill's bound workflow

#### Scenario: Reject legacy workflow route for agent-bound skill
- **WHEN** client calls `/skills/{id}/workflow` for an agent-bound skill
- **THEN** system SHALL return `409` with explicit target mismatch message

### Requirement: Frontend SHALL Provide Unified Target Management And Binding UX
The system SHALL provide a unified frontend UX for managing workflow/agent executables and binding skills to a single selected target.

#### Scenario: Open unified target management
- **WHEN** user opens assistant orchestration settings
- **THEN** workflow and agent executables SHALL be listed in one target management page with type labels and shared actions

#### Scenario: Bind skill from single selector
- **WHEN** user edits a skill and selects one target from the unified selector
- **THEN** frontend SHALL submit matching target binding fields (`targetType` plus one of `workflowId`/`agentProfileId`) without asking user to pick target type separately

#### Scenario: Edit agent prompt, KB, and tools
- **WHEN** user opens agent editor from target management
- **THEN** user SHALL be able to edit `systemPrompt`, `kbConfig.enabled`, and `tools[]` and persist the configuration

#### Scenario: Expand target row for details
- **WHEN** user clicks a target row in unified target management
- **THEN** the row SHALL expand to show details (Agent runtime details or Workflow mini graph), and only one row remains expanded at a time

#### Scenario: Do not expose toggle and normalize legacy disabled targets
- **WHEN** unified target management loads existing executables
- **THEN** the UI SHALL not show enable/disable toggle controls and SHALL attempt to normalize legacy disabled targets to enabled state

#### Scenario: Run agent draft in editor without persistence
- **WHEN** user edits an agent and starts test-run from the editor right pane without saving
- **THEN** backend SHALL execute the draft payload via dedicated assistant-config test-run endpoint and SHALL NOT write conversation/messages records

#### Scenario: Agent editor dual-pane workflow
- **WHEN** user opens agent editor
- **THEN** UI SHALL present a dual-pane workspace with configuration on the left and test-run panel on the right (stacked on narrow screens)

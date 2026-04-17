## MODIFIED Requirements

### Requirement: The system SHALL provide independent CRUD APIs for reusable workflows and agent profiles.
Reusable workflows and agent profiles SHALL remain independently manageable targets, and workflow graph persistence SHALL use version snapshots as the single source of truth.

#### Scenario: Workflow detail is materialized from the draft version snapshot
- **WHEN** a client requests a workflow detail payload
- **THEN** the system SHALL resolve the workflow graph from the workflow's `draft_version_id` snapshot
- **AND** SHALL return `nodes`, `edges`, and `workflowViewport` derived from that snapshot rather than relational node/edge rows

#### Scenario: Workflow create initializes snapshot-backed draft and published heads
- **WHEN** a client creates a new workflow
- **THEN** the system SHALL create an initial `publish` workflow version snapshot
- **AND** SHALL set both `draft_version_id` and `published_version_id` to that version
- **AND** SHALL NOT persist separate current-state workflow node/edge rows

### Requirement: Saving workflow/agent editor content SHALL create a `save` version and advance only the draft head.
Saving editor content SHALL create a new draft snapshot without mutating any separate relational workflow graph tables.

#### Scenario: Workflow save advances the draft snapshot only
- **WHEN** user saves workflow editor content through `PUT /workflows/{id}`
- **THEN** the system SHALL validate the submitted graph
- **AND** SHALL create a `save` workflow version snapshot from the submitted `WorkflowInput`
- **AND** SHALL update only `draft_version_id`
- **AND** SHALL NOT write current-state workflow nodes or edges to dedicated relational child tables

### Requirement: Publishing SHALL write a `publish` version and update both draft and published heads.
Publishing a workflow SHALL promote the submitted graph only by updating version snapshots and workflow head pointers.

#### Scenario: Workflow publish updates both workflow heads with a publish snapshot
- **WHEN** user calls `POST /workflows/{id}/publish` with the current workflow graph
- **THEN** the system SHALL validate the graph
- **AND** SHALL create a `publish` workflow version snapshot from the submitted graph
- **AND** SHALL point both `draft_version_id` and `published_version_id` to that version
- **AND** SHALL NOT persist the graph to dedicated workflow node/edge tables

### Requirement: The system SHALL track both draft and published version heads for each workflow target and agent target.
Workflow graph state SHALL be fully recoverable from version snapshots referenced by draft and published heads.

#### Scenario: Rollback restores draft graph from an existing workflow version snapshot
- **WHEN** client rolls back a workflow draft to a historical version
- **THEN** the system SHALL set `draft_version_id` to the selected version
- **AND** SHALL derive the returned workflow graph entirely from that version snapshot

#### Scenario: System workflow baseline restore compares snapshot state
- **WHEN** the system reconciles a shipped system workflow to its canonical baseline
- **THEN** it SHALL compare desired baseline input against the workflow's current draft/published snapshot state
- **AND** SHALL restore canonical snapshots without relying on persisted workflow node/edge rows

### Requirement: During transition, legacy skill workflow endpoints SHALL remain available for workflow-bound skills.
Legacy skill workflow endpoints SHALL continue to operate for workflow-bound skills, but the underlying workflow graph SHALL be persisted only as workflow version snapshots.

#### Scenario: Legacy skill workflow update writes snapshot-backed workflow state
- **WHEN** client calls `/skills/{id}/workflow` for a workflow-bound skill
- **THEN** the system SHALL apply the submitted graph to the bound workflow by creating or updating workflow version snapshots
- **AND** SHALL NOT persist dedicated skill node/edge or workflow node/edge rows

## REMOVED Requirements

### Requirement: Workflow current-state graphs SHALL be persisted as relational node and edge rows.
**Reason**: Relational node/edge current-state persistence duplicates version snapshots and increases write-path complexity without providing proportional value in the current architecture.

**Migration**: Workflow current-state reads and writes move entirely to `assistant_workflow_version.snapshot` referenced by draft/published heads, and legacy workflow/skill node-edge tables are dropped.

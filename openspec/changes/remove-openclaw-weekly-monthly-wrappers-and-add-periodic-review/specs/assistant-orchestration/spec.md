## MODIFIED Requirements

### Requirement: System Workflow Assets SHALL Support Stable Workflow-Call Targets
System workflow assets SHALL be allowed to reference other standalone system workflow assets without embedding fixed workflow UUIDs in the source JSON.

#### Scenario: System asset workflow calls resolve by asset key during sync
- **WHEN** MindAtlas syncs or republishes a system workflow asset that contains a `workflow_call` node with `targetSystemAssetKey`
- **THEN** the system SHALL resolve that asset key to the current standalone system workflow
- **AND** it SHALL persist the resolved workflow id and published version id into the synced workflow before execution

### Requirement: Periodic Review SHALL Reuse One Structured Core Workflow
MindAtlas periodic review experiences SHALL share one structured core workflow across chat wrappers and external entry points.

#### Scenario: Chat periodic review uses a text wrapper over the core workflow
- **WHEN** the chat-side periodic review system workflow runs from natural-language user input
- **THEN** it SHALL first extract `focus`, `period`, `startDate`, and `endDate`
- **AND** it SHALL invoke the shared `periodic_review_core` workflow through `workflow_call`

#### Scenario: Periodic review core defaults to a recent time window
- **WHEN** `periodic_review_core` receives no explicit date range and no relative period
- **THEN** it SHALL normalize the request to the most recent 30 days
- **AND** it SHALL return the final review through a structured `{ content: string }` output contract

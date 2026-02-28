## ADDED Requirements
### Requirement: Workflow Editor SHALL Support Draft Test Run
The workflow editor SHALL allow executing unsaved workflow drafts without persisting workflow DAG changes.

#### Scenario: Execute unsaved draft
- **WHEN** user modifies workflow graph but does not save
- **AND** user triggers test run
- **THEN** system SHALL execute current in-memory workflow draft
- **AND** system SHALL NOT update persisted workflow nodes/edges

### Requirement: Workflow Test Run SHALL Expose Full Trace SSE Events
Workflow test run endpoint SHALL stream runtime trace events for lifecycle and execution details.

#### Scenario: Stream runtime events during execution
- **WHEN** a test run starts
- **THEN** system SHALL stream run lifecycle events (`run_start`, `run_end`, `run_error`)
- **AND** system SHALL stream node, branch, and tool events (`node_start`, `node_output_delta`, `node_end`, `branch_decision`, `tool_call_start`, `tool_call_end`)
- **AND** system SHALL stream output chunks as `content_delta`

#### Scenario: Include per-node input/output snapshots
- **WHEN** a node finishes execution (success or error)
- **THEN** system SHALL emit `node_snapshot`
- **AND** payload SHALL include `nodeId`, `nodeType`, `status`, `input`, `output`, and `errorMessage`
- **AND** large snapshot fields MAY be truncated with a truncation indicator

### Requirement: Test Run Streaming SHALL Be Caller-Controlled
Test run output streaming SHALL be controlled by caller input (`streamOutput`) instead of editor node-local toggles.

#### Scenario: Stream enabled
- **WHEN** caller sends `streamOutput=true`
- **THEN** output chunks SHALL be streamed incrementally as `content_delta`

#### Scenario: Stream disabled
- **WHEN** caller sends `streamOutput=false`
- **THEN** runtime SHALL aggregate output content and emit final content at run completion

### Requirement: Test Run SHALL Be Isolated From Conversation Persistence
Workflow test run SHALL not create assistant conversation/messages records.

#### Scenario: Execute test run
- **WHEN** workflow test run is executed from editor
- **THEN** no conversation/message persistence APIs SHALL be invoked
- **AND** no chat history record SHALL be created from test-run output

### Requirement: Subflow Trace IDs SHALL Be Routable
Container subflow trace events SHALL expose scoped node IDs to avoid collisions and enable editor routing.

#### Scenario: Emit subflow node events
- **WHEN** `iteration` or `loop` body nodes emit runtime node events
- **THEN** node ID in callbacks SHALL be scoped as `containerId::innerNodeId`

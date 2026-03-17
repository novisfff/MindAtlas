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

### Requirement: Workflow Draft Test Run SHALL Support Multi-Turn Conversation For Text Start
Workflow draft test run SHALL support multi-turn conversation when the workflow `start.inputMode` is `text`.

#### Scenario: Continue text-mode workflow conversation
- **WHEN** workflow test run is started from a text-mode start node
- **AND** caller provides a stable `sessionId` plus completed `history`
- **THEN** runtime SHALL execute the new turn with that history
- **AND** the frontend SHALL be able to inspect the result, trace, and raw events for each completed turn

#### Scenario: Structured start remains single-run
- **WHEN** workflow test run is started from a structured start node
- **THEN** caller SHALL provide only the current structured input
- **AND** `history` and `sessionMemory` SHALL be rejected

### Requirement: Workflow Draft Test Run SHALL Maintain Ephemeral L0 L1 L2 Session Memory
Workflow draft test run SHALL keep short-term conversation memory semantics aligned with the assistant system for text-mode sessions, without backend persistence.

#### Scenario: Use ephemeral session memory in text-mode test run
- **WHEN** caller sends text-mode `history` and `sessionMemory`
- **THEN** runtime SHALL build L0 from `history`
- **AND** runtime SHALL use `sessionMemory.conversationSummary` as temporary L1
- **AND** runtime SHALL use `sessionMemory.skillFacts` as temporary L2
- **AND** runtime SHALL NOT persist those values to backend conversation memory tables

#### Scenario: Return next ephemeral session memory after completed run
- **WHEN** a text-mode workflow test run completes successfully
- **THEN** system SHALL compute the next effective conversation summary and skill facts
- **AND** system SHALL return them in `run_end.sessionMemory`
- **AND** if memory computation fails, the run SHALL still complete and the previous session memory SHALL be returned

### Requirement: Subflow Trace IDs SHALL Be Routable
Container subflow trace events SHALL expose scoped node IDs to avoid collisions and enable editor routing.

#### Scenario: Emit subflow node events
- **WHEN** `iteration` or `loop` body nodes emit runtime node events
- **THEN** node ID in callbacks SHALL be scoped as `containerId::innerNodeId`

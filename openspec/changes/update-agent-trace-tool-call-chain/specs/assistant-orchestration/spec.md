## ADDED Requirements

### Requirement: Agent Tool Call Events SHALL Include Parent Node Trace Context
Workflow runtime SHALL attach parent node execution context to `agent` tool call events without introducing new SSE event names.

#### Scenario: Agent tool call emits parent execution context
- **WHEN** a `workflow_dag.agent` node emits `tool_call_start` or `tool_call_end`
- **THEN** payload SHALL include `nodeId`, `nodeType`, and `nodeExecutionId`
- **AND** payload SHALL include `agentRound`, `toolCallIndex`, and `toolKind` when available
- **AND** existing event names SHALL remain `tool_call_start` and `tool_call_end`

### Requirement: Workflow Test Trace SHALL Present Agent Tool Chain By Execution Instance
Workflow test trace SHALL group node events by execution instance and render agent tool calls under the matching agent execution step.

#### Scenario: Same agent node executes multiple times
- **WHEN** the same `agent` node ID is executed multiple times in `iteration` or `loop`
- **THEN** frontend trace aggregation SHALL distinguish executions by `nodeExecutionId`
- **AND** tool calls SHALL be attached to the matching execution instance instead of overwriting previous runs
- **AND** scoped container node IDs SHALL remain routable for editor locate actions

#### Scenario: Agent tool chain is visible in workflow test trace
- **WHEN** an `agent` node performs one or more tool calls during workflow test run
- **THEN** the trace panel SHALL display a nested `Tool Chain` section under that agent execution card
- **AND** each tool step SHALL expose round, tool name, status, duration, and expandable args/result details

### Requirement: Assistant Conversation Tool Call Persistence SHALL Preserve Trace Context For Replay
Assistant conversation persistence SHALL retain tool trace context so refreshed chat sessions can replay the same tool call metadata.

#### Scenario: Reload conversation after agent tool call
- **WHEN** assistant chat persists `tool_calls` and `tool_results` for a message containing agent tool activity
- **THEN** persisted JSON SHALL preserve the trace context fields emitted during streaming
- **AND** refreshing and replaying the conversation SHALL restore those fields to the frontend tool call model
- **AND** no database schema migration SHALL be required

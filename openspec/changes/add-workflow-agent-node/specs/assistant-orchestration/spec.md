## ADDED Requirements

### Requirement: Workflow DAG SHALL Support Agent Node In Main Flow And Container Body
Workflow DAG runtime and validator SHALL support node type `agent` in both top-level DAG and `iteration/loop` container body subflows.

#### Scenario: Main flow agent node is accepted and executable
- **WHEN** workflow defines an `agent` node in main DAG topology
- **THEN** workflow save/compile validation SHALL accept the node when config is valid
- **AND** runtime SHALL execute the `agent` node and produce `response` text output

#### Scenario: Container body agent node is accepted and executable
- **WHEN** `iteration` or `loop` body defines an `agent` node
- **THEN** container validation SHALL apply the same config rules as main flow
- **AND** runtime SHALL execute the body `agent` node within container subflow

### Requirement: Agent Node SHALL Call Only Configured Tool Names
`agent` node tool invocation SHALL be limited to `agent.toolNames` whitelist.

#### Scenario: Whitelisted tool call succeeds
- **WHEN** model requests a tool call whose name is listed in `agent.toolNames`
- **THEN** runtime SHALL execute that tool and continue the loop

#### Scenario: Missing or unavailable tool is terminal
- **WHEN** model requests a tool that is not available in runtime tool map
- **THEN** runtime SHALL raise node failure
- **AND** run SHALL follow existing terminal failure chain

### Requirement: Agent Node SHALL Run Serial Tool Loop With Max Iterations
`agent` node SHALL run iterative LLM->tool rounds serially, with at most one tool call executed per round.

#### Scenario: Multiple tool calls in one round
- **WHEN** model returns multiple tool calls in one response
- **THEN** runtime SHALL execute only the first call
- **AND** runtime SHALL emit warning log for dropped calls

#### Scenario: Max iteration exceeded
- **WHEN** tool loop does not converge before `maxIterations`
- **THEN** runtime SHALL raise terminal node failure

### Requirement: Agent Node SHALL Support Node-Level Model Selection
`agent` node SHALL support `modelSource/default|custom + modelId` semantics equivalent to other LLM-like nodes.

#### Scenario: Main DAG custom model binding
- **WHEN** `agent` node sets `modelSource=custom` with valid `modelId`
- **THEN** runtime SHALL resolve and bind the custom model client for that node

#### Scenario: Container body custom model binding
- **WHEN** container body `agent` node sets `modelSource=custom` with valid `modelId`
- **THEN** runtime SHALL resolve and bind the model under scoped runtime key `containerId::nodeId`

### Requirement: Agent Node SHALL Respect Start Memory Mode Semantics
`agent` node SHALL follow current `memoryMode` semantics for automatic memory injection behavior.

#### Scenario: Auto mode injects memory context
- **WHEN** runtime executes `agent` node with `memoryMode=auto`
- **THEN** runtime SHALL include L1/L2 memory block in system context
- **AND** runtime SHALL include L0 dialogue messages as message-flow context

#### Scenario: Off and structured modes disable auto injection
- **WHEN** runtime executes `agent` node with `memoryMode=off` or `memoryMode=structured`
- **THEN** runtime SHALL NOT auto-inject memory context

### Requirement: Agent Node Failures SHALL Be Terminal For Run
Any `agent` node runtime failure SHALL be treated as terminal and SHALL NOT silently downgrade.

#### Scenario: Tool execution exception
- **WHEN** tool execution raises an exception inside `agent` loop
- **THEN** node SHALL fail immediately
- **AND** run SHALL enter existing failure completion path

#### Scenario: Invalid agent configuration at runtime
- **WHEN** runtime encounters invalid `agent` configuration (such as empty `toolNames`)
- **THEN** node SHALL fail immediately
- **AND** no fallback auto-repair behavior SHALL be applied

## ADDED Requirements
### Requirement: Workflow Must Use A Dedicated Output Node
Workflow DAG SHALL define exactly one `output` node as the terminal response producer.

#### Scenario: Missing output node is rejected
- **WHEN** a workflow is validated without any `output` node
- **THEN** validation SHALL fail with an error indicating exactly one output node is required

#### Scenario: Multiple output nodes are rejected
- **WHEN** a workflow contains two or more `output` nodes
- **THEN** validation SHALL fail with an error indicating only one output node is allowed

#### Scenario: Output node must be terminal
- **WHEN** an `output` node has any outgoing edge
- **THEN** validation SHALL fail and indicate output node cannot have outgoing edges

### Requirement: Output Node Supports Text And Structured Modes
The `output` node SHALL support `text` and `structured` output modes.

#### Scenario: Text mode renders template
- **WHEN** output mode is `text` and `textTemplate` is configured
- **THEN** runtime SHALL render the template and produce it as final response text

#### Scenario: Structured mode renders field mappings
- **WHEN** output mode is `structured` and `outputFields` are configured with `value` templates
- **THEN** runtime SHALL resolve each field, coerce value type according to field spec, and emit one JSON object response

### Requirement: Streaming Is Caller-Controlled
Workflow response streaming SHALL be controlled by the chat request switch (`streamOutput` / runtime `stream_output`) rather than node-local output flags.

#### Scenario: Stream enabled with passthrough-eligible output
- **WHEN** `stream_output=true` and output template is a single LLM response reference
- **THEN** runtime SHALL stream upstream LLM token deltas and SHALL NOT duplicate the same final text from output node

#### Scenario: Stream disabled
- **WHEN** `stream_output=false`
- **THEN** runtime SHALL buffer content deltas and emit a single aggregated final response after graph completion

### Requirement: Output Node Is Not Allowed In Container Subflows
Container body graphs (`iteration` and `loop`) SHALL NOT allow `output` nodes.

#### Scenario: Output node appears inside container body
- **WHEN** container body nodes include `output`
- **THEN** workflow validation SHALL fail with an unsupported node type error for container body

### Requirement: Legacy LLM Output Flag Requires Migration
Legacy workflows using `llm.isOutput` SHALL be migrated to explicit `output` nodes before runtime execution.

#### Scenario: Legacy single-output workflow migration
- **WHEN** a workflow has exactly one `llm.isOutput=true` node and no output node
- **THEN** migration SHALL create a new output node, connect legacy LLM to output node, and remove legacy `isOutput` flag

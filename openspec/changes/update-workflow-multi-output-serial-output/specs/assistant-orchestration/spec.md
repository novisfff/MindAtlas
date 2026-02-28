## MODIFIED Requirements

### Requirement: Workflow Must Use Dedicated Output Node(s)
Workflow DAG SHALL define at least one `output` node as terminal response producer(s).

#### Scenario: Missing output node is rejected
- **WHEN** a workflow is validated without any `output` node
- **THEN** validation SHALL fail with an error indicating at least one output node is required

#### Scenario: Multiple output nodes are allowed
- **WHEN** a workflow contains two or more `output` nodes
- **THEN** validation SHALL pass this topology rule if all output nodes are terminal

#### Scenario: Output node must be terminal
- **WHEN** an `output` node has any outgoing edge
- **THEN** validation SHALL fail and indicate output node cannot have outgoing edges

### Requirement: Output Streaming Uses Single Message Segmentation
When multiple output nodes emit content in one run, runtime SHALL serialize chunks into a single response stream with source-switch segmentation.

#### Scenario: Completion-order segmented output
- **WHEN** two output nodes emit content and their completion order differs
- **THEN** runtime SHALL emit content in event arrival order
- **AND** SHALL insert `\n\n` between adjacent segments when output source node changes

#### Scenario: Structured output in multi-output workflow
- **WHEN** multiple output nodes include structured-mode output
- **THEN** each structured output SHALL be emitted as JSON text segment in the shared stream
- **AND** runtime SHALL NOT require final aggregation into a single JSON object

### Requirement: Single-Output Passthrough Optimization Remains Scoped
LLM token passthrough optimization SHALL remain enabled only for single-output workflows.

#### Scenario: Multi-output disables passthrough source selection
- **WHEN** a workflow has more than one output node
- **THEN** runtime SHALL NOT select an `output_stream_source_node_id` passthrough source
- **AND** outputs SHALL be emitted by output nodes themselves

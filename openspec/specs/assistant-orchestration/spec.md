# Capability: Assistant Orchestration

## Requirements

### Requirement: LangGraph Runtimes SHALL Be Invokable And Streamable
Assistant and workflow runtimes SHALL expose a runnable graph contract that supports streamed execution and direct invocation.

#### Scenario: Runtime uses compiled graph stream path
- **WHEN** the compiled graph exposes `stream`
- **THEN** assistant execution SHALL consume that stream directly

#### Scenario: Runtime uses invoke-only graph path
- **WHEN** the compiled graph exposes `invoke` but not `stream`
- **THEN** assistant execution SHALL derive streamed progress from the invoke result without failing

#### Scenario: Runtime uses lightweight test fallback
- **WHEN** the runtime environment provides a graph stub without `stream` or `invoke`
- **THEN** assistant execution SHALL use a fallback runner that preserves node behavior and state merging

### Requirement: Workflow DAG Execution SHALL Preserve Runtime State
Workflow DAG execution SHALL preserve merged node outputs, execution trace, branch decisions, environment variables, and memory context across node execution.

#### Scenario: Sequential env var mutations
- **WHEN** a workflow runs `start -> variable_assign -> output`
- **THEN** downstream nodes SHALL observe the updated `env` values

#### Scenario: Parallel branches merge safely
- **WHEN** two parallel branches both feed a later node
- **THEN** the later node SHALL run only after both predecessor branches complete

### Requirement: Memory Context SHALL Be Type Stable
Assistant execution SHALL normalize runtime memory context before prompt injection or node execution.

#### Scenario: L2 facts payload arrives as list
- **WHEN** L2 memory facts load as a list of strings
- **THEN** runtime SHALL preserve the facts list and rendered text

#### Scenario: L2 facts payload arrives as count-only legacy shape
- **WHEN** a legacy caller returns rendered L2 text plus a numeric fact count
- **THEN** runtime SHALL keep the rendered text, normalize facts to an empty list, and continue execution

### Requirement: Workflow Node Custom Models SHALL Resolve For Nested Nodes
Workflow node model resolution SHALL support both top-level nodes and container body nodes.

#### Scenario: Top-level agent node uses custom model
- **WHEN** a top-level `agent`, `llm`, or `parameter_extractor` node sets `modelSource=custom`
- **THEN** runtime SHALL resolve and bind that custom model under the node ID

#### Scenario: Container body node uses custom model
- **WHEN** an `iteration` or `loop` body node sets `modelSource=custom`
- **THEN** runtime SHALL resolve and bind that custom model under `containerId::nodeId`

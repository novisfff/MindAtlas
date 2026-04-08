## ADDED Requirements

### Requirement: Workflow DAG SHALL Support Workflow Call Nodes
Workflow DAG SHALL support a dedicated `workflow_call` node type in both the main graph and container body subflows.

#### Scenario: Workflow call node is available in editor and runtime
- **WHEN** a user adds nodes in the workflow editor or a container body subflow
- **THEN** `workflow_call` SHALL be selectable, serializable, and executable as a workflow DAG node
- **AND** it SHALL remain distinct from `tool` nodes and `agent.toolNames`

### Requirement: Callable Workflow Targets SHALL Be Contract-Gated
Only enabled workflows with a published structured start input contract and a single unambiguous structured output contract SHALL be exposed as callable workflow targets.

#### Scenario: Non-callable workflow is excluded
- **WHEN** a workflow is disabled, unpublished, text-input, or has ambiguous structured output contracts
- **THEN** it SHALL NOT appear in callable workflow target listings
- **AND** `workflow_call` validation SHALL reject references to it

#### Scenario: Callable workflow exposes versioned contracts
- **WHEN** a workflow is callable
- **THEN** the system SHALL expose callable published versions and their contract-derived input/output params

### Requirement: Workflow Call Nodes SHALL Support Pinned And Latest Binding
`workflow_call` nodes SHALL support explicit `pinned` and `latest` target binding modes.

#### Scenario: New node defaults to pinned latest published version
- **WHEN** a user creates a new `workflow_call` node
- **THEN** the node SHALL default to `pinned`
- **AND** `targetPublishedVersionId` SHALL be initialized to the target workflow current latest published version

#### Scenario: Latest binding resolves dynamically
- **WHEN** a `workflow_call` node uses `latest`
- **THEN** runtime SHALL resolve the target workflow current published version at execution time

### Requirement: Workflow Call Validation SHALL Prevent Recursive References
Workflow validation SHALL reject self-references and cross-workflow recursive call chains.

#### Scenario: Self-reference is rejected
- **WHEN** workflow `A` contains a `workflow_call` node targeting workflow `A`
- **THEN** workflow validation SHALL fail

#### Scenario: Indirect cycle is rejected
- **WHEN** workflow `A` references workflow `B`
- **AND** workflow `B` directly or transitively references workflow `A`
- **THEN** workflow validation SHALL fail

### Requirement: Workflow Call Runtime SHALL Scope Child Execution Events
Runtime SHALL execute child workflows with scoped trace events and shared human approval lifecycle handling.

#### Scenario: Child workflow emits scoped events
- **WHEN** a `workflow_call` node executes a child workflow
- **THEN** child node, tool, branch, snapshot, and approval events SHALL be forwarded with scoped node ids
- **AND** child `content_delta` events SHALL NOT be streamed as final user output

#### Scenario: Child workflow output becomes parent node output
- **WHEN** a child workflow completes successfully
- **THEN** the parent `workflow_call` node SHALL expose `response`
- **AND** it SHALL expose the child workflow declared structured output fields for downstream references

### Requirement: Workflow Call References SHALL Protect Referenced Workflows And Versions
The system SHALL block destructive workflow and published-version operations while `workflow_call` references still exist.

#### Scenario: Referenced workflow delete is blocked
- **WHEN** a workflow is still referenced by any `workflow_call` node
- **THEN** workflow deletion SHALL fail and include reference information

#### Scenario: Referenced pinned published version delete is blocked
- **WHEN** a published workflow version is still pinned by any `workflow_call` node
- **THEN** version deletion SHALL fail and include reference information

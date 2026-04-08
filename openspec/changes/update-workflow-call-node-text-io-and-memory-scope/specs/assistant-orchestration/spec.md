## MODIFIED Requirements

### Requirement: Callable Workflow Targets SHALL Be Contract-Gated
Only enabled workflows with a published text or structured start contract and a single unambiguous text or structured output contract SHALL be exposed as callable workflow targets.

#### Scenario: Text and structured callable workflows are accepted
- **WHEN** a published enabled workflow exposes one of `text->text`, `text->structured`, `structured->text`, or `structured->structured`
- **THEN** it SHALL appear in callable workflow target listings
- **AND** the callable contract SHALL expose `inputMode`, `outputMode`, and normalized callable params

#### Scenario: Text input is normalized
- **WHEN** a callable workflow start node uses text input mode
- **THEN** the callable contract SHALL expose exactly one required input field named `user_input`

#### Scenario: Text output is normalized
- **WHEN** a callable workflow output contract resolves to text mode
- **THEN** the callable contract SHALL normalize the canonical output to `response`
- **AND** callable API payloads MAY leave `outputParams` empty while runtime and frontend still expose `response`

#### Scenario: Ambiguous output contracts are rejected
- **WHEN** a workflow mixes text and structured outputs, or exposes multiple different structured output contracts
- **THEN** it SHALL NOT be callable
- **AND** workflow-call validation SHALL reject references to it

### Requirement: Workflow Call Runtime SHALL Scope Child Execution Events
Runtime SHALL execute child workflows with scoped trace events, shared human approval lifecycle handling, and normalized parent node outputs.

#### Scenario: Child workflow input mapping follows contract mode
- **WHEN** a `workflow_call` node executes a text child workflow
- **THEN** runtime SHALL pass the bound `user_input` as child text input
- **AND** it SHALL NOT synthesize the child payload as `structured_input`

#### Scenario: Child workflow output remains parent-node oriented
- **WHEN** a child workflow completes successfully
- **THEN** the parent `workflow_call` node SHALL always expose `response`
- **AND** it SHALL additionally expose declared structured output fields only when the child workflow output mode is structured

## ADDED Requirements

### Requirement: Workflow Call Runtime SHALL Maintain Child Memory Scopes
Nested workflow execution SHALL merge parent memory with child-scope memory and persist child-scope memory when a stable conversation or session scope exists.

#### Scenario: Assistant chat persists child memory by call-node scope
- **WHEN** assistant chat executes a `workflow_call` node with a stable `conversation_id`
- **THEN** the child workflow SHALL read parent memory plus any previously stored child memory for the same `source_workflow_id + source_node_scope + target_workflow_id`
- **AND** successful completion SHALL update that child-scope memory immediately

#### Scenario: Same top-level run sees updated child memory
- **WHEN** a loop or iteration invokes the same `workflow_call` node multiple times in one run
- **THEN** later invocations in that run SHALL see the child memory updated by earlier invocations

#### Scenario: Workflow test run round-trips child memory scopes
- **WHEN** workflow test run executes a workflow with session memory enabled
- **THEN** nested child memory SHALL round-trip through `sessionMemory.workflowCallScopes`
- **AND** structured-root test runs SHALL also support that nested session memory payload

#### Scenario: Runtimes without stable scope do not persist child memory
- **WHEN** a runtime has no stable conversation or session identifier
- **THEN** child memory SHALL be treated as read-only temporary context for that run
- **AND** no persistent child-memory writeback SHALL occur

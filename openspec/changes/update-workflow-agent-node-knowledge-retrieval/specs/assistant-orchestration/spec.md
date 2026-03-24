## ADDED Requirements

### Requirement: Workflow Agent Node SHALL Support Built-in Autonomous Knowledge Retrieval
`workflow_dag.agent` SHALL support built-in autonomous knowledge retrieval independent from normal `toolNames` configuration.

#### Scenario: KB-enabled agent binds internal kb_search
- **WHEN** an `agent` node sets `knowledgeEnabled=true`
- **THEN** runtime SHALL make an internal `kb_search` tool available to that node
- **AND** the tool SHALL NOT need to appear in `agent.toolNames`

#### Scenario: KB-only agent is accepted
- **WHEN** an `agent` node enables KB and leaves `toolNames` empty
- **THEN** save/compile validation SHALL accept the node
- **AND** runtime SHALL treat KB as a valid callable capability for the node

### Requirement: Workflow Agent Node SHALL Expose Only Node-Level KB Mode And TopK Controls
`workflow_dag.agent` SHALL expose only node-level KB overrides for retrieval mode and top-k.

#### Scenario: Node-level KB overrides are applied internally
- **WHEN** an `agent` node configures `knowledgeMode` and/or `knowledgeTopK`
- **THEN** the model-visible KB tool interface SHALL still accept only `query`
- **AND** runtime SHALL inject the configured `mode/topK` values into the underlying `kb_search` invocation

### Requirement: Workflow Agent Node SHALL Enforce KB Citation Guidance When KB Is Enabled
KB-enabled `agent` nodes SHALL receive explicit citation guidance for knowledge-grounded answers.

#### Scenario: KB-enabled agent receives citation instructions
- **WHEN** `knowledgeEnabled=true`
- **THEN** the node system prompt SHALL instruct the model to use `kb_search` when the answer depends on stored knowledge
- **AND** the prompt SHALL require `[^n]` markers that only reference `kb_search.references`

#### Scenario: KB-disabled agent receives no KB-specific prompt
- **WHEN** `knowledgeEnabled=false`
- **THEN** runtime SHALL NOT append KB-specific prompt instructions to the node

### Requirement: Workflow Validator SHALL Reject kb_search In Agent Tool Names
The workflow validator SHALL reject `kb_search` when it appears inside `agent.toolNames`.

#### Scenario: kb_search appears in agent.toolNames
- **WHEN** a workflow `agent` node includes `kb_search` in `toolNames`
- **THEN** save/compile validation SHALL fail
- **AND** the error SHALL direct the user to enable built-in KB via `knowledgeEnabled`

### Requirement: Workflow Dependency Collection SHALL Treat KB Usage As Implicit kb_search Dependency
Workflow dependency collection SHALL include `kb_search` implicitly for KB-related workflow nodes.

#### Scenario: knowledge_retrieval node requires kb_search implicitly
- **WHEN** a workflow contains a `knowledge_retrieval` node
- **THEN** workflow dependency collection SHALL include `kb_search`

#### Scenario: KB-enabled agent requires kb_search implicitly
- **WHEN** a workflow contains an `agent` node with `knowledgeEnabled=true` in main flow or container body
- **THEN** workflow dependency collection SHALL include `kb_search`
- **AND** internal tool validation SHALL allow that dependency without exposing it in normal tool lists

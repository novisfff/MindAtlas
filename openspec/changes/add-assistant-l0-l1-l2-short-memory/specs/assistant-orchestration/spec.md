## ADDED Requirements

### Requirement: Assistant Runtime SHALL Maintain L0/L1/L2 Memory Layers
Assistant runtime SHALL maintain three short-memory layers to support continuous multi-turn execution context: L0 recent transcript window, L1 conversation incremental summary, and L2 conversation+skill facts.

#### Scenario: Build L0 from recent dialogue window
- **WHEN** assistant starts a new turn
- **THEN** runtime SHALL build L0 from recent user/assistant messages within configured turn and char budgets
- **AND** L0 SHALL remain ephemeral (not persisted)

#### Scenario: Update and load L1 by conversation
- **WHEN** a turn completes with non-terminal-failure status
- **THEN** runtime SHALL update L1 using incremental summary strategy for that conversation
- **AND** subsequent turns SHALL load latest L1 by `conversation_id`

#### Scenario: Store and load L2 by conversation and skill
- **WHEN** a skill turn completes with selected skill context
- **THEN** runtime SHALL update L2 facts for `(conversation_id, skill_name)`
- **AND** runtime SHALL read L2 scoped to current selected skill during next relevant turn

### Requirement: Workflow Start Node SHALL Support Memory Mode
Workflow start configuration SHALL support `memoryMode` with `auto`, `off`, and `structured`, defaulting to `auto` when omitted.

#### Scenario: Default to auto mode
- **WHEN** workflow start config does not provide `memoryMode`
- **THEN** runtime SHALL treat memory mode as `auto`

#### Scenario: Disable memory in off mode
- **WHEN** workflow start config sets `memoryMode=off`
- **THEN** runtime SHALL NOT auto-inject memory into nodes
- **AND** runtime SHALL NOT expose memory fields in `start.*`

#### Scenario: Expose memory fields in structured mode
- **WHEN** workflow start config sets `memoryMode=structured`
- **THEN** runtime SHALL expose `start.memory_recent_dialogue`, `start.memory_conversation_summary`, and `start.memory_skill_facts`
- **AND** runtime SHALL NOT perform automatic memory prompt injection

### Requirement: Auto Mode SHALL Inject Memory Into Eligible LLM Nodes
In `memoryMode=auto`, runtime SHALL inject memory context into eligible conversational LLM execution points only.

#### Scenario: Inject into workflow_dag llm node with hybrid strategy
- **WHEN** workflow_dag executes an `llm` node under `memoryMode=auto`
- **THEN** runtime SHALL inject L0 as dialogue-style `user/assistant` messages
- **AND** runtime SHALL inject L1/L2 through system memory block assembly

#### Scenario: Inject into agent_loop without duplicating L0
- **WHEN** agent_loop executes under `memoryMode=auto`
- **THEN** runtime SHALL inject L1/L2 through system memory block assembly
- **AND** runtime SHALL NOT inject L0 as extra messages

#### Scenario: Skip non-eligible nodes
- **WHEN** runtime executes non-conversational nodes (such as `parameter_extractor`, `tool`, `code_executor`, `if_else`)
- **THEN** runtime SHALL NOT auto-inject memory context

### Requirement: Structured Mode SHALL Expose Memory Fields For Manual Templates
In `memoryMode=structured`, memory SHALL be available only through explicit template references.

#### Scenario: Manual template reference to memory
- **WHEN** a node template contains `{{start.memory_conversation_summary}}`
- **THEN** runtime SHALL resolve it from current memory snapshot
- **AND** runtime SHALL keep auto injection disabled for that execution

#### Scenario: Legacy memory field names are rejected
- **WHEN** a template references `start.memory_l0`, `start.memory_l1`, or `start.memory_l2`
- **THEN** runtime SHALL reject the reference as invalid

### Requirement: Memory Update SHALL Be Non-Blocking
Memory persistence/update failures SHALL NOT break user-visible assistant response flow.

#### Scenario: Memory update failure degrades gracefully
- **WHEN** L1 or L2 update fails after a turn
- **THEN** system SHALL log the failure with diagnostics
- **AND** assistant output and event streaming SHALL continue without raising turn-level failure

## MODIFIED Requirements

### Requirement: Start Field References SHALL Be Mode-Aware
Template references to `start.<field>` SHALL be validated against start input mode and memory mode.

#### Scenario: Text mode reference
- **WHEN** a workflow is in `text` input mode
- **THEN** `start.user_input` SHALL be valid
- **AND** any other business `start.*` reference SHALL be rejected
- **AND** memory references `start.memory_*` SHALL be valid only when `memoryMode=structured`

#### Scenario: Structured mode reference
- **WHEN** a workflow is in `structured` input mode
- **THEN** configured structured field names SHALL be valid in `start.*` references
- **AND** `start.user_input` SHALL be rejected
- **AND** memory references `start.memory_*` SHALL be valid only when `memoryMode=structured`

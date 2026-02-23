## ADDED Requirements

### Requirement: Workflow DAG SHALL Support Code Executor Node
The workflow system SHALL support a new node type `code_executor` that executes inline scripts in Python or JavaScript.

#### Scenario: Configure and run a Python code executor
- **WHEN** a workflow includes a `code_executor` node with `language=python`, valid `inputBindings`, and declared `outputFields`
- **THEN** the runtime SHALL execute the node script via `main(inputs, context)`
- **AND** node output SHALL be available for downstream template references

#### Scenario: Configure and run a JavaScript code executor
- **WHEN** a workflow includes a `code_executor` node with `language=javascript`, valid `inputBindings`, and declared `outputFields`
- **THEN** the runtime SHALL execute the node script via `main(inputs, context)`
- **AND** node output SHALL be available for downstream template references

### Requirement: Code Executor SHALL Enforce Strict Output Contract
Code executor output SHALL strictly match the declared `outputFields` schema.

#### Scenario: Output schema mismatch
- **WHEN** script output has missing fields, extra fields, or type mismatch against `outputFields`
- **THEN** the node execution SHALL fail
- **AND** workflow execution SHALL stop with an error

### Requirement: Code Executor SHALL Run Under Guardrails
Code executor runtime SHALL apply sandbox and resource guardrails for safety and stability.

#### Scenario: Disallowed imports at validation
- **WHEN** a workflow contains code importing non-whitelisted modules or dynamic import mechanisms
- **THEN** workflow validation SHALL fail
- **AND** publish SHALL be blocked

#### Scenario: Runtime timeout or limit exceeded
- **WHEN** script execution exceeds configured timeout or runtime limits
- **THEN** node execution SHALL fail fast
- **AND** workflow execution SHALL stop with an error

### Requirement: Code Executor SHALL Be Available In Container Body
`iteration` and `loop` container body subflows SHALL allow `code_executor` nodes.

#### Scenario: Run code executor inside iteration body
- **WHEN** an iteration body graph contains a valid `code_executor` node
- **THEN** container subflow execution SHALL run the code node successfully
- **AND** body outputs SHALL be usable by container output selectors and downstream nodes

## MODIFIED Requirements

### Requirement: Workflow DAG SHALL Support Code Executor Node
The workflow system SHALL support a `code_executor` node that executes inline scripts in Python or JavaScript using parameter names dynamically matched to `inputBindings`.

#### Scenario: Configure and run a Python code executor with custom signature
- **WHEN** a workflow includes a `code_executor` node with `language=python`, custom `inputBindings` keys, and declared `outputFields`
- **THEN** runtime SHALL execute the script by mapping argument values to parameter names in `main(...)`
- **AND** node output SHALL be available for downstream template references

#### Scenario: Configure and run a JavaScript code executor with custom signature
- **WHEN** a workflow includes a `code_executor` node with `language=javascript`, custom `inputBindings` keys, and declared `outputFields`
- **THEN** runtime SHALL execute the script by mapping argument values to parameter names in `main(...)`
- **AND** node output SHALL be available for downstream template references

#### Scenario: Signature and input binding keys mismatch is rejected
- **WHEN** script parameter names do not exactly match configured `inputBindings` keys
- **THEN** workflow validation SHALL fail
- **AND** workflow publish SHALL be blocked

## ADDED Requirements

### Requirement: Code Executor Editor SHALL Provide Editable Binding UX and Formatting
The workflow editor SHALL provide an authoring experience where default bindings are seeded but fully editable.

#### Scenario: Missing bindings are seeded with default arg1/arg2
- **WHEN** a new code executor node has missing `inputBindings`
- **THEN** editor SHALL seed default keys `arg1` and `arg2`
- **AND** users SHALL be able to add/remove/rename keys after seeding

#### Scenario: Python code formatting uses Ruff
- **WHEN** a user clicks format on a Python code executor script
- **THEN** editor SHALL format code via Ruff WASM
- **AND** formatting errors SHALL not overwrite the original script

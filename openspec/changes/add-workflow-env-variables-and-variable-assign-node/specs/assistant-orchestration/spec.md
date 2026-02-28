## ADDED Requirements

### Requirement: Workflow DAG SHALL Support Session ENV Variables
Workflow DAG SHALL support session-scoped ENV variable definitions from the main graph start node config (`sessionVars` / `session_vars`) and initialize them for every workflow run.

#### Scenario: Initialize env vars at run start
- **WHEN** a workflow start node defines `sessionVars` with valid names, types, and default values
- **THEN** runtime SHALL initialize `env` values from those defaults at the beginning of each run
- **AND** previous run mutations SHALL NOT persist to the next run

### Requirement: Workflow Templates SHALL Resolve `env.<name>` References
Template resolution and condition evaluation SHALL support `env.<name>` references across main graph and container body execution.

#### Scenario: Read env variable from downstream node template
- **WHEN** a node template includes `{{env.counter}}` after the variable has been initialized or updated
- **THEN** runtime SHALL resolve the current session value
- **AND** validator SHALL reject unknown `env.<name>` references

### Requirement: Workflow DAG SHALL Support `variable_assign` Node
Workflow DAG SHALL support a `variable_assign` node type in both main graph and `iteration`/`loop` container body, with operations `set`, `increment`, and `append`.

#### Scenario: Set value
- **WHEN** `variable_assign` is configured with operation `set`
- **THEN** runtime SHALL coerce and assign the resolved value to the target env variable type

#### Scenario: Increment numeric variable
- **WHEN** `variable_assign` uses `increment` for a `number` or `integer` env variable
- **THEN** runtime SHALL add the resolved numeric value to the current variable value
- **AND** runtime SHALL fail if target type is non-numeric

#### Scenario: Append to string or array
- **WHEN** `variable_assign` uses `append` on a `string` env variable
- **THEN** runtime SHALL concatenate the resolved value as string
- **AND WHEN** target type is `array`
- **THEN** runtime SHALL append a single value or extend by list values

### Requirement: ENV Validation SHALL Enforce Schema and Operation Safety
Workflow validation SHALL enforce ENV variable definition schema and `variable_assign` operation/type compatibility.

#### Scenario: Invalid env definition or assign config
- **WHEN** `sessionVars` contain duplicate names, invalid identifiers, or default value/type mismatch
- **OR WHEN** `variable_assign` references an unknown env var or invalid operation/value config
- **THEN** validation SHALL fail with a 422-style invalid workflow error

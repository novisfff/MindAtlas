## ADDED Requirements
### Requirement: Workflow Iteration Node
Workflow DAG SHALL support an `iteration` node that executes an inner container subflow for each item of an input array and aggregates selected outputs.

#### Scenario: Iterate and aggregate outputs
- **WHEN** an `iteration` node receives an array input and valid `bodyNodes/bodyEdges`
- **THEN** the engine executes the container body for each item and aggregates outputs into the configured `outputVariable`

#### Scenario: Iteration error strategy
- **WHEN** `errorStrategy` is `fail_fast` and one item execution fails
- **THEN** the iteration node SHALL fail immediately
- **WHEN** `errorStrategy` is `skip_item`
- **THEN** failed items SHALL be skipped and reported in error metadata

### Requirement: Workflow Loop Node
Workflow DAG SHALL support a `loop` node that repeatedly executes an inner container subflow until termination conditions are met or `maxIterations` is reached.

#### Scenario: Loop terminates by condition
- **WHEN** loop termination conditions evaluate to true under configured logic
- **THEN** loop execution SHALL stop before reaching `maxIterations`

#### Scenario: Loop max iteration guard
- **WHEN** termination conditions are never met
- **THEN** loop execution SHALL stop at `maxIterations`

### Requirement: Container Subflow Constraints
Container nodes (`iteration`/`loop`) SHALL store inner subflow definitions in node config and enforce topology and nesting constraints at save-time.

#### Scenario: Body topology validation
- **WHEN** `bodyNodes/bodyEdges` contains duplicate ids, invalid references, or cycles
- **THEN** workflow validation SHALL fail with explicit errors

#### Scenario: No nested container nodes
- **WHEN** a container body includes `iteration` or `loop` nodes
- **THEN** workflow validation SHALL reject the workflow

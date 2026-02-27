## ADDED Requirements

### Requirement: Workflow DAG SHALL Support Human Approval Gate Node
Workflow DAG SHALL support a `human_in_loop` node type in both main graph and `iteration`/`loop` container body, allowing runtime to pause for human decision.

#### Scenario: Node available in main graph and container body
- **WHEN** a user adds workflow nodes in main graph or container body
- **THEN** `human_in_loop` SHALL be selectable and serializable in workflow config

### Requirement: Human Approval Node SHALL Enforce Dual Branch Routing
A `human_in_loop` node SHALL route execution by decision handle and MUST provide exactly one outgoing edge for each handle: `approved` and `rejected`.

#### Scenario: Missing required branch handle edge
- **WHEN** a `human_in_loop` node is missing `approved` or `rejected` outgoing edge
- **THEN** workflow validation SHALL fail

#### Scenario: Reject decision routes to rejected branch
- **WHEN** user submits decision `rejected`
- **THEN** runtime SHALL continue execution through `rejected` branch
- **AND** run SHALL NOT be marked as runtime error solely due to rejection

### Requirement: Human Approval Requests SHALL Be Persisted and Resolvable
Runtime SHALL persist pending human approvals and allow external decision submission through API.

#### Scenario: Pending approval survives page refresh
- **WHEN** an approval is created and UI reloads
- **THEN** pending approval SHALL be queryable and renderable for user action

#### Scenario: Decision submission resumes run
- **WHEN** client submits approve/reject decision with field values
- **THEN** runtime SHALL validate and store decision
- **AND** blocked node execution SHALL resume with resolved decision payload

### Requirement: Workflow Test-Run and Assistant Chat SHALL Emit HITL SSE Events
Workflow test-run and assistant chat streams SHALL emit human approval lifecycle events for UI synchronization.

#### Scenario: Approval requested and resolved events
- **WHEN** runtime reaches a `human_in_loop` node
- **THEN** stream SHALL emit `human_approval_requested`
- **AND WHEN** decision is submitted
- **THEN** stream SHALL emit `human_approval_resolved`

### Requirement: Human Approval UI SHALL Support Editable Field Form and Approve/Reject
Frontend UI SHALL render approval cards with editable schema fields, approve/reject actions, and optional reject comment validation.

#### Scenario: Approve with edited values
- **WHEN** user edits one or more fields and clicks approve
- **THEN** UI SHALL submit `approved` with current field values

#### Scenario: Reject requires comment when enabled
- **WHEN** node config enables `requireRejectComment`
- **AND** user chooses reject without comment
- **THEN** UI SHALL block submission and show validation error

## ADDED Requirements

### Requirement: Workflow DAG SHALL Support HTTP Request Node
The workflow system SHALL support a node type `http_request` in main DAG and container body subflows (`iteration` and `loop`).

#### Scenario: Add and run http_request in main DAG
- **WHEN** a workflow includes a valid `http_request` node in the main graph
- **THEN** workflow validation SHALL pass
- **AND** runtime SHALL execute the HTTP request and expose node output for downstream references

#### Scenario: Add and run http_request in container body
- **WHEN** an `iteration` or `loop` body includes a valid `http_request` node
- **THEN** workflow validation SHALL pass for container body topology and config
- **AND** container runtime SHALL execute the node successfully

### Requirement: HTTP Request Node SHALL Provide Fixed Output Contract
`http_request` node output SHALL expose fixed `json_fields`: `body`, `status_code`, `headers`, `ok`, `error_message`, and `response`.

#### Scenario: Successful response mapping
- **WHEN** `http_request` receives a 2xx response
- **THEN** output SHALL set `ok=true`
- **AND** `body` and `response` SHALL contain response text
- **AND** `status_code`/`headers` SHALL reflect HTTP response metadata

#### Scenario: HTTP error response mapping
- **WHEN** `http_request` receives a 4xx or 5xx response
- **THEN** workflow SHALL continue without transport exception
- **AND** output SHALL set `ok=false`
- **AND** `error_message` SHALL contain HTTP status information

### Requirement: HTTP Request Node SHALL Enforce Safe Runtime Controls
`http_request` runtime SHALL apply SSRF checks, timeout bounds, optional retry policy, and SSL verify toggle.

#### Scenario: SSRF or transport-level failure
- **WHEN** request fails due blocked URL or transport/network timeout and retries are exhausted
- **THEN** node execution SHALL fail with runtime error
- **AND** workflow execution SHALL stop

#### Scenario: Retry behavior
- **WHEN** retry is enabled and request fails with transport error or HTTP 5xx
- **THEN** runtime SHALL retry up to configured `maxRetries` with `retryIntervalMs`
- **AND** runtime SHALL not retry HTTP 4xx responses

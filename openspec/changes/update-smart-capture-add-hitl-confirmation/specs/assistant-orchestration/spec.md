## MODIFIED Requirements

### Requirement: smart_capture workflow must require human confirmation before record creation
The system default `smart_capture` workflow SHALL gate `create_entry` with a `human_in_loop` approval node so users can review and edit final write payload fields before persistence.

#### Scenario: Approved decision continues to create_entry
- **WHEN** `smart_capture` reaches the `human_confirm` node and the user submits `approved`
- **THEN** the workflow SHALL route through the `approved` handle to `tool_create`
- **AND** `tool_create.inputBindings` SHALL read from `human_confirm` output fields
- **AND** entry creation SHALL use reviewed values instead of raw upstream LLM outputs

#### Scenario: Rejected decision cancels creation
- **WHEN** `smart_capture` reaches the `human_confirm` node and the user submits `rejected`
- **THEN** the workflow SHALL route through the `rejected` handle to a cancel-response path
- **AND** the workflow SHALL NOT call `create_entry`
- **AND** the final output SHALL inform the user that creation was canceled

#### Scenario: Confirmation form covers full create payload
- **WHEN** `smart_capture` renders the human confirmation form
- **THEN** it SHALL expose editable fields for `title`, `summary`, `content`, `type_code`, `tags`, `time_mode`, `time_at`, `time_from`, and `time_to`
- **AND** `tags` SHALL be represented as comma-separated text for editing

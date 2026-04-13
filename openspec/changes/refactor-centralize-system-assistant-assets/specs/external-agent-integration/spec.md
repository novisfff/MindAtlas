## ADDED Requirements

### Requirement: OpenClaw System Workflow Items SHALL Reference Central Assistant Assets
Workflow-backed OpenClaw system items SHALL reference assistant-owned system workflow assets by `asset_key` and canonical target instead of embedding duplicate workflow asset content.

#### Scenario: Context capture capability resolves shared workflow asset
- **WHEN** OpenClaw prepares the `submit_context_capture` system capability
- **THEN** it SHALL reference the shared `context_capture` workflow asset managed by assistant-config
- **AND** the executed persisted target SHALL remain the canonical `system_context_capture__workflow`

#### Scenario: OpenClaw keeps capability metadata but not asset truth
- **WHEN** OpenClaw exposes localized titles, descriptions, schemas, or tool names for a workflow-backed system capability
- **THEN** it MAY keep that capability metadata in the OpenClaw registry
- **AND** it SHALL not own a duplicate preset file path or duplicate workflow asset payload for the referenced system workflow

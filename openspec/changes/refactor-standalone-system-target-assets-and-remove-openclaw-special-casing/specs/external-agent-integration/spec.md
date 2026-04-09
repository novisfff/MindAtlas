## MODIFIED Requirements

### Requirement: OpenClaw System Items SHALL Support Copy-First Customization Over Immutable System Targets
OpenClaw system items SHALL remain editable at the binding layer even when they point to immutable system workflow or agent targets, and the UI SHALL direct administrators to duplicate those targets before customizing them.

#### Scenario: Workflow-backed OpenClaw system item binds through a shared standalone system workflow asset
- **WHEN** the shipped `submit_context_capture` system item is seeded, reset, or executed
- **THEN** it SHALL bind to assistant-config's canonical standalone system workflow asset
- **AND** OpenClaw SHALL not own a private workflow preset materialization path for that workflow-backed system item

#### Scenario: OpenClaw surfaces display names for shared system targets
- **WHEN** administrators view OpenClaw workflow or agent sources in settings
- **THEN** the source picker and bound-source summary SHALL show the assistant-config display name for the bound system target
- **AND** they SHALL not expose the raw canonical workflow or agent name as the user-facing source label

## MODIFIED Requirements

### Requirement: Assistant Tool Resolution SHALL Distinguish Visible System Tools From Hidden Compatibility Aliases
The system SHALL expose a canonical visible system tool surface for assistant configuration while continuing to resolve hidden compatibility aliases needed by existing workflows and agents.

#### Scenario: Visible system tools exclude adapter-only aliases
- **WHEN** the application lists built-in system tools for configuration or settings UI
- **THEN** it SHALL return only canonical tool names
- **AND** it SHALL exclude hidden adapter-only aliases such as legacy `openclaw_*` wrapper names

#### Scenario: Persisted workflow keeps resolving a hidden alias
- **WHEN** an existing workflow or agent references a hidden compatibility alias such as `openclaw_search_entries`
- **THEN** validation SHALL still treat that tool name as resolvable
- **AND** runtime execution SHALL continue to execute the compatible implementation

#### Scenario: Canonical relation, graph, and report tools are available
- **WHEN** the application loads visible system tool definitions
- **THEN** the canonical tool surface SHALL include relation creation, knowledge graph query, weekly report generation, and monthly report generation tools without OpenClaw-specific prefixes

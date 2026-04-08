## ADDED Requirements

### Requirement: OpenClaw System Items SHALL Support Copy-First Customization Over Immutable System Targets
OpenClaw system items SHALL remain editable at the binding layer even when they point to immutable system workflow or agent targets, and the UI SHALL direct administrators to duplicate those targets before customizing them.

#### Scenario: System item bound to system workflow or agent
- **WHEN** an administrator edits an OpenClaw system item whose current source is an `is_system=true` workflow or agent
- **THEN** the administrator SHALL still be allowed to keep or change the binding
- **AND** the UI SHALL indicate that the bound system target itself is read-only
- **AND** the UI SHALL direct the administrator to copy that target before editing it

#### Scenario: OpenClaw executes a system-target-backed item
- **WHEN** OpenClaw executes a catalog item bound to a system workflow or system agent
- **THEN** the integration SHALL execute the currently published canonical baseline target
- **AND** it SHALL not require any special mutable source path for system-owned targets

#### Scenario: Reset system items preserves user-owned copies
- **WHEN** administrators reset shipped OpenClaw system items to defaults
- **THEN** the shipped item metadata and default bindings SHALL be restored
- **AND** any user-created workflow or agent copies used for customization SHALL remain unchanged

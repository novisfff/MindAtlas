## MODIFIED Requirements

### Requirement: Shipped OpenClaw System Items SHALL Behave As First-Class Catalog Items
The system SHALL treat shipped OpenClaw defaults as first-class catalog items while keeping the shipped capability surface aligned to the current official OpenClaw product contract.

#### Scenario: The shipped OpenClaw entry-create surface is a single smart capability
- **WHEN** the system seeds or resets shipped OpenClaw system items
- **THEN** the shipped capability surface SHALL expose `submit_context_capture` as the only official entry-creation capability
- **AND** the older field-level `capture_entry` system item SHALL no longer be recreated or returned as a shipped capability

#### Scenario: Obsolete shipped system items are removed during sync
- **WHEN** a newer build sees an older system item whose `system_default_key` is no longer part of the shipped OpenClaw registry
- **THEN** the obsolete shipped system item SHALL be removed during system-item sync or reset
- **AND** the remaining shipped system items SHALL continue using the current canonical defaults

#### Scenario: Legacy field-level capture bindings are retired instead of executed
- **WHEN** an existing custom OpenClaw catalog item is still bound to the retired `openclaw_capture_entry` source
- **THEN** the item SHALL remain visible in the settings catalog for inspection or rebinding
- **AND** it SHALL be marked as retired with a clear reason
- **AND** it SHALL not appear in OpenClaw runtime capability discovery
- **AND** direct execution attempts SHALL be rejected

### Requirement: OpenClaw Capability Execution SHALL Reuse MindAtlas Business Services
Each exposed catalog item SHALL route into existing MindAtlas services or runners instead of duplicating business logic.

#### Scenario: Smart capture remains thin-context input with richer output
- **WHEN** OpenClaw executes the shipped `submit_context_capture` capability
- **THEN** MindAtlas SHALL keep accepting the thin-context request contract
- **AND** the structured result SHALL include the created entry identity, type, summary, tags, time fields, and created time needed by downstream automation

#### Scenario: Smart capture availability matches entry-type readiness
- **WHEN** the system has no enabled entry types
- **THEN** the shipped `submit_context_capture`, `search_entries`, and `get_entry` capabilities SHALL all be reported as unavailable with a clear reason
- **AND** the runtime execution path SHALL refuse execution instead of failing later with an internal error

### Requirement: OpenClaw Integration SHALL Be Configurable From A Dedicated Settings Page
The application SHALL provide a dedicated settings page for OpenClaw integration management.

#### Scenario: Retired legacy items stay visible in settings
- **WHEN** the user opens the OpenClaw integration settings page and a legacy retired catalog item still exists
- **THEN** the page SHALL display that item with a retired state and a clear explanation
- **AND** the page SHALL not present it as an actively exposed runtime capability
- **AND** the user SHALL still be able to inspect it, delete it, or rebind it to another supported source

## MODIFIED Requirements

### Requirement: OpenClaw Catalog SHALL Stay In Sync With System Items
The external integration catalog SHALL ensure system-backed items are available before capability execution, and the OpenClaw plugin surface SHALL preserve dedicated capability tools while exposing a stable dispatcher fallback for newly exposed capabilities.

#### Scenario: First execution after system item changes
- **WHEN** runtime settings or system targets changed since the last catalog snapshot
- **THEN** the integration service SHALL reconcile system items before resolving the requested capability

#### Scenario: Dispatcher lists the latest available capabilities
- **WHEN** the plugin has valid MindAtlas config and `mindatlas_list_capabilities` is called
- **THEN** it SHALL refresh the remote catalog before returning results
- **AND** it SHALL include whether each available capability already has a dedicated session-visible tool

#### Scenario: Dispatcher executes a newly exposed capability
- **WHEN** a matching capability is available in the refreshed catalog but is not yet a dedicated session-visible tool
- **THEN** `mindatlas_run_capability` SHALL execute that capability by `capabilityKey`
- **AND** existing dedicated tool reload or stale semantics SHALL remain unchanged

### Requirement: OpenClaw MindAtlas Skills SHALL Route Across Custom Exposed Capabilities
The shipped MindAtlas skills SHALL guide OpenClaw across both built-in dedicated tools and administrator-exposed custom capabilities.

#### Scenario: Overview escalates to dispatcher for custom capability discovery
- **WHEN** the current session lacks a clearly matching dedicated `mindatlas_*` tool for a MindAtlas request
- **THEN** the overview skill SHALL direct OpenClaw to inspect the latest capability catalog through the dispatcher path
- **AND** it SHALL only say MindAtlas is not exposed when neither dedicated tools nor dispatcher tools are available

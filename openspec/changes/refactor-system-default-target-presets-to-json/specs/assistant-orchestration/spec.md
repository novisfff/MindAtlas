## ADDED Requirements

### Requirement: System Default Targets SHALL Be Loaded From JSON Presets
The system SHALL load built-in workflow and agent default presets from JSON files in the backend code directory instead of Python hardcoded constants.

#### Scenario: Load system defaults from manifest + preset files
- **WHEN** system default skill definitions are initialized
- **THEN** the system SHALL read `system_defaults/manifest.json`
- **AND** SHALL resolve each referenced preset file under `system_defaults/`
- **AND** SHALL build runtime `SkillDefinition` objects from these JSON files

### Requirement: System Default JSON Loading SHALL Fail Fast On Invalid Data
The system SHALL reject startup/first-load when default preset JSON is missing or malformed.

#### Scenario: Missing preset file in manifest
- **WHEN** a manifest entry references a non-existing preset JSON file
- **THEN** initialization SHALL fail immediately with a clear error
- **AND** the system SHALL NOT fallback to legacy hardcoded defaults

#### Scenario: Invalid schema content
- **WHEN** manifest or preset JSON violates required schema fields/types
- **THEN** initialization SHALL fail immediately with a clear error

### Requirement: Compatibility Exports SHALL Remain Stable
The system SHALL preserve compatibility for existing imports using `definitions.py` symbols.

#### Scenario: Named constant compatibility
- **WHEN** code imports `QUICK_STATS`, `SMART_CAPTURE`, `PERIODIC_REVIEW`, or `GENERAL_CHAT`
- **THEN** these symbols SHALL still resolve to valid `SkillDefinition` objects loaded from JSON presets

### Requirement: System Baseline Rollback SHALL Use Canonical JSON Baseline
For system workflow/agent targets, baseline rollback SHALL restore draft content from canonical JSON defaults.

#### Scenario: Workflow baseline rollback from legacy earliest publish snapshot
- **WHEN** a system workflow baseline version is selected for rollback and that historical snapshot differs from current defaults
- **THEN** rollback SHALL restore draft graph from JSON baseline preset
- **AND** returned draft payload SHALL match JSON baseline coordinates/config

#### Scenario: Agent baseline rollback from legacy earliest publish snapshot
- **WHEN** a system agent baseline version is selected for rollback and that historical snapshot differs from current defaults
- **THEN** rollback SHALL restore draft agent config from JSON baseline preset
- **AND** returned draft payload SHALL match JSON baseline prompt/tools/kb/model config

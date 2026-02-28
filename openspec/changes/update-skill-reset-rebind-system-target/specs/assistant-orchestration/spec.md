## ADDED Requirements

### Requirement: System Skill Reset SHALL Rebind To Canonical System Targets
When resetting a system skill, the system SHALL rebind the skill to its canonical system target (`<skill_name>__workflow` or `<skill_name>__agent`) and SHALL NOT mutate user-created targets.

#### Scenario: Reset system skill currently bound to custom workflow
- **WHEN** `POST /skills/{id}/reset` is called for a system workflow skill that currently binds a non-system workflow
- **THEN** the skill SHALL be rebound to a system workflow target
- **AND** the previously bound custom workflow SHALL remain unchanged

#### Scenario: Reset system skill currently bound to custom agent
- **WHEN** `POST /skills/{id}/reset` is called for a system agent skill that currently binds a non-system agent
- **THEN** the skill SHALL be rebound to a system agent target
- **AND** the previously bound custom agent profile SHALL remain unchanged

### Requirement: System Skill Reset SHALL Reapply Baseline Configuration And Collapse History
Resetting a system skill SHALL restore system target runtime configuration from JSON baseline defaults and keep only the reset-generated publish version for that target.

#### Scenario: Reset workflow target
- **WHEN** a system workflow skill is reset
- **THEN** the target workflow graph SHALL be restored from JSON baseline defaults
- **AND** a publish version SHALL be created
- **AND** all other versions for that target SHALL be removed
- **AND** draft/published heads SHALL both point to the kept publish version

#### Scenario: Reset agent target
- **WHEN** a system agent skill is reset
- **THEN** target agent runtime fields (`system_prompt/tools/kb/model`) SHALL be restored from JSON baseline defaults
- **AND** a publish version SHALL be created
- **AND** all other versions for that target SHALL be removed
- **AND** draft/published heads SHALL both point to the kept publish version

### Requirement: Bulk Reset SHALL Reuse Single-Reset Semantics
Bulk reset of system skills SHALL apply the same target rebinding and baseline restore semantics as single reset for each system skill.

#### Scenario: Reset all system skills
- **WHEN** `POST /skills/reset-all` is executed with confirmation
- **THEN** each active system skill SHALL be reset using canonical-system-target rebinding and baseline restore behavior
- **AND** stale system skill records not present in defaults MAY be removed
- **AND** user-created targets SHALL NOT be mutated by reset operations

### Requirement: Reset UX SHALL Require Two-Step Dangerous Confirmation
The UI SHALL require a two-step destructive confirmation for both single reset and reset-all operations, including typed `RESET` confirmation.

#### Scenario: User confirms reset in UI
- **WHEN** user initiates reset from skill settings UI
- **THEN** UI SHALL first display a high-risk warning describing reset impact
- **AND** UI SHALL require user to type `RESET` exactly before final confirmation is enabled

### Requirement: Reset i18n SHALL Not Leak Raw Keys
Reset-related labels and warnings SHALL be localized in skill settings pages.

#### Scenario: Render reset-all action label
- **WHEN** skill settings page is rendered in any supported locale
- **THEN** the reset-all action label SHALL show localized text
- **AND** SHALL NOT render untranslated key strings (for example `settings.skills.resetAll`)

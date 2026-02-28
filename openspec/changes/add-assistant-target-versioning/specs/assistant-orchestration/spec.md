## ADDED Requirements

### Requirement: Workflow and Agent Targets SHALL Maintain Draft/Published Version Heads
The system SHALL track both draft and published version heads for each workflow target and agent target.

#### Scenario: Initialize version heads during migration
- **WHEN** existing workflow or agent records are migrated to version-aware schema
- **THEN** the system SHALL create an initial `publish` version snapshot and set both `draft_version_id` and `published_version_id` to that version

### Requirement: Save SHALL Create Draft Versions Without Affecting Runtime
Saving workflow/agent editor content SHALL create a `save` version and advance only the draft head.

#### Scenario: Save workflow draft
- **WHEN** user saves workflow editor content through `PUT /workflows/{id}`
- **THEN** the system SHALL validate the workflow, create a `save` version, and update only `draft_version_id`
- **AND** published workflow graph used by runtime SHALL remain unchanged

#### Scenario: Save agent draft
- **WHEN** user saves agent editor runtime fields through `PUT /agents/{id}`
- **THEN** the system SHALL create a `save` version and update only `draft_version_id`
- **AND** published agent runtime fields used by runtime SHALL remain unchanged

### Requirement: Save And Publish SHALL Update Published Runtime State
Publishing SHALL write a `publish` version and update both draft and published heads.

#### Scenario: Publish workflow
- **WHEN** user calls `POST /workflows/{id}/publish` with current editor graph
- **THEN** the system SHALL validate and apply the graph to published workflow storage
- **AND** SHALL create a `publish` version and point both draft and published heads to it

#### Scenario: Publish agent
- **WHEN** user calls `POST /agents/{id}/publish` with current draft runtime config
- **THEN** the system SHALL validate and apply agent runtime config to published profile fields
- **AND** SHALL create a `publish` version and point both draft and published heads to it

### Requirement: Version History and Rollback SHALL Be Available For Both Target Types
The system SHALL provide version history listing and rollback-to-draft APIs for workflow and agent targets.

#### Scenario: List workflow versions
- **WHEN** client calls `GET /workflows/{id}/versions`
- **THEN** response SHALL include ordered version records and current draft/published head IDs

#### Scenario: Rollback workflow draft
- **WHEN** client calls `POST /workflows/{id}/versions/{version_id}/rollback`
- **THEN** the system SHALL set workflow draft head to the selected version
- **AND** SHALL return restored draft workflow payload
- **AND** SHALL NOT change published head

#### Scenario: Rollback agent draft
- **WHEN** client calls `POST /agents/{id}/versions/{version_id}/rollback`
- **THEN** the system SHALL set agent draft head to the selected version
- **AND** SHALL return restored draft agent payload
- **AND** SHALL NOT change published head

### Requirement: Version Retention SHALL Be Bounded Per Target
The system SHALL keep a bounded number of versions per target while preserving active heads.

#### Scenario: Exceed retention threshold
- **WHEN** a target accumulates more than 100 versions
- **THEN** the system SHALL remove older versions beyond the threshold
- **AND** SHALL preserve versions currently referenced by draft/published heads

#### Scenario: Preserve system baseline publish version
- **WHEN** a system workflow or system agent target exceeds retention threshold
- **THEN** the system SHALL preserve its earliest `publish` version in addition to active heads

### Requirement: System Targets SHALL Expose A Pinned “System Default” Entry In Version History UI
The UI SHALL show a highlighted “system default” entry for system workflow/agent targets and pin it to the top of the version history list.

#### Scenario: Show pinned system default entry
- **WHEN** user opens version history for a system workflow or system agent
- **THEN** UI SHALL resolve earliest `publish` version as “system default”
- **AND** SHALL render it first with distinct visual style
- **AND** SHALL keep restore-to-draft action available

### Requirement: Version History SHALL Support Deletion With Protection Rules
The system SHALL allow deleting historical versions while preventing deletion of protected versions.

#### Scenario: Delete non-protected workflow version
- **WHEN** client calls `DELETE /workflows/{id}/versions/{version_id}` for a version that is not draft, not published, and not system baseline
- **THEN** the version SHALL be deleted
- **AND** draft/published pointers SHALL remain unchanged

#### Scenario: Reject protected version deletion
- **WHEN** client attempts to delete a version referenced by draft/published head, or earliest `publish` version of a system target
- **THEN** the system SHALL reject with `409`

### Requirement: Version History SHALL Support Draft Cleanup
The system SHALL provide a cleanup action that only removes obsolete draft-save versions.

#### Scenario: Clear workflow draft history
- **WHEN** client calls `POST /workflows/{id}/versions/clear`
- **THEN** the system SHALL delete only versions where `version_source=save` and version is not the latest
- **AND** SHALL preserve protected versions (draft/published/system baseline)

#### Scenario: Clear agent draft history
- **WHEN** client calls `POST /agents/{id}/versions/clear`
- **THEN** the system SHALL delete only versions where `version_source=save` and version is not the latest
- **AND** SHALL preserve protected versions (draft/published/system baseline)

### Requirement: Workflow Publish SHALL Be Blocked By Backend Validation Failure
Workflow publish MUST pass backend validation at publish time.

#### Scenario: Block publish on invalid workflow
- **WHEN** client calls `POST /workflows/{id}/publish` with invalid topology/parallel/dependency state
- **THEN** the system SHALL return `422`
- **AND** SHALL NOT create a publish version
- **AND** SHALL NOT move `published_version_id`

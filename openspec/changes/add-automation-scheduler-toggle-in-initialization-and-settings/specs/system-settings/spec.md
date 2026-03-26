## ADDED Requirements
### Requirement: Initialization SHALL expose the automation scheduler toggle
The system SHALL allow users to choose whether background automation scheduling is enabled during initialization and persist that choice as part of runtime automation config.

#### Scenario: Initialization submits scheduler toggle explicitly
- **WHEN** a user completes initialization with automation scheduling enabled or disabled
- **THEN** the initialization request includes `runtimeConfig.automation.schedulerEnabled`
- **AND** the persisted automation runtime config reflects the chosen value rather than falling back to environment defaults

### Requirement: Settings SHALL provide a dedicated automation management page
The system SHALL provide a dedicated Automation settings page alongside the existing runtime capability settings pages.

#### Scenario: User opens automation settings from settings surfaces
- **WHEN** a user navigates from Settings home or System Setup
- **THEN** the user can open `/settings/automation`
- **AND** the page shows the scheduler toggle and the list of affected system jobs

### Requirement: Automation scheduler changes SHALL hot-apply on the current instance
The system SHALL synchronize the current backend instance scheduler immediately after automation runtime config is updated.

#### Scenario: Enable scheduler from settings
- **WHEN** a runtime automation config update turns `schedulerEnabled` from false to true
- **THEN** the current backend instance starts the scheduler if needed
- **AND** the weekly and monthly report jobs are registered exactly once

#### Scenario: Disable scheduler from settings
- **WHEN** a runtime automation config update turns `schedulerEnabled` from true to false
- **THEN** the current backend instance stops the scheduler and clears registered jobs

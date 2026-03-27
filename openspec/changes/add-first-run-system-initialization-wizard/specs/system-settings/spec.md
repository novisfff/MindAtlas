## ADDED Requirements
### Requirement: First-Run Initialization Gate
The system SHALL determine whether the app has completed first-run initialization and block normal app navigation until setup is complete for fresh installs.

#### Scenario: Fresh install is redirected to initialization
- **WHEN** the app has no initialization state and no legacy usage/customization signals
- **THEN** the frontend routes the user to `/initialize`
- **AND** normal dashboard, assistant, and settings routes remain inaccessible until initialization succeeds

#### Scenario: Legacy install is auto-completed
- **WHEN** the app has no initialization state but has clear usage or configuration signals from an existing system
- **THEN** the backend persists an initialized state automatically
- **AND** the frontend does not force the user through `/initialize`

### Requirement: Locale-Aware Initialization Defaults
The system SHALL provide locale-aware initialization defaults for entry types and apply locale-aware default relation metadata during initialization.

#### Scenario: Initialization defaults follow selected language
- **WHEN** the frontend requests initialization defaults with `locale=zh` or `locale=en`
- **THEN** the backend returns entry type templates in that language
- **AND** the templates preserve stable machine codes and structural flags across locales

### Requirement: Atomic Initialization Submission
The system SHALL apply first-run initialization in one transaction.

#### Scenario: Initialization completes successfully
- **WHEN** the user submits locale, AI credential, LLM model, and entry type selections from the initialization wizard
- **THEN** the backend persists system locale, AI credential, LLM model, default LLM bindings, localized entry/relation defaults, system-owned defaults, and initialization state together
- **AND** the frontend redirects the user to the dashboard

#### Scenario: Initialization submission fails
- **WHEN** any required initialization step fails during backend processing
- **THEN** the backend rolls back all initialization writes
- **AND** the system remains uninitialized

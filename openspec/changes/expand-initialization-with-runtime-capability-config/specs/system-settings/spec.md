## ADDED Requirements
### Requirement: Initialization Capability Center
The system SHALL extend first-run initialization with an optional capability center for user-facing runtime modules after the required language, AI model, and entry type steps.

#### Scenario: User completes only the core setup path
- **WHEN** a fresh system user completes language, AI model, and entry type setup but skips all optional capability modules
- **THEN** initialization still succeeds
- **AND** skipped capability modules remain unresolved through env fallback or default-unconfigured behavior
- **AND** the completion summary shows which modules were skipped

#### Scenario: User configures capability modules during initialization
- **WHEN** a user fills one or more runtime capability modules before finishing initialization
- **THEN** the submitted initialization payload persists those module settings atomically with the rest of initialization
- **AND** a failure in any persisted capability module rolls back the whole initialization submission

### Requirement: Runtime Capability Config Resolution
The system SHALL resolve user-facing runtime capability configuration through grouped persisted settings with env fallback and encrypted secret handling.

#### Scenario: App config overrides env
- **WHEN** a runtime capability group has persisted app-level values
- **THEN** runtime consumers use the persisted values instead of env defaults
- **AND** secret fields are read from encrypted storage rather than returned in plaintext

#### Scenario: Missing app config falls back to env
- **WHEN** a runtime capability group has no persisted app-level value
- **THEN** runtime consumers continue using the existing env-backed behavior
- **AND** the frontend surfaces that module as using an environment default when applicable

### Requirement: System Setup Management
The system SHALL provide a post-initialization `System Setup` settings surface that reuses the runtime capability modules and clearly communicates value sources and restart expectations.

#### Scenario: User edits a runtime capability after initialization
- **WHEN** the user opens `Settings > System Setup` and saves one module
- **THEN** only that module’s app-level runtime config is updated
- **AND** the response indicates whether the effective value comes from app config or environment defaults
- **AND** any restart/reload expectations are shown in the UI

### Requirement: Friendly Missing Capability Behavior
The system SHALL fail with explicit, user-understandable behavior when runtime capabilities are unavailable instead of returning generic internal errors.

#### Scenario: Attachment storage is not configured
- **WHEN** a user tries to upload or fetch attachments without usable object storage configuration
- **THEN** the backend returns a clear not-configured error
- **AND** the frontend can direct the user to `System Setup`

#### Scenario: Knowledge graph is skipped or incomplete
- **WHEN** a user opens graph-related experiences or knowledge-graph-powered entry actions without a usable knowledge graph configuration
- **THEN** the frontend shows an explicit empty or disabled state
- **AND** the backend does not surface a generic 500 for missing runtime capability configuration

## ADDED Requirements

### Requirement: The Application SHALL Persist A Global System Locale
The system SHALL persist a global application locale with supported values `zh` and `en`, and SHALL use it as the default language for system-owned AI behavior when no request-specific locale is provided.

#### Scenario: Interactive request overrides persisted locale
- **WHEN** an interactive HTTP request includes `X-MindAtlas-Locale`
- **THEN** execution for that request SHALL use the header locale
- **AND** the persisted global locale SHALL remain unchanged until explicitly updated

#### Scenario: Background execution uses persisted locale
- **WHEN** a scheduler job or other non-interactive system execution runs without a request locale
- **THEN** the system SHALL use the persisted `system_locale`
- **AND** if no persisted locale exists it SHALL fallback to `APP_DEFAULT_LOCALE`

### Requirement: System AI Execution SHALL Receive Locale-Aware Runtime Context
Assistant chat, workflow test runs, agent test runs, and system AI behavior execution SHALL all resolve locale through the same backend locale resolver and expose locale-aware runtime variables.

#### Scenario: Workflow runtime receives locale variables
- **WHEN** a workflow executes through any supported runtime entry point
- **THEN** execution context SHALL include `sys.locale` and `sys.language`
- **AND** model-visible wrapper prompts SHALL instruct the model to respond in the resolved language by default

#### Scenario: Assistant chat follows current locale
- **WHEN** assistant chat is executed under the current system locale
- **THEN** the assistant runtime context SHALL carry that locale
- **AND** fallback and error copy SHALL be localized consistently

### Requirement: System-Owned Defaults SHALL Materialize In The Active Locale
System skill defaults and system AI behavior default targets SHALL support localized preset sources and SHALL materialize the active locale when reset, sync, or example-creation flows rebuild them.

#### Scenario: Resetting a system behavior default workflow
- **WHEN** a system AI behavior binding is reset while the active locale is `zh` or `en`
- **THEN** the rebuilt canonical default workflow SHALL use the matching localized description, node labels, and prompts
- **AND** its executable graph structure SHALL remain equivalent across locales

#### Scenario: Resetting all system skills
- **WHEN** the user triggers reset-all for system skills
- **THEN** each rebuilt system-owned target SHALL use the current locale's preset content
- **AND** user-created targets SHALL NOT be rewritten just because the locale changed

### Requirement: Reports SHALL Track Content Locale And Regenerate On Locale Mismatch
Weekly and monthly report records SHALL store the locale used to generate their content, and SHALL regenerate the current period when the stored locale differs from the resolved system locale.

#### Scenario: Completed report locale mismatch
- **WHEN** a completed weekly or monthly report exists for the current period
- **AND** its `content_locale` differs from the resolved system locale
- **THEN** the next generate request SHALL overwrite that period's content with a new result in the current locale

#### Scenario: Report API exposes content locale
- **WHEN** report data is returned from list, latest, or generate APIs
- **THEN** the response SHALL include `contentLocale`

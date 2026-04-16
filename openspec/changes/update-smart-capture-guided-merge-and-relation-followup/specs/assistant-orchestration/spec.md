## MODIFIED Requirements

### Requirement: smart_capture workflow must require staged human guidance before record persistence
The system default `smart_capture` workflow SHALL pre-search similar entries from raw input, conditionally ask the user whether to create a new entry or merge into a selected existing one, require a second human confirmation before `create_entry` or `update_entry`, and optionally follow successful persistence with batch relation confirmation.

#### Scenario: No similar candidates goes straight to create confirmation
- **WHEN** `smart_capture` cannot find viable similar candidates from its precise and broad raw-input lookups
- **THEN** it SHALL skip the first merge-triage approval step
- **AND** it SHALL still materialize a create payload and require human confirmation before calling `create_entry`

#### Scenario: Similar candidates require create-vs-merge triage
- **WHEN** `smart_capture` finds ranked similar candidates from its raw-input lookups
- **THEN** it SHALL pause for human triage between `create_new` and `merge_existing`
- **AND** if the user chooses `merge_existing`, the workflow SHALL require a selected target before continuing

#### Scenario: Merge path still requires human-confirmed rewrite
- **WHEN** the user chooses `merge_existing` and selects a target entry
- **THEN** `smart_capture` SHALL fetch that existing entry, rewrite a merged payload conservatively, and require human confirmation before calling `update_entry`

#### Scenario: Rejected triage or write confirmation cancels persistence
- **WHEN** the user rejects the triage step, rejects the create confirmation, or rejects the merge confirmation
- **THEN** `smart_capture` SHALL stop without calling `create_entry` or `update_entry`

#### Scenario: Relation follow-up runs only after successful persistence
- **WHEN** `smart_capture` successfully creates or updates an entry
- **THEN** it SHALL request relation recommendations for the persisted entry
- **AND** it SHALL filter out recommendations whose relation type is missing before asking for human confirmation
- **AND** it SHALL allow batch selection of zero or more recommended relations

### Requirement: human_in_loop nodes SHALL support reusable batch multi-select option UIs
`human_in_loop` nodes SHALL support a reusable array-oriented `checkbox_group` widget and SHALL preserve object-style option payloads for select-like widgets without breaking legacy string-list options.

#### Scenario: checkbox_group accepts object options
- **WHEN** a `human_in_loop` field uses `widget=checkbox_group`
- **THEN** it SHALL accept array values
- **AND** it SHALL support options shaped as strings or `{ value, label, description? }`

#### Scenario: Templated options preserve labels and descriptions
- **WHEN** `optionsTemplate` resolves to object-style options for `select`, `radio`, or `checkbox_group`
- **THEN** runtime and preview SHALL preserve `label`, `value`, and optional `description` instead of flattening everything to raw strings

#### Scenario: Legacy option lists remain valid
- **WHEN** an existing `human_in_loop` config uses string-list options for `select`, `radio`, or `tag_selector`
- **THEN** runtime and validation SHALL continue to accept them without requiring migration

### Requirement: smart_capture and context_capture workflow roles SHALL remain distinct but reusable
The shipped capture workflows SHALL keep distinct primary roles while remaining reusable workflow assets.

#### Scenario: smart_capture remains assistant-first guided capture
- **WHEN** the system advertises or binds the default `smart_capture` workflow
- **THEN** it SHALL describe it as the assistant-first guided capture workflow primarily used by system skills / the in-app AI assistant
- **AND** it SHALL still remain a reusable general capture workflow instead of hardcoding caller-channel logic

#### Scenario: context_capture remains OpenClaw-facing automatic capture
- **WHEN** the system advertises or binds the default `context_capture` workflow
- **THEN** it SHALL remain the OpenClaw-facing thin-context automatic create/merge workflow
- **AND** this change SHALL not alter its published contract

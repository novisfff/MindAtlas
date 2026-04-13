## ADDED Requirements

### Requirement: System Assistant Assets SHALL Have One Canonical Source
Shipped system workflow and system agent assets SHALL be owned by `assistant/workflow/system_assets`, and other modules SHALL reference those assets by `asset_key` or canonical name instead of owning preset files or duplicated asset metadata.

#### Scenario: Skill defaults resolve from central assets
- **WHEN** the assistant skill catalog loads shipped system skills
- **THEN** it SHALL synthesize those skills from centrally registered assets tagged for skill-default usage
- **AND** it SHALL not parse a separate manifest or preset directory as an additional source of truth

#### Scenario: System behaviors and standalone targets resolve through asset keys
- **WHEN** assistant-config ensures, resets, or rehydrates built-in system targets
- **THEN** it SHALL load standalone targets and system behavior defaults through the centralized asset registry and loader
- **AND** assistant-config registries SHALL not expose or persist preset file paths for those built-in assets

### Requirement: System Assistant Assets SHALL Resolve Locale-Aware Metadata And Payloads
The centralized system asset layer SHALL provide localized metadata and localized JSON payload loading for shipped system workflows and agents.

#### Scenario: Localized workflow asset is loaded
- **WHEN** a caller loads a system workflow asset for `zh` or `en`
- **THEN** the centralized loader SHALL return the localized JSON payload for that locale
- **AND** it SHALL keep the canonical `asset_key` and canonical workflow name unchanged

#### Scenario: Invalid asset lookup is rejected
- **WHEN** a caller requests an unknown asset key, an unsupported locale, or an escaping asset path
- **THEN** the centralized asset layer SHALL fail fast instead of silently falling back to another source

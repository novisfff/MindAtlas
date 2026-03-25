## ADDED Requirements
### Requirement: Initialization Introduction Step
The system SHALL include a localized, read-only introduction step immediately after system language selection in the first-run initialization wizard.

#### Scenario: Intro step appears after choosing language
- **WHEN** the user enters the initialization wizard
- **THEN** the step order is `system language`, `system introduction`, `AI model`, `entry types`, `extended configuration`, and `review`
- **AND** the introduction step is shown after the language step and before AI model configuration

#### Scenario: Intro step follows the selected language
- **WHEN** the user switches the initialization language between `zh` and `en`
- **THEN** the introduction step title, body copy, and status text update immediately to the selected language

#### Scenario: Intro step shows current capability states
- **WHEN** the initialization defaults include runtime config values for `knowledgeGraph.enabled` and `documentParsing.workerEnabled`
- **THEN** the introduction step shows `LightRAG` as enabled or disabled from `knowledgeGraph.enabled`
- **AND** it shows `Docling` as enabled or disabled from `documentParsing.workerEnabled`
- **AND** `pictureDescriptionEnabled` alone does not mark `Docling` as enabled

#### Scenario: Intro step remains informational only
- **WHEN** the user views the introduction step
- **THEN** the page explains what MindAtlas is and what the remaining setup will configure
- **AND** it only allows the user to continue to the next step without changing initialization payload fields

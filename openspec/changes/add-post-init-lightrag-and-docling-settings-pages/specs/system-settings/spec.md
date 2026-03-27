## MODIFIED Requirements
### Requirement: System Setup Management
The system SHALL provide a post-initialization `System Setup` overview plus dedicated detail pages for runtime modules that need continued editing.

#### Scenario: User opens LightRAG and Docling detail pages after initialization
- **WHEN** the user navigates to Settings after the system is initialized
- **THEN** Settings includes direct entries for `LightRAG` and `Docling`
- **AND** `/settings/lightrag` provides editable LightRAG runtime configuration
- **AND** `/settings/docling` provides editable Docling runtime configuration
- **AND** `System Setup` remains an overview page and links its knowledge graph and document parsing modules to those detail pages

#### Scenario: User sees runtime metadata on module detail pages
- **WHEN** the user opens the LightRAG or Docling settings page
- **THEN** the page shows the module status, value source, restart requirement, and effective summary before the editable form

## ADDED Requirements
### Requirement: Post-Initialization LightRAG Field Locking
The system SHALL protect initialization-only LightRAG fields after the system has been initialized, including legacy systems auto-marked as initialized.

#### Scenario: Initialized system shows locked LightRAG fields
- **WHEN** `system_initialization_state.initialized` is `true`
- **THEN** `summaryLanguage` and `embeddingModelName / embeddingModelId` are shown in the LightRAG settings page
- **AND** those fields are read-only when they already have effective values
- **AND** the UI explains that they were determined during initialization and can no longer be changed

#### Scenario: Initialized system can fill an empty locked field once
- **WHEN** the system is initialized but `embeddingModelName / embeddingModelId` is currently empty
- **THEN** the LightRAG settings page allows the user to save a value once
- **AND** subsequent attempts to modify that value are rejected

#### Scenario: Backend rejects locked field mutation
- **WHEN** an initialized system sends a runtime config update that changes an already-set `summaryLanguage` or `embeddingModelName / embeddingModelId`
- **THEN** the backend rejects the request with a clear error instead of silently mutating the locked field

### Requirement: Shared Runtime Validation Rules
The system SHALL use shared frontend validation rules for initialization and post-initialization LightRAG and Docling editing.

#### Scenario: Knowledge graph validation blocks incomplete required fields
- **WHEN** `knowledgeGraph.enabled` is `true`
- **THEN** frontend validation requires `embeddingModelName / embeddingModelId`, `embeddingHost`, and an embedding API key or an already configured secret
- **AND** when rerank is enabled it also requires `rerankModel`, `rerankHost`, and a rerank API key or an already configured secret

#### Scenario: Docling validation blocks incomplete required fields
- **WHEN** `ocrEnabled` is `true`
- **THEN** frontend validation requires `ocrLangs`
- **AND** when `pictureDescriptionEnabled` is `true` it requires `pictureDescriptionUrl`, `pictureDescriptionModel`, `pictureDescriptionPrompt`, and an image-description API key or an already configured secret

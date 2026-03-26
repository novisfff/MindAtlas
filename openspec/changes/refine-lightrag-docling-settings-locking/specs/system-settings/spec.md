## MODIFIED Requirements
### Requirement: System Setup Management
The system SHALL provide post-initialization runtime detail pages that reflect deployment-owned startup state and only expose the settings that remain meaningful after initialization.

#### Scenario: LightRAG settings hide deployment-owned fields
- **WHEN** the user opens `/settings/lightrag`
- **THEN** the page shows whether LightRAG is started
- **AND** Neo4j connection fields and graph storage fields are not editable from that page
- **AND** the page only exposes runtime-adjustable values such as LLM override, workspace, embedding API key, and optional rerank settings

#### Scenario: Docling settings depend on worker startup state
- **WHEN** the user opens `/settings/docling`
- **THEN** the page shows whether the Docling worker is started
- **AND** `workerEnabled` is not editable from the page
- **AND** OCR and image-description settings are only editable when the worker is started

## MODIFIED Requirements
### Requirement: Post-Initialization LightRAG Field Locking
The system SHALL protect deployment-owned and initialization-only LightRAG fields after the system has been initialized, including legacy systems auto-marked as initialized.

#### Scenario: Initialized system keeps deployment-owned LightRAG fields immutable
- **WHEN** `system_initialization_state.initialized` is `true`
- **THEN** runtime config updates cannot change `enabled`, `neo4jUri`, `neo4jUser`, `neo4jPassword`, `neo4jDatabase`, or `graphStorage`

#### Scenario: Initialized system locks embedding host after first effective value
- **WHEN** an initialized system has an effective `embeddingHost`
- **THEN** the LightRAG settings page shows the field as read-only
- **AND** backend runtime config updates reject attempts to change it

#### Scenario: Initialized system can fill an empty embedding host once
- **WHEN** the system is initialized and `embeddingHost` is still empty
- **THEN** the LightRAG settings page allows one successful save
- **AND** later attempts to modify the saved value are rejected

#### Scenario: Initialized system always locks embedding model selection
- **WHEN** `system_initialization_state.initialized` is `true`
- **THEN** the LightRAG settings page shows `embeddingModelName / embeddingModelId` as read-only even if no effective value is present
- **AND** backend runtime config updates reject any attempt to change the embedding model selection

## MODIFIED Requirements
### Requirement: Shared Runtime Validation Rules
The system SHALL use shared frontend validation rules that respect module startup state before enforcing detailed runtime field requirements.

#### Scenario: Disabled LightRAG does not require runtime form completion
- **WHEN** `knowledgeGraph.enabled` is `false`
- **THEN** LightRAG validation does not require embedding or rerank fields

#### Scenario: Stopped Docling worker does not require OCR or image-description fields
- **WHEN** `documentParsing.workerEnabled` is `false`
- **THEN** Docling validation does not require OCR or image-description fields

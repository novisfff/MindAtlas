## MODIFIED Requirements

### Requirement: Capture workflows SHALL use shared semantic similarity retrieval
The shipped capture-oriented workflows SHALL use a shared `search_similar_entries` system tool backed by LightRAG to recall entry-level similarity candidates before same-record decisions are made.

#### Scenario: Semantic retrieval returns entry candidates instead of chunks
- **WHEN** `smart_capture` or `context_capture` performs similar-entry recall
- **THEN** it SHALL call the shared `search_similar_entries` tool
- **AND** the tool SHALL return entry-level candidates enriched from LightRAG source hits rather than raw chunk rows

#### Scenario: Retrieval failure degrades to empty candidates
- **WHEN** LightRAG is unavailable or semantic recall fails
- **THEN** `search_similar_entries` SHALL return an unavailable status with an empty candidate list
- **AND** capture workflows SHALL treat that result as “no similar candidates found” instead of failing their public contract

### Requirement: smart_capture workflow must require human confirmation before record creation
The system default `smart_capture` workflow SHALL materialize its write payload in one structured pass, surface likely duplicate candidates during the `human_in_loop` step, and remain human-confirmed create-only.

#### Scenario: Duplicate warning appears before create
- **WHEN** `smart_capture` derives possible duplicate candidates from shared semantic similarity recall
- **THEN** the `human_confirm` instruction SHALL include a concise duplicate notice
- **AND** the notice SHALL explain that the workflow will not auto-merge
- **AND** approval SHALL still create a new entry instead of merging into an existing one

#### Scenario: Rejected decision still cancels creation
- **WHEN** `smart_capture` reaches `human_confirm` and the user submits `rejected`
- **THEN** the workflow SHALL route through the reject branch without calling `create_entry`
- **AND** the final output SHALL confirm that persistence was canceled

### Requirement: Capture workflows SHALL isolate session memory during record materialization
The shipped capture-oriented workflows SHALL disable automatic session memory injection so persistence fields are generated only from current input, `sys` metadata, and explicit tool outputs.

#### Scenario: smart_capture disables memory injection
- **WHEN** the system default `smart_capture` workflow starts
- **THEN** its `start` node SHALL set `memoryMode=off`

#### Scenario: context_capture disables memory injection
- **WHEN** the system default `context_capture` workflow starts
- **THEN** its `start` node SHALL set `memoryMode=off`

### Requirement: human_in_loop node request text SHALL support template resolution
`human_in_loop` nodes SHALL resolve workflow templates in request-facing text fields before the approval request is persisted or previewed.

#### Scenario: Runtime resolves templated title and instruction
- **WHEN** a `human_in_loop` node config uses templates in `title` or `instruction`
- **THEN** runtime SHALL resolve those templates against upstream node outputs, start inputs, `sys`, and `env` variables

#### Scenario: Runtime resolves templated button labels
- **WHEN** a `human_in_loop` node config uses templates in `approveLabel` or `rejectLabel`
- **THEN** runtime SHALL resolve those templates before sending the approval payload

### Requirement: Workflow entry writes SHALL reject invalid explicit capture fields
Workflow-driven entry writes SHALL apply defaults only for missing or blank capture fields, and SHALL reject non-empty invalid explicit type or time inputs.

#### Scenario: Blank type still uses default type
- **WHEN** `create_entry` or `update_entry` receives an empty `type_code`
- **THEN** the request SHALL still use the default enabled entry type

#### Scenario: Invalid explicit type is rejected
- **WHEN** `create_entry` or `update_entry` receives a non-empty `type_code` that does not resolve to an enabled entry type
- **THEN** the tool SHALL raise an error instead of silently falling back

#### Scenario: Invalid explicit time combination is rejected
- **WHEN** `create_entry` or `update_entry` receives malformed dates, partial range fields, or conflicting `POINT` / `RANGE` inputs
- **THEN** the tool SHALL raise an error instead of silently rewriting the time payload

### Requirement: context_capture workflow SHALL use conservative semantic recall before auto merge
The system default `context_capture` workflow SHALL use shared semantic similarity retrieval, reuse existing tag names where possible, and merge only when its top recalled candidate is conservatively judged to be the same durable record.

#### Scenario: Top semantic candidate can merge
- **WHEN** semantic similarity recall returns a top candidate entry
- **AND** the decision node determines that candidate is clearly the same durable record
- **THEN** `context_capture` SHALL update that existing entry

#### Scenario: Empty or rejected top candidate creates
- **WHEN** semantic similarity recall returns no candidate, or the top candidate is rejected as not clearly the same durable record
- **THEN** `context_capture` SHALL create a new entry instead of merging

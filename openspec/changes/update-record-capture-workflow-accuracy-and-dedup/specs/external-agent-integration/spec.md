## MODIFIED Requirements

### Requirement: OpenClaw Capability Execution SHALL Reuse MindAtlas Business Services
Each exposed catalog item SHALL continue routing into existing MindAtlas services and workflows, while preserving the public OpenClaw contract even when internal capture behavior becomes stricter.

#### Scenario: submit_context_capture keeps its thin public contract
- **WHEN** OpenClaw discovers or executes the shipped `submit_context_capture` capability
- **THEN** the public input contract SHALL still require only `context`
- **AND** the public tool name SHALL remain unchanged
- **AND** internal changes such as memory isolation, semantic similarity retrieval, stricter merge gating, and tag reuse SHALL NOT add new public fields

#### Scenario: submit_context_capture keeps its unified result shape
- **WHEN** `submit_context_capture` completes through either create or merge
- **THEN** it SHALL continue returning the existing unified structured result shape
- **AND** the `status` field SHALL continue distinguishing `created` from `merged`

#### Scenario: Ambiguous auto-capture still falls back to create
- **WHEN** the internal `context_capture` workflow cannot confirm that its top semantic candidate is the same durable record
- **THEN** `submit_context_capture` SHALL fall back to the create path
- **AND** it SHALL NOT require human confirmation from OpenClaw

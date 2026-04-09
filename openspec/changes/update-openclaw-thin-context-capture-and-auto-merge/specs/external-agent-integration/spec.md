## MODIFIED Requirements

### Requirement: OpenClaw Capability Execution SHALL Reuse MindAtlas Business Services
Each exposed catalog item SHALL route into existing MindAtlas services or runners instead of duplicating business logic.

#### Scenario: Smart capture accepts only a thin context payload
- **WHEN** OpenClaw discovers or executes the shipped `submit_context_capture` capability
- **THEN** the public input contract SHALL require only `context`
- **AND** older multi-field capture hints such as `intent`, `source`, `session`, `channel`, `taskHint`, `timeHint`, and `tagHints` SHALL no longer be part of the shipped runtime contract

#### Scenario: Thin capture uses OpenClaw request metadata inside workflow execution
- **WHEN** OpenClaw executes `submit_context_capture` with request headers carrying source, channel, session, or tool metadata
- **THEN** MindAtlas SHALL pass those values into the workflow runtime context
- **AND** the workflow SHALL be able to reference them through generic `sys.request_*` variables without exposing them as public capture fields

#### Scenario: Smart capture can merge into an existing entry conservatively
- **WHEN** the shipped `submit_context_capture` workflow finds one clear matching candidate entry for the submitted context
- **THEN** it SHALL be allowed to update that existing entry instead of creating a new one
- **AND** ambiguous or low-confidence candidate sets SHALL fall back to creating a new entry

#### Scenario: Smart capture returns a unified create-or-merge result
- **WHEN** `submit_context_capture` completes through either create or merge
- **THEN** the structured result SHALL expose the same top-level fields for both paths
- **AND** the `status` field SHALL distinguish whether the final action was `created` or `merged`

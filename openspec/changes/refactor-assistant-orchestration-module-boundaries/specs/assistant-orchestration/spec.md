## ADDED Requirements
### Requirement: Assistant Runtime Module Boundaries
The system SHALL separate assistant runtime concerns into dedicated packages for orchestration, workflow execution/validation, and skill catalog metadata.

#### Scenario: Assistant imports resolve without legacy skills package
- **WHEN** backend services and tests import assistant runtime modules
- **THEN** imports resolve via `app.assistant.orchestration`, `app.assistant.workflow`, and `app.assistant.skill_catalog`
- **AND** no `app.assistant.skills.*` import is required.

### Requirement: Behavior-Preserving Internal Refactor
The system SHALL preserve existing assistant HTTP API and workflow runtime semantics during internal package refactoring.

#### Scenario: Workflow runtime behavior remains unchanged
- **WHEN** workflow execution features (llm/tool/if_else/iteration/loop/code_executor/variable_assign/human_in_loop) are exercised
- **THEN** outputs, events, and validator error semantics remain compatible with existing behavior.

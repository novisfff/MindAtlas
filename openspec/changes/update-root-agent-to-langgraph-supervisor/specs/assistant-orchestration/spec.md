## ADDED Requirements

### Requirement: Root Assistant SHALL Use Supervisor Graph Orchestration
The assistant root runtime SHALL orchestrate routing and skill execution through a LangGraph supervisor graph with explicit state transitions.

#### Scenario: Single-turn execution follows route and execute states
- **WHEN** a user sends one chat turn
- **THEN** the root runtime SHALL run a single routing node once
- **AND** SHALL execute at most one selected skill subgraph in that turn
- **AND** SHALL finish through explicit graph terminal states.

### Requirement: Router Decision SHALL Be Structured And Deterministic
The router SHALL output a structured decision containing a single candidate `skill` and `reason`, and runtime SHALL deterministically select execution skill with default fallback rules.

#### Scenario: Empty or invalid route output falls back to default
- **WHEN** router returns an empty skill, invalid skill, or unparsable payload
- **THEN** runtime SHALL reject that candidate for execution
- **AND** SHALL fall back to `general_chat` when available.

#### Scenario: Legacy router output remains compatible
- **WHEN** router returns legacy format `{"skills": ["name"]}`
- **THEN** runtime SHALL parse it compatibly
- **AND** SHALL execute the first valid candidate if available.

### Requirement: Skill Failure SHALL Be Terminal Without Auto Fallback
If the selected skill execution fails, the turn SHALL fail without attempting automatic execution of another skill.

#### Scenario: Selected skill raises execution error
- **WHEN** selected skill runtime throws an execution exception
- **THEN** root runtime SHALL emit `skill_end` with error status
- **AND** SHALL terminate the turn as failed
- **AND** SHALL NOT auto-switch to another skill.

### Requirement: Service SHALL Not Use Outer OpenAI Fallback After Agent Failure
Assistant service SHALL not run a second outer OpenAI stream fallback once root-agent execution fails.

#### Scenario: Supervisor execution fails in service response generation
- **WHEN** root agent returns execution failure
- **THEN** service SHALL return a direct error response path
- **AND** SHALL NOT invoke outer `_openai_stream` fallback logic.

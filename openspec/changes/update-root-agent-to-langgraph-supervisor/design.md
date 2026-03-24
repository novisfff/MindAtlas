## Context
Root-agent behavior currently mixes routing policy, fallback policy, and skill execution in imperative runtime code. The system already uses LangGraph for skill internals; this change aligns root orchestration with the same model.

## Goals
- Root orchestration is graph-based and state-explicit.
- Single skill is selected and executed per turn.
- Routing policy is stable, inspectable, and deterministic.
- Existing streaming event protocol remains backward compatible.

## Non-Goals
- Multi-skill orchestration in one turn.
- New SSE event types for router internals.
- Changing skill subgraph semantics (`agent_loop`, `workflow_dag`).

## Architecture
1. `route_once` node:
- Calls router once.
- Stores route fields (`route_skill`, `route_reason`).
- Writes `selected_skill` from valid-skill check and default fallback policy.

2. Conditional transition:
- Invalid/no selected skill -> `fail`.
- `selected_skill == general_chat` -> `execute_default_skill`.
- Otherwise -> `execute_selected_skill`.

3. Execute nodes:
- Emit `skill_start` and `skill_end` via existing callbacks.
- Execute selected skill via `LangGraphEngine.execute`.
- Forward content chunks via thread-safe queue.
- On error set `execution_status=failed` and keep failure terminal.

4. Service behavior:
- Assistant service no longer runs outer OpenAI stream fallback after agent failure.
- Error messaging remains user-friendly and policy-aware.

## Routing Selection Rules
- Router returns only a single candidate skill name (or empty skill).
- Legacy `skills[]` output is accepted by reading the first item.
- Empty skill, invalid skill, parse failure, or router exception falls back to default when available.
- If default skill is unavailable, routing stage fails explicitly.

## Compatibility
- SSE schema unchanged.
- Legacy router JSON (`{"skills": ["..."]}`) remains accepted.
- Skill execution callbacks preserved.

## Failure Model
- Skill execution failure is terminal for the turn.
- No automatic fallback to another skill.
- If default skill is unavailable during fallback, the run fails with explicit error.

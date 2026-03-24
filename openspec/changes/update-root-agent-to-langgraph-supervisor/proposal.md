# Change: Update Root Agent To LangGraph Supervisor

## Why
The current root assistant path performs routing and execution in imperative code (`AssistantAgent.stream`) and still preserves legacy direct-reply and outer fallback branches. This makes routing policy hard to evolve and obscures execution-state control.

## What Changes
- Move root orchestration to a LangGraph supervisor graph with explicit route/execute/fail states.
- Keep single-skill-per-turn policy and one-stage routing.
- Upgrade router output to stable single-skill decisions (`skill/reason`) with compatibility for legacy `skills[]` output.
- Use deterministic fallback policy: invalid/empty route output falls back to `general_chat`.
- Remove `no skill -> direct_reply` and remove service-level outer OpenAI fallback.
- Preserve existing SSE contract (`content_delta`, `tool_call_*`, `skill_*`, `analysis_*`).

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant/orchestration/{intent_router.py,agent_runtime.py,supervisor_state.py,supervisor_graph.py}`
  - `backend/app/assistant/service.py`
  - `backend/app/config.py`
  - `backend/tests/test_*` (router/supervisor/service fallback)

# Plan 10 — HumanLoop fail-closed + orchestration removal (2026-07-23)

**Commit series:** residual after `eee3b8f` skill drop  
**Alembic head:** unchanged `5cc5a70095f9`

## HumanLoop
- `assistant_human_approval` table already dropped (`ca6f564ef4bd`)
- Removed `AssistantHumanApproval` ORM class
- `human_approval_runtime.py` rewritten fail-closed:
  - `create_and_wait` → `LegacyHitlRemoved`
  - `submit_human_approval_decision` → HTTP 410
  - list/cancel pending → empty
- Migration archive/count helpers return 0 / [] without the table

## Orchestration
Deleted legacy-only modules:
- `intent_router.py`
- `supervisor_graph.py`
- `supervisor_state.py`
- `agent_runtime.py`

Retained shared helpers:
- `chat_events.py`
- `memory_context.py`
- `openai_fallback_client.py`

`AssistantService._generate_response` no longer constructs `AssistantAgent`.

## Tests
Retired suites skipped; architecture tests assert modules removed and HITL fail-closed.

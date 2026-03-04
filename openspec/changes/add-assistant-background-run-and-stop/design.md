## Context
Assistant chat and workflow execution already support rich runtime events and HITL persistence, but execution is bound to one SSE request lifecycle. We need connection-independent run continuity and controllable stop.

## Goals / Non-Goals
- Goals:
  - Keep run executing when client disconnects/reloads.
  - Support reattach with event replay (`afterSeq`) and live follow.
  - Provide server-side soft stop and terminal cancellation semantics.
- Non-Goals:
  - Cross-process recovery after backend restart.
  - Multi-active-run per conversation.

## Decisions
- Persist run state in `assistant_chat_run` and event log in `assistant_chat_run_event`.
- Keep existing chat endpoint and add run-specific endpoints.
- Use polling replay for stream attach (DB is source of truth).
- Apply soft cancellation via `cancel_checker` propagated into supervisor, workflow stream runtime, and HITL waiting loop.
- Keep fallback follow-up message only for disconnected legacy edge cases with no active background run.

## Risks / Trade-offs
- Polling stream readers add DB read pressure.
  - Mitigation: bounded polling interval and incremental `afterSeq` queries.
- Cancellation may not terminate an in-flight single LLM HTTP call instantly.
  - Mitigation: soft-stop contract (node boundary / polling point interruption).

## Migration Plan
1. Add DB tables and indexes.
2. Introduce run service and background execution in assistant service.
3. Add run APIs and keep existing chat API compatible.
4. Add frontend active-run attach + stop UX.
5. Add regression tests for run lifecycle/replay/stop/HITL cancellation.

## Open Questions
- None for current scope; process-restart durability intentionally out of scope.

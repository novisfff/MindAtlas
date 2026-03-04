# Change: Add Assistant Background Run And Stop

## Why
Assistant chat execution is currently tied to a single request-bound SSE connection. When users refresh or leave the page, execution visibility breaks and stop is only local abort, which degrades UX in long workflows and HITL scenarios.

## What Changes
- Add persistent assistant chat run lifecycle with event log replay.
- Keep `POST /api/assistant/conversations/{id}/chat` compatible while changing internals to start background run + attach stream.
- Add run APIs for active-run lookup, stream attach by cursor, and stop.
- Add soft cancellation checks across supervisor/workflow runtime/HITL wait loop.
- Add frontend active-run attach, cursor persistence, and send/stop button switch.

## Impact
- Affected specs: `assistant-orchestration`.
- Affected code:
  - `backend/app/assistant/{models.py,run_service.py,service.py,router.py,schemas.py}`
  - `backend/app/assistant/workflow/{human_approval_runtime.py,engine/*}`
  - `frontend/src/features/assistant/{api,index.ts,hooks/useChat.ts,stores/chat-store.ts,components/*,types.ts}`

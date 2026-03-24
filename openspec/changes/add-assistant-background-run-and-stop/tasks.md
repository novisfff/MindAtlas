## 1. Backend
- [x] 1.1 Add `AssistantChatRun` and `AssistantChatRunEvent` models + Alembic migration.
- [x] 1.2 Add `run_service.py` for run lifecycle, event append/query, checkpoint and stop.
- [x] 1.3 Refactor assistant chat execution to background thread and event-log streaming attach.
- [x] 1.4 Add run APIs: active run query, run stream attach, and stop.
- [x] 1.5 Add soft cancellation propagation in supervisor/workflow stream/HITL runtime.
- [x] 1.6 Keep disconnected follow-up fallback constrained to non-active-run cases.

## 2. Frontend
- [x] 2.1 Add assistant run types and chat store run state.
- [x] 2.2 Add active-run/stop/stream API helpers.
- [x] 2.3 Add active-run attach + cursor persistence in `useChat`.
- [x] 2.4 Switch send button to stop button during loading.
- [x] 2.5 Ensure full-page/floating chat share the same stop/attach behavior.

## 3. Tests
- [x] 3.1 Add run service lifecycle tests.
- [x] 3.2 Add run stream replay tests.
- [x] 3.3 Add stop idempotency tests.
- [x] 3.4 Extend HITL runtime tests for cancellation path.
- [x] 3.5 Run targeted backend and frontend checks.

## 4. Spec
- [x] 4.1 Add OpenSpec proposal/design/tasks/spec delta files.
- [x] 4.2 Validate with `openspec validate add-assistant-background-run-and-stop --strict --no-interactive`.

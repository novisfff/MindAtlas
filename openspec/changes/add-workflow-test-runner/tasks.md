## 1. Backend
- [x] 1.1 Add workflow test-run request schema
- [x] 1.2 Add `POST /skills/{id}/workflow/test-run` SSE endpoint
- [x] 1.3 Add workflow test-run service (preflight validation + in-memory execution + SSE forwarding)
- [x] 1.4 Scope container-subflow node event IDs as `containerId::nodeId`
- [x] 1.5 Add backend tests for preflight rejection and stream lifecycle events
- [x] 1.6 Add `node_snapshot` event forwarding for per-node I/O snapshots (with truncation guard)
- [x] 1.7 Add text-mode multi-turn test-run request fields (`sessionId`, `history`, `sessionMemory`)
- [x] 1.8 Add ephemeral L1/L2 computation + `run_end.sessionMemory` return path for workflow test-run
- [x] 1.9 Support runtime memory overrides for workflow test-run without DB persistence

## 2. Frontend
- [x] 2.1 Add workflow test-stream API types and runner
- [x] 2.2 Add reusable SSE parser utility and migrate chat hook to reuse it
- [x] 2.3 Add workflow test-run zustand store
- [x] 2.4 Add docked workflow test-run panel (input/run/cancel/stream toggle + result/trace/raw tabs)
- [x] 2.5 Wire panel into workflow editor actions
- [x] 2.6 Add runtime node highlight and trace-to-node selection routing
- [x] 2.7 Add node I/O snapshot viewer in trace panel (summary + expandable details)
- [x] 2.8 Upgrade text-mode workflow test-run into multi-turn conversation view with in-memory transcript
- [x] 2.9 Preserve per-turn result / trace / raw selection from conversation history
- [x] 2.10 Keep structured-mode workflow test-run as single-run UI and reject multi-turn payloads

## 3. Validation
- [x] 3.1 Run frontend build (`npm run build`)
- [x] 3.2 Run backend tests (`pytest -q`)
- [x] 3.3 Run OpenSpec strict validation for the new change

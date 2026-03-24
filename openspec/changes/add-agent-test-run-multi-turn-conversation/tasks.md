## 1. Backend
- [x] 1.1 Extend `AgentTestRunRequest` with validated multi-turn `history` payload.
- [x] 1.2 Pass agent test-run history into `LangGraphEngine.execute(...)`.
- [x] 1.3 Add regression test proving history is forwarded to the engine.

## 2. Frontend
- [x] 2.1 Refactor agent test-run store to keep message history across turns.
- [x] 2.2 Render Agent test-run as a multi-turn conversation list instead of single result-only panel.
- [x] 2.3 Keep Tool Chain attached to each assistant reply and place it above reply content.

## 3. Verification
- [x] 3.1 Run `backend/tests/test_agent_test_run_stream.py`.
- [x] 3.2 Run `frontend npm run build`.

## Context
Workflow DAG already supports validation and persistence but lacks an integrated test runner for unsaved drafts. The runtime engine (`LangGraphEngine.execute`) already exposes callback hooks for node/tool/branch events and content output chunks.

## Goals
- Run unsaved workflow drafts directly from editor.
- Stream end-to-end execution trace and output in real-time.
- Keep test runs isolated from conversation persistence.
- Support caller-controlled streaming (`streamOutput`) in test-run channel.
- Expose node-level input/output snapshots for every executed node.
- Support text-mode multi-turn test conversations with assistant-like context carryover.
- Keep per-turn debug artifacts selectable after each conversation round.

## Non-Goals
- Persist test run history into database.
- Introduce dynamic input forms beyond `user_input` text.
- Add independent subflow history stacks.
- Add structured-mode multi-turn test sessions in this phase.

## Decisions
- Add dedicated `assistant-config` SSE endpoint for workflow test runs.
- Execute using in-memory `SkillDefinition` built from request workflow.
- Reuse workflow topology + dependency validation before starting stream.
- Keep single active run on frontend; new run cancels previous one.
- Scope subflow node callback IDs with `containerId::innerNodeId` to make traces routable.
- Text-mode test-run uses a stable front-end generated `sessionId`, completed `history`, and ephemeral `sessionMemory`.
- L0 continues to be built from `history`; L1/L2 are computed after completed runs and returned in `run_end.sessionMemory`.
- Workflow test-run never persists L1/L2; it only uses runtime overrides to inject temporary session memory.
- Structured-mode test-run remains single-shot and rejects `history` / `sessionMemory`.
- Result / Trace / Raw panels read from the currently selected conversation turn instead of a single global latest run.

## SSE Protocol
- `run_start`
- `node_start`
- `node_output_delta`
- `node_snapshot`
- `branch_decision`
- `tool_call_start`
- `tool_call_end`
- `content_delta`
- `node_end`
- `run_error`
- `run_end`

## Risks / Trade-offs
- Runtime highlight is node-level and may not represent partial branch completion semantics in complex graphs.
- Frontend keeps run history in-memory only, so refresh clears context.
- `streamOutput=false` still emits trace events while content is aggregated at end.
- `node_snapshot` payload can be large, so backend applies hard truncation safeguards.
- Temporary L1/L2 lives only on the current page, so refresh intentionally resets the test conversation.
- Multi-turn test-run reuses assistant-like memory semantics without backend persistence, so behavior is aligned but not durable.

## Rollout
- Backend and frontend ship together.
- OpenSpec proposal validated with strict mode.
- Existing save/load and chat APIs remain unchanged.

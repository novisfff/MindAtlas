## Context
- Workflow execution currently supports automatic branching (`if_else`) but does not support human confirmation checkpoints.
- Both workflow test-run and assistant conversation stream already use SSE, which can carry approval request/resolution events.
- The required behavior is long-poll style waiting with no auto-timeout.

## Goals
- Add first-class `human_in_loop` node with editable field form and approve/reject actions.
- Persist pending approvals for page refresh and cross-page decision submission.
- Route execution by branch decision (`approved` / `rejected`) without treating rejection as execution error.
- Keep existing workflow DAG protocol unchanged except new node type/handles/events.

## Non-Goals
- Full process crash recovery with call-stack restore after server restart.
- Additional decision actions beyond approve/reject.
- Auto-timeout or auto-rejection strategy.

## Decisions
- Add `AssistantHumanApproval` persistence table and a runtime coordinator (`HumanLoopRuntime`) for wait/notify.
- `human_in_loop` node emits two branch outcomes via `branch_decisions[node_id]`:
  - `approved`
  - `rejected`
- Node output contains:
  - `decision`
  - `comment`
  - user-submitted fields (flattened)
- Validator enforces exactly one outgoing edge for `approved` and `rejected`.
- Frontend uses shared HITL components for both workflow test-run and assistant chat to avoid duplicated logic.

## Risks / Trade-offs
- In-process waiting means service restart cannot resume already-paused execution stack automatically.
- Added runtime coordination and SSE events increase state synchronization complexity.
- UI and validator must stay aligned on field typing and required constraints.

## Migration Plan
1. Add model/migration and runtime coordinator.
2. Add engine node builder + branch routing + snapshot support.
3. Add validator rules for node config and branch handles (main + container body).
4. Add decision routes for workflow runs and assistant conversations.
5. Integrate frontend workflow editor node settings and node rendering.
6. Integrate frontend workflow test-run and assistant chat approval cards.
7. Add/extend tests and run strict OpenSpec validation.

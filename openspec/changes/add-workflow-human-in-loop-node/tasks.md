## 1. Backend
- [x] 1.1 Add `human_in_loop` to workflow node type enums and schema contracts.
- [x] 1.2 Add persisted approval model and migration for pending/resolved approvals.
- [x] 1.3 Add human-loop runtime coordinator for wait/notify and decision submission.
- [x] 1.4 Add `human_in_loop` validator rules (config + approved/rejected branch handle constraints).
- [x] 1.5 Add engine builder/runtime execution for `human_in_loop` in main graph and container body.
- [x] 1.6 Add workflow test-run and assistant SSE events for approval requested/resolved.
- [x] 1.7 Add decision submission APIs for workflow run and assistant conversation channels.

## 2. Frontend
- [x] 2.1 Add `human_in_loop` node type support in workflow API/types and node catalog/factory/canvas rendering.
- [x] 2.2 Add workflow property panel editor `HumanInLoopNodeSettings`.
- [x] 2.3 Add shared HITL approval components (`card`, `field form`, `action bar`, `status badge`).
- [x] 2.4 Integrate pending approval cards and decision submission in workflow test-run panel.
- [x] 2.5 Integrate pending approval cards and decision submission in assistant chat stream.
- [x] 2.6 Add conversation pending-approval recovery on page load/refresh.
- [x] 2.7 Add i18n keys for node config and approval UI.

## 3. Tests
- [x] 3.1 Add validator tests for `human_in_loop` config and approved/rejected edge constraints.
- [x] 3.2 Add runtime tests for approval persistence/decision application.
- [x] 3.3 Add SSE stream tests for `human_approval_requested` and `human_approval_resolved` events.

## 4. Spec
- [x] 4.1 Add OpenSpec change files (`proposal`, `design`, `tasks`, `spec delta`).
- [x] 4.2 Validate with `openspec validate add-workflow-human-in-loop-node --strict --no-interactive`.

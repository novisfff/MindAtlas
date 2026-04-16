## 1. Spec
- [x] 1.1 Add OpenSpec deltas for `assistant-orchestration`
- [x] 1.2 Validate the change with `openspec validate update-smart-capture-guided-merge-and-relation-followup --strict --no-interactive`

## 2. Runtime And Workflow Implementation
- [x] 2.1 Extend `human_in_loop` to support `checkbox_group` and object-style options while keeping existing option lists backward compatible
- [x] 2.2 Update HITL preview/runtime/editor/frontend rendering for templated object options and batch multi-select approvals
- [x] 2.3 Rebuild `smart_capture` into guided create-or-merge with conditional triage, split write confirmation, and post-write relation follow-up
- [x] 2.4 Update system asset descriptions for assistant-first guided capture positioning

## 3. Verification
- [x] 3.1 Add or update regression tests for HITL widgets/options, smart_capture topology, and layout
- [x] 3.2 Run targeted backend verification and ensure `context_capture` / OpenClaw contracts stay unchanged

## 1. Spec
- [x] 1.1 Add OpenSpec deltas for `assistant-orchestration` and `external-agent-integration`
- [x] 1.2 Validate the change with `openspec validate update-record-capture-workflow-accuracy-and-dedup --strict --no-interactive`

## 2. Runtime And Workflow Implementation
- [x] 2.1 Tighten `create_entry` / `update_entry` explicit field validation for type and time inputs
- [x] 2.2 Extend `human_in_loop` runtime and snapshot resolution to template `title`, `instruction`, and button labels
- [x] 2.3 Add a shared `search_similar_entries` system tool backed by LightRAG semantic recall and expose it in the assistant tool catalog without changing OpenClaw shipped capabilities
- [x] 2.4 Rebuild `context_capture` with memory isolation, tag reuse, single semantic recall `top1` selection, and conservative merge-or-create gating

## 3. Verification
- [x] 3.1 Add or update backend tests for entry tool validation, human-in-loop templating, workflow topology, and merge gating
- [x] 3.2 Verify `submit_context_capture` public contract and output shape remain unchanged

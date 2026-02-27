## 1. Implementation
- [x] 1.1 Update `smart_capture` preset graph to include `human_confirm` (`human_in_loop`) before `tool_create`.
- [x] 1.2 Add reject branch (`human_confirm.rejected`) to a cancel response node and ensure no `create_entry` call on reject path.
- [x] 1.3 Rebind `tool_create.inputBindings` to `human_confirm.*` fields and include all create payload fields.
- [x] 1.4 Keep a single terminal output node that can render either approved or rejected path response.

## 2. Testing
- [x] 2.1 Update system layout preset test expectations for new nodes and positions.
- [x] 2.2 Extend system workflow reference contract test to support `human_in_loop` fields.
- [x] 2.3 Add explicit `smart_capture` HITL topology and binding assertions.
- [x] 2.4 Add defaults loader regression assertion for `human_confirm` branch handles.

## 3. Specification
- [x] 3.1 Add OpenSpec change files (`proposal/tasks/spec`).
- [x] 3.2 Validate with `openspec validate update-smart-capture-add-hitl-confirmation --strict --no-interactive`.

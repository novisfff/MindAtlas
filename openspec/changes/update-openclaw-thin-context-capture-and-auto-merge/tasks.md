## 1. Spec
- [x] 1.1 Add OpenSpec deltas for `external-agent-integration`, `openclaw-plugin-package`, and `assistant-orchestration`
- [x] 1.2 Validate the change with `openspec validate update-openclaw-thin-context-capture-and-auto-merge --strict --no-interactive`

## 2. Backend Runtime
- [x] 2.1 Bridge OpenClaw request metadata into workflow runtime context and `sys` variables
- [x] 2.2 Add internal-only `update_entry` workflow tool with the same output shape as `create_entry`
- [x] 2.3 Update OpenClaw system item metadata to describe thin-context capture clearly

## 3. Workflow Preset
- [x] 3.1 Reduce `openclaw_context_capture` start input to a single required `context` field
- [x] 3.2 Add candidate search, conservative merge decision, merge rewrite, and create/update branching
- [x] 3.3 Keep create and merge outputs schema-identical

## 4. OpenClaw Plugin And Copy
- [x] 4.1 Update shipped MindAtlas skills so capture guidance tells OpenClaw to submit one high-value context block
- [x] 4.2 Refresh plugin/runtime tests for the new `submit_context_capture` contract

## 5. Verification
- [x] 5.1 Run `pytest -q backend/tests/test_openclaw_integration.py`
- [x] 5.2 Run `npm --prefix integrations/openclaw-mindatlas test`
- [x] 5.3 Run `openspec validate update-openclaw-thin-context-capture-and-auto-merge --strict --no-interactive`

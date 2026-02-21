## 1. Backend
- [x] 1.1 Extend workflow test-run request schema for dual input payload (`user_input` / `structured_input`).
- [x] 1.2 Add publish request `description` field for atomic publish + metadata update.
- [x] 1.3 Add service guard to block skill binding for structured-input workflows.
- [x] 1.4 Add service guard to block switching referenced workflows to structured mode.
- [x] 1.5 Add extra validation aggregation for structured-input binding conflict on validate APIs.
- [x] 1.6 Update workflow validator with start input schema checks and start field reference checks.
- [x] 1.7 Update LangGraph start-node runtime to consume structured input and remove `start.user_input` in structured mode.
- [x] 1.8 Update workflow test-run service to pass structured input runtime context and pre-check payload consistency.

## 2. Frontend
- [x] 2.1 Add start-node config utility and types (`inputMode`, `structuredFields`).
- [x] 2.2 Add Start node property panel editor UI (description + input mode + schema fields).
- [x] 2.3 Wire workflow description draft state into save/publish payload.
- [x] 2.4 Add structured-input test-run UI and payload builder.
- [x] 2.5 Update variable reference list for start outputs by input mode.
- [x] 2.6 Add default start config and backward-compatible deserialization fallback.
- [x] 2.7 Add skill target bindability flags and disable structured workflows in binding selector.
- [x] 2.8 Add i18n keys for start config, structured test input, and binding-block messages.

## 3. Specification
- [x] 3.1 Add OpenSpec change files (`proposal/design/tasks/spec`).
- [x] 3.2 Validate with `openspec validate add-workflow-start-input-modes --strict --no-interactive`.

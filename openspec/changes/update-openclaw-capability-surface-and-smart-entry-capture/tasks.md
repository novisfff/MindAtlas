## 1. Backend
- [x] 1.1 Remove `capture_entry` from shipped OpenClaw system items, retire legacy bindings to `openclaw_capture_entry`, and keep runtime discovery/execution aligned with the new single-entry surface.
- [x] 1.2 Expand the workflow-backed `submit_context_capture` contract and add consistent availability checks plus idempotent system workflow baseline rebuilding.
- [x] 1.3 Update backend tests to cover the new shipped surface, retired legacy bindings, smart capture output, and repeated sync/list stability.

## 2. Frontend
- [x] 2.1 Update OpenClaw settings types and catalog UI to show retired legacy items clearly and prevent confusing exposure controls.
- [x] 2.2 Refresh localized copy for the retired-state messaging and the single smart entry-create flow.

## 3. Spec And Validation
- [x] 3.1 Add this OpenSpec change package and external-agent-integration spec delta for the capability-surface update.
- [x] 3.2 Run targeted backend verification plus `openspec validate update-openclaw-capability-surface-and-smart-entry-capture --strict --no-interactive`.

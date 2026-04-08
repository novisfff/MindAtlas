## 1. Spec
- [x] 1.1 Add OpenSpec change files (`proposal`, `design`, `tasks`, `spec delta`).
- [x] 1.2 Validate with `openspec validate add-workflow-call-node --strict --no-interactive`.

## 2. Backend
- [x] 2.1 Add `workflow_call` to workflow node type enums and request/response schema contracts.
- [x] 2.2 Extract shared callable-workflow contract parsing helpers and reuse them from OpenClaw integration.
- [x] 2.3 Add callable workflow listing API with callable published versions plus contract-derived input/output params.
- [x] 2.4 Add save/compile/dependency validation for `workflow_call`, including target existence, version existence, callable contract checks, and input binding validation.
- [x] 2.5 Add cross-workflow self-reference and recursive cycle detection that considers the current draft plus persisted workflow graphs.
- [x] 2.6 Add runtime execution for `workflow_call` with `pinned`/`latest` resolution, scoped nested events, shared locale/context, and HITL propagation.
- [x] 2.7 Add workflow/published-version delete protection for active `workflow_call` references.

## 3. Frontend
- [x] 3.1 Add callable workflow API/types and editor state wiring.
- [x] 3.2 Add separate `Workflows` sections to `NodePalette` and `QuickAddPopover`.
- [x] 3.3 Add `workflow_call` node creation, rendering, previews, and container-subflow support.
- [x] 3.4 Add property panel editing for child workflow selection, binding mode, version selection, input bindings, and output preview.
- [x] 3.5 Add `workflow_call` output fields to variable mention/reference builder and validation error display paths.
- [x] 3.6 Add i18n strings for workflow call editor UI.

## 4. Tests
- [x] 4.1 Add backend tests for callable workflow listing, validation, cycle detection, runtime execution, HITL propagation, and delete/version protection.
- [x] 4.2 Run backend targeted tests for workflow validation/runtime/service coverage.
- [x] 4.3 Run frontend type/build validation for workflow editor changes.

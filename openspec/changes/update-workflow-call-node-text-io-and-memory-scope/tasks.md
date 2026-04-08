## 1. Spec
- [x] 1.1 Add OpenSpec change files (`proposal`, `design`, `tasks`, `spec delta`).
- [x] 1.2 Validate with `openspec validate update-workflow-call-node-text-io-and-memory-scope --strict --no-interactive`.

## 2. Backend
- [x] 2.1 Extend callable workflow contract parsing with `inputMode` / `outputMode`, text input normalization, and text output normalization.
- [x] 2.2 Update callable workflow listing, workflow-call validation, and runtime input/output mapping for text and structured child workflows.
- [x] 2.3 Add runtime workflow identity plumbing and workflow-call child memory persistence/merge logic.
- [x] 2.4 Update workflow test session memory normalization and nested `workflowCallScopes` round-trip behavior.
- [x] 2.5 Add Alembic migration and backend data model for persisted workflow-call child memory scopes.

## 3. Frontend
- [x] 3.1 Extend callable workflow API/types with `inputMode` / `outputMode`.
- [x] 3.2 Update workflow-call settings/output hints for text callable workflows.
- [x] 3.3 Update workflow test session memory types/store/panel to preserve nested `workflowCallScopes` and send structured-root session state.

## 4. Tests And Validation
- [x] 4.1 Add or update backend tests for text callable workflow discovery/runtime and workflow-call child memory persistence/round-trip.
- [x] 4.2 Run backend targeted tests and backend compile validation.
- [x] 4.3 Run frontend `npx tsc --noEmit` and `npm run build`.

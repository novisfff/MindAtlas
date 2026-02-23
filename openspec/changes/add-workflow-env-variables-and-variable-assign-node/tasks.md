## 1. Backend
- [x] 1.1 Add `variable_assign` to workflow node type enums and node metadata.
- [x] 1.2 Implement shared ENV variable helpers for parsing, coercion, and operations.
- [x] 1.3 Extend workflow validator for start `sessionVars` and `variable_assign` config rules.
- [x] 1.4 Extend template variable validation/runtime resolution to support `env.<name>`.
- [x] 1.5 Add `variable_assign` runtime builder and execution in main graph.
- [x] 1.6 Support ENV read/write propagation inside `iteration`/`loop` container body execution.

## 2. Frontend
- [x] 2.1 Extend workflow API/node types for `variable_assign` and start `sessionVars`.
- [x] 2.2 Add node catalog/canvas/quick-add integration for `variable_assign` (main graph + container body).
- [x] 2.3 Add ENV variable management UI (panel + create/edit dialog) and write-back to start config.
- [x] 2.4 Add `variable_assign` property panel with variable selection, operation, and value template.
- [x] 2.5 Extend variable references and mention transforms to include `env.<name>`.
- [x] 2.6 Add i18n keys for ENV panel and variable assign node.

## 3. Tests
- [x] 3.1 Add validator tests for ENV var definitions, `env` references, and `variable_assign` validation.
- [x] 3.2 Add runtime tests for `set`, `increment`, `append`, and container-body ENV behavior.
- [x] 3.3 Add stream snapshot assertion coverage for `variable_assign` payload.

## 4. Spec
- [x] 4.1 Add OpenSpec change files (`proposal`, `design`, `tasks`, `spec delta`).
- [x] 4.2 Validate with `openspec validate add-workflow-env-variables-and-variable-assign-node --strict --no-interactive`.

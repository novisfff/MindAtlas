## 1. Backend
- [x] 1.1 Add `code_executor` to workflow node type enums and node metadata.
- [x] 1.2 Implement sandbox runtime module (`code_executor.py`) and Python/JS runners.
- [x] 1.3 Add validator rules for code executor config and import whitelist checks.
- [x] 1.4 Integrate code executor builder into `LangGraphEngine` main graph execution.
- [x] 1.5 Integrate code executor execution into container body (`iteration`/`loop`).
- [x] 1.6 Add runtime limits to config (`timeout`, `max timeout`, `memory`, `max output chars`, module whitelists).

## 2. Frontend
- [x] 2.1 Extend node types and config types for `code_executor`.
- [x] 2.2 Add node catalog entry and node visual mapping.
- [x] 2.3 Add property panel editor (`CodeExecutorNodeSettings`).
- [x] 2.4 Support quick-add and container-body node creation for `code_executor`.
- [x] 2.5 Extend variable references to include code-executor declared output fields.
- [x] 2.6 Add i18n keys for code-executor editor labels and node-type label.

## 3. Tests
- [x] 3.1 Add validator tests for code-executor workflow validation.
- [x] 3.2 Add runtime tests for Python/JS success, schema mismatch, timeout, and container-body execution.
- [x] 3.3 Add publish-gate tests to ensure invalid code blocks publish.

## 4. Spec
- [x] 4.1 Add OpenSpec change files (`proposal`, `design`, `tasks`, `spec delta`).
- [x] 4.2 Validate with `openspec validate add-workflow-code-executor-node --strict --no-interactive`.

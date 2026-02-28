## 1. OpenSpec
- [x] 1.1 Create proposal/tasks/spec delta for iteration/loop container nodes
- [x] 1.2 Validate change with `openspec validate add-workflow-iteration-loop-container-nodes --strict --no-interactive`

## 2. Backend
- [x] 2.1 Extend node types with `iteration` and `loop`
- [x] 2.2 Add validator rules for container configs and body subflow constraints
- [x] 2.3 Add runtime node builders and subflow execution helper
- [x] 2.4 Add dependency collection for tools/models inside container body nodes

## 3. Frontend
- [x] 3.1 Extend workflow API types for iteration/loop and container body schema
- [x] 3.2 Add palette/node style/label support for new node types
- [x] 3.3 Add node settings UI for iteration/loop configs
- [x] 3.4 Add reference transform + variable reference support for container fields
- [x] 3.5 Add i18n keys for iteration/loop and container UI

## 4. Validation & Tests
- [ ] 4.1 Add/update backend tests for validator and runtime behavior
- [ ] 4.2 Run frontend build and backend test subset

## 1. OpenSpec
- [x] 1.1 Create proposal/tasks/design/spec delta for output-node architecture
- [x] 1.2 Validate change with `openspec validate add-workflow-output-node --strict --no-interactive`

## 2. Backend
- [x] 2.1 Extend backend workflow node types with `output`
- [x] 2.2 Add output node builder and terminal output emission in LangGraph engine
- [x] 2.3 Add caller-controlled streaming switch (`stream_output`) and wire to runtime
- [x] 2.4 Update workflow validator rules (exactly one output, terminal, config checks, container restrictions)
- [x] 2.5 Update built-in workflow skill definitions to end at output node
- [x] 2.6 Add one-time migration script for legacy `llm.isOutput`

## 3. Frontend
- [x] 3.1 Extend workflow types and node factory defaults for `output`
- [x] 3.2 Add palette/rendering/property-panel support for output node
- [x] 3.3 Remove LLM final-output toggle and related node preview marker
- [x] 3.4 Enforce single output node and no output source handle in main canvas
- [x] 3.5 Update default seed workflow to `start -> llm -> output`
- [x] 3.6 Update i18n labels and descriptions for output node UX

## 4. Validation & Tests
- [x] 4.1 Update backend validator/runtime/system-workflow tests to output-node semantics
- [ ] 4.2 Run frontend build and backend tests (`pytest -q`) in full dependency environment

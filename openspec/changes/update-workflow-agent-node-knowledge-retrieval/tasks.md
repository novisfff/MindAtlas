## 1. Backend Runtime
- [x] 1.1 Extend `workflow_dag.agent` runtime to bind a built-in `kb_search` tool when `knowledgeEnabled=true`.
- [x] 1.2 Fix the built-in KB tool to expose only `query` to the model and inject node-level `mode/topK` overrides internally.
- [x] 1.3 Append KB citation instructions to the agent system prompt only when KB is enabled.
- [x] 1.4 Keep `tool_call_*`, maxIterations, single-tool-per-round, and terminal failure semantics unchanged.

## 2. Validation + Dependencies
- [x] 2.1 Extend save/compile validator to accept `knowledgeEnabled/knowledgeMode/knowledgeTopK` on `agent` nodes.
- [x] 2.2 Reject `kb_search` inside `agent.toolNames` with guidance to use `knowledgeEnabled`.
- [x] 2.3 Allow KB-only agents by validating that at least one normal tool or KB capability is configured.
- [x] 2.4 Treat `knowledge_retrieval` nodes and KB-enabled `agent` nodes as implicit `kb_search` workflow dependencies.

## 3. Frontend Editor
- [x] 3.1 Extend `AgentNodeConfig` with KB fields and align default config.
- [x] 3.2 Add KB controls to `AgentNodeSettings` and keep `kb_search` hidden from normal tool selection.
- [x] 3.3 Preserve KB fields through workflow JSON serialization / deserialization and test-run snapshots.
- [x] 3.4 Update i18n copy for agent KB controls.

## 4. Tests + Verification
- [x] 4.1 Add/extend backend validator tests for valid/invalid KB agent configs and `kb_search` prohibition.
- [x] 4.2 Add/extend backend runtime tests for built-in KB binding, fixed `mode/topK`, and KB prompt injection.
- [x] 4.3 Add/extend dependency collection tests for KB-enabled agents and `knowledge_retrieval` nodes.
- [x] 4.4 Run key backend pytest suites.
- [x] 4.5 Run frontend `npm run build`.
- [x] 4.6 Run `openspec validate update-workflow-agent-node-knowledge-retrieval --strict --no-interactive`.

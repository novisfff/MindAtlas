## 1. Backend Node Type + Validation
- [x] 1.1 Add `agent` to backend node type enums/contracts and node type metadata API.
- [x] 1.2 Extend save-time validation for `agent` config (`toolNames`, `maxIterations`, prompt fields, modelSource/modelId).
- [x] 1.3 Extend compile-time validation for `agent` config and tool existence checks.
- [x] 1.4 Ensure container body (`iteration/loop`) applies the same `agent` validation rules.

## 2. Backend Runtime
- [x] 2.1 Add DAG runtime builder for `agent` node with serial tool loop.
- [x] 2.2 Restrict tool invocation to node-level `toolNames` whitelist.
- [x] 2.3 Enforce max-iteration failure semantics and tool failure terminal semantics.
- [x] 2.4 Wire `agent` builder into DAG assembler and container runtime.
- [x] 2.5 Align streaming and output passthrough behavior for `agent` source nodes.

## 3. Backend Dependencies + Model Resolution
- [x] 3.1 Include `agent.toolNames` in workflow dependency collection (main + container body).
- [x] 3.2 Include `agent` custom model dependencies (main + container body).
- [x] 3.3 Extend workflow node model resolver to support `agent` runtime keys.

## 4. Frontend Editor
- [x] 4.1 Add `agent` to workflow node types and default node factory config.
- [x] 4.2 Add `agent` to node catalog/canvas/icon/labels for main flow and container body.
- [x] 4.3 Add `AgentNodeSettings` property panel UI (model, prompts, toolNames, maxIterations).
- [x] 4.4 Expose `agent.response` in variable references.
- [x] 4.5 Add i18n copy for `agent` node and form labels.

## 5. Tests + Verification
- [x] 5.1 Add/extend workflow validator tests for `agent` (main + container body).
- [x] 5.2 Add/extend runtime streaming tests for `agent` node execution and failure paths.
- [x] 5.3 Add/extend config service tests for `agent` dependency and model collection.
- [x] 5.4 Run key backend pytest suites and frontend build.
- [x] 5.5 Run `openspec validate add-workflow-agent-node --strict --no-interactive`.

## 1. Shared Backend Kernel
- [x] 1.1 Add shared `agent_execution_core` for LLM rounds, single-tool loop, KB tool binding, and trace hooks.
- [x] 1.2 Normalize shared stop reasons, tool call metadata, and default max-iteration behavior.

## 2. agent_loop Wrapper Migration
- [x] 2.1 Replace the old multi-node `agent_loop` runtime path with a thin wrapper around the shared core.
- [x] 2.2 Preserve top-level skill prompt + KB integration semantics without adding new public config.
- [x] 2.3 Preserve top-level memory semantics: no extra L0 message injection, only existing system memory block behavior.

## 3. workflow_dag.agent Wrapper Migration
- [x] 3.1 Rebuild workflow DAG `agent` execution around the shared core.
- [x] 3.2 Preserve node-level tool whitelist, KB config, NodeOutput shaping, and `memoryMode` semantics.
- [x] 3.3 Preserve workflow trace context and output passthrough behavior.

## 4. Tests + Verification
- [x] 4.1 Add/extend shared-core regression coverage for `agent_loop` tool calls, KB calls, and tool failures.
- [x] 4.2 Re-run workflow agent, validator, workflow test-run, chat stream/stop, service persistence, and trace regression suites.
- [x] 4.3 Run frontend build baseline and OpenSpec strict validation.

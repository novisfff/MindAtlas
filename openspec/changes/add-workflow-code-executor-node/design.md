## Context
Workflow nodes currently cannot execute inline code. This causes overuse of external tools for trivial transformation tasks and makes some deterministic logic difficult to express. The code executor must keep workflow safety properties and preserve current API/database contracts.

## Goals
- Introduce `code_executor` node for Python/JavaScript scripts.
- Keep route contracts unchanged.
- Ensure strict runtime guardrails (sandbox, timeout, memory, output schema).
- Support main graph and container body execution.
- Keep current node/edge storage format (node config JSON).

## Non-Goals
- Dynamic dependency installation (`pip`/`npm`).
- Multi-file script projects.
- Network or local filesystem access from scripts.
- Container-level hard isolation in this iteration.

## Decisions
- Runtime model:
  - Execute user code in a subprocess wrapper.
  - Python runner uses restricted builtins and import whitelist.
  - JavaScript runner uses `node:vm` sandbox and guarded `require` whitelist.
- Script contract:
  - Entrypoint defaults to `main`.
  - Signature: `main(inputs, context)`.
  - Return must be an object.
- Validation model:
  - Save/publish/compile validation checks language, code, entrypoint, input bindings, output schema.
  - Static import checks reject disallowed modules and dynamic imports.
- Runtime output model:
  - Strictly match declared `outputFields` (missing/extra/type mismatch fail).
  - Node output emits JSON text plus `json_fields` for downstream template references.
- Error policy:
  - Any execution error is fatal for the node and interrupts workflow execution.
- Config and limits:
  - Defaults are configurable via env (`WORKFLOW_CODE_EXECUTOR_*`).
  - Default timeout 5s and memory limit 128MB.

## Risks / Trade-offs
- `node:vm` and Python restricted builtins reduce risk but are not kernel-grade isolation.
- Very strict schema matching may require users to define output contracts more explicitly.
- Subprocess execution introduces overhead per code node invocation.

## Migration / Compatibility
- No DB migration.
- Existing workflows without `code_executor` are unaffected.
- Node type metadata and editor catalog are additive.

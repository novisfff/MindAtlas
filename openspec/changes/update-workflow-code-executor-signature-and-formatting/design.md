## Context
The `code_executor` node already exists in runtime and editor. Recent changes over-constrained the node to fixed keys/signature (`arg1`/`arg2`), which conflicts with user expectations for configurable inputs.

## Goals
- Keep deterministic validation/runtime behavior.
- Keep default bindings (`arg1`/`arg2`) for fast start.
- Allow flexible input keys for real workflows.
- Consistent validation/publish/runtime behavior.
- Better authoring UX and Python formatting quality.

## Non-Goals
- No database schema migration.
- No backend formatting API.
- No compatibility bridge for legacy signatures.

## Decisions
- Runtime contract is dynamic: user function parameters must match `inputBindings` keys.
- Validator enforces exact key-set equality between signature parameters and input binding keys.
- `arg1`/`arg2` are default initial keys only, not required keys.
- Editor uses one-line dynamic binding rows with add/remove/rename capabilities.
- Python formatting uses Ruff WASM (`@wasm-fmt/ruff_fmt/vite`) with lazy initialization.

## Risks / Trade-offs
- Existing workflows with fixed `arg1`/`arg2` continue working.
- Workflows with signature/binding mismatch will be blocked until corrected.
- Ruff WASM adds a frontend dependency and initialization overhead; mitigated by lazy init and cached promise.

## Migration Plan
1. Save-time and compile-time validation enforce signature-key matching.
2. Editor seeds default `arg1`/`arg2` only when bindings are missing.
3. Test coverage ensures runtime/publish/validator enforce dynamic matching.

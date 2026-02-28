## Context
- Workflow execution already supports node outputs and template references, but has no mutable cross-node run state.
- Existing storage and versioning rely on workflow node `config` snapshots; we should avoid DB schema migration.
- Runtime behavior must remain deterministic and fail-fast when variable operations are invalid.

## Goals
- Provide session-scoped workflow ENV variables initialized from start-node defaults each run.
- Provide a first-class assignment node to mutate ENV values during execution.
- Keep compatibility with current workflow serialization/versioning pipeline.
- Support both main graph and container body execution.

## Non-Goals
- Persistent ENV state across workflow runs.
- Variable deletion/unset operation.
- New database tables/columns for ENV metadata.

## Decisions
- ENV definitions are stored in main graph start node config (`sessionVars` / `session_vars`).
- ENV references use reserved namespace `env.<name>`.
- New node type `variable_assign` supports `set`, `increment`, `append`.
- Runtime state extends workflow state with:
  - `env_specs`: normalized variable definitions
  - `env_vars`: current variable values
- `append` works only for:
  - string: concatenation
  - array: append/extend
- Any ENV type mismatch, unknown variable, invalid operation, or coercion error fails node execution and aborts run.

## Risks / Trade-offs
- More validator rules increase strictness; legacy malformed graphs may fail validation until fixed.
- Container body ENV mutation requires state propagation at each body node execution; this adds execution-path complexity.
- Frontend now has two editing surfaces (node panel + ENV side panel), which increases UX coordination requirements.

## Migration Plan
1. Add type/schema support (`variable_assign`, `sessionVars`) in backend and frontend.
2. Add validator and runtime support for ENV initialization, reference resolution, and mutation.
3. Add workflow editor ENV management panel and variable assign node settings.
4. Add i18n keys and mention/reference support for `env.<name>`.
5. Add validator/runtime tests and stream snapshot assertions.

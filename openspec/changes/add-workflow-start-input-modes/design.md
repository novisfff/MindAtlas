## Context
Workflow DAG currently assumes `start.user_input` text-only semantics. We need to support structured start input while preserving backward compatibility for existing text workflows and preventing incompatible skill bindings.

## Goals
- Introduce explicit start input mode (`text` / `structured`) without DB migration.
- Keep existing workflows functional by default (`text` mode).
- Enable structured input in test-run and runtime execution.
- Enforce hard guard that structured workflows cannot be skill-bound.

## Non-Goals
- Nested structured schemas (`object`/`array` trees).
- Automatic migration of existing templates from `start.user_input`.
- Skill-level version pinning or protocol changes.

## Start Config Model
- Stored in start node `config` JSON:
  - `inputMode: "text" | "structured"`
  - `structuredFields: [{ name, type, required, description? }]`
- Field constraints:
  - name regex: `[a-zA-Z_][a-zA-Z0-9_]*`
  - unique per workflow start config
  - reserved name `user_input` forbidden in structured mode
  - type restricted to `string | number | integer | boolean`

## Runtime Semantics
- `text` mode:
  - start output keeps `json_fields.user_input`
  - templates continue using `{{start.user_input}}`
- `structured` mode:
  - runtime consumes `structured_input` payload from test-run context
  - start output fields are only configured structured field names
  - `start.user_input` is not emitted
  - unknown input keys, missing required keys, and type mismatch fail execution

## Validation Semantics
- Workflow validation checks:
  - start config shape and structured field constraints
  - template references to `start.*` must match allowed start fields by mode
- Additional validation checks for workflow-level route:
  - if workflow currently has skill references, validating/saving/publishing structured mode reports blocking error

## Skill Binding Guard
- Frontend:
  - structured workflows shown in binding selector but disabled with reason
- Backend:
  - binding structured workflow to skill returns 422
  - switching referenced workflow to structured mode returns 409
- Defense-in-depth keeps behavior safe even for direct API calls.

## Backward Compatibility
- Existing workflows with `start.config = null` are treated as `text` mode.
- Deserialization fallback auto-fills default start config on frontend.
- Existing text templates continue unchanged.

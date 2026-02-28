## Context
`human_in_loop` approval currently serializes only `name/label/type/required` per field and runtime UI only renders basic text/number/boolean controls. The workflow editor needs richer field controls while preserving existing HITL records and workflows.

## Goals / Non-Goals
- Goals:
  - Add schema-driven widget metadata for HITL fields.
  - Keep main graph and container body behavior consistent.
  - Reuse shared HITL runtime components across test-run and assistant chat.
  - Preserve backward compatibility for old approvals and old workflow configs.
- Non-Goals:
  - No DB schema changes.
  - No new HTTP routes.
  - No cross-language value conversion/migration for existing approval rows.

## Decisions
- Decision: Extend field schema with `widget`, `options`, `allowCustom`, `placeholder` while keeping `type` authoritative for value coercion.
- Decision: Add `array` type only for tag selector values (`string[]`).
- Decision: Strict option enforcement for `select`/`radio`; extensible tags for `tag_selector` when `allowCustom=true`.
- Decision: Date/time formats are fixed to string formats `YYYY-MM-DD` and `HH:mm`.
- Decision: Backward compatibility defaults use inferred widget (`boolean -> switch`, else `input`).

## Risks / Trade-offs
- Risk: Frontend and backend coercion mismatch for date/time and options.
  - Mitigation: enforce the same rules in backend runtime and frontend pre-submit validation.
- Risk: Old approvals missing widget metadata.
  - Mitigation: widget inference fallback in shared runtime UI and backend coercion.

## Migration Plan
1. Deploy backend validator/runtime changes first.
2. Deploy frontend schema editor + approval renderer updates.
3. Keep old records functional via widget inference fallback.

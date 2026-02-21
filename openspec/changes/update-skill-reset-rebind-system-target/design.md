## Context
System skills are expected to be recoverable to a stable baseline. Existing reset logic may operate on currently bound targets directly and can miss agent runtime reset behavior when rebinding to existing profiles.

## Goals
- Make reset deterministic for system skills.
- Protect user-created targets from reset mutations.
- Ensure reset actually refreshes bound workflow/agent runtime content.
- Keep reset UX explicitly destructive with clear user intent confirmation.

## Non-Goals
- Add backend `confirmPhrase` protocol.
- Introduce version pinning per skill.
- Change external reset API shape.

## Decisions
- Reset target resolution:
  - Prefer currently bound target only when it is system-owned.
  - Else resolve by canonical system name (`<skill>__workflow` / `<skill>__agent`) with `is_system=true`.
  - If missing, create the canonical system target.
- Reset execution:
  - Reapply baseline workflow/agent config from JSON defaults.
  - Create a publish version for reset result.
  - Prune history to keep only this reset-generated publish version.
  - Set both draft/published pointers to that kept version.
- Frontend safety UX:
  - Step 1 warning with explicit impact summary.
  - Step 2 typed `RESET` check before mutation request.
- Cache consistency:
  - Invalidate skill list, target lists, editor detail queries, and version-history queries after reset/reset-all.

## Risks / Trade-offs
- Rebinding to canonical system targets can fail if a custom target occupies canonical name.
  - Mitigation: return explicit 409 conflict.
- Aggressive history pruning removes older system target versions during reset.
  - Mitigation: this is intentional for reset semantics and explicitly warned in UI.

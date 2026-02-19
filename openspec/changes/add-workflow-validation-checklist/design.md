## Context
Workflow validation exists in backend (`validate-workflow`) and save flow, but editor lacks a persistent visibility mechanism. Users need immediate feedback while editing and direct navigation from issue list to graph nodes, including container subflow nodes.

## Goals
- Continuously validate workflow with debounce while editing.
- Show current error count in toolbar without opening panel.
- Provide a detailed checklist with actionable locate behavior.
- Keep backend API unchanged and reuse existing validation semantics.

## Non-Goals
- Introduce new backend validation endpoints.
- Block editing/saving on warning-level issues.
- Add persistent validation history storage.

## Decisions
- Reuse existing backend `validate-workflow` as the authoritative error source.
- Add frontend warning-only reachability analysis for nodes not connected to final output.
- Count only error severity in toolbar badge.
- Use request sequence guards to avoid stale validation response overwrites.
- Parse container body errors (`body node 'x'`) to support subflow locate routing.

## Risks / Trade-offs
- Continuous validation can increase API frequency; mitigated with signature-based debounce.
- Warning computation is frontend-only and complements backend; may diverge if backend adds equivalent rule later.
- For subflow errors without parseable inner node id, locate falls back to container-level selection.

## Rollout
- Frontend-only change with no API contract changes.
- OpenSpec validated in strict mode.
- Existing save-time validation remains unchanged as final guard.

## Context
System skills, system AI behaviors, and OpenClaw system items now all depend on reusable workflow and agent targets. That reuse makes in-place editing of shipped system targets risky because one mutation can silently change multiple product surfaces. The desired product model is to treat shipped system targets as official baselines and move all customization to an explicit copy-first flow.

## Goals
- Make shipped system workflows and system agent profiles immutable baselines.
- Provide a consistent duplicate flow for both workflows and agents.
- Force drifted system targets back to canonical shipped defaults while preserving stable IDs for existing bindings.
- Keep system binding layers editable so users can rebind skills, system AI behaviors, and OpenClaw items to their own copies.

## Non-Goals
- Remove the ability to test-run or inspect system targets.
- Rebuild user-created workflow or agent copies during reset.
- Make system skills, system AI behaviors, or OpenClaw system items themselves read-only.

## Decisions

### 1. System targets become shipped read-only baselines
- `is_system=true` workflows and agent profiles reject update, publish, rollback, delete-version, clear-versions, and delete operations.
- Read-only surfaces still expose list/detail/history plus validate/test-run behavior.
- The editor UX becomes a viewer with copy-first messaging instead of a save-first workspace.

### 2. Copy APIs create immediately bindable user-owned duplicates
- `POST /api/assistant-config/workflows/{id}/copy`
- `POST /api/assistant-config/agents/{id}/copy`
- Copying a system target clones the canonical baseline, not any mutated live state.
- Copying a custom target clones the current draft state.
- New duplicates are always `is_system=false`, get an auto-generated localized duplicate name, and are published immediately so upper-layer bindings can switch to them without an extra publish step.

### 3. Reconciliation restores canonical baselines in place
- Sync/ensure logic rewrites shipped system workflow and agent targets from canonical defaults.
- Target IDs stay stable so existing skill/system-behavior/OpenClaw bindings remain valid.
- Each restored system target collapses to a single published baseline version with draft/published heads aligned.
- Reconciliation now checks both published snapshots and the current entity state so direct DB mutations to nodes, positions, or runtime fields are corrected as well.

### 4. Binding layers remain editable but become copy-first aware
- System skills can still reset/rebind to canonical system targets or switch to user copies.
- System AI behavior example workflow creation reuses the same workflow copy helper.
- OpenClaw system items can still choose system workflow/agent sources or user copies, but the UI reminds admins that system targets must be copied before customization.

## Risks / Trade-offs
- This is intentionally destructive for prior in-place edits to system targets.
  - Mitigation: preserve IDs and offer a fast duplicate path for future customization.
- Immediate reconciliation on read/sync paths can surface conflicts sooner.
  - Mitigation: keep copy-first errors explicit and preserve user-owned duplicates untouched.
- Copying from canonical system baselines instead of live state may surprise users who previously modified system targets directly.
  - Mitigation: make reset/read-only messaging explicit in target editors and binding screens.

## Migration Plan
1. Add immutable guards and copy APIs in assistant-config services and routers.
2. Reconcile shipped system workflow and agent targets back to canonical defaults, preserving IDs and collapsing version history.
3. Update frontend target editors, list cards, version panels, and binding UIs to use copy-first messaging and duplicate actions.
4. Re-run targeted orchestration/OpenClaw tests plus spec validation.

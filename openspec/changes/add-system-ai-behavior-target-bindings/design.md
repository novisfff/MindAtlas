## Context
The assistant orchestration system already supports reusable workflow and agent targets with publish/version semantics, while system skills can bind to canonical system-owned targets. Weekly and monthly reports, however, still bypass that stack and call the default assistant model directly. The goal is to move those built-in behaviors onto the same target execution model without reusing skill bindings or legacy prompt fallback logic.

## Goals
- Add an extensible registry for system AI behaviors.
- Allow each behavior to bind to either a published workflow or a published agent profile.
- Ensure execution always uses published snapshots and fixed structured contracts.
- Provide canonical system default targets for built-in behaviors and deterministic fallback behavior.
- Keep skill references and system-behavior references distinct in delete semantics and UI.

## Non-Goals
- Generalize all current assistant actions to system AI behaviors in one change.
- Reuse `periodic_review` as the canonical report default target.
- Preserve legacy direct-model report generation as a runtime fallback.

## Decisions
- Persistence:
  - Add `assistant_system_behavior_binding` with unique `behavior_key`.
  - `workflow_id` and `agent_profile_id` are mutually exclusive.
- Behavior registry:
  - Registry metadata lives in Python code.
  - Canonical workflow baselines live in dedicated JSON presets separate from system skill manifests.
  - Initial behaviors are `weekly_report_generation` and `monthly_report_generation`.
- Execution:
  - `SystemAiBehaviorRunner` resolves the current binding, loads the published snapshot, wraps it into a `SkillDefinition`, executes it through `LangGraphEngine`, and validates fixed JSON output `{summary, suggestions, trends}`.
  - Workflow bindings require structured start input with required fields `periodType`, `periodStart`, `periodEnd`, `entryCount`, and at least one structured output node exposing `summary`, `suggestions`, and `trends`.
  - Agent bindings reuse the published agent snapshot, but the runner injects system behavior instructions so the final result must satisfy the same JSON contract.
  - If the bound target is disabled, deleted, unpublished, or otherwise unusable, execution falls back to the canonical system default target for that behavior.
- Canonical defaults:
  - Each behavior owns a separate system workflow target and binding.
  - Canonical names are `system_weekly_report__workflow` and `system_monthly_report__workflow`.
  - Sync/ensure logic is idempotent and can run on read/write paths that require these behaviors.
- Delete semantics:
  - `referenceCount` continues to mean skill references only.
  - Workflow/agent payloads add `systemBehaviorReferenceCount` and `referencedSystemBehaviorKeys`.
  - If a user target is referenced only by system behaviors, first delete attempt returns a conflict describing impacted behaviors.
  - A confirmed delete performs atomic "rebind impacted behaviors to canonical defaults, then delete target".

## Risks / Trade-offs
- Published-snapshot validation is stricter than current workflow/agent usage and may reject some previously valid reusable targets for system behavior binding.
  - Mitigation: validation errors are returned explicitly at bind time.
- Runtime fallback can hide stale bindings if users do not revisit settings.
  - Mitigation: delete-confirm flows rebind persistently, and UI surfaces current binding/default target explicitly.
- Canonical system target names can conflict with pre-existing user targets.
  - Mitigation: fail with explicit conflict instead of silently mutating user content.

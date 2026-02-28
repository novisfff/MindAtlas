## Context
Workflow and Agent are now independent executable targets bound by skills. We need a safe workflow where editing does not immediately impact runtime and operators can publish intentionally.

## Goals
- Keep a complete target version history for explicit save/publish operations.
- Ensure runtime always reads published configuration.
- Support rollback-to-draft for both target types.
- Keep API and UI semantics consistent across workflow and agent editors.

## Non-Goals
- Version pinning at skill-binding level.
- Multi-branch drafts.
- Release channels or staged rollouts.

## Data Model
- Add tables:
  - `assistant_workflow_version`
  - `assistant_agent_profile_version`
- Add target head pointers:
  - `assistant_workflow.draft_version_id`
  - `assistant_workflow.published_version_id`
  - `assistant_agent_profile.draft_version_id`
  - `assistant_agent_profile.published_version_id`
- Version rows store immutable snapshots and metadata:
  - `sequence_no`, `version_name`, `version_source(save|publish)`, `snapshot`.

## API Model
- Save:
  - Workflow: `PUT /workflows/{id}` with `workflow` creates `save` version only.
  - Agent: `PUT /agents/{id}` with runtime fields creates `save` version only.
- Publish:
  - Workflow: `POST /workflows/{id}/publish` applies snapshot to published graph + writes `publish` version.
  - Agent: `POST /agents/{id}/publish` applies runtime config to main profile fields + writes `publish` version.
- Rollback:
  - Sets draft head to selected historical version.
  - Returns restored draft payload to hydrate editor.
  - Never updates published head.
- Delete single version:
  - Workflow: `DELETE /workflows/{id}/versions/{version_id}`.
  - Agent: `DELETE /agents/{id}/versions/{version_id}`.
  - Guarded versions (`draft`, `published`, and system baseline publish) are rejected.
- Clear draft history:
  - Workflow: `POST /workflows/{id}/versions/clear`.
  - Agent: `POST /agents/{id}/versions/clear`.
  - Only removes non-latest `save` versions, while preserving protected pointers.

## Runtime Behavior
- Workflow runtime keeps using main workflow node/edge tables (published graph).
- Agent runtime keeps using main agent profile runtime fields (published config).
- Therefore, drafts are isolated from production execution until publish.

## Retention Strategy
- Keep latest 100 versions per target.
- Always preserve versions referenced by draft/published heads.
- For system targets, also preserve the earliest `publish` version as baseline so UI can always show a stable “system default” entry.

## Version History UI Strategy
- For system workflow/agent targets, version history panel computes a synthetic highlighted entry from existing data:
  - choose earliest `publish` version (`sequence_no` minimum) as “system default”.
  - pin this entry to top of history list.
  - keep normal restore-to-draft action enabled.
- No backend API schema change is required; panel derives this from existing fields.
- Add two direct maintenance actions:
  - row-level delete action.
  - header-level clear action for draft-only history cleanup.

## Publish Validation Gate
- Workflow publish MUST pass backend validation at publish time.
- Validation gate checks are aligned with validate API semantics (topology + parallel + dependency checks).
- When validation fails, publish returns 422 and no publish version/pointer update occurs.

## Risks / Trade-offs
- Legacy rows with invalid snapshot data could fail rollback parsing.
  - Mitigation: validate snapshot on rollback and return explicit error.
- Save frequency can increase DB writes.
  - Mitigation: bounded retention + lightweight snapshots.

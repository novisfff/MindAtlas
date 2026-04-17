# Change: Remove Workflow Node/Edge Persistence And Use Version Snapshots

## Why
Current workflow persistence keeps two graph representations in parallel:
- current workflow graph in relational `assistant_workflow_node` / `assistant_workflow_edge`
- historical versions in `assistant_workflow_version.snapshot`

This dual-write model increases service complexity, forces repeated graph rebuilds, and introduces avoidable write-path contention on edge uniqueness constraints. The codebase already treats version snapshots as the authoritative format for version history, callable contract derivation, workflow-call resolution, rollback, and many system-baseline checks. We should make version snapshots the single graph source and remove node/edge table persistence entirely.

## What Changes
- Refactor workflow persistence so the current draft graph is always resolved from `draft_version_id -> assistant_workflow_version.snapshot`.
- Keep `assistant_workflow` as workflow metadata plus draft/published head pointers only.
- Remove live persistence and loading of:
  - `assistant_workflow_node`
  - `assistant_workflow_edge`
  - `assistant_skill_node`
  - `assistant_skill_edge`
- Update workflow create/update/publish/rollback/copy/system-baseline logic to operate only on version snapshots.
- Keep API payloads and frontend editor protocol unchanged: workflow details still return `nodes`, `edges`, and `workflowViewport`, but these fields are materialized from the draft snapshot rather than relational child rows.
- Add a migration that backfills any missing draft snapshots from legacy node/edge rows before dropping the legacy tables.

## Impact
- Affected specs:
  - `assistant-orchestration`
- Affected code:
  - `backend/app/assistant_config/models.py`
  - `backend/app/assistant_config/service.py`
  - `backend/app/assistant_config/router.py`
  - `backend/alembic/versions/*`
  - `backend/tests/test_assistant_config_service*.py`
  - `backend/tests/test_system_ai_behavior_bindings.py`

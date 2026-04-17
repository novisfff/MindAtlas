## Context
The assistant configuration system currently stores workflow graphs twice:
- relational child rows on workflow/skill entities for the current graph
- JSON snapshots in version tables for history

Over time, the snapshot path has become the more important abstraction. Versioning, publish/rollback, workflow-call resolution, callable workflow contract extraction, and many system-baseline checks already operate on snapshots.

The relational child tables now mostly add operational cost:
- repeated delete-and-reinsert graph writes
- deadlock risk on edge uniqueness indexes
- duplicate conversion code between ORM rows and `WorkflowInput`
- more complicated system sync and restore paths

## Goals
- Make workflow version snapshots the single source of truth for workflow graphs
- Preserve current external API shapes and editor behavior
- Remove legacy node/edge tables for both workflows and skills
- Keep draft/published head semantics unchanged

## Non-Goals
- Redesign workflow editor request/response schemas
- Change workflow version retention behavior
- Change OpenClaw contracts

## Decisions
- Decision: `assistant_workflow_version.snapshot` becomes the only persisted graph payload for workflows.
  - Rationale: the snapshot shape already matches the service/runtime contract and supports version-aware operations directly.

- Decision: `assistant_workflow` keeps metadata and version-head columns only.
  - Rationale: workflow name, description, enabled/system state, and draft/published pointers remain useful relational metadata.

- Decision: workflow detail APIs will continue returning `nodes`, `edges`, and `workflowViewport`.
  - Rationale: this avoids unnecessary frontend/editor churn; only the storage source changes.

- Decision: draft graph reads will resolve from `draft_version_id` first and published graph reads from `published_version_id` where appropriate.
  - Rationale: this aligns current graph resolution with the already-defined versioning model.

- Decision: legacy node/edge rows will be backfilled into snapshots only where draft heads are missing or invalid during migration.
  - Rationale: avoid data loss while still allowing hard deletion of obsolete tables.

## Risks / Trade-offs
- Risk: some old service code may still implicitly expect ORM child relationships.
  - Mitigation: remove or rewrite those call sites rather than leaving silent fallbacks.

- Risk: migration may encounter workflows created before versioning that rely on node/edge rows.
  - Mitigation: migration must synthesize a snapshot from legacy rows before dropping tables.

- Risk: system workflow baseline restore may have hidden dependencies on current entity rows.
  - Mitigation: update baseline comparisons to compare desired baseline vs draft/published snapshots directly.

## Migration Plan
1. Add snapshot-only service/model support while legacy tables still exist.
2. Backfill missing workflow snapshots from relational rows.
3. Remove application references to node/edge ORM models.
4. Drop workflow/skill node-edge tables in Alembic.
5. Re-run workflow/version/system-target regression suites.

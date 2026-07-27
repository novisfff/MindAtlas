# Plan 10 — Legacy surface final pass (2026-07-23)

## Changes
- Removed skill-scoped FE workflow APIs (`saveWorkflow`/`validateWorkflow` nested under `/skills/...`)
- `runWorkflowTestStream` requires explicit standalone path
- Slimmed `legacy_adapter.py` by deleting dead shadow publish/materialize helpers
- Removed `legacy_skill_id` from skill package/profile API schemas and serializers
- Fixed config service skill-stub annotations after schema removal
- Kept pure migration helpers: name map, digests, render, target resolve

## Verification
158 passed / 89 skipped focused residual suite including PG migration repository tests.

# Change: Refactor Standalone System Targets And Remove OpenClaw Special-Casing

## Why
`submit_context_capture` currently depends on a workflow asset that is materialized through OpenClaw-specific registry metadata rather than through assistant-config's normal system target lifecycle. That makes one system workflow behave differently from every other system workflow and leaks an OpenClaw-specific canonical name into internal and user-visible surfaces.

## What Changes
- Add assistant-config-owned standalone system workflow assets for persistent `is_system=true` targets that do not belong to a system skill or system AI behavior default binding.
- Move the context capture workflow into that shared standalone registry and rename its canonical workflow asset to `system_context_capture__workflow`.
- Remove OpenClaw-owned workflow preset materialization for `submit_context_capture`; OpenClaw should bind to the shared standalone system workflow asset instead.
- Keep the shipped OpenClaw capability key, tool name, schemas, summaries, and runtime behavior unchanged.
- Update display-name resolution, migration compatibility, and tests so the renamed system workflow appears as a normal read-only system workflow everywhere.

## Impact
- Affected specs:
  - `assistant-orchestration`
  - `external-agent-integration`
- Affected code:
  - `backend/app/assistant_config/*`
  - `backend/app/openclaw_integration/*`
  - `backend/app/assistant/skill_catalog/system_defaults/workflows/*`
  - `backend/alembic/versions/*`
  - `backend/tests/*`

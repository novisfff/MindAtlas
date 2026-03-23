# Change: Bind System AI Behaviors To Assistant Targets

## Why
Weekly and monthly reports still use a legacy "pick assistant default model and assemble a direct prompt" path. That bypasses the reusable workflow/agent target system, prevents published-version control, and makes system AI behaviors inconsistent with the rest of the assistant orchestration model.

## What Changes
- Add a dedicated "system AI behavior" binding layer so built-in behaviors can bind to reusable workflow or agent targets.
- Introduce canonical system default workflows for weekly and monthly report generation, backed by separate JSON presets and versioned/persisted like other targets.
- Add a unified `SystemAiBehaviorRunner` that resolves bindings, executes only published target snapshots, validates fixed JSON output contracts, and falls back to canonical system defaults when a bound target is unavailable.
- Replace weekly/monthly report direct-model execution with the new system behavior runner while keeping existing report APIs unchanged.
- Add a dedicated settings page for configuring system AI behavior bindings, separate from Assistant Targets and AI Providers.
- Refine workflow/agent delete semantics so system-behavior references are handled separately from skill references, including explicit confirm-and-rebind behavior for user targets.

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant_config/models.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant_config/service.py`
  - `backend/app/assistant_config/router.py`
  - `backend/app/assistant_config/system_behavior_*.py`
  - `backend/app/report/service.py`
  - `backend/alembic/versions/*`
  - `frontend/src/features/assistant-config/**`
  - `frontend/src/features/settings/**`
  - `frontend/src/app/App.tsx`
  - `frontend/src/locales/*/common.json`

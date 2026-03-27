# Change: Add First-Run System Initialization Wizard

## Why
MindAtlas currently expects users to discover and configure language, AI providers, and entry types from scattered settings pages. For first-time use this creates too much setup friction and leaves the system in a partially configured state.

## What Changes
- Add a first-run initialization state and gated `/initialize` wizard that blocks normal app navigation until setup is complete.
- Add backend APIs for initialization status, locale-aware default entry type presets, and atomic initialization submission.
- Materialize system locale, default AI bindings, entry types, relation types, and system-owned defaults in one transaction when initialization completes.

## Impact
- Affected specs: `system-settings`
- Affected backend code:
  - `backend/app/system_settings/**`
  - `backend/app/assistant_config/service.py`
- Affected frontend code:
  - `frontend/src/app/**`
  - `frontend/src/features/initialization/**`
  - `frontend/src/locales/{zh,en}/common.json`

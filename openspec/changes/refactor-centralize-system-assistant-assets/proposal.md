# Change: Refactor Centralize System Assistant Assets

## Why
System workflow and agent assets are still split across `assistant/skill_catalog/system_defaults`, `assistant_config/system_behavior_defaults`, and multiple registries that repeat descriptions, canonical names, and file paths. That drift already shows up as a product mismatch: the filesystem exposes four system workflow presets while runtime surfaces six system workflow targets.

This change is compatible with `refactor-standalone-system-target-assets-and-remove-openclaw-special-casing`: that earlier change normalized standalone system targets, and this change finishes the job by centralizing the remaining system skill and system behavior assets under one assistant-owned source of truth.

## What Changes
- Move all shipped system workflow and agent JSON assets into `backend/app/assistant/workflow/system_assets/`.
- Add a centralized registry and loader that own localized metadata, canonical names, usage tags, and locale-aware asset loading.
- Update `assistant.skill_catalog`, `assistant_config`, and `openclaw_integration` to reference central assets only by `asset_key` or canonical target name.
- Remove legacy manifest-based and preset-path-based loaders so non-central modules no longer own system asset files or duplicated asset metadata.
- Preserve current skill names, canonical names, behavior keys, OpenClaw capability keys, API shapes, and database schema.

## Impact
- Affected specs:
  - `assistant-orchestration`
  - `external-agent-integration`
- Affected code:
  - `backend/app/assistant/workflow/system_assets/*`
  - `backend/app/assistant/skill_catalog/*`
  - `backend/app/assistant_config/*`
  - `backend/app/openclaw_integration/*`
  - `backend/tests/*`

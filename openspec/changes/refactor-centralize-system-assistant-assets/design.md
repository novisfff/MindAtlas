## Context
System assistant assets currently have three independent storage patterns:
- skill defaults: manifest plus preset files in `assistant/skill_catalog/system_defaults`
- system behavior defaults: workflow presets in `assistant_config/system_behavior_defaults`
- standalone system targets: registry metadata in `assistant_config`

That forces downstream modules to know where each class of asset lives and to duplicate user-facing metadata. The result is asset drift, uneven localization handling, and extra migration risk whenever a new system workflow is added.

## Goals / Non-Goals
- Goals:
  - Make `assistant/workflow/system_assets` the only place that owns shipped system workflow and agent assets.
  - Make consumers load assets through a shared registry/loader using `asset_key` and canonical name.
  - Preserve all external identifiers and existing runtime behavior.
- Non-Goals:
  - Change database schema or public API contracts.
  - Change the current system workflow/agent product surface.
  - Introduce a new asset authoring format beyond the current locale-aware JSON files.

## Decisions
- Decision: Centralize both workflow and agent assets under `assistant/workflow/system_assets`.
  - Why: these are all AI execution assets and should share one authoritative loader and metadata registry even if different business modules consume them.
- Decision: Keep business-level behavior definitions in `assistant_config`, but replace preset-file references with `default_target_asset_key`.
  - Why: `assistant_config` still owns behavior contracts and binding policies, but not asset file truth.
- Decision: Keep thin adapters such as `standalone_system_target_registry` where a business module benefits from a focused view.
  - Why: callers can still query “standalone system targets” without duplicating metadata or preset paths.
- Decision: Let OpenClaw keep capability metadata while referencing centralized workflow assets by `asset_key`.
  - Why: OpenClaw still owns external capability semantics, but assistant-config owns the underlying system workflow asset lifecycle.

## Risks / Trade-offs
- Cache invalidation now spans the central registry and loader.
  - Mitigation: test bootstrap clears both caches explicitly.
- Locale handling becomes stricter for central asset queries.
  - Mitigation: central APIs fail fast on unsupported locales, while existing callers only pass normalized system locales.

## Migration Plan
1. Move all shipped JSON presets into `assistant/workflow/system_assets/workflows|agents`.
2. Switch consumers to central registry/loader APIs.
3. Delete legacy manifest/preset loaders and old truth files.
4. Backfill contract tests around central asset enumeration, locale loading, and downstream behavior sync.

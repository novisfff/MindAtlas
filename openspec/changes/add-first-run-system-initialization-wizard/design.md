## Context
The app already has locale-aware defaults and a reusable settings surface, but there is no single onboarding flow that ensures the system is usable on first launch. Fresh installs can land in dashboard or assistant views without an AI model, without curated entry types, and without a persisted initialization state.

## Goals / Non-Goals
- Goals:
  - Detect whether the app still needs first-run setup.
  - Gate the app behind a dedicated initialization wizard for fresh installs.
  - Make initialization atomic so locale, AI config, and default content structure are applied together.
- Non-Goals:
  - Add a server-side draft for partial initialization.
  - Introduce per-user onboarding or authentication-aware setup.
  - Replace the existing settings pages for post-initialization editing.

## Decisions
- Decision: persist initialization completion in `app_setting` under `system_initialization_state`
  - Why: initialization is an app-wide concern and fits the existing global settings model.
- Decision: treat legacy systems with clear usage traces as already initialized
  - Why: existing users should not be forced through a wizard retroactively.
- Decision: use locale-aware JSON presets for initialization entry/relation defaults
  - Why: the wizard, legacy detection, and initialization submit path should share one source of truth.
- Decision: initialization submission reuses existing assistant/system-default reset flows but runs without intermediate commits
  - Why: system-owned defaults must match the selected language while preserving transaction atomicity.

## Risks / Trade-offs
- Initialization now touches AI config, content schema, and system-owned defaults in one flow.
  - Mitigation: keep the flow scoped to fresh installs and add rollback coverage for submission failures.
- Legacy detection based on heuristics can misclassify near-empty systems.
  - Mitigation: only auto-complete when usage or customization signals are clear, otherwise keep the system eligible for wizard setup.

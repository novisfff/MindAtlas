## Context
MindAtlas now supports system AI behaviors and system-owned skill targets, but locale handling was still fragmented. Interactive chat might follow the current UI language, while scheduler jobs, report generation, default target resets, and system preset materialization could use stale or implicit language assumptions. This change standardizes locale resolution and makes system-owned AI execution predictable across both interactive and background paths.

## Goals / Non-Goals
- Goals:
  - Persist a single global system language for the app.
  - Make runtime execution locale-aware across all system AI entry points.
  - Keep system-owned presets available in both Chinese and English while preserving graph structure.
  - Ensure report records explicitly track generation language.
- Non-Goals:
  - Introduce per-user or per-conversation locale routing.
  - Rewrite user-created workflows or agents when the global language changes.
  - Add arbitrary BCP 47 locale support beyond `zh` and `en`.

## Decisions
- Decision: Use `app_setting` with key `system_locale`
  - Why: a general settings table is reusable for future app-wide settings and avoids hard-coding locale into unrelated models.
- Decision: Resolve locale in the order `X-MindAtlas-Locale` -> persisted `system_locale` -> `APP_DEFAULT_LOCALE` -> `zh`
  - Why: interactive requests should react immediately to the current UI language, while background jobs need a stable persisted fallback.
- Decision: Expose `sys.locale` and `sys.language` through workflow execution context
  - Why: system-owned presets and user-authored workflows both need a consistent, explicit runtime contract for locale-aware prompting.
- Decision: Keep system preset graph structure identical across locales and translate only human-facing text fields
  - Why: binding validation, node references, and test expectations must remain stable regardless of language.
- Decision: Locale changes affect execution immediately, but destructive reset/sync/example creation is still explicit
  - Why: users should not lose edits on existing system targets just because the app language changed.

## Risks / Trade-offs
- Locale-aware execution wrappers can change model-visible prompts and therefore output distribution.
  - Mitigation: constrain changes to wrapper copy and add regression tests around runtime context propagation.
- Persisted global locale is app-wide and last-writer-wins.
  - Mitigation: this matches the current no-auth architecture and keeps scheduler behavior deterministic.
- Localized preset duplication can drift structurally over time.
  - Mitigation: add tests that compare zh/en preset graph structure.

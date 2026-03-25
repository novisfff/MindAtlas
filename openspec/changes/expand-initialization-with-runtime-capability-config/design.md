## Context
The project already has an initialization wizard, locale-aware defaults, and a growing runtime surface for object storage, LightRAG, document parsing, and scheduled jobs. Those capabilities are still largely configured through env variables, which is fine for deployment concerns but too opaque for end users configuring a running app.

## Goals / Non-Goals
- Goals:
  - Expose user-understandable runtime capability configuration inside initialization and settings.
  - Preserve env fallback behavior so existing deployments continue to work unchanged.
  - Keep initialization fast by separating mandatory core setup from optional capability modules.
  - Make missing capability states explicit in both backend behavior and frontend UX.
- Non-Goals:
  - Replace deployment-level env configuration such as database, CORS, host/port, upload directory, or Fernet bootstrap secrets.
  - Introduce a server-side initialization draft.
  - Guarantee hot reload for every worker or scheduler process after runtime config updates.

## Decisions
- Decision: persist runtime capability groups in `app_setting`
  - Why: grouped app-level settings already exist, and the new modules fit the same scope as locale and initialization state.
- Decision: resolve runtime config as `app_setting override > env fallback > built-in default`
  - Why: new UI-configured values must win, while existing env-based deployments remain compatible without migration.
- Decision: reuse the same module forms in initialization and settings
  - Why: capability setup should feel consistent before and after initialization, and shared forms reduce drift.
- Decision: keep capability modules optional during initialization
  - Why: a usable system should still be reachable after the core language/model/entry-type path, while advanced modules can be configured later.
- Decision: integrate LightRAG infra config via runtime settings but keep model execution on the existing AI registry bindings
  - Why: infrastructure values and model bindings are different concerns, and model bindings already have a dedicated registry lifecycle.

## Risks / Trade-offs
- Runtime config now influences multiple subsystems in-process.
  - Mitigation: centralize cache clearing and resolver access, and make restart requirements explicit in UI responses.
- Initialization UX can become overwhelming if capability pages are too dense.
  - Mitigation: keep the first three steps mandatory and lightweight, then move optional modules into a separate capability center with clear skip affordances.
- LightRAG and attachment flows previously assumed env-only configuration.
  - Mitigation: update runtime consumers and missing-state UX together so newly skipped capabilities fail clearly instead of surfacing generic errors.

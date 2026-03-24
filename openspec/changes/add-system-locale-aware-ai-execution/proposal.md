# Change: Make System AI Execution Locale-Aware

## Why
System AI execution currently needs to respond in the application's active language, but locale handling has been inconsistent across assistant chat, workflow/agent test runs, system behaviors, and system-owned default targets. The application also lacked a persisted global system language setting, which made scheduler-driven AI execution and default reset/sync behavior non-deterministic.

## What Changes
- Add a persisted global `system_locale` setting with a unified resolver that prioritizes request header, persisted setting, environment default, and final fallback.
- Propagate locale through assistant chat, workflow test runs, agent test runs, and system AI behavior execution contexts, exposing `sys.locale` and `sys.language`.
- Localize system skill defaults and system AI behavior default workflow presets so reset/sync/example creation materializes the current language version.
- Record report content language with `content_locale` and regenerate weekly/monthly reports when the stored language no longer matches the current system language.

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/system_settings/**`
  - `backend/app/common/request_context.py`
  - `backend/app/main.py`
  - `backend/app/assistant/**`
  - `backend/app/assistant_config/**`
  - `backend/app/report/**`
  - `backend/app/scheduler.py`
  - `backend/alembic/versions/a2b3c4d5e6f7_add_system_locale_and_report_content_locale.py`
- Affected frontend code:
  - `frontend/src/app/providers.tsx`
  - `frontend/src/components/layout/LanguageSwitcher.tsx`
  - `frontend/src/lib/api/**`
  - `frontend/src/features/settings/api/system-settings.ts`
  - `frontend/src/features/assistant/hooks/useChat.ts`
  - `frontend/src/features/assistant-config/api/{workflow,agents}.ts`
  - `frontend/src/features/attachments/api/attachments.ts`
  - `frontend/src/features/dashboard/api/reports.ts`

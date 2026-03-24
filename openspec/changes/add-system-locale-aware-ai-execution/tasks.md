## 1. Backend
- [x] 1.1 Add persisted system locale settings, request-locale context handling, and unified locale resolution.
- [x] 1.2 Propagate locale through assistant chat, workflow test runs, agent test runs, and system AI behavior execution.
- [x] 1.3 Localize system skill defaults, system behavior defaults, and locale-aware reset/sync/example materialization.
- [x] 1.4 Add report `content_locale` storage plus regenerate-on-locale-mismatch behavior for manual and scheduler generation.

## 2. Frontend
- [x] 2.1 Synchronize `LanguageSwitcher` with persisted system locale on boot and on toggle.
- [x] 2.2 Send `X-MindAtlas-Locale` on interactive API requests, including raw fetch paths.
- [x] 2.3 Expose locale-aware system variables and report response fields needed by the UI.

## 3. Validation
- [x] 3.1 Add or update backend tests for locale precedence, localized defaults, execution-context propagation, and report locale behavior.
- [x] 3.2 Run backend regression tests plus frontend build verification.
- [x] 3.3 Run `openspec validate add-system-locale-aware-ai-execution --strict --no-interactive`.

## 1. Implementation
- [x] 1.1 Add the OpenSpec delta for automation scheduler toggle in initialization and settings
- [x] 1.2 Refactor the backend scheduler into an idempotent sync manager and apply it after automation config updates
- [x] 1.3 Expose automation scheduler state in the initialization capabilities step and review summary, and submit it explicitly
- [x] 1.4 Add Automation settings navigation, route, and dedicated detail page while keeping System Setup overview-only
- [x] 1.5 Add i18n coverage and targeted backend/frontend validation for automation runtime behavior

## 2. Validation
- [x] 2.1 Run `openspec validate add-automation-scheduler-toggle-in-initialization-and-settings --strict --no-interactive`
- [x] 2.2 Run targeted backend tests for scheduler sync and automation runtime config updates
- [x] 2.3 Run the frontend build to confirm initialization and settings automation surfaces compile

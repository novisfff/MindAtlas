## 1. Implementation
- [x] 1.1 Add the standalone `periodic_review_core` system workflow asset, convert the chat `periodic_review` asset into a wrapper, and resolve system-asset workflow-call targets during sync.
- [x] 1.2 Replace the OpenClaw weekly/monthly system capabilities with a single workflow-backed `generate_periodic_review` capability and remove the old wrappers, aliases, and schemas.
- [x] 1.3 Update OpenClaw shipped skills, routing hints, and warning paths to recommend `mindatlas_generate_periodic_review`.
- [x] 1.4 Refresh backend and plugin tests for the unified periodic review surface.

## 2. Validation
- [x] 2.1 Run targeted backend and plugin tests for periodic review, system asset sync, and OpenClaw integration.
- [x] 2.2 Validate the OpenSpec change with `openspec validate remove-openclaw-weekly-monthly-wrappers-and-add-periodic-review --strict --no-interactive`.

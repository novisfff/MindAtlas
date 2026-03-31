## 1. Backend
- [x] 1.1 Make system workflows and system agent profiles immutable in the assistant-config service and reject all write/version-management operations with copy-first errors.
- [x] 1.2 Add workflow and agent copy APIs that create editable user-owned duplicates from canonical system baselines or current custom drafts.
- [x] 1.3 Reconcile shipped system workflow/agent targets back to canonical defaults during sync/migration while collapsing each target to one published baseline version.
- [x] 1.4 Keep system skills, system AI behaviors, and OpenClaw system items rebindable while preserving user-owned copies and binding references.
- [x] 1.5 Update targeted backend and OpenClaw integration tests for immutable-system-target and copy-first behavior.

## 2. Frontend
- [x] 2.1 Make system workflow and system agent editor pages read-only, keep test-run available, and add `Copy As Duplicate` entry points on editors and list cards.
- [x] 2.2 Disable version-management mutations for system targets and surface copy-first guidance in binding-layer UIs for skills, system AI behaviors, and OpenClaw settings.
- [x] 2.3 Refresh localized copy for immutable system target and duplicate flows.

## 3. Spec And Validation
- [x] 3.1 Add this OpenSpec change package and spec deltas for assistant orchestration and external agent integration.
- [x] 3.2 Run targeted backend tests, OpenClaw integration tests, Python compile checks, frontend TypeScript checks, and `openspec validate update-system-workflow-agent-immutability-and-copy-first-customization --strict --no-interactive`.

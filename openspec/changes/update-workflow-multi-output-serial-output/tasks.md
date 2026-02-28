## 1. Implementation
- [x] 1.1 Update workflow topology rule to require at least one output node (remove single-output rejection).
- [x] 1.2 Update compile validator compatibility filtering for new output-missing message.
- [x] 1.3 Remove frontend single-output creation guard in FlowCanvas (quick-add + drag-drop).
- [x] 1.4 Update reachability warning calculation to start reverse traversal from all output nodes.
- [x] 1.5 Extend output node `content_delta` emission with output-source metadata.
- [x] 1.6 Extend stream runtime `on_content_delta` metadata pass-through and add source-switch segment separator logic.

## 2. Verification
- [x] 2.1 Run OpenSpec strict validation for this change.
- [ ] 2.2 Run backend tests (or targeted tests if full suite unavailable).
- [x] 2.3 Run frontend build.

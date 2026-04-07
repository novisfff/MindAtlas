## 1. Spec And Surface Cleanup
- [x] 1.1 Define the canonical system tool surface and compatibility expectations in OpenSpec deltas.
- [x] 1.2 Update the assistant tool exports so canonical tools stay visible while `openclaw_*` wrappers become hidden compatibility aliases.

## 2. Backend Runtime Refactor
- [x] 2.1 Add canonical system tools for relation creation, knowledge graph queries, weekly report generation, and monthly report generation.
- [x] 2.2 Rebind OpenClaw shipped system items to canonical tool names and add an explicit OpenClaw adapter mapping layer for contract translation.
- [x] 2.3 Auto-migrate legacy OpenClaw catalog item source bindings to canonical tool names while preserving retired `openclaw_capture_entry`.
- [x] 2.4 Keep workflow and agent validation compatible with hidden system-tool aliases so existing persisted references still resolve.

## 3. Verification
- [x] 3.1 Add or update backend tests for visible tool definitions, alias resolution, OpenClaw migration, and runtime execution.
- [x] 3.2 Run OpenSpec validation, targeted backend tests, and the frontend build.

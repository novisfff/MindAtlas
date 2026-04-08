## 1. Implementation
- [x] 1.1 Update the 4 shipped MindAtlas skills so `mindatlas-overview` becomes the total router and the other 3 skills become narrower sub-strategies.
- [x] 1.2 Strengthen plugin-generated `mindatlas_*` tool descriptions with MindAtlas routing hints while keeping the existing tool names and execution flow unchanged.
- [x] 1.3 Update backend OpenClaw system capability metadata copy for the existing 7 shipped capabilities without changing keys, schemas, or API contracts.
- [x] 1.4 Refresh OpenClaw integration docs and settings copy to explain session-visible MindAtlas tools, new-session requirements, and explicit absent-tool messaging.
- [x] 1.5 Add or update focused tests for routing-oriented tool descriptions and refreshed system capability metadata.

## 2. Validation
- [x] 2.1 Run targeted backend and plugin tests covering the revised capability metadata and tool descriptions.
- [x] 2.2 Run `openspec validate update-mindatlas-auto-routing-and-tool-hints --strict --no-interactive`.

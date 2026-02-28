## 1. Implementation
- [x] 1.1 Add JSON source files for system defaults (`manifest.json`, workflow presets, agent presets).
- [x] 1.2 Add strict JSON loader with schema validation and cached load semantics.
- [x] 1.3 Refactor `definitions.py` into compatibility export layer backed by JSON loader.
- [x] 1.4 Update system baseline rollback behavior (workflow + agent) to canonical JSON presets.

## 2. Validation
- [x] 2.1 Add tests for loader success/fail-fast behavior.
- [x] 2.2 Add test for system agent baseline rollback restoring JSON canonical defaults.
- [x] 2.3 Run targeted backend tests and python compile checks.

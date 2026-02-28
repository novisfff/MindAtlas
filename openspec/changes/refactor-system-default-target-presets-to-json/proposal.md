# Change: Refactor System Default Targets To JSON Presets

## Why
System default workflow/agent presets are currently hardcoded in Python constants, which makes non-code preset maintenance difficult and creates drift risk between baseline restore behavior and source defaults.

## What Changes
- Move system default workflow/agent preset source-of-truth from Python constants to JSON files under `backend/app/assistant/skills/system_defaults/`.
- Add a strict loader that validates manifest/preset JSON and fails fast on missing/invalid definitions.
- Keep existing runtime/public APIs unchanged by exposing the same `SKILLS`/`get_skill_by_name` compatibility surface from `definitions.py`.
- Ensure system baseline rollback (workflow + agent) resolves canonical baseline from JSON presets.

## Impact
- Affected specs: `assistant-orchestration`
- Affected code:
  - `backend/app/assistant/skills/defaults_loader.py`
  - `backend/app/assistant/skills/definitions.py`
  - `backend/app/assistant/skills/system_defaults/**`
  - `backend/app/assistant_config/service.py`
  - `backend/tests/test_system_defaults_loader.py`
  - `backend/tests/test_system_agent_baseline_restore.py`

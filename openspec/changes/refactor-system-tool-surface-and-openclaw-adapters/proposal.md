# Change: Refactor System Tool Surface And OpenClaw Adapters

## Why
The current system tool registry mixes canonical MindAtlas tools with OpenClaw-specific wrapper names, so the normal system-tool UI exposes adapter-only implementation details and invites duplicate bindings. That overlap also leaves OpenClaw execution coupled to legacy `openclaw_*` source names instead of a stable canonical tool surface.

## What Changes
- Define one canonical system tool surface for entries, relations, knowledge graph queries, and report generation.
- Remove `openclaw_*` wrappers from normal system-tool discovery while keeping them as hidden compatibility aliases for existing workflows and agents.
- Rebind shipped OpenClaw system items to canonical tool names and introduce an explicit adapter map in the OpenClaw integration runtime for request/response contract translation.
- Auto-migrate existing OpenClaw catalog items that still point at legacy `openclaw_*` source tool names onto canonical tool bindings, while keeping retired `openclaw_capture_entry` retired.
- Keep existing OpenClaw-facing `mindatlas_*` capability names, schemas, and workflow defaults unchanged.

## Impact
- Affected specs:
  - `assistant-orchestration`
  - `external-agent-integration`
- Affected code:
  - `backend/app/assistant/tools/*`
  - `backend/app/assistant_config/registry.py`
  - `backend/app/assistant_config/service.py`
  - `backend/app/assistant_config/agent_test_service.py`
  - `backend/app/openclaw_integration/registry.py`
  - `backend/app/openclaw_integration/service.py`
  - `backend/tests/test_assistant_kb_tools.py`
  - `backend/tests/test_openclaw_integration.py`
  - `frontend/src/features/assistant/components/*`

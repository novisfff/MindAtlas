# Change: Refactor OpenClaw System Items Into First-Class Exposure Bindings

## Why
The current OpenClaw catalog still carries legacy `system_adapter` and “system preset” concepts that make shipped capabilities feel special-cased even though product direction has moved toward a unified exposure catalog. That extra layer makes the execution path, settings UX, and documentation more complex than necessary.

## What Changes
- Remove `system_adapter` from the OpenClaw catalog model so catalog items bind only to real `tool`, `workflow`, or `agent` sources.
- Replace “system preset” terminology with first-class “system item” defaults that share the same editing and deletion rules as custom items.
- Seed shipped OpenClaw defaults from a single system item registry and bind them to real system tools or workflows while keeping existing `capabilityKey` and `toolName` stable.
- Simplify runtime execution to the three real source paths and keep reset scoped to restoring shipped system item defaults only.
- Update admin APIs, frontend wording, plugin expectations, docs, and tests to match the new model.

## Impact
- Affected specs: `external-agent-integration`
- Affected code:
  - `backend/app/openclaw_integration/*`
  - `backend/app/assistant/tools/openclaw_tools.py`
  - `backend/app/assistant_config/registry.py`
  - `backend/alembic/versions/*`
  - `backend/tests/test_openclaw_integration.py`
  - `frontend/src/features/settings/*`
  - `frontend/src/locales/*/common.json`
  - `docs/openclaw/*`
  - `integrations/openclaw-mindatlas/*`

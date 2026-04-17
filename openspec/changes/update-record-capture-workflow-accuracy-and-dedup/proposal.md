# Change: Optimize Record Capture Workflow Accuracy And Dedup Convergence

## Why
The shipped `smart_capture` and `context_capture` workflows still allow avoidable drift in field materialization, duplicate handling, and explicit write validation. We need both flows to stay role-stable while improving accuracy, duplicate awareness, and deterministic write behavior.

## What Changes
- Disable automatic session memory injection for both capture workflows so record persistence is grounded only in current input plus explicit tool results.
- Tighten `create_entry` / `update_entry` validation so non-empty invalid `type_code` and conflicting or malformed explicit time fields fail fast instead of silently falling back.
- Extend `human_in_loop` request text semantics so `title`, `instruction`, `approveLabel`, and `rejectLabel` support template resolution against upstream node outputs.
- Add a shared LightRAG-backed `search_similar_entries` system tool that returns entry-level semantic-similarity candidates without changing the OpenClaw capability surface.
- Replace capture workflow keyword-based duplicate retrieval with shared semantic candidate recall, while keeping duplicate judgment in LLM / HITL layers rather than in the retrieval tool itself.
- Strengthen `context_capture` with tag reuse and a stricter merge rule that only merges when its top recalled candidate is judged by the LLM to be the same durable record.
- Keep the public OpenClaw `submit_context_capture` capability schema, tool name, and output shape unchanged.

## Impact
- Affected specs:
  - `assistant-orchestration`
  - `external-agent-integration`
- Affected code:
  - `backend/app/assistant/tools/entry_tools.py`
  - `backend/app/assistant/tools/__init__.py`
  - `backend/app/assistant_config/registry.py`
  - `backend/app/assistant/workflow/engine/node_builders/human_in_loop_node.py`
  - `backend/app/assistant/workflow/engine/snapshot_input_resolvers.py`
  - `backend/app/assistant/workflow/system_assets/workflows/smart_capture*.json`
  - `backend/app/assistant/workflow/system_assets/workflows/context_capture*.json`
  - `backend/tests/test_system_skill_workflow_refs.py`
  - `backend/tests/test_assistant_config_service_more.py`
  - `backend/tests/test_system_workflow_layout_presets.py`
  - `backend/tests/test_openclaw_integration.py`
  - new runtime and entry-tool regression tests

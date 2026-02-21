# Change: Separate Skill From Workflow/Agent Targets

## Why
`assistant_skill` currently mixes routing semantics with executable body configuration. This makes reuse difficult and prevents one workflow/agent executable from being bound by multiple skills.

## What Changes
- Introduce independent executable entities:
  - `AssistantWorkflow` (+ node/edge tables)
  - `AssistantAgentProfile`
- Refactor `AssistantSkill` into routing metadata + single target binding (`workflow_id` xor `agent_profile_id`).
- Add canonical APIs:
  - `/api/assistant-config/workflows` CRUD + validate + test-run
  - `/api/assistant-config/agents` CRUD
- Keep one-version compatibility for legacy skill workflow routes:
  - `/skills/{id}/workflow`
  - `/skills/{id}/validate-workflow`
  - `/skills/{id}/workflow/test-run`
- Update frontend settings UX:
  - Unified "Assistant Targets" management page (mixed Workflow/Agent list)
  - Dedicated Agent editor page with system prompt + KB enable + tools selection
  - Agent editor upgraded to dual-pane workspace (left config + right draft test-run)
  - Skill editor switches to single target selector with automatic type mapping
  - Assistant Targets rows expand for details (Agent runtime details / Workflow mini graph)
  - Assistant Targets no longer expose enable/disable switch (always-on semantics)
  - Workflow editor routed by `workflowId`
- Add non-persistent agent draft test-run SSE endpoint:
  - `POST /api/assistant-config/agents/{id}/test-run`
  - supports caller-controlled stream output with merged `content_delta`

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant_config/models.py`
  - `backend/alembic/versions/e5f6a7b8c9d0_separate_skill_workflow_agent.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant_config/service.py`
  - `backend/app/assistant_config/router.py`
  - `backend/app/assistant_config/registry.py`
  - `backend/app/assistant/skills/converters.py`
  - `backend/app/assistant_config/workflow_test_service.py`
- Affected frontend code:
  - `frontend/src/features/assistant-config/api/skills.ts`
  - `frontend/src/features/assistant-config/api/workflows.ts`
  - `frontend/src/features/assistant-config/api/agents.ts`
  - `frontend/src/features/assistant-config/components/skillTargetOptions.ts`
  - `frontend/src/features/assistant-config/components/SkillManager.tsx`
  - `frontend/src/features/assistant-config/components/SkillRowEditor.tsx`
  - `frontend/src/features/assistant-config/components/useSkillForm.ts`
  - `frontend/src/features/assistant-config/pages/WorkflowEditorPage.tsx`
  - `frontend/src/features/assistant-config/pages/AssistantTargetsSettings.tsx`
  - `frontend/src/features/assistant-config/pages/AgentEditorPage.tsx`
  - `frontend/src/features/settings/SettingsPage.tsx`
  - `frontend/src/app/App.tsx`

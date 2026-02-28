# Change: Add Assistant Target Versioning

## Why
Workflow and Agent editing currently overwrites runtime configuration directly. There is no draft/publish separation or rollback history, which blocks safe iteration and controlled rollout.

## What Changes
- Add version history for both `AssistantWorkflow` and `AssistantAgentProfile`.
- Introduce target head pointers:
  - `draft_version_id`
  - `published_version_id`
- Update save semantics:
  - `PUT /workflows/{id}` and `PUT /agents/{id}` create `save` versions and only move draft head.
- Add publish APIs:
  - `POST /workflows/{id}/publish`
  - `POST /agents/{id}/publish`
- Add version history and rollback APIs:
  - `GET /workflows/{id}/versions`
  - `POST /workflows/{id}/versions/{version_id}/rollback`
  - `GET /agents/{id}/versions`
  - `POST /agents/{id}/versions/{version_id}/rollback`
- Enforce runtime behavior:
  - Skill execution reads published data only.
  - Rollback restores draft only and does not auto-publish.
- Add frontend versioning UX for workflow and agent editors:
  - `保存` / `保存并发布`
  - publish version name dialog (default current timestamp)
  - version history panel and rollback action.

## Impact
- Affected specs: `assistant-orchestration`
- Affected backend code:
  - `backend/app/assistant_config/models.py`
  - `backend/alembic/versions/f6a7b8c9d0e1_add_target_versioning.py`
  - `backend/app/assistant_config/schemas.py`
  - `backend/app/assistant_config/service.py`
  - `backend/app/assistant_config/router.py`
- Affected frontend code:
  - `frontend/src/features/assistant-config/api/workflows.ts`
  - `frontend/src/features/assistant-config/api/agents.ts`
  - `frontend/src/features/assistant-config/pages/WorkflowEditorPage.tsx`
  - `frontend/src/features/assistant-config/pages/AgentEditorPage.tsx`
  - `frontend/src/features/assistant-config/components/versioning/TargetVersionPanel.tsx`
  - `frontend/src/features/assistant-config/components/versioning/PublishVersionDialog.tsx`
  - `frontend/src/locales/zh/common.json`
  - `frontend/src/locales/en/common.json`

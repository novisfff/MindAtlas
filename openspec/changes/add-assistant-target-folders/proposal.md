# Change: Add assistant target folders

## Why
The Workflows & Agents page currently shows a flat list. As reusable workflows and agents grow, users need a durable way to organize both target types together without changing execution semantics.

## What Changes
- Add multi-level folders for assistant executable targets.
- Allow workflows and agents, including system targets, to be assigned to folders.
- Add folder CRUD and move APIs for targets and folders.
- Update the Workflows & Agents UI to browse mixed folders, workflows, and agents with search, filters, drag-and-drop movement, and menu fallback.

## Impact
- Affected specs: assistant-orchestration
- Affected code: assistant_config models/schemas/service/router, Alembic migrations, assistant-config frontend API/query/page/card/i18n

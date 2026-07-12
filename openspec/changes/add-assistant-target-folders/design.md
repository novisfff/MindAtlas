## Context
Workflows and agents are separate persisted entities but are presented together as assistant executable targets. Folder membership should be independent from execution, binding, publishing, and system read-only behavior.

## Goals / Non-Goals
- Goals: multi-level folders, shared organization for workflows and agents, system target organization, safe moves, mixed directory browsing.
- Non-Goals: manual ordering, permissions, folder-specific execution behavior, deleting targets when deleting folders.

## Decisions
- Add `AssistantTargetFolder` as a shared folder table and add nullable `folder_id` to both workflow and agent tables.
- Keep folder contents as simple foreign keys from targets/folders rather than a polymorphic membership table because each target can belong to at most one folder.
- Delete folders by lifting direct children and targets to the deleted folder parent.
- Resolve lifted child-folder name conflicts with deterministic numeric suffixes so deletion preserves both contents and sibling-name uniqueness.
- Sort mixed lists by recently active time, with folders using the max activity timestamp across descendants.
- Use existing `@dnd-kit/core` for drag/drop and provide menu movement for precision and mobile fallback.

## Risks / Trade-offs
- Multi-level folders require cycle checks; service methods validate parent moves before persistence.
- PostgreSQL serializes folder mutations with a transaction advisory lock so concurrent moves cannot bypass cycle and sibling-name checks; list serialization also detects persisted cycles defensively.
- Recursive activity/stat computation can grow with target count; initial implementation computes from the fetched flat list, which is adequate for settings-scale data.
- System assets are movable for organization only; existing system read-only rules still prevent editing their contents.

## Migration Plan
- Create the folder table.
- Add nullable `folder_id` columns to workflow and agent tables.
- Create a regular `系统内置` folder if system targets exist and assign existing system workflows/agents to it.
- Keep all custom targets at root by default.

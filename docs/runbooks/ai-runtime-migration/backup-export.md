# AI Runtime Migration Backup / Export Procedure

**Status:** template (Task 0)

## Purpose

Produce a sanitized, digestable export suitable for:

- Deploy A practice restore drills
- Deploy B2 preflight evidence
- Migration row-count sign-off

## Export contents (minimum)

| Component | Include | Notes |
|---|---|---|
| `assistant_skill` | yes | legacy sources |
| `assistant_skill_package` + versions/aliases | yes | universal targets |
| `assistant_main_agent_profile` + versions | yes | Profile targets |
| `assistant_conversation_skill_l2_memory` | yes | package/namespace shape |
| `assistant_human_approval` | yes | terminal + pending counts |
| `assistant_chat_run` / capability calls | yes | runtime_kind + settlement |
| migration audit tables (Task 1+) | yes when present | batches/items/events |
| object store Artifacts | as required | private prefixes only |

Never place raw credentials, unrestricted prompts, or production inventory identifiers into git.

## Manifest shape

Use the sanitized fixture as the schema example:

`backend/tests/fixtures/ai_runtime_migration/backup_export_manifest.json`

Fields:

- `schema_version`, `export_kind`, `schema_head`, `environment`
- `tables[]`: `name`, `row_count`, `content_digest`
- `object_store`: `object_count`, `content_digest` (bucket/prefix only in operator storage)

Digest helper:

```python
from app.assistant.migration.verification import digest_backup_export_manifest
digest = digest_backup_export_manifest(manifest)
```

## Restore verification steps

1. Restore into isolation.
2. Compare schema head.
3. Recompute table digests / counts.
4. Run legacy + Main Agent **safe** smoke (no real external writes).
5. Record pass/fail in operator audit storage.

## Task 0 skip

Live backup restore against real Postgres/MinIO is skipped when unavailable. Unit tests cover manifest digest stability only.

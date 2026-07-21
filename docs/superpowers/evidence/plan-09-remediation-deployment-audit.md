# Plan 09 remediation — deployment audit (Task 1)

**Purpose:** Freeze whether any shared development / staging / production database has applied Plan 09 revisions before rewriting the pre-merge migration graph.

**UTC audit window:** 2026-07-21T02:30:35Z (initial gather) … 2026-07-21T02:32:13Z (re-verify)

## Configured database environments in this repository

| Environment name | Source | DATABASE_URL (redacted) | Notes |
|---|---|---|---|
| Shared/dev (`backend/.env`) | `backend/.env` `DATABASE_URL` | `postgresql://postgres:***@192.168.30.120:5432/mindatlas` | Only live shared DB configured for this workspace |
| Local example | `backend/.env.example` | `postgresql://postgres:***@localhost:5432/mindatlas` | Template only; not a live shared DB |
| Local test override | `backend/.env.local` `TEST_DATABASE_URL` | `postgresql://postgres:***@localhost:5432/mindatlas` | Local disposable/test; not shared staging/prod |
| Compose stack | `deploy/docker-compose.yml` | container-internal `postgres:5432/mindatlas` | Compose template; no separate staging/prod credentials in-repo |
| CI / disposable | env `MINDATLAS_TEST_POSTGRES_URL` | operator-supplied | Disposable; may be wiped |

**Finding:** No separate staging or production `DATABASE_URL` is configured in this repository beyond the shared `.env` and CI/disposable test DBs.

## Shared/dev database probe

**Env name:** Shared/dev (`backend/.env` `DATABASE_URL`)  
**Host/DB:** `192.168.30.120:5432/mindatlas`  
**UTC timestamp:** `2026-07-21T02:32:13Z`

### Command: `alembic current -v`

```text
$ cd backend && .venv/bin/alembic current -v
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
Current revision(s) for postgresql+psycopg2://postgres:***@192.168.30.120:5432/mindatlas:
Rev: 984c07876856
Parent: 7a3dac0ac2a8
Path: .../backend/alembic/versions/984c07876856_add_capability_call_ledger.py

    add capability call ledger

    Revision ID: 984c07876856
    Revises: 7a3dac0ac2a8
    Create Date: 2026-07-17 16:47:00.000000

    Plan 08 Task 1: CapabilityCall ledger tables, Run capability_ledger_mode,
    call-owned Interrupt origin XOR profile, Entry source_capability_call_id,
    immutable-identity / append-only triggers, and guarded downgrade.
```

### Command: direct SQL

```text
SELECT version_num FROM alembic_version;
→ [('984c07876856',)]
```

### Plan 09 column presence on shared DB

```text
assistant_skill_package columns aggregate_revision / archived_at → []
```

Plan 09 lifecycle columns are **not** present on the shared DB.

## Plan 09 revision application matrix

| Revision | Meaning | Applied on shared/dev? |
|---|---|---|
| `403414a62e55` | 09A skill package admin lifecycle | **No** |
| `027869a00a47` | 09B skill evaluation workbench | **No** |
| `24f1e06fdd9e` | residual alias soft-disable (post-09B) | **No** |

Shared/dev is still at Plan 08 capability-call ledger tip `984c07876856` (ancestor of Plan 08 evidence tip `d7e8f9a0b1c3`, which is the parent of 09A).

## Disposable remediation DB (not a shared deployment)

| Env | Value |
|---|---|
| `MINDATLAS_TEST_POSTGRES_URL` | `postgresql://postgres:***@192.168.30.120:5432/mindatlas_test_plan09_remediation` |
| State at audit | empty public schema (no `alembic_version` table) |
| Role | destructive PostgreSQL verification only |

## Decision

**Clean pre-merge rewrite path is ALLOWED.**

- No shared database reports `403414a62e55`, `027869a00a47`, or `24f1e06fdd9e`.
- Therefore delete residual revision `24f1e06fdd9e` and fold its column-aware alias soft-disable trigger into 09A (`403414a62e55`).
- Sole pre-merge Plan 09 head becomes `027869a00a47`.
- Do **not** use a forward repair revision for this residual.

If any shared environment later reports one of those three revisions before merge, stop and switch to a forward-only repair; do not delete or rewrite an applied revision.

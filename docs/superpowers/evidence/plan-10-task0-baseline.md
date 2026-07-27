# Plan 10 Task 0 Baseline (Runtime Migration Inventory Freeze)

**Recorded at (UTC):** 2026-07-22T17:07:49Z  
**Branch:** `worktree-plan10-runtime-migration`  
**Worktree:** `/Users/zyf/IdeaProjects/MindAtlas/.claude/worktrees/plan10-runtime-migration`  
**HEAD at freeze:** `c93a3a62b374344c40bd23f41e9ab877c10a6aa3` (`c93a3a6`)  
**HEAD subject:** `feat(ai): Plan 09 universal skill admin and testing (M4) (#56)`  
**Working tree product code at freeze start:** clean (Task 0 adds inventory tooling only).

---

## 1. Environment

| Item | Value |
|---|---|
| Python (local venv) | **3.12.7** (production target remains **3.11**; local drift is not Plan 10 compatibility evidence) |
| Installed `langgraph` (local venv) | **1.0.5** (pin vs installed drift same class as prior plans) |
| Installed `langchain` / `langchain-core` | 1.2.3 / 1.2.7 |
| pydantic | 2.12.5 |
| sqlalchemy | 2.0.45 |
| alembic | 1.17.2 |
| fastapi | 0.128.0 |
| httpx | 0.28.1 |
| cryptography | 46.0.3 |
| Sole Alembic head | **`027869a00a47`** (`add skill evaluation workbench`; parent `403414a62e55`) |
| Task 1 migration parent | **`027869a00a47`** — generate a **fresh unique** revision with `alembic revision -m "add ai runtime migration audit"`; do **not** preselect a revision ID |
| `APP_BUILD_REVISION` default | **`development`** |
| `MINDATLAS_TEST_POSTGRES_URL` | treat as **unset** for Task 0 (live restore skipped) |
| `MINDATLAS_TEST_MINIO` | treat as **unset** for Task 0 (live restore skipped) |
| Live Docker compose golden | **not run** |
| Full-suite `pytest` / frontend test/build | **skipped** (focused inventory tests only; honest skip) |

### Worker / codec / flags (frozen at Plan 09 tip)

| Item | Value |
|---|---|
| `ASSISTANT_MAIN_AGENT_MODE` default | **`off`** |
| `ASSISTANT_DURABLE_INTERRUPTS_ENABLED` default | **`false`** |
| `ASSISTANT_CAPABILITY_LEDGER_MODE` default | **`legacy_read_only`** |
| `ASSISTANT_MAIN_AGENT_WRITE_MODE` default | **`off`** |
| `ASSISTANT_SKILL_PUBLISH_GATE_MODE` default | **`observe`** |
| `ASSISTANT_SKILL_ADMIN_TRUSTED_MOUNT` | unset → admin/eval routers **unmounted** (even in development) |
| Staging/production skill admin mount | **never** mount even if trusted flag set |
| Operator headers (trusted mount only) | `X-MindAtlas-Operator-Id`, `X-MindAtlas-Operator-Role` — **not** release auth |

### Known system skills / assets (discovery baseline)

| Kind | Names |
|---|---|
| System skills | `general_chat` (DEFAULT), `quick_stats`, `smart_capture`, `periodic_review` |
| Related assets | `smart_capture_relation_followup`, `smart_capture_golden_create`, `periodic_review_core`, `context_capture`, `weekly_report`, `monthly_report` |
| L2 dual identity | `AssistantConversationSkillL2Memory` nullable `skill_package_id` + `memory_namespace` with dual unique indexes (Plan 06) |

### Plan 02B status

| Item | Value |
|---|---|
| Status | **complete** (shared capability runtime; non-blocking for Task 0 inventory) |
| Evidence | `docs/superpowers/evidence/plan-02b-observation.md`, `plan-02b-final.md` |
| Deploy B1 implication | shared-only OpenClaw exit mandatory before removing overlapping legacy owners |

---

## 2. Inherited ship-gate matrix (stage applicability)

Source of truth in code: `app.assistant.migration.gates.GATE_MATRIX`.

| Plan | Gate ID | Satisfied | Blocks stages | Notes |
|---|---|---|---|---|
| 06 | `plan06_preinsert_runtime_choice` | yes | shadow/read/write/cleanup | admission before Run insert |
| 06 | `plan06_single_nonterminal_run` | yes | shadow/read/write/cleanup | one nonterminal production Run |
| 06 | `plan06_lease_recovery_sse_memory_cas` | yes | shadow/read/write/cleanup | recovery vectors |
| 07 | `plan07_interrupt_cas` | yes | read/write/cleanup | Run-first interrupt CAS |
| 07 | `plan07_entrypoint_decision_channels` | yes | write/cleanup | durable decision channels |
| 08 | `plan08_independent_write_grant` | yes | write/cleanup | write grant |
| 08 | `plan08_call_owned_approval` | yes | write/cleanup | call-owned approval |
| 08 | `plan08_cancel_started_settlement` | yes | write/cleanup | cancel × started settlement |
| 08 | `plan08_idempotency_reconciliation` | yes | write/cleanup | idempotency + reconciliation |
| 09 | `plan09_publish_gate_mode_enforce` | **no** | write/cleanup | default still `observe` |
| 09 | `plan09_gate_use_on_enable` | yes | write/cleanup | gate-use on enable path present |
| 09 | `plan09_eval_isolation` | yes | shadow/read/write/cleanup | isolation tripwires |
| 09 | `plan09_operator_principal` | **no** | shadow/read/write/cleanup | **no project-wide RBAC** |
| 09 | `plan09_m4_release_complete` | **no** | shadow/read/write/cleanup | trusted-mount only |
| 02B | `plan02b_shared_only_openclaw` | yes | cleanup | shared-only OpenClaw |

### Production cutover status (Task 0)

| Item | Value |
|---|---|
| Production cutover blocked | **yes** |
| Local/dev migration tooling allowed | **yes** |
| Reason codes | `publish_gate_default_observe`, `plan09_operator_principal_missing`, `plan09_m4_not_release_complete` |
| Gray/canary/soak/production paired-shadow | **skipped** (system not launched) |

---

## 3. Behavior-branch matrix (smart_capture write paths)

| Branch | Supported today | Plan 08 evidence | Inventory state |
|---|---|---|---|
| `create_entry` | yes | yes (golden path) | discovered |
| `update_entry` | yes | **no** | blocked (`unsupported_or_unevidenced_write_branch`) |
| `merge_entry` | yes | **no** | blocked |
| `relation_followup` | yes | **no** | blocked |

Silent retirement is forbidden; each blocked branch needs migration evidence or an approved product retirement before legacy deletion.

---

## 4. Metric dictionary

Locked definitions live in `app.assistant.migration.metrics.METRIC_DICTIONARY` (16 metrics). Required IDs include completion, failure, latency, tokens, unauthorized calls, real shadow writes, eval-shadow leaks, duplicate writes, false completion, unresolved reconciliation, injection accuracy, capability path, recovery, SSE integrity, legacy access, and L2 namespace blockers.

---

## 5. Ownership audit anchors

Legacy (Deploy B1 candidates):

- `backend/app/assistant/orchestration/intent_router.py`
- `backend/app/assistant/orchestration/supervisor_graph.py`
- `backend/app/assistant/orchestration/supervisor_state.py`
- `backend/app/assistant/orchestration/agent_runtime.py`
- `backend/app/assistant/skill_catalog/*`
- `backend/app/assistant/workflow/human_approval_runtime.py`
- `backend/app/assistant_config/models.py` (`AssistantSkill`, `AssistantHumanApproval`)
- `frontend/src/features/assistant-config/pages/SkillSettings.tsx`

Native / shared owners retained: `main_agent/*`, `skills/*`, `evaluation/*`, `capability_calls/*`, `durable/*`, `migration/*`.

---

## 6. Backup restore

| Item | Status |
|---|---|
| Live PG/MinIO restore drill | **skipped** — fixtures/services unavailable; system not launched |
| Procedure templates | `docs/runbooks/ai-runtime-migration/backup-export.md`, `deploy-a-rollback.md`, `deploy-b2-restore.md` |
| Unit-testable export digest | `digest_backup_export_manifest` + sanitized fixture |
| Skip reason | no production-shaped DB in Task 0 environment; partner constraint allows procedure + digest helper |

---

## 7. Task 0 source snapshot digest

Freeze of inventory/upstream-gate/metric/ownership/runbook schema for Task 1 dry-runs:

```text
6375df666365275af27a5965d1ca42e17d29f504fa367eea358d6b90c9cdcdde
```

Computed by:

```python
from app.assistant.migration.verification import compute_task0_source_snapshot_digest
compute_task0_source_snapshot_digest()
```

Never includes production inventory IDs.

---

## 8. Verification commands run (Task 0)

```bash
backend/.venv/bin/python -m pytest backend/tests/test_ai_runtime_migration_inventory.py -q
# 12 passed

git rev-parse HEAD
# c93a3a62b374344c40bd23f41e9ab877c10a6aa3

cd backend && .venv/bin/alembic heads
# 027869a00a47 (head)

git diff --check
# clean
```

Full backend/frontend suites: **not run** (focused inventory tooling only).

---

## 9. Explicit non-goals of Task 0

- No Alembic schema / migration audit tables
- No traffic routing, canary percentages, or rollout control rows
- No project-wide RBAC invention
- No production inventory identifiers committed
- No Tasks 1+ package/L2/approval mutation commands (CLI stubs exit `3`)

# Plan 06 Task 10 — Rollout and Exit Evidence

**Recorded at (UTC):** 2026-07-15T09:31:21Z  
**Branch:** `worktree-plan-06-durable-agent-run` / `pr-52`  
**Worktree:** `/root/MindAtlas/.claude/worktrees/plan-06-durable-agent-run`  
**HEAD at record:** `f068d05be5199f1fd8fdd8b13664e5170bf39558`  
**Base main (Plan 05 merged):** `0811239df2ef47ffff32e2aed6326f3cdd15f0f0`  
**Environment:** no live Docker / PostgreSQL / MinIO for full deploy demos — unit/integration + CI-gated evidence recorded honestly.

---

## 1. Hard release boundary (plan §1) — recorded procedure

| Step | Action | Status in this environment |
|---|---|---|
| 1 | Set `ASSISTANT_MAIN_AGENT_MODE=off` before migration/worker cutover | **pass** — default remains `off` in `backend/app/config.py` and compose (`ASSISTANT_MAIN_AGENT_MODE:-off`) |
| 2 | Drain/cancel every nonterminal pre-Plan-06 Main Agent execution | **operator procedure** — in-memory Plan 04 state cannot be fabricated into a durable Checkpoint |
| 3 | Deploy migration + API image + compatible assistant-worker while mode stays `off` | **CI/staging-gated** — symbols present; live compose not run here |
| 4 | Pass worker registration, claim/recovery, private Artifact, read-only smoke gates | **partial** — unit/SQLite smoke green; PG/MinIO live suites CI-gated |
| 5 | Re-enable only new Runs with `ASSISTANT_MAIN_AGENT_MODE=read_only` | **unit-demoed** (Task 10 rollout suite) |
| 6 | Rolling rollback sets mode `off`; never switches an existing durable Run to Legacy; keep ≥1 compatible worker image until active durable Runs drain | **unit-demoed** (Task 10 rollout suite) |

Database downgrade is **not** normal rollback and remains forbidden while any durable Main Agent Run/history exists.

---

## 2. Frozen symbols and config

### 2.1 Migration

| Item | Value |
|---|---|
| Sole Alembic head after Task 1 | **`6af373ef040f`** |
| Parent (post-Plan-05 head) | **`9ed6f561a381`** |
| File | `backend/alembic/versions/6af373ef040f_add_durable_agent_run_foundation.py` |
| `alembic heads` (this worktree) | `6af373ef040f (head)` |

### 2.2 Build / codec / runtime contract

| Item | Value |
|---|---|
| `APP_BUILD_REVISION` (local default) | `development` (production/staging must set deployment-immutable value) |
| Compose / Dockerfile | `APP_BUILD_REVISION` required for production-like deploy (`:?Set APP_BUILD_REVISION…`); multi-stage target `assistant-worker` in `backend/Dockerfile` |
| Checkpoint `schema_version` | **1** (`SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS = [1]`) |
| `runtime_contract_version` | **1** (`RUNTIME_CONTRACT_VERSION = 1`) |
| Worker capability feature digest | `de9af0d91cca357a53e11ac65c614fac5403484ed22bdb3bac55ac4f36b9c63a` (none\|read\|compute, interrupt none, codec 1) |

### 2.3 Lease / recovery / orphan-grace formula (defaults)

| Setting | Default |
|---|---|
| `ASSISTANT_WORKER_POLL_INTERVAL_MS` | 500 |
| `ASSISTANT_WORKER_LEASE_TTL_SEC` | 30 |
| `ASSISTANT_WORKER_HEARTBEAT_INTERVAL_SEC` | 5 |
| `ASSISTANT_WORKER_REGISTRATION_TTL_SEC` | 20 |
| `ASSISTANT_WORKER_MAX_RECOVERY_ATTEMPTS` | 5 |
| `ASSISTANT_WORKER_RETRY_BASE_MS` | 500 |
| `ASSISTANT_WORKER_RETRY_MAX_MS` | 30000 |
| `ASSISTANT_ARTIFACT_ORPHAN_SCAN_INTERVAL_SEC` | 60 |
| `ASSISTANT_ARTIFACT_ORPHAN_GRACE_SEC` | 900 |
| `ASSISTANT_DURABLE_CLOCK_SKEW_SEC` | 30 |
| Derived orphan grace floor | **136** s (`lease + ceil(backoff_sum_ms/1000) + scan + skew`) |
| Heartbeat rule | `heartbeat * 3 < lease_ttl` → `5 * 3 = 15 < 30` **pass** |
| Artifact bucket | `mindatlas-assistant-artifacts` |

Floor formula (plan §8 / `compute_artifact_orphan_grace_floor_sec`):

```text
lease_ttl
+ sum_{a=0..max_recovery-1} min(retry_base_ms * 2^a, retry_max_ms)  # ceil to seconds
+ orphan_scan_interval
+ clock_skew
= 30 + 16 + 60 + 30 = 136  (configured grace 900 ≥ 136)
```

### 2.4 Evaluation digests (carried from Task 0 baseline; offline/scripted)

| Ref | Value |
|---|---|
| Plan 05 entrypoint policy revision | `plan05-v1` |
| Plan 05 entrypoint policy digest | `e2049c4f562fb4281a9774779678cfb805c45d0e18cf2c7b2ff81be34c52099f` |
| Main Agent read-only ceiling digest | `a444b092ef2332ec9d0943c12d2bcbd3ac28d8c23ca0a609f692cc6eac1c6482` |
| Classification ruleset digest | `1b3d2d217c35dd9272dfcb850a7006ef38872aa8434cbd9f9c535c613ffdb711` |
| Eval dataset logical digest | `267909679d385ef618749ad85bd155159dbbf32073921a391104022deacabae7` |
| Eval dataset file SHA-256 | `f3779cd43554678385bdb2a4f334aff44c1422c9e9c3d810be188420d6bf0d68` |
| Legacy dataset logical digest | `c99a8cb943c457b42e8037d134529b425117521aa2b087fb95e11b45907f2ca9` |
| Paid Provider evaluation | **not re-run** in Task 10 (offline/scripted only) |

### 2.5 Artifact bucket policy evidence

Source: `deploy/minio-init.sh`

- Attachment bucket (`MINIO_BUCKET`): `mc anonymous set download` (public download — **must not** store durable Provider/Artifact content).
- Assistant Artifact bucket (`ASSISTANT_ARTIFACT_BUCKET`, default `mindatlas-assistant-artifacts`):
  - created with `mc mb --ignore-existing`
  - **explicit** `mc anonymous set none` (private; no anonymous policy)
  - must be distinct from `MINIO_BUCKET` (script hard-fails if equal)
- Live MinIO policy probe: **CI-gated** (`MINDATLAS_TEST_MINIO` unset here). Unit evidence: `ConfigAndArtifactPolicyEvidenceTests.test_minio_init_script_keeps_artifact_bucket_private`.

---

## 3. Task 0–9 test counts (from per-task reports)

| Task | Focus | Recorded result |
|---|---|---|
| 0 | Baseline freeze / Plans 01–05 | Core 141 passed; expanded 993 passed (+9 subtests); activation 24 passed |
| 1 | Schema / migration ORM | 24 passed, 4 skipped (PG migration CI-gated) |
| 2 | Checkpoint codec | 20–22 passed (codec suite) |
| 3 | CAS repository / events | 22 passed, 8 skipped (PG two-session CI-gated) |
| 4 | Private Artifacts | 23–24 unit passed; 6 MinIO skipped |
| 5 | Worker registry / lease / recovery | 27 passed, 10 skipped (PG lease CI-gated) |
| 6 | Runner / continuation / budgets | 21 focused passed; prior durable suites 71 |
| 7 | SSE cursor replay | 21 streaming + frontend 27–33 path green |
| 8 | Terminal memory finalizer | 20 memory commit passed |
| 9 | Crash matrix + smoke | 16 crash matrix passed (2 skipped); durable subset 76 / 123; frontend 33; PG 28 skipped; MinIO skipped |
| **10** | **Rollout demos** | **15 passed** (`tests/test_durable_rollout_task10.py`) |

**CI-gated (not live in this environment):**

- `MINDATLAS_TEST_POSTGRES_URL` — migration, two-session CAS/events, SKIP LOCKED lease/takeover
- `MINDATLAS_TEST_MINIO` — orphan GC barriers, private bucket integration
- Full compose golden: API + assistant-worker + kill/restart + SSE reconnect

---

## 4. Task 10 rollout demonstrations (unit/scripted)

Suite: `backend/tests/test_durable_rollout_task10.py` — **15 passed**.

### 4.1 `off → worker ready → read_only → off` (no active Run runtime switch)

| Step | Evidence | Result |
|---|---|---|
| Mode `off` | `admit_and_select_runtime(..., mode="off")` → `legacy` / `mode_off` | **pass** |
| Worker ready | compatible registration present | **pass** |
| Mode `read_only` | admits `main_agent` with `runtime_contract_version=1` + frozen `required_app_build_revision` | **pass** |
| Mode back to `off` | new admissions → legacy; **existing** `main_agent` Run keeps `runtime_kind`, build, status, `state_revision` | **pass** |

### 4.2 API admission without compatible worker heartbeat

| Scenario | Result |
|---|---|
| No registration | `legacy` / `no_compatible_worker` — **no** Main Agent Run kwargs |
| Stale heartbeat beyond registration TTL | `no_compatible_worker` |
| Draining worker | `no_compatible_worker` (not admitted for **new** Runs) |

Safe pre-insert fallback only; never inserts a durable Main Agent Run without a fresh compatible worker.

### 4.3 Rolling deploy drain (old-build Run ownership)

| Scenario | Result |
|---|---|
| Old worker claims old-build queued Run | **pass** (`queued → running`) |
| New worker cannot claim old-build Run | **pass** (`claim_next() is None`) |
| Parallel drain: old claims old, new claims new | **pass**; cross-claim impossible |
| Recovery build mismatch | `needs_reconciliation` / `build_revision_mismatch`; no Provider/Capability I/O |

### 4.4 Plan 07 remains disabled

| Check | Result |
|---|---|
| `MAIN_AGENT_READ_ONLY_EFFECT_CEILING.allowed_interrupt_modes` | `("none",)` only |
| Ceiling side effects | `none \| read \| compute` only |
| `build_openclaw_effect_ceiling(..., interrupt durable)` | raises `ValueError("ceiling must not permit durable interrupt")` |
| Default `ASSISTANT_MAIN_AGENT_MODE` | `off` |
| Production catalog / Main Agent surface | no production descriptor may publish `interrupt_mode=durable` under Plan 06 ceiling |

---

## 5. Exit criteria §14 → evidence map

| # | Exit criterion | Status | Evidence |
|---|---|---|---|
| 1 | Exactly one compatible worker lease can mutate an active Main Agent Run | **pass** (unit) / **CI-gated** (PG SKIP LOCKED) | Task 5 lease suites; Task 10 drain demos |
| 2 | Semantic status mutations use expected revision + allowed source; stop/result/memory/cancel converge; no result overwrites `cancelling` | **pass** (unit) / **CI-gated** (PG two-session) | Task 3 repository; Task 8/9 finalizer + crash matrix |
| 3 | API/worker restart resumes from verified immutable Checkpoint + exact frozen refs | **pass** (unit inject) / **partial** (no live compose kill) | Task 6 runner; Task 9 crash matrix + kill/restart smoke |
| 4 | Every adapter boundary has pre-execution Checkpoint; committed results atomic with pointers/events | **pass** | Task 6 prepare/started/result; Task 9 kill points 1–4, 8 |
| 5 | Provider transcript + Plan 03 continuation survive recovery without re-resolving `latest` | **pass** | Task 6 provider continuation + budget/obligation recovery |
| 6 | Complete grants/`grant_source_digest` + portable frames survive codec; recovery never copies classifier into grant | **pass** | Task 2 codec; Task 0 grant vectors |
| 7 | Manifest/policy/budgets/obligations/Provider messages/Artifacts/events have verified digest/lineage | **pass** | Tasks 2–4, 6 materialize |
| 8 | Skill activation durable only in one post-lineage lifecycle-accept CAS; staged discard leaves no residue | **pass** | Task 6 activation; Task 9 kill points 5–6 |
| 9 | SSE disconnect does not cancel; cursor replay ordered; frontend idempotent | **pass** | Task 7 streaming + frontend tests |
| 10 | Cancellation + lease loss prevent new work and converge to one terminal | **pass** (unit) / **CI-gated** (PG) | Task 5 recovery; Task 9 stop seal kill point |
| 11 | Deterministic event keys; event/state/sequence atomic; PG replay/conflict tested | **pass** (unit) / **CI-gated** (PG) | Task 3 events postgres suite |
| 12 | Private Artifact storage bounded; no anonymous; cleanup grace/live-Run/lease/inflight gates | **pass** (unit + minio-init script) / **CI-gated** (MinIO barriers) | Task 4; §2.5 this document |
| 13 | New L2 identity stable package ID + namespace with source-version/Run evidence | **pass** | Task 8 memory commit |
| 14 | Terminal memory application cannot be overtaken by a later conversation Run | **pass** | Task 8 finalizer CAS |
| 15 | L0 final output Run/message/digest-idempotent; `ready_for_memory` internal/nonterminal; blocks cancel after final content | **pass** | Task 8 + Task 9 memory kill points |
| 16 | No Checkpoint contains ephemeral/runtime object or credential | **pass** | Task 9 forbidden payload scan |
| 17 | Every production Capability remains `none \| read \| compute` with `interrupt_mode=none` | **pass** | Task 10 Plan07StillDisabled; Main Agent ceiling |
| 18 | Legacy Runs and approval behavior unchanged when Main Agent mode is off | **pass** | Task 6 mode-off legacy path; Task 10 mode demos |

Legend: **pass** = unit/integration evidence in this branch; **CI-gated** = implemented tests skip without live PG/MinIO; **partial** = unit boundary proven, full multi-process deploy not run here.

---

## 6. Task 9 verification summary (evidence companion)

Task 9 product commit: `f068d05 test(ai): crash matrix and durable run smoke` (+ `818ab54` finalizer wiring).

| Gate | Result |
|---|---|
| 12 kill-point matrix | **16 passed, 2 skipped** |
| Golden path → completed + memory | **pass** (scripted) |
| Kill/restart converges to one terminal | **pass** (unit inject) |
| Secret/runtime payload scan | **pass** |
| Frontend test/build | **33 passed** / build ok |
| Compose config with `APP_BUILD_REVISION` | **ok** |
| Live PG + MinIO + worker golden | **not live-run** — residual for staging/CI |

Full narrative: `.superpowers/sdd/task-9-report.md`.

---

## 7. Product commits in Plan 06 branch (scoped)

```text
5cbefcd docs: record Plan 06 Task 0 post-Plan-05 baseline
bcd1f6a feat(ai): add durable run persistence schema
ee5f936 feat(ai): define strict durable checkpoint contracts
3040c69 fix(ai): enforce waiting-only provider continuation on checkpoint
0c4ce6b feat(ai): commit durable run state through cas
c6a1102 fix(ai): no-op pure identical event package without revision bump
7f65f31 feat(ai): persist run artifacts in private storage
0f3bc1f fix(ai): gate artifact orphan delete on current checkpoint only
2bb8230 feat(ai): run durable assistant leases in a worker
6e72995 feat(ai): execute main agent runs from checkpoints
350d212 fix(ai): short-circuit accepted skill activation before lineage reject
382808f feat(ai): replay durable assistant events by cursor
665661d fix(ai): preserve active run on nonterminal message_end
21970cb feat(ai): finalize durable run memory once
818ab54 feat(ai): wire durable finalizer and crash inject points
f068d05 test(ai): crash matrix and durable run smoke
```

Task 10 adds scoped evidence + rollout unit demos only.

---

## 8. Residuals / operator notes before read_only enablement

1. Run PostgreSQL suites with `MINDATLAS_TEST_POSTGRES_URL` (migration, CAS races, SKIP LOCKED, takeover).
2. Run MinIO suites with `MINDATLAS_TEST_MINIO` (orphan barriers, private policy probe).
3. Staging compose golden: mode off → migrate → deploy API+worker with shared immutable `APP_BUILD_REVISION` → worker ready → `read_only` smoke → mode off drain.
4. Keep at least one compatible worker image until every active durable Run terminals.
5. Plan 07 still owns `interrupt_mode=durable` and Checkpoint v2; do not enable in production catalog under Plan 06.

---

## Conclusion

```text
PLAN_06_TASK10_ROLLOUT_EVIDENCE=yes
ALEMBIC_HEAD=6af373ef040f
ALEMBIC_PARENT=9ed6f561a381
SCHEMA_VERSION=1
RUNTIME_CONTRACT_VERSION=1
ASSISTANT_MAIN_AGENT_MODE_DEFAULT=off
ORPHAN_GRACE_FLOOR_SEC=136
ORPHAN_GRACE_CONFIG_SEC=900
ARTIFACT_BUCKET=mindatlas-assistant-artifacts
ARTIFACT_ANONYMOUS_POLICY=none
TASK10_ROLLOUT_UNIT_DEMOS=15_passed
PLAN_07_DURABLE_INTERRUPT=disabled
LIVE_COMPOSE_GOLDEN=not_run_ci_gated
```

Plan 06 implementation + rollout documentation complete for merge-dark / operator enablement review. Live PG/MinIO/compose gates remain required before production `read_only`.

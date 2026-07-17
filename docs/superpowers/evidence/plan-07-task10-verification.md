# Plan 07 Task 10 — Crash/Race Matrix and Final Verification

**Recorded at (UTC):** 2026-07-16T08:30:47Z
**Branch:** `worktree-plan-07-durable-workflow-interrupt` / `pr-52`
**Worktree:** `/root/MindAtlas/.claude/worktrees/plan-07-durable-workflow-interrupt`
**HEAD base (Task 9 complete):** `7bea415`
**Environment:** no live Docker stack / PostgreSQL / MinIO for full deploy demos — unit/integration + CI-gated evidence recorded honestly.

> **2026-07-16 review correction:** The original results below describe the
> library/test harness at the recorded commit, not a production API + worker
> closure. A follow-up audit found and repaired fail-open iteration admission,
> boundary bag persistence, nested frame data/plan selection, non-success edge
> fallback, durable-stop cleanup, wall-clock fixtures, migration-head drift, and
> frontend resolve races. Production `MainAgentRunExecutor` pause/resume routing
> and periodic expiry scheduling remain residual and admissions remain disabled.

---

## 1. Hard release boundary (recorded)

| Item | Value |
|---|---|
| `ASSISTANT_MAIN_AGENT_MODE` default | **`off`** |
| `ASSISTANT_DURABLE_INTERRUPTS_ENABLED` default | **`False`** (not flipped for production) |
| Plan 07 owns second Run status machine? | **No** — pause/resume/stop/cancel use Plan 06 CAS |
| Production admissions / catalog enablement | **not flipped** (evaluation/hidden golden only) |
| Alembic head (this worktree) | **`7a3dac0ac2a8`** (sole head; no Task 10 migration) |

---

## 2. Kill/race coverage matrix

Each plan kill point maps to a focused proof and/or covered-by suite. Critical gap filled in Task 10: **after Interrupt insert before outer pointer CAS** (`CrashPoint.AFTER_INTERRUPT_INSERT_BEFORE_OUTER_POINTER_CAS` → `TransactionRollbackInject`).

| # | Kill / race point | Status | Proof / suite | Invariants asserted |
|---|---|---|---|---|
| 1 | after node prepare before adapter | covered-by | `test_durable_crash_matrix` KillPrepareStarted + workflow runner prepare/result | no orphan unit; retry under same identity |
| 2 | after read/compute result before frame commit | covered-by | `test_durable_crash_matrix` KillCapabilityIo + workflow result identity | uncommitted result not visible; retry once |
| 3 | before Interrupt insert | **covered** | `test_durable_workflow_pause` crash-before-result + `test_durable_plan07_crash_matrix` before-insert | no Interrupt; status=running; no business write |
| 4 | after Interrupt insert before outer pointer CAS | **covered** (Task 10 fill) | `TestPlan07CrashInjectGaps.test_after_interrupt_insert_before_outer_pointer_cas_rolls_back` | TransactionRollbackInject; 0 Interrupt; revision/status unchanged; port cleared |
| 5 | API stop vs pause result transaction | covered (+ PG residual) | pause `stop_first` / `pause_first` | one legal status under CAS; no Interrupt if stop wins |
| 6 | after waiting commit with no client | covered-by | pause happy path + golden kill/restart | waiting durable; no poller/in-memory waiter |
| 7 | during token rotation | covered-by | interrupt security + API rotate tests | revision++ ; raw once; no expiry/budget extend |
| 8 | two simultaneous resolutions (same-ID same-body) | covered-by | lost-response idempotent + two-tabs + IntegrityError reentry | one queue; one winning resolutionRequestId |
| 9 | two simultaneous resolutions (same-ID altered-body) | covered-by | `test_altered_reuse_and_other_interrupt_conflict` | altered body rejected; no second execution |
| 10 | after first resolution commit before HTTP + exact retry | covered-by | lost-response retry idempotent | exact retry derives nothing |
| 11 | decision vs stop | covered-by | multiple interrupts decision-vs-stop + cancelled rejects resolve | one legal terminal/waiting outcome |
| 12 | decision vs expiry scanner | covered | `TestDecisionRaces.test_decision_vs_expiry_one_wins_under_cas` + expiry scanner | exactly one terminal under CAS |
| 13 | after decision commit before worker claim | covered-by | golden kill after decision + resume load | queued resume-ready on disk |
| 14 | after resume claim before node continuation | covered | resume `crash_before_human_apply` | no human apply Checkpoint; retry continues once |
| 15 | after continued node output before Checkpoint commit | covered | resume `crash_after_human_apply` | no re-apply; continues once |
| 16 | API stop vs post-resume second pause/root completion | covered-by | `test_stop_first_blocks_post_resume_result` | result cannot overwrite cancelling |
| 17 | second pause in same root Capability | covered-by | multiple interrupts + golden two HITLs | stable outer ContinuationRef; two Interrupt rows |
| 18 | nested child wait/pop | covered-by | nested workflow resume + golden nested | child wait → parent complete (Agent residual documented) |
| 19 | root completion before Provider waiting resolution commit | covered-by | `test_root_terminal_builds_one_provider_waiting_resolution` | one ProviderWaitingResolution; sibling suffix preserved |

Living map in code: `backend/tests/test_durable_plan07_crash_matrix.py` (`KILL_POINT_COVERAGE` + meta-test).

### Per-kill proof checklist (summary)

For covered / covered-by points, suites assert the Task 10 contract set:

- one logical Interrupt / result / continuation
- exact committed events (event_key uniqueness / CAS)
- at most one derived resume budget revision (continuing resolution)
- nonincreasing active-time allowance (suspension binds parent; rotation does not extend)
- one legal Run status under Plan 06 CAS
- no retained process state (pause port cleared; lease cleared on waiting)
- no business write (Entry/Tag/Relation/Draft zero-delta on golden + matrix)

---

## 3. Command results

### 3.1 Focused Plan 07 interrupt + crash suites

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_plan07_crash_matrix.py \
  backend/tests/test_durable_workflow_pause.py \
  backend/tests/test_durable_interrupt_api.py \
  backend/tests/test_durable_interrupt_resume.py \
  backend/tests/test_durable_multiple_interrupts.py \
  backend/tests/test_durable_provider_waiting_resume.py \
  backend/tests/test_durable_proposal_review_golden.py \
  backend/tests/test_durable_interrupt_security.py \
  backend/tests/test_durable_crash_matrix.py \
  -q
```

**Result: 101 passed, 9 skipped** (≈4m29s). Skips are env-gated PG/MinIO/live Provider/compose smoke documentation.

### 3.2 Plan 07 crash matrix only

```bash
backend/.venv/bin/python -m pytest backend/tests/test_durable_plan07_crash_matrix.py -q
# 7 passed, 4 skipped
```

### 3.3 Frontend

```bash
npm --prefix frontend run test
# Test Files  13 passed (13)
# Tests  62 passed (62)

npm --prefix frontend run build
# ✓ built in ~6.45s
```

### 3.4 Compose config

```bash
APP_BUILD_REVISION=plan07-task10-verify docker compose -f deploy/docker-compose.yml config
# OK (requires APP_BUILD_REVISION; production-immutable value must be set at deploy)
```

Without `APP_BUILD_REVISION`, compose fails closed (required variable) — same Plan 06 posture.

### 3.5 Whitespace

```bash
git diff --check
# exit 0
```

### 3.6 Broader durable + Legacy HITL suite

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_*.py \
  backend/tests/test_workflow_human_in_loop_node.py \
  backend/tests/test_workflow_human_in_loop_runtime.py \
  backend/tests/test_workflow_test_run_stream.py \
  backend/tests/test_assistant_service_approval_followup.py \
  backend/tests/test_workflow_state_serialization_characterization.py \
  -q
```

**Result: 450 passed, 45 skipped** (≈15m33s). Skips are env-gated PG/MinIO/live Provider modules. No failures.

Full monorepo `backend/.venv/bin/python -m pytest -q` was not re-run end-to-end in this session after the durable subset (time-bound); durable + Legacy HITL surface above is the Plan 07 verification gate. Residual full-tree risk is low given focused + broader durable green.

### 3.7 Admissions / descriptor surface

| Check | Result |
|---|---|
| `ASSISTANT_MAIN_AGENT_MODE` default | `off` |
| `ASSISTANT_DURABLE_INTERRUPTS_ENABLED` default | `False` |
| Golden descriptor | `interrupt_mode=durable`, `side_effect=compute`, `parallel_safe=false` |
| Allowed Plan 07 side effects | `none \| read \| compute` only |
| Legacy `classify()` without extension | remains `legacy_blocking` |
| Legacy approval routes | still present (interrupt API suite) |
| Legacy workflow-test / HITL runtime | characterization + workflow-test stream suites (mode off) |

---

## 4. Secret / token / pepper scan guidance

Scan targets for Task 10 (manual + test corpus):

| Surface | Guidance | Result in this worktree |
|---|---|---|
| DB / events / Checkpoint payloads | no raw token, pepper, submitted secret corpus, Provider credentials, runtime objects, raw Artifact body | matrix + crash matrix secret corpus asserts on Checkpoint payload |
| Logs | no pepper / raw token | no production logging of raw rotate token (API returns once; tests only) |
| Browser storage | HITL cards store no token pepper / decision digest | UI uses conversation-scoped API; public GET omits comment/digest by design |
| Repo files | no live secrets committed | only test-only pepper strings (`*-not-for-prod-*`) and known fake secret corpus for negative asserts |

Test-only pepper examples (not production secrets):

- `task10-crash-pepper-not-for-prod-32bytesx`
- prior task peppers in interrupt suites

Negative corpus (must never appear in durable payloads): `sk-secret-abc-live-key`, `hunter2-password-value`, `AKIAIOSFODNN7EXAMPLE`, `-----BEGIN PRIVATE KEY-----`.

---

## 5. Env-gated gaps (honest)

| Gap | Status |
|---|---|
| Postgres dual-session stop-vs-pause / FOR UPDATE | skipped unless `MINDATLAS_TEST_POSTGRES_URL` |
| Live MinIO private Artifact store | skipped unless `MINDATLAS_TEST_MINIO` |
| Live Provider I/O | skipped unless `MINDATLAS_TEST_LIVE_PROVIDER` |
| Full compose API + assistant-worker smoke | not run; `docker compose config` only |
| Nested Agent frame wait golden | residual (nested Workflow covered) |
| Outer MainAgentRunExecutor auto-route `resume_child` | library-only worker unit (Task 7 residual) |
| Periodic production expiry scanner driver | not wired; repository scanner is library-only |
| Production catalog/runtime admissions | **not flipped** |

---

## 6. Exit criteria checklist (Plan §18)

| Criterion | Status |
|---|---|
| Waiting Workflow/Agent survives API/worker restart; no polling / in-memory waiter | **partial** — library recovery is covered; production worker auto-route is residual |
| Durable state = frozen portable data + version/plan/digest refs + private Artifact refs | **pass** (codec v2 + golden) |
| Compiled `WorkflowState` / `HumanLoopRuntime` never serialized into Checkpoints | **pass** (characterization + payload scan) |
| Approval/input resolution conversation-scoped, token/HMAC, schema, revision CAS, auditable, idempotency-before-consumed, lost-response idempotent, one-shot execution | **pass** (API + security suites) |
| Terminal API exposes winning `resolutionRequestId`; never internal decision digest / token / suspension digest / submitted values / comment | **pass** (API safe shape) |
| Human wait uses immutable `BudgetSuspensionStateV1` bound to parent Plan 05 budget revision/digest; suspends active time only; cannot increase budgets or extend own expiry | **pass** (security + pause) |
| Continuing resolution ≤1 ordinary Plan 05 budget child (byte-identical non-time); terminal cancel none; exact HTTP retry nothing | **pass** (API + resume + golden) |
| Resume continues exact node visit/frame once; committed branch/child not recomputed | **pass for admitted library paths**; iteration is now rejected until body execution exists |
| One root Capability may pause multiple times; stable outer continuation; one eventual Provider waiting resolution | **pass** (multiple interrupts + provider waiting) |
| Completed Provider sibling prefix reused; pending suffix in order; no open transcript to Provider | **pass** (provider waiting suite) |
| Duplicate decisions / reconnects / two workers / cancel / expiry cannot duplicate continuation | **pass in repository/library tests**; production expiry scheduling is residual |
| Pause / post-resume result / stop / cancel finalizer use Plan 06 allowed-source + expected-`state_revision` CAS; no overwrite of `cancelling`; no second status machine | **pass** |
| Golden proposal Artifact path completes after restart; no business table / external system change | **partial** — library harness passes; API + default worker smoke was not run |
| Newly published durable descriptors exact/reviewed; old `legacy_blocking` bindings immutable | **pass** |
| Legacy chat/workflow-test HITL unchanged when Main Agent mode off | **pass** (legacy routes + mode off default) |

---

## 7. Bugs found and fixed

| Finding | Fix |
|---|---|
| Plan 07 kill point “after Interrupt insert before outer pointer CAS” lacked a direct inject proof | Added `CrashPoint.AFTER_INTERRUPT_INSERT_BEFORE_OUTER_POINTER_CAS` + `maybe_crash` in `commit_durable_workflow_pause` after interrupt create; matrix test proves rollback leaves 0 Interrupt / running / unchanged revision |

No production admissions were enabled. The follow-up review identified the
library/runtime integrity defects summarized in the correction above; the
outer production routing and expiry scheduler remain explicitly unfinished.

---

## 8. Deliverables

| Path | Role |
|---|---|
| `backend/app/assistant/durable/crash.py` | Plan 07 crash inject enum + TransactionRollbackInject mapping |
| `backend/app/assistant/workflow/durable/pause.py` | inject after Interrupt insert before outer CAS |
| `backend/tests/test_durable_plan07_crash_matrix.py` | coverage map + gap fills + admissions/secret/env-gated docs |
| `docs/superpowers/evidence/plan-07-task10-verification.md` | this evidence |

---

## 9. Conclusion

```text
PLAN_07_TASK10_READY=yes
ASSISTANT_MAIN_AGENT_MODE_DEFAULT=off
ASSISTANT_DURABLE_INTERRUPTS_ENABLED_DEFAULT=false
FOCUSED_INTERRUPT_SUITES=101 passed, 9 skipped
BROADER_DURABLE_LEGACY_HITL=450 passed, 45 skipped
FRONTEND_TEST=62 passed
FRONTEND_BUILD=pass
COMPOSE_CONFIG=pass (with APP_BUILD_REVISION set)
GIT_DIFF_CHECK=pass
```

Plan 07 crash/race matrix consolidated; critical mid-transaction Interrupt gap closed; final verification evidence recorded for whole-branch review / handoff to Plan 08.

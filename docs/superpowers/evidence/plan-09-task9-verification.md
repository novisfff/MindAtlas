# Plan 09 Task 9 Final Verification & Plan 10 Handoff

**Recorded at (UTC):** 2026-07-21
**Branch:** `worktree-plan-09-skill-admin`
**Worktree / repo:** `/Users/zyf/IdeaProjects/MindAtlas`
**HEAD at verification runs:** `50edecf75bf6cd62f96810d2b194bc1d94cd0dfe` (Tasks 1–11 tip; suites re-verified before Task 12 commit)
**Task 12 commit message:** `test(ai): prove Plan 09 completion lifecycle` (tip of `worktree-plan-09-skill-admin` after this change)
**Base (Plan 08 tip):** `cb5dac3`
**Sole Alembic head:** `027869a00a47` (`add skill evaluation workbench`)

---

## 1. Migration / worker contract

| Item | Value |
|---|---|
| Sole Alembic head | **`027869a00a47`** |
| Task 1 lifecycle migration (09A) | `403414a62e55` ← parent `d7e8f9a0b1c3` (Plan 08 head) |
| Task 3 evaluation migration (09B) | `027869a00a47` ← parent `403414a62e55` |
| Chain independence | 09A deploys/rolls back independently of 09B (PG suites prove intermediate) |
| Evaluation worker module | `python -m app.assistant.evaluation.worker` (not in production compose registry) |
| Runner / runtime contract | evaluation `runner_contract_version=1`; durable `RUNTIME_CONTRACT_VERSION=1` |
| Publish gate mode default | **`observe`** (`ASSISTANT_SKILL_PUBLISH_GATE_MODE`) |
| Gate evidence grace | `ASSISTANT_SKILL_GATE_EVIDENCE_GRACE_DAYS` |

### Release flags / mount

| Control | Default / behavior |
|---|---|
| `ASSISTANT_SKILL_ADMIN_TRUSTED_MOUNT` | unset → Plan 09 admin+eval routers **unmounted** |
| Staging/production | **never** mount even if trusted flag set |
| Development/test | mount only when `ASSISTANT_SKILL_ADMIN_TRUSTED_MOUNT=1` |
| `ASSISTANT_SKILL_PUBLISH_GATE_MODE` | `observe` (bootstrap ungated publish only for live-disabled aggregates; enable always gated) |
| `VITE_ASSISTANT_UNIVERSAL_SKILLS` | optional hard-off (`0`) for Universal UI |
| Operator headers (trusted mount only) | `X-MindAtlas-Operator-Id`, `X-MindAtlas-Operator-Role` — **not** release auth |

---

## 2. OpenAPI / auth release blocker (explicit)

**There is still no project-wide authenticated assistant-config principal/RBAC dependency.**

Therefore:

1. Production/staging OpenAPI has **zero** `/skill-admin` and `/skill-eval` paths (verified in `test_plan09_lifecycle_e2e.py::test_openapi_unmounted_has_no_plan09_paths` and `test_skill_admin_api` / `test_skill_eval_api`).
2. Plan 01 `/skill-packages` and `/main-agent-profiles` remain present.
3. Direct service privileged transitions still require a verified `OperatorPrincipal` value (not an `isAdmin` boolean).
4. **Plan 09 / M4 is NOT release-complete** until a real principal/operator guard is mounted on every Plan 09 route.

Trusted-mount tests prove 401 without headers and operator-role checks for privileged transitions; this is **not** release evidence.

---

## 3. Architecture / safety audit

| Check | Result |
|---|---|
| Physical package DELETE API | **absent** (archive only) |
| Immutable version in-place update/delete | **absent** (append-only) |
| Package `scripts/` execution endpoint | **absent** in skill admin; scripts inert in UI |
| Client-authored gate `passed`/`decision`/`metrics`/`assertions`/`subject` | **rejected** (`extra=forbid` + service derive) |
| EvaluationRunner production adapter imports | **banned**; tripwires → `isolation_breach` gate-ineligible |
| Production write mode off\|golden parity | covered by isolation/runner tests |
| Eval object keys `skill-eval/` | production Artifact store rejects; eval namespace enforced |
| Unauthenticated Plan 09 privileged surface in prod OpenAPI | **absent** |
| Synthetic / structural runs as promotion evidence | **cannot qualify** gates |
| Two-gate separation | publish gate cannot enable; enable requires fresh `skill_catalog_enable` on published version |

---

## 4. Focused verification commands run (Task 12)

All commands below were executed on this machine; counts are actual.

### 4.1 Process-level lifecycle + gates

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_plan09_lifecycle_e2e.py \
  backend/tests/test_skill_two_gate_lifecycle.py \
  backend/tests/test_skill_eval_real_orchestration.py \
  backend/tests/test_skill_publish_gate.py \
  backend/tests/test_skill_eval_api.py -q
```

**Result:** `66 passed, 1 warning in 13.13s`

### 4.2 Broader focused Plan 09 backend

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_plan09_lifecycle_e2e.py \
  backend/tests/test_skill_two_gate_lifecycle.py \
  backend/tests/test_skill_eval_real_orchestration.py \
  backend/tests/test_skill_publish_gate.py \
  backend/tests/test_skill_eval_api.py \
  backend/tests/test_skill_admin_api.py \
  backend/tests/test_skill_eval_isolation.py \
  backend/tests/test_skill_candidate_closure.py -q
```

**Result:** `97 passed, 1 warning in 15.78s`

### 4.3 Disposable PostgreSQL lifecycle + migration

```bash
MINDATLAS_TEST_POSTGRES_URL="postgresql://postgres:***@192.168.30.120:5432/mindatlas_test_plan09_remediation" \
MINDATLAS_TEST_POSTGRES_DESTRUCTIVE=1 \
  backend/.venv/bin/python -m pytest \
  backend/tests/test_plan09_lifecycle_postgres.py \
  backend/tests/test_agent_skill_admin_postgres_migration.py \
  backend/tests/test_skill_eval_repository_postgres.py -q
```

**Result:** `19 passed, 31 warnings in 158.60s`

### 4.4 Frontend lifecycle component evidence

```bash
npm --prefix frontend run test -- --run \
  src/features/assistant-config/plan09-lifecycle.e2e.test.tsx \
  src/features/assistant-config/pages/UniversalSkillEditorPage.test.tsx \
  src/features/assistant-config/components/Plan09RouteGate.test.tsx
```

**Result:** `Test Files 3 passed · Tests 12 passed`

### 4.5 Frontend production build

```bash
npm --prefix frontend run build
```

**Result:** success (`✓ built in 5.27s`)

### 4.6 Alembic heads

```bash
cd backend && .venv/bin/alembic heads
```

**Result:** `027869a00a47 (head)` — sole head

---

## 5. Observe → enforce readiness

| Invariant | Status |
|---|---|
| Observe: ungated publish only for live-disabled bootstrap | **enforced** in lifecycle matrix tests |
| Observe: already enabled aggregate cannot advance published pointer without gate | **enforced** |
| Enable Catalog/Profile always requires fresh matching gate | **enforced** (process e2e + two-gate) |
| Hard safety failures never waivable | **enforced** |
| Synthetic / missing probes cannot promote | **enforced** |
| Enforce mode operational cutover | **blocker** until real auth + inventory canary |

**Do not set production `ASSISTANT_SKILL_PUBLISH_GATE_MODE=enforce` or mount Plan 09 routers until principal/operator guard exists and every live-enabled aggregate has a current matching gate.**

---

## 6. Package / Profile inventory (Plan 10 inputs)

APIs (Plan 01, always mounted):

- `GET /api/assistant-config/skill-packages`
- `GET /api/assistant-config/skill-packages/{id}`
- `GET /api/assistant-config/skill-packages/{id}/versions`
- `GET /api/assistant-config/main-agent-profiles/default`
- Export: `GET .../versions/{version_id}/export`

Admin / Eval (trusted mount only): archive/unarchive, catalog enable/disable, import preview/apply, eval runs/gates/datasets.

UI:

- Universal Skills: `/settings/universal-skills`, `/settings/universal-skills/:packageId`
- Main Agent Profile: `/settings/main-agent-profile`
- Legacy Skill Library: `/settings/assistant-skills`

---

## 7. Plan 10 entry checklist

| Requirement | Plan 09 status |
|---|---|
| One Alembic head after both migrations | **yes** `027869a00a47` |
| Exact evaluation worker/runtime contract versions | worker module present; compose registration **deferred** |
| Publish gate mode `enforce` with gate for every enabled native package/Profile | **not release-ready** — observe default; needs inventory canary + real auth |
| Observe rejects ungated enabled pointer advance | **proven in tests** |
| Deterministic dataset/threshold definitions | Plan 04 fixture import path + policy pins |
| Isolation/tripwire/snapshot/worker/gate-pin tests under write off\|golden | **passing** in focused suites |
| Package/Profile inventory APIs + export digests | **available** |
| Universal UI + legacy rollback + **real** principal/operator guard | UI yes; **real guard NO → M4 release blocked** |
| Process-level create→enable lifecycle + negatives | **proven** (`test_plan09_lifecycle_e2e` + PG + frontend component) |
| No script execution / production-data test mode / unauthenticated privileged mutation / eval leak | **pass** under default-unmounted production posture |

---

## 8. CI gates added (Task 12)

`.github/workflows/ci.yml`:

- Backend job: focused Plan 09 lifecycle process harness step (`test_plan09_lifecycle_e2e` + two-gate + real-orchestration + publish-gate + eval-api)
- Agent-skill-migration job: includes `tests/test_plan09_lifecycle_postgres.py` with existing PG migration suites
- Frontend job: focused Plan 09 lifecycle component evidence step + full `npm run test` + build

Synthetic structural tests remain unit evidence only — **not** release evidence.

---

## 9. Rollback

1. Leave `ASSISTANT_SKILL_ADMIN_TRUSTED_MOUNT` unset (default) — Plan 09 routes unmounted; Plan 01 catalog/read surfaces remain.
2. Keep `ASSISTANT_SKILL_PUBLISH_GATE_MODE=observe` until inventory + real auth ready.
3. Guarded Alembic downgrade of 09B requires `MINDATLAS_PLAN09_EVAL_DOWNGRADE_ACK=1` after terminalizing active eval runs and clearing gate_use pins; 09A archive/catalog evidence blocks lifecycle downgrade without explicit cleanup.
4. Frontend: set `VITE_ASSISTANT_UNIVERSAL_SKILLS=0` and use legacy Skill Library routes.

---

## 10. Explicit M4 release decision

**Plan 09 implementation is partial** behind default-off trusted mount. Process-level create→enable lifecycle, negative matrix, PG durable pins, and component-level two-gate UI evidence exist on this branch, but the worker/gate chain is still incomplete (worker not in production compose; multi-process API+worker E2E and full real-probe MA compose remain open).

**Plan 09 / M4 release is NOT complete** because the repository still lacks a real server-side assistant-config principal/operator dependency (project-wide RBAC), and worker/gate operational evidence is incomplete.

**Plan 10 must not start production cutover** until that guard is merged, the checklist in §7 is green under enforce, and worker/gate chain evidence is regenerated.

See also: `docs/superpowers/evidence/plan-09-remediation-e2e.md`.

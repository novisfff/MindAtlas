# Plan 09 Remediation E2E Evidence

**Recorded at (UTC):** 2026-07-21
**Branch:** `worktree-plan-09-skill-admin`
**Harness kind:** process-level (in-process API + EvaluationRepository + PublishGateService + SkillAdminService); **not** multi-process API+worker spawn.
**Migration head:** `027869a00a47`
**Frontend evidence:** component-level (Vitest), not full browser E2E.

---

## 1. Positive lifecycle (backend process harness)

File: `backend/tests/test_plan09_lifecycle_e2e.py`
Test: `test_create_real_eval_publish_fresh_eval_promote_enable`

Observed path:

1. **create** native package → draft version present, catalog empty
2. **save_draft** with CAS (`requestId` + `expectedAggregateRevision`) → revision advances
3. **real_orchestration** completed dataset_scripted run on draft → `gate_eligible=true`
4. **skill_publish** gate created from qualifying run → decision `passed`
5. **publish** consumes publish gate → published pointer advances, **catalog still empty**
6. **real_orchestration** completed run on published version → `gate_eligible=true`
7. **skill_catalog_enable** gate created (distinct id/action/version from publish gate)
8. **enable_catalog** consumes promotion gate → `catalog_enabled=true` and catalog contains package

Command (local):

```bash
backend/.venv/bin/python -m pytest backend/tests/test_plan09_lifecycle_e2e.py -q
```

Result: **7 passed** (includes positive + negatives + OpenAPI unmounted + real-orchestration probe check).

---

## 2. Negative matrix (backend)

| Negative | Observation | Pointer / catalog unchanged |
|---|---|---|
| Synthetic `structural_synthetic` run | gate create raises `eval_run_not_gate_eligible` / `eval_run_not_real_orchestration` | yes |
| Wrong action (publish gate used for enable) | admin enable raises `ApiException`; catalog remains false | yes |
| Missing CAS on publish body | HTTP **422** | n/a (no mutation) |
| Client-authored gate `subject` / `passed` / `decision` | HTTP **422/400** under trusted mount | n/a |
| Probe-less real orchestration | missing safety counters stay `None`; not gate-eligible; probes restore eligibility | n/a |
| Production OpenAPI unmounted | zero `/skill-admin` / `/skill-eval` / `/catalog/enable` paths | n/a |

Supporting suites (same command set as Task 12 verification):

- `test_skill_two_gate_lifecycle.py` — publish gate cannot enable; synthetic; fresh enable gate; authoritative rebuild
- `test_skill_eval_real_orchestration.py` — expected≠actual, missing counters, fixture cannot force eligibility
- `test_skill_publish_gate.py` — hard safety, drift, observe/enforce matrix, production delta
- `test_skill_eval_api.py` — client-authored gate fields rejected, mount auth

Combined focused result:

```text
66 passed (lifecycle + two-gate + real-orchestration + publish-gate + eval-api)
```

---

## 3. PostgreSQL durable slice

File: `backend/tests/test_plan09_lifecycle_postgres.py`
DB: disposable `MINDATLAS_TEST_POSTGRES_URL` + `MINDATLAS_TEST_POSTGRES_DESTRUCTIVE=1`

| Check | Result |
|---|---|
| Sole Alembic head | `027869a00a47` |
| 09A intermediate | `403414a62e55` parent of head; Plan 08 parent `d7e8f9a0b1c3` |
| Tables at head | package, publish_gate, gate_use, eval_run, import_preview |
| Gate-use unique | second `gate_id+action` insert → `IntegrityError`; count remains 1 |
| Import preview durable columns | `id`, `expires_at`, `principal_id`, digests, `archive_bytes`, `consumed` |

Full PG command result with admin + eval repository suites:

```text
19 passed
```

---

## 4. Frontend component lifecycle

File: `frontend/src/features/assistant-config/plan09-lifecycle.e2e.test.tsx`

| Case | Result |
|---|---|
| Draft publish gate not reused for enable | enable disabled; “Evaluate the published version…” shown; enable API not called |
| Gate request has no client digests/decisions | body = requestId/action/subject ids/run ids/waivers only |
| Plan09RouteGate fail-closed (unmounted) | alert; no protected package fetch |
| Plan09RouteGate fail-closed (principal unauthorized) | alert; no protected package fetch |
| Promotion gate targets published version | action `skill_catalog_enable`, subjectVersionId = published |

With related two-gate page + route gate tests:

```text
Test Files  3 passed
Tests       12 passed
```

---

## 5. Release posture (explicit)

- **Plan 09 is partial / implementation-in-progress** under default-off trusted mount. Process-level lifecycle suites above pass, but this is not a full multi-process worker+API E2E and not a production readiness claim.
- **Worker/gate chain still incomplete** for release: evaluation worker is not in production compose; real probe-backed orchestration and full MA compose E2E remain open (Batch B).
- **M4 not release-complete** — real project-wide RBAC / authenticated principal dependency still absent.
- **Plan 10 production cutover blocked** until real principal guard + inventory canary under enforce + worker chain evidence.

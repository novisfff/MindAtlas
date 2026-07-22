# Plan 09 Remediation E2E Evidence

**Recorded at (UTC):** 2026-07-22T04:26:07Z
**Branch:** `worktree-plan-09-skill-admin`
**HEAD:** tip of `worktree-plan-09-skill-admin` after Audit R2 Batch C
**Harness kind:** process-level (in-process API + EvaluationRepository + EvaluationWorker + PublishGateService + SkillAdminService); **not** multi-process API+worker spawn.
**Migration head:** `027869a00a47`
**Frontend evidence:** component-level (Vitest), not full browser E2E.

---

## 1. Positive lifecycle (backend process harness)

File: `backend/tests/test_plan09_lifecycle_e2e.py`
Test: `test_create_real_eval_publish_fresh_eval_promote_enable`

Observed path:

1. **create** native package → draft version present, catalog empty
2. **save_draft** with CAS (`requestId` + `expectedAggregateRevision`) → revision advances
3. **real_orchestration** dataset_scripted run via **EvaluationWorker** on draft → observations decide `gate_eligible`
4. **skill_publish** gate created with **authoritative subject rebuild** (no prebuilt) → decision `passed` / soft-waived non-safety
5. **publish** consumes publish gate → published pointer advances, **catalog still empty**
6. **real_orchestration** worker run on published version
7. **skill_catalog_enable** gate created (distinct id/action/version from publish gate)
8. **enable_catalog** consumes promotion gate → `catalog_enabled=true` and catalog contains package

Command:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_plan09_lifecycle_e2e.py -q
```

Also:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_skill_two_gate_lifecycle.py -q
# rewritten to worker observations + authoritative gates only
```

---

## 2. Negative matrix (backend)

| Negative | Observation | Pointer / catalog unchanged |
|---|---|---|
| Synthetic `structural_synthetic` run | gate create raises not gate-eligible / not real_orchestration | yes |
| Wrong action (publish gate for enable) | `gate_action_subject_mismatch` | yes |
| Missing CAS on publish | 422 | yes |
| Client-authored gate subject fields | 422 (`extra=forbid`) | yes |
| Env pin / fixture drift across runs | `eval_run_env_pin_drift` / subject drift | yes |

---

## 3. Frontend lifecycle (component)

| Suite | Result |
|---|---|
| `MainAgentProfileEditorPage.test.tsx` | 9 passed — two-gate + dual-pointer workbench |
| `UniversalSkillEditorPage.test.tsx` | dual-pointer eval target (prior) |
| `SkillPublishGateDialog.test.tsx` | no client-authored closure |

---

## 4. Environment pins on gates (Batch C)

- Qualifying runs contribute isolation/policy/runtime/provider-fixture pins.
- `compare_run_to_subject` enforces them.
- Pins persisted under `assertion_snapshot.environment_pins` for consume re-verify without migration.

---

## 5. Release posture

- **Plan 09 code path complete** behind default-off mount.
- **M4 not release-complete** — no project-wide principal/RBAC.
- **Plan 10 cutover blocked.**

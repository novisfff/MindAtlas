# Plan 04 Readiness Evidence

**Recorded at (UTC):** 2026-07-14T05:02:28Z  
**Branch:** `feature/plan-04-main-agent-skill-injection`  
**Git revision:** `f7a3bbd5f52674a72b446855fc5a662ed9540f6e`  
**Local verification build revision:** `plan04-dev`  
**Conclusion:** `PLAN_04_READY=no`

This record is generated from verified local command output for Plan 04 Tasks 0–11 on the worktree. It does **not** claim production `read_only` composition is complete, paid-provider e2e, or durable resume.

---

## 1. Scope and hard rules observed

- `ASSISTANT_MAIN_AGENT_MODE` default remains `off` (verified `Settings().assistant_main_agent_mode == "off"`).
- No frontend changes.
- No Manifest v1 schema changes.
- No OpenClaw public API breakage in sampled suites.
- Read/compute ceiling only for the new path; L2 remains zero on Main Agent success path (Task 8 tests).
- Golden-path rollout is reversible: `disable` clears aggregate flags without deleting version history.
- Evaluation CI path is offline/scripted; no paid Provider calls during verification.
- Plan 02B status is recorded as complete from Task 0 baseline (non-blocking for Plan 04).

---

## 2. Prerequisites

| Check | Result |
|---|---|
| Plan 01 agent skills contracts | merged (`2d47173` / #48) |
| Plan 02A readiness | `PLAN_02A_READY=yes` at `docs/superpowers/evidence/plan-02a-readiness.md` |
| Plan 02B | `complete` (Task 0 baseline) |
| Plan 03 readiness | `PLAN_03_READY=yes` at `docs/superpowers/evidence/plan-03-readiness.md` |
| Plan 04 Task 0 baseline | `PLAN_04_TASK0_READY=yes` at `docs/superpowers/evidence/plan-04-task0-baseline.md` |
| Sole Alembic head | **pass** — `9ed6f561a381` (Plan 04 flag enablement; parent `b666b11a5faa`) |

---

## 3. Environments

| Item | Value |
|---|---|
| Python (project venv) | 3.12.x local (`backend/.venv`) |
| `APP_BUILD_REVISION` | `plan04-dev` |
| `AI_PROVIDER_FERNET_KEY` | test key set for suite |
| `PYTHONPATH` | `backend` |
| Paid Provider calls | **none** |
| Clean Python 3.11 full-suite gate | **not re-run in this session** (residual; Plan 03 clean-env remains authoritative for adapter pins) |
| PostgreSQL 15 migration gate | **skipped locally** (5 skipped in `test_main_agent_postgres_migration.py`; CI owns disposable PG) |

---

## 4. Migration inventory

| Item | Value |
|---|---|
| Sole Alembic head | `9ed6f561a381` |
| Revision file | `backend/alembic/versions/9ed6f561a381_enable_main_agent_catalog_flags.py` |
| Parent | `b666b11a5faa` (Plan 03) |
| Upgrade | drops disabled-only CHECKs only; no data mutation |
| Downgrade | refuses while any `catalog_enabled` / `runtime_enabled` is true |
| Defaults | both aggregate flags remain false |

---

## 5. Static boundary searches

```text
rg openclaw backend/app/assistant/main_agent  → (none)
rg "from app.assistant.main_agent|import app.assistant.main_agent" backend/app/assistant/provider_loop → (none)
rg "from app.assistant.main_agent|import app.assistant.main_agent" backend/app/assistant/capabilities → (none)
```

Notes:
- `provider_loop` mentions the string `main_agent` only as Manifest/domain field names and comments (`ResolvedMainAgentRef`, context_type, policy notes) — **no package import** of `app.assistant.main_agent`.
- `capabilities` has no import of `app.assistant.main_agent` (generic control port only).

---

## 6. Verification command results

Env for all runs:

```bash
export AI_PROVIDER_FERNET_KEY=07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=
export APP_BUILD_REVISION=plan04-dev
export PYTHONPATH=backend
export APP_ENV=test
```

### 6.1 Focused Plan 04 suite

```text
backend/tests/test_main_agent_catalog.py
backend/tests/test_main_agent_controls.py
backend/tests/test_main_agent_authorization.py
backend/tests/test_main_agent_skill_injection.py
backend/tests/test_main_agent_resources.py
backend/tests/test_main_agent_artifacts.py
backend/tests/test_main_agent_model_eligibility.py
backend/tests/test_main_agent_runtime.py
backend/tests/test_main_agent_prompt_builder.py
backend/tests/test_main_agent_rollout.py
backend/tests/test_main_agent_evaluation.py
backend/tests/test_main_agent_profile_service.py
backend/tests/test_main_agent_postgres_migration.py
→ 170 passed, 5 skipped
```

### 6.2 Legacy baseline + adapter compatibility

```text
backend/tests/test_skill_router_decision.py
backend/tests/test_skill_router_prompt_format.py
backend/tests/test_assistant_chat_run_stream.py
backend/tests/test_assistant_chat_stop.py
backend/tests/test_assistant_service_no_outer_fallback.py
backend/tests/test_assistant_service_l1_summary.py
backend/tests/test_assistant_service_l2_memory.py
backend/tests/test_agent_skill_legacy_adapter.py
→ 41 passed
```

### 6.3 Sample Plan 02 / Plan 03 suites

```text
backend/tests/test_capability_gateway.py
backend/tests/test_capability_policy.py
backend/tests/test_provider_agent_loop.py
backend/tests/test_provider_loop_contracts.py
backend/tests/test_provider_messages.py
backend/tests/test_openclaw_shared_capability_runtime.py
→ 153 passed
```

### 6.4 Offline evaluation gate

```text
python -m app.assistant.main_agent.evaluation \
  --dataset backend/tests/fixtures/main_agent_eval/read_only_v1.jsonl \
  --legacy backend/tests/fixtures/main_agent_eval/legacy_read_only_v1.jsonl \
  --scripted
→ success=True
  cases=113
  dataset_digest=267909679d385ef618749ad85bd155159dbbf32073921a391104022deacabae7
  legacy_digest=c99a8cb943c457b42e8037d134529b425117521aa2b087fb95e11b45907f2ca9
  recall_at_8=1.0
  false_injection_rate=0.0
  direct_answer_accuracy=1.0
  capability_path_accuracy=1.0
  completion_success=1.0
  unauthorized_broader_side_effect_count=0
```

### 6.5 Mode / flags defaults

| Check | Result |
|---|---|
| Mode default `off` | pass |
| Aggregate flags default false | pass (services never auto-enable on publish) |
| Rollout enable/disable reversible | pass (`test_main_agent_rollout.py`) |
| Legacy adapter skips `cutover` | pass (package `migration_state != shadow`) |

---

## 7. Delivered Plan 04 surface (Tasks 1–11)

| Area | Status |
|---|---|
| Aggregate flag migration | done (`9ed6f561a381`) |
| Settings mode + ceilings | done |
| Prompt builder | done |
| Catalog recall | done |
| Controls + min authorization | done |
| Skill inject + lifecycle | done (sequential path) |
| Resources / artifacts | done |
| Runtime admission + Assistant wiring | done (safe fallback) |
| Golden rollout plan/enable/disable | done |
| Offline evaluation gate | done |
| Production Gateway/catalog/inject composition | **residual** |
| Parallel multi-call lifecycle accept/discard | **residual** |
| Paid-provider e2e | **not claimed** |

---

## 8. Gate mapping

| Gate | Result | Notes |
|---|---|---|
| Gate 04A merge dark | **pass** | mode `off`, flags false, focused + legacy + sample 02/03 green |
| Gate 04B shadow evaluation | **partial** | offline fixtures pass; production Profile/package enable is operator-driven via rollout |
| Gate 04C read_only enablement | **blocked** | live composition residual; production `read_only` without injected ports falls back to Legacy |

---

## 9. Residual risks (block `PLAN_04_READY=yes`)

1. **Full production port composition incomplete** — live Gateway dispatcher, control `inject_handler` package lock/recheck, Session-backed catalog projector, and Main Agent `RoundContextProvider` are not fully composed. Production `read_only` without injected ports fails admission/composition and **falls back to Legacy** when §4.3 allows (safe default).
2. **Parallel multi-call lifecycle** accept/discard still open (sequential path only).
3. **No paid-provider e2e** — happy path uses scripted/fake loop ports only.
4. **Clean Python 3.11 full backend suite + `pip check`** not re-executed in this verification session.
5. **Local PostgreSQL parent↔head** not re-executed (CI owns disposable PG 15 fixture).
6. **quick_stats golden preference** — classifier path prefers pure-read fixture when quick_stats recursive workflow bindings are not present as system-tool-only; fixture is intentional Plan 04 fallback and is catalog-allowlisted alone on enable.

---

## 10. Rollback (documented, not executed against prod)

1. Keep/set `ASSISTANT_MAIN_AGENT_MODE=off`.
2. `python backend/scripts/assistant_main_agent_rollout.py disable` to clear `catalog_enabled` / `runtime_enabled` without deleting history.
3. Alembic downgrade of `9ed6f561a381` only after both flag classes are false.

---

## 11. Conclusion

```text
PLAN_04_READY=no
PLAN_04_GATE_04A_MERGE_DARK=yes
PLAN_04_OFFLINE_EVAL_PASS=yes
PLAN_02B_STATUS=complete
```

Plan 04 is **merge-dark ready** with mode default `off` and reversible rollout tooling, but **not** ready to claim full exit criteria / Gate 04C enablement until production composition residuals are closed and re-verified.

# Plan 03 Readiness Evidence

**Recorded at (UTC):** 2026-07-13T20:14:13Z  
**Branch:** `feature/shared-capability-runtime` (PR #49)  
**Git revision:** `4c808c567b013b8855faa7eb088e9edd671a744e`  
**Local verification build revision:** `plan03-task10-local`  
**Conclusion:** `PLAN_03_READY=yes`

This record is generated from verified local command output for Plan 03 Tasks 0–10. It does **not** switch Main Assistant, claim Plan 02B complete, claim durable waiting, or claim a first-class tenant model.

---

## 1. Scope and hard rules observed

- Default product path remains Router / Supervisor / `AssistantAgent` (`backend/app/assistant/service.py`).
- Provider Loop is internal/test-only (`run_internal_test_provider_loop`, `issuer=test`); no public chat cutover.
- Live paid probe default: `AI_MODEL_CAPABILITY_PROBE_ENABLED=false` (verified `Settings().ai_model_capability_probe_enabled is False`).
- `confirmProviderCall=true` is cost acknowledgement only, not authentication.
- No paid Provider calls were made during this verification.
- Core loop modules under `backend/app/assistant/provider_loop` (excluding `adapters/`) do not import LangGraph, LangChain, OpenAI SDK, OpenClaw, Router, Supervisor, or `run_agent_execution`.
- OpenAI Responses adapter remains deferred.
- Waiting continuation is portable but not durable; child resume/cancel authorization remains Plans 06–07.
- Repository has no first-class tenant persistence; scope-isolation tests are forward-compatible guards only.
- Plan 02B OpenClaw observation/cleanup remains **pending** and is not claimed by Plan 03.

---

## 2. Prerequisites

| Check | Result |
|---|---|
| Plan 01 Manifest v1 shape (`schema_version=1`, `provider_aliases` slot) | Pass |
| Plan 02A readiness | `PLAN_02A_READY=yes` at `docs/superpowers/evidence/plan-02a-readiness.md` (base `5f2118ac4813cb252e28cd8a6fab60a998a3fe10`) |
| Classification revision / fixed ruleset digest | `plan02-v1` / `a2c9182e4a735813319dec16ef67768773482a607c170f994c7eac92fd4a7aa2` |
| Plan 02B | **pending** (non-blocking coordination only) |

---

## 3. Environments and dependency inventory

### Project venv (`backend/.venv`) — regression only

| Item | Value |
|---|---|
| Python | 3.12.7 |
| openai | 2.15.0 |
| pydantic | 2.12.5 |
| SQLAlchemy | 2.0.45 |
| langgraph | 1.0.5 (local drift vs pin) |
| langchain-core | 1.2.7 (local drift vs pin) |
| langchain-openai | 1.1.7 |
| fastapi | 0.128.0 |
| alembic | 1.17.2 |
| `pip check` | broken transitive pins from unrelated docling/torch stack (not Plan 03) |

### Clean Python 3.11 (authoritative compatibility gate)

| Item | Value |
|---|---|
| Path | `/tmp/mindatlas-plan03-py311-adapter.0lW8CT` (recorded in `.superpowers/sdd/plan03-clean-env-path.txt`) |
| Python | 3.11.11 |
| openai | **2.45.0** (direct bound `openai>=1.104.2,<3.0.0`) |
| pydantic | 2.13.4 |
| SQLAlchemy | 2.0.51 |
| langgraph | **0.3.34** (matches `requirements.txt`) |
| langchain-core | 0.3.86 |
| langchain-openai | 0.3.35 |
| fastapi | 0.139.0 |
| alembic | 1.18.5 |
| httpx | 0.28.1 |
| anyio | 4.14.2 |
| jsonschema | 4.26.0 |
| `pip check` | **No broken requirements found** |

Local project 3.12 drift is **not** treated as Plan 03 compatibility evidence.

---

## 4. Adapter / probe contract constants

| Constant | Value |
|---|---|
| `ADAPTER_KEY` | `openai_chat_completions` |
| `DEFAULT_ADAPTER_REVISION` | `1` |
| `PROBE_CONTRACT_VERSION` | `1` |
| Direct `openai` requirement | `openai>=1.104.2,<3.0.0` in `backend/requirements.txt` |

---

## 5. Migration inventory

| Item | Value |
|---|---|
| Sole Alembic head | `b666b11a5faa` |
| Probe revision | `b666b11a5faa_add_ai_model_capability_probes.py` |
| Parent | `acf208493c87` (Plan 01) |
| Plan 01 parent | `a7b8c9d0e1f2` |
| Unique ID (not `c9d0e1f2a3b4`) | Pass |
| Downgrade guard | refuses while probe rows remain |
| CI job | `.github/workflows/ci.yml` `agent-skill-migration` runs upgrade/downgrade/upgrade + `tests/test_provider_model_probe_postgres.py` on PostgreSQL 15 |

### Local PostgreSQL 15 gate (this session)

```text
MINDATLAS_TEST_POSTGRES_URL=UNSET
localhost:5432 closed
Docker daemon unavailable
backend/.venv/bin/python -m pytest backend/tests/test_provider_model_probe_postgres.py -q
→ 4 skipped
```

Honest status: local guarded upgrade/downgrade/upgrade was **not re-executed** here. CI owns the disposable PostgreSQL 15 fixture. Non-Postgres probe/API/ORM suite: **65 passed**.

---

## 6. Verification command results

Env for all runs:

```bash
export AI_PROVIDER_FERNET_KEY=07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=
export APP_BUILD_REVISION=plan03-task10-local
export OPENCLAW_CAPABILITY_RUNTIME_MODE=legacy
```

### Step 1 — Provider Loop focused (project venv)

```text
242 passed, 2 warnings in 27.86s
```

Files: contracts, aliases, messages, streaming, agent_loop, multi_tool_calls, openai_chat_adapter, model_probe, gateway_integration, clean_environment.

### Step 2 — Clean Python 3.11 focused

```text
239 passed, 3 warnings in 26.08s
pip check: No broken requirements found
```

(same provider suite without `test_provider_loop_clean_environment.py` as specified)

### Step 3 — PostgreSQL migration gate

```text
4 skipped (MINDATLAS_TEST_POSTGRES_URL unset)
alembic heads → b666b11a5faa (head)
```

### Step 4 — Plan 01/02 integration regressions (project venv)

```text
231 passed, 1 warning, 2 subtests passed in 20.25s
```

### Step 5 — Legacy assistant regressions (project venv)

```text
16 passed in 3.02s
```

(`test_agent_test_run_stream`, `test_assistant_openai_compat`, `test_supervisor_graph_runtime`)

### Step 6 — Full backend suite

| Environment | Result |
|---|---|
| Project venv 3.12 | **19 failed**, 1483 passed, 15 skipped, 49 subtests passed |
| Clean 3.11 | **18 failed**, 1484 passed, 15 skipped, 49 subtests passed |

**Plan 03 / provider / capability / probe / gateway / manifest related failures:** none.

Baseline reds (pre-existing / unrelated product tools & config):

- `test_agent_skill_service.py` (resource blob dedup/quota)
- `test_assistant_config_service_more.py` (target folder delete)
- `test_entry_tools.py` (time/type validation set)
- `test_stats_tools.py` (statistics/activity windows)
- `test_system_ai_behavior_bindings.py` (default workflow reconcile)
- project-only extra: `test_openclaw_integration.py::test_search_entries_treats_blank_optional_filters_as_unset`

These are **not** treated as Plan 03 exit blockers.

### Step 7 — Static architecture checks

Searches under `backend/app/assistant/provider_loop` for Culina paths, `app.ai.runtime`, OpenClaw, SkillRouter, Supervisor, `skill.inject`, `run_agent_execution`, `agent_execution_core`, and secret/raw-body fields in `contracts.py`/`probe.py`: **clean** (no matches).

Core loop has no `langgraph`/`langchain` imports; direct `openai` import only under `adapters/`.

### Step 8 — No public cutover

- `backend/app/assistant/service.py` still constructs `AssistantAgent`.
- No `ProviderAgentLoop` / `run_internal_test_provider_loop` in public assistant router or `main.py`.
- Production imports of `provider_loop` outside package: probe adapter helpers from `ai_registry/service.py` only (capability probe path).
- Probe routes exist under AI model config boundary and remain feature-gated default-off.

### Step 9 — Seeded protocol invariants

```text
backend/tests/test_provider_multi_tool_calls.py → 27 passed
including test_seeded_invariant_sequences[0..4]
```

### Step 10 — Repository hygiene

```text
alembic heads → b666b11a5faa (head)
git diff --check → clean
```

---

## 7. Exit criteria mapping (summary)

| Criterion family | Verdict |
|---|---|
| Plan 01 vectors + Plan 02A ready; Plan 02B not claimed | Pass |
| Alias/Manifest directionality, append-only aliases | Pass (focused suite) |
| Fresh surface each round; reverse alias resolution | Pass |
| Classification equality pre-plan / pre-dispatch / resume; drift fail-closed | Pass |
| Every call paired; waiting only open state; resume freezes surface | Pass |
| Safe parallel only; writes never parallel; fatal retains started order | Pass |
| Isolated Sessions/evidence; soft finalization; sanitized errors | Pass |
| OpenAI Chat adapter one-round; no general retry; tools not owned by adapter | Pass |
| Direct openai bound + clean 3.11 `pip check` + adapter tests | Pass |
| Probe explicit/bounded/default-disabled; unique Alembic parent/head | Pass (local PG cycle CI-owned) |
| No Culina/OpenClaw/Main cutover/Skill inject/old engine from loop | Pass |
| Current main assistant unchanged | Pass |

---

## 8. Residual risks / known limitations

1. **Local PostgreSQL 15 upgrade/downgrade/upgrade not re-run this session** (Docker/`MINDATLAS_TEST_POSTGRES_URL` unavailable). CI `agent-skill-migration` job is the authoritative disposable gate.
2. Full suite still has unrelated baseline failures listed above; do not treat them as Plan 03 regressions.
3. Waiting is portable, not durable (Plans 06–07).
4. No first-class tenant table; isolation digests are forward-compatible only.
5. OpenAI Responses adapter deferred.
6. Plan 02B OpenClaw cleanup still pending.
7. Real system tools `list_tags` / `list_entry_types` still fail Gateway output schema validation (array root vs object); Task 9 noted; integration uses alternate system tools.
8. Live paid probe was not operator-enabled outside CI (correct default).

---

## 9. Handoff readiness

Plan 04 may begin against this `ProviderAgentLoop` and Plan 02 Gateway **without** switching Main Assistant itself until Plan 04 owns eligibility/entrypoint selection.

**`PLAN_03_READY=yes`**

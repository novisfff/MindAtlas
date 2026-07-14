# Plan 02B Shared-Mode Observation Evidence

**Recorded at (UTC):** 2026-07-14T01:41:54Z  
**Branch:** `feature/shared-capability-runtime`  
**Git revision observed:** `8f4c526e498edbda3c7fb4a2c589ed798d3d657e`  
**Local observation build revision:** `plan02b-local-observation-8f4c526`  
**Environment class:** local staging against the operator-provided PostgreSQL, **not** a production cluster  
**Conclusion:** `PLAN_02B_OBSERVATION=conditional-pass`  

This record is generated from a real process-start `OPENCLAW_CAPABILITY_RUNTIME_MODE=shared` window. It does **not** invent production traffic, dual-run write shadowing, or unit-test-only “observation.” Cleanup (Task 10 Steps 4–9) still requires **explicit human approval** after review of the residual gaps below.

---

## 1. Scope and hard rules observed

- Future-request mode only: mode was frozen at process start via cached Settings; no in-process hot toggle was used.
- No same-request fallback between `shared` and `legacy`.
- No dual execution of the same non-idempotent write through both modes.
- `submit_context_capture` was **disabled in the catalog before shared-only readiness** (operator decision: disable before 02B) because its `code_executor` closure classifies `unknown` and is shared-unavailable.
- Observation endpoint: `http://127.0.0.1:18010` (port 8010 was occupied by an unrelated Culina process).
- DB target: `postgresql://postgres:***@192.168.30.120:5432/mindatlas` from operator `backend/.env`.
- Preflight migration applied only additive Plan 01/03 heads already on the branch:
  - before: `a7b8c9d0e1f2`
  - after: `b666b11a5faa` (sole head)
  - migrations: `acf208493c87` (agent skill contract tables), `b666b11a5faa` (ai model capability probes)
- Integration secret was decrypted from existing `app_setting.openclaw_integration_config` for Bearer auth only; secret was **not** rotated and **not** written into evidence/logs.

---

## 2. Predeclared window / abort thresholds

Written **before** the shared switch:

| Item | Predeclared value |
|---|---|
| Window | bounded local staging; single observer process; request-count matrix below |
| Build attribution | every terminal log must include `mode=` and request id; process `APP_BUILD_REVISION=plan02b-local-observation-8f4c526` |
| Abort if | unexplained increase in auth/schema/5xx categories for shared-ready tools; duplicate target executions; secret leakage into logs; mixed unattributable mode traffic |
| Allowed expected failures | disabled capture → `40362`; invalid input → `42261`; bad auth → `40161`; env-dependent KG Neo4j down → characterized dependency failure; missing-entry tool `ValueError` mapped to public `40961` envelope |
| Rollback procedure | stop shared process; start new process with `OPENCLAW_CAPABILITY_RUNTIME_MODE=legacy`; smoke `search_entries`; restore shared |

---

## 3. Catalog inventory at observation time

| capability_key | source_type | enabled | disposition |
|---|---|---|---|
| `search_entries` | tool | true | shared-ready; exercised successfully |
| `get_entry` | tool | true | shared-ready; exercised successfully with real entry id |
| `create_relation` | tool | true | shared-ready; exercised successfully (write_local) |
| `query_knowledge_graph` | tool | true | shared-ready path entered; dependency failure (Neo4j localhost down) |
| `generate_periodic_review` | workflow | true | shared path entered; workflow engine RuntimeError (provider/runtime env) → public `40961` |
| `submit_context_capture` | workflow | **false** | **disabled before 02B**; execute denied `40362` |
| custom items | — | none | no enabled custom catalog items in this DB |

No unresolved `unknown` classification remained on an **enabled** catalog item after the capture disable.

---

## 4. Request matrix and outcomes

All execute requests used Bearer auth + OpenClaw audit headers  
`X-OpenClaw-Source=plan02b-observation`, `channel=local-staging`, session windows `plan02b-window-1|2|3`.

| # | Capability / case | HTTP | app code | mode log | Result classification |
|---|---|---|---|---|---|
| 1 | catalog list | 200 | 0 | n/a (list) | success; system items returned |
| 2 | `search_entries` safe read | 200 | 0 | shared / success | **pass** |
| 3 | `search_entries` invalid limit | 422 | 42261 | shared / failed | **pass** (characterized schema) |
| 4 | `get_entry` missing UUID | 409 | 40961 | shared / failed | expected domain miss mapped to availability envelope |
| 5 | `get_entry` real entry | 200 | 0 | shared / success | **pass** |
| 6 | `create_relation` bad type/ids | 409 | 40961 | shared / failed | domain validation via shared path |
| 7 | `create_relation` RELATES_TO real ids | 200 | 0 | shared / success | **pass write_local** |
| 8 | `create_relation` USES real ids | 200 | 0 | shared / success | **pass write_local** (second intentional write) |
| 9 | `query_knowledge_graph` | 500 | 50012 | shared / failed | **env gap**: Neo4j `bolt://localhost:7687` refused; LightRAG failed. Shared path invoked (`invocation_started=True`). Not a mode-branch failure. |
| 10 | `generate_periodic_review` | 409 | 40961 | shared / failed | shared adapter + workflow engine entered; provider/runtime RuntimeError sanitized to `40961`. Needs healthier AI runtime for golden success. |
| 11 | `submit_context_capture` disabled | 403 | 40362 | n/a (pre-dispatch deny) | **pass** disable gate |
| 12 | bad Bearer secret | 401 | 40161 | n/a | **pass** auth |
| 13 | rollback legacy smoke `search_entries` | 200 | 0 | **legacy** / success | **pass** future-request rollback |
| 14 | restore shared smoke `search_entries` | 200 | 0 | **shared** / success | **pass** restore |

### Source-type coverage

| Source type | Representative | Successful shared execute? |
|---|---|---|
| tool (read) | `search_entries`, `get_entry` | yes |
| tool (write_local) | `create_relation` | yes |
| workflow | `generate_periodic_review` | path entered; terminal success blocked by runtime/provider env |
| agent | none enabled | n/a |
| disabled code_executor workflow | `submit_context_capture` | denied before execution |

---

## 5. Mode attribution and no-double-execution

Terminal diagnostics observed (excerpt pattern):

```text
openclaw_capability_execution request_id=... capability=... tool=... source=plan02b-observation ... status=... mode=shared|legacy invocation_started=... duration_ms=...
```

Counts from observation logs:

| Log file | `mode=shared` | `mode=legacy` |
|---|---|---|
| `/tmp/mindatlas-02b-shared.log` | 12 | 0 |
| `/tmp/mindatlas-02b-legacy.log` | 0 | 1 |
| `/tmp/mindatlas-02b-shared2.log` | 1 | 0 |

- Every execute that reached the worker recorded exactly one `openclaw_capability_execution` line.
- No request showed dual `invocation_started` across modes.
- Secret string was grepped against all three log files: **no matches**.

---

## 6. Rollback drill (Task 10 Step 2)

1. Stopped shared process on `:18010`.
2. Started new process with `OPENCLAW_CAPABILITY_RUNTIME_MODE=legacy` and `APP_BUILD_REVISION=...-legacy-rollback`.
3. `search_entries` succeeded with log `mode=legacy`.
4. Stopped legacy process; started shared again with original build revision.
5. `search_entries` succeeded with log `mode=shared`.

Interpretation: future requests only follow the process that accepted them; Settings are process-cached; rollback is restart/rolling deploy, not a hot flag.

---

## 7. Side effects created during observation

Intentional write_local observation artifacts in DB:

| relation id | description |
|---|---|
| `61abf4d4-1711-4dde-8c3c-e0db578157a9` | `plan02b observation relation (shared mode)` |
| `0ef8a28d-e9cb-434a-a92a-1428530dd17a` | `plan02b observation relation uses` |

Operators may delete these two relations if undesired. No other destructive cleanup was performed.

---

## 8. Residual gaps / honesty notes

These do **not** look like shared/legacy branch defects, but they prevent claiming a perfect production observation:

1. **Not production.** Single local uvicorn process against the operator DB; no multi-instance rolling deploy traffic mix.
2. **`query_knowledge_graph`:** local `.env` points Neo4j at `bolt://localhost:7687`, which was down → HTTP 500 / `50012`. Shared path still started (`mode=shared`, `invocation_started=True`).
3. **`generate_periodic_review`:** shared workflow adapter invoked; engine raised `RuntimeError` (provider retries visible in logs) and public mapping returned `40961`. Deterministic unit/integration parity already covers this workflow in Plan 02A focused suites; this staging host lacked a fully healthy provider path for a golden live success.
4. **Missing-entry `get_entry` / invalid relation inputs** surface as tool `ValueError` → shared unexpected-error path → public `40961` “currently unavailable”. Characterized, but coarser than a dedicated 404-style business code. Same family as current OpenClaw execute envelope for unexpected/unavailable conditions.
5. **Worker admission latency** is high on cold start (~6–9s wall clock for first calls); in-process `duration_ms` for successful tools was ~0.9–1.5s after warmup. No unexplained regression vs legacy smoke (legacy smoke wall ~7s / in-process ~0.37s on this host).
6. **`submit_context_capture` remains disabled.** Re-enable only after a frozen sandbox/`code_executor` contract makes it shared-ready.

---

## 9. Gate checklist vs Task 10 Step 1

| Required evidence | Status |
|---|---|
| deployed/observed 02A image revision | local git `8f4c526` + `APP_BUILD_REVISION=plan02b-local-observation-8f4c526` |
| shared enabled for predeclared window | yes |
| ≥1 successful safe representative per enabled source type present | tool yes; workflow path entered but not golden-success; agent n/a |
| all enabled system items covered by staging or deterministic evidence | yes (live or characterized env failure / Plan 02A deterministic suite) |
| no unexplained 401/403/404/409/422/5xx increase for shared-ready happy paths | happy-path tools green; failures explained above |
| no duplicate target executions | yes |
| latency within accepted local bound | accepted for this host; no production p95 claim |
| audit logs IDs/status, no secrets | yes |
| no unresolved `unknown` on enabled items | yes after capture disable |
| no enabled `code_executor` closure on shared-only path | yes (`submit_context_capture` disabled) |
| no unapproved `legacy_blocking` on shared-only path | none enabled |
| custom items exercised/disabled | none enabled |
| terminal diagnostics attributable to build/mode | yes |
| future-request rollback exercised | yes |

### Observation flag

```text
PLAN_02B_OBSERVATION=conditional-pass
PLAN_02B_CLEANUP_APPROVED=yes
```

`conditional-pass` means the shared bridge was observed on real HTTP + DB for the critical tool class (including one write_local), catalog disable of the code_executor workflow holds, rollback works, and residuals are environment/provider gaps rather than dual-mode defects. It is **not** a silent substitute for production soak approval.

---

## 10. Cleanup decision

Human approved cleanup after local shared-mode observation:

```text
PLAN_02B_CLEANUP_APPROVED=yes
```

Residual workflow/KG golden gaps are accepted as environment gaps. After this cleanup, rollback is image/config rollback to verified 02A (where `OPENCLAW_CAPABILITY_RUNTIME_MODE=legacy` still exists), not a flag on the cleaned binary.

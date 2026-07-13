# Plan 02A Readiness Evidence

**Recorded at (UTC):** 2026-07-13T18:38:58Z  
**Branch:** `feature/shared-capability-runtime`  
**Base HEAD before Task 9 commit:** `5f2118ac4813cb252e28cd8a6fab60a998a3fe10`  
**Local verification build revision:** `plan02-task9-local`  
**Conclusion:** `PLAN_02A_READY=yes`

This record is generated from verified local command output for Plan 02 Tasks 0–9. It does **not** claim production observation. Plan 02B (Task 10) remains incomplete.

---

## 1. Scope and hard rules observed

- Default runtime mode remains `legacy`.
- Legacy OpenClaw dispatch branches are retained (Task 10 / 02B only removes them with human approval).
- No production observation window was run or claimed.
- Race semantics are documented honestly below.
- Every `code_executor` closure remains shared-unavailable in v1 (`submit_context_capture` classifies `unknown`).
- Shared dispatch never falls back to legacy after mode freeze / admission.

---

## 2. Environment and dependency inventory

| Item | Value |
|---|---|
| Python (local venv) | 3.12.7 (production/CI target remains 3.11; local venv is regression-only) |
| jsonschema | 4.26.0 |
| pydantic | 2.12.5 |
| sqlalchemy | 2.0.45 |
| anyio | 4.12.1 |
| langgraph (local venv) | 1.0.5 |
| langchain-core (local venv) | 1.2.7 |
| Classification revision | `plan02-v1` |
| Classification ruleset digest | `a2c9182e4a735813319dec16ef67768773482a607c170f994c7eac92fd4a7aa2` |
| Ceiling revision | `plan02-v1` |
| Public error fixture path | `backend/tests/fixtures/openclaw_runtime_error_contract.json` |
| Public error fixture sha256 | `086bda181ebdfb8a6af47d3b3179431c42f2c7dba8c1461fe06515e91cdaba81` |
| Public error fixture size | 3824 bytes |
| Fixture top-level keys | `adminOnlyNotExecuteCompatibility`, `alembicHead`, `executeBranches`, `notes`, `openclawCompatTools`, `plan01Head`, `publicResponseEnvelope`, `recordedAt`, `runtimeAuthAndExecute`, `schemaVersion`, `source`, `systemToolExports` |
| Fixture runtimeAuthAndExecute rows | 12 |

Verification env for focused suites:

```bash
export AI_PROVIDER_FERNET_KEY=07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ=
export APP_BUILD_REVISION=plan02-task9-local
```

---

## 3. Default / deploy propagation (Step 8)

Verified:

- `Settings.model_fields["openclaw_capability_runtime_mode"].default == "legacy"`.
- Invalid values (`auto`, `true`) fail Settings validation.
- `backend/.env.example`:

```text
# Plan 02A temporary OpenClaw Capability Runtime mode (process/deployment switch).
# Restart required after change. Accepts only: legacy | shared
OPENCLAW_CAPABILITY_RUNTIME_MODE=legacy
```

- `deploy/.env.example`:

```text
# Plan 02A temporary OpenClaw Capability Runtime mode (process/deployment switch).
# Restart/rolling deploy required. Accepts only: legacy | shared
# OPENCLAW_CAPABILITY_RUNTIME_MODE=legacy
```

- `deploy/docker-compose.yml` passes:

```text
OPENCLAW_CAPABILITY_RUNTIME_MODE: ${OPENCLAW_CAPABILITY_RUNTIME_MODE:-legacy}
```

and requires immutable `APP_BUILD_REVISION` for production-like deploys.

- Mode is process-cached via `get_settings()`; changing it requires restart / rolling deployment and applies only to requests accepted by an instance after it starts.
- Terminal OpenClaw execution logs include `mode=` and request/capability identifiers; authorization secrets are not logged (`OpenClawAuthenticationProof` / worker request `repr=False` on secret-bearing fields).

---

## 4. Focused 02A suite (Step 5)

Command:

```bash
for mode in legacy shared; do
  OPENCLAW_CAPABILITY_RUNTIME_MODE="$mode" \
  backend/.venv/bin/python -m pytest \
    backend/tests/test_openclaw_integration.py \
    backend/tests/test_openclaw_shared_capability_runtime.py \
    backend/tests/test_openclaw_capability_worker.py \
    backend/tests/test_remote_tool.py \
    backend/tests/test_workflow_execution_context.py \
    backend/tests/test_capability_contracts.py \
    backend/tests/test_capability_json_schema.py \
    backend/tests/test_capability_registry.py \
    backend/tests/test_capability_execution_closure.py \
    backend/tests/test_capability_classification.py \
    backend/tests/test_capability_tool_adapter.py \
    backend/tests/test_capability_workflow_adapter.py \
    backend/tests/test_capability_workflow_engine_scope.py \
    backend/tests/test_capability_agent_adapter.py \
    backend/tests/test_capability_policy.py \
    backend/tests/test_capability_gateway.py -q
done
```

Verified results:

| Mode | Result |
|---|---|
| `legacy` | **346 passed**, 1 warning, 6 subtests passed (32.22s) |
| `shared` | **346 passed**, 1 warning, 6 subtests passed (36.25s) |

Shared-module characterization after Task 9 expansion:

- `test_openclaw_shared_capability_runtime.py`: **26 passed** in both modes.

---

## 5. Main Assistant / legacy workflow regressions (Step 6)

Command:

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_test_run_stream.py \
  backend/tests/test_assistant_openai_compat.py \
  backend/tests/test_ai_registry_runtime.py \
  backend/tests/test_supervisor_graph_runtime.py \
  backend/tests/test_workflow_call_node.py \
  backend/tests/test_workflow_test_run_stream.py \
  backend/tests/test_workflow_memory_mode_step4.py \
  backend/tests/test_workflow_code_executor_runtime.py \
  backend/tests/test_workflow_http_request_runtime.py \
  backend/tests/test_workflow_human_in_loop_runtime.py \
  backend/tests/test_assistant_service_l1_summary.py \
  backend/tests/test_assistant_service_l2_memory.py -q
```

Verified result: **77 passed**, 1 warning, 7 subtests passed (5.87s).

---

## 6. Full backend suite (Step 7)

Command:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Verified result: **19 failed, 1201 passed, 11 skipped, 1 warning, 51 subtests passed** (71.43s).

### Classification of full-suite failures

Plan 02A focused + Main Assistant suites are green. Full-suite failures observed in this local run are classified as follows:

| Failure | Classification | Blocks Plan 02A? |
|---|---|---|
| `test_entry_tools.py` (`StructuredTool` not callable) | Pre-existing / LangChain tool-wrapper API mismatch in local venv (`langgraph 1.0.5` / `langchain-core 1.2.7` diverge from pinned production baseline). Unrelated to OpenClaw capability cutover. | No |
| `test_stats_tools.py` (`StructuredTool` not callable) | Same as above. | No |
| `test_agent_skill_service.py::test_resource_blob_dedup...` | Pre-existing resource/blob lifecycle failure outside Plan 02A cutover surface. | No |
| `test_assistant_config_service_more.py::test_target_folder_delete...` | Pre-existing folder cascade behavior; not OpenClaw runtime. | No |
| `test_system_ai_behavior_bindings.py::test_list_system_behaviors_reconciles...` | Pre-existing system AI behavior binding reconciliation; not OpenClaw runtime. | No |
| Transient `test_build_revision_drift_fails_closed_on_shared` during full suite | Suite-order `APP_BUILD_REVISION` pollution. Fixed to pin revision inside the test; focused suite remains green. | No (resolved for 02A claims) |

`git diff --check` was clean for the Task 9 working tree edits.

---

## 7. Parity matrix (Step 1)

Isolated dual-mode execution (never against the same non-idempotent real target; mocked runners) for system tools:

| Capability | Source | Compared | Result |
|---|---|---|---|
| `search_entries` | tool | public envelope + invocation count | match (1 call / mode) |
| `get_entry` | tool | public envelope keys + invocation count | match |
| `create_relation` | tool | public envelope keys + invocation count | match |
| `query_knowledge_graph` | tool | public envelope keys + invocation count (LightRAG availability patched) | match |
| invalid `search_entries` input | tool | HTTP/app code | both modes `422` / `42261` |

Additional integration coverage (existing suite, dual-mode green): auth codes, disabled capability, catalog schema, agent catalog item, workflow catalog item, periodic review (mocked engine), capture workflow mode-aware shared unavailability.

---

## 8. Mode freeze (Step 2)

Verified in `test_openclaw_shared_capability_runtime.py`:

- Request frozen as `legacy` ignores process flip to `shared`; shared path not entered.
- Request frozen as `shared` ignores process flip to `legacy`; legacy tool path not entered.
- Shared failure before/during adapter does not fallback to legacy.
- Shared success followed by output validation failure does not retry legacy.
- Shared cancellation maps to public `409` / `40961` with no legacy retry.
- HTTP router snapshots mode before worker dispatch.

---

## 9. Race semantics (Step 3) — documented honestly

| Race | Semantics verified / enforced |
|---|---|
| Catalog item disabled before admission | OpenClaw verifier re-reads exposure; raises `catalog_item_not_exposed` → deny. |
| Catalog item disabled after admission | Admitted call completes; future calls deny (`40362`); no cancel/replay of the admitted call. |
| Tool / application build revision drift | Frozen system-tool surface fails closed with `version_drift` / `build_revision_drift` before target invocation. |
| Tool config revision / remote endpoint drift | Covered by shared registry/tool-adapter suites (`config_revision_drift`, endpoint digest drift). |
| Workflow/Agent republish / exact version delete | Covered by registry/execution-closure suites: frozen exact version is not replaced by a new publish; missing version fails closed. |
| Component default model change | Frozen model dependency is exact; component-binding mutation does not replace frozen model (agent registry tests). |
| Exact AiModel/AiCredential revision change / credential rotation | Fail closed before client construction / decrypt (tool + agent adapter/registry tests; Plan 01 revision policy). |
| No legacy retry | Shared path never catches and re-enters legacy after mode freeze. |

Honest residual:

- Catalog re-verification is a short check before admission only. After the admission transaction ends, a later disable does not abort an already-admitted call.
- Plan 02 v1 has no durable interrupt/ledger; cooperative cancellation is best-effort at gateway boundaries.

---

## 10. System item inventory and ceilings (Step 4)

### Definitions vs ceilings

`list_openclaw_system_item_definitions` keys exactly match `OPENCLAW_SYSTEM_ITEM_EFFECT_CEILINGS`:

- `search_entries`
- `get_entry`
- `create_relation`
- `query_knowledge_graph`
- `submit_context_capture`
- `generate_periodic_review`

Ceiling revision for all rows: `plan02-v1`.

| Ceiling key | Max lattice effect | Ceiling digest |
|---|---|---|
| `search_entries` | `read` | `6964300df016479b90cda37e247e59007ad94f0ed92ffc7eb4ccd13014484043` |
| `get_entry` | `read` | `5b6b239ff07c031605e4d68e665ed3ddc491b93acaec37393f4ea82e63b175cb` |
| `create_relation` | `write_local` | `c38721d885f6fe3d7b57269773f2562dd60c247b804e882749457c59e2e3a5f6` |
| `query_knowledge_graph` | `read` | `ff08cf4ba2a141858609b32dfc9ad9c29639a0a7c1309e8c87c02d37c6b1a83c` |
| `submit_context_capture` | `write_local` | `e0a6ffa2600fa71fec8e80c2da177174e76a74c4226d2cd0faab43bb8dab05bb` |
| `generate_periodic_review` | `write_local` | `b342a23da8455c086629e270d22856ac80173c1a67ab3c9054c5bba67f7f9765` |

Custom source ceilings:

| Source | Max effect | Interrupts | Digest |
|---|---|---|---|
| tool | `write_external` | `none` | `bf6b8c33f10e78182398ca92cce7c54a98c14a646481fe354391d034c230f3df` |
| workflow | `write_external` | `none`, `legacy_blocking` | `4723f1434dd726e6f54913c44e6b56602f868dc0c9c0d2df1ed184ccd28a5fec` |
| agent | `write_external` | `none`, `legacy_blocking` | `2d95b4e4d967da13c9d1b4507d2e7a83d71ad560f52e4b620eca4db9cb9b3cf2` |

Negative rows covered by tests:

- effect above ceiling
- missing ceiling key
- missing inventory/custom source type
- unknown classification never granted
- unapproved durable interrupt; tool custom source rejects `legacy_blocking`

Classifier output and grant ceilings are independent: ceiling digests ≠ `CLASSIFICATION_RULESET_DIGEST`.

### Enabled system item runtime inventory (local seeded DB)

| Key | Type | Classified effect | Interrupt | Shared readiness | External input digest | External output digest | Binding contract digest | Dependency closure digest |
|---|---|---|---|---|---|---|---|---|
| `create_relation` | tool | `write_local` | `none` | shared-ready | `30a30516239670fe93beba5a008199715a3487598fffc2ab72462999e5a1c159` | `3a373f3ce33a2f30a32ac9ef1ff946c09db7ceebdbd2456ebe56d3e030dae0dd` | `0132990444afc0362a480cee4839149994cbf8e901adc44c5b04716669c8b3ac` | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| `generate_periodic_review` | workflow | `read` | `none` | shared-ready | `e93c35bf6e8660355bcb67243003265ad433aafffc6e204ac96ec9cd6b5846a6` | `335a3b1e5353820016b3ed4967fffe68443ee101da1cd6f57e6864240cf9fd19` | `7993f9921ccc61ee733b7e4309651f2fcfda09202ac19834313ab140d9cb331c` | `ac08d6b4d4e1d4c812e045b11834b7e1d009c5365e53d7d403a9cb7facc9ba73` |
| `get_entry` | tool | `read` | `none` | shared-ready | `bc92ebb1398f7851b276bf4991faced3ec134b6a68acbc72fdb816aa3f4e3f0f` | `aedee35dec4d4c154227465b1fb835641a87fbc7019648bcdbc8b44bbbd07090` | `263f85daeef7f4af7605ccf526e7ff47a910d217953f5b84eeb4beb7c199f3a0` | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| `query_knowledge_graph` | tool | `read` | `none` | shared-ready | `46e41300d88b034fcaa216a614d914ae3e28d77cc0f9a34e26d7d01c2e8323e7` | `0d11ff443796a500140528c1a4440c011e38ef7e8926bd741ddcf7b4a29b52ad` | `d74d24b7045e199b929061f1bda80536e5f9aad1c24649dd533f692f02060cdb` | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| `search_entries` | tool | `read` | `none` | shared-ready | `87f341775dbdf4bc7503d2ee47e2eebdfb33ba65931d583aef29103bed6af209` | `94aa2fb4fb74c6adcd24856b308a65bc2278dcaa0076cb77b895f265824156bf` | `ac1b1337bf1319850239453dc97c306db57e76ecda050f1f967e8c118ff3f232` | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| `submit_context_capture` | workflow | `unknown` | `none` | **legacy-only / shared-unavailable** | `a4b14d884b015c6c176576e166dd14337d67309fd038add38205f48ce81bd7ef` | `9cf3454f4b8fde7b6b964267a790a68118a44ee771e63f50ee974948da93321e` | `b8d92ae17bf837006efb9e1efea0438b5e69aca97687b0e31e17467af029b4b1` | `3e88524b6a642a93b3473c7cd0fc1e2c2f60403e538b2988e1fd4ec3b231640f` |

`code_executor` note: `submit_context_capture` asset includes `code_executor` nodes; classification is `unknown` and shared preflight fails with public `40961`. No `code_executor` closure may pass shared preflight in v1.

Enabled custom catalog items: none in the seeded Task 9 inventory (system items only). Staging operators must re-list enabled custom items per environment and mark each as shared-ready / legacy-only / disabled after reviewing side effects.

---

## 11. Runtime-reachability / public-error matrix

Covered runtime-reachable OpenClaw public codes exercised by fixture + characterization:

- `40161` authentication
- `40361` integration disabled
- `40362` capability disabled / unauthorized exposure
- `40461` capability not found
- `42261` schema/input/output validation
- `42262` invalid source / missing ceiling
- `40961` availability / version drift / shared runtime failure / cancellation / timeout envelope
- `40061` / `40062` fixture-covered configuration vectors

Admin-only codes remain non-execute compatibility and are not claimed as execute-path cutover evidence.

---

## 12. Expected shared diagnostics

When `OPENCLAW_CAPABILITY_RUNTIME_MODE=shared`:

- Log line `openclaw_capability_execution ... mode=shared ...` includes request id, capability, tool, source/channel/session, status, invocation_started, duration_ms.
- Successful system tools return the same public envelope as legacy (`capabilityKey`, `toolName`, `result`).
- `submit_context_capture` returns `409` / `40961` (unknown classification / unavailable).
- No secret / bearer token appears in worker request `repr` or auth proof `repr`.

Accepted local category expectations for switch-capable staging (not production observation):

- no unexplained increase in `401/403/404/409/422/5xx` vs legacy baseline for shared-ready items
- target invocation count remains 1 per request
- dual-running write calls are prohibited

---

## 13. Staging commands and rollback

### Staging enable (shared)

```bash
# rolling deploy only; process-cached Settings
export APP_BUILD_REVISION=<immutable-image-git-revision>
export OPENCLAW_CAPABILITY_RUNTIME_MODE=shared
# deploy/restart instances accepting new traffic
```

### Rollback

```bash
export OPENCLAW_CAPABILITY_RUNTIME_MODE=legacy
# restart / rolling rollback to legacy-configured instances
```

Rollback value is exactly `legacy`.

### Mixed-instance interval

During rolling deploy, instances may temporarily serve both modes. Attribute traffic using terminal diagnostics (`mode=`, `APP_BUILD_REVISION`). Expected mixed interval equals the rolling batch window only.

### Explicit prohibition

**Do not dual-run non-idempotent / write OpenClaw calls against both modes for the same real target.** Parity comparison uses isolated transactions and mocked runners only.

### Log / metric queries (operator handoff)

- Filter application logs: `openclaw_capability_execution`
- Group by: `mode`, `status`, `capability`, HTTP/app code
- Abort shared staging window if unexplained increase in failure categories or duplicate target executions is observed.

Latency thresholds: keep within the historical legacy p95 envelope for shared-ready system tools under the same fixture load; no production latency claim is made here.

---

## 14. Conclusion

| Gate | Status |
|---|---|
| Tasks 0–8 implementation present on branch | yes |
| Task 9 parity / freeze / race / system-item tests | yes |
| Focused 02A suite legacy | 346 passed |
| Focused 02A suite shared | 346 passed |
| Main Assistant / legacy workflow regressions | 77 passed |
| Default mode `legacy` + invalid rejected + compose propagation | yes |
| Evidence generated from actual digests/counts | yes |
| Production observation | **not claimed** |
| Legacy branch removal (02B) | **not done** |

### Unresolved blockers for Plan 02A

None for the automated Plan 02A delivery scope.

Residual non-blockers:

1. Full backend suite still red on pre-existing / local-venv tool-wrapper and unrelated module failures; focused 02A + Main Assistant surfaces are green.
2. `submit_context_capture` remains legacy-only under shared mode until a frozen sandbox/`code_executor` contract exists.
3. Plan 02B production observation and legacy-branch removal still require human approval.

### Final flag

```text
PLAN_02A_READY=yes
```

Plan 03 may begin against the approved shared Gateway contract without waiting for production observation, but must not import OpenClaw, depend on the temporary selector, or claim OpenClaw cutover complete. Full Plan 02 remains incomplete until 02B.

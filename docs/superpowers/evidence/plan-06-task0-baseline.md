# Plan 06 Task 0 Baseline (Post-Plan-05 Freeze)

**Recorded at (UTC):** 2026-07-15T05:08:48Z  
**Branch:** `worktree-plan-06-durable-agent-run`  
**Base commit (origin/main with Plan 05 merged as PR #52):** `0811239df2ef47ffff32e2aed6326f3cdd15f0f0`  
**Worktree:** `.claude/worktrees/plan-06-durable-agent-run`  
**HEAD at freeze:** `0811239df2ef47ffff32e2aed6326f3cdd15f0f0`  
**Working tree product code:** clean (only untracked local `backend/.venv` symlink → plan-05 venv; not product code)

## Environment

| Item | Value |
|---|---|
| Python (local venv) | **3.12.3** (production target remains **3.11**; local drift is **not** Plan 06 compatibility evidence) |
| `backend/requirements.txt` pin `langgraph` | `langgraph==0.3.34` |
| Installed `langgraph` (local venv) | **1.2.9** (mismatch vs pin; same class of drift Plan 05 recorded for Python — **not** treated as compatibility evidence) |
| Installed `langchain` / `langchain-core` | 1.3.13 / 1.4.9 |
| pydantic | 2.13.4 |
| sqlalchemy | 2.0.51 |
| jsonschema | 4.26.0 |
| alembic | 1.18.5 |
| fastapi | 0.139.0 |
| httpx | 0.28.1 |
| Alembic heads | sole **`9ed6f561a381`** |
| `ASSISTANT_MAIN_AGENT_MODE` default | `off` |
| Plan 05 entrypoint policy revision | `plan05-v1` |
| Plan 05 entrypoint policy digest | `e2049c4f562fb4281a9774779678cfb805c45d0e18cf2c7b2ff81be34c52099f` |
| Empty policy digest | `0000000000000000000000000000000000000000000000000000000000000000` |
| Classification contract revision | `plan02-v1` |
| Classification ruleset digest | `1b3d2d217c35dd9272dfcb850a7006ef38872aa8434cbd9f9c535c613ffdb711` |

### Environment verification note

The Task 0 brief still says “clean Python 3.11 with `langgraph==0.3.34`”. Actual local venv is Python **3.12.3** with **langgraph 1.2.9**. Repository pin remains `langgraph==0.3.34` in `backend/requirements.txt`. Plan document Task 0 checklist wording was amended to record mismatch honestly (mirrors Plan 05 Python handling). **Do not** use this local 1.x environment as Plan 06 durable-runtime compatibility proof.

## Prerequisite evidence

| Prerequisite | Status | Reference |
|---|---|---|
| Plan 01 | merged on main (`2d47173` / #48) | agent skill contracts + immutable versioning |
| Plan 02A | `PLAN_02A_READY=yes` | `docs/superpowers/evidence/plan-02a-readiness.md` |
| Plan 02B | `complete` / `FULL_PLAN_02_COMPLETE=yes` (non-blocking coordination) | `plan-02b-observation.md`, `plan-02b-final.md` |
| Plan 03 | `PLAN_03_READY=yes` | `docs/superpowers/evidence/plan-03-readiness.md` (shipped under shared-capability track; provider_loop present on main) |
| Plan 04 | merged on main (`718122a` / #50); readiness residual risks still recorded as `PLAN_04_READY=no` | Task 0 suites green on current main |
| Plan 05 | merged on main (`0811239` / #52); Task 9 verification recorded | `plan-05-task9-verification.md` |

Plan 02B coordination status for Plan 06: **`complete`** (non-blocking track; no dependency on observation/cleanup contracts).

## Section 2 repository facts reconfirmed

| Anchor | Confirmed |
|---|---|
| `backend/app/assistant/models.py` defines `AssistantChatRun` + `AssistantChatRunEvent` | **yes** |
| `run_service.py` allocates `seq = last_event_seq + 1` in app code and commits each mutation independently | **yes** (not multi-worker safe) |
| `service.py` owns `_background_run_threads`, stream-attachment bookkeeping, `_run_chat_background` **daemon** threads | **yes** |
| `router.py` exposes conversation-scoped Run/SSE/stop; SSE query `afterSeq` | **yes** |
| Frontend `useChat.ts` reconnects by `afterSeq`; store tracks `lastEventSeq` | **yes** |
| `AssistantConversationSkillL2Memory` keyed by `conversation_id + skill_name` (no stable package ID/namespace) | **yes** |
| `common/storage.py` + `deploy/minio-init.sh` attachment bucket with **anonymous download** | **yes** — durable Provider/Artifact content must not use this bucket |
| `deploy/docker-compose.yml` has API, LightRAG worker, Docling worker; **no assistant worker** | **yes** |
| Sole Alembic head | **`9ed6f561a381`** (plan authoring placeholder `a7b8c9d0e1f2` superseded; plan §2 updated) |
| `backend/tests/_db.py` SQLite-only | **yes** (JSONB compile shim; cannot prove partial indexes / `SKIP LOCKED` / concurrent CAS) |

## Stop-condition checks (plan §2)

| Gate | Result |
|---|---|
| Exact immutable Manifest payload/digest and lineage (`ResolvedRunManifestRevision` with `manifest_digest`, `parent_digest`, `revision`, `effective_policy_digest`) | **pass** — `app.assistant.domain.contracts.ResolvedRunManifestRevision` |
| Lossless Plan 03 Provider messages, `ProviderLoopContinuation`, transcript validators, resume request | **pass** — `app.assistant.provider_loop.messages` + `contracts` (`ProviderLoopContinuation`, `ProviderLoopResumeRequest`) |
| Plan 05 serializable `EffectiveRunPolicySnapshot`, complete `EffectiveCapabilityGrant` with `grant_source_digest`, `BudgetLedgerState`, `ObligationLedgerState`, portable `CapabilityCallFrame` | **pass** (see contracts + serializability below) |
| Plan 04/05 call-scoped pending activation `stage -> lineage -> accept`; discard leaves zero residue; no rewind of started `skill.inject` charge | **pass** — activation vector suite 24/24 |
| Lossless Provider discriminators `runtime_instruction \| runtime_context \| runtime_completion` (not downcast to `system`) | **pass** — distinct `role` literals on message models |
| Safe Main Agent public/internal event models | **pass** — `app.assistant.main_agent.events.MainAgentEventAdapter` |
| One read-only Main Agent golden path; every visible production descriptor `interrupt_mode=none` and side effects ⊆ `none\|read\|compute` | **pass** (controls + golden tools) |

**No stop-condition failure.** Task 1 may begin.

## Contract import paths (verified)

| Symbol | Module |
|---|---|
| `ResolvedRunManifestRevision` | `app.assistant.domain.contracts` |
| `ProviderLoopContinuation`, `ProviderLoopResumeRequest`, `ManifestEffectLifecyclePort` (`accept`/`discard`), `ProviderCompletionGuard` | `app.assistant.provider_loop.contracts` |
| `ProviderRuntimeInstructionMessage` (`role=runtime_instruction`) | `app.assistant.provider_loop.messages` |
| `ProviderContextUpdateMessage` (`role=runtime_context`) | `app.assistant.provider_loop.messages` |
| `ProviderCompletionInstructionMessage` (`role=runtime_completion`) | `app.assistant.provider_loop.messages` |
| `ProviderMessage` union discriminator=`role` | `app.assistant.provider_loop.contracts` / messages |
| `EffectiveRunPolicySnapshot`, `EffectiveCapabilityGrant`, `RunBudgetLimits`, `ManifestExposureIndex` | `app.assistant.policy.contracts` |
| `BudgetLedgerState`, `create_initial_ledger_state`, `serialize_ledger_state` / `deserialize_ledger_state` | `app.assistant.policy.budgets` |
| `ObligationLedgerState`, `create_initial_obligation_ledger_state`, `serialize_obligation_ledger_state` / `deserialize_obligation_ledger_state` | `app.assistant.policy.obligations` |
| `CapabilityCallFrame`, `build_capability_call_frame` | `app.assistant.policy.recursion` |
| `PendingSkillActivationPackage`, `build_domain_key_ownership_map` | `app.assistant.main_agent.manifest_runtime` |
| `MAIN_AGENT_READ_ONLY_EFFECT_CEILING`, `LOCAL_ASSISTANT_PRINCIPAL`, `compute_main_agent_grant_source_digest` | `app.assistant.main_agent.authorization` |
| `MainAgentEventAdapter` + public/internal payloads | `app.assistant.main_agent.events` |
| `MainAgentService`, `should_construct_main_agent` | `app.assistant.main_agent.service` |
| `CapabilityGateway` | `app.assistant.capabilities.gateway` |
| `FrozenCapabilityBinding`, `CapabilityAuthorizationEvidence` | `app.assistant.capabilities.contracts` |
| `MAIN_AGENT_CONTROL_CLASSIFICATIONS`, system tool classifications | `app.assistant.capabilities.classification` |

## Plan 05 / 06-relevant fixed values

### Effect ceiling

```text
MAIN_AGENT_READ_ONLY_EFFECT_CEILING:
  schema_version=1
  ceiling_key=main_agent_read_only
  revision=plan04-v1
  allowed_side_effects=["none","compute","read"]
  allowed_interrupt_modes=["none"]
  ceiling_digest=a444b092ef2332ec9d0943c12d2bcbd3ac28d8c23ca0a609f692cc6eac1c6482
```

### Principal

```text
principal_type=service principal_id=local-assistant authenticated=True
```

### Run budget defaults (`ASSISTANT_CHAT_RUN_BUDGET_DEFAULTS`)

| Field | Default |
|---|---:|
| max_provider_rounds | 8 |
| max_main_agent_cycles | 1 |
| max_active_skills | 4 |
| max_total_capability_calls | 16 |
| max_parallel_calls | 4 |
| max_capability_depth | 4 |
| max_agent_depth | 2 |
| max_same_read_signature | 3 |
| max_prompt_tokens | null |
| max_completion_tokens | 4096 |
| max_wall_time_ms | 120000 |
| max_completion_followup_rounds | 2 |

### Feature flags / operator settings present today

- `ASSISTANT_MAIN_AGENT_MODE` default **`off`** (verified `Settings().assistant_main_agent_mode == "off"`)
- `ASSISTANT_MAIN_AGENT_CATALOG_TOP_K` (hard max 32)
- `ASSISTANT_MAIN_AGENT_MAX_ACTIVE_SKILLS` (hard max 8)
- Resource/artifact byte ceilings (chunk / per-call / artifact / run / inline) in `backend/app/config.py`

## Exact message contracts (Plan 03/04/05)

### `runtime_instruction` — `ProviderRuntimeInstructionMessage`

| Field | Type |
|---|---|
| role | `Literal["runtime_instruction"]` |
| instruction_type | `Literal["soft_finalization"]` |
| locale | `str` |
| content | `str` |

### `runtime_context` — `ProviderContextUpdateMessage`

| Field | Type |
|---|---|
| role | `Literal["runtime_context"]` |
| context_type | `Literal["main_agent_manifest"]` |
| locale | `str` |
| manifest_revision | `int` |
| manifest_digest | `str` |
| prompt_build_digest | `str` |
| content | `str` |

### `runtime_completion` — `ProviderCompletionInstructionMessage`

| Field | Type |
|---|---|
| role | `Literal["runtime_completion"]` |
| locale | `str` |
| manifest_revision | `int` |
| manifest_digest | `str` |
| guard_state_digest | `str` |
| content | `str` |

`ProviderMessage` is a discriminated union on `role` including these three plus `system|user|assistant|tool`. Round-trip `model_dump(mode="json")` / `model_validate` preserves discriminators. **No reduced durable duplicate is required.**

### Portable `CapabilityCallFrame` — `app.assistant.policy.recursion.CapabilityCallFrame`

Fields: `call_id`, `capability_type` (`tool|workflow|agent`), `domain_key`, `target_identity`, `target_version_id`, `binding_contract_digest`, `owner_kind` (`main_agent|skill_version`), `owner_version_id`, `capability_depth`, `agent_depth`, `frame_digest`.  
`model_dump` / `model_validate` round-trip verified. **Portable as-is for durable codec.**

### Complete `EffectiveCapabilityGrant` (no classifier-derived grants)

Fields: `owner_kind`, `owner_version_id`, `capability_key`, `binding_contract_digest`, `allowed_side_effects`, `allowed_interrupt_modes`, `platform_ceiling_digest`, `entrypoint_policy_digest`, `global_policy_digest`, `owner_policy_digest`, `grant_source_digest`.

Smoke: `build_effective_capability_grant(...)` → `model_dump(mode="json")` → `model_validate` identity; serialized JSON contains **no** classifier/classification fields; `grant_source_digest` is independent SHA-256.

### Ledger states

- `BudgetLedgerState`: revisioned counters + reservations + `ledger_digest`; `serialize_ledger_state` / `deserialize_ledger_state` lossless.
- `ObligationLedgerState`: `revision`, `obligations`, `evidence_edges`, `followup_rounds_started`, `ledger_digest`; serialize helpers lossless.

### `ProviderLoopContinuation` (portable waiting)

Includes `contract_version=1`, execution scope, model ref, locale, round/tool counters, usage, current manifest revision/digest, exposed surface, assistant/transcript digests, waiting call, next call index, pending/completed call records.

### Activation package

`PendingSkillActivationPackage` (`app.assistant.main_agent.manifest_runtime`) carries staged effect + candidate policy/budget/terminal/rebind material; lifecycle via `ManifestEffectLifecyclePort.accept` / `.discard`.

## Public / internal event payloads

Module: `backend/app/assistant/main_agent/events.py`

**Public names:** `runtime_selected`, `skill_search`, `skill_activation_end`, `manifest_revision`, `content_delta`, `run_status`, `message_end`, `fallback_selected`.

**Internal names (Plan 05 allowlist + staging):**  
`authorization_decision`, `budget_reserved`, `budget_started`, `budget_released`, `budget_denied`, `obligation_created`, `obligation_resolved`, `completion_decision`, `policy_snapshot`, `skill_activation_start`, `main_agent_diagnostic`  
(`PLAN05_INTERNAL_EVENTS` frozenset; marked `_visibility="internal"` so stream can advance cursor without yielding).

Representative public payload shapes:

- `runtime_selected`: `runId`, `sourceRuntime`, `targetRuntime`, optional `reasonCode`, `mode`
- `skill_search`: `catalogDigest`, `resultCount`, `excludedCount`, `semanticFallback`, `status`
- `skill_activation_end`: `status`, `activatedVersionIds`, `noopVersionIds`, optional `reasonCode`, success-only `manifestRevision`/`manifestDigest`
- `manifest_revision`: `revision`, `manifestDigest`, `activeSkillCount`, optional `parentDigest`
- `fallback_selected`: run/runtime IDs, `reasonCode`, provider/capability start counts, `userOutputStarted`, optional `strongestSideEffect`
- `content_delta`: `{delta}`

### Frontend reconnect

- API: `GET .../runs/{runId}/stream?afterSeq=` (`router.py` Query alias `afterSeq`)
- `run_service.list_events_after` filters `seq > after_seq`
- `useChat.attachActiveRun`: `afterSeq = max(storedSeq, run.checkpointSeq)`; `buildRunStreamUrl(..., afterSeq)`
- Store: `lastEventSeq` on active run; consumers must remain idempotent under at-least-once replay

## Production Main Agent descriptors (`none|read|compute`, `interrupt_mode=none`)

### Control capabilities (`MAIN_AGENT_CONTROL_CLASSIFICATIONS`)

| domain_key | side_effect | interrupt_mode | parallel_safe |
|---|---|---|---|
| skill.search | read | none | True |
| skill.inject | none | none | False |
| skill.read_resource | read | none | True |
| artifact.read | read | none | True |

### Golden fixture read tools

| tool | side_effect | interrupt_mode |
|---|---|---|
| get_statistics | read | none |
| analyze_activity | read | none |
| get_tag_statistics | read | none |

All production Main Agent visible descriptors verified ⊆ `none|read|compute` with `interrupt_mode=none`.

## Golden Profile / Skill / model / evaluation refs

| Ref | Value |
|---|---|
| Golden fixture canonical name | `main-agent-read-only-fixture` |
| Display name | `Main Agent Read-Only Fixture` |
| Read tools | `get_statistics`, `analyze_activity`, `get_tag_statistics` |
| Quick-stats strategy names | `quick-stats` / legacy alias `quick_stats` |
| Control keys | `skill.search`, `skill.inject`, `skill.read_resource`, `artifact.read` |
| Default profile `output_budget` | matches Plan 05 budget defaults (8 rounds / 16 calls / depth 4 / agent depth 2 / 4096 tokens / 120s wall) |
| Eval dataset schema version | 1 |
| Dataset path | `backend/tests/fixtures/main_agent_eval/read_only_v1.jsonl` (113 cases) |
| Dataset file SHA-256 | `f3779cd43554678385bdb2a4f334aff44c1422c9e9c3d810be188420d6bf0d68` |
| Dataset logical digest | `267909679d385ef618749ad85bd155159dbbf32073921a391104022deacabae7` |
| Legacy baseline path | `backend/tests/fixtures/main_agent_eval/legacy_read_only_v1.jsonl` (113 lines) |
| Legacy file SHA-256 | `0c47bdf67a54f61a2577cdce2b25949ded26ff26eed29cca954b06801cec8aa0` |
| Legacy logical digest | `c99a8cb943c457b42e8037d134529b425117521aa2b087fb95e11b45907f2ca9` |
| App build / model | no paid Provider calls in Task 0; golden path remains offline/scripted evaluation |

## API-process loss during Plan 04 Main Agent Run (daemon state)

**Code evidence (live process kill not required for Task 0):**

1. `AssistantService._background_run_threads: dict[str, threading.Thread]` is a **process-local class dict** (`backend/app/assistant/service.py`).
2. `_start_background_run` starts `threading.Thread(..., daemon=True)` named `assistant-run-{id[:8]}` targeting `_run_chat_background`.
3. Stream attachment is also in-process (`_attached_run_stream_ids`).
4. Event sequence allocation is non-atomic app-level `last_event_seq + 1` with independent commits — unsafe under multiple workers and lost entirely if the API process dies mid-run before durable worker ownership exists.
5. Compose has **no** assistant worker service; Run execution is co-located with the API process.

**Conclusion:** killing/restarting the API process loses in-memory daemon thread state, stream attachment, and any uncheckpointed Main Agent/Provider loop memory. This is the Plan 06 problem statement (durable Run/worker/lease/checkpoint).

## Section 12 path inspection

All listed modify targets exist. `backend/app/assistant/durable/` does **not** exist yet (correct for Task 0).

Plan document updates made in this Task 0 freeze:

1. §2 Alembic placeholder `a7b8c9d0e1f2` → record sole head `9ed6f561a381`.
2. §12 replaced “exact post-Plan-05 …” placeholders with concrete `main_agent/*`, `policy/*`, `provider_loop/*`, `domain/*`, `capabilities/*` paths.
3. Task 0 checklist env line amended for local Python 3.12 + langgraph 1.x mismatch vs pins.

No prerequisite path renames required; no duplicate durable contract types invented.

## Suite results

### Core Task 0 command (plan-suggested)

```text
backend/tests/test_assistant_chat_run_service.py
backend/tests/test_assistant_chat_run_stream.py
backend/tests/test_assistant_memory_l0.py
backend/tests/test_assistant_memory_l1_service.py
backend/tests/test_assistant_memory_l2_service.py
backend/tests/test_main_agent_runtime.py
backend/tests/test_agent_policy_runtime.py
backend/tests/test_agent_budget_ledger.py
backend/tests/test_agent_obligation_ledger.py
→ 141 passed in 40.68s
```

### Expanded Plans 01–05 + Run/SSE/stop/memory (993 passed, 9 subtests)

Includes Plan 01 skill package/publish/spec/models/service/closure/import-export + manifest; Plan 02 capability policy/gateway/contracts/registry + openclaw shared runtime; Plan 03 provider loop/messages/contracts/multi-tool/gateway/streaming; Plan 04 main_agent authorization/injection/runtime/evaluation/catalog/controls/artifacts/resources/prompt/profile/rollout; Plan 05 exposure/conflicts/policy matrix/runtime/budgets/obligations/completion/scheduler/recursion/evidence; plus chat run service/stream/stop and memory L0–L2.

```text
993 passed, 3 warnings, 9 subtests passed in 504.15s
```

### Plan 05 copy-descriptor / grant-source negatives + Plan 04/05 activation vectors

```text
# negatives (authorization / policy evidence / matrix)
test_copy_descriptor_effect_verifier_cannot_widen_grant
test_grant_source_digest_changes_with_ceiling_or_owner
test_issue_rejects_missing_grant_source
test_descriptor_read_to_write_denied
test_copy_descriptor_effect_trap
test_omit_grant_source_digest_denied
test_grant_source_digest_mismatch_denied
test_ceiling_revision_changes_grant_source
test_classification_ruleset_not_in_grant_source
test_grant_source_set_digest_order_independent
test_grant_derivation_does_not_use_descriptor_side_effect
test_descriptor_effect_denied_by_global_after_grant_freeze
test_version_or_digest_drift_descriptor
test_grant_derived_before_descriptor_and_digest_stable

# activation (stage -> lineage -> accept / discard residue / domain keys / inject charge)
test_stage_inject_and_lifecycle_accept
test_lineage_reject_discards_package
test_domain_key_conflict_with_base_control
test_same_batch_domain_key_conflict
test_discard_leaves_no_owner_bucket_or_obligation
test_staged_package_not_visible_on_ledgers_until_accept
test_wrong_effect_digest_discards_package
test_production_package_requires_finished_skill_inject_reservation
test_next_manifest_hook_discards_failed_control_effect
test_owner_budget_limits_commit_only_on_accept

→ 24 passed in 1.86s
```

### Alembic

```text
(cd backend && .venv/bin/alembic heads)
→ 9ed6f561a381 (head)   # sole head
```

## Notes for Plan 06 implementers

1. Reuse Plan 05 portable types end-to-end in durable codec (`EffectiveRunPolicySnapshot`, complete `EffectiveCapabilityGrant`, ledger states, `CapabilityCallFrame`, Provider message discriminators, `ProviderLoopContinuation`). **Do not** invent reduced durable-only clones.
2. Keep `provider_loop` pure (no DB imports). Persistence/leases/recovery live under `app.assistant.durable` + `app.assistant.worker`.
3. Replace daemon-thread Main Agent execution with worker claim/lease; API stop must use status/`state_revision` CAS per plan §3–4.
4. Do not store Provider/Artifact bytes in the anonymous-download attachment bucket; private bucket path required.
5. Parent migration head is **`9ed6f561a381`** only.
6. Local langgraph 1.x / Python 3.12 is regression-only; CI/production pin remains `langgraph==0.3.34` + Python 3.11 target.
7. `ASSISTANT_MAIN_AGENT_MODE=off` remains default; durable foundation must not force-enable Main Agent.

## Conclusion

```text
PLAN_06_TASK0_READY=yes
PLAN_02B_STATUS=complete
PLAN_02A_READY=yes
PLAN_03_READY=yes
PLAN_05_MERGED=yes  # 0811239 / #52
ALEMBIC_HEAD=9ed6f561a381
CORE_SUITE=141_passed
EXPANDED_PLANS_01_05=993_passed
NEG_AND_ACTIVATION=24_passed
LANGGRAPH_PIN=0.3.34
LANGGRAPH_LOCAL=1.2.9  # mismatch recorded; not compatibility evidence
PYTHON_LOCAL=3.12.3
PYTHON_TARGET=3.11
```

Task 1 may begin: durable schema / contracts / codec design against this frozen baseline.

# Plan 09 Task 0 Baseline (Plans 01–08 Freeze + UX/API Inventory)

**Recorded at (UTC):** 2026-07-19T15:06:01Z
**Branch:** `worktree-plan-09-skill-admin`
**Worktree:** `/root/MindAtlas/.claude/worktrees/plan-09-skill-admin`
**HEAD at freeze:** `cb5dac353408021fffeb5e3902acd2fc317b91de` (`cb5dac3`)
**HEAD subject:** `feat(ai): Plan 08 capability call ledger and durable golden writes (#55)`
**Working tree product code at freeze start:** clean (untracked local `backend/.venv` symlink only; frontend `node_modules` installed during Task 0 for baseline commands).

---

## 1. Environment

| Item | Value |
|---|---|
| Python (local venv) | **3.12.3** (production target remains **3.11**; local drift is **not** Plan 09 compatibility evidence) |
| `backend/requirements.txt` pin `langgraph` | `langgraph==0.3.34` |
| Installed `langgraph` (local venv) | **1.2.9** (mismatch vs pin — same class of drift Plans 05–08 recorded) |
| Installed `langchain` / `langchain-core` | 1.3.13 / 1.4.9 |
| pydantic | 2.13.4 |
| sqlalchemy | 2.0.51 |
| jsonschema | 4.26.0 |
| alembic | 1.18.5 |
| fastapi | 0.139.0 |
| httpx | 0.28.1 |
| cryptography | 49.0.0 |
| Node / npm | **v22.22.0** / **10.9.4** |
| Frontend lock | `frontend/package-lock.json` present (7299 lines); `npm --prefix frontend ci` succeeded (470 packages) |
| Sole Alembic head | **`d7e8f9a0b1c3`** (`d7e8f9a0b1c3_protect_reconciliation_evidence.py`; parent `f2c3a4b5d6e7`) |
| Occupied revision ID named in plan | **`b4c5d6e7f8a9`** is already used by `b4c5d6e7f8a9_add_openclaw_capability_item_catalog.py` |
| Task 1 migration parent | **`d7e8f9a0b1c3`** (sole post-Plan-08 head). Generate a **fresh unique** revision ID with `alembic revision -m "add skill package admin lifecycle"`; do **not** preselect `b4c5d6e7f8a9`. |
| Plan 08 migration chain | `7a3dac0ac2a8 → 984c07876856 → f2c3a4b5d6e7 → d7e8f9a0b1c3` |
| `APP_BUILD_REVISION` default | **`development`** (`Settings.app_build_revision`) |
| `MINDATLAS_TEST_POSTGRES_URL` | **unset** (PG two-session suites skipped when encountered) |
| `MINDATLAS_TEST_MINIO` | **unset** (MinIO suites skipped when encountered) |
| Live Docker compose golden | **not run** |

### Worker / codec / flags (frozen at Plan 08 tip)

| Item | Value |
|---|---|
| Checkpoint `SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS` | **`frozenset({1, 2, 3})`** (`app.assistant.durable.codec`; v3 from Plan 08 ledger) |
| `RUNTIME_CONTRACT_VERSION` | **1** (`app.assistant.durable.worker_registry`) |
| Worker capability feature digest | `11af8408a0d3a6ff93a5170a9bb6758f430773d1e1343ee3982396f0ed9cd3b4` (`default_capability_feature_digest` / `plan08_capability_ledger_feature_digest`) |
| `ASSISTANT_MAIN_AGENT_MODE` default | **`off`** |
| `ASSISTANT_DURABLE_INTERRUPTS_ENABLED` default | **`false`** |
| `ASSISTANT_CAPABILITY_LEDGER_MODE` default | **`legacy_read_only`** |
| `ASSISTANT_MAIN_AGENT_WRITE_MODE` default | **`off`** |
| `ASSISTANT_MAIN_AGENT_WRITE_COHORT_DIGEST` default | empty |
| `ASSISTANT_CAPABILITY_CALL_IDEMPOTENCY_SECRET` default | empty |
| `ASSISTANT_CAPABILITY_RECONCILIATION_ENABLED` default | **`false`** |
| Main Agent effect ceiling revision | **`plan07-v1`** |
| Main Agent read-only ceiling digest | `c67231e1c3372271ba2b56e450779889d38a28d1bd04b09c202e812b12077669` |
| Ceiling allowed side effects | **`("none", "compute", "read")`** |
| Ceiling allowed interrupt modes | **`("none", "durable")`** |
| Plan 05 hard release gate | **`PLAN05_RELEASE_GATE_SIDE_EFFECTS = ("none", "compute", "read")`** |
| Plan 05 entrypoint policy revision / digest | **`plan05-v1`** / `9c3899d2450714b2783ad09792dd5fad725324f024524b931de7b3e7feff639b` |
| Classification ruleset digest | `1b3d2d217c35dd9272dfcb850a7006ef38872aa8434cbd9f9c535c613ffdb711` (`plan02-v1`) |
| Policy decision types (merged) | `AuthorizationDecision` (v1, no `contract_version`) + `AuthorizationDecisionV2` (`contract_version`, `policy_allowed`, `dispatch_disposition`, `write_release_digest`, …) in `app.assistant.policy.contracts` |
| Budget suspension contract | `BudgetSuspensionStateV1` fields: `contract_version`, `run_id`, `interrupt_id`, `parent_budget_revision_id`, `parent_ledger_revision`, `parent_ledger_digest`, `suspended_at_utc`, `remaining_active_ms`, `human_wait_expires_at_utc`, `suspension_digest` |
| Plan 09 flags | **absent** (no `ASSISTANT_SKILL_ADMIN*`, `ASSISTANT_SKILL_EVAL*`, publish-gate mode flags, or OperatorPrincipal dependency) |

### Deploy workers (current)

| Service | Image target | Role |
|---|---|---|
| `assistant-worker` | `assistant-worker` | Durable Main Agent worker (`python -m app.assistant.worker`) |
| `lightrag-worker` | `worker` | LightRAG index outbox consumer |
| `docling-worker` | `parse-worker` | Attachment parse worker |
| Evaluation worker | **absent** | Plan 09 Task 3+ adds `python -m app.assistant.evaluation.worker` |

Compose flag defaults (`deploy/docker-compose.yml` / `.env.example`) match Settings: main-agent `off`, interrupts `false`, ledger `legacy_read_only`, write `off`, reconciliation `false`. Production-like deploys require non-`development` `APP_BUILD_REVISION`.

### Prior evidence paths

| Evidence | Path |
|---|---|
| Plan 02B observation / final | `docs/superpowers/evidence/plan-02b-observation.md`, `plan-02b-final.md` |
| Plan 05–07 Task 0 | `docs/superpowers/evidence/plan-05-task0-baseline.md` … `plan-07-task0-baseline.md` |
| Plan 08 Task 0 / Task 9 | `docs/superpowers/evidence/plan-08-task0-baseline.md`, `plan-08-task9-verification.md` |

---

## 2. Plans 01–08 contract symbol proofs

### 2.1 Plan 01 — package parse / import / version / publish (pass)

| Claim | Result | Exact symbols / files |
|---|---|---|
| ZIP/dir parse + path security | **pass** | `parse_skill_zip`, `parse_skill_directory_files`, `normalize_package_path`, `detect_media_type`, limits (`MAX_ZIP_UPLOAD_BYTES=32MiB`, `MAX_TOTAL_UNCOMPRESSED_BYTES=25MiB`, `MAX_ENTRIES=200`), symlink/device/fifo/socket/encrypted rejection in `app.assistant.skills.package_io` |
| Frontmatter / manifest parse | **pass** | `parse_skill_md` → `AgentSkillFrontmatter`; `parse_mindatlas_yaml` → `MindAtlasSkillManifestV1` |
| Create / import / export | **pass** | `SkillPackageService.create_native_package`, `import_package`, `export_version` (`app.assistant.skills.service`); HTTP: `create_skill_package`, `import_skill_package`, `export_skill_package_version` (`skills/router.py`) |
| Draft save append-only | **pass** | `SkillPackageService.save_draft` + `SaveSkillDraftCommand` / `PUT .../draft` |
| Publish advances pointer without auto-catalog | **pass** | `SkillPackageService.publish` sets `published_version_id`, forces `catalog_enabled=False`; comment: “Publish never auto-enables catalog” |
| Aggregate columns (pre-09) | **pass (gap expected)** | `AssistantSkillPackage`: `canonical_name`, `display_name`, `description`, `draft_version_id`, `published_version_id`, `catalog_enabled`, `is_system`, … — **no** `aggregate_revision`, `archived_at`, `catalog_enabled_at` yet (Task 1) |
| Alias immutability | **pass** | `AssistantSkillPackageAlias` append-only; `alias_type IN ('canonical','legacy','custom')`; no delete-orphan |
| OpenAPI Plan 01 paths present | **pass** | See §4; 13 skill-package/main-agent-profile paths; **no** `skill-eval` paths |

### 2.2 Plan 04 — `catalog_enabled + published_version_id` recall (pass)

| Claim | Result | Exact symbols / files |
|---|---|---|
| Live catalog projects only enabled + published | **pass** | `build_run_catalog_state` joins `AssistantSkillPackage.published_version_id` → `AssistantSkillVersion`, filters `catalog_enabled.is_(True)` and `version_source == "publish"` (`app.assistant.main_agent.inject_wiring`) |
| Snapshot purity | **pass** | `build_catalog_snapshot` / `evaluate_candidate_eligibility` in `app.assistant.main_agent.catalog` require `catalog_enabled` and matching version |
| Catalog toggle separate from publish | **pass** | `SkillPackageService.set_catalog_enabled` (requires published version; does not mutate versions) |
| Golden rollout respects flags | **pass** | `app.assistant.main_agent.rollout` plans/enables only after published digests + `catalog_enabled` / Profile `runtime_enabled` checks |
| Deterministic evaluation harness | **pass** | `app.assistant.main_agent.evaluation` + `backend/tests/test_main_agent_evaluation.py` (`scripted=True` default; live eval not default) |

### 2.3 Plan 05 — policy / budget / obligation (pass)

| Claim | Result | Exact symbols / files |
|---|---|---|
| Independent grant derivation | **pass** | `derive_effective_capability_grant`, `evaluate_authorization` (`app.assistant.policy.evaluator`); release gate before descriptor inspection |
| Hard release gate read-only | **pass** | `PLAN05_RELEASE_GATE_SIDE_EFFECTS = ("none","compute","read")` |
| Platform ceiling read-only | **pass** | `MAIN_AGENT_READ_ONLY_EFFECT_CEILING` in `app.assistant.main_agent.authorization` |
| Budget ledger | **pass** | `BudgetLedger`, `BudgetLedgerState`, `BudgetLedgerDispatchGuard` (`policy/budgets.py`, `policy/runtime.py`) |
| Obligation ledger | **pass** | `ObligationLedger`, `ObligationLedgerState`, `ObligationLedgerCompletionGuard` |
| Plan 08 v2 write admission additive | **pass** | `evaluate_authorization_v2` in `policy/write_admission.py`; V1 path remains default |

### 2.4 Plan 06/07 — durable Run / interrupt (pass)

| Claim | Result | Exact symbols / files |
|---|---|---|
| Run statuses include reconciliation | **pass** | `RUN_STATUS_*` in `app.assistant.run_service` includes `needs_reconciliation` |
| Stop vs ordinary result | **pass** | `ALLOWED_TRANSITIONS`: `(running|recovering→cancelling): stop`; `(cancelling→cancelled): cancel_finalizer`; **no** ordinary complete/fail from `cancelling` |
| Plan 08 settlement edge present | **pass** | `(cancelling→needs_reconciliation): call_settlement_unproven` |
| Interrupt resolve idempotency-first | **pass** | `DurableInterruptRepository.resolve_interrupt` locks Run then `resolution_request_id` before token consume (`workflow/durable/interrupts.py`) |
| Pause / resume | **pass** | `commit_durable_workflow_pause`, `execute_interrupt_resume`, `BudgetSuspensionStateV1` |
| Checkpoint codec retains v1–v3 | **pass** | `SUPPORTED_CHECKPOINT_SCHEMA_VERSIONS = {1,2,3}` |

### 2.5 Plan 08 — capability call ledger / golden writes (pass)

| Claim | Result | Exact symbols / files |
|---|---|---|
| Ledger mode freeze per Run | **pass** | `AssistantChatRun.capability_ledger_mode`; `freeze_capability_ledger_mode_for_run` (`capability_calls/release_admission.py`) |
| Golden write eligibility | **pass** | `is_golden_write_eligible`; Settings reject `write_mode=golden` without `ledger=enforced` + strong HMAC secret |
| Local transactional write | **pass** | `create_entry_local_transactional` / `EntryService.create_in_uow` (no commit; ledger-owned Session) |
| Aggregate / settlement | **pass** | `DurableCapabilityLedgerAggregate`, settlement join, reconciliation evidence immutability migration `d7e8f9a0b1c3` |
| Attempt lifecycle | **pass** | Migration `f2c3a4b5d6e7` constrained attempt UPDATE guard |

### 2.6 Plan 02B observation status (non-blocking)

| Item | Value |
|---|---|
| Status | **`complete`** (coordination only; **non-blocking** for Plan 09) |
| Flags | `PLAN_02B_OBSERVATION=conditional-pass`, `PLAN_02B_CLEANUP_APPROVED=yes`, `FULL_PLAN_02_COMPLETE=yes` |
| Evidence | `docs/superpowers/evidence/plan-02b-observation.md`, `plan-02b-final.md` |
| Implication | Evaluation must **not** import OpenClaw legacy execution or expose a production OpenClaw runtime path |

### 2.7 Stop-condition summary

| Gate | Result |
|---|---|
| Package parse/import/publish contracts reusable | **pass** |
| Catalog recall = `catalog_enabled + published_version_id` | **pass** |
| Policy/budget/obligation reconstructible | **pass** |
| Durable Run/interrupt CAS intact | **pass** |
| Capability call ledger + golden write gate present | **pass** |
| One Alembic head | **pass** (`d7e8f9a0b1c3`) |
| Authenticated assistant-config principal | **absent (expected)** — Plan 09 router stays unmounted in staging/production until one exists |

**Plan 09 Task 1 may begin** for lifecycle migration/API behind default-off mount. No prerequisite plan amendment required for package bytes/digest/import security or evaluation isolation contracts.

### Honest enablement gaps (do **not** auto-stop Plan 09)

1. Production Main Agent mode remains **`off`**; dual-wiring / live enablement gaps from Plans 06–08 still apply.
2. PostgreSQL two-session CAS/events/lease suites CI-gated — skipped here (`MINDATLAS_TEST_POSTGRES_URL` unset).
3. MinIO private Artifact live suites CI-gated — skipped.
4. Live compose golden path not run.
5. Local venv `langgraph`/`langchain` drift vs pins (same as Plans 05–08).
6. **No project-wide auth/RBAC** — Plan 09 HTTP surface must remain unmounted outside trusted test/dev until a real principal dependency lands (see §4).

---

## 3. Baseline test results

Fernet key generated per-process. PG/MinIO env vars unset.

### 3.1 Brief-required suites

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_assistant_config_service.py \
  backend/tests/test_assistant_config_service_more.py -q
# → 69 passed, 12 subtests passed
```

```bash
npm --prefix frontend ci
npm --prefix frontend run test -- --run \
  src/features/assistant-config/queries.test.tsx
# → 2 passed
npm --prefix frontend run build
# → tsc + vite build OK
```

### 3.2 Plan 01 / 04 contract suites

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_skill_api.py \
  backend/tests/test_agent_skill_package_io.py \
  backend/tests/test_main_agent_profile_api.py \
  backend/tests/test_main_agent_profile_service.py \
  backend/tests/test_main_agent_catalog.py \
  backend/tests/test_main_agent_evaluation.py \
  backend/tests/test_main_agent_authorization.py -q
# → 162 passed, 5 subtests passed
```

### 3.3 Durable / interrupt / ledger suites

```bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_policy_runtime.py \
  backend/tests/test_durable_run_streaming.py \
  backend/tests/test_durable_interrupt_api.py \
  backend/tests/test_capability_call_idempotency.py \
  backend/tests/test_capability_call_dispatcher.py \
  backend/tests/test_main_agent_golden_create_entry.py -q
# → 100 passed
```

### 3.4 Totals this Task 0

| Suite class | passed | failed | skipped |
|---|---:|---:|---:|
| assistant-config service | 69 (+12 subtests) | 0 | 0 |
| skill package / profile / catalog / eval / auth | 162 (+5 subtests) | 0 | 0 |
| policy / durable / interrupt / capability / golden | 100 | 0 | 0 |
| frontend queries | 2 | 0 | 0 |
| frontend build | OK | — | — |
| **Focused total** | **333** | **0** | **0** |

**Unexplained baseline failures:** none in focused suites.
**Honest skips not exercised here:** PG two-session, MinIO live, full backend suite, live compose.

---

## 4. API / auth conventions freeze

### 4.1 Response envelope

`app.common.responses.ApiResponse`:

- fields: `success: bool`, `code: int`, `message: str`, `data: Any | None`
- helpers: `ApiResponse.ok(...)`, `ApiResponse.fail(code, message, data=None)`

Capability domain errors use `safe_code` / bounded details (`app.assistant.capabilities.errors`); never raw secrets or unrestricted traces.

### 4.2 Authenticated principal dependency — **none**

| Surface | Auth dependency |
|---|---|
| `backend/app/assistant_config/router.py` (`/api/assistant-config/*` legacy tools/skills/workflows/agents) | **`Depends(get_db)` only** |
| `backend/app/assistant/skills/router.py` Plan 01 package/profile routers | **`Depends(get_db)` only**; router-level `dependencies=[]` |
| Project-wide FastAPI principal / operator role | **absent** — no `OperatorPrincipal`, `get_current_user`, or RBAC Depends for assistant-config |

Plan authorization decision therefore remains fail-closed:

1. All **new** Plan 09 routes must live under **one separately mountable parent router**.
2. Every mutation, resource-content read, Eval evidence read, and SSE stream requires a real server-verified principal once one exists.
3. Catalog enable/disable, non-safety waiver, system-package/Profile mutation require operator/admin role on a verified `OperatorPrincipal` value (not `isAdmin: bool`).
4. **Until that dependency exists:** production/staging keep the **entire Plan 09 router unmounted and absent from OpenAPI**. Trusted test/dev mounting only via explicit environment guard used by tests — never release evidence.
5. Do **not** substitute header booleans, loopback, Origin, CORS, or feature flags for auth.

### 4.3 Existing Plan 01 OpenAPI paths (mounted today)

Under `/api/assistant-config` (56 assistant-config paths total; skill/profile subset):

```text
GET|POST /skill-packages
POST     /skill-packages/import
GET      /skill-packages/{package_id}
PUT      /skill-packages/{package_id}/draft
POST     /skill-packages/{package_id}/publish
GET      /skill-packages/{package_id}/versions
GET      /skill-packages/{package_id}/versions/{version_id}
GET      /skill-packages/{package_id}/versions/{version_id}/export
GET      /skill-packages/{package_id}/versions/{version_id}/resources/{path}
GET      /main-agent-profiles/default
PUT      /main-agent-profiles/default/draft
POST     /main-agent-profiles/default/publish
GET      /main-agent-profiles/default/versions
```

**Absent today (Plan 09):** archive/unarchive, alias disable, catalog enable/disable with gate, import preview/apply modes, version diff/restore-draft, evaluation datasets/runs/gates, `skill-eval/*`, aggregate metadata PATCH with revision CAS.

### 4.4 Task 1 startup / OpenAPI / service tests (required)

Record exact proofs Task 1 must land (names normative intent):

| Test intent | Must prove |
|---|---|
| Staging/production mount default | Plan 09 parent router **not** in `app.routes` / OpenAPI when `APP_ENV` ∈ {`staging`,`production`} (or equivalent release env) **and** no verified principal dependency is configured |
| OpenAPI absence | `GET /openapi.json` has **zero** paths for Plan 09-only prefixes (archive, catalog enable with gate, import preview/apply, skill-eval, restore-draft, …) outside trusted test mount |
| Missing principal rejection | Service methods for privileged transitions reject missing principal (not router-only) |
| Fake principal rejection | Forged header / body / loopback / Origin / CORS / feature-flag “admin” does **not** authorize |
| Trusted test mount only | Explicit test env guard can mount router for contract tests; that path is **not** release evidence |
| Plan 01 paths remain | Existing create/import/draft/publish/export OpenAPI paths still present (unless intentionally versioned later) |

`backend/tests/test_agent_skill_api.py::test_openapi_exposes_exact_package_paths_once` and `test_main_agent_profile_api.py` OpenAPI tests are the baseline shape to extend — not replace with UI-only checks.

---

## 5. Frontend integration points freeze

### 5.1 App routes (`frontend/src/app/App.tsx`)

| Path | Page |
|---|---|
| `/settings/assistant-skills` | legacy `SkillSettings` |
| `/settings/assistant-tools` | `ToolSettings` |
| `/settings/assistant-targets` | `AssistantTargetsSettings` |
| `/settings/workflow-editor/:workflowId` | `WorkflowEditorPage` (full-bleed) |
| `/settings/agent-editor/:agentProfileId` | `AgentEditorPage` (full-bleed) |
| `/settings/system-ai-behaviors` | `SystemAiBehaviorsSettings` |
| redirects | `/settings/assistant-workflows` → targets; `/settings/assistant-agents` → targets |

**Absent:** Universal Skills list/editor, Profile editor, evaluation workbench routes (09C).

### 5.2 Settings navigation

- Primary hub: `frontend/src/features/settings/SettingsPage.tsx` cards include `assistant-tools`, `assistant-skills`, `assistant-targets`, OpenClaw, system AI behaviors, …
- Sidebar (`frontend/src/components/layout/Sidebar.tsx`): dashboard, assistant, entries, graph, calendar, settings only — no deep skill admin links.
- System setup also links to `/settings/assistant-skills`.

### 5.3 Query keys (`frontend/src/features/assistant-config/queries.ts`)

Canonical keys today:

- `['assistant-tools']`, `['system-tool-definitions', locale]`
- `['assistant-skills']`
- `['assistant-workflows']`, `['assistant-callable-workflows']`, `['assistant-workflow', id]`, `['assistant-workflow-versions', …]`
- `['assistant-agents']`, `['assistant-agent-profile', id]`, `['assistant-agent-versions', …]`
- `['assistant-target-folders']`
- `['assistant-system-behaviors']`

**Plan 09 should add new keys** (do not overload legacy `assistant-skills` for universal packages), e.g. `skill-packages`, `skill-package`, `skill-package-versions`, `skill-eval-*`, `main-agent-profiles` — exact names Task 6+ own; invalidation must not break legacy SkillManager.

### 5.4 Stores

| Store | Path | Role |
|---|---|---|
| `workflow-editor-store` | `features/assistant-config/stores/workflow-editor-store.ts` | Workflow graph editor state |
| `workflow-test-run-store` | `…/workflow-test-run-store.ts` | Workflow test run/trace (zustand) |
| `agent-test-run-store` | `…/agent-test-run-store.ts` | Agent test run/trace (zustand) |
| `chat-store` | `features/assistant/stores/chat-store.ts` | Production chat (do not reuse for Eval) |
| `app-store` | `frontend/src/stores/app-store.ts` | App shell |

### 5.5 Notifications / i18n / a11y / responsive

| Concern | Baseline |
|---|---|
| Notifications | `sonner` toasts (`toast.success/error/loading`); `<Toaster />` in `app/providers.tsx` |
| i18n | `frontend/src/locales/{en,zh}/common.json`; settings keys `pages.settings.assistantSkills*`, large `settings.skills.*` tree; **no** Universal Skills / Eval keys yet |
| A11y anchors | `aria-label` / `role` on folder drag handles, Copilot panel, workflow rails/canvases; `focus-visible:ring-*` form patterns on Agent editor |
| Responsive | Settings grids `grid-cols-1 md:grid-cols-2 xl:grid-cols-3`; section padding `p-5 sm:p-6` |
| Reusable version/test UI | `components/versioning/{TargetVersionPanel,PublishVersionDialog}.tsx`; `WorkflowTestRunPanel`; agent/workflow test stores — reuse patterns, not production chat |

Legacy `SkillSettings` / `SkillManager` remain the labeled legacy page during migration (Plan 10 cutover).

---

## 6. Script / write safety tripwire map (Task 4 matrix)

### 6.1 Existing execution surfaces (must not gain package-script execution)

| Path | Notes |
|---|---|
| `backend/app/assistant/workflow/code_executor.py` | `subprocess.run` for workflow Code Executor nodes (allowlisted runner); rejects `__import__` / `importlib` in source scan |
| `backend/app/assistant/workflow/code_executor_runners/python_runner.py` | Restricted `exec` with safe builtins |
| `backend/app/assistant/orchestration/__init__.py`, `tools/__init__.py` | Dynamic `__import__` for registered modules only |
| `backend/app/attachment/parser.py` | `importlib` for OCR optional deps |
| Frontend `AttachmentPreview` iframe | Attachment preview only — **not** package resource preview (Plan 09 resource preview must not execute scripts) |

**Plan 09 invariant:** package `scripts/` remain inert bytes; no new subprocess/dynamic-import/eval/Worker/iframe execution endpoint for packages.

### 6.2 Nested Workflow / Agent invoke paths

| Path | Symbol |
|---|---|
| Capability Gateway | `CapabilityGateway.execute` (`capabilities/gateway.py`) — nested frames for child calls |
| Workflow adapter | `capabilities/adapters/workflow.py` `execute` |
| Agent adapter | `capabilities/adapters/agent.py` `execute` |
| Nested resolution | `skills/resolution.py` `_collect_workflow_closure` / nested `workflow_call` |
| Nested agent idempotency | `capability_calls/idempotency.make_nested_agent_logical_call_key` |

Eval isolation must wrap **all** nested Gateway/dispatcher children; no raw Tool invoke or production Session escape hatch.

### 6.3 Production writer tripwire sites

| Class | Exact sites |
|---|---|
| Entry create/commit | `EntryService.create_in_uow`, `EntryService.create` (+ commit); HTTP `entry/router.create_entry`; tool `tools/entry_tools.create_entry`; golden `capability_calls/local_write.create_entry_local_transactional` |
| CapabilityCall ledger | `capability_calls/repository.create_or_verify_proposed` and attempt/result writers; `DurableCapabilityLedgerAggregate.commit_*` |
| Durable Run / events | `DurableRunRepository.commit_*`; `run_service.create_run` / `append_event`; `AssistantChatRun` / `AssistantChatRunEvent` |
| Memory | `AssistantMemoryService` / L1 `AssistantConversationL1Memory`, L2 `AssistantConversationSkillL2Memory`, workflow-call memory |
| Artifacts | `DurableArtifactService.commit_row`; object keys `assistant-runs/{run_id}/{sha256}` (`OBJECT_KEY_PREFIX="assistant-runs"`); private `ASSISTANT_ARTIFACT_BUCKET` |
| Outboxes / queues | `EntryIndexOutbox`, `AttachmentIndexOutbox`; artifact GC enqueue |
| Side-effect adapters | Production write/external adapters behind Gateway; OpenClaw capability adapter (must stay out of Eval) |

### 6.4 Task 4 test matrix (normative intent)

| Case | Expectation |
|---|---|
| Eval with `ASSISTANT_MAIN_AGENT_WRITE_MODE=off` | Write classes **simulate only**; no production Entry/ledger/Run/memory/event/Artifact rows |
| Eval with `ASSISTANT_MAIN_AGENT_WRITE_MODE=golden` | **Unchanged** Eval behavior; golden production path ignored for `owner_kind=test` |
| Nested workflow_call / agent under Eval | Same isolation wrapper; no production Session |
| Architecture import bans | `EvaluationRunner` must not depend on `EntryService`, production CapabilityCall repository writers, production Run/event/Artifact/memory writers, production write adapters |
| Runtime tripwire | If Eval reaches `EntryService.create`/commit, production ledger insert, production Run/L1/L2/event/Artifact write, production outbox/object prefix, or production write adapter → Eval Run `isolation_breach`, permanently gate-ineligible, safe error only |
| Secret canaries | Credential/Authorization/Cookie/signed URL/private identity never enter Eval events/Artifacts/results/gates/logs |
| `scripts/` resources | Stored/exported/previewed as non-executable; no run button/endpoint |

---

## 7. Migration IDs and heads

| Item | Value |
|---|---|
| Occupied ID `b4c5d6e7f8a9` | **yes** — `backend/alembic/versions/b4c5d6e7f8a9_add_openclaw_capability_item_catalog.py`; child `0c1d2e3f4a5b_allow_workflow_system_presets.py` |
| Sole head (Task 1 parent) | **`d7e8f9a0b1c3`** |
| Head file | `d7e8f9a0b1c3_protect_reconciliation_evidence.py` |
| Head parent | `f2c3a4b5d6e7` |
| Plan 08 chain | `7a3dac0ac2a8 → 984c07876856 → f2c3a4b5d6e7 → d7e8f9a0b1c3` |
| Task 1 action | `alembic revision -m "add skill package admin lifecycle"` from head `d7e8f9a0b1c3` (fresh ID) |
| Task 3 action | separate evaluation migration from Task 1 head; `alembic revision -m "add skill evaluation workbench"` (fresh ID) |
| 09A vs 09B | lifecycle migration must deploy/roll back **independently** of evaluation migration |

---

## 8. Release controls 09A–09D

### 8.1 Slice definitions

| Slice | Tasks | Backend | UI | Catalog/runtime impact |
|---|---|---|---|---|
| **09A** | 0–2 | Aggregate lifecycle + safe import behind **default-off / unmounted** Plan 09 router | none required | no live Catalog change |
| **09B** | 3–5 | Evaluation isolation + datasets + gates; scripted eval CI gate | none required | publish of **enabled** aggregates requires matching gate even in `observe` |
| **09C** | 6–7 | same contracts | Universal Skills + Profile + workbench routes; legacy pages remain | still feature/mount gated |
| **09D** | 8–9 | gate mode → `enforce` after all enabled native packages/Profiles have fresh matching evidence | enforcement UX | observe no longer operational dependency |

### 8.2 Flags / modes (to introduce; absent at freeze)

| Control | Default at introduce | Notes |
|---|---|---|
| Plan 09 router mount / trusted-dev guard | **unmounted** outside test/dev | not a substitute for principal auth |
| Publish gate mode | start `observe` (09B); exit requires `enforce` (09D) | examples/tests must not depend on observe for exit |
| Eval worker process | absent until 09B | `python -m app.assistant.evaluation.worker`; compatible build/contract checks |
| Production flags unchanged | main-agent `off`, write `off`, ledger `legacy_read_only`, interrupts `false` | Eval ignores golden write mode |

Exact env var names are Task 1/3 ownership; Task 0 locks **behavior**, not speculative flag strings.

### 8.3 Enabled-aggregate publish invariant (both gate modes)

```text
Skill package:      catalog_enabled=true  => every published_version_id advance requires a fresh matching gate
Main Agent Profile: runtime_enabled=true  => every published_version_id advance requires a fresh matching gate
```

| Gate mode / aggregate state | Publish may advance pointer without `gateId`? | Enable live visibility? |
|---|---:|---:|
| `observe`, Skill `catalog_enabled=false` / Profile `runtime_enabled=false` | Yes, only recorded non-live bootstrap/migration publish | No; enable still needs fresh matching gate |
| `observe`, already live-enabled | **No** | Already live; ungated pointer advance fails atomically |
| `enforce`, native package/Profile | **No** | No without exact current-version promotion gate |
| legacy shadow sync, still live-disabled | Yes, locked Plan 01 compatibility service only | No; Plan 10 cutover gate |

### 8.4 Compatible worker / rollback

| Topic | Rule |
|---|---|
| Production assistant-worker | Continues Plan 06–08 claim/lease/CAS; must **not** claim Eval runs |
| Eval worker | Separate table/repository/`RuntimeIsolationContext`; fails admission if unavailable — never falls back to production execution |
| 09A rollback | Reverse lifecycle migration only; no evaluation schema dependency |
| 09B rollback | Reverse evaluation migration; leave lifecycle intact; gate mode back to non-enforce |
| Router rollback | Unmount Plan 09 parent router → OpenAPI/UI invisible again |
| Auth prerequisite | M4 release incomplete until real principal/RBAC guards Plan 09 router |

### 8.5 Invisible surface by slice

| Slice | Invisible / disabled |
|---|---|
| 09A | Eval APIs/UI; Universal Skills UI; production mount of new admin routes |
| 09B | Universal Skills UI; enforce mode; live eval-by-default |
| 09C | enforce mode until 09D; legacy SkillSettings still available |
| pre-auth | entire Plan 09 router unmounted in staging/production regardless of feature completeness |

---

## 9. Plan corrections

**None.** Plan text already records:

- `b4c5d6e7f8a9` occupied;
- Task 1 generates lifecycle migration from real post-Plan-08 head;
- principal dependency expected absent → fail-closed unmounted router;
- Plan 02B non-blocking.

No Plan 01 package security amendment required; evaluation isolation can reuse runtime contracts without bypass.

---

## 10. Verdict

```text
PLAN_09_TASK0_BASELINE=pass
SOLE_ALEMBIC_HEAD=d7e8f9a0b1c3
TASK1_MIGRATION_PARENT=d7e8f9a0b1c3
B4C5D6E7F8A9_OCCUPIED=yes
AUTH_PRINCIPAL_DEPENDENCY=none
PLAN09_ROUTER_MOUNT=unmounted_required_for_staging_production
PLAN_02B_STATUS=complete (non-blocking)
FOCUSED_TESTS_PASSED=333
FOCUSED_TESTS_FAILED=0
```

**Plan 09 Task 1 may begin** (lifecycle migration + admin APIs behind default-off/unmounted router and service-level principal rejection).

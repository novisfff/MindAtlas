# MindAtlas AI Runtime Migration and Legacy Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. This plan deliberately spans a reversible cutover release and a later destructive-cleanup release. Never combine the destructive database step with the first 100% traffic cutover.

**Goal:** Migrate every production built-in/custom Skill, alias, Main Agent baseline, L2 memory namespace, and eligible HITL entrypoint to the universal Skill/Main Agent runtime; prove quality and operational stability through shadow/canary/full rollout; then remove the legacy Router, single-Skill Supervisor, single-target Skill model/APIs/UI, blocking HITL path, compatibility adapter, and obsolete schema without losing audit/history.

**Architecture:** Phase A is additive and reversible: create explicit migration/rollout evidence, promote frozen native packages, backfill stable package IDs plus nonempty memory namespaces into the existing L2 rows, execute side-effect-free shadow comparisons through Plan 09's independent Eval Run/worker rather than a second active Chat Run, and route deterministic cohorts while each production Chat Run freezes its runtime. After 100% Main Agent traffic meets locked data/quality/safety/soak gates, Phase B removes legacy code in one release and drops legacy schema in a later maintenance migration. Automatic fallback is allowed only during API admission before any durable `AssistantChatRun` row is inserted; after insertion that exact Run must recover, fail, cancel, or reconcile and no second runtime is spawned.

**Prerequisites:** Plans 01–09 contract/release handoffs required below are merged and verified, including one read-only and one approved local-write golden path, durable recovery/HITL, call ledger/reconciliation, package admin, fixed evaluation datasets, publish gates, and a real server-side assistant-config principal/operator guard. Plan 02B status is recorded at Task 0; its shared-only OpenClaw exit is mandatory before Deploy B1 removes overlapping legacy owners, even if earlier Deploy-A inventory/migration work can proceed independently.

---

## Position and Hard Boundary

This is Plan 10 of 10 and milestone M5.

Implemented here:

- Complete inventory and deterministic migration of system/custom legacy Skills and aliases.
- Promotion of `general_chat` semantics into the default Main Agent Profile.
- Stable package-ID L2 backfill, collision merge, compatibility reads, and final name-column removal.
- Side-effect-safe Legacy/Main shadow comparison and rollout telemetry.
- Deterministic shadow/canary/main assignment with independent write rollout.
- Reversible traffic cutover, soak/quality gates, and rollback drills.
- Removal of legacy routing/supervisor/single-target config/admin and blocking HITL runtime after all entrypoints drain or migrate.
- Destructive schema cleanup only after backups, zero-blocker audit, and a separate release gate.
- Dependency/config/docs/test cleanup based on an import/usage audit.

Not implemented here:

- No redesign of Workflow DAGs, Tools, or Agents that already execute through the shared Capability Runtime.
- No automatic fuzzy mapping of ambiguous names or memories.
- No double execution of real writes in shadow mode.
- No fallback after a durable Chat Run row exists—even before Provider I/O. Worker/Manifest/model/config failures after insertion remain on that Run's recover/fail/cancel/reconcile path.
- No deletion of immutable universal Skill/Run/Call/evaluation history.
- No opportunistic LangGraph/LangChain upgrade merely because the legacy Supervisor is removed.
- No claim that the destructive database release can be rolled back without restoring compatible schema/data.

### Inherited ship blockers

“Plans 01–09 merged” is not sufficient evidence. Task 0 records the exact upstream fixed vectors and stops before any production shadow/read canary percentage exceeds zero unless all applicable gates pass:

- Plan 06 admission chooses final runtime before `AssistantChatRun` insertion, permits at most one nonterminal production Run per conversation scope, and has passing lease/recovery/SSE/memory CAS vectors;
- Plan 07 waiting/resolve uses its single Run-first interrupt CAS with durable decision-channel coverage for every admitted entrypoint;
- Plan 08 has passing independent write-grant, call-owned approval, `cancel x started-call` settlement, idempotency, and reconciliation vectors; a write canary also requires the exact approved golden path and compatible worker;
- Plan 09 is M4 release-complete, not merely code-complete behind an unmounted router: gate mode is `enforce`, enabled Skill/Profile pointer advances require matching gate-use evidence even in the former observe regression, Eval isolation tripwires pass, and every mounted admin/diagnostic route has the real principal/operator guard;
- the active package/Profile/alias/gate closure and required worker/runtime/build/schema versions match the rollout candidate under lock.

Failure of a read-path prerequisite blocks shadow/read canary activation. Failure of a write/settlement prerequisite additionally blocks every write canary. Any missing Plan 09 authorization guard blocks Plan 10 entirely because migration, diagnostics, Gate use, and cleanup approvals are privileged operations. These are service-enforced admission conditions and cleanup-gate inputs, not checklist prose or operator assertions.

---

## Two-Phase, Three-Deploy Rule

“Release A/B” describes reversible versus destructive phases; it does not permit code deletion and schema deletion in one deployment. Production uses three separately observable deploy gates.

### Deploy A — migrate and cut over, legacy intact

- Add migration/assignment/comparison/archive schema.
- Freeze inventory and mappings.
- Convert/promote all packages and Profile.
- Backfill/verify L2 and approval history.
- Shadow, read canary, write canary, then 100% Main Agent.
- Keep legacy code/table/API disabled but deployable for the full soak window.
- Practice rollback before any destructive cleanup.

### Deploy B1 — remove legacy code/UI, keep legacy tables

1. Deploy code that has no runtime/config/UI dependency on legacy tables or modules while the tables still exist.
2. Verify production import/query logs and compatibility probes show zero legacy access.

### Deploy B2 — destructive schema maintenance

1. Re-evaluate all gates at maintenance start.
2. Take/verify backups and export audit records.
3. Apply the destructive migration while incompatible API/workers are stopped.
4. Start only the B1-or-newer binary set.
5. Do not redeploy Deploy-A/older binaries against the cleaned schema.

Deploy B1 begins only after every locked cleanup gate below passes. Deploy B2 begins only after the additional B1 zero-access window and maintenance preflight pass. A calendar deadline is not a substitute for evidence.

---

## Additive Migration Schema and Evidence

Do not preselect an additive revision ID. Task 0 records the real post-Plan-09 Alembic head and Task 1 generates a fresh unique revision with `alembic revision -m "add ai runtime migration audit"`.

### `assistant_runtime_migration_item`

- UUID ID.
- Subject kind `skill | profile | alias | l2_memory | approval | entrypoint`.
- Stable source type/ID/name/digest and target type/ID/version/digest.
- State `discovered | mapped | migrated | verified | blocked | archived`.
- Reason code, bounded safe evidence JSON, source/target revision, attempt count.
- Created/updated/verified timestamps and actor/build revision.
- Unique stable source identity; state changes are audited by append-only event rows.

### `assistant_runtime_migration_event`

- Migration-item FK, monotonic revision, previous/new state, evidence digest, safe details, actor/build/time.
- Append-only trigger.

### `assistant_runtime_migration_batch`

- UUID ID and command kind `inventory | package | l2 | approval | verify`.
- Source snapshot digest, configuration digest, build/schema revision, environment/database fingerprint.
- Status `prepared | running | completed | failed | cancelled` and `state_revision`.
- Bounded batch size, stable resume cursor, processed/succeeded/blocked/failed counts.
- Started/completed actor/time, safe report Artifact ID and report digest.
- A resume requires the same command/source/config/build/schema digests; drift creates a new batch and leaves the old one immutable/auditable.

Batch rows never contain raw facts, prompts, approval payloads, or credentials.

### `assistant_runtime_rollout_revision`

- Immutable revision label/UUID and complete normalized rollout configuration.
- Runtime mode, explicit shadow-eligible scope/percent, read canary percent, write mode/percent, eligible package/Profile/gate-use closure digest, and config origin `native | plan04_compat`.
- Build/runtime/policy/worker contract versions and non-secret cohort-salt fingerprint.
- Metric definition/window IDs, approval/evidence Artifact IDs, actor/reason/timestamps.
- Revision content never changes after insert. A changed percentage/mode/eligible closure requires a new row; environment values alone cannot silently redefine an existing label. `plan04_compat` fixes production paired-shadow eligibility to zero and cannot be activated as canary/main.

### `assistant_runtime_rollout_event` and control pointer

- Rollout Event is append-only with revision FK, action `prepared | activated | superseded | rolled_back`, previous active revision, actor/reason/evidence/time, and monotonic control revision.
- `assistant_runtime_rollout_control` is a singleton row containing `active_rollout_revision_id` and `state_revision`.
- Activation locks the control row, verifies candidate/evidence and expected control revision, appends activation/supersession events, advances the pointer, and increments `state_revision` in one transaction.
- Exactly one revision is active because there is one pointer, not because immutable revision rows change status.
- Configuration admission must match the active durable revision and control revision.

### `assistant_runtime_rollout_assignment`

- Use the current authoritative conversation ownership scope. At plan-authoring time `assistant_conversation` has no user/tenant owner column, so the fallback affinity key is `conversation_id`; if Plans 04–09 add an authenticated principal/tenant scope, use that exact frozen scope and retain `conversation_id` as the conversation affinity. Do not invent an unauthenticated `user_id`.
- FK to exact `assistant_runtime_rollout_revision`, cohort, deterministic `assigned_runtime_kind` (`legacy | main_agent`), and assigned write mode.
- Assignment reason `hash | staff | explicit_override | rollback` and non-secret cohort-key digest.
- Created-at only; unique scope + rollout revision.
- A new rollout percentage creates/uses a new revision; it never mutates historical assignment. The assignment is a stable cohort decision, not a per-request fallback record.

### `assistant_runtime_admission_fallback_event`

- Append-only per-request evidence with unique request ID, rollout revision/assignment, `candidate_runtime_kind=main_agent`, `selected_runtime_kind=legacy`, reason `preinsert_fallback`, safe admission-failure/evidence digest, resulting Legacy Run ID, actor/principal scope digest, build/schema/runtime versions, and timestamp.
- Created only after the Main Agent admission preflight fails and before any Chat Run exists for that request. Pre-generate the Legacy Run ID and insert the fallback event plus exactly one Legacy `AssistantChatRun` in the same transaction; a deferred FK or equivalent repository invariant proves the event's resulting Run exists and has `runtime_kind=legacy`.
- Identical request retry returns the same fallback event/Run. Altered reuse conflicts. A fallback event can never reference a Main Agent Run, coexist with a Main Agent Run for the same request, or be appended after any Chat Run insert.
- A stable assignment may serve many requests; a transient pre-insert failure does not rewrite the conversation's assignment. A later request under the same revision reevaluates admission and may create a Main Agent Run if preflight passes.

### `assistant_runtime_shadow_comparison`

- Pair ID, exact legacy production `AssistantChatRun` ID and isolated Plan 09 Eval Run ID; the shadow side is never an `AssistantChatRun`.
- Exact rollout revision/assignment, shadow-eligibility decision, input/context/fixture digests, and catalog/profile/model/runtime/build revisions.
- Comparable intent class and whether write simulation was required.
- Legacy/new Skill selection, Capability path, completion/stop/error summaries.
- Quality assertion snapshot, rounds/calls/tokens/latency/cost estimates.
- Reviewer/result state and timestamps.
- No raw credentials, secret config, unrestricted prompts, or shadow user-visible event.
- Shadow comparison/evidence must not block Plan 06 conversation privacy deletion. The deletion service first expires/deletes the paired Eval Run/input/events/Artifacts through evaluation cleanup and enqueues private object GC, then removes the comparison and production conversation/Run. Rollout gates retain only aggregate safe metric/digest evidence, not the deleted user's content or IDs.

### Runtime-shadow Eval extension

Plan 10 chooses the Plan 09 Eval aggregate/worker for shadow execution. It does **not** weaken Plan 06's active-production-Run uniqueness and does not introduce a third shadow runner:

- add/reuse Eval purpose `admin_evaluation | runtime_shadow`; `runtime_shadow` is always `gate_eligible=false` because online shadow traffic is rollout evidence, not a publish gate dataset;
- a `runtime_shadow` Eval Run owns synthetic Eval CapabilityCalls, evaluation-only events/Artifacts, leases/recovery, isolation context, exact Main Agent subject/Profile/Catalog/Manifest/model/runtime/build digests, and a short-lived internal input snapshot;
- `assistant_runtime_shadow_comparison` is the only cross-namespace pairing row. It references the production Run and Eval Run; Eval tables retain no production conversation/message/L1/L2/CapabilityCall FK;
- build `RuntimeShadowInputSnapshot` before enqueue from the exact authorized production message/context prefix. Store its bounded payload only in the private evaluation namespace, bind source IDs/digests/principal scope/policy/expiry, exclude credentials/raw headers/signed URLs, and never copy raw content into comparison rows, events, metrics, logs, or gate evidence;
- the Eval worker consumes only that frozen snapshot and never queries a live production Conversation/Message Session during shadow execution. Retention/privacy deletion of the source conversation also expires/deletes the paired shadow payload through the evaluation cleanup path;
- enable production shadow only where the deployment's approved data-processing policy permits the additional model evaluation. Otherwise shadow remains staff/fixture-only; a rollout flag is not privacy authorization.

Every `AssistantChatRun` remains a production Chat Run and continues to obey Plan 06's single-nonterminal-Run-per-conversation constraint; Plan 10 adds no shadow `execution_mode` column or partial-index exception. A production Run and an Eval shadow may coexist; two production nonterminal Chat Runs may not.

### `assistant_legacy_approval_archive`

- Immutable copy of terminal legacy approval/input request and resolution audit fields.
- Source row/run/conversation IDs, safe payload digest, status/decision/timestamps, migration evidence.
- No continuation token and no claim that it is resumable.
- Append-only; used only for retention/audit after the old table is dropped.

### `assistant_runtime_cleanup_gate`

- Append-only gate kind `deploy_b1 | deploy_b2`, decision `passed | failed`, schema/build/runtime revision, actor/reason/time.
- Exact inventory, migration batch, rollout revision, metric window, backup/restore drill, legacy-access window, archive-count, and reconciliation evidence digests.
- Snapshot counts for nonterminal production/Eval shadow Runs, pending approvals, null/invalid/split L2 namespace triples, package/alias/entrypoint blockers, unresolved calls, shadow visibility/privacy violations, and legacy reads/writes/invocations.
- Expiry and invalidation inputs. Source/config/schema/build/data-count drift invalidates the gate.
- B2 preflight requires a current passing B2 gate plus deliberate maintenance acknowledgment, then recomputes every hard count under maintenance lock. The row is evidence, not a bypass.

For durable shadow recovery, use the Plan 09 Eval lease/CAS/event/Artifact contracts. Production chat history, active/latest Run lookup, SSE replay, stop/resume, Artifact fetch, Message/L1/L2/terminal finalizers, conversation deletion, and normal search/list APIs must reject Eval Run IDs and evaluation object keys. Shadow comparison/detail APIs are a separate privileged diagnostic surface behind the Plan 09 principal/operator boundary; if that guard is absent, they remain unmounted and cannot count as rollout evidence.

---

## Locked Skill and Profile Migration Map

### Built-ins, in order

1. **Default Main Agent Profile** — migrate `general_chat` prompt/model/control behavior into a native published Profile; `general_chat` is reserved migration provenance, not a production Skill selected by the new runtime.
2. **`quick-stats`** (legacy source/alias `quick_stats`) — first read-only system package and canary path.
3. **`periodic-review`** (legacy source/alias `periodic_review`) — remaining read/report system package(s), retaining exact published Workflow bindings.
4. **`smart-capture`** (legacy source/alias `smart_capture`) — migrate read/propose/HITL first, then the Plan 08 approved `create_entry` branch. Before legacy deletion, each currently supported update/merge/relation branch must either be migrated separately through the same ledger/approval/idempotency/evaluation gates or be explicitly retired by an approved product decision with user-facing compatibility handling; it may not disappear silently.
5. **Every enabled custom legacy Skill** — convert its one legacy Workflow/Agent target into one binding inside a universal package, preserving portable instructions/examples and aliases; universal editing may add more bindings later.
6. Disabled/custom historical Skills — archive or migrate according to explicit inventory policy; never silently enable them.

System registry discovery must determine the actual current set; the names above are the known plan-writing baseline, not permission to ignore newly added production Skills.

### Per-Skill promotion protocol

For each source:

1. lock/read legacy row and published target versions;
2. verify Plan 01 shadow package source digest and alias ownership;
3. generate a full native draft with explicit instructions, resources, bindings, policy, budgets, and completion contract;
4. validate side-effect/execution classification and exact frozen targets;
5. run candidate-specific and shared Plan 09 datasets, including negative/ambiguous cases;
6. publish with a qualifying Plan 09 gate and verify the transactional `skill_publish|profile_publish` gate-use row;
7. mark package `cutover`/native so Legacy Adapter can no longer mutate it;
8. catalog/Profile-enable only in the intended rollout cohort with the exact current-version promotion gate-use row;
9. compare shadow/canary behavior and record migration verification;
10. keep the source row unchanged until Deploy B2.

No package is declared migrated merely because a shadow row exists.

### Migration item transition rules

~~~text
discovered -> mapped | blocked | archived
mapped     -> migrated | blocked
migrated   -> verified | blocked
blocked    -> mapped | archived       # explicit actor/reason and new evidence
verified   -> blocked                  # only detected source/target drift; blocks rollout
archived   -> blocked                  # only if later inventory proves it active/referenced
~~~

Every transition locks the item, checks its `source_revision/source_digest` and expected state revision, appends one event, and advances the item in one transaction. `verified` is never set by the mutation command that creates the target: an independent verification pass must compare source mapping, target digest, evaluation gate, aliases, and runtime resolution. Bulk commands continue past item-level blockers but return nonzero/blocked summary; rollout activation refuses any in-scope blocker.

### Aliases

- Canonical and known legacy names become reserved aliases on one package.
- Normalize with the same Plan 01 lookup function; no local migration-only normalization.
- `general_chat` resolves only in the temporary legacy/profile bridge and is not exposed as a Skill Capability owner.
- Duplicate/ambiguous aliases block that item and the destructive gate.
- Disabled aliases remain reserved; names are never reassigned to a different stable package.
- Runtime Manifest/memory/authorization uses package/version UUIDs, never an alias string.

---

## L2 Namespace Migration

Plan 06 already added nullable `skill_package_id` and `memory_namespace` while retaining legacy `skill_name`. Native identity is the exact triple `(conversation_id, skill_package_id, memory_namespace)`, with a nonempty normalized namespace and default `default`. Phase A backfills the same rows in place so legacy name lookup and new package-ID-plus-namespace lookup observe one fact set during rollback eligibility.

### Deterministic mapping precedence

1. direct `assistant_skill_package.legacy_skill_id`/migration record mapping;
2. exact normalized canonical/legacy alias unique match;
3. checked-in system migration map with matching source digest;
4. otherwise `blocked`; never fuzzy-match by display text, description, or embedding.

### Collision handling

Multiple old `(conversation_id, skill_name)` rows may map to one package. Because Legacy has no namespace field, a backfilled legacy row uses `memory_namespace='default'` unless an exact checked-in source contract supplies another nonempty normalized namespace; it never writes null for a package-backed row.

For each exact target triple `(conversation_id, skill_package_id, memory_namespace)`:

- archive every original row payload/digest as migration evidence first;
- order sources deterministically by created time then UUID;
- normalize and stable-deduplicate facts while preserving first evidence order;
- retain source row/version/digest provenance in the new fact representation or migration archive;
- if facts exceed the runtime bound, use the existing deterministic memory compaction contract and retain the full originals in migration evidence;
- write/merge through one transaction and Plan 06's unique `(conversation_id, skill_package_id, memory_namespace)` constraint;
- verify repeat execution produces the same digest and no additional facts.

### Compatibility window

- Legacy reads resolve `skill_name` to package ID plus the exact mapped namespace (normally `default`) and prefer that package-backed triple, falling back only for an unmapped migration blocker.
- Legacy writes and Main Agent writes update the same package/namespace row and retain canonical `skill_name` only as a compatibility column.
- `last_applied_run_id` prevents terminal memory replay.
- Re-run the resumable backfill until the delta is zero before 100% cutover.
- Deploy B2 requires every active L2 row to have one verified package ID, one nonempty normalized `memory_namespace`, and zero blockers; then enforce both columns non-null/normalized, retain the three-column unique index, and drop only `skill_name` plus the legacy null-package name index.

Do not “solve” an unmapped memory by deleting it. Resolve/map it explicitly or keep Deploy B2 blocked.

---

## Legacy HITL Drain and Entrypoint Migration

Plan 07 replaced Main Agent blocking waits, but other current entrypoints must be audited before deleting `HumanLoopRuntime`.

1. Enumerate every production/test/OpenClaw/standalone Workflow/Agent path that can reach a human node.
2. Main Agent and any run-backed production path use durable interrupts.
3. Test workbench uses Plan 09 simulated/evaluation interrupts.
4. An entrypoint without an authenticated durable decision channel must classify human-interrupt Workflows as unavailable/`unsupported_interrupt`; it may not fall back to blocking polling.
5. Disable creation of new legacy approvals only after those rules are live.
6. Allow active legacy Runs to finish; explicitly reject/cancel/expire remaining pending approvals according to existing user semantics.
7. Require zero active legacy Runs and zero pending `AssistantHumanApproval` rows.
8. Copy terminal legacy rows to the immutable archive and verify counts/digests.
9. Remove coordinator/proxy/polling code; drop the old table only in the destructive migration.

There is no safe generic conversion of a pending legacy row into a resumable interrupt because it lacks the frozen durable Checkpoint/frame. Never fabricate one.

---

## Shadow Contract

Legacy remains the only user-visible production Chat runtime in shadow mode. The paired Main Agent execution is a Plan 09 `runtime_shadow` Eval Run, not an `AssistantChatRun`.

- Create an isolated, gate-ineligible Main Agent Eval shadow with the frozen authorized `RuntimeShadowInputSnapshot`/digest.
- Do not append production assistant messages, user-visible SSE, L1/L2, terminal memory, or normal conversation Artifacts.
- Read/compute Capabilities may execute under a shadow-specific read grant and normal budgets.
- `draft`, local write, external write, approval, and input paths are planned/simulated through the Plan 09 isolation adapter; they never reach production adapters or prompt the user twice.
- Do not run legacy and new real writes for the same request.
- Pair the production Legacy Run and Eval Run outcomes in `assistant_runtime_shadow_comparison`; a shadow failure never changes the legacy response or Run identity.
- Apply independent concurrency/cost limits so shadow traffic cannot starve production.

Compare injection, visible owner-qualified Capability set, call sequence, completion obligations, final task assertions, stop/error, latency, tokens, and safety decisions. Natural-language text equality is not a success metric by itself.

---

## Rollout Assignment and Run Freezing

Configuration during Deploy A:

~~~text
ASSISTANT_RUNTIME_MODE=legacy|shadow|canary|main
ASSISTANT_RUNTIME_ROLLOUT_REVISION=<immutable label>
ASSISTANT_RUNTIME_CANARY_PERCENT=0..100
ASSISTANT_RUNTIME_COHORT_SALT=<secret/stable deployment value>
ASSISTANT_MAIN_AGENT_WRITE_MODE=off|golden
ASSISTANT_MAIN_AGENT_WRITE_CANARY_PERCENT=0..100
~~~

`ASSISTANT_RUNTIME_MODE` becomes the single Deploy-A traffic-routing source. The earlier Plan 04 `ASSISTANT_MAIN_AGENT_MODE=off|shadow|read_only` is accepted only by this exact temporary compatibility parser when the new variable is absent:

| Legacy value | Parsed Deploy-A mode | Additional locked behavior |
|---|---|---|
| absent | `legacy` | default; no shadow Eval scheduling |
| `off` | `legacy` | Plan 04 explicit evaluation remains disabled |
| `shadow` | `shadow` | compatibility origin is recorded; production response remains Legacy and only the existing explicit staff/fixture evaluation entry is eligible. It does **not** schedule paired production shadow traffic |
| `read_only` | startup error `explicit_runtime_mode_required` | operator must prepare/activate an explicit `canary` or `main` rollout revision; never silently map to user-visible Main Agent traffic or 100% |

If both variables are present, startup fails even when their strings appear equivalent. There is no safe generic “consistent dual config” because Plan 04 `shadow` means explicit evaluation only while Plan 10 `shadow` may schedule paired Eval jobs under an active rollout revision. The deprecation diagnostic reports names/source only, never secret/salt values. Remove the old variable from application/env/Compose examples before activating any non-legacy rollout revision.

New `shadow` mode always leaves the user-visible production Run on Legacy and schedules a separate Plan 09 Eval shadow only for the explicit scope/percentage encoded in the active durable rollout revision. A compatibility-mapped old `shadow` value has paired-shadow eligibility fixed to zero. Never let environment values outside the active durable revision silently widen shadow/canary traffic.

- Default remains `legacy` until migration gates allow shadow.
- Assignment is deterministic from authorized scope ID + stable salt + rollout revision; never from prompt content or model choice.
- Staff/explicit overrides are audited.
- A production Chat Run freezes `runtime_kind`, Profile/model/Skill/Manifest versions, write eligibility, and rollout assignment at creation. A paired Eval shadow separately freezes `runtime_shadow` purpose and its exact subject/input/runtime evidence. Percentage/config changes affect only future production/Eval Runs.
- Existing legacy Runs finish on legacy; existing Main Agent Runs finish/recover on Main Agent.
- Read canary and write canary are separate. Increasing read traffic never implicitly enables writes.

Suggested gates:

1. internal/offline evaluation;
2. staff shadow;
3. read-only production shadow;
4. read-only canary `1% -> 5% -> 25% -> 50% -> 100%`;
5. golden write staff, then an independently approved bounded canary;
6. all migrated supported paths on Main Agent;
7. full soak with legacy disabled but intact.

Each step requires a new rollout revision, recorded decision, metric window, and rollback drill result. Percentages may be adjusted for actual scale, but no step may skip the same evidence classes.

---

## Locked Fallback and Rollback Boundary

### Automatic per-request fallback

Automatic fallback is an API-admission decision, not a Run transition. Resolve assignment candidate, Profile/Catalog/Manifest/model/probe/policy/build evidence, compatible-worker heartbeat, and every validation allowed to cause fallback **before** `AssistantChatRun` insertion. The service may choose Legacy instead of the candidate Main Agent only if all are still true:

- no `AssistantChatRun` row of either runtime has been inserted for the request;
- initialization/Manifest/model capability validation failed in the pre-insert admission phase;
- no CapabilityCall Attempt was dispatched;
- no interrupt was created;
- no user-visible content/event was emitted;
- no business/external side-effect boundary was crossed.

Keep the deterministic rollout assignment unchanged. Append one `assistant_runtime_admission_fallback_event` containing `candidate_runtime_kind=main_agent`, `selected_runtime_kind=legacy`, reason `preinsert_fallback`, and the safe failure/evidence digest, and insert exactly one Legacy Run in the same transaction. No Main Agent Run row or Main Agent durable child exists in this path.

Once any Main Agent `AssistantChatRun` row is inserted/queued, fallback is permanently closed even if worker claim, Manifest reconstruction, model/probe/config drift, Provider construction, or another later validation fails before external I/O. That same Run must recover, fail explicitly, cancel, or enter reconciliation; it cannot invoke the legacy Router/Supervisor or create/remap to a second Legacy Run. Production APIs/SSE retain the original Run ID and never publish a fallback remapping event.

### Deploy-A rollout rollback before Deploy B1

- Set a new rollout revision selecting legacy for future Runs.
- Do not mutate runtime identity of in-flight Runs.
- Keep Main Agent workers alive until their active/waiting Runs reach terminal/reconciled states.
- Because L2 rows are shared/backfilled in place, legacy reads remain current.
- Write calls that crossed `side_effect_started_at` stay on their ledger/reconciliation path.

### After destructive cleanup

There is no instant config rollback to legacy. Recovery is a forward fix or coordinated restore of database snapshot **and** matching Deploy-A application images. A downgrade migration that recreates empty tables is not a data rollback. This limitation must be present in the change record and maintenance approval.

Keep a Main Agent kill switch after cleanup, but its safe action is to stop accepting/queueing AI Runs or restrict capabilities—not route to deleted legacy code.

---

## Quality, Safety, and Destructive Gates

### Offline/publish gates

All Plan 04/09 thresholds continue to apply:

- appropriate Skill recall Top-8 ≥ 0.90;
- false injection ≤ 0.05;
- direct-answer/no-Skill accuracy ≥ 0.90;
- acceptable Capability path ≥ 0.85;
- completion success no worse than legacy by more than 0.02;
- unauthorized Capability calls = 0.

### Online rollout gates

For comparable eligible Runs, using locked metric definitions and confidence/sample reporting:

- Main Agent task completion is no worse than legacy by more than 2 percentage points.
- User-visible failure rate increases by at most 1 percentage point.
- p95 end-to-end latency is at most 1.5x the comparable legacy p95.
- Mean normalized model tokens are at most 1.35x legacy unless an explicit non-safety exception is approved.
- Unauthorized calls, real shadow writes, Eval-shadow visibility/privacy leaks, duplicate local/external writes, and false completion with obligations are exactly 0.
- No unresolved `unknown`/`needs_reconciliation` call exists at the cleanup gate.
- No open Sev-1/Sev-2 AI-runtime incident attributable to the cutover.
- Cancellation, resume, SSE replay, worker recovery, and L1/L2 idempotency dashboards stay within their tested invariants.

### Minimum cleanup evidence

- 100% eligible traffic on Main Agent for at least 14 consecutive calendar days.
- At least 100 eligible production Main Agent Runs in the stable window.
- If the golden write path is enabled, at least 20 approved/rejected/cancelled HITL/write-path cases combined, including recovery drills; zero duplicate writes.
- Manual review of a stratified sample plus the fixed offline datasets.
- Zero legacy Run creation, Router invocation, config mutation, blocking waiter, or legacy-table read/write for the final 7 days.

Low traffic is a reason to retain legacy longer, not to silently lower safety gates. A recorded governance exception may replace only non-safety sample/latency/token gates with additional fixed offline/staging evidence; zero unauthorized/duplicate/real-shadow-write/unresolved-reconciliation and data-integrity gates are never waivable.

### Data cleanup gate

- Every enabled legacy Skill has one verified native package/Profile outcome.
- Every currently supported legacy Capability branch has a verified new-runtime equivalent, or an explicit approved retirement record and compatibility response; silent behavior loss is a blocker.
- No `migration_state=shadow` production source remains.
- Every active alias resolves uniquely.
- Every active L2 row has a package ID, nonempty normalized `memory_namespace`, and verified digest under the exact three-part identity; blockers = 0.
- Pending legacy approvals and active legacy Runs = 0.
- Active Main Agent Runs are compatible with the target code/schema deploy.
- Backups restore successfully in a disposable environment.
- Migration/export row counts and digests are signed off.

---

## Destructive Database Migration

Do not preselect the destructive revision ID. Generate it only in Task 10 from the exact Deploy-B1 schema head with `alembic revision -m "remove legacy assistant skill runtime"`. The migration must not exist in the Deploy-A artifact if its mere presence could be applied accidentally by an automatic `upgrade head` job; package/release the revision only with the approved B2 maintenance artifact.

Preflight must fail the migration unless the data/traffic markers prove the locked gate. Do not use an environment variable alone as proof; read durable migration items/counts and require a deliberate maintenance acknowledgment.

Operations, in safe dependency order:

1. verify backup/export identifiers and zero blockers/active legacy state;
2. copy/verify final terminal approval and legacy Skill provenance archives;
3. remove FKs/relationships from universal aggregates to `assistant_skill` after preserving source IDs/digests in migration evidence/version `source_ref`;
4. set L2 `skill_package_id` and `memory_namespace` NOT NULL, verify/retain unique `(conversation_id, skill_package_id, memory_namespace)`, drop the legacy null-package name index, then drop `skill_name`;
5. drop legacy approval indexes/table after archive verification;
6. drop relationships/FKs/checks that depend on `assistant_skill`;
7. drop `assistant_skill`, including `ck_assistant_skill_single_target_binding` with it;
8. remove obsolete `legacy_skill_id`, `legacy_source_digest`, and temporary migration-only aggregate fields once provenance exists elsewhere;
9. tighten `assistant_chat_run.runtime_kind`/defaults so new legacy Runs cannot be inserted;
10. retain universal versions, bindings, Run Manifests, Provider messages, Checkpoints, interrupts, CapabilityCalls, evaluation/gate, migration audit, and rollout evidence.

Test migration against a sanitized production-shaped snapshot containing aliases, duplicate-to-one L2 mappings, terminal approvals, old Runs, published target references, and maximum-size fact/resource payloads.

Downgrade may recreate structural compatibility for local testing but must explicitly refuse to claim restoration of dropped legacy rows without the matching backup. Production rollback uses the documented snapshot/image procedure.

---

## Code and UI Cleanup Map

Delete only after Release-B code runs with legacy tables still present and import/query telemetry proves zero dependency.

### Delete legacy assistant routing/catalog modules

- `backend/app/assistant/orchestration/intent_router.py`
- `backend/app/assistant/orchestration/supervisor_graph.py`
- `backend/app/assistant/orchestration/supervisor_state.py`
- legacy-only implementation in `backend/app/assistant/orchestration/agent_runtime.py` (delete file if no reusable code remains)
- `backend/app/assistant/skill_catalog/base.py`
- `backend/app/assistant/skill_catalog/converters.py`
- `backend/app/assistant/skill_catalog/defaults_loader.py`
- `backend/app/assistant/skill_catalog/definitions.py`
- `backend/app/assistant/workflow/human_approval_runtime.py` after the entrypoint/HITL drain gate
- legacy-only supervisor/router/HITL tests replaced by Main Agent/durable equivalents

Audit `orchestration/chat_events.py`, `memory_context.py`, `openai_fallback_client.py`, `assistant/openai_compat.py`, and `run_control.py`; migrate shared behavior to the new owner or delete only when `rg`, import graph, runtime smoke, and tests prove they are legacy-only.

### Modify backend owners

- `backend/app/assistant/service.py` — queue only durable Main Agent Runs; remove daemon/single-Skill selection/fallback branches.
- `backend/app/assistant/memory_service.py` / `memory_computation.py` — package-ID L2 only after schema cleanup.
- `backend/app/assistant/models.py` — no legacy runtime/name-keyed memory contract.
- `backend/app/assistant_config/models.py` — remove `AssistantSkill`, old relationships, and `AssistantHumanApproval`.
- `backend/app/assistant_config/router.py` / `schemas.py` / `service.py` / `registry.py` / `bootstrap.py` — remove `/skills` CRUD, nested Skill Workflow endpoints, single-target serializers/guards/sync; keep standalone Tool/Workflow/Agent config.
- `backend/app/assistant/workflow/engine/*` — replace semantic `skill_name` routing/memory identity with package/version owner refs; display labels may remain non-authoritative.
- `backend/app/assistant/workflow/system_assets/registry.py` — bootstrap universal package keys/IDs, not legacy `AssistantSkill` rows.
- `backend/app/openclaw_integration/*` — remain on the Plan 02 shared Capability Runtime and must not import legacy assistant Skill registry.
- worker/API/config/deploy/CI/docs/observability files.

The old nested endpoints under `/api/assistant-config/skills/{id}/workflow*` are removed only after all UI/test consumers use the already-separate `/workflows/*` APIs or the Plan 09 workbench.

### Delete legacy frontend surface

- `frontend/src/features/assistant-config/pages/SkillSettings.tsx`
- `frontend/src/features/assistant-config/api/skills.ts`
- `frontend/src/features/assistant-config/components/SkillCard.tsx`
- `frontend/src/features/assistant-config/components/SkillManager.tsx`
- `frontend/src/features/assistant-config/components/SkillRow.tsx`
- `frontend/src/features/assistant-config/components/SkillRowEditor.tsx`
- `frontend/src/features/assistant-config/components/useSkillForm.ts`
- `frontend/src/features/assistant-config/components/skillTargetOptions.ts` and its legacy-only tests

Update `frontend/src/app/App.tsx`, assistant-config exports/queries, Workflow preview/serialization types, navigation/i18n, and any legacy `/skills/{id}/workflow` calls to the Plan 09 universal pages and standalone Workflow APIs. Use `rg` before each deletion; do not remove a shared editor component solely because its filename contains “Skill”.

---

## Dependency and Configuration Cleanup

- Remove rollout modes `legacy|shadow|canary` only after evidence is archived; keep a Main Agent enable/kill switch and independent write/policy gates.
- Remove legacy Router/provider environment variables and daemon-thread controls.
- Keep worker lease/recovery/HITL/call-ledger/reconciliation settings.
- Run an import/dependency audit after module deletion.
- Removing the Supervisor does **not** imply LangGraph is unused: current Workflow DAG execution still depends on it unless separately proven otherwise.
- Do not change `langgraph==0.3.34`, LangChain bounds, Provider libraries, or lockfiles in this plan without compatibility tests against Workflow/Agent/Main Agent/worker and a documented reason.
- Remove only packages with zero production/test/build imports and successful clean-environment install, `pip check`, backend tests, and smoke.
- Update architecture, Skill authoring, admin, worker, rollout, backup/restore, incident, reconciliation, and API docs.

---

## File Responsibility Map

### Create

- `backend/app/assistant/migration/__init__.py`
- `backend/app/assistant/migration/contracts.py`
- `backend/app/assistant/migration/models.py`
- `backend/app/assistant/migration/repository.py`
- `backend/app/assistant/migration/inventory.py`
- `backend/app/assistant/migration/packages.py`
- `backend/app/assistant/migration/l2.py`
- `backend/app/assistant/migration/approvals.py`
- `backend/app/assistant/migration/verification.py`
- `backend/app/assistant/migration/cleanup.py`
- `backend/app/assistant/migration/rollout.py`
- `backend/app/assistant/migration/shadow.py`
- `backend/app/assistant/migration/cli.py`
- one generated additive `backend/alembic/versions/<revision>_add_ai_runtime_migration_audit.py`
- one later, separately released destructive `backend/alembic/versions/<revision>_remove_legacy_assistant_skill_runtime.py`
- `backend/tests/test_ai_runtime_migration_inventory.py`
- `backend/tests/test_ai_runtime_skill_migration.py`
- `backend/tests/test_ai_runtime_l2_migration.py`
- `backend/tests/test_ai_runtime_shadow.py`
- `backend/tests/test_ai_runtime_rollout.py`
- `backend/tests/test_ai_runtime_fallback_boundary.py`
- `backend/tests/test_ai_runtime_cleanup_preflight.py`
- `backend/tests/test_ai_runtime_migration_repository_postgres.py`
- `backend/tests/test_ai_runtime_destructive_migration.py`
- operational migration/rollout/rollback/reconciliation runbooks under the repository’s existing docs location

### Modify/Delete

Use the cleanup map above plus:

- `backend/app/config.py`
- merged Plan 09 `backend/app/assistant/evaluation/contracts.py`, `models.py`, `repository.py`, `runner.py`, `worker.py`, and private Artifact/snapshot owners for the additive `runtime_shadow` purpose
- merged Plan 09 assistant-config principal/operator dependency and route-mount owner; do not create a migration-only auth path
- `backend/.env.example`
- `deploy/.env.example`
- `deploy/docker-compose.yml`
- `backend/requirements.txt` only when the dependency audit proves a change
- `backend/app/main.py`
- `backend/tests/_db.py`
- `backend/tests/_bootstrap.py`
- `frontend/src/app/App.tsx`
- `frontend/src/features/assistant-config/index.ts`
- `frontend/src/features/assistant-config/queries.ts`
- `.github/workflows/ci.yml`

Re-read the repository at implementation time because Plans 01–09 will add the new concrete files referenced throughout this plan.

At plan-authoring time the legacy truth anchors are `backend/app/assistant/orchestration/intent_router.py`, `supervisor_graph.py`, `supervisor_state.py`, `agent_runtime.py`, `backend/app/assistant/skill_catalog/*`, `backend/app/assistant/workflow/human_approval_runtime.py`, `backend/app/assistant_config/models.py`, and `frontend/src/features/assistant-config/pages/SkillSettings.tsx`. L2 is currently `AssistantConversationSkillL2Memory` in `backend/app/assistant/models.py`, uniquely keyed by `(conversation_id, skill_name)`, and `AssistantHumanApproval` is in `backend/app/assistant_config/models.py`. Task 0 must refresh this inventory after Plans 01–09 and must not delete a file solely because it appears in this baseline list.

---

## Migration CLI and Service Contracts

The CLI is a thin transport over transaction-tested services. Minimum commands:

~~~text
python -m app.assistant.migration.cli inventory scan
python -m app.assistant.migration.cli packages migrate
python -m app.assistant.migration.cli packages verify
python -m app.assistant.migration.cli l2 backfill
python -m app.assistant.migration.cli l2 verify
python -m app.assistant.migration.cli approvals archive
python -m app.assistant.migration.cli approvals verify
python -m app.assistant.migration.cli rollout prepare
python -m app.assistant.migration.cli rollout activate
python -m app.assistant.migration.cli rollout rollback
python -m app.assistant.migration.cli cleanup evaluate --gate deploy_b1|deploy_b2
python -m app.assistant.migration.cli cleanup preflight --gate deploy_b2
~~~

All data mutation commands require:

~~~text
--environment <label>
--database-fingerprint <expected non-secret fingerprint>
--source-snapshot-digest <sha256>
--expected-schema-head <revision>
--expected-build-revision <revision>
--request-id <uuid>
--batch-size <bounded integer>
--dry-run | --apply
--report-json <path>
~~~

These flags are safety bindings, not authority. Every prepare/read uses the project-authenticated migration principal; `--apply`, rollout activate/rollback, cleanup gate creation, and destructive preflight require a verified operator principal, bounded reason, and the configured distinct approval where the owning gate requires it. No request flag, environment label, local-shell presence, or report path can mint that principal. If the Plan 09/project operator verifier is unavailable, mutation transports remain unmounted/disabled and Plan 10 stops.

`--apply` additionally requires the exact prepared/dry-run batch ID and digest. The service rechecks principal/role, environment/database/schema/build/source/config under transaction before mutation. Identical `request-id` retry returns the same batch/outcome; altered reuse conflicts. CLI exit codes are stable: `0=completed`, `2=completed_with_blockers`, `3=precondition_failed`, `4=conflict_or_drift`, `5=unexpected_failure`.

Core service results are typed and bounded:

~~~python
class MigrationBatchResult(FrozenContract):
    batch_id: UUID
    status: MigrationBatchStatus
    processed: int
    succeeded: int
    blocked: int
    failed: int
    next_cursor: str | None
    report_artifact_id: UUID
    report_digest: str


class RuntimeShadowInputSnapshot(FrozenContract):
    snapshot_id: UUID
    source_production_run_id: UUID
    source_user_message_id: UUID
    principal_scope_digest: str
    message_prefix_digest: str
    authorized_context_digest: str
    snapshot_policy_digest: str
    private_eval_artifact_id: UUID
    payload_digest: str
    expires_at: datetime


class RolloutDecision(FrozenContract):
    rollout_revision_id: UUID
    assignment_id: UUID
    assigned_runtime_kind: Literal["legacy", "main_agent"]
    selected_runtime_kind: Literal["legacy", "main_agent"]
    write_mode: str
    bucket: int
    assignment_reason: RolloutAssignmentReason
    selection_reason: Literal["assigned", "preinsert_fallback"]
    fallback_event_id: UUID | None
    admission_failure_digest: str | None


class CleanupPreflightResult(FrozenContract):
    gate_id: UUID
    valid: bool
    blockers: tuple[CleanupBlocker, ...]
    evidence_digest: str
~~~

For `RolloutDecision.selection_reason=assigned`, selected equals assigned and both fallback fields are null. For `preinsert_fallback`, assigned is `main_agent`, selected is `legacy`, and both fallback fields are required and must reconstruct the append-only fallback event. No other mismatch is valid. The only inserted Chat Run's immutable `runtime_kind` must equal `selected_runtime_kind`.

No service accepts a caller-supplied “verified=true”, target digest, rollout bucket, cleanup count, shadow eligibility, or `RuntimeShadowInputSnapshot` digest/Artifact reference without recomputing/creating it from authoritative principal, source rows, config, and build metadata.

---

## Execution Discipline, Artifacts, and Stop Rules

- This plan is a rollout program, not one long code branch. Tasks 0–7 form Deploy A, Task 8 is an evidence-only gate, Task 9 forms Deploy B1, Task 10 forms Deploy B2, and Task 11 verifies the final state.
- Deploy A and B1 must each be independently releasable and rollback-tested. Deploy B2 uses a separately built artifact containing the destructive revision; do not leave the revision waiting in an earlier auto-migrated image.
- Every CLI mutation supports `--dry-run`, bounded `--batch-size`, a stable resume cursor/checkpoint, `--report-json <path>`, and explicit target environment/database identity. It refuses unknown schema/build revisions.
- Migration reports contain counts, stable IDs, digests, reason codes, build/revision, and timestamps only. They exclude raw memory facts, prompts, approval payloads, secrets, and conversation text. Store operational reports in the approved artifact/audit store; commit only sanitized fixtures/templates, never production evidence.
- Database/data claims require PostgreSQL 15 and a sanitized production-shaped snapshot. SQLite and unit fixtures cannot approve a destructive gate.
- Rollout percentages/config never mutate an existing Run. Every change creates a new immutable rollout revision and durable assignment evidence before new traffic is admitted.
- Automatic fallback completes before any Chat Run insert. After insert, failure stays on that Run; no second Legacy Run or public Run-ID remap is permitted.
- Shadow execution uses a Plan 09 Eval Run and private evaluation namespace. It never weakens the Plan 06 production active-Run unique index or appears in normal chat/Run/SSE/Artifact/memory APIs.
- Native L2 identity is always `(conversation_id, skill_package_id, memory_namespace)` with a nonempty normalized namespace; package-backed `NULL` and `default` rows may not coexist.
- Migration, diagnostic, rollout activation, cleanup gate, and destructive approval operations require the real server-side principal/operator boundary inherited from Plan 09. Without it, transports remain unmounted and progression stops.
- A hard-gate failure stops progression and returns to the owning task. Do not lower zero-tolerance safety/data gates to meet a date.
- Destructive preflight is code plus durable evidence, not an environment variable. It must be rerun after processes are stopped at B2 maintenance start.
- Keep legacy code and tables while any nonterminal legacy Run, pending legacy approval, unmapped L2 row, unresolved reconciliation, unsupported behavior branch, or observed legacy access exists.
- Write tests red first; run focused tests and `git diff --check` per code task. Task 11 runs the final clean/full suite.

---

## Task 0: Freeze Inventory, Metrics, and Rollback Evidence

**Files:** read all merged Plan 01–09 owners and legacy anchors; create read-only inventory/report contracts plus `migration/inventory.py`, `verification.py`, CLI skeleton, sanitized fixtures, and runbook templates. Do not add schema or traffic routing.

**Produces:** a signed-off immutable inventory snapshot format, stage-specific upstream hard-gate matrix, metric dictionary, behavior-branch matrix, and tested Deploy-A rollback/B2 restore procedures.

- [ ] **Step 1: Record a clean baseline.** Capture Git/build/config/dependency/worker/schema versions and one Alembic head; run Plan 01–09, legacy, frontend, deterministic evaluation, clean Python 3.11 install, and `pip check`. Record Plan 02B shared-only OpenClaw status separately.
- [ ] **Step 2: Verify inherited ship gates.** Load exact Plan 06 pre-insert fallback/active-unique/recovery-memory vectors, Plan 07 interrupt CAS/entrypoint matrix, Plan 08 independent write grant/call-owned approval/cancel-started settlement/reconciliation vectors, and Plan 09 enforce/live-pointer/gate-use/Eval-isolation/operator-guard handoff. Persist safe evidence digests and stage applicability; any missing read gate fixes shadow/read percentages at zero, any missing write gate fixes write mode off, and missing Plan 09 auth stops Plan 10.
- [ ] **Step 3: Write red inventory fixtures.** Include known system/custom/disabled Skills, new unknown Skill, Workflow/Agent targets, aliases, L2 namespaces including `NULL` versus `default`, approvals, active Runs, Eval shadow Runs, dynamic imports, API/UI/config, and supported/unsupported write branches.
- [ ] **Step 4: Implement read-only inventory.** Enumerate every source/target/version/status/digest and output only bounded stable IDs/counts/digests/reason codes. Unknown/new items become blockers.
- [ ] **Step 5: Implement import/query ownership audit.** Separate source-string history from static imports, dynamic composition, SQL ownership, and observed runtime access; tests include dynamic import/query fixtures and every normal API surface that must reject Eval shadow identities.
- [ ] **Step 6: Lock metric definitions.** Define numerator/denominator/eligibility/window/confidence for completion, failures, latency, tokens/cost, injection/path, safety, recovery, duplicates, isolation, and reconciliation.
- [ ] **Step 7: Prove backup restore.** Create DB/object/audit exports, restore into isolation, verify schema/count/digests, and run legacy plus Main Agent safe smoke.
- [ ] **Step 8: Write and execute rollback/restore runbooks.** Deploy-A rollback and B2 snapshot+matching-image restore include stop/drain, rollout revision, workers, L2 namespace triples, calls/reconciliation, validation, and abort criteria.
- [ ] **Step 9: Produce the Task 0 source snapshot digest.** Freeze the inventory/upstream-gate/metric/runbook schema and record the digest required by Task 1 dry runs.

~~~bash
git status --short
git rev-parse --short HEAD
cd backend && .venv/bin/alembic heads && cd ..
backend/.venv/bin/python -m pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
git diff --check
~~~

Commit inventory tooling, sanitized fixtures, and runbook templates only. Never commit production inventory/metric/backup identifiers.

**Commit:** `chore(ai): add runtime migration inventory tooling`

---

## Task 1: Add Migration/Rollout Evidence Schema

**Files:** create migration contracts/models/repositories/CLI modules, generated additive revision, PostgreSQL tests, model/bootstrap imports, and config for default-legacy routing. Extend Plan 09 Eval persistence only for the runtime-shadow purpose/linkage required below; do not weaken the Plan 06 production Run unique boundary.

**Produces:** evidence storage and dry-run/resumable commands only. Runtime selection remains legacy.

- [ ] **Step 1: Write red PostgreSQL schema/repository tests.** Cover item/batch transitions, append-only events/archive/gates, rollout revision/assignment immutability, per-request fallback event/Legacy Run atomicity and retry, one active revision, evidence limits, FK retention, Eval shadow purpose/comparison linkage, production-plus-Eval coexistence, and rejection of two production nonterminal Runs.
- [ ] **Step 2: Generate the additive migration.** Generate from exact Plan 09 head, assert unique ID/filename and one head, and review all constraint/index names.
- [ ] **Step 3: Implement models/triggers/repository.** One repository owns item/batch/revision/assignment/admission-fallback/comparison/archive/gate writes with expected revisions and append-only enforcement.
- [ ] **Step 4: Implement discovered-only backfill.** Exact source IDs/digests create idempotent `discovered` rows; drift appends a blocker event and never silently remaps/marks verified.
- [ ] **Step 5: Implement CLI prepare/apply/resume.** Enforce all safety flags, dry-run digest, request idempotency, bounded cursor, stable exit codes, and safe reports.
- [ ] **Step 6: Add the Eval shadow extension without a ChatRun exception.** Add/reuse `runtime_shadow` purpose, gate-ineligible constraint, private input-snapshot linkage, and comparison FK to the Eval Run. Keep Plan 06's production active unique unchanged; normal conversation/history/active/latest/SSE/stop/resume/Artifact/memory/search APIs reject Eval IDs/keys, and privileged diagnostics remain unmounted without the real operator guard.
- [ ] **Step 7: Implement guarded additive downgrade.** Flags legacy, workers stopped, no active migration/shadow/canary data requiring schema, exports recorded, and acknowledgment.
- [ ] **Step 8: Run PostgreSQL parent/head cycle.** Include legacy/Main Agent history, active assignments, migration batches, and guarded-refusal cases.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_ai_runtime_migration_inventory.py \
  backend/tests/test_ai_runtime_rollout.py \
  backend/tests/test_ai_runtime_shadow.py -q
cd backend && .venv/bin/alembic heads && cd ..
git diff --check
~~~

**Commit:** `feat(ai): add runtime migration and rollout evidence`

---

## Task 2: Migrate Main Agent Profile and All Skill Packages

**Files:** create `migration/packages.py`; modify only migration CLI/composition and Plan 01 legacy-adapter cutover guard; add deterministic system/custom migration fixtures/tests. Native package/Profile bytes are produced through Plan 09 APIs/services, not hand-written DB inserts.

**Produces:** evaluated published native Profile/packages plus explicit migration states; Catalog/traffic remains controlled by rollout cohorts.

- [ ] **Step 1: Write red mapping/golden vectors.** `general_chat`, known system Skills, custom Workflow/Agent, disabled source, alias collision, target drift/missing version, secret config, rerun, and sync/promotion race.
- [ ] **Step 2: Implement deterministic source adapters.** Convert only portable instructions/examples/resources/policy metadata and exact published target refs; reject credentials/mutable runtime secrets.
- [ ] **Step 3: Promote `general_chat` to Profile.** Reproduce prompt/model/control behavior, evaluate/publish through Plan 09, and assert no universal `general_chat` Skill or single target.
- [ ] **Step 4: Migrate known system packages in order.** Each uses CLI batch, native draft, validation, dataset/gate, publish, aliases, migration event, then an independent verify pass.
- [ ] **Step 5: Migrate all discovered custom/enabled sources.** Disabled/historical sources get explicit archive decisions and stay disabled; unknown source kind blocks.
- [ ] **Step 6: Lock native cutover.** Atomically mark promoted package so shadow sync/legacy adapter cannot mutate it; race both paths.
- [ ] **Step 7: Verify independently.** Compare source/target digest, bindings, aliases, gate, Catalog cohort state, and runtime resolution before `verified`.
- [ ] **Step 8: Enforce zero blockers.** Every source is verified/archived/approved-retired, every alias unique, no secret copied, and rerun is idempotent.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_ai_runtime_skill_migration.py \
  backend/tests/test_skill_publish_gate.py -q
git diff --check
~~~

**Commit:** `feat(ai): migrate legacy skills to native packages`

---

## Task 3: Backfill and Verify L2 Stable IDs

**Files:** create `migration/l2.py`; modify merged L2 model/repository/service compatibility seam and tests. Do not remove `skill_name` or its legacy index in Deploy A.

**Produces:** one package-ID-backed fact row per exact conversation/package/memory-namespace triple while legacy and new code read/write the same row.

- [ ] **Step 1: Write red PostgreSQL mapping vectors.** Every precedence, normalization ambiguity, many-to-one, duplicate/order, overflow, concurrent update, disabled/deleted, missing alias, rerun, legacy-null-to-`default` backfill, two native namespaces for one package, and `(package,NULL)` versus `(package,default)` split prevention.
- [ ] **Step 2: Implement archive-before-mutation.** Persist source ID/name/version/fact digest/count/provenance before row change; keep raw facts out of general event JSON.
- [ ] **Step 3: Implement bounded locked batches.** Use the proven batch/lock pattern, recheck source version/mapping digest, persist cursor, and return blockers without losing prior items.
- [ ] **Step 4: Implement deterministic triple-key merge.** Normalize/derive the target namespace before grouping, default legacy rows to `default`, then order by creation time/UUID, stable-deduplicate, compact only through the existing contract, and retain complete approved provenance per exact triple.
- [ ] **Step 5: Implement one-row compatibility seam.** Legacy name/alias resolves to package ID plus mapped namespace and Main Agent uses the same exact triple with optimistic version and `last_applied_run_id`; forbid null package-backed namespaces, default/NULL split rows, and dual-write.
- [ ] **Step 6: Prove concurrent correctness.** Race legacy write, Main Agent terminal memory, and backfill; no lost facts, duplicate terminal apply, or split rows.
- [ ] **Step 7: Run delta verifier to stability.** Require two consecutive locked-window zero-delta scans and equal row/fact/digest invariants grouped by `(conversation_id, skill_package_id, memory_namespace)`.
- [ ] **Step 8: Prove rollback/blocking.** Ambiguity stays blocked, B1/B2 gate fails, and Deploy-A legacy reads current facts through names.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_ai_runtime_l2_migration.py \
  backend/tests/test_assistant_memory_l2_service.py \
  backend/tests/test_assistant_service_l2_memory.py -q
git diff --check
~~~

**Commit:** `feat(ai): migrate skill memory to stable package ids`

---

## Task 4: Migrate/Drain HITL Entrypoints

**Files:** create `migration/approvals.py`; modify entrypoint capability classification/admission, legacy approval creation gate, and tests. Keep the legacy table/runtime code until B1/B2.

**Produces:** every entrypoint is durably resumable, evaluation-simulated, or explicitly unavailable; no new legacy approval is created after the recorded cutoff.

- [ ] **Step 1: Inventory every human-node entrypoint.** Assistant Chat, standalone and nested Workflow, Agent, legacy Workflow test, Plan 09 workbench, OpenClaw catalog/execute, scheduler/background, CLI, and direct service callers; static import/route scans plus runtime smoke catch dynamic paths. A missing row is a B1 blocker.
- [ ] **Step 2: Write red entrypoint matrix tests.** Each concrete entrypoint must be durable with an authenticated decision channel, evaluation-simulated, or explicitly `unsupported_interrupt` before child execution; unknown defaults unsupported. Pin OpenClaw and legacy workflow-test behavior explicitly so neither can retain a hidden blocking `HumanLoopRuntime` path.
- [ ] **Step 3: Route supported paths.** Main/run-backed to Plan 07, workbench to Plan 09 simulation, and no new production path imports/calls blocking runtime.
- [ ] **Step 4: Add and test the creation cutoff.** Enable only after matrix green; existing legacy Runs keep frozen semantics and are drained/cancelled/expired with user-safe events.
- [ ] **Step 5: Archive terminal history.** Copy bounded request/resolution audit with source counts/digests; continuation tokens are never treated as resumable authority.
- [ ] **Step 6: Race cutoff/create/resolve.** Prove no lost terminal record, new pending row after cutoff, fabricated durable resume, waiter thread, or proxy.
- [ ] **Step 7: Verify archives and zero active state.** Counts/digests match and every pending/active row is resolved; otherwise B1 gate fails.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_workflow_human_in_loop_runtime.py \
  backend/tests/test_workflow_test_run_stream.py \
  backend/tests/test_openclaw_integration.py \
  backend/tests/test_ai_runtime_migration_inventory.py \
  backend/tests/test_ai_runtime_cleanup_preflight.py -q
git diff --check
~~~

**Commit:** `feat(ai): migrate durable human interrupt entrypoints`

---

## Task 5: Implement Side-Effect-Safe Shadow Comparison

**Files:** create `migration/shadow.py`; modify the post-admission shadow scheduler and Plan 09 Eval/isolation composition; add shadow comparison/query-isolation/privacy tests. Do not modify legacy output behavior or the Plan 06 production active-Run unique index.

**Produces:** internal paired comparisons whose Main Agent half is a gate-ineligible Plan 09 Eval Run, never a second Chat Run, and cannot become user-visible or mutate production state.

- [ ] **Step 1: Write red identity/isolation tests.** One production Chat Run plus one Eval shadow may coexist; two nonterminal production Chat Runs still violate Plan 06. Tripwire production Message/SSE/L1/L2/terminal memory/approval/business/outbox/Artifact writers and require normal history, active/latest Run, SSE, stop/resume, Artifact GET, search/list, memory finalizer, and conversation-delete APIs to reject the Eval identity/key.
- [ ] **Step 2: Implement frozen private shadow input.** From the exact authorized production prefix, create a bounded `RuntimeShadowInputSnapshot` in the private evaluation namespace with source/principal/policy/content digests and expiry. Never persist raw content in comparison/events/metrics/logs; privacy/source deletion expires the payload. Deny scheduling where deployment data-processing policy does not authorize additional evaluation.
- [ ] **Step 3: Implement paired Eval scheduling.** Insert the gate-ineligible `runtime_shadow` Eval Run and comparison pair after the production request is durably admitted, record exact digests, and apply independent concurrency/token/cost/queue limits. Shadow queue failure is nonblocking and never creates/remaps a Chat Run.
- [ ] **Step 4: Compose Plan 09 isolation.** The Eval worker consumes only the frozen snapshot; data/memory/events/Artifacts/Eval CapabilityCalls are evaluation-owned, only shadow-specific bounded reads execute, all side effects/HITL simulate before adapter lookup, and no live production Conversation Session is queried.
- [ ] **Step 5: Implement durable Eval recovery and leakage guards.** Freeze subject/Profile/Catalog/Manifest/model/runtime/build versions, use the Plan 09 lease/CAS worker, never use Plan 06 Chat Run Checkpoints for the shadow half, and keep diagnostic queries behind the verified operator boundary or unmounted.
- [ ] **Step 6: Implement comparison evidence.** Typed intent/injection/path/obligations/stop/error and normalized metrics; bounded summaries/digests only. Online shadow comparisons remain ineligible for Plan 09 publish gates.
- [ ] **Step 7: Run offline then staff shadow.** Classify all differences, meet Plan 04/09 thresholds, and require zero safety/privacy violations before an explicitly approved production-shadow scope is eligible.
- [ ] **Step 8: Prove legacy response independence.** Shadow queue/failure/cancel/cost exhaustion/cleanup never changes or delays the user-visible legacy outcome beyond configured nonblocking overhead and never changes its Run ID or events.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_ai_runtime_shadow.py \
  backend/tests/test_skill_eval_isolation.py \
  backend/tests/test_skill_eval_snapshot_policy.py -q
git diff --check
~~~

**Commit:** `feat(ai): add isolated runtime shadow comparison`

---

## Task 6: Add Deterministic Canary Routing and Rollback Drill

**Files:** create `migration/rollout.py`; modify admission/config/env/Compose, Run creation schema/service, observability, and rollout/fallback tests.

**Produces:** default-legacy immutable rollout revisions/assignments with a tested rollback; it does not change production percentage by merging code.

- [ ] **Step 1: Write red rollout/config vectors.** Pin the legacy mapping table: absent/off -> legacy, old shadow -> explicit-eval-only shadow with paired production eligibility zero, and old read_only -> startup error requiring explicit new canary/main revision. Any dual-variable presence fails startup. Also cover active durable revision match, 0/1/5/25/50/100 buckets, revision/salt/config drift, same conversation across devices/processes, and override authorization.
- [ ] **Step 2: Implement rollout revision prepare/activate.** Persist immutable config/evidence, verify gates/build/workers/packages, atomically supersede the prior active revision, and never store salt.
- [ ] **Step 3: Implement canonical assignment.** Compute and insert/get the stable `assigned_runtime_kind` server-side from authoritative scope/conversation/salt/revision before Run creation; assignment stores no request-local fallback outcome. Then run the complete admission preflight and derive the typed `RolloutDecision`.
- [ ] **Step 4: Freeze pre-insert Run admission.** Profile/Catalog/Manifest/model/probe/policy/build/compatible-worker checks and any permitted fallback finish before `create_run`; the one inserted Run freezes selected runtime, Profile/Manifest/model, write mode, rollout revision/assignment as immutable evidence.
- [ ] **Step 5: Enforce pre-insert-only fallback.** A proven fallback leaves assignment unchanged, atomically appends the per-request fallback event with `candidate_runtime_kind=main_agent`, `selected_runtime_kind=legacy`, reason `preinsert_fallback`, and inserts exactly one Legacy Run. Force failure before insert and after Main Agent insert/queue, worker claim, Manifest reconstruction, Provider construction, attempt, interrupt, visible output, and effect; every post-insert case creates no fallback event/Legacy Run/remap and stays on the original Run's recovery/failure/cancel/reconciliation path.
- [ ] **Step 6: Keep read/write independent.** Separate percentage/eligibility/policy evidence and prove read changes cannot enable write.
- [ ] **Step 7: Drill canary rollback.** Activate a new legacy-selecting revision while running/waiting/recovering/reconciliation Runs exist; they retain frozen runtime and no call/write duplicates.
- [ ] **Step 8: Verify configuration startup.** Environment must name/match the active durable revision; both old/new mode variables, old `read_only`, unknown values, compatibility shadow paired-traffic attempts, and config/revision mismatch fail before Run admission. Remove the old variable from env/Compose examples before non-legacy activation.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_ai_runtime_rollout.py \
  backend/tests/test_ai_runtime_fallback_boundary.py \
  backend/tests/test_assistant_chat_run_service.py -q
git diff --check
~~~

**Commit:** `feat(ai): add deterministic runtime rollout assignment`

---

## Task 7: Roll Out Read Paths, Then the Golden Write

**Files:** normally deployment configuration/runbook decisions and migration state only. Code defects return to Tasks 2–6 and are fixed/tested there. Commit sanitized config templates or runbook corrections; do not commit production reports.

**Produces:** Deploy A at 100% supported Main Agent traffic while legacy code/schema remain intact and rollbackable.

- [ ] **Step 1: Recheck inherited gates and prepare each rollout revision.** Recompute the Task 0 Plan 06/07/08/09 evidence and bind cohort/window/build/config/datasets/gates/gate-use/packages/workers/metrics before activation; never edit an active revision. Missing read evidence holds shadow/read at zero, and missing write evidence holds write off.
- [ ] **Step 2: Roll Catalog packages in locked order.** Require current Plan 09 gate and verified migration item for each cohort exposure.
- [ ] **Step 3: Execute shadow/read stages.** First prove staff/fixture Eval shadow, then explicitly approved production-shadow scopes, then each read canary stage. For each 1/5/25/50/100 or approved equivalent, meet the locked window/sample and compare quality/safety/privacy/latency/token/recovery with confidence notes; compatibility-mapped Plan 04 shadow never schedules production pairs.
- [ ] **Step 4: Execute independent golden-write canary.** Staff then bounded cohort; approve/reject/cancel/expire/reconnect/restart/two-worker with one-write maximum.
- [ ] **Step 5: Close every behavior branch.** Each supported write gains exact Plan 08/09 evidence or an approved, implemented user-facing retirement; no unclassified gap.
- [ ] **Step 6: Apply automatic stop criteria.** Freeze on unknown/reconciliation, unauthorized/duplicate/real-shadow-write/false-completion, hard data mismatch, or incident; resolve before a new revision.
- [ ] **Step 7: Reach 100% supported Main Agent.** Legacy disabled but deployable, write at separately approved level, no unsupported traffic silently routed away.
- [ ] **Step 8: Drill final Deploy-A rollback.** New revision selects legacy for future Runs; existing Main Agent Runs settle on compatible workers.

Execution uses a prepared revision/batch from the same build; substitute approved values without printing the cohort salt:

~~~bash
backend/.venv/bin/python -m app.assistant.migration.cli rollout prepare \
  --environment "$ENVIRONMENT" \
  --database-fingerprint "$DB_FINGERPRINT" \
  --source-snapshot-digest "$SOURCE_DIGEST" \
  --expected-schema-head "$SCHEMA_HEAD" \
  --expected-build-revision "$BUILD_REVISION" \
  --request-id "$REQUEST_ID" \
  --batch-size 100 \
  --dry-run \
  --report-json "$REPORT_PATH"

# After review, rerun with the prepared batch ID/digest and --apply,
# then activate that exact rollout revision through the same guarded CLI.
~~~

**Commit:** normally none. Commit only reviewed deployment templates/runbook corrections; production rollout revisions and evidence live in durable audit storage.

---

## Task 8: Complete Soak and Approve Deploy B1

**Files:** evidence/report generation and runbook updates only. Do not delete code or generate the destructive migration.

**Produces:** two distinct approvals: permission to build/deploy B1 code cleanup, and a later conditional B2 maintenance checklist that is not yet executable.

- [ ] **Step 1: Complete the stability window.** At least 14 consecutive days and minimum samples; qualifying rollback or hard-safety defect restarts the window.
- [ ] **Step 2: Re-evaluate all offline/online/upstream gates.** Quality, failures, latency/token, safety/privacy, incidents, recovery/SSE/cancel/resume, triple-key memory, ledger/reconciliation, Plan 09 live-pointer/gate-use/operator guard, and data integrity. Confirm Plan 02B shared-only OpenClaw exit before B1 if any cleanup owner overlaps.
- [ ] **Step 3: Complete the legacy-zero window.** Seven days of zero Run/router/supervisor/waiter/config/table access using application counters plus query/probe evidence.
- [ ] **Step 4: Assert all hard counts zero.** Legacy Runs/approvals, nonterminal runtime-shadow Eval Runs, unresolved calls, migration blockers, unsupported HITL/OpenClaw/workflow-test gaps, unverified archives, leaked Eval shadow identities, null package-backed L2 namespaces, and split default/NULL triples.
- [ ] **Step 5: Repeat restore and dry runs.** DB/object/audit restore, Deploy-A rollback, B1 compatibility smoke, schema snapshot, and destructive migration dry-run on production-shaped clone.
- [ ] **Step 6: Create `deploy_b1` cleanup gate.** Service recomputes evidence/counts plus Plan 02B and Plan 06–09 contract digests, stores passed/failed immutable gate, and invalidates on drift. It requires the real operator principal/approval path; without it the operation is unmounted and B1 is blocked.
- [ ] **Step 7: Obtain explicit B1 approval.** B2 remains unapproved until B1 zero-access window and fresh maintenance gate.

~~~bash
backend/.venv/bin/python -m app.assistant.migration.cli cleanup evaluate \
  --gate deploy_b1 \
  --environment "$ENVIRONMENT" \
  --database-fingerprint "$DB_FINGERPRINT" \
  --source-snapshot-digest "$SOURCE_DIGEST" \
  --expected-schema-head "$SCHEMA_HEAD" \
  --expected-build-revision "$BUILD_REVISION" \
  --request-id "$REQUEST_ID" \
  --batch-size 100 \
  --dry-run \
  --report-json "$REPORT_PATH"
~~~

**Commit:** no production evidence. Commit only sanitized gate fixtures/runbook corrections.

---

## Task 9: Remove Legacy Code/UI While Keeping Tables

**Files:** cleanup map plus exact runtime/import/query owners found by Task 0. The additive migration and all legacy tables remain present. Add temporary B1 startup/schema compatibility probes and query-access telemetry.

**Produces:** Deploy B1 binaries that cannot create/select/read/write legacy runtime data but tolerate legacy tables being present.

- [ ] **Step 1: Write red architecture/API/UI tests.** Reject production imports/composition/routes/queries for legacy Router/Supervisor/catalog/HITL/Skill CRUD/frontend pages while allowing audit/migration readers.
- [ ] **Step 2: Switch all composition owners.** Bootstrap, admission, worker, config, admin, OpenClaw, navigation, and tests use universal runtime; automatic legacy fallback removed, safe-disable retained. OpenClaw must already satisfy Plan 02B shared-only dispatch and must not retain a blocking human-loop or legacy AssistantSkill registry import.
- [ ] **Step 3: Migrate nested API consumers.** Move to standalone Workflow/Plan 09 workbench endpoints before removing legacy nested routes.
- [ ] **Step 4: Remove semantic name ownership.** Authorization/routing/memory uses package/version IDs; remaining name strings are display/provenance/audit only and explicitly classified.
- [ ] **Step 5: Delete proven legacy-only code/UI.** Require `rg`, static/dynamic import graph, OpenAPI/route snapshots, composition smoke, query telemetry, and tests; preserve Workflow DAG/LangGraph owners.
- [ ] **Step 6: Run full/clean B1 verification.** Backend/frontend/evaluation/recovery/write/HITL/OpenClaw and clean install against populated restored schema.
- [ ] **Step 7: Deploy B1 without destructive migration.** Pin schema head, keep old tables populated, and observe pre-B2 window with zero query/import/endpoint/config access.
- [ ] **Step 8: Create `deploy_b2` candidate gate only after the window.** Drift or any access fails it and returns to this task.

~~~bash
rg -n 'IntentRouter|SupervisorGraph|AssistantSkill|AssistantHumanApproval|skill_name' \
  backend/app frontend/src
backend/.venv/bin/python -m pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
git diff --check
~~~

Classify each remaining match as immutable migration/audit/provenance or a blocker. A passing `rg` alone is not proof of no dynamic SQL/import.

**Commit:** `refactor(ai): remove legacy assistant runtime`

---

## Task 10: Apply Destructive Schema Cleanup

**Files:** generate the destructive revision only now; add preflight/postflight/restore tests and maintenance runbook updates. No ordinary runtime feature code belongs in this task.

**Produces:** the separate B2 maintenance artifact and cleaned schema.

- [ ] **Step 1: Write red destructive preflight tests.** Every hard blocker, stale/missing backup, drift, process heartbeat, post-approval legacy access, nonterminal runtime-shadow Eval, reconciliation, archive mismatch, null/invalid/split L2 namespace triple, expired gate, missing operator/approval, and wrong binary/schema marker.
- [ ] **Step 2: Generate B2 revision from exact B1 head.** Keep it out of Deploy-A/B1 artifacts; assert one head and explicit dependency order.
- [ ] **Step 3: Implement migration preflight inside the revision/service.** Require current passing B2 gate and recompute authoritative counts under maintenance conditions; environment acknowledgment alone fails.
- [ ] **Step 4: Test maximum production-shaped migration.** Provenance/archive, L2 package/namespace non-null plus three-column unique index, tables/FKs/checks removal, retained histories, and exact row/fact/digest invariants.
- [ ] **Step 5: Execute maintenance stop/drain/preflight.** Stop API/workers/schedulers, verify heartbeats inactive and active state drained, then rerun gate/preflight.
- [ ] **Step 6: Verify final backups and apply.** Record DB/object/audit identifiers outside Git, migrate, run postflight schema/count/digest/head checks, and start B1-or-newer only.
- [ ] **Step 7: Run production smoke.** Read, durable Run, approval/input, golden write, cancel/SSE/restart, memory, evaluation/admin, reconciliation, OpenClaw.
- [ ] **Step 8: Prove disposable restore.** Final backup plus Deploy-A images restores legacy-capable state; never claim empty downgrade restores data.
- [ ] **Step 9: Store evidence correctly.** Commit migration/tests/sanitized runbook only; actual maintenance evidence stays in audit storage.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_ai_runtime_cleanup_preflight.py \
  backend/tests/test_ai_runtime_destructive_migration.py -q
cd backend && .venv/bin/alembic heads && cd ..
git diff --check
~~~

**Commit:** `feat(ai): remove legacy assistant schema`

---

## Task 11: Dependency, Documentation, and Final Verification

**Files:** requirements/lock/config/env/Compose/CI and final architecture/authoring/admin/API/worker/HITL/reconciliation/backup/restore/incident docs. Remove a dependency only with import/build evidence.

- [ ] **Step 1: Produce dependency/import report.** Remove only zero-use packages/flags with clean install/`pip check`; retain LangGraph/LangChain while Workflow DAG imports them.
- [ ] **Step 2: Remove rollout compatibility configuration safely.** Delete the old `ASSISTANT_MAIN_AGENT_MODE` parser/env/Compose examples only after native durable revisions own all traffic; startup rejects the removed variable instead of guessing. Preserve assignment/evidence readers and final kill switch/recovery/HITL/ledger/reconciliation/write-policy controls.
- [ ] **Step 3: Update final docs.** Architecture, authoring/import safety, admin/evaluation, workers/recovery, HITL/reconciliation, backup/restore, incidents, OpenClaw, API replacements.
- [ ] **Step 4: Run full clean verification.** Backend, PostgreSQL additive/destructive/restore, frontend, deterministic evaluation, Python 3.11, and process-level restart/HITL/write/SSE/memory smoke.
- [ ] **Step 5: Audit all legacy names/surfaces.** Code/OpenAPI/schema/docs/config/Compose/CI; immutable audit/migration mentions classified, live owners block completion.
- [ ] **Step 6: Prove final kill switch.** Reject/queue/narrow new Main Agent work only; no deleted runtime import/config/route exists.
- [ ] **Step 7: Verify final artifacts.** One head, binary/schema marker, restore drill, cleanup gates/evidence retention, `git diff --check`, and no unrelated staged files.

~~~bash
backend/.venv/bin/python -m pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
cd backend && .venv/bin/alembic heads && cd ..
git diff --check
~~~

**Commit:** `chore(ai): finalize universal skill runtime migration`

---

## Exit Criteria

- Every production Skill/Profile/alias has a verified universal immutable target and qualifying evaluation evidence.
- Every active L2 memory uses the exact stable `(conversation_id, skill_package_id, memory_namespace)` identity with a nonempty normalized namespace; name-keyed and package-backed null-namespace identity are gone without data loss.
- Main Agent owns 100% supported production traffic and meets the locked soak, quality, safety, and operational gates.
- Read/write canaries, restart/resume/cancel/SSE, and rollback boundaries have been exercised.
- Runtime shadow uses only gate-ineligible Plan 09 Eval Runs; Plan 06 still admits at most one nonterminal production Chat Run per conversation, and no Eval shadow identity/payload is reachable from normal production APIs or memory finalizers.
- Automatic fallback occurs only before any Chat Run insert, leaves the stable rollout assignment unchanged, and persists a per-request fallback event atomically with one Legacy Run; no inserted Main Agent Run creates or remaps to a Legacy Run.
- No real shadow write, shadow visibility/privacy leak, unauthorized call, duplicate write, false completion, or unresolved reconciliation remains.
- No runtime/config/UI code imports or queries the legacy Skill/HITL mechanisms.
- `SkillRouter`, single-Skill Supervisor, blocking HITL coordinator, legacy Skill admin, `assistant_skill`, and its single-target constraint are removed.
- Tool, Workflow, Agent, OpenClaw shared Capability execution, immutable histories, and audit evidence remain intact; overlapping OpenClaw legacy dispatch/blocking-HITL owners have satisfied the Plan 02B/entrypoint gates before B1 deletion.
- Dependency/config/docs match the final architecture.
- Deploy-A rollout revisions/assignments, B1/B2 cleanup gates, migration batches/events, archive counts, and backup/restore evidence remain queryable and digest-verifiable after legacy schema removal.
- The old/new runtime mode mapping vectors pass, the old `read_only` value cannot silently select canary/main, dual variables fail startup, and no Plan 04 compatibility shadow can schedule production paired traffic.
- All migration/diagnostic/rollout/cleanup transports use the real server-side principal/operator boundary; no feature flag, CLI argument, or local request boolean substitutes for authorization.
- The running binary declares and passes the final schema/runtime compatibility marker; older Deploy-A binaries are rejected operationally and are used only with a matching restored snapshot during a documented recovery drill.

## Final System State

MindAtlas now has a general Skill layer rather than “one Skill equals one execution target”: one durable Main Agent dynamically injects multiple immutable Skill versions, exposes owner-qualified Tool/Workflow/Agent Capabilities through a shared Gateway, executes a Provider-neutral recoverable loop, persists budgets/obligations/Artifacts/interrupts/calls, protects writes with exact approval/idempotency/reconciliation, keys L2 by stable package ID plus memory namespace, and is managed through versioned package/evaluation workflows. Legacy routing and schema are no longer a fallback or hidden dependency.

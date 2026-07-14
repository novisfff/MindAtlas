# MindAtlas Universal Skill Admin and Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Begin after the Plans 01–08 contract handoffs required below are stable; Plan 02B production observation is coordination evidence, not an M4 coding prerequisite. Do not use the workbench to bypass runtime policy or enable additional production writes.

**Goal:** Let an administrator safely create, import, edit, validate, compare, test, publish, restore, export, archive, and catalog-enable universal Skill Packages and Main Agent Profiles while preserving immutable history and enforcing reproducible evaluation evidence before publication/promotion.

**Architecture:** Extend the Plan 01 package/version APIs instead of replacing them. Mutable aggregates expose revision-CAS metadata and archive/catalog controls; every content edit, restore, and publish appends an immutable version. A versioned evaluation subsystem runs the real Main Agent contracts inside an isolated `owner_kind=test` environment with simulated side effects. The React assistant-config area adds a package editor, version/diff views, safe resource preview, interactive trace workbench, dataset evaluation, and publish-gate flow while the legacy Skill page remains available until Plan 10.

**Prerequisites:** Plan 01 immutable package/Profile contracts; Plan 02A Registry/Gateway contracts; Plans 03–08 runtime, injection, policy/budget/obligation, durability, HITL, and CapabilityCall handoffs. Plan 02B OpenClaw observation/legacy-removal status is recorded in Task 0 but does not block this plan because evaluation neither imports OpenClaw legacy execution nor exposes a production runtime path.

---

## Position and Hard Boundary

This is Plan 09 of 10 and milestone M4.

Implemented here:

- Universal Skill aggregate metadata, alias, archive/catalog controls, and append-only content lifecycle.
- Standard package create/import/append/fork/export with validation and deterministic preview.
- Skill/Main Agent Profile editors for instructions, resources, bindings, policy, budgets, completion, and version history.
- Version/digest/diff and “restore as new draft” flows.
- Versioned fixed datasets, interactive isolated tests, deterministic evaluation, optional live-model evaluation, and publish/promotion gates.
- Safe non-executable display for `scripts/` and all package resources.
- Server-enforced separation between test and production data/memory/events/writes.

Not implemented here:

- No in-place update/delete of immutable Skill/Profile versions, bindings, resources, evaluation cases/results, or gate records.
- No physical package deletion; “delete” in product CRUD is archive.
- No automatic merge of two arbitrary package file trees.
- No execution of package `scripts/`, shell/Python/JS snippets, macros, or uploaded binaries.
- No real local/external mutation from the workbench, even when production golden writes are enabled.
- No production runtime cutover or deletion of legacy `SkillSettings`; Plan 10.
- No waiver that silently converts a failed safety assertion into a pass.

### Authorization delivery decision

The repository has no general authenticated assistant-config principal or operator-role dependency at plan-authoring time; the current `backend/app/assistant_config/router.py` mounts mutations with only `Depends(get_db)`. This plan therefore chooses the fail-closed option instead of inventing an ad hoc `X-Admin` header, request-body boolean, loopback/Origin check, or UI-only role:

- all new Plan 09 HTTP routes are included under one separately mountable parent router (subrouters may remain split by responsibility) and a real server-verified assistant-config principal dependency;
- every mutation, resource-content read, Eval evidence read, and SSE stream requires that authenticated principal; catalog enable/disable, non-safety waiver, system-package mutation, and system-Profile mutation additionally require an operator/admin role;
- service methods for privileged transitions accept a verified `OperatorPrincipal` value, not an `isAdmin` boolean, so a direct service call cannot bypass the router guard;
- if no such dependency exists when the implementation reaches Task 0, production/staging keep the entire Plan 09 router unmounted and absent from OpenAPI. Trusted test/dev mounting is permitted only by the explicit environment guard used by the tests and is never release evidence;
- shipping a real project-wide authentication/RBAC foundation is outside this plan. Until one is merged and the Plan 09 router is guarded by it, the backend/UI may be code-complete behind the default-off mount, but Plan 09/M4 release and Plan 10 entry are not complete.

Feature flags, hidden navigation, confirmation dialogs, CORS, reverse-proxy placement, and possession of an arbitrary package ID are not authentication.

---

## Locked Lifecycle Semantics

### Aggregate CRUD

- **Create:** append the initial draft through Plan 01 contracts.
- **Read:** list/detail/version/resource APIs never return resource bytes implicitly.
- **Update:** mutable display metadata, custom aliases, archive/catalog flags, and pointers change only through expected aggregate revision; content changes append a draft.
- **Delete:** archive only. Physical delete is not exposed.

Add to `assistant_skill_package`:

- `aggregate_revision INTEGER NOT NULL DEFAULT 0` for optimistic updates;
- `archived_at`, `archived_by` nullable;
- `catalog_enabled_at`, `catalog_enabled_by` nullable evidence.

Archive atomically disables new Catalog recall and prevents new publish/catalog operations until unarchived. It does not mutate immutable versions or invalidate a Run already frozen to a version. Emergency Capability disable remains a live global/target availability control, not an archive side effect.

Add `disabled_at`, `disabled_by` to custom alias rows. Canonical/legacy aliases cannot be disabled through ordinary admin. Alias rows are never deleted or reassigned; disabled names remain reserved so historical references cannot resolve to a different package later.

Task 1 owns a dedicated generated additive migration for these aggregate/alias fields. Do not defer them into the evaluation migration: 09A must deploy and roll back independently from 09B. Generate it from the exact Plan 08 head with `alembic revision -m "add skill package admin lifecycle"`; Task 3's evaluation migration then descends from the Task 1 head.

### Draft/save

- The browser edits a working copy only.
- Save submits a complete normalized package snapshot plus `expectedAggregateRevision`.
- The server reruns the Plan 01 parser/security/manifest validators and appends/reuses an immutable `version_source=save` row.
- Browser parsing is advisory UX; it is never the acceptance boundary.
- Concurrent divergent saves return conflict with both version IDs; the UI offers explicit compare/restore, not last-write-wins.

### Publish

- A publish always names one owned draft version. It also names one qualifying gate except for the explicitly non-live bootstrap case in the matrix below.
- Service re-resolves every Capability target and recomputes content/binding/version digests.
- The recomputed candidate must exactly match gate subject, resolved bindings, runtime contract, dataset versions, and policy thresholds.
- Target/config/build drift invalidates the gate and requires reevaluation.
- Publish appends a distinct `version_source=publish` row and atomically advances the pointer; it never edits/promotes the draft row in place.
- Legacy shadow sync may append its locked migration versions without a normal admin gate only while `catalog_enabled=false`; Plan 10 owns their cutover gate.

The production visibility invariant is stronger than the temporary gate mode:

~~~text
Skill package:      catalog_enabled=true  => every published_version_id advance requires a fresh matching gate
Main Agent Profile: runtime_enabled=true  => every published_version_id advance requires a fresh matching gate
~~~

This applies in both `observe` and `enforce`. Plan 04 builds each new Run's live Catalog from `catalog_enabled + current published_version_id`; therefore advancing the pointer of an enabled aggregate is itself a live promotion, even when the enable flag does not change.

| Gate mode / aggregate state | Publish may advance pointer without `gateId`? | Enable live visibility? |
|---|---:|---:|
| `observe`, Skill `catalog_enabled=false` / Profile `runtime_enabled=false` | Yes, only for an explicitly recorded non-live bootstrap/migration publish | No; enable still requires a fresh matching gate |
| `observe`, already live-enabled aggregate | No | Already live; an ungated pointer advance fails atomically |
| `enforce`, native package/Profile | No | No without the exact current-version promotion gate |
| legacy shadow sync, still live-disabled | Yes, through the locked Plan 01 compatibility service only | No; Plan 10 supplies the cutover gate |

Publish locks the aggregate, rechecks the current revision/live flag, re-resolves the candidate closure, verifies gate expiry and exact subject/digest evidence when required, appends the publish row plus append-only gate-use evidence (absent only for the non-live cases above), and advances the owned pointer in one transaction. A missing/stale gate on an enabled aggregate changes neither the published pointer nor the Catalog/Profile snapshot inputs. There is no fallback to a “last gated” hidden pointer and no second Catalog version pointer in Plan 09.

An ungated non-live publish does not become eligible later merely because it exists. Catalog/Profile enable must evaluate the exact current published version and use a fresh non-expired promotion gate whose candidate closure still matches under the aggregate lock.

### Restore/rollback

“Restore version” copies an owned immutable version’s portable content/resources into a **new draft** with provenance `restoredFromVersionId`. It does not move the published pointer backward. The restored draft must validate/evaluate/publish normally, producing new sequence/digest/audit evidence.

For an urgent runtime incident, disable Catalog/Profile runtime through the existing feature controls first; do not bypass append-only publication history.

---

## Standard Package Import and Export

Plan 01 create-only import remains the default. Add an explicit dry-run preview and these apply modes:

| Mode | Contract |
|---|---|
| `create` | canonical name/aliases must be unused; creates aggregate + draft |
| `append_to_existing` | package name must resolve to the selected aggregate; appends a complete draft after expected-revision check |
| `fork_as_new` | admin supplies a new valid canonical name; server rewrites/validates standard frontmatter name and creates a distinct package |

There is no hidden file-level merge. The preview returns bounded structural/text diffs, validation findings, normalized paths, MIME/kind/size/digests, capability resolution preview, and the resulting content digest. Apply must include the preview digest; changed bytes or aggregate revision invalidate it.

All Plan 01 protections remain mandatory:

- frontmatter/name/schema validation;
- path normalization and traversal/absolute-path rejection;
- symlink/special-file rejection;
- file/count/uncompressed/ratio/depth limits against ZIP bombs;
- server MIME sniffing and per-kind limits;
- deterministic export ordering/timestamps/permissions;
- no credentials, secret headers, or executable bit;
- `scripts/` stored and exported as non-executable resources only.

Imported/appended/forked content is always a Draft, never automatically published, catalog-enabled, or runtime-enabled.

---

## Editor Information Architecture

Keep the current `frontend/src/features/assistant-config/pages/SkillSettings.tsx` as the clearly labeled legacy page during migration. Add a separate “Universal Skills” route with:

1. **Overview** — canonical/display names, description, aliases, native/shadow/system status, archive/catalog state.
2. **Instructions** — complete `SKILL.md` editor with parsed frontmatter diagnostics and description guidance.
3. **Applicability** — positive/negative examples and catalog metadata from `mindatlas.yaml`.
4. **Capabilities** — ordered multi-select from the shared Registry, exact target/version/resolution/risk preview.
5. **Policy & side effects** — declared classes, approval rules, principal/entrypoint restrictions; cannot exceed server global policy.
6. **Budgets** — Skill call/repeat limits and nested depth constrained by Profile ceilings.
7. **Completion** — terminal-output/follow-up/Artifact/approval/input obligations.
8. **Resources** — add/replace/remove in the working copy; path/kind/MIME/size/digest and safe preview.
9. **Versions** — Draft/Published markers, source/provenance, digests, binding snapshots, compare, restore-as-draft, export.
10. **Test workbench** — prompt, locale, profile/model selection, trace, result, assertions, dataset runs, gate status.

Main Agent Profile gets a sibling editor for prompt layers, catalog scope, control Capability set, Provider/model binding, Run budgets, completion policy, feature mode, versions, compare/restore, and evaluation. It must not embed one special Skill as the execution target.

### Resource preview

- Text/Markdown/code is displayed as escaped, size-bounded plain text with optional syntax highlighting.
- Images use validated media type and object URL with CSP; SVG/HTML is downloaded or sanitized, never injected as trusted DOM.
- Other binary resources expose metadata/download only.
- `scripts/` has a persistent “stored as non-executable context resource” badge and no run/terminal button.
- Preview never resolves a path outside the selected immutable version.

---

## Evaluation Persistence

Do not preselect a revision ID. The current repository already uses `b4c5d6e7f8a9`; Task 0 records the real post-Plan-08 head, Task 1 generates the lifecycle revision from it, and Task 3 generates a fresh unique evaluation revision from the merged Task 1 head with `alembic revision -m "add skill evaluation workbench"`.

### Evaluation Run state machine and worker

Evaluation is asynchronous and uses its own durable aggregate; it does not create a production `AssistantChatRun`:

~~~text
queued -> running -> completed | failed | cancelled
running -> queued       # stale compatible lease recovery
queued | running -> cancelling -> cancelled
~~~

Add to `assistant_skill_eval_run`:

- `state_revision`, `lease_owner`, `lease_generation`, `lease_expires_at`, `heartbeat_at`;
- `requested_cancel_at`, `started_at`, `ended_at`, `last_event_seq`;
- `runner_contract_version`, `required_build_revision`, `attempt_count`, `failure_code`;
- exact isolation, subject, dataset, policy, runtime, and Provider evidence digests.

Run `python -m app.assistant.evaluation.worker`. It reuses the proven Plan 06 claim/lease/CAS pattern but has an evaluation-specific repository/table and `RuntimeIsolationContext`; it must not claim production Run rows or emit production Run events. Claim uses PostgreSQL `FOR UPDATE SKIP LOCKED`, compatible build/contract checks, bounded attempts, heartbeat, cancellation checks before every Provider/Capability boundary, and deterministic stale-lease recovery. Interactive SSE replays `assistant_skill_eval_event` by sequence and is at-least-once; clients deduplicate by Eval Run ID plus sequence.

No API request holds an open Provider stream or in-process background task. If an evaluation worker is unavailable/incompatible, create-run admission fails before charging a live Provider or returns a stable unavailable error according to the endpoint contract; it never falls back to production execution.

---

## Public Contracts and Ownership

Create frozen server contracts for working copies, validation, preview/apply, evaluation, and gate subjects. The names below are normative unless a merged Plan 01 type already owns the same concept:

~~~python
class SkillWorkingSnapshot(FrozenContract):
    package_id: UUID
    expected_aggregate_revision: int
    base_version_id: UUID | None
    files: tuple[NormalizedPackageFile, ...]
    metadata: SkillPackageMetadata


class ImportPreviewToken(FrozenContract):
    preview_id: UUID
    actor_scope_digest: str
    mode: ImportMode
    target_package_id: UUID | None
    expected_aggregate_revision: int | None
    upload_digest: str
    candidate_content_digest: str
    expires_at: datetime


class EvalSubjectRef(FrozenContract):
    kind: EvalSubjectKind
    aggregate_id: UUID
    version_id: UUID
    content_digest: str
    resolved_binding_digest: str


class PublishGateSubject(FrozenContract):
    subject: EvalSubjectRef
    profile_digest: str
    catalog_digest: str
    runtime_contract_version: int
    policy_version: str
    threshold_version: str
    dataset_version_ids: tuple[UUID, ...]
    build_revision: str


class EvalExecutionIdentity(FrozenContract):
    eval_run_id: UUID
    eval_case_id: UUID
    namespace_id: UUID
    owner_kind: Literal["test"]
    subject_kind: EvalSubjectKind
    subject_aggregate_id: UUID
    subject_version_id: UUID


class CreatePublishGateRequest(FrozenContract):
    request_id: UUID
    subject: PublishGateSubject
    qualifying_eval_run_ids: tuple[UUID, ...]
    requested_non_safety_waiver_codes: tuple[str, ...]
    waiver_reason: str | None
~~~

`CreatePublishGateRequest` deliberately has no `passed`, `decision`, metric, assertion, or safety-override field. The server loads the referenced Eval Runs/evidence, recomputes the subject closure and thresholds, and derives `passed | failed | waived_non_safety`. A client cannot manufacture a passing gate by posting an assertion summary. Waiver codes must be empty with a null reason or name only currently failing non-safety assertions with a bounded nonempty reason and verified operator principal; unknown/safety/passing assertion codes are rejected. The configured distinct second approver for a system Skill remains a separate server-side gate-use requirement, not another client boolean.

Ownership:

- Plan 01 package service is the only writer of immutable package/Profile versions and digests.
- `SkillAdminService` owns aggregate revision/archive/catalog/alias mutations and calls Plan 01 services for content versions.
- `ImportPreviewService` owns temporary upload/preview tokens; apply calls Plan 01 package service atomically and consumes the token.
- `EvaluationRepository` is the only writer of Dataset/Eval Run/case/result/Eval CapabilityCall/event/Artifact/gate/gate-use tables.
- `EvaluationRunner` composes real runtime contracts under `RuntimeIsolationContext`; it never imports production business adapters directly.
- `PublishGateService` verifies evidence and appends gate-use evidence through `EvaluationRepository` inside package/Profile publish/catalog transactions. UI/API cannot mark a gate passed.
- Add architecture tests forbidding direct ORM writes to immutable version/evaluation/gate tables outside their repositories.

`EvalExecutionIdentity.owner_kind=test` identifies the execution namespace, not ownership of the Skill/Profile being evaluated. `EvalSubjectRef.aggregate_id/version_id` remains the separate immutable subject owner identity used for Manifest/policy/binding checks. Neither value is projected into a production Skill owner, Principal, Run, or CapabilityCall row.

### `assistant_skill_eval_dataset`

- UUID, stable key, display name/description, system/custom ownership.
- Mutable pointer to current immutable version and aggregate revision.
- Archived metadata; no physical delete when referenced.

### `assistant_skill_eval_dataset_draft`

- Exactly one mutable working draft per Dataset aggregate, unique Dataset FK.
- `draft_revision` CAS, schema version, complete normalized bounded case snapshot, and draft digest.
- Base published Dataset Version ID, updated-by/time, and last-validation summary digest.
- Draft rows are never referenced by Eval Runs or gates. Publish locks aggregate/draft, validates the full snapshot, appends one immutable Dataset Version plus Cases, advances the current-version pointer, and rebases the retained draft on that version in the same transaction.
- Concurrent `PUT /draft` requires `expectedDraftRevision`; there is no per-case partial merge or last-write-wins.

### `assistant_skill_eval_dataset_version`

- Dataset FK, monotonic sequence, version name, schema version.
- Immutable digest, created-by/time, optional source fixture revision.
- Append-only.

### `assistant_skill_eval_case`

- Dataset-version FK and stable case key/ordinal.
- Locale, input messages, sanitized fixture/setup references.
- Expected mode and acceptable/forbidden Skill package/version sets.
- Acceptable Capability sequences/sets and forbidden side-effect classes.
- Expected completion/obligation/stop/error assertions.
- Numeric ceilings for rounds/calls/tokens/latency where deterministic.
- Tags, manual notes, case digest; append-only through dataset version.

Datasets may not contain production credentials, authorization headers, raw secret config, or unapproved user conversation exports.

### `assistant_skill_eval_run`

- Subject kind `skill_draft | skill_version | main_agent_profile_draft | main_agent_profile_version | legacy_baseline` and exact subject/content/binding digests.
- Dataset version IDs and threshold-policy version.
- Mode `interactive_scripted | dataset_scripted | dataset_live`.
- Status, isolation namespace, runtime/build revision.
- Provider/model/probe/prompt/Profile/Skill digests when live.
- Aggregate metrics, gate eligibility, timestamps, actor.
- Cancel/lease fields if long runs use the durable worker.

### `assistant_skill_eval_case_result`

- Eval Run/case FKs, result state and assertion details.
- Actual active Skills, visible Capability aliases with owner evidence, call/interrupt/obligation trace, stop reason.
- Output/evidence Artifact references in the **evaluation namespace**.
- Rounds/calls/tokens/latency and safe errors.
- Append-only; unique run/case.

### `assistant_skill_eval_capability_call`

- Eval Run/case FK plus synthetic `eval_call_id`, logical call key, parent/child ordinal, attempt, exact subject owner/binding/input/descriptor/policy digests, and terminal `succeeded_isolated | simulated | denied | failed` outcome. `succeeded_isolated` is limited to evaluation-owned `none|compute|read` adapters; write classes can never use it.
- Records the Plan 08 planner/policy decision shape and owner evidence without inserting into or foreign-keying `assistant_capability_call`.
- `owner_kind=test` is fixed by the Eval execution identity; evaluated Skill/Profile ownership remains in separate subject fields.
- Unique Eval Run/case/logical-call-key/attempt constraints and repository CAS prevent recovery/replay from double-counting a simulated call. It is evidence, never executable authorization.
- It cannot contain production authorization evidence, approval tokens, idempotency keys accepted by a production adapter, raw arguments, credentials, or unredacted Provider payloads.

### `assistant_skill_eval_event` and `assistant_skill_eval_artifact`

- Eval Event uses Eval Run FK + monotonic sequence + safe typed payload; unique Run/sequence and append-only.
- Eval Artifact uses Eval Run FK, kind/media type/label, byte size/digest, and exactly one bounded inline payload or evaluation-prefixed object key.
- Evaluation object keys are server-generated in a namespace that production Run/conversation APIs cannot read.
- Neither table has a production conversation/message/L1/L2/CapabilityCall FK that could make a test result user-visible or terminal-memory eligible.
- Gate-referenced evidence follows the gate retention policy; ordinary interactive evidence may expire through an evaluation-only cleanup job.

Retention is reference-driven, not only age-driven:

- a qualifying gate pins its minimal evidence closure: exact Dataset Versions/Cases, Eval Run/Case Results/Eval Capability Calls, assertion snapshot, subject/runtime/policy/build digests, and any Artifact explicitly referenced by an assertion;
- gate expiry prevents future promotion but does not immediately unpin evidence;
- an evidence closure referenced by a publish/catalog/Profile-enable audit record is retained for the immutable publication-audit horizon in Plan 09; cleanup never deletes it merely because the gate expired;
- failed or never-used gates retain their closure through `gate.expires_at + ASSISTANT_SKILL_GATE_EVIDENCE_GRACE_DAYS`, after which only unreferenced high-volume events/non-assertion Artifacts may be deleted;
- cleanup locks/rechecks gate and publication references in the same transaction as deletion. A gate created or consumed concurrently wins and keeps the evidence.

### `assistant_skill_publish_gate`

- Subject Skill/Profile draft or published version ID plus content and resolved binding/candidate digest; publish gates target the owned draft candidate, while later enable gates may target the exact current published version.
- Exact dataset versions, qualifying eval Run IDs, runtime/build/policy/threshold versions.
- Decision `passed | failed | waived_non_safety`.
- Assertion/metric snapshot, actor, reason, created/expiry time.
- Decision is server-derived from evidence references; request payloads cannot persist it directly.
- Append-only.

### `assistant_skill_publish_gate_use`

- Append-only link from one qualifying gate to the exact action `skill_publish | skill_catalog_enable | profile_publish | profile_runtime_enable`, aggregate, resulting published version, actor principal, request ID, aggregate revision, and timestamp.
- Created in the same transaction as the publish pointer or live-enable transition; a gate row existing by itself is not proof that it authorized anything.
- Non-live observe/bootstrap and live-disabled legacy-shadow publishes append an explicit `ungated_non_live_publish` audit event instead of a fake gate use; those events are rejected for Catalog/Profile enable.
- Request/action uniqueness makes an identical retry return the original transition and prevents one request from recording two gate uses. Gate drift/expiry or action/subject mismatch rolls back the use row and transition together.

Safety assertions (`unauthorized call`, real side effect in test, secret exposure, schema escape, duplicate write, unresolved obligation falsely completed) must equal zero and cannot be waived. A non-safety waiver requires an explicit privileged actor/reason, is visible in publish history, and cannot catalog-enable a system Skill without the project’s configured second approval.

---

## Test Runtime Isolation Contract

Every workbench run uses an explicit `RuntimeIsolationContext`:

~~~python
class RuntimeIsolationContext(FrozenContract):
    namespace_id: UUID
    owner_kind: Literal["test"]
    subject_digest: str
    dataset_version_ids: tuple[UUID, ...]
    memory_mode: Literal["empty", "fixture"]
    data_mode: Literal["fixture", "read_snapshot"]
    data_snapshot_id: UUID | None
    snapshot_projection_policy_digest: str | None
    side_effect_mode: Literal["simulate_only"]
    event_namespace: Literal["evaluation"]
    artifact_namespace: Literal["evaluation"]
~~~

`data_mode=fixture` requires both snapshot fields to be null. `data_mode=read_snapshot` requires both fields, is default-disabled, and never means “give the runner a production Session.” A separately authorized snapshot builder creates an immutable evaluation-owned projection before the Eval worker can claim the Run. Each supported source has a versioned `SnapshotProjectionPolicy` with an explicit field allowlist, row/byte ceilings, actor scope, source revision, expiry, and policy digest. Wildcards and “all model columns except...” are forbidden.

The following values are hard-denied regardless of an allowlist entry: Credential/API-key/token/password/encrypted-secret fields; `Authorization`, cookie, and raw request/response headers; signed or presigned attachment/object URLs; storage credentials and unrestricted object keys; Provider request payloads; private identity/contact fields; and any field classified as secret/private by the repository data policy. Unknown source types or fields fail snapshot creation. If useful content cannot be projected without a private field, use a sanitized fixture instead.

Snapshot construction and Eval output are separate defenses. The builder copies only the allowed projection into the evaluation namespace and records policy/source digests; event/Artifact/assertion writers then apply bounded schema allowlists and secret canaries again. Rejected values must not appear in Eval events, Artifacts, safe errors, gate snapshots, or logs.

Server invariants:

- Never create/update production conversations, messages, L1/L2 memory, terminal memory commits, chat events, or production Artifacts.
- Resolve the candidate draft explicitly; it does not enter the production Catalog or alter the default Profile.
- `owner_kind=test` never passes a production write grant, and `ASSISTANT_MAIN_AGENT_WRITE_MODE=golden` is ignored for evaluation. Eval write simulation must work when the production flag is `off`, and enabling that flag must not change Eval behavior.
- `none`, `compute`, and authorized `read` calls use evaluation-owned/fixture adapters only. Intercept `draft`, `write_local`, and `write_external` before production adapter lookup, return a deterministic simulated result/approval path, and record the intent in `assistant_skill_eval_capability_call`. Deny `unknown` before adapter lookup; never turn unknown behavior into simulated success.
- Every nested Workflow/Agent child Capability returns through the same isolation-wrapped Gateway/dispatcher. Evaluation has no inner raw Tool invoke, direct business-service shortcut, or nested production Session escape hatch.
- Architecture tests reject an `EvaluationRunner` dependency path to `app.entry.service.EntryService`, production write/external adapters, the production CapabilityCall repository/table writer, production Run repositories, or production event/Artifact/memory writers.
- Runtime tripwires sit at production adapter factories and durable writers. If an Eval scope reaches `EntryService.create()`/commit, production CapabilityCall insert, production Run/L1/L2/message/event/Artifact write, outbox/queue/object-store production prefix, or a production write adapter, abort the Eval Run as `isolation_breach`, mark it permanently gate-ineligible, and emit only a safe evaluation error. A metric increment is not sufficient.
- Do not rely on “open a transaction and roll it back” as isolation: remote calls, queues, indexes, object storage, and nested Sessions can escape it.
- Read fixtures are isolated records. An explicitly authorized `read_snapshot` reads only the prebuilt immutable allowlisted projection; the runner never queries the production source directly.
- Test Provider credentials use the normal credential resolver but are never copied into result payloads.
- Cleanup is retention-based deletion of the isolated evaluation namespace only; referenced gate evidence is retained.

The workbench uses the real parser, Catalog search/injection, prompt layering, aliasing, policy, budgets, obligations, Provider loop, CapabilityCall planning, and completion logic. It records synthetic Eval call identity/evidence instead of a production ledger row. Only side-effect/data adapters and data/memory/event/Artifact/call namespaces are replaced; planner and policy decisions remain byte-contract compatible with Plans 05/08.

---

## Evaluation and Publish Gates

### Required suites

- Contract/security validation for every candidate.
- Candidate-specific positive, negative, ambiguous, direct-answer, and duplicate-ownership cases.
- Shared regression suite for Skill recall/injection and Capability path.
- Side-effect/approval/obligation denial simulations for any candidate declaring `draft` or write classes.
- Main Agent Profile suite across every catalog cohort it can expose.

The initial shared thresholds remain those locked in Plan 04:

- appropriate Skill recall at Top-8 ≥ 0.90;
- false Skill injection ≤ 0.05;
- direct-answer/no-Skill accuracy ≥ 0.90;
- acceptable Capability path ≥ 0.85;
- completion success no worse than legacy by more than 0.02;
- unauthorized Capability calls = 0.

Add hard invariants from Plans 05–08:

- budget/recursion/policy bypasses = 0;
- false completion with pending obligations = 0;
- real workbench side effects = 0;
- duplicate simulated write dispatches = 0;
- secret-bearing trace/resource output = 0.

Deterministic scripted evaluation is required in CI and for every gate. Optional live-model evaluation records exact model capability probe, model ID/revision, prompt/version/build digests and never becomes an unpinned default CI dependency. The publish policy may require a recent live run for a configured high-risk/system cohort; if required, absence or drift fails closed.

A gate is invalid when candidate bytes, resolved target versions/config revisions, Profile/prompt, runtime contract, threshold policy, dataset version, required model probe, or build revision changes.

Gate enforcement rolls out through temporary `ASSISTANT_SKILL_PUBLISH_GATE_MODE=observe|enforce`. `observe` exists only to import the Plan 04 fixtures, run fresh evaluations, create matching gates for every already live-enabled package/Profile, and—while an aggregate is still live-disabled—permit an auditable non-live bootstrap publish. It never permits an ungated Catalog/Profile enable **or an ungated published-pointer advance on an already enabled aggregate**. Before this plan exits, switch to `enforce`, require `gateId` for every native publish/promotion, and remove any operational dependence on observe mode. Legacy shadow sync remains exempt only while its output is Catalog/runtime-disabled.

Gate use is transactional and exact:

1. lock the Skill/Profile aggregate and verify expected aggregate revision;
2. determine the live flag before any write; if already enabled, gate is mandatory regardless of observe/enforce;
3. rebuild the candidate closure from exact bytes, bindings, Profile/Catalog, policy/threshold/dataset/runtime/build/probe evidence;
4. load a non-expired server-derived qualifying gate and compare the complete closure;
5. append the immutable publish row and matching `assistant_skill_publish_gate_use`, then advance the pointer; or append the gate use and enable the exact current published version;
6. on any mismatch, roll back every row/pointer/flag change and return a stable stale/missing-evidence conflict.

The required regression is explicit: with `catalog_enabled=true`, call publish for a new owned draft without `gateId` in `observe`; assert failure, unchanged aggregate revision/published pointer, and byte-identical subsequent Plan 04 Catalog snapshot/digest. Repeat for `runtime_enabled=true` Main Agent Profile.

---

## API Additions

Under `/api/assistant-config`:

~~~text
GET    /skill-packages
POST   /skill-packages
GET    /skill-packages/{id}
PATCH  /skill-packages/{id}/metadata
POST   /skill-packages/{id}/aliases
POST   /skill-packages/{id}/aliases/{alias_id}/disable
POST   /skill-packages/{id}/archive
POST   /skill-packages/{id}/unarchive
POST   /skill-packages/{id}/catalog/enable
POST   /skill-packages/{id}/catalog/disable
POST   /skill-packages/{id}/drafts
PUT    /skill-packages/{id}/drafts/{version_id}
POST   /skill-packages/{id}/validate
POST   /skill-packages/{id}/versions/{version_id}/publish
GET    /skill-packages/{id}/versions
GET    /skill-packages/{id}/versions/{version_id}
GET    /skill-packages/{id}/versions/{left}/diff/{right}
POST   /skill-packages/{id}/versions/{version_id}/restore-draft
GET    /skill-packages/{id}/versions/{version_id}/resources/{path}
GET    /skill-packages/{id}/versions/{version_id}/export
POST   /skill-packages/import/preview
POST   /skill-packages/import/apply

GET|POST /main-agent-profiles
GET|PATCH /main-agent-profiles/{id}
POST      /main-agent-profiles/{id}/drafts
PUT       /main-agent-profiles/{id}/drafts/{version_id}
POST      /main-agent-profiles/{id}/versions/{version_id}/publish
GET       /main-agent-profiles/{id}/versions
GET       /main-agent-profiles/{id}/versions/{left}/diff/{right}
POST      /main-agent-profiles/{id}/versions/{version_id}/restore-draft

GET|POST /skill-eval/datasets
GET|PUT  /skill-eval/datasets/{id}/draft
POST     /skill-eval/datasets/{id}/publish
POST     /skill-eval/runs
GET      /skill-eval/runs/{id}
GET      /skill-eval/runs/{id}/events
POST     /skill-eval/runs/{id}/cancel
POST     /skill-eval/gates
GET      /skill-eval/gates/{id}
~~~

Contract rules:

- List endpoints are cursor/pagination bounded and return metadata only. Resource bytes, full instructions, traces, and result payloads require an exact detail/resource endpoint.
- Every mutation accepts `requestId` plus the relevant expected aggregate/version/Run revision. Identical retry returns the persisted outcome; altered reuse conflicts.
- Draft save bodies are complete normalized snapshots, not JSON Patch or partial file merge.
- Resource paths are server-normalized identifiers from the immutable version; raw filesystem paths are never accepted.
- Event replay uses `afterSequence`, bounded page/stream size, heartbeat, and the existing safe SSE disconnect semantics.
- Cancel only records a CAS request; the worker observes it before the next boundary and commits the terminal state.
- Publish/catalog/gate/waiver actions recompute the exact candidate closure in the service transaction and return stable stale-evidence conflicts.
- `POST /skill-eval/gates` accepts only subject/evidence references, request ID, and optional requested non-safety waiver codes/reason. It rejects client-supplied `passed`, `decision`, assertions, metrics, or safety overrides; `PublishGateService` derives the decision.

Extend Plan 01 publish bodies with nullable `gateId` when Plan 09 lands, but enforce the lifecycle matrix above in the service—not in the router. Catalog/Profile enable requires an exact currently published version and qualifying non-expired promotion gate. All Plan 09 routes require the verified assistant-config principal described in the authorization decision; privileged waiver/catalog/system operations require its concrete operator/admin role. If the dependency does not exist, the Plan 09 router remains unmounted/default-disabled and absent from production OpenAPI; these operations and M4 release cannot be declared complete.

Responses use bounded summaries and Artifact/resource fetch endpoints; never return all resource bytes, raw Provider payloads, secrets, or unrestricted traces in list APIs.

---

## File Responsibility Map

### Create: backend

- `backend/app/assistant/skills/admin_service.py`
- `backend/app/assistant/skills/diff.py`
- `backend/app/assistant/skills/import_preview.py`
- `backend/app/assistant/evaluation/__init__.py`
- `backend/app/assistant/evaluation/contracts.py`
- `backend/app/assistant/evaluation/models.py`
- `backend/app/assistant/evaluation/datasets.py`
- `backend/app/assistant/evaluation/isolation.py`
- `backend/app/assistant/evaluation/snapshots.py`
- `backend/app/assistant/evaluation/repository.py`
- `backend/app/assistant/evaluation/runner.py`
- `backend/app/assistant/evaluation/worker.py`
- `backend/app/assistant/evaluation/assertions.py`
- `backend/app/assistant/evaluation/gates.py`
- `backend/app/assistant/evaluation/artifacts.py`
- `backend/app/assistant/evaluation/router.py`
- `backend/app/assistant/evaluation/schemas.py`
- one generated Task 1 `backend/alembic/versions/<revision>_add_skill_package_admin_lifecycle.py`
- one generated Task 3 `backend/alembic/versions/<revision>_add_skill_evaluation_workbench.py`
- `backend/tests/test_agent_skill_admin_service.py`
- `backend/tests/test_agent_skill_diff_restore.py`
- `backend/tests/test_agent_skill_import_preview.py`
- `backend/tests/test_skill_eval_models.py`
- `backend/tests/test_skill_eval_isolation.py`
- `backend/tests/test_skill_eval_snapshot_policy.py`
- `backend/tests/test_skill_eval_runner.py`
- `backend/tests/test_skill_eval_worker.py`
- `backend/tests/test_skill_eval_repository_postgres.py`
- `backend/tests/test_skill_publish_gate.py`
- `backend/tests/test_skill_admin_api.py`

### Create: frontend

- `frontend/src/features/assistant-config/api/skill-packages.ts`
- `frontend/src/features/assistant-config/api/main-agent-profiles.ts`
- `frontend/src/features/assistant-config/api/skill-evaluations.ts`
- `frontend/src/features/assistant-config/pages/UniversalSkillSettings.tsx`
- `frontend/src/features/assistant-config/pages/UniversalSkillEditorPage.tsx`
- `frontend/src/features/assistant-config/pages/MainAgentProfileEditorPage.tsx`
- `frontend/src/features/assistant-config/components/UniversalSkillEditor.tsx`
- `frontend/src/features/assistant-config/components/SkillCapabilityEditor.tsx`
- `frontend/src/features/assistant-config/components/SkillPolicyEditor.tsx`
- `frontend/src/features/assistant-config/components/SkillResourceBrowser.tsx`
- `frontend/src/features/assistant-config/components/SkillVersionHistory.tsx`
- `frontend/src/features/assistant-config/components/SkillVersionDiff.tsx`
- `frontend/src/features/assistant-config/components/SkillTestWorkbench.tsx`
- `frontend/src/features/assistant-config/components/SkillEvaluationRun.tsx`
- `frontend/src/features/assistant-config/components/SkillPublishGateDialog.tsx`
- `frontend/src/features/assistant-config/stores/skill-editor-store.ts`
- `frontend/src/features/assistant-config/stores/skill-test-run-store.ts`

### Modify

- `backend/app/assistant/skills/models.py`
- `backend/app/assistant/skills/service.py`
- `backend/app/assistant/skills/package_io.py`
- `backend/app/assistant/skills/router.py`
- `backend/app/assistant/skills/schemas.py`
- `backend/app/assistant/main_agent/evaluation.py`
- `backend/app/assistant/main_agent/service.py`
- `backend/app/assistant/capabilities/gateway.py`
- `backend/app/assistant/capability_calls/dispatcher.py`
- `backend/app/main.py`
- `backend/tests/_db.py`
- `frontend/src/features/assistant-config/index.ts`
- `frontend/src/features/assistant-config/queries.ts`
- application route/navigation files discovered during implementation
- `.github/workflows/ci.yml`

Reuse the existing assistant-config query/editor primitives where they fit. Do not build a second generic Tool/Workflow/Agent editor inside the Skill editor; select their published Capability identities from the shared Registry.

At plan-authoring time the current UI anchors are `frontend/src/app/App.tsx`, `frontend/src/features/assistant-config/queries.ts`, `frontend/src/features/assistant-config/pages/SkillSettings.tsx`, and the existing Workflow/Agent versioning and test-run stores under the same feature. The current backend assistant-config surface is concentrated in `backend/app/assistant_config/router.py`, `schemas.py`, `service.py`, and `registry.py`; it currently has no user/admin dependency. Task 0 must recheck that fact and apply the locked authorization decision above: this plan does not call a route “admin” unless a server-enforced principal/role check actually exists.

---

## Execution Discipline and Release Slices

Implement this plan in four deployable slices; each slice must leave production Catalog/runtime behavior valid:

1. **09A — aggregate lifecycle/import:** Tasks 0–2. New APIs remain feature-disabled; no evaluation schema or frontend dependency is required.
2. **09B — evaluation isolation/gates:** Tasks 3–5. Scripted evaluation becomes a CI gate. Compatibility publish is restricted to explicitly live-disabled aggregates; an already enabled Skill/Profile cannot advance its published pointer without matching evidence even in observe.
3. **09C — UI:** Tasks 6–7. Universal pages are separately routed; legacy pages remain available.
4. **09D — enforcement:** Tasks 8–9. Only after every currently enabled native package/Profile has fresh matching evidence does publish/catalog gate mode become `enforce`.

Rules:

- Write tests red first and commit each task independently. Backend contracts land before frontend consumers.
- Use the exact Plan 01 parser, canonical bytes, digest factory, alias resolver, binding resolver, and immutable version service. A UI helper may preview but never define acceptance.
- PostgreSQL 15 is mandatory for append-only triggers, aggregate revision races, gate references, migration, and retention tests. Browser/component tests cannot substitute for server enforcement.
- Interactive/dataset evaluation never uses production conversation, Run, memory, event, Artifact, CapabilityCall, outbox/object-key, or business-data namespaces. Tests compare table/object-key deltas and exercise production-writer tripwires, not just mocked method calls.
- `scripts/` remains inert bytes. No subprocess, dynamic import, shell, eval, Worker, iframe execution, or “test script” endpoint may be introduced.
- If no authenticated assistant-config principal/role boundary exists after Plan 08, the entire new Plan 09 router stays unmounted/default-disabled and absent from production OpenAPI; privileged service calls also reject a missing verified principal. Trusted test/dev mounting is not M4 release evidence.
- The temporary observe mode can create evidence for already enabled native versions and can bootstrap a disabled aggregate, but it cannot advance the pointer of an enabled aggregate or catalog/runtime-enable an ungated version. Plan exit requires enforce mode in examples/tests and no operational dependency on observe.
- Every task runs its focused backend/frontend tests and `git diff --check`; Task 9 runs full clean verification.

---

## Task 0: Baseline and UX/API Inventory

**Files:** read merged Plans 01–08 owners plus the current anchors listed above, `frontend/src/components/layout` navigation owners, `backend/app/main.py`, `backend/app/config.py`, deployment examples, OpenAPI tests, and PostgreSQL helper. Modify only this plan for factual corrections.

**Produces:** a route/query/auth/feature inventory, exact post-Plan-08 contract names, and a no-code slice plan.

- [ ] **Step 1: Record repository/toolchain state.** Capture Git/HEAD, Python/Node/npm, dependency locks, one Alembic head, build revision, current workers, and all relevant flag defaults.
- [ ] **Step 2: Prove Plans 01–08 contracts.** Trace package parse/import/version/publish, Plan 04 `catalog_enabled + current published_version_id` recall, policy/budget/obligation, durable Run/interrupt, and ledger behavior through exact merged symbols. Record Plan 02B observation status as non-blocking coordination evidence. Stop on mutable or duplicate sources of truth.
- [ ] **Step 3: Run backend/frontend baselines.** Run the focused suites, current assistant-config tests, query tests, frontend build, and deterministic Plan 04 evaluation; record unexplained baseline failures.
- [ ] **Step 4: Freeze API/auth conventions.** Snapshot OpenAPI/envelopes/error codes and identify the exact authenticated principal dependency and privileged role check. The current baseline is none. Record the exact Task 1 startup/OpenAPI/service tests that must prove the Plan 09 router is unmounted in staging/production and missing/fake principals are rejected; do not substitute a header boolean, loopback, Origin, CORS, or feature flag.
- [ ] **Step 5: Freeze frontend integration points.** Record App routes, settings navigation, query keys/invalidation, stores, notifications, i18n, responsive and accessibility patterns, and reusable Workflow/Agent version/test components.
- [ ] **Step 6: Prove script/write safety baseline.** Search subprocess/dynamic-import/eval/iframe/Worker paths; map nested Workflow/Agent raw-invoke paths and every future tripwire site: `EntryService.create/commit`, production ledger/Run/memory/event/Artifact writers, outboxes/queues/object prefixes, and production side-effect adapters. Record the Task 4 test matrix and that evaluation write classes must simulate with `ASSISTANT_MAIN_AGENT_WRITE_MODE=off` and remain unchanged when it is `golden`.
- [ ] **Step 7: Record migration IDs and heads.** Prove `b4c5d6e7f8a9` is occupied and capture the sole Plan 08 head from which Task 1 will generate its lifecycle migration.
- [ ] **Step 8: Lock release controls.** Define 09A–09D flags, compatible worker behavior, rollback action, router mount/auth prerequisites, the enabled-aggregate publish invariant in both gate modes, and which APIs/UI remain invisible at each slice.

~~~bash
git status --short
git rev-parse --short HEAD
cd backend && .venv/bin/alembic heads && cd ..
rg -n 'b4c5d6e7f8a9' backend/alembic/versions
backend/.venv/bin/python -m pytest \
  backend/tests/test_assistant_config_service.py \
  backend/tests/test_assistant_config_service_more.py -q
npm --prefix frontend run test -- --run \
  src/features/assistant-config/queries.test.tsx
npm --prefix frontend run build
~~~

Stop and amend Plan 01 if exact package bytes/digest/import security cannot be reused, or the relevant owner plan if evaluation isolation would require bypassing its runtime contract.

**Commit:** no runtime commit. Commit only factual plan corrections or missing baseline characterization tests.

---

## Task 1: Add Aggregate Admin and Append-Only Restore APIs

**Files:** modify merged Skill package/alias models and schemas; create `skills/admin_service.py`, `skills/diff.py`, and the generated Task 1 lifecycle migration; modify Skill router/service/PostgreSQL migration tests.

**Produces:** revision-CAS aggregate metadata/archive/catalog/alias operations, bounded diff, and restore-as-new-draft. It does not change production Catalog visibility by itself.

- [ ] **Step 1: Write red lifecycle migration tests.** Cover new columns/defaults/checks/FKs, existing rows, aliases, archive/catalog invariants, downgrade guards, and one-head behavior on PostgreSQL.
- [ ] **Step 2: Generate and implement the Task 1 migration.** Generate from the exact Plan 08 head; add lifecycle columns in an additive/backfill/finalize sequence and assert unique revision/filename.
- [ ] **Step 3: Write red service/API/auth race tests.** Exact aggregate revision, concurrent metadata/save/archive, alias reservation/disable, archive effects, catalog constraints, `requestId` retry, and no physical DELETE. Also prove the parent router is absent from staging/production OpenAPI without a real guard and missing/fake/non-operator principals cannot invoke protected/privileged service transitions.
- [ ] **Step 4: Implement the aggregate and mount boundary.** One service/repository owns mutations, requires the verified principal (and operator role for privileged transitions), and returns new aggregate revision plus immutable pointer IDs; no router writes ORM objects directly. Put every Plan 09 subrouter behind the conditionally mounted parent router and keep it absent when the guard dependency is unavailable.
- [ ] **Step 5: Implement archive/catalog/alias semantics.** Archive disables new recall/publish/catalog; unarchive never re-enables automatically; canonical/legacy aliases are protected and disabled custom names remain reserved.
- [ ] **Step 6: Implement bounded diff.** Use normalized immutable metadata and bounded text hunks. Resource bytes, secrets, Provider payloads, and unbounded instruction bodies are excluded.
- [ ] **Step 7: Implement restore-as-new-draft.** Copy portable content/resources with provenance, rerun Plan 01 validation, append a draft, and leave published pointer/history untouched.
- [ ] **Step 8: Run migration and API gates.** Parent -> Task1 head -> parent -> head plus two-session CAS tests and OpenAPI proof of no DELETE/in-place immutable update.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_skill_admin_service.py \
  backend/tests/test_agent_skill_diff_restore.py \
  backend/tests/test_skill_admin_api.py -q
git diff --check
~~~

**Commit:** `feat(ai): add skill package admin lifecycle`

---

## Task 2: Add Safe Import Preview/Append/Fork

**Files:** create `skills/import_preview.py`; modify Plan 01 `package_io.py`, service/router/schemas, and import/export tests.

**Produces:** two-step `preview -> apply` for create/append/fork with an exact preview token/digest.

- [ ] **Step 1: Write the malicious archive corpus.** Traversal, absolute path, symlink/special file, duplicate normalized path, ZIP ratio/depth/count/size, spoofed MIME, executable bit, active HTML/SVG/script, stale bytes/revision, alias/name collision, and fork rewrite.
- [ ] **Step 2: Confirm red preview/apply tests.** Expected failures are missing two-step service/modes, not Plan 01 parser failures. Existing Plan 01 create-only security stays green.
- [ ] **Step 3: Implement bounded upload and preview token.** Stream to bounded temporary storage, hash exact bytes, bind actor/scope/mode/target revision/digests/expiry, and never unpack outside the controlled parser.
- [ ] **Step 4: Reuse Plan 01 acceptance.** Call the exact parser/path/MIME/digest/resolution services; add an architecture test rejecting duplicate normalization/security code.
- [ ] **Step 5: Implement explicit modes.** Append replaces a complete snapshot without file merge. Fork rewrites only the standard name field and reruns all checks.
- [ ] **Step 6: Implement atomic idempotent apply.** Recheck token/bytes/revision, append one unpublished/catalog-disabled draft, consume `requestId`/preview once, and return the persisted result on identical retry.
- [ ] **Step 7: Verify deterministic export.** Canonical ordering/timestamps/permissions/digests; `scripts/` bytes preserved but executable permission absent.
- [ ] **Step 8: Run resource/secret/log safety checks.** No temp path, raw archive, credential, resource body, or unbounded diff leaks into list responses/events/logs.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_agent_skill_import_preview.py \
  backend/tests/test_agent_skill_package_io.py -q
git diff --check
~~~

Use the exact merged Plan 01 package-I/O test filename if it differs.

**Commit:** `feat(ai): add safe skill package preview and apply`

---

## Task 3: Add Evaluation Models, Dataset Versioning, and Migration

**Files:** create evaluation contracts/models/dataset/artifact repositories, the generated migration, PostgreSQL tests, and a deterministic fixture importer. Modify model/bootstrap imports only.

**Produces:** persistence and fixture import only. No Provider run, publish gate enforcement, or UI.

- [ ] **Step 1: Write red evaluation schema tests.** Cover immutable datasets/cases/results/events/gates/gate uses, eval-only CapabilityCall identity/attempt uniqueness, Eval Run state/lease/revisions, monotonic sequence, one result per case, separation of execution `owner_kind=test` from subject ownership, digest refs, gate-use action/request uniqueness, Artifact payload XOR, retention, and FK delete rules.
- [ ] **Step 2: Generate Task 3 migration from Task 1 head.** Assert unique revision/filename and exactly one Alembic head; never descend directly from Plan 08 after Task 1 exists.
- [ ] **Step 3: Implement schema/triggers/repositories.** Add explicit transition/check constraints and append-only/immutable triggers, including `assistant_skill_eval_capability_call` with synthetic IDs/no production ledger FK and `assistant_skill_publish_gate_use` for exact transactional promotion evidence. EvaluationRepository is the only writer and requires expected revisions.
- [ ] **Step 4: Enforce evaluation namespaces.** Service and persisted metadata validate object-key prefix; normal production Artifact/Event/Run/CapabilityCall APIs reject evaluation IDs and keys. Architecture tests reject production Run/CapabilityCall FKs and production writer imports.
- [ ] **Step 5: Import Plan 04 dataset deterministically.** Preserve the checked-in fixture, fixed dataset/version/case digests, and idempotent rerun.
- [ ] **Step 6: Implement retention protection.** Pin the exact minimal gate evidence closure; gate expiry alone does not unpin publication-used evidence. Failed/unused evidence observes `gate.expires_at + ASSISTANT_SKILL_GATE_EVIDENCE_GRACE_DAYS`; race cleanup against gate creation/consumption under locks and delete only unreferenced high-volume evidence.
- [ ] **Step 7: Implement guarded downgrade.** Workers stopped, no queued/running Runs, retained evidence exported, no published/catalog dependency, and explicit acknowledgment.
- [ ] **Step 8: Run PostgreSQL migration cycle.** Task1 head -> Task3 head -> Task1 head -> Task3 head with preexisting packages/Profiles and retained evaluation fixtures.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_skill_eval_models.py \
  backend/tests/test_skill_eval_repository_postgres.py -q
cd backend && .venv/bin/alembic heads && cd ..
git diff --check
~~~

**Commit:** `feat(ai): add skill evaluation persistence`

---

## Task 4: Implement the Isolated Interactive Test Runtime

**Files:** create evaluation isolation/runner/assertion/event/artifact modules and tests; add narrow injection seams to Catalog, memory/data providers, Gateway/ledger dispatcher, and event/Artifact stores.

**Produces:** `interactive_scripted` runs over real orchestration contracts with evaluation namespaces and simulated side effects.

- [ ] **Step 1: Write red isolation-delta/dependency tests.** Snapshot production conversations/messages/Runs/L1/L2/events/Artifacts/CapabilityCalls/business rows/outboxes/object keys before success/failure/cancel/crash/malicious/nested runs and require zero delta. Reject EvaluationRunner import paths to `EntryService`, production side-effect adapters, and production Run/ledger/memory/event/Artifact writers.
- [ ] **Step 2: Make isolation context and identity mandatory.** Propagate `RuntimeIsolationContext + EvalExecutionIdentity` in typed execution scope; missing, production, mismatched, mixed namespace, or conflated subject/test ownership fails before Provider/Gateway construction.
- [ ] **Step 3: Implement fixture/snapshot resolution.** Resolve a candidate draft explicitly without Catalog mutation. Fixtures are evaluation-owned. `read_snapshot` is default-disabled, prebuilt from a versioned per-source field allowlist, immutable, bounded, and hard-denies credentials/encrypted values/auth/cookies/raw headers/signed URLs/private fields. Add output canaries proving those values never enter event/Artifact/gate evidence.
- [ ] **Step 4: Install isolation before all dispatch.** Route every top-level and nested Workflow/Agent child through the isolation-wrapped Gateway. Use eval adapters for allowed read/compute, simulate draft/write locally in the Eval namespace, deny unknown, and reject inner raw invokes/direct business-service shortcuts.
- [ ] **Step 5: Add hard production tripwires.** Touching `EntryService.create/commit`, a production adapter, production CapabilityCall/Run/memory/event/Artifact writer, outbox/queue, or production object prefix aborts as gate-ineligible `isolation_breach`; it cannot be downgraded to a metric. Prove production `off|golden` write flag values yield identical Eval results.
- [ ] **Step 6: Implement evaluation repository/worker.** Claim with SKIP LOCKED and compatible build/contract, heartbeat, lease owner/generation/expiry, CAS revisions, bounded retry, cancellation, stale lease recovery, synthetic Eval calls, and no in-process API background task.
- [ ] **Step 7: Implement replay and prove recovery.** Bounded typed events, safe trace redaction, monotonic sequence, SSE reconnect/dedup, and not-found from normal production endpoints. Kill before/after Provider and simulated/nested Capability boundaries; resume only Eval state and do not double-count logical calls.
- [ ] **Step 8: Prove unavailable behavior.** No compatible evaluation worker fails admission safely and never falls back to production or charges a live Provider.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_skill_eval_isolation.py \
  backend/tests/test_skill_eval_snapshot_policy.py \
  backend/tests/test_skill_eval_runner.py \
  backend/tests/test_skill_eval_worker.py -q
git diff --check
~~~

**Commit:** `feat(ai): run skill tests in isolated evaluation worker`

---

## Task 5: Implement Dataset Evaluation and Gates

**Files:** create `evaluation/assertions.py` and `gates.py`; modify evaluation runner/router/schemas, Skill/Profile publish services, config/env/CI, and tests.

**Produces:** deterministic CI evaluation and exact-evidence publish/catalog enforcement.

- [ ] **Step 1: Write red assertion vectors.** Typed pass/fail/indeterminate outcomes for recall, false injection, direct answer, Capability path, completion, policy/budget/obligation, duplicate simulated dispatch, secret canary, and all zero-tolerance safety counters.
- [ ] **Step 2: Implement deterministic aggregation.** Missing evidence is fail/indeterminate, never pass; hard safety assertions cannot be waived or averaged away.
- [ ] **Step 3: Freeze reproducibility closure.** Dataset cases run against exact subject/Profile/Catalog/binding/policy/runtime/build/fixture digests and persist secret-free evidence sufficient to reproduce.
- [ ] **Step 4: Add isolated legacy baseline.** Compare typed assertions/metrics, not prose equality, and prove zero production mutation.
- [ ] **Step 5: Add explicit live mode.** Require a current compatible probe, actor confirmation, cost/deadline bound, and recorded model/config evidence. CI remains scripted/network-disabled.
- [ ] **Step 6: Implement server-derived gate service.** Accept only subject/evidence refs plus optional requested non-safety waiver codes/reason; reject client decision/metric/assertion fields. Recompute candidate closure, load qualifying Eval Runs/datasets/thresholds/expiry, and derive/append pass, fail, or allowed non-safety waiver evidence.
- [ ] **Step 7: Enforce gates transactionally.** Publish and Catalog/Profile enable recompute once more under aggregate lock immediately before pointer/state change and append exact gate-use evidence in the same transaction. In both modes an already `catalog_enabled`/`runtime_enabled` aggregate requires a fresh matching gate to advance its published pointer; rejection leaves gate-use rows, revision, pointer, and Catalog/Profile snapshot inputs unchanged.
- [ ] **Step 8: Execute observe -> enforce migration.** Observe permits ungated publish only for explicitly live-disabled bootstrap/migration aggregates; it never permits ungated enable or an ungated pointer advance on an already enabled aggregate. Evaluate/gate all already enabled native packages/Profiles, then change examples/tests to enforce.
- [ ] **Step 9: Run drift/non-waiver/live-pointer matrix.** Drift each digest/version/build/policy/dataset/probe independently and prove invalidation; prove every hard safety failure blocks all waiver paths. In observe, publish an enabled Skill/Profile without a gate and assert no publish row, pointer/revision change, or Catalog/Profile digest change; prove disabled bootstrap remains non-live until a separately gated enable.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_skill_eval_runner.py \
  backend/tests/test_skill_publish_gate.py \
  backend/tests/test_skill_admin_api.py -q
git diff --check
~~~

**Commit:** `feat(ai): enforce reproducible skill publish gates`

---

## Task 6: Build Universal Skill List and Editor

**Files:** create the listed package APIs/pages/editor/store/components; modify `frontend/src/app/App.tsx`, settings navigation, feature exports/queries/i18n, and frontend tests.

**Produces:** a separately feature-gated Universal Skills list/editor. Legacy `SkillSettings` remains routed and unchanged except for a clear legacy label/link.

- [ ] **Step 1: Write red API/query contract tests.** Pin request/response types, central query keys, pagination, revision/request IDs, error mapping, and exact invalidation sets.
- [ ] **Step 2: Implement typed API clients.** Use existing envelope/error conventions; no component calls `fetch` directly or invents another cache key.
- [ ] **Step 3: Add separately gated routes/navigation.** Lazy list/editor routes require both the server-reported feature and authenticated assistant-config principal. If the backend router is unmounted, navigation is absent and direct URLs fail closed; preserve legacy `SkillSettings`.
- [ ] **Step 4: Implement one working-copy store.** Key by package/draft; support dirty guard, validation diagnostics, save, stale conflict, explicit reload/compare/restore, and identical retry.
- [ ] **Step 5: Implement editor sections.** Overview/instructions/applicability/capabilities/policy/budgets/completion/resources with server-owned validation and no embedded target editor.
- [ ] **Step 6: Implement safe resource preview.** Escaped bounded text, validated raster object URLs with cleanup/CSP, download-only HTML/SVG/binary, inert script badge, no execution control.
- [ ] **Step 7: Implement list actions safely.** Import/export/archive/catalog status/actions honor server capability/principal/role/revisions and render 401/403/unmounted/conflict states without destructive local overwrite. UI state never supplies authority.
- [ ] **Step 8: Run interaction/accessibility/responsive tests.** Keyboard/focus/error summary, mobile/narrow, loading/empty/403/404/409/422/500, and production Catalog invisibility for Draft/archived/non-enabled packages.

~~~bash
npm --prefix frontend run test -- --run \
  src/features/assistant-config
npm --prefix frontend run build
git diff --check
~~~

**Commit:** `feat(ai): add universal skill editor`

---

## Task 7: Build Versions, Profile Editor, and Workbench UI

**Files:** create the listed version/Profile/workbench/evaluation/gate components and stores; modify routes/query clients/i18n and component tests.

**Produces:** complete 09C UI over the already enforced backend contracts.

- [ ] **Step 1: Write red version/Profile/workbench route tests.** Pin lazy routes, query states, reconnect/cancel, stale conflicts, and server authorization errors.
- [ ] **Step 2: Implement immutable history/diff/restore/export.** Render source/provenance/digests/bindings; restore is visibly a new Draft, never pointer rewind.
- [ ] **Step 3: Implement Profile working copy.** Prompt layers, Catalog scope, control Capabilities, model binding, budgets, completion, feature mode, versions; assert no single-target fields in UI/API payload.
- [ ] **Step 4: Implement workbench event client/store.** Replay after sequence, deduplicate, reconnect, cancel request, terminal reconciliation, and sanitized owner-qualified trace/assertions.
- [ ] **Step 5: Implement dataset/run result views.** Bounded case summaries, filters, metrics, evidence links, retention/expiry, and no raw secret/resource/provider payload.
- [ ] **Step 6: Implement gate dialog.** Submit only evidence refs and optional requested non-safety waiver codes/reason. Clearly separate hard safety failure, non-safety waiver, expiry/drift, and second approval; never submit/compute `passed`, assertions, metrics, or waiver eligibility client-side.
- [ ] **Step 7: Implement conflict UX.** Preserve local work on 409 and offer compare/reload/restore; stale/archived controls disabled only as UX, not security.
- [ ] **Step 8: Run component/store/query/route/a11y tests and build.** Reuse existing test-run patterns only after cancellation/reconnect semantics match exact Eval API.

~~~bash
npm --prefix frontend run test -- --run \
  src/features/assistant-config
npm --prefix frontend run build
git diff --check
~~~

**Commit:** `feat(ai): add skill evaluation workbench`

---

## Task 8: End-to-End Security and Publication Verification

**Files:** end-to-end API/UI fixtures and security tests only, plus fixes in the owning module when a test exposes a defect.

**Produces:** reviewable evidence that 09A–09C compose safely before enforcement exit.

- [ ] **Step 1: Build the golden admin fixture.** Multiple Capability types, positive/negative examples, reference, raster asset, inert script, budgets, policy, and completion obligations with fixed expected digests.
- [ ] **Step 2: Run lifecycle/Eval-gate E2E.** Create -> save -> validate -> interactive -> dataset -> server-derived gate -> publish -> Catalog enable; query Catalog after every step and prove visibility starts only at final allowed transition. Posting client-authored pass/metrics/assertions is rejected.
- [ ] **Step 3: Run portability/history E2E.** Canonical export/digest, create conflict, append, fork, stale preview, restore-as-new-draft, and immutable history/pointers.
- [ ] **Step 4: Run malicious package E2E.** Full archive/MIME/HTML/SVG/script/secret corpus; assert rejection or inert rendering and zero secret in API/event/log snapshots.
- [ ] **Step 5: Run isolation E2E.** Every simulated side-effect/approval/input/cancel/reconnect/nested attempt with production write mode both `off` and `golden`; compare production DB/Run/ledger/object/outbox counts and digests for zero change. Force each production-writer tripwire and require terminal gate-ineligible `isolation_breach`.
- [ ] **Step 6: Run snapshot confidentiality E2E.** Seed API key/encrypted credential/raw Authorization+Cookie headers/private identity and signed attachment URL canaries; attempt direct and nested reads and assert none enters Eval events, Artifacts, results, gate records, logs, or normal production endpoints.
- [ ] **Step 7: Run two-client/live-pointer races.** Save/publish/archive/catalog/gate invalidation and request retry produce exact CAS/idempotent outcomes. In observe, an enabled package/Profile publish without gate loses atomically and leaves pointer/revision/live snapshot digest unchanged.
- [ ] **Step 8: Verify auth/feature boundaries.** With no real guard, staging/production OpenAPI has no Plan 09 routes and direct service privilege calls reject. With a test guard, unauthenticated/non-operator callers get 401/403 for all protected/privileged surfaces; feature/UI off remains unavailable without breaking legacy pages/runtime.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_skill_admin_api.py \
  backend/tests/test_skill_eval_isolation.py \
  backend/tests/test_skill_eval_snapshot_policy.py \
  backend/tests/test_skill_publish_gate.py -q
npm --prefix frontend run test -- --run src/features/assistant-config
git diff --check
~~~

**Commit:** `test(ai): verify universal skill admin safety`

---

## Task 9: Final Verification

**Files:** CI selection/config, final OpenAPI/architecture/security snapshots, handoff fixture/report templates, and owning-module fixes only. Do not introduce new lifecycle or UI behavior here.

**Produces:** an enforce-mode M4 release candidate plus exact migration/evaluation evidence required by Plan 10. It becomes an M4 release only when the real principal/operator guard is present; otherwise the router remains unmounted and Task 9 records a release blocker.

- [ ] **Step 1: Run all focused/PostgreSQL tests.** Both migrations, triggers, CAS races, worker recovery, retention/gate races, import security, isolation, API, and frontend stores/components.
- [ ] **Step 2: Run full clean verification.** Full backend, frontend tests/build, clean Python 3.11 install/`pip check`, and deterministic network-disabled evaluation.
- [ ] **Step 3: Verify persisted quality/safety gates.** Plan 04 thresholds and Plan 05–08 hard invariants come from reproducible Eval evidence, not a manual chat. Every production-used gate retains its minimal evidence closure; cleanup respects gate/publication pins and grace.
- [ ] **Step 4: Finish enforce cutover.** Every enabled native package/Profile has a current gate; examples/tests use `enforce`; new ungated publish/Catalog/Profile enable fails. Re-run the observe enabled-pointer regression before removing operational dependence on observe.
- [ ] **Step 5: Verify compatibility rollback.** Universal visibility off preserves legacy page/runtime; on exposes both without rewriting legacy rows.
- [ ] **Step 6: Audit OpenAPI/imports/storage.** No immutable update/delete, physical package delete, script execution, client-authored gate decision, unauthenticated Plan 09/privileged surface, direct EvaluationRunner production adapter/writer import, production-data Session in test mode, unbounded resource, or evaluation namespace leak. Without a real principal/role guard, assert the entire Plan 09 router is absent and stop M4 release.
- [ ] **Step 7: Produce Plan 10 handoff.** Exact dataset/gate IDs in safe fixtures, migration heads, worker contract, feature flags, package/Profile inventory, and commands required for migration/canary.
- [ ] **Step 8: Verify final repository state and release blockers.** One Alembic head, real guarded exposure or default-unmounted Plan 09 router, `git diff --check`, and no unrelated staged files. Do not mark Plan 09/M4 release complete or hand off Plan 10 while the operator guard is absent.

~~~bash
backend/.venv/bin/python -m pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
cd backend && .venv/bin/alembic heads && cd ..
git diff --check
~~~

**Commit:** `test(ai): verify skill admin and publish gates`

---

## Exit Criteria

- An administrator can create a standard-compatible Skill with multiple Capability bindings and complete policy/budget/completion/resource metadata.
- Content edits, restore, and publish preserve append-only immutable history and exact digests.
- Imports/exports are deterministic and malicious/oversized/unsafe resources fail closed.
- Draft/archived/non-catalog Skills never enter production Catalog recall.
- Test/evaluation runs use real orchestration contracts with separate Eval execution/subject identities, calls, Runs, events, Artifacts, and data projections; touching any production writer/adapter hard-fails the run and cannot mutate production data or memory.
- `read_snapshot` is default-disabled and, when explicitly authorized, uses only a versioned immutable field-allowlisted projection; secrets, raw auth/cookies/headers, signed URLs, encrypted/private fields, and production Sessions cannot reach Eval/gate evidence.
- Publish/catalog/Profile promotion requires reproducible, non-stale server-derived evidence and zero hard safety violations. In observe as well as enforce, an already live-enabled aggregate cannot advance its published pointer without a matching gate; rejection leaves its pointer/revision/live snapshot unchanged.
- `POST /skill-eval/gates` accepts evidence references, not a client-authored decision/assertion/metric snapshot, and production-used evidence remains pinned for audit.
- Package scripts are view/export-only and never executable.
- Legacy admin/runtime remain available for controlled Plan 10 migration.
- Every mounted Plan 09 route has a verified assistant-config principal; catalog/waiver/system operations additionally enforce operator/admin role in the service. If the repository still lacks that server-side guard, the Plan 09 router is absent from staging/production OpenAPI and Plan 09/M4 is explicitly **not release-complete**.

## Handoff to Plan 10

Plan 10 may migrate built-in Skills/aliases/L2 namespaces, compare shadow/canary metrics, cut production traffic over, and finally remove legacy code/schema in a separately gated destructive release. It must use the versioned datasets/gates created here and must not interpret a successful one-off workbench conversation as rollout evidence.

Plan 10 must not start until the handoff records all of the following:

- one Alembic head after both Plan 09 migrations and exact evaluation worker/runtime contract versions;
- publish gate mode `enforce` with a qualifying current gate for every already enabled native package/Profile;
- proof that observe rejects an ungated pointer advance for an already enabled Skill/Profile without changing the live Catalog/Profile digest;
- deterministic system/shared dataset version IDs, threshold policy version, and fixed expected metric definitions;
- passing evaluation namespace/side-effect tripwire/nested-dispatch/snapshot allowlist/worker recovery/gate-pin-retention tests under production write mode `off` and `golden`;
- complete package/Profile/alias inventory APIs and deterministic export digests;
- Universal UI/API feature controls plus a verified legacy-off compatibility rollback and a real server-side principal/operator guard on every mounted Plan 09 route;
- no script execution endpoint, production-data test mode, unauthenticated privileged mutation, or evaluation data leak.

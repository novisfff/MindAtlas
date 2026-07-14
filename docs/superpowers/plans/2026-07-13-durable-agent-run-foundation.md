# MindAtlas Durable Agent Run Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Start only after full Plan 01, the reviewed Plan 02A readiness record with `PLAN_02A_READY=yes`, and full Plans 03–05 are merged; the Plan 04 read-only golden path, Plan 05 fixed policy evaluation, grant-independence vectors, and post-lineage activation lifecycle regressions must be green, and the post-Plan-05 repository must have exactly one Alembic head. Plan 02B observation/OpenClaw cleanup is a non-blocking coordination track.

**Goal:** Make every new Main Agent Run recoverable after API/worker process loss by durably freezing its Manifest, Provider transcript, Provider Loop continuation, policy/budget/obligation revisions, Artifacts, events, and terminal-memory boundary; execute it through one compatible leased worker; and make SSE replay and cancellation independent of an HTTP connection.

**Architecture:** Keep `AssistantChatRun` as the mutable aggregate and lease pointer, but move all Main Agent execution out of `AssistantService` daemon threads. Every semantic status mutation uses an allowed-from-state plus `state_revision` CAS; leased execution mutations additionally verify the exact lease token. Append immutable Run child rows only through a repository transaction that advances all current pointers and inserts idempotent ordered events. A dedicated `python -m app.assistant.worker` process advertises its build/codec support, claims compatible Runs with PostgreSQL `FOR UPDATE SKIP LOCKED`, reconstructs ephemeral Provider/Gateway clients only from frozen references, and commits a reserved/prepared Checkpoint before every Provider or Capability boundary, a distinct started transition immediately before external I/O, and a post-execution Checkpoint afterward. Plan 05 Skill activation remains process-local while staged and becomes durable only when the post-lineage lifecycle accept commits its complete state package. Legacy Runs keep their existing thread/polling path until Plan 10.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2 frozen contracts, SQLAlchemy 2, Alembic, PostgreSQL 15, MinIO in a private Artifact bucket, server-sent events, pytest, and the already merged Plans 01–05 runtime contracts.

---

## 1. Position, Scope, and Release Boundary

This is Plan 06 of 10 and the first part of milestone M3.

Implemented here:

- Durable Main Agent Run aggregate and explicit state transition matrix.
- Schema-versioned Checkpoint codec and lossless persistence of the exact Plan 03 `ProviderLoopContinuation`.
- Persistent Manifest, Provider messages, Plan 05 policy/budget/obligation snapshots, Artifacts, and safe events.
- Persist-before-execute boundaries for Provider rounds and read/compute Capability units.
- Compatible worker registration, lease, heartbeat, retry backoff, takeover, and recovery scanning.
- Database-driven SSE replay with stable event identity and client-side deduplication semantics.
- Durable cancellation and terminal finalization.
- L0/L1/L2 timing plus stable Skill Package/namespace foundation and one-shot terminal memory application.
- Private, bounded Artifact storage with object cleanup/outbox behavior.

Explicitly not implemented here:

- No production `interrupt_mode=durable` Workflow or human pause. Plan 07 enables it. Plan 06 persists only a scripted/test continuation to prove the outer Provider Loop contract.
- No CapabilityCall ledger, write idempotency, external reconciliation, or business write. Plan 08 owns them.
- No automatic recovery of `runtime_kind=legacy` Runs.
- No serialization of SQLAlchemy `Session`, Provider/LLM clients, callbacks, closures, locks, threads, generators, open files, coroutines, compiled LangGraph objects, or decrypted credentials.
- No deletion of `AssistantService` legacy thread state, `HumanLoopCoordinator`, Router, Supervisor, or legacy APIs.
- No claim that an uncommitted Provider/read/compute request executes exactly once. The committed state is exactly-once by CAS; a started unit whose result never committed may run again under the same logical unit identity. This is safe only because Plan 06 keeps every enabled Capability at `none | read | compute`.

Hard release boundary:

1. Set `ASSISTANT_MAIN_AGENT_MODE=off` before deploying the migration/worker cutover.
2. Drain or explicitly cancel every nonterminal pre-Plan-06 Main Agent execution. In-memory Plan 04 state cannot be fabricated into a durable Checkpoint.
3. Deploy database migration, API image, and compatible assistant worker while the mode stays `off`.
4. Pass the worker registration, claim/recovery, private Artifact, and read-only smoke gates.
5. Re-enable only new Runs with `ASSISTANT_MAIN_AGENT_MODE=read_only`.
6. Rolling application rollback sets the mode `off`; it never switches an existing durable Run to Legacy. Keep at least one compatible worker image available until every active durable Run drains, cancels, or is explicitly reconciled.

Database downgrade is not the normal rollback. It is forbidden while any durable Main Agent Run/history exists unless an explicit export-and-purge maintenance procedure has completed and supplied the required acknowledgment to the migration.

---

## 2. Repository Facts That Task 0 Must Reconfirm

The current pre-plan repository has these concrete anchors:

- `backend/app/assistant/models.py` already defines `AssistantChatRun` and `AssistantChatRunEvent`.
- `backend/app/assistant/run_service.py` currently allocates `seq` as `last_event_seq + 1` in application code and commits each mutation independently; that is not safe under two workers.
- `backend/app/assistant/service.py` owns `_background_run_threads`, stream-attachment bookkeeping, and `_run_chat_background` daemon execution.
- `backend/app/assistant/router.py` exposes conversation-scoped Run/SSE/stop routes.
- `frontend/src/features/assistant/hooks/useChat.ts` reconnects by `afterSeq` and already needs reducer-level idempotence.
- `AssistantConversationSkillL2Memory` is keyed by `conversation_id + skill_name`; it has no stable package ID or namespace.
- `backend/app/common/storage.py` resolves the existing attachment bucket. `deploy/minio-init.sh` currently grants anonymous download to that bucket, so durable Provider/Artifact content must not be stored there.
- `deploy/docker-compose.yml` has API, LightRAG worker, and Docling worker patterns but no assistant worker.
- Current local Alembic head at plan-authoring time is `a7b8c9d0e1f2`; Plans 01/03/04 will add generated revisions before Plan 06. This document must not predict or reuse a revision ID.
- `backend/tests/_db.py` is SQLite-only. It remains useful for pure/unit tests but cannot prove partial indexes, `SKIP LOCKED`, triggers, or concurrent CAS.

Task 0 records the actual post-Plan-05 paths/types. If a prerequisite renamed a contract, update this plan before implementation and use the merged type; do not create a duplicate compatibility type merely to preserve this draft's spelling.

Stop and amend the owning prerequisite if any of these are absent:

- exact immutable Manifest payload/digest and lineage;
- lossless Plan 03 Provider messages, `ProviderLoopContinuation`, transcript validators, and resume request;
- Plan 05 serializable `EffectiveRunPolicySnapshot`, complete independently derived `EffectiveCapabilityGrant` values with `grant_source_digest`, `BudgetLedgerState`, `ObligationLedgerState`, and portable `CapabilityCallFrame` values;
- Plan 04/05 call-scoped pending activation with the exact ordering `stage -> Plan 03 lineage validation -> ManifestEffectLifecyclePort.accept`, where a rejected/discarded candidate leaves no active/resource/Tool/context/event state and never rewinds a started `skill.inject` charge;
- lossless Provider message discriminators for Plan 03 `runtime_instruction`, Plan 04 `runtime_context`, and Plan 05 `runtime_completion`; none may be downcast to an ordinary `system` message;
- safe Main Agent public/internal event models;
- one read-only Main Agent golden path with every visible descriptor at `interrupt_mode=none`.

Plan 02B production observation, temporary-selector removal, and OpenClaw legacy deletion are not prerequisites for this durable Main Agent path. Record their coordination status without importing or depending on those contracts.

---

## 3. Locked Runtime Invariants

1. A Run chooses `runtime_kind` once. It never changes between `legacy` and `main_agent`.
2. Only a worker whose advertised app-build and Checkpoint codec support match the Run may claim it.
3. One lease token is `(run_id, worker_id, lease_generation)`. Every execution mutation verifies all three values plus expected `state_revision`.
4. Heartbeats extend liveness only; they do not mutate semantic state or reset budgets.
5. Every Provider/Capability boundary has a committed pre-execution Checkpoint. No external/adapter call starts from merely in-memory state.
6. Every post-execution result, pointer advance, safe event, and state transition commits in one database transaction.
7. A Checkpoint always references exact immutable Manifest/policy/budget/obligation revisions and an exact Provider transcript prefix. Recovery never resolves `latest`.
8. Provider protocol may be open only with the exact Plan 03 waiting continuation. No incomplete transcript is sent to a Provider.
9. Client disconnect does not cancel the Run. Cancellation is a durable state transition/request observed before every Provider/Capability boundary.
10. Server event rows are durable and ordered; SSE transport is replayable at-least-once, not magically exactly-once. Consumers deduplicate by Run plus event identity/sequence.
11. A terminal success decision is finalized, memory is applied or safely marked failed, and then `completed` is committed. No later Run can overtake an unfinished memory finalizer for the same conversation.
12. No production descriptor above `none | read | compute` is admitted in this plan.
13. Every semantic status transition, including an API stop that does not own a lease, uses an allowed-from-status predicate plus expected `state_revision` and increments that revision. A result never overwrites `cancelling`; only the cancellation finalizer may produce `cancelled` from it.
14. Persisted Provider messages retain their exact protected discriminator and revision linkage. Adapter-level system encoding never changes the durable `runtime_instruction|runtime_context|runtime_completion` identity.
15. A staged Skill activation is not durable truth. Only the result transaction implementing `ManifestEffectLifecyclePort.accept` may make its Manifest/policy/grant/budget/obligation/context/activation package visible after lineage validation.
16. Artifact orphan cleanup is subordinate to Run recovery: no object belonging to a nonterminal Run, live lease, or inflight unit may be deleted merely because its row has not committed yet.

Runtime admission therefore moves to the last safe point before `AssistantChatRun` insertion: prepare/validate the conversation and message pair, evaluate Plan 04/05 Main Agent admission plus compatible-worker heartbeat, choose Legacy or Main Agent, then create the Run with that immutable kind. A permitted Legacy fallback happens only before the durable Run row exists. Once a `main_agent` Run is inserted/queued, worker loss or later runtime failure stays inside that Run and never spawns/switches to a Legacy execution.

---

## 4. Locked Run State Machine

Statuses for `runtime_kind=main_agent`:

~~~text
queued
  -> running
running
  -> waiting_approval | waiting_input       # schema reserved; production starts in Plan 07
  -> completed | failed | needs_reconciliation
  -> cancelling
running + expired lease
  -> recovering -> running
waiting_approval | waiting_input
  -> queued                                  # trusted resume, Plan 07
  -> cancelled                               # no work was in flight
queued
  -> cancelled                               # cancellation before claim
recovering
  -> cancelling | failed | needs_reconciliation
cancelling
  -> cancelled
needs_reconciliation
  -> cancelled                               # operator/user abandons the Run
completed | failed | cancelled
  -> terminal
~~~

Transition ownership is fixed:

| From | To | Actor and condition |
|---|---|---|
| `queued` | `running` | compatible worker claim |
| expired `running` | `recovering` | takeover claim; no adapter call yet |
| `recovering` | `running` | worker verifies Checkpoint/refs/digests and commits recovery event |
| `running` | `waiting_*` | checkpoint transaction with exact portable continuation only |
| `waiting_*` | `queued` | trusted interrupt/continuation CAS; Plan 07 |
| `queued` or `waiting_*` | `cancelled` | direct cancellation transaction, because no adapter owns work |
| `running` or `recovering` | `cancelling` | stop request; current lease remains responsible |
| expired `cancelling` | `cancelled` | recovery finalizer; it does not resume model/capability work |
| `running` | `completed` | completion guard accepted, final output persisted, memory boundary resolved |
| `running` or `recovering` | `failed` | deterministic terminal safe failure |
| `running` or `recovering` | `needs_reconciliation` | unsupported codec/build/ref drift or uncertain internal storage state |

Every row in this table is implemented through one transition API whose SQL predicate includes Run ID, expected `state_revision`, and exact allowed source status; successful transitions increment `state_revision`. Lease-owned transitions additionally include `lease_owner` and `lease_generation`. The API stop path does not need a lease, but it is not exempt from revision/status CAS:

~~~text
UPDATE assistant_chat_run
SET status = :target_status,
    cancel_requested_at = COALESCE(cancel_requested_at, :now),
    state_revision = state_revision + 1,
    updated_at = :now
WHERE id = :run_id
  AND state_revision = :expected_revision
  AND status IN :allowed_from_statuses
RETURNING ...
~~~

Cancellation/result precedence is locked:

- `queued|waiting_*` stop may commit directly to `cancelled` through the same CAS because no adapter owns work.
- `running|recovering` stop may commit only to `cancelling`. Its revision bump immediately invalidates every in-flight result prepared under the older revision even while the worker's heartbeat lease remains live.
- Normal Provider/Capability/completion result commits require `status=running`; a recovery worker must first commit `recovering -> running`. No ordinary result accepts `cancelling` as a source.
- Only the cancellation finalizer may commit `cancelling -> cancelled`; expired-lease takeover of `cancelling` performs no Provider/Capability work.
- If a result wins first and commits a terminal status, a later stop is an idempotent terminal read. If stop wins first, the result returns a stable stale-revision/invalid-source conflict and cannot replace cancellation.
- `ready_for_memory` remains an internal Checkpoint phase while status is `running`. The transaction that persists accepted final content and enters that phase is the last cancellation fence: a stop that wins before it may move to `cancelling`; after `ready_for_memory` commits, stop returns a stable `run_finalizing`/already-committed response and does not cancel the accepted response. Memory committed/failed then converges to `completed`.

Forbidden transitions fail CAS with a stable conflict code. Terminal states never reopen. `needs_reconciliation` is quiescent and never auto-claimed for execution; Plan 08 may add explicit reconciliation transitions without weakening terminal-state rules. PostgreSQL two-session tests, not only unit mocks, must prove stop-versus-result, stop-versus-ready-for-memory, stop-versus-memory-finalizer, and duplicate cancellation-finalizer convergence to exactly one legal terminal outcome.

The active-conversation uniqueness set is:

~~~text
queued, running, recovering, waiting_approval, waiting_input,
cancelling, needs_reconciliation
~~~

Legacy status behavior remains readable and compatible. Legacy code must not start emitting `recovering`, `waiting_input`, or `needs_reconciliation` unless it is explicitly migrated in Plan 10.

---

## 5. Database Schema and Migration Contract

Generate a real Alembic revision after reading the sole execution-time head:

~~~bash
cd backend
.venv/bin/alembic heads
.venv/bin/alembic revision -m "add durable agent run foundation"
cd ..
~~~

The generated revision ID and `down_revision` must be unique and equal the actual post-Plan-05 head. Do not use `e1f2a3b4c5d6` or any other ID copied from an earlier draft.

### 5.1 Extend `assistant_chat_run`

Add:

| Column | Contract |
|---|---|
| `runtime_kind` | `legacy | main_agent`, non-null, server default `legacy`, immutable after creation |
| `runtime_contract_version` | positive integer; `1` for Plan 06 |
| `required_app_build_revision` | nullable for Legacy, required for Main Agent; copied from base Manifest |
| `state_revision` | non-negative integer CAS counter, server default `0` |
| `current_manifest_revision_id` | nullable pointer to this Run's current Manifest row |
| `current_policy_revision_id` | nullable pointer to this Run's current policy revision |
| `current_checkpoint_id` | nullable pointer to this Run's current Checkpoint |
| `current_budget_revision_id` | nullable pointer to this Run's budget revision |
| `current_obligation_revision_id` | nullable pointer to this Run's obligation revision |
| `lease_owner` | nullable `String(160)` worker ID |
| `lease_generation` | non-negative integer, server default `0` |
| `lease_expires_at` | nullable timezone timestamp using database time |
| `heartbeat_at` | nullable timezone timestamp |
| `next_attempt_at` | nullable timezone timestamp for bounded backoff |
| `recovery_count` | non-negative integer, server default `0` |
| `deadline_at` | nullable active execution deadline copied from budget state |
| `failure_code` | nullable bounded stable code; no exception text |
| `memory_commit_status` | `not_applicable | pending | committed | failed`, Legacy default `not_applicable` |
| `memory_committed_at` | nullable timezone timestamp |

Keep existing `last_event_seq` and `checkpoint_seq` for API compatibility. For Main Agent Runs they are advanced only by the durable repository. `checkpoint_seq` equals the current Checkpoint sequence.

Replace the status check with the complete locked state set. Replace application-only active detection with one PostgreSQL partial unique index on `conversation_id` for the active set above. Migration preflight must query and abort on duplicate active rows; it must not choose or delete a winner.

The five current-pointer FKs are added only after child tables exist, use `ON DELETE SET NULL`, and are validated in service/database ownership guards to ensure the target child belongs to the same Run. This avoids an unmanageable parent/child cascade cycle.

### 5.2 `assistant_worker_registration`

Mutable liveness table:

- `worker_id` primary key.
- `app_build_revision`, `runtime_contract_version`.
- supported Checkpoint codec versions and capability/runtime feature digest.
- `started_at`, `heartbeat_at`, `draining_at`.
- optional safe hostname/instance label; no PID command line, credential, environment dump, or IP token.

The API admission preflight requires a compatible non-draining heartbeat newer than the configured registration TTL before it creates a new Main Agent Run. Failure occurs before Provider work and may use Plan 04's safe Legacy fallback. Registration is not a lease and cannot authorize a Run mutation.

### 5.3 Immutable Run children

`assistant_run_manifest_revision`:

- UUID ID, Run FK `ON DELETE CASCADE`.
- exact Manifest revision number, optional parent row ID/digest, manifest digest, schema version, canonical JSON payload, created-at.
- unique `(run_id, revision)`, `(run_id, manifest_digest)`.

`assistant_run_provider_message`:

- UUID ID, Run FK.
- monotonic ordinal, Provider round, exact `role`, role-specific payload version/discriminator/body, protection visibility, content digest.
- `role` is a checked enum containing exactly `system | runtime_instruction | runtime_context | runtime_completion | user | assistant | tool` for codec v1. `runtime_instruction` retains Plan 03 `instruction_type=soft_finalization`; `runtime_context` retains the Plan 04 context discriminator; `runtime_completion` retains the Plan 05 completion discriminator.
- Manifest revision linkage is required for every message. Policy revision linkage is required for protected Main Agent runtime messages, and obligation revision linkage is required for `runtime_completion`; all linked rows must belong to the same Run. The role-specific canonical payload retains any prompt-build/guard-state digest required by its owning contract.
- `runtime_instruction|runtime_context|runtime_completion` are always protected/internal Provider input. The OpenAI adapter may encode them as system-level wire messages, but the database/codec must reject storing or decoding them as a bare `system` role.
- Provider message ID and Tool Call ID where applicable.
- unique `(run_id, ordinal)`; Tool Call IDs remain globally unique within the Run transcript by service/constraint test.
- the row may contain user-authorized conversation content and Tool arguments needed for exact resume, but never credentials, decrypted Tool config, callbacks, arbitrary exception text, or raw provider headers.
- role, discriminator, payload version/body, protection kind, revision links, and content bytes all participate in the message/transcript digest. Codec round-trip must preserve the exact union member rather than reconstructing it from text.

`assistant_run_policy_revision`:

- exact normalized `EffectiveRunPolicySnapshot`, revision/digest/parent, created-at.
- the canonical body also stores the complete, deterministically ordered `EffectiveCapabilityGrant` values for that snapshot, including each exact binding/owner, `allowed_side_effects`, immutable source-policy digests, and `grant_source_digest`.
- this is required; a digest alone is insufficient if a later build cannot reconstruct old policy constants. Recovery never rebuilds a grant from `CapabilityDescriptor.behavior` or substitutes a classification digest for `grant_source_digest`.

`assistant_run_budget_revision`:

- exact Plan 05 `BudgetLedgerState`, revision/digest/parent, created-at.
- reservations and started/finished state are preserved; recovery never resets a round/call counter.

`assistant_run_obligation_revision`:

- exact Plan 05 `ObligationLedgerState`, revision/digest/parent, created-at.

`assistant_run_checkpoint`:

- UUID ID, Run FK.
- monotonic sequence, expected/committed Run `state_revision`, `schema_version=1`.
- Manifest/policy/budget/obligation revision FKs.
- highest Provider-message ordinal plus transcript digest.
- phase, logical-unit ID, reason, canonical durable-state JSON, state digest, created-at.
- unique `(run_id, sequence)`, `(run_id, committed_state_revision)`, `(run_id, state_digest)` where semantically appropriate.

`assistant_run_artifact`:

- UUID ID, Run FK.
- kind, media type, display label, storage kind `inline | object`.
- byte size and SHA-256.
- exactly one of `inline_bytes` (`LargeBinary`) or server-generated private object key.
- safe metadata JSON, created-at.
- unique Run/content identity required by the Artifact service so retrying the same logical Artifact converges.

`assistant_run_artifact_gc`:

- independent outbox with no Run FK, frozen bucket name, object key, digest, status, attempts, next-attempt, created/deleted timestamps.
- it survives conversation/Run deletion and removes private MinIO objects idempotently.

Immutability behavior:

- PostgreSQL triggers reject `UPDATE` on Manifest/message/policy/budget/obligation/Checkpoint/Artifact rows.
- Direct child `DELETE` is rejected unless the transaction sets `SET LOCAL mindatlas.allow_durable_run_purge = 'on'`, used only by the reviewed conversation deletion/maintenance service.
- Privacy deletion remains possible: before deleting a conversation, enqueue every object-backed Artifact in `assistant_run_artifact_gc`, set the local purge flag, then allow FK cascades in the same database transaction.
- SQLite tests prove repository behavior; PostgreSQL tests prove triggers and purge semantics.

### 5.4 Stable memory additions

`assistant_conversation_l1_memory`:

- add nullable `last_applied_run_id`.

`assistant_conversation_skill_l2_memory`:

- add nullable `skill_package_id` FK and nullable `memory_namespace`.
- add nullable `facts_v2` containing a strictly validated list of `{text, sourceSkillVersionId, sourceRunId, sourceCapabilityCallId, observedAt}`; `sourceCapabilityCallId` remains null until Plan 08.
- add nullable `last_applied_run_id`.
- keep `skill_name` and legacy `facts` for old reads/writes.
- for native rows, store the package's frozen canonical name in `skill_name` only as a compatibility/display value; native reads, uniqueness, and updates use package ID + namespace.
- replace the unconditional legacy unique index with:
  - unique `(conversation_id, skill_name)` where `skill_package_id IS NULL`;
  - unique `(conversation_id, skill_package_id, memory_namespace)` where `skill_package_id IS NOT NULL`.
- native rows require nonempty normalized `memory_namespace` (default `default`) in service validation. The legacy alias/display name is never the native lookup key.

Existing rows backfill as Legacy (`skill_package_id=NULL`, `memory_namespace=NULL`) without changing their facts. Plan 10 owns full legacy fact migration.

---

## 6. Durable Checkpoint v1

Use the merged Plans 01–05 contracts directly inside a strict outer contract:

~~~python
class DurableExecutionUnitV1(FrozenContract):
    logical_unit_id: str
    kind: Literal[
        "provider_round",
        "capability_group",
        "completion",
        "memory_commit",
    ]
    state: Literal["prepared", "started"]
    provider_round: int | None
    call_ids: tuple[str, ...]
    attempt: int
    reserved_budget_revision: int
    started_budget_revision: int | None


class DurableAgentCheckpointV1(FrozenContract):
    schema_version: Literal[1] = 1
    run_id: UUID
    phase: Literal[
        "ready_for_provider",
        "dispatching_calls",
        "waiting",
        "ready_for_completion",
        "ready_for_memory",
        "terminal",
    ]
    manifest_revision_id: UUID
    policy_revision_id: UUID
    budget_revision_id: UUID
    obligation_revision_id: UUID
    provider_message_ordinal: int
    provider_transcript_digest: str
    provider_loop_continuation: ProviderLoopContinuation | None
    inflight_unit: DurableExecutionUnitV1 | None
    capability_frames: tuple[CapabilityCallFrame, ...]
    artifact_ids: tuple[UUID, ...]
    visible_text_artifact_id: UUID | None
    next_action: DurableNextActionV1
~~~

Rules:

- `provider_loop_continuation` is the exact Plan 03 contract, not a reduced local copy.
- A transcript may be open only when `phase=waiting` and that continuation validates it.
- The full frozen exposed Tool surface in the Plan 03 continuation is retained. Recovery does not rebuild it from current catalog state.
- `capability_frames` is the exact portable Plan 05 frame stack in canonical order and participates in the Checkpoint digest. If an enabled production path cannot reproduce/persist it losslessly, that Agent/nested Capability path is unavailable in Plan 06 rather than rebuilt from a mutable runtime stack.
- `inflight_unit=prepared` is committed before adapter/network work. For a Capability group its `reserved_budget_revision` contains reservations only and `started_budget_revision=None`; Plan 05/Gateway validation must still run before a started transition. For a Provider round, the prepared state similarly does not count network I/O as started.
- Immediately before each external adapter call, commit a second CAS transition to `state=started` with the exact budget revision produced by Gateway `mark_started` or Provider-round `before_round`. No external I/O begins from `prepared`.
- Recovery of an uncommitted started Provider/read/compute unit increments only its attempt, not its logical round/call budget. Encountering the same `logical_unit_id` reuses the existing reservation/started state and deterministic start event; it never reserves, charges, or emits start a second time. A committed post-result Checkpoint short-circuits execution completely.
- Plan 05 Skill activation staging remains process-local within one attempt and is absent from Checkpoint truth. Only a successfully committed lifecycle-accept result may advance Manifest/policy/grant/budget/obligation/context pointers. Recovery never infers acceptance from a Tool Result, proposed child, or event.
- Plan 07 introduces `DurableAgentCheckpointV2` with Workflow state and an explicit `v1 -> v2` codec migration; Plan 06 does not hide an untyped future Workflow blob in v1.

The codec:

- uses canonical JSON and fixed digest vectors;
- rejects extra fields, aliases not normalized by the owning contract, NaN/Infinity, excess depth/size, arbitrary class instances, bytes outside Artifact storage, and every known ephemeral type;
- has explicit `decode_v1` and a migration registry;
- round-trips every Provider union member, complete grant set, and portable frame without changing its discriminator/digest;
- rejects protected-message downcast, missing/mismatched role-specific revision linkage, grant/classification substitution, and a frame stack inconsistent with the inflight unit;
- sends unknown future versions to `needs_reconciliation` before Provider/Gateway I/O;
- never guesses or silently drops an unknown field.

---

## 7. Persist-Before-Execute and Commit Protocol

No database transaction or row lock stays open across Provider, MinIO, or Capability execution.

### 7.1 Prepare transaction

For one logical unit:

1. verify lease owner/generation, `status=running`, and expected Run revision;
2. verify cancellation/deadline and exact current pointers;
3. if the same `logical_unit_id` is already current, validate its immutable call/round identity and reuse its reservation state; if its post-result Checkpoint exists, return that result without execution;
4. otherwise apply only the Plan 05 reservation transition exactly once. A Capability is not marked started here; a Provider round is not counted started here;
5. append policy/budget/obligation revisions if changed;
6. append a Checkpoint with `inflight_unit.state=prepared`, deterministic `logical_unit_id`, incremented attempt only for a true retry, `reserved_budget_revision`, and `started_budget_revision=None`;
7. advance pointers/revision and append/reuse deterministic prepared events;
8. commit.

Prepare never invokes an adapter and never consumes a started-call/round count merely because work was queued. After it commits, the worker may construct an ephemeral client/context and decrypt only the exact frozen credential slot whose revision matches; construction itself performs no Provider/Capability I/O and nothing secret is persisted.

### 7.2 Started transaction

Immediately before each external Provider/Capability adapter call:

1. verify the same lease token, `status=running`, current expected revision/pointers, and exact prepared logical unit;
2. recheck cancellation/deadline, current descriptor/grant/evidence, availability, model/credential/config revisions, ephemeral client identity, and validated input digest at the owning Plan 02/03/05 boundary;
3. for a Capability, invoke the Plan 05 `mark_started` transition for that exact call; for a Provider round, invoke its exact `before_round` start transition;
4. append the resulting budget revision and a Checkpoint retaining the same logical unit/attempt with `state=started` and updated `started_budget_revision`;
5. advance revision and append/reuse the deterministic call/round-start event;
6. commit.

For a parallel Capability group, reservations remain all-or-none, but each worker's exact call becomes started through its own serialized CAS immediately before that call's adapter I/O. `BudgetLedgerState` remains authoritative for which sibling calls started; the group Checkpoint retains the same logical unit and latest started budget revision. Calls that never cross this boundary are released, not charged.

Only after the applicable started transaction commits may the worker construct/use the external client and invoke the adapter.

### 7.3 Execute outside transaction

- Recheck lease through the heartbeat/cancellation probe immediately before the call.
- Use only the ephemeral Provider/Gateway/Skill/Tool/Workflow objects built from exact frozen refs and the matching credential slot after prepare. Credential-slot rotation or revision drift detected before the started CAS is fail-closed: do not decrypt/use the new slot or continue the old semantic Run; commit `needs_reconciliation` through the normal CAS path without consuming a started call/round. A race detected after the started CAS but before I/O also performs no I/O and is recorded honestly as a started attempt that could not proceed; it never substitutes the new key.
- Never hold the repository `Session` inside a parallel sibling worker.
- A lost lease prevents another call/result commit. It cannot recall an already sent read/compute request.

### 7.4 Result transaction

1. verify the same lease token, `status=running`, expected revision, and exact allowed current Checkpoint phase;
2. verify result belongs to the prepared/started logical unit and exact call IDs; reject an unstarted Capability result;
3. append Provider messages/Manifest/policy/grants/budget/obligation/Artifacts as needed;
4. for `skill.inject`, recompute/verify the process-local pending package, accepted Plan 03 lineage, exact parent/child/effect digests, and the already-started control-call accounting. This result transaction is the durable `ManifestEffectLifecyclePort.accept`: it atomically appends the accepted Manifest/policy/exposure/grant/owner-limit/obligation/context/activation state and provisional Tool Result. No earlier transaction may persist any candidate-active state;
5. append the post-unit Checkpoint with no open inflight unit or the exact waiting continuation;
6. advance aggregate pointers/status/revision and append idempotent safe events;
7. commit.

If take, lineage, lease, cancellation, package, digest, source-status, revision, or lifecycle validation fails before accept, discard the process-local activation candidate. No candidate Manifest/policy/grant/owner bucket/obligation/context/resource/Tool/event/result becomes visible. A `skill.inject` call that crossed `mark_started` remains charged exactly once.

The result CAS never accepts `cancelling`, and a result prepared under a revision invalidated by stop cannot commit. If the worker dies after execution but before this transaction, recovery may repeat that logical Provider/read/compute unit. It reuses the same logical IDs, existing reservation/started budget state, and deterministic events. Plan 06 tests convergence of committed transcript/events/state, not an impossible exactly-once promise for an unledgered external compute request.

---

## 8. Worker Registration, Lease, and Recovery

### 8.1 Configuration

Add validated settings with conservative defaults:

~~~text
ASSISTANT_WORKER_POLL_INTERVAL_MS=500
ASSISTANT_WORKER_LEASE_TTL_SEC=30
ASSISTANT_WORKER_HEARTBEAT_INTERVAL_SEC=5
ASSISTANT_WORKER_REGISTRATION_TTL_SEC=20
ASSISTANT_WORKER_MAX_RECOVERY_ATTEMPTS=5
ASSISTANT_WORKER_RETRY_BASE_MS=500
ASSISTANT_WORKER_RETRY_MAX_MS=30000
ASSISTANT_ARTIFACT_BUCKET=mindatlas-assistant-artifacts
ASSISTANT_ARTIFACT_INLINE_MAX_BYTES=262144
ASSISTANT_ARTIFACT_MAX_BYTES=26214400
ASSISTANT_ARTIFACT_RUN_MAX_BYTES=104857600
ASSISTANT_ARTIFACT_ORPHAN_SCAN_INTERVAL_SEC=60
ASSISTANT_ARTIFACT_ORPHAN_GRACE_SEC=900
ASSISTANT_DURABLE_CLOCK_SKEW_SEC=30
~~~

Require heartbeat interval `< lease TTL / 3`. Invalid relationships fail worker startup with safe setting names. The worker ID is generated from a stable instance label plus a random boot UUID; it is not accepted from model/user input.

The orphan grace must be at least:

~~~text
lease_ttl
+ sum(min(retry_base * 2^attempt, retry_max), attempt=0..max_recovery_attempts-1)
+ orphan_scan_interval
+ bounded_clock_skew
~~~

Settings may make the grace more conservative but cannot configure it below this derived recovery window.

### 8.2 Claim

In one PostgreSQL transaction using database `now()`:

1. select the earliest eligible compatible Run by `next_attempt_at, created_at` with `FOR UPDATE SKIP LOCKED`;
2. allow `queued`, or expired `running/recovering`, or expired `cancelling` for cancellation finalization;
3. match `required_app_build_revision` and codec/runtime support advertised by this worker;
4. increment `lease_generation`, set owner/heartbeat/expiry, and increment `state_revision`;
5. use `running` for a normal queued claim and `recovering` for an expired execution claim;
6. commit and return the lease token.

### 8.3 Heartbeat and graceful shutdown

- Conditional update by Run ID + owner + generation + allowed status.
- Zero rows means lease lost. Stop before any additional adapter call or semantic commit.
- Heartbeat does not increment `state_revision`.
- Heartbeat is the only lease write that does not bump semantic revision. Stop/cancel, result, recovery, backoff, pointer, memory, and terminal changes all use the Section 4 revision/status CAS.
- On SIGTERM, mark registration draining, stop claims, finish or checkpoint the current bounded unit, and continue heartbeats during the grace period. If the process cannot finish, let the lease expire; never clear another worker's lease.

### 8.4 Recovery classification

- Validate Checkpoint digest/version, all current pointers, protected Provider-message discriminators/linkage, transcript, Manifest lineage, complete independent grant bodies/digests, portable call frames, build/dependency/model/credential/config revisions, policy/budget/obligation digests, and referenced Artifact availability before returning `recovering -> running`.
- Reconstruct only ephemeral adapters and fresh one-call authorization evidence. Persisted authorization evidence is never replayed.
- A credential revision mismatch, including key-slot rotation during an active Run, performs no Provider/Gateway call and becomes `needs_reconciliation`; recovery never silently uses a newer key for an older frozen Run.
- A Checkpoint with the same inflight `logical_unit_id` reuses its reservation/started state and increments only `attempt`. A Checkpoint whose post-result state is already committed short-circuits without re-execution.
- Process-local activation staging is never reconstructed or promoted directly. An uncommitted `skill.inject` attempt may be recomputed under the same logical call identity, but only a new lineage-validated lifecycle-accept result transaction can create durable active state; an already accepted child is detected from the committed Checkpoint/pointers and never appended again.
- Transient infrastructure errors schedule bounded exponential backoff via `next_attempt_at`.
- Deterministic contract/input/output failures become `failed` with a safe code.
- Unsupported codec/build/reference drift and a database Artifact row whose object is missing become `needs_reconciliation`.
- Recovery-count exhaustion fails safely if no uncertain state exists; uncertain state remains `needs_reconciliation`.
- A `cancelling` takeover performs only cancellation sealing/finalization. It does not resume Provider or Capability work.
- The periodic scanner also observes `waiting_*` and `needs_reconciliation` for expiry/staleness metrics and alerts, but does not claim them for normal execution. Plan 07 owns Interrupt expiry/requeue; reconciliation remains manual until Plan 08.

---

## 9. SSE and Event Contract

Extend `assistant_chat_run_event` additively for Main Agent rows:

- nullable `event_key` with partial unique `(run_id, event_key)` for non-null values;
- `payload_version` default `1`;
- explicit `visibility = public | internal` if Plan 04 still stores visibility inside payload, migrating existing rows to the equivalent safe default without exposing internal rows.

Event insertion algorithm:

1. serialize the semantic writer through the Run row's lease/revision CAS lock;
2. look up the deterministic event key inside that transaction;
3. if it already exists, verify name/payload digest and reuse it without changing `last_event_seq`;
4. otherwise atomically increment `last_event_seq` with `UPDATE ... RETURNING`, insert the new event with that sequence, and commit it with the state change;
5. conflicting key reuse is a protocol error, never last-writer-wins.

Nullability exists only for Legacy/backfilled compatibility. Every `runtime_kind=main_agent` semantic event requires a nonempty deterministic `event_key`; repository validation/trigger rejects a keyless durable event. Lease validation when applicable, allowed source status, expected `state_revision`, sequence allocation, child/pointer/status mutation, and event insert occur in the same transaction. A duplicate key with identical name/payload digest reuses the existing row and advances neither status/revision nor sequence unless the entire semantic transition was already committed; a conflicting duplicate advances nothing.

PostgreSQL two-session vectors must force different-key appends, same-key identical replay, same-key conflicting replay, event-insert failure after `UPDATE ... RETURNING`, and stop/result event races. Committed sequences are monotonic and gap-free under this allocation strategy because the increment rolls back with a failed insert/transition; SQLite tests must not claim this guarantee.

Delivery semantics:

- `GET .../stream?afterSeq=N` returns committed public rows with `seq > N` in order.
- Internal rows advance the server scan cursor but are never yielded to the client.
- A disconnect closes only the reader Session.
- The HTTP transport is at-least-once across uncertain reconnects. Every event carries Run ID, sequence, and stable event key where applicable; frontend reducers ignore already-applied sequence/key values.
- The stream exits only after the Run is terminal and every committed public row through `last_event_seq` has been considered.
- Do not emit provisional final text. Preserve Plan 04's rule that the final no-Tool text is buffered, persisted, and then emitted in bounded deterministic chunks.

`AssistantService._attached_run_stream_ids` and `_background_run_threads` remain only for explicit Legacy behavior. Main Agent execution and cancellation never consult them.

---

## 10. Durable Artifact Storage

The existing attachment bucket is public-download in current deploy setup. Create and configure a separate private `ASSISTANT_ARTIFACT_BUCKET`; `deploy/minio-init.sh` creates it but never assigns anonymous policy.

Limits:

- inline threshold: 256 KiB;
- one Artifact: 25 MiB;
- cumulative Run Artifacts: 100 MiB;
- configured values may lower these bounds, never raise checked-in ceilings.

Object protocol:

1. sanitize/validate media type, size, and content before storage;
2. derive SHA-256 and a server-only content-addressed key under `assistant-runs/{run_id}/...`; never accept a key from Provider/model input;
3. upload idempotently to the private bucket and verify object size/digest metadata;
4. commit the Artifact row and Checkpoint reference in the semantic transaction;
5. a crash after upload but before row commit leaves an orphan eligible only for the bounded scanner rules below; age alone never makes it safe to delete;
6. a committed row with a missing/mismatched object is not re-created from model output and sends the Run to `needs_reconciliation`.

The orphan scanner uses database time plus object metadata and may delete only when all are true:

- object age is strictly greater than the validated `ASSISTANT_ARTIFACT_ORPHAN_GRACE_SEC`;
- no `assistant_run_artifact` row references the exact bucket/key/digest;
- the key's Run does not exist or is terminal; no `queued|running|recovering|waiting_*|cancelling|needs_reconciliation` Run may lose an object;
- no live lease and no current/prepared/started Checkpoint unit can still commit that object;
- a final metadata read immediately before deletion still matches the scanned key, size, and digest;
- no deletion-outbox operation already owns the same object, or both paths converge through one idempotent delete identity.

A nonterminal/stuck Run may retain an orphan until it is completed, cancelled, purged, or explicitly reconciled. Storage leakage is preferable to deleting data that an active recovery may still commit. MinIO/PostgreSQL integration tests use barriers to run the scanner after upload but before row commit, during lease expiry/recovery claim, concurrently with cancellation/conversation deletion, and during idempotent same-key retry.

Artifact content is never placed in SSE, logs, metrics, Message summaries, L1/L2, or object URLs. `artifact.read` remains Run-scoped, digest/range checked, and backend mediated. Conversation deletion enqueues object GC before database cascade as specified above.

---

## 11. L0/L1/L2 Finalization Contract

- L0 remains the existing `assistant_message` history. Provider working messages are not additional user-visible L0 messages.
- Final assistant content is persisted only after Plan 05 completion acceptance. Failed/cancelled/waiting/provisional text never updates final Message/L1/L2/title.
- Final L0 application is idempotent by exact `(run_id, assistant_message_id, final_content_digest)`. Reapplying the same digest is a no-op; a different final digest for the same Run/message is `policy_state_protocol_error`. Recovery never inserts a second user-visible assistant Message.
- Final content persistence and entry into `ready_for_memory` occur in one revision/status CAS transaction. The phase is internal and the public status remains nonterminal `running`; it is not exposed as an undocumented API status and no terminal SSE event is emitted yet.
- After that transaction the same active Run does not become `completed` yet, so the active-Run uniqueness constraint prevents a later conversation Run from overtaking memory application. A stop arriving after this phase returns `run_finalizing`/already committed and cannot change the accepted result to `cancelling`; a stop that won the preceding CAS prevents entry into the phase.
- The worker computes/appends eligible L1/L2 changes. A crash may repeat computation, but database application is guarded by `last_applied_run_id` plus expected memory-row revision.
- Native L2 writes occur only through the controlled Run Finalizer for active frozen Skill Packages whose policy declares the namespace eligible. They are written per stable package ID + namespace, never Provider alias/display name.
- Every new fact carries source Skill Version and Run evidence. Plan 08 fills source CapabilityCall evidence where applicable.
- If memory computation/application fails after bounded attempts, set `memory_commit_status=failed`, emit an internal safe diagnostic, and still allow the user-visible Run to complete. It must not write a partial mixed L1/L2 set.
- One transaction applies all prepared memory rows, sets their `last_applied_run_id`, sets Run `memory_commit_status=committed`, and commits `completed` plus terminal events. The failed-memory path commits `completed` with `memory_commit_status=failed` explicitly.

API/SSE tests lock the external view: `completed` and the terminal public event appear only with `memory_commit_status=committed|failed`; before then the Run remains active/nonterminal and a new Run for the conversation is rejected by the database unique boundary. Internal `ready_for_memory` details never appear as a new public status value.

This ordering removes the ambiguous “completed before memory vs memory before completed” race and keeps conversation memory application serial.

---

## 12. File Responsibility Map

Exact post-Plan-05 paths must be confirmed in Task 0.

Create:

- `backend/app/assistant/durable/__init__.py`
- `backend/app/assistant/durable/contracts.py`
- `backend/app/assistant/durable/codec.py`
- `backend/app/assistant/durable/models.py`
- `backend/app/assistant/durable/repository.py`
- `backend/app/assistant/durable/leases.py`
- `backend/app/assistant/durable/checkpoints.py`
- `backend/app/assistant/durable/artifacts.py`
- `backend/app/assistant/durable/memory.py`
- `backend/app/assistant/durable/recovery.py`
- `backend/app/assistant/durable/worker_registry.py`
- `backend/app/assistant/worker.py`
- generated `backend/alembic/versions/<revision>_add_durable_agent_run_foundation.py`
- `backend/tests/postgres_helpers.py` or the merged repository-standard PostgreSQL fixture
- focused tests named in the tasks below

Modify:

- `backend/app/assistant/models.py`
- `backend/app/assistant/run_service.py`
- `backend/app/assistant/service.py`
- `backend/app/assistant/router.py`
- `backend/app/assistant/schemas.py`
- exact post-Plan-05 Main Agent service/runtime files
- exact post-Plan-05 Provider Loop integration file; do not put database imports in the pure loop package
- `backend/app/assistant/memory_service.py`
- `backend/app/common/storage.py` only if a generic private-bucket client can be added without changing attachment semantics; otherwise keep assistant storage under `app.assistant.durable`
- `backend/app/config.py`
- `backend/alembic/env.py`
- `backend/tests/_db.py`
- `backend/.env.example`
- `deploy/.env.example`
- `deploy/minio-init.sh`
- `deploy/docker-compose.yml`
- `backend/Dockerfile`
- `frontend/src/features/assistant/types.ts`
- `frontend/src/features/assistant/stores/chat-store.ts`
- `frontend/src/features/assistant/hooks/useChat.ts`
- `.github/workflows/ci.yml`

Do not create `backend/app/assistant/main_agent/*` or `provider_loop/*` duplicates if Plans 03–05 used different final paths.

---

## 13. Implementation Tasks

### Task 0: Freeze the Post-Plan-05 Baseline

- [ ] Record branch/commit, dirty state, Python version, locked dependency versions, one Alembic head, Plan 04/05 feature flags, evaluation dataset/result digests, and exact golden Profile/Skill/model/build refs.
- [ ] Verify the execution environment is clean Python 3.11 with `langgraph==0.3.34`; the repository spec explicitly rejects the local mismatched 1.x environment as compatibility evidence.
- [ ] Record and run full Plan 01, the exact reviewed Plan 02A revision with `PLAN_02A_READY=yes`, and full Plans 03–05 suites plus current Run/SSE/stop/memory tests. Record Plan 02B status for coordination only; do not wait for observation/cleanup.
- [ ] Re-run Plan 05 copy-descriptor/grant-source negative vectors and prove complete `EffectiveCapabilityGrant` values are serializable without classifier-derived grants.
- [ ] Re-run Plan 04/05 activation vectors proving `stage -> lineage -> accept`, zero residue on discard, base/active/same-batch Domain Key handling, and no rollback of a started `skill.inject` charge.
- [ ] Record the exact `runtime_instruction|runtime_context|runtime_completion` message contracts and portable `CapabilityCallFrame` type; stop if either would require a reduced/duplicate durable model.
- [ ] Record exact public/internal event payloads and frontend reconnect behavior.
- [ ] Reproduce API-process loss during a Plan 04 Main Agent Run and show the daemon state is lost.
- [ ] Confirm all production Main Agent descriptors are `none | read | compute` and `interrupt_mode=none`.
- [ ] Inspect all concrete files in Section 12 and update stale paths in this plan before implementation.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_assistant_chat_run_service.py \
  backend/tests/test_assistant_chat_run_stream.py \
  backend/tests/test_assistant_memory_l0.py \
  backend/tests/test_assistant_memory_l1_service.py \
  backend/tests/test_assistant_memory_l2_service.py \
  backend/tests/test_main_agent_runtime.py \
  backend/tests/test_agent_policy_runtime.py \
  backend/tests/test_agent_budget_ledger.py \
  backend/tests/test_agent_obligation_ledger.py -q
(cd backend && .venv/bin/alembic heads)
~~~

Expected: recorded passing baseline and exactly one head. Use the actual merged test filenames if prerequisites renamed them.

### Task 1: Add Models and a Generated Migration

- [ ] Write failing ORM tests for every column/check/index/FK, the checked Provider role/discriminator/revision-link contract, and the split Legacy/native L2 uniqueness contract.
- [ ] Write PostgreSQL migration tests for active Run uniqueness, pointer ownership, immutability/purge triggers, and duplicate-active preflight refusal.
- [ ] Generate the revision from the sole current head; review every operation rather than trusting autogenerate.
- [ ] Create child tables before aggregate pointers; drop pointer FKs before child tables on downgrade.
- [ ] Backfill every existing Run as `runtime_kind=legacy` without synthesizing durable state.
- [ ] Backfill existing memory rows as Legacy without rewriting facts.
- [ ] Implement upgrade -> downgrade -> upgrade on disposable PostgreSQL. Downgrade must refuse durable data without the maintenance acknowledgment.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_run_models.py \
  backend/tests/test_durable_run_migration_postgres.py -q
~~~

Commit: `feat(ai): add durable run persistence schema`

### Task 2: Implement Strict Contracts and Codec

- [ ] Write frozen/extra-forbid/round-trip/canonical-digest fixed vectors for Checkpoint v1, prepared/started execution units, complete grant sets, portable frames, and every Provider message union member.
- [ ] Persist and reconstruct the exact Plan 03 continuation, protected message discriminators/linkage, Plan 05 snapshots/grants, and call frames without local reduced copies.
- [ ] Reject `runtime_instruction|runtime_context|runtime_completion` downcast to `system`, missing/mismatched revision links, copied-descriptor grants, and frame/inflight inconsistency; prove existing ordinary message vectors remain unchanged.
- [ ] Test maximum JSON depth/size, NaN/Infinity, bytes, cycles, arbitrary classes, and every ephemeral type family.
- [ ] Test unknown schema version -> `needs_reconciliation` before any runtime object is constructed.
- [ ] Add recursive secret/credential/runtime-object corpus tests.

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_durable_checkpoint_codec.py -q
~~~

Commit: `feat(ai): define strict durable checkpoint contracts`

### Task 3: Implement CAS Repository and Idempotent Events

- [ ] Write two-Session PostgreSQL tests for allowed-from + revision state CAS, stop/result/ready-for-memory/finalizer races, event sequence allocation, event-key replay/conflict, child-append rollback, pointer ownership, and terminal immutability.
- [ ] Require deterministic nonempty `event_key` for every Main Agent event while preserving nullable Legacy rows. Force different-key append, identical replay, conflicting replay, insert rollback, and stop/result event races; verify no committed sequence gap or state advance on failure.
- [ ] Implement one repository transaction API; callers cannot append durable child rows and commit them independently.
- [ ] Replace Main Agent event/status/checkpoint writes with repository operations while leaving Legacy methods intact.
- [ ] Add the exact transition table and stable conflict/failure codes.
- [ ] Prove SQLite unit tests do not claim concurrency guarantees.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_run_repository.py \
  backend/tests/test_durable_run_events_postgres.py -q
~~~

Commit: `feat(ai): commit durable run state through cas`

### Task 4: Implement Private Artifact Storage and Cleanup

- [ ] Create a private assistant Artifact bucket path/config; do not alter current attachment-download compatibility.
- [ ] Test inline/object thresholds, cumulative limits, digest/range checks, cross-Run denial, upload-before-row crash, missing object, and idempotent retry.
- [ ] Add conversation deletion outbox and bounded GC/orphan scanner with the validated lease/recovery/scan/skew grace formula and terminal/no-live-lease/no-inflight deletion gates.
- [ ] Use real MinIO/PostgreSQL barriers for GC between upload/row commit, lease expiry/recovery, cancellation/deletion, and same-key retry; no active or reconcilable Run may lose an object.
- [ ] Verify no public bucket policy, presigned/public URL, log, event, memory, or Message contains Artifact content/key.
- [ ] Run a real MinIO integration test in CI or a locked integration job; pure mocks are not the only evidence.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_artifacts.py \
  backend/tests/test_durable_artifacts_minio.py -q
~~~

Commit: `feat(ai): persist run artifacts in private storage`

### Task 5: Implement Worker Registration, Lease, and Recovery Scanner

- [ ] Write real PostgreSQL claim/skip-locked/heartbeat/expiry/takeover/lost-lease/backoff/draining tests.
- [ ] Test build/codec mismatch is not claimed and API admission sees no compatible worker.
- [ ] Implement database-time lease calculations and zero-row lost-lease handling.
- [ ] Add bounded recovery classification and cancellation-only takeover behavior.
- [ ] Prove credential rotation/revision drift mid-Run performs no Provider/Gateway I/O and enters `needs_reconciliation`; never continue an old Run with a new key slot.
- [ ] Prove same-logical-unit recovery reuses reservation/started accounting and deterministic events, increments only attempt, and short-circuits after a committed post-result Checkpoint.
- [ ] Add Dockerfile assistant-worker target and Compose service with the same database/provider/private-MinIO configuration needed by the API.
- [ ] Add a worker healthcheck that validates a fresh compatible registration rather than merely checking that a Python PID exists.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_worker_registry.py \
  backend/tests/test_durable_worker_lease_postgres.py \
  backend/tests/test_durable_run_recovery.py -q
~~~

Commit: `feat(ai): run durable assistant leases in a worker`

### Task 6: Persist the Main Agent Loop at Execution Boundaries

- [ ] Add failing scripted tests for pre/post Provider, Capability group, Manifest activation, waiting continuation, completion, and memory units.
- [ ] Move runtime admission to immediately before Run insertion and pass the selected immutable `runtime_kind` into `create_run`; prove fallback is impossible after a durable Run exists.
- [ ] Queue Main Agent Runs from the API; never call `_run_chat_background` for `runtime_kind=main_agent`.
- [ ] Materialize the base Manifest/policy/budget/obligation/Provider transcript and first Checkpoint atomically.
- [ ] Persist reservations in a `prepared` logical unit, then commit a distinct per-call/round `started` CAS immediately before external I/O; no Capability is charged started before Gateway validation/`mark_started`.
- [ ] Reconstruct exact protected Provider messages, grants, frames, and validate transcript/continuation before every resumed Provider request.
- [ ] Implement durable Skill activation as process-local stage plus one post-lineage result transaction acting as `ManifestEffectLifecyclePort.accept`; test every take/lineage/digest/CAS/cancel/lease/crash/replay failure and prohibit candidate residue or duplicate child append.
- [ ] Issue fresh authorization evidence after recovery; never persist/replay old evidence or credentials.
- [ ] Prove an uncommitted read/compute retry uses the same logical unit and does not consume a second round/call budget.
- [ ] Keep `ASSISTANT_MAIN_AGENT_MODE=off`/Legacy behavior unchanged.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_main_agent_runner.py \
  backend/tests/test_durable_provider_continuation.py \
  backend/tests/test_durable_budget_obligation_recovery.py -q
~~~

Commit: `feat(ai): execute main agent runs from checkpoints`

### Task 7: Make SSE Replay and Stop Database-Driven

- [ ] Remove stream-attachment/background-thread checks from Main Agent decisions only.
- [ ] Test disconnect during Provider work; the Run continues.
- [ ] Test reconnect from older/equal/newer cursors, internal sequence gaps, two readers, and a duplicate last event caused by uncertain client cursor persistence.
- [ ] Update frontend reducers to deduplicate by Run/sequence/event identity and preserve active waiting/recovering state.
- [ ] Test cancellation while queued/running/recovering/cancelling and lease expiry during cancellation.
- [ ] Force PostgreSQL stop versus Provider result, Capability result, activation accept, `ready_for_memory`, memory finalizer, and duplicate cancellation finalizer. Exactly one legal outcome commits; ordinary result never overwrites `cancelling`, and post-`ready_for_memory` stop returns `run_finalizing`.
- [ ] Preserve current Legacy event names/payloads when Main Agent mode is off.

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_durable_run_streaming.py -q
npm --prefix frontend run test -- src/features/assistant
~~~

Commit: `feat(ai): replay durable assistant events by cursor`

### Task 8: Implement Ordered Idempotent Terminal Memory

- [ ] Write failures first for Run/message/content-digest-idempotent L0 final output, conflicting final digest, L1, multiple active Skill namespaces, fact provenance, crash before/after memory apply, duplicate finalizer, and memory-provider failure.
- [ ] Add stable package/namespace L2 APIs alongside unchanged Legacy name APIs.
- [ ] Enter `ready_for_memory` before terminal state and keep the conversation active lock until memory is resolved.
- [ ] Apply the complete prepared L1/L2 set in one transaction with expected revisions and `last_applied_run_id`.
- [ ] Never write waiting/failed/cancelled/provisional/fallback-discarded state.
- [ ] Prove memory failure is explicit but does not erase a successful user response.
- [ ] Prove public status/SSE stays nonterminal through `ready_for_memory`, the active unique blocks a later Run, phase is not exposed as a status, and terminal completion appears only with committed/failed memory outcome.

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_durable_memory_commit.py -q
~~~

Commit: `feat(ai): finalize durable run memory once`

### Task 9: Crash Matrix and End-to-End Smoke

Kill the worker at each injected point:

- after reservation/prepare commit before `mark_started`;
- after `mark_started` commit before Provider/Capability adapter I/O;
- after Provider response before result commit;
- after Capability result before result commit;
- after `skill.inject` stage/lineage acceptance before lifecycle-accept result commit;
- after lifecycle-accept transaction commit before the worker observes success;
- after Manifest/Artifact upload before Checkpoint commit;
- after Checkpoint insert before aggregate pointer advance (transaction rollback injection);
- after final Message before memory application;
- during memory computation before apply;
- during heartbeat;
- after stop request before cancellation seal.

For every point assert:

- at most one valid lease owner;
- exact committed transcript/event/Manifest/policy/budget/obligation lineage;
- exact protected-message discriminator/linkage, complete grant-source values, and portable call-frame round-trip;
- no budget reset or duplicate committed event;
- an uncommitted read/compute unit may retry only under the same logical identity;
- no merely staged activation becomes active, no accepted activation appends twice, and a started rejected `skill.inject` remains charged once;
- no business write/draft/external call becomes visible;
- one terminal state and one memory application outcome.

- [ ] Start PostgreSQL + private MinIO + API + assistant-worker, run the read-only golden request, kill/restart the worker, reconnect SSE, and verify completion.
- [ ] Run the real PostgreSQL race suite and migration cycle.
- [ ] Run full backend tests, frontend tests/build, and Compose config validation.
- [ ] Scan persisted payloads/objects/events/log capture for forbidden runtime objects and secret corpus values.

~~~bash
backend/.venv/bin/python -m pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
docker compose -f deploy/docker-compose.yml config
git diff --check
~~~

### Task 10: Rollout and Documentation Evidence

- [ ] Record generated migration/parent, worker/API image build revision, codec/runtime versions, lease/recovery/orphan-grace formula values, test counts, evaluation digest, and Artifact bucket policy evidence.
- [ ] Demonstrate `off -> worker ready -> read_only -> off` with no active Run switching runtime.
- [ ] Demonstrate API admission fails safely when no compatible worker heartbeat exists.
- [ ] Demonstrate old compatible worker drains an old-build Run during a rolling deploy.
- [ ] Keep Plan 07 disabled; production Catalog still exposes no `interrupt_mode=durable` descriptor.
- [ ] Commit only scoped Plan 06 code/tests/config/evidence.

---

## 14. Exit Criteria

- Exactly one compatible worker lease can mutate an active Main Agent Run.
- Every semantic status mutation uses expected revision plus allowed source state; stop/result/memory/cancellation races converge to one legal outcome and no result overwrites `cancelling`.
- API/worker restart resumes from a verified immutable Checkpoint and exact frozen refs.
- Every adapter boundary has a pre-execution Checkpoint and every committed result is atomic with pointers/events.
- Provider transcript and Plan 03 continuation survive recovery without re-resolving aliases, catalog, model, Workflow, Agent, or Tool `latest` state; `runtime_instruction|runtime_context|runtime_completion` retain exact discriminators and revision linkage.
- Complete independent `EffectiveCapabilityGrant` bodies/`grant_source_digest` and portable call frames survive codec round-trip; recovery never copies classifier output into a grant or guesses a runtime stack.
- Manifest, policy, budgets, obligations, Provider messages, Artifacts, and events have verified digest/lineage.
- Skill activation becomes durable only in one post-lineage lifecycle-accept CAS; staged/discarded candidates leave no durable active state and accepted children are never duplicated.
- SSE disconnect does not cancel; cursor replay is ordered and frontend application is idempotent.
- Cancellation and lease loss prevent new work and converge to one terminal state.
- Every Main Agent event has a deterministic key and event/state/sequence commits atomically with tested PostgreSQL replay/conflict behavior.
- Private Artifact storage has bounded size, no anonymous access, and tested cleanup whose grace/live-Run/lease/inflight gates cannot race an active upload commit.
- New L2 identity is stable package ID + namespace with source-version/Run evidence.
- Terminal memory application cannot be overtaken by a later conversation Run.
- L0 final output is Run/message/digest-idempotent; `ready_for_memory` is internal/nonterminal, blocks later Runs, and does not accept cancellation after final content commitment.
- No Checkpoint contains an ephemeral/runtime object or credential.
- Every production Capability remains `none | read | compute` with `interrupt_mode=none`.
- Legacy Runs and approval behavior remain unchanged when Main Agent mode is off.

## 15. Handoff to Plan 07

Plan 07 may:

- add `DurableAgentCheckpointV2` and `v1 -> v2` migration for durable Workflow/Agent state;
- publish reviewed exact Workflow bindings with `interrupt_mode=durable`;
- create durable approval/input records and resume the exact Plan 03 waiting Capability;
- reuse this worker/lease/CAS/event/Artifact foundation.

Plan 07 inherits the exact cancellation fence: trusted resume/decision, API stop, worker result, and cancellation finalizer all compete through allowed-from-status plus `state_revision` CAS. It also preserves protected Provider-message discriminators, complete grants/frames, and the stage/lineage/accept ordering when a resumed Workflow later activates or calls Skills.

Plan 07 must not:

- reintroduce polling or a retained worker thread while waiting for a human;
- mutate Checkpoint v1 in place;
- use an interrupt ID as a replacement for the stable outer Capability continuation when a Workflow may pause more than once;
- open a business `draft`, `write_local`, or `write_external` side effect;
- bypass the Plan 03 transcript/waiting/sibling contract or Plan 05 fixed budgets.

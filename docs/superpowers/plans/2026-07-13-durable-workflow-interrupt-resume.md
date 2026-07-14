# MindAtlas Durable Workflow Interrupt and Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. Start only after Plan 06 is merged, its API + assistant-worker read-only recovery smoke is green, exactly one Alembic head exists, and Plan 06's PostgreSQL evidence proves allowed-source + `state_revision` CAS for every semantic transition, stop/result convergence, protected Provider-message round-trip, and post-lineage Skill-activation lifecycle acceptance. Plan 02B observation/OpenClaw cleanup is a non-blocking coordination track.

**Goal:** Make reviewed Workflow and Agent Capability executions advance through durable, versioned node/round boundaries; turn approval and structured-input nodes into nonblocking persistent Interrupts; survive API/worker restarts; and resume the exact frozen Capability, frame, node visit, Provider sibling position, policy/budget/obligation state, and human decision once without opening a business write.

**Architecture:** Do not serialize or retrofit a database Checkpointer into the current compiled LangGraph `WorkflowState`. Add a Main-Agent-only durable interpreter that derives a deterministic `DurableExecutionPlanV1` from an exact immutable Workflow/Agent version, executes at most one recoverable node/agent-round transition per prepared unit, and stores portable frame state inside `DurableAgentCheckpointV2`. A durable human node returns a pure portable `DurablePauseProposal`; after Plan 03 has converted the waiting Capability result into the complete `ProviderLoopContinuation`, the outer worker commits the Interrupt, Workflow state, continuation, Checkpoint, waiting Run transition, and SSE event in one CAS transaction, then releases the lease. Conversation-scoped token/decision endpoints only resolve the Interrupt and queue the Run; a worker later continues the child frame. The stable outer `ContinuationRef` identifies the root Capability invocation, not one particular Interrupt, so one Workflow Capability can pause multiple times before the Plan 03 Provider Loop receives its single final `ProviderWaitingResolution`.

**Tech Stack:** Python 3.11, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL 15, the Plan 03 Provider waiting/resume contracts, Plan 06 durable worker/Checkpoint/Artifact/event infrastructure, JSON Schema Draft 2020-12 subset validation, React, Zustand, Vitest, and the existing shared HITL components.

---

## 1. Position, Scope, and Non-Goals

This is Plan 07 of 10 and the second part of milestone M3.

Implemented here:

- `DurableAgentCheckpointV2` with an explicit `v1 -> v2` migration.
- Deterministic durable execution plans and portable Workflow/Agent frame stacks.
- Main-Agent-only execution of reviewed `none | read | compute` Workflow/Agent versions one recoverable boundary at a time.
- Persistent approval and structured-input Interrupts.
- Nonblocking `waiting_approval` and `waiting_input` transitions.
- Token/revision/idempotency-protected approve, reject, submit, cancel, and expiry decisions.
- Exact nested Workflow/Agent frame, branch, loop cursor, and child-continuation recovery.
- Correct integration with Plan 03 open transcripts, waiting siblings, and one final waiting resolution.
- Bounded human-wait suspension of Plan 05's active execution deadline without resetting any call/round/token/depth budget.
- Assistant UI reload/reconnect support using the current conversation-scoped route/store/event structure and shared HITL controls.
- One hidden/reviewed read/compute proposal-Artifact golden path with no business write.

Not implemented here:

- No business Draft row, local write, external request, or side-effecting Tool. Plan 08 opens the first controlled write.
- No CapabilityCall database ledger. `assistant_run_interrupt.capability_call_id` remains null until Plan 08 adds the real FK/record.
- No retry of a side effect with uncertain outcome.
- No conversion of every current Workflow node/version. Unsupported or unsafe durable graphs fail publication/admission; Legacy execution remains unchanged.
- No direct persistence of existing `WorkflowState.metadata`, `node_llms`, `HumanLoopRuntime`, callbacks, `Session`, compiled graph, client, lock, thread, generator, coroutine, or arbitrary Python object.
- No deletion or semantic mutation of `AssistantHumanApproval`, `HumanLoopRuntime`, `HumanLoopCoordinator`, workflow-test HITL routes, or Legacy chat approval routes.
- No per-user authentication system. The repository currently has an explicit local single-tenant Assistant Principal. This plan does not mislabel that boundary as multi-user authorization.

Activation and rollback boundary:

1. Keep `ASSISTANT_DURABLE_INTERRUPTS_ENABLED=false` while applying the migration and deploying v2-capable API/workers.
2. Prove v1 Run recovery still works, then publish the new hidden durable binding/version; do not mutate an existing Legacy binding.
3. Run the full hidden golden-path recovery/race gate with an explicit evaluation scope.
4. Enable new durable-interrupt admissions only after a compatible v2 worker heartbeat and stable token pepper are present.
5. Rollback sets the flag false for new admissions and disables the durable binding from new Manifests. It does not invalidate existing Interrupts or change an active Run's frozen descriptor.
6. Keep the compatible worker, token pepper, private Artifacts, APIs, and v2 codec available until every existing waiting/queued/running v2 Run drains, cancels, expires, or is reconciled.

---

## 2. Repository Facts and Start-State Gate

Task 0 must re-read the post-Plan-06 repository. At plan-authoring time, the concrete Legacy anchors are:

- `backend/app/assistant/workflow/engine/state.py` puts `metadata`, `node_llms`, callbacks, and other runtime objects inside `WorkflowState`.
- `backend/app/assistant/workflow/engine/execution_services.py` injects `HumanLoopRuntime` into metadata.
- `backend/app/assistant/workflow/engine/node_builders/human_in_loop_node.py` calls `runtime.create_and_wait(...)`.
- `backend/app/assistant/workflow/human_approval_runtime.py` stores `AssistantHumanApproval`, registers a `threading.Event`, and polls every 0.5 seconds.
- `backend/app/assistant/workflow/engine/stream_runtime.py` runs a compiled graph in another daemon thread.
- `backend/app/assistant/workflow/engine/node_builders/workflow_call_node.py` and `container_runtime.py` build nested in-memory execution.
- `backend/app/assistant/router.py` uses conversation-scoped routes; it does not have the unscoped `/api/assistant/runs/...` route proposed by the old draft.
- `frontend/src/features/shared/hitl/*` already owns `HumanApprovalCard`, fields, action bar, and status UI.
- `frontend/src/features/assistant/hooks/useChat.ts`, `stores/chat-store.ts`, `api/index.ts`, `types.ts`, and `components/MessageItem.tsx` own assistant event/state/render behavior.
- `smart_capture` has read, LLM, two human nodes, write Tools, code execution, and a nested Workflow. It cannot be enabled wholesale in Plan 07 because its frozen side effect remains write-capable.

Plan 06 handoff must provide:

- one compatible registered assistant worker and lease/CAS repository;
- strict Checkpoint v1 codec/migration registry;
- exact persisted Plan 03 Provider messages and `ProviderLoopContinuation`;
- persisted Plan 05 policy/budget/obligation revisions;
- private durable Artifact storage;
- database-driven public/internal events and cancellation;
- production Main Agent surfaces still restricted to `interrupt_mode=none`;
- one transition API whose SQL predicate contains the exact allowed source status plus expected `state_revision`, with stop incrementing that revision, ordinary results accepting only `running`, and only the cancellation finalizer producing `cancelled` from `cancelling`;
- PostgreSQL two-Session stop/result, stop/`ready_for_memory`, stop/memory-finalizer, and duplicate cancellation-finalizer convergence evidence;
- lossless `runtime_instruction | runtime_context | runtime_completion` Provider-message discriminators and revision linkage; and
- the exact `stage -> Plan 03 lineage validation -> ManifestEffectLifecyclePort.accept` activation boundary, with no durable candidate state before lifecycle acceptance.

Stop and amend Plan 06 before implementation if it reduced the Plan 03 continuation, did not persist the original exposed surface, cannot hold an open waiting transcript, lacks a `prepared -> started -> result` boundary, allows a result to overwrite `cancelling`, downcasts a protected Provider message, or makes a staged Skill activation durable before post-lineage lifecycle acceptance. Plan 07 consumes those contracts and must not implement a second Run status machine, reduced message codec, or parallel activation ledger.

Plan 02B production observation, temporary-selector removal, and OpenClaw legacy deletion are not Plan 07 prerequisites. Task 0 records their coordination status only; no Plan 07 module imports or depends on those temporary contracts.

---

## 3. Locked Cross-Plan Contract Amendments

Plan 07 is additive and versioned. It does not silently reinterpret already published bindings or Checkpoints.

### 3.1 Outer Checkpoint v2

`DurableAgentCheckpointV2` adds exactly:

- `workflow_state: DurableWorkflowStateV1 | None`;
- `active_capability_continuation: ContinuationRef | None`;
- a versioned `DurableExecutionUnitV2` whose kind set adds `workflow_node | agent_round | interrupt_resume` to Plan 06's Provider/capability/completion/memory units;
- an exact pending Interrupt reference whose immutable `BudgetSuspensionStateV1` binds the waiting Checkpoint to its unchanged parent Plan 05 budget revision/digest; and
- a next action that distinguishes `resume_child`, `continue_child`, `resume_provider_loop`, and `expire_or_cancel_child`.

`migrate_checkpoint_v1_to_v2` maps each v1 execution unit losslessly into the corresponding v2 kind, preserves every other v1 field/digest meaning, and fills new fields with null/empty values. It does not alter transcript, Manifest, budgets, obligations, or event identity. A Plan 06 active read-only Run can continue under v2; a v2 waiting Run requires a v2-capable worker.

### 3.2 Newly published durable descriptors only

Plan 02 classifies current Legacy `human_in_loop` execution as `legacy_blocking` and conservatively `draft`. Plan 07 does not mutate those immutable descriptor/binding snapshots.

For a newly published exact Workflow/Skill binding to declare `interrupt_mode=durable`:

1. derive and validate a `DurableExecutionPlanV1` from the exact immutable Workflow version;
2. require every reachable node/target to have a supported durable adapter and complete frozen dependency closure;
3. require every business side effect to be `none | read | compute` in this plan;
4. treat the persistent Interrupt/Checkpoint/Event rows as runtime control bookkeeping, not a business `draft` side effect;
5. calculate the Workflow's business side-effect maximum from its actual non-control nodes/targets;
6. publish a new binding/Skill version using a versioned binding-snapshot extension that freezes the durable plan digest; its descriptor declares `interrupt_mode=durable` and its binding/descriptor digests cover that extension. Do not add an unversioned nullable field that changes old descriptor fixed vectors.

Existing published `legacy_blocking` bindings never change by deployment. A Workflow containing `create_entry`, `update_entry`, remote HTTP, a business Draft writer, unsupported code execution, or any unknown target remains unavailable to the Plan 07 Main Agent.

### 3.3 Stable outer continuation across multiple pauses

The first pause from one root Capability invocation returns:

~~~python
ContinuationRef(
    continuation_type="durable_capability_invocation",
    contract_version=1,
    reference_id=str(root_frame_id),
    payload_digest=root_invocation_digest,
)
~~~

`root_invocation_digest` covers Run, exact call ID/input digest, root frame/target/version/plan/Manifest/policy identity. It deliberately does not identify the current human Interrupt row.

If the child resumes and reaches another human node, the same outer Capability remains waiting. The worker creates a new Interrupt and updates Workflow state, but does not call `resume_provider_loop` and does not replace the Plan 03 waiting call's `ContinuationRef`. Only when the root child becomes `completed | failed | cancelled` does the worker construct one trusted `ProviderWaitingResolution` matching the original outer continuation.

This is required because Plan 03 correctly forbids another `waiting` result as the resolution of a waiting Tool Call.

### 3.4 Active execution deadline during human wait

Plan 05's call/round/token/depth/repeat budgets never reset or increase. Human wait is separately bounded and does not consume worker active-execution time.

Use a sibling contract rather than silently changing Plan 05's frozen `BudgetLedgerState` shape:

~~~python
class BudgetSuspensionStateV1(FrozenContract):
    contract_version: Literal[1] = 1
    run_id: UUID
    interrupt_id: UUID
    parent_budget_revision_id: UUID
    parent_ledger_revision: int
    parent_ledger_digest: str
    suspended_at_utc: datetime
    remaining_active_ms: int
    human_wait_expires_at_utc: datetime
    suspension_digest: str
~~~

The canonical payload and `suspension_digest` are immutable request identity stored on the Interrupt row. The digest covers every field above except itself. `parent_budget_revision_id`, revision, and digest must match the waiting Checkpoint and the Run's current exact `assistant_run_budget_revision`; recovery never searches for a current/latest budget. An unversioned extra JSON object in a Checkpoint, Interrupt, Run metadata, or budget revision is forbidden.

Pause semantics are locked:

1. Use PostgreSQL `transaction_timestamp()` for `suspended_at_utc` and compute `remaining_active_ms = max(0, floor(parent.deadline_at_utc - database_now))` in milliseconds. Flooring and clamping may shorten but never extend the active allowance.
2. If no positive active time remains, do not create an Interrupt; enter the existing Plan 05 budget-exhaustion path.
3. Set `human_wait_expires_at_utc` equal to the Interrupt's server-derived `expires_at`; Skill/model input and token rotation cannot choose or extend it.
4. Commit the Interrupt, suspension state, waiting Checkpoint, and `running -> waiting_*` Run transition together. Keep `current_budget_revision_id` on the exact parent revision, set aggregate `Run.deadline_at=NULL`, and start no Provider/Capability reservation while waiting.
5. The waiting Checkpoint plus Interrupt suspension state is the complete portable clock truth. A crash/restart or token rotation while waiting leaves `remaining_active_ms`, every usage/reservation counter, and the parent ledger bytes unchanged.

Resolution semantics are locked:

1. A terminal Interrupt status closes the suspension. If the frozen outcome queues more execution, the same transaction requires the Run's current budget pointer/digest still equal the suspension parent and derives one ordinary Plan 05 `BudgetLedgerState` child revision.
2. That child copies limits, owner limits, all call/round/token/depth/repeat usage, reservations, denial count, and `started_at_utc` byte-for-byte; only ledger revision, `deadline_at_utc = database_now + remaining_active_ms`, and the resulting `ledger_digest` change.
3. Store the new child as a normal `assistant_run_budget_revision`, append one resume-ready outer Checkpoint v2 with `DurableExecutionUnitV2(kind="interrupt_resume")`, link both from the Interrupt, advance the Run's Checkpoint/budget pointers, and copy the child deadline to `Run.deadline_at` in the same `waiting_* -> queued` CAS.
4. Rejection/expiry that follows a declared Workflow branch uses the same resume derivation. Cancellation/expiry that makes the Run terminal creates no active child budget revision and leaves aggregate `Run.deadline_at=NULL`.
5. An idempotent HTTP retry never creates another budget revision or queues the Run again.

Worker downtime while `running | recovering | queued` still consumes the absolute active deadline. Only a committed `waiting_approval | waiting_input` Run with one matching pending Interrupt suspends it. A later sequential Interrupt may suspend the remaining allowance again but can never increase it. Required fixed vectors cover pause-before-crash, crash-through-wait, token rotation, resume, second pause, running-worker downtime, expired-at-pause, parent-digest tamper, and idempotent resolution retry; every vector asserts nonincreasing `remaining_active_ms` and byte-identical non-time budget usage.

This is a versioned clarification for durable human wait, not Skill budget amplification.

---

## 4. Durable Execution Topology

Do not add a database saver to the existing compiled graph and do not serialize `WorkflowState`.

~~~mermaid
flowchart LR
    GW["Plan 02 Capability Gateway"] --> AD["Durable Workflow/Agent adapter"]
    AD --> PLAN["Frozen DurableExecutionPlanV1"]
    PLAN --> RUNNER["One-boundary durable runner"]
    RUNNER --> PRE["Plan 06 prepare Checkpoint"]
    PRE --> NODE["Ephemeral node/agent adapter"]
    NODE --> POST["Artifact + frame result"]
    POST -->|continue| RUNNER
    POST -->|human wait| PROP["Portable PauseProposal"]
    PROP --> PWAIT["Plan 03 complete waiting continuation"]
    PWAIT --> INT["One CAS: Interrupt + frame + Checkpoint + waiting Run"]
    INT --> API["Token/decision API"]
    API --> QUEUE["queued Run"]
    QUEUE --> RUNNER
    POST -->|root terminal| RES["ProviderWaitingResolution"]
    RES --> LOOP["Plan 03 resume + pending siblings"]
~~~

The Main Agent durable adapter and Legacy adapter are selected from explicit `runtime_kind` plus frozen descriptor mode. Ambient global state never selects them.

The durable runner is a deterministic interpreter over normalized nodes/edges, not a second free-form orchestration system. It reuses pure template/validation helpers and exact dependency resolvers where safe, but it does not invoke `graph.invoke()` for a durable frame.

The durable Workflow/Agent adapter never commits an Interrupt independently and does not add an unversioned field to Plan 03's `ProviderDispatchResult`. Through an injected worker-unit-scoped `DurablePauseEffectPort`, it stages one pure pause proposal keyed by the exact root call/continuation, then returns the ordinary `CapabilityResult(status="waiting", continuation=root_ref)`. After Plan 03 builds the original assistant-message/sibling waiting continuation, the outer worker consumes that exact staged proposal once and the Plan 06 result transaction persists both layers together. The staging port is ephemeral only within this bounded execution unit; a crash before commit loses it and leaves neither durable layer behind, so recovery safely re-enters the same deterministic human node visit.

---

## 5. Durable Contracts

### 5.1 Frozen execution plan

~~~python
class DurableNodePlanV1(FrozenContract):
    node_id: str
    node_type: str
    config_digest: str
    outgoing_edges: tuple[DurableEdgeV1, ...]
    adapter_key: str
    business_side_effect: SideEffectClass
    may_interrupt: bool
    dependency_refs: tuple[FrozenExecutionDependencyRef, ...]


class DurableExecutionPlanV1(FrozenContract):
    contract_version: Literal[1] = 1
    target_kind: Literal["workflow", "agent"]
    target_version_id: UUID
    target_digest: str
    entry_node_id: str
    nodes: tuple[DurableNodePlanV1, ...]
    plan_digest: str
~~~

The plan is derived at publication/admission from the exact immutable target snapshot, canonicalized, digest-checked, and stored/referenced by the versioned binding snapshot. Resume recomputes the plan only from the same immutable snapshot and requires the same digest; it never reads Draft/current/latest state.

### 5.2 Portable Workflow/Agent state

~~~python
class DurableWorkflowStateV1(FrozenContract):
    schema_version: Literal[1] = 1
    run_id: UUID
    root_frame_id: UUID
    root_invocation_digest: str
    frame_stack: tuple[DurableCallFrameV1, ...]
    pending_interrupt_id: UUID | None
    terminal_output_artifact_id: UUID | None


class DurableCallFrameV1(FrozenContract):
    frame_id: UUID
    parent_frame_id: UUID | None
    invocation_call_id: str
    owner_skill_package_id: UUID | None
    owner_skill_version_id: UUID | None
    target_kind: Literal["workflow", "agent"]
    target_id: UUID
    target_version_id: UUID
    target_digest: str
    execution_plan_digest: str
    current_node_id: str | None
    node_visit_id: str | None
    node_visit_ordinal: int
    execution_attempt: int
    phase: Literal[
        "ready",
        "executing",
        "waiting",
        "child_active",
        "completed",
        "failed",
        "cancelled",
    ]
    node_state_artifact_id: UUID | None
    node_output_artifact_ids: tuple[UUID, ...]
    branch_decisions: tuple[DurableBranchDecisionV1, ...]
    loop_cursors: tuple[DurableLoopCursorV1, ...]
    child_frame_ids: tuple[UUID, ...]
    agent_loop_continuation: ProviderLoopContinuation | None
~~~

Rules:

- Frame IDs and `node_visit_id` are deterministic UUIDv5/digest identities from root invocation + parent path + node visit ordinal; retries do not create a new logical visit.
- `execution_attempt` may increase after a crash while `executing`; the node visit and budget reservation remain the same.
- Node state/output beyond strict inline limits is stored as immutable private Artifacts.
- A child frame is pushed once, parent becomes `child_active`, and pop/result application happens atomically.
- Branch decisions and loop cursors are persisted before the next node executes; recovery never recomputes a committed choice.
- `agent_loop_continuation` uses the existing Plan 03 contract for a reviewed nested Agent with fixed frozen tools. It is not a Legacy Agent object/graph.

### 5.3 Pause proposal staging port

~~~python
class DurablePauseProposalV1(FrozenContract):
    contract_version: Literal[1] = 1
    run_id: UUID
    root_call_id: str
    root_continuation: ContinuationRef
    frame_id: UUID
    node_id: str
    node_visit_id: str
    interrupt_id: UUID
    kind: Literal["approval", "input"]
    request_payload: dict[str, JsonValue]
    field_schema: dict[str, JsonValue] | None
    initial_values: dict[str, JsonValue]
    proposed_workflow_state: DurableWorkflowStateV1
    proposal_digest: str


class DurablePauseEffectPort(Protocol):
    def stage(self, proposal: DurablePauseProposalV1) -> None: ...
    def consume_exact(
        self,
        *,
        root_call_id: str,
        continuation: ContinuationRef,
    ) -> DurablePauseProposalV1: ...
~~~

`interrupt_id` is derived deterministically from the same logical interrupt key, so the proposed Workflow state can reference it before insertion and a retry converges on the same row. The port is injected by the outer durable worker for one prepared Provider/Capability unit. It rejects duplicate/mismatched calls, is cleared in `finally`, and is never serialized. A Plan 03 waiting result with no exact proposal, or a proposal without one matching waiting result, is `durable_pause_protocol_error` and cannot commit a waiting Run.

### 5.4 Ephemeral context

~~~python
@dataclass(slots=True)
class EphemeralWorkflowContext:
    session_factory: Callable[[], Session]
    provider_resolver: ProviderResolver
    capability_gateway: CapabilityGateway
    artifact_store: ArtifactStore
    event_sink: EventSink
    cancellation_probe: CancellationProbe
    clock: Clock
    exact_dependency_resolver: ExactRuntimeDependencyResolver
    node_adapters: DurableNodeAdapterRegistry
~~~

It is reconstructed after every claim from application wiring plus exact frozen references. It never enters a Checkpoint, Artifact, Provider message, event, log, or model-visible result. The Plan 06 recursive forbidden-type assertion includes this type and every field family.

---

## 6. Supported Durable Node/Target Matrix

Task 0 updates spellings from the exact merged node catalog. Initial Plan 07 production support is fail-closed:

| Node/target | Plan 07 durable behavior |
|---|---|
| `start` | deterministic input Artifact/state initialization |
| `output` | deterministic final Artifact/result projection |
| `if_else` | pure condition evaluation; persist chosen handle before next node |
| `variable_assign` | pure portable value update |
| `llm` / parameter extractor | one exact-model compute unit; pre/post Checkpoint; no streaming state object |
| `tool` | only exact frozen `none`, `read`, or `compute` Tool through Gateway |
| `knowledge` | exact frozen read dependency through Gateway; no ambient latest KB binding |
| `human_in_loop` in a new durable plan | create approval/input Interrupt; never call `create_and_wait` |
| `workflow_call` | push exact child Workflow frame and freeze parent |
| reviewed Agent target/node | child Agent frame using fixed exact tools and Plan 03 loop continuation; no Main Agent Skill injection inside it |
| `iteration` / bounded loop | persist item/iteration cursor and completed child outputs before next iteration |
| `code_executor` | unsupported in first release unless a separate deterministic, read/compute-only sandbox contract and crash tests are added to this plan before implementation |
| `http_request` | denied; remote side effect remains Plan 08+ |
| Tool/Workflow/Agent with `draft`, `write_local`, `write_external`, or `unknown` | denied in Plan 07 |
| Legacy `human_in_loop` binding | stays `legacy_blocking`; not auto-upgraded |

Publication/admission rejects:

- unsupported node type or adapter version;
- mutable/latest target lookup;
- incomplete dependency/model/credential closure;
- graph cycles without an explicit bounded loop contract;
- ambiguous/multiple entry nodes or invalid edges;
- reachable unsafe node even if a test input would not take that branch;
- nested Main Agent recursion or depth above Plan 05;
- `parallel_safe=true` for any interrupt-capable Workflow/Agent.

The hidden golden path uses only `start -> llm -> human_in_loop -> output` so it does not rely on optional loop/code/HTTP support.

---

## 7. One-Node/Agent-Round Transaction Protocol

### 7.1 Prepare execution

While holding a valid Plan 06 lease:

1. load/verify Run, outer Checkpoint v2, Manifest, policy, budget, obligations, Provider continuation, Workflow state, exact plan, and Artifact refs;
2. select the top frame and exact next node/agent round;
3. derive stable `node_visit_id` and expected input digest;
4. reserve/start the same Plan 05 call/round budget once where required;
5. append a pre-execution Checkpoint with frame `phase=executing` and incremented attempt only if this is a recovery retry;
6. commit and release all row locks/Session state.

### 7.2 Execute ephemeral adapter

- Reconstruct exact model/Tool/Workflow/Agent adapters after authorization.
- Check lease/cancellation immediately before invocation.
- Execute until one deterministic boundary only: node completed, child pushed, human pause requested, root completed, or safe failure.
- Never retain a `Session`, model stream, task, generator, callback, or waiter after returning the boundary result.

### 7.3 Commit result

One Plan 06 CAS transaction:

- verify same lease/revision/node visit/input digest;
- store outputs/large state as Artifacts;
- append branch/loop/frame/agent-continuation updates;
- append policy/budget/obligation revisions;
- append outer Checkpoint v2 and safe deterministic events;
- advance Run pointers/status.

Crash while `executing`:

- `none | read | compute` may re-execute under the same node visit/logical call identity and already-consumed budget;
- any unexpected unsafe classification enters `needs_reconciliation` before invocation;
- Plan 08 replaces this boundary with the CapabilityCall ledger for writes.

---

## 8. Durable Interrupt Persistence

Generate a real revision after reading the sole post-Plan-06 head:

~~~bash
cd backend
.venv/bin/alembic heads
.venv/bin/alembic revision -m "add durable run interrupts"
cd ..
~~~

Do not use `f2a3b4c5d6e7` or another predicted ID.

### 8.1 `assistant_run_interrupt`

| Column | Contract |
|---|---|
| `id` | UUID primary key |
| `run_id` | FK to `assistant_chat_run`, durable Main Agent only |
| `interrupt_key` | deterministic logical pause key |
| `kind` | `approval` or `input` |
| `status` | `pending`, `approved`, `rejected`, `submitted`, `cancelled`, or `expired` |
| `checkpoint_id` | exact waiting Checkpoint |
| `resolution_checkpoint_id` | nullable resume-ready Checkpoint; required only when a terminal decision queues further execution |
| `manifest_revision_id` | exact frozen Manifest revision |
| `owner_skill_package_id` | nullable immutable owner package |
| `owner_skill_version_id` | nullable exact owner version |
| `capability_call_id` | nullable UUID, always null in Plan 07; Plan 08 adds FK/population |
| `workflow_frame_id` | exact durable frame |
| `node_id` | exact node |
| `node_visit_id` | stable visit identity; retries share it |
| `request_revision` | interrupt-local request revision |
| `request_run_revision` | exact waiting Run state revision |
| `resolution_run_revision` | nullable Run revision committed by decision |
| `budget_revision_id` | exact pre-pause Plan 05 budget revision referenced by the waiting Checkpoint |
| `budget_suspension_state` / `budget_suspension_digest` | canonical immutable `BudgetSuspensionStateV1` and digest |
| `resolution_budget_revision_id` | nullable derived active budget revision; required only when a terminal decision queues further execution |
| `request_payload` / `request_digest` | bounded safe display/instruction body and digest |
| `field_schema` / `field_schema_digest` | normalized JSON Schema subset or null |
| `initial_values` | bounded validated defaults |
| `submitted_values` | accepted typed values only |
| `decision` | typed outcome or null |
| `comment` | bounded plain text or null |
| `resume_token_digest` | nullable HMAC digest; never raw token |
| `token_revision` | monotonic rotation counter |
| `resolution_request_id` | nullable caller-generated UUID idempotency key |
| `resolution_digest` | nullable internal canonical digest of the accepted decision envelope; excludes raw token/token digest and is never returned or emitted |
| `expires_at` | required bounded human-wait deadline |
| timestamps | created/resolved/updated token fields as required |

Constraints and indexes:

- unique `(run_id, interrupt_key)`;
- one `status='pending'` Interrupt per Run by PostgreSQL partial unique index;
- unique `(run_id, resolution_request_id)` where non-null;
- the partial unique is concurrent-state exclusivity only: one root Capability may accumulate multiple sequential terminal Interrupt rows and then create one new pending row; it does not impose one Interrupt for the lifetime of a Run;
- `budget_revision_id`, suspension parent revision/digest, waiting Checkpoint budget pointer, and current Run budget pointer must all agree when the pause commits;
- a queued resolution requires both `resolution_budget_revision_id` and `resolution_checkpoint_id` to identify the one derived active budget child and resume-ready `interrupt_resume` Checkpoint; a terminal Run outcome requires both to remain null;
- `input` requires a nonempty bounded schema; simple `approval` may omit it;
- immutable request identity, including budget suspension payload/digest, cannot update;
- repository/trigger permits only token rotation and one pending -> terminal resolution mutation;
- direct delete follows Plan 06 controlled Run purge only;
- audit events contain safe IDs/status/revisions and allowlisted non-content contract/build digests only; never token, resolution/suspension digest, submitted value, or comment content.

`interrupt_key` is derived from Run + root invocation + frame + `node_visit_id` + logical interrupt ordinal. It must not include `execution_attempt`, because crash retries must converge on the same row.

### 8.2 Approval/input outcome matrix

| Kind | Allowed schema | Allowed outcomes | Values |
|---|---|---|---|
| simple `approval` | null | `approved`, `rejected`, `cancelled`, or `expired` | empty only |
| editable `approval` | bounded object schema | `approved`, `rejected`, `cancelled`, or `expired` | allowed on approved/rejected if node contract permits |
| `input` | required bounded object schema | `submitted`, `cancelled`, or `expired` | required/validated for submitted |

The typed continuation adapter maps outcomes to the frozen node's branch/output contract. It does not invent a generic “approved” for an input node unless that exact durable node version declared the mapping at publication.

---

## 9. Structured Input and Render Contract

Persist a server-enforced JSON Schema Draft 2020-12 subset:

- object root only;
- string, number, integer, boolean, enum, and bounded arrays of supported primitive/object items;
- explicit `required`, length/range/item bounds, and `additionalProperties=false`;
- maximum nesting depth 4, field count 40, request JSON 64 KiB, submitted JSON 256 KiB;
- local `$defs` only if the normalizer expands and depth-checks them before storage; no remote `$ref`;
- no executable expression, HTML, script, file path, credential/secret widget, arbitrary component name, regex with unsafe complexity, or model-provided URL action.

The backend stores the normalized schema/digest and returns a separate allowlisted render model compatible with existing shared HITL fields (`input`, `textarea`, `switch`, `select`, `radio`, `checkbox_group`, `tag_selector`, `date`, `time`). The frontend never interprets arbitrary component code. All model text is rendered as text, not HTML.

Server validation is authoritative on resolution. Client validation is UX only.

---

## 10. Resume Token, Authorization, and Idempotency

### 10.1 Honest authorization boundary

The current Assistant routes have no per-user authentication/tenant ownership layer. Therefore:

- build endpoints under the existing conversation-scoped Assistant boundary and verify `conversation_id -> run_id -> interrupt_id` ownership;
- use the server-created local single-tenant Principal from Plans 04–05; no Principal/tenant/user field is accepted from HTTP input;
- require the replay token and exact revisions as additional guards;
- document that this is local single-tenant authorization, not multi-user isolation;
- if the deployment becomes multi-user before an authenticated Principal is wired to conversation ownership, `interrupt_mode=durable` must remain disabled.

### 10.2 Token protocol

- Add required secret `ASSISTANT_INTERRUPT_TOKEN_PEPPER` when durable Interrupts are enabled; never provide a real default in examples.
- Generate at least 32 random bytes with `secrets`, return URL-safe token text.
- Store `HMAC-SHA256(pepper, token)` and compare with constant-time equality. Do not use plain `SHA256(pepper || token)`.
- Token endpoint locks Run first, then Interrupt, verifies pending/current request+Run revision/deadline, increments `token_revision`, rotates the digest, and returns the raw token once.
- Rotation does not extend `expires_at` or active budgets.
- Raw token/digest is absent from logs, traces, analytics, SSE, Provider messages, Checkpoints, Artifacts, and browser persistent storage.

### 10.3 Feature and TTL settings

Add validated settings:

~~~text
ASSISTANT_DURABLE_INTERRUPTS_ENABLED=false
ASSISTANT_INTERRUPT_DEFAULT_TTL_SEC=86400
ASSISTANT_INTERRUPT_MAX_TTL_SEC=604800
ASSISTANT_INTERRUPT_COMMENT_MAX_CHARS=4000
ASSISTANT_INTERRUPT_TOKEN_PEPPER=
~~~

- `false` keeps every durable-interrupt descriptor unavailable for new admissions even if a reviewed binding exists; token/decision/worker support remains available for already frozen active Runs.
- Enabling requires a nonempty stable pepper, a compatible v2 worker heartbeat, and the reviewed local single-tenant authorization mode or a future authenticated Principal port.
- Default TTL may be lowered by deployment and must not exceed the checked-in seven-day hard ceiling; Skill/model content cannot choose or extend it.
- Comment limit may be lowered but not raised above the checked-in 4,000-character ceiling.
- Environment examples keep the flag false and pepper blank. Compose passes a real pepper only from deployment secret configuration.

### 10.4 Decision idempotency

Resolution request includes caller-generated `resolutionRequestId` plus expected token/request/Run revisions.

After bounded HTTP decoding, compute one canonical `resolution_digest` from interrupt ID, request ID, expected token/request/Run revisions, typed outcome, submitted values, and comment. The digest excludes the raw replay token and stored token digest. It is an internal equality guard, not a public correlation value: submitted values/comments may be low entropy, so GET/SSE/events/logs never expose it.

After conversation authorization and the Run lock, idempotency lookup precedes pending/token/current-revision checks:

- An existing `(run_id, resolutionRequestId)` owned by this Interrupt with the same digest returns the already stored terminal safe state with HTTP 200. It does not validate the consumed token, derive another budget revision, append events, or queue again, even if the client lost the first response.
- The same request ID with a different digest, or a request ID already owned by another Interrupt in the Run, is `resolution_idempotency_conflict`.
- Only a previously unknown request ID proceeds to pending status, token HMAC, expected revision, deadline, cancellation, decision, and schema validation.
- The first valid unknown request stores `resolution_request_id` and `resolution_digest`, consumes the token, resolves the row, applies the budget/Run transition once, and returns the safe render state.
- A different request ID after resolution is `interrupt_already_resolved` and returns safe current status.
- Stale/missing/rotated token, revision mismatch, expiry, cancellation, and conversation mismatch on a new request never queue the Run.

Terminal detail responses expose the winning `resolutionRequestId` but not `resolutionDigest`. The request ID is sufficient for multiple tabs and a lost-response client to identify which click won; altered reuse is still checked server-side against the stored internal digest.

---

## 11. Pause, Resolve, Resume, Reject, Cancel, and Expire

All paths lock `AssistantChatRun` first and then `assistant_run_interrupt` to avoid decision/cancellation/expiry deadlocks.

### 11.1 Create pause

The durable node first stages a bounded `DurablePauseProposalV1` through the injected worker-unit port. The proposal contains only the stable node visit, request/schema/default bodies and digests, intended kind/expiry policy, proposed Workflow state, and root continuation identity. It writes no row and does not change `ProviderDispatchResult`. The Gateway validates the ordinary waiting `CapabilityResult`; Plan 03 builds the exact open-transcript `ProviderLoopContinuation` and returns `ProviderLoopResult(status="waiting")` to the outer worker, which consumes the exact proposal.

Then, while holding the worker lease, one Plan 06 result transaction:

1. derive stable interrupt key/request/schema/value digests from persisted node inputs;
2. derive the immutable `BudgetSuspensionStateV1`, insert-or-read the same Interrupt, and require immutable request/suspension digest equality;
3. store proposal/partial outputs as Artifacts;
4. update the top frame to `waiting`, set `pending_interrupt_id`;
5. persist the stable outer Capability continuation and exact Plan 03 Provider continuation from the same waiting result;
6. add the Plan 05 approval/user-input obligation, keep the exact parent budget revision pointer, and store the sibling suspension state on the Interrupt;
7. append outer Checkpoint v2;
8. CAS Run from exact `status=running` plus expected `state_revision` to `waiting_approval` or `waiting_input`, set aggregate `deadline_at=NULL`, clear lease owner/expiry, and append safe event;
9. commit and return control to the worker loop.

If API stop wins the Plan 06 revision/source-status CAS first, this entire result transaction rolls back and neither Interrupt nor waiting continuation becomes durable. No worker/provider/HTTP request polls or sleeps. No Session/Future/Event/callback remains alive.

### 11.2 Resolve decision

The conversation-scoped HTTP endpoint performs one transaction only:

1. bounded-decode the request and compute its canonical internal `resolution_digest`; no Workflow/model/Gateway/user code runs;
2. authorize the conversation/Run/target-Interrupt relationship;
3. lock the Run, look up the owner of `(run_id, resolution_request_id)`, then lock the target Interrupt; the Run lock serializes Interrupt mutation for that Run;
4. if the request ID already exists, require it belongs to the target Interrupt, has the same digest, and names a terminal stored result; return that result with HTTP 200 without checking the consumed token/current pending revisions or mutating anything; otherwise return `resolution_idempotency_conflict`;
5. only for an unknown request ID, verify `waiting_*` state, pending status, token HMAC/revisions, request/Run revisions, deadline, and cancellation state;
6. normalize/validate decision/comment/values against the stored contract and persist the terminal Interrupt result/request ID/digest;
7. close the suspension through the terminal Interrupt status; when the frozen outcome continues, derive/link exactly one child Plan 05 budget revision and active deadline without resetting any other field;
8. append one resume-ready outer Checkpoint v2 referencing the resolved Interrupt and derived budget child, then CAS Run `waiting_* -> queued` with the exact expected `state_revision`, advance the Checkpoint/budget pointers/deadline, and append safe resolution/queued events; a terminal cancel/expiry instead uses the inherited Plan 06 terminal transition and leaves no resume-ready Checkpoint or active budget child;
9. commit.

It never constructs a Workflow/model/Gateway and never runs user code inline.

### 11.3 Worker resume

The worker:

1. claims queued Run normally;
2. loads the exact resume-ready Checkpoint, its resolved Interrupt, and the Interrupt's original waiting Checkpoint;
3. verifies root continuation, frame/node visit, request/schema/submission digests, suspension parent and resolution budget lineage, Manifest/policy/obligation lineage;
4. injects one typed immutable continuation result into that exact node visit;
5. checkpoints the node output/branch once;
6. continues child boundaries until it pauses again or the root Capability becomes terminal.

Every worker result—including a second pause and post-resume root completion—uses Plan 06's exact lease + expected revision + `status=running` result CAS. If stop has already advanced the Run to `cancelling`, the result cannot commit and the cancellation finalizer owns the terminal state. If the child pauses again, create a new pending Interrupt after the earlier row is terminal and keep the original outer continuation. If the root becomes terminal, create exactly one trusted `ProviderWaitingResolution`, call Plan 03 resume validation, append the waiting Tool Result in original call order, and continue the pending sibling suffix with fresh authorization evidence against the original exposed surface.

### 11.4 Reject/cancel/expire

- Approval rejection is typed node output; the frozen graph may branch, reprompt through a later new Interrupt, or finish.
- Input cancellation is typed only if the frozen node declares it; otherwise it cancels the root Capability/Run.
- Whole-Run cancellation serializes on the same Run-first lock order. If decision committed first, worker may process it before later cancellation; if cancellation committed first, resolution fails. No write can start in either order.
- In-flight pause and post-resume completion are not special cancellation paths: they inherit Plan 06's stop revision bump and result `status=running` predicate. Stop-first invalidates the result; result-first leaves the later stop to the next legal state transition. Plan 07 does not add a competing status writer.
- Expiry scanner first reads a bounded candidate ID list without locking Interrupt rows. For each candidate it opens a transaction, locks Run first and then the Interrupt, rechecks pending/expiry state, resolves `expired`, and either queues the typed expiry branch or safely cancels/fails according to the frozen node contract. Competing scanners may discover the same candidate, but the second CAS becomes a no-op/conflict without reversing lock order.
- Terminal Run cancellation of any still-pending Interrupt is idempotent.
- Cancellation of an outer waiting Provider call first terminates the durable child, then uses Plan 03 cancellation sealing for the waiting call and never-started siblings.

---

## 12. Provider Siblings, Nested Frames, and Multiple Interrupts

Plan 03 already requires interrupt-capable calls to execute sequentially and stop later siblings after one `waiting` result. Preserve that contract:

- completed sibling prefix remains persisted and is never replayed;
- waiting call has no Tool Result until root child terminal;
- pending sibling suffix remains internal `deferred`, with no fabricated Provider Tool message;
- original assistant message/surface/call order and latest current Manifest stay frozen in the continuation;
- no Provider request sees the open transcript;
- after root child terminal, exactly one result closes the waiting call, then pending siblings are re-authorized/executed in order.

Nested frames:

- parent `workflow_call`/Agent node commits `child_active` plus deterministic child frame push before child work;
- child may pause one or many times;
- child completion Artifact/result and frame pop advance parent atomically;
- depth/cycle/owner/policy/budget/cancellation ports are shared from Plan 05; a child does not create a new Run budget or base Manifest;
- two workers cannot advance parent and child separately because the whole frame stack belongs to one Run CAS.

Required tests include:

1. completed read sibling -> waiting Workflow -> pending read sibling;
2. one root Workflow with two sequential Interrupts before terminal output;
3. parent Workflow -> child Workflow -> human wait -> child complete -> parent complete;
4. reviewed child Agent round -> durable Workflow wait -> Agent complete;
5. cancellation at each frame depth;
6. stale/tampered original exposed surface, continuation, or frame digest fails before runtime work.

---

## 13. Durable Interrupt API

Use actual conversation-scoped Assistant routes:

~~~text
GET  /api/assistant/conversations/{conversation_id}/runs/{run_id}/interrupts/pending
GET  /api/assistant/conversations/{conversation_id}/runs/{run_id}/interrupts/{interrupt_id}
POST /api/assistant/conversations/{conversation_id}/runs/{run_id}/interrupts/{interrupt_id}/token
POST /api/assistant/conversations/{conversation_id}/runs/{run_id}/interrupts/{interrupt_id}/resolve
~~~

Contracts:

- pending list/detail returns safe render state, Run/message IDs, status/kind, request and Run revisions, token revision, expiry, and allowed actions; terminal detail additionally returns the winning `resolutionRequestId`. It never returns a raw token, token digest, internal `resolutionDigest`, suspension digest, or frame payload.
- token request requires expected request/Run revisions; response is `{token, tokenRevision}`.
- resolve requires token, `resolutionRequestId`, expected token/request/Run revisions, typed outcome, and bounded values/comment.
- resolve returns the same safe terminal state shape, including `resolutionRequestId`, for both the first commit and an exact idempotent retry.
- normal Run stop endpoint remains the whole-Run cancellation API.
- existing Legacy pending/decision routes remain unchanged and never accept durable tokens.

Stable HTTP/API error codes:

| Safe code | Typical HTTP |
|---|---:|
| `durable_interrupt_not_found` | 404 |
| `durable_interrupt_conversation_mismatch` | 404/403 per existing boundary |
| `interrupt_not_pending` | 409 |
| `interrupt_request_revision_mismatch` | 409 |
| `interrupt_run_revision_mismatch` | 409 |
| `interrupt_token_stale` | 409 |
| `interrupt_token_invalid` | 403 |
| `interrupt_expired` | 409 |
| `interrupt_run_cancelled` | 409 |
| `interrupt_values_invalid` | 422 |
| `resolution_idempotency_conflict` | 409 |
| `durable_interrupt_auth_mode_unavailable` | 503/feature disabled |

SSE events use stable names such as `human_interrupt_pending`, `human_interrupt_resolved`, `human_interrupt_expired`, and `run_status`. Payloads contain IDs/status/revisions only. Plan 06 event sequence/key semantics prevent duplicate reducer actions.

---

## 14. Frontend Integration Without a Duplicate HITL Stack

Do not create separate `DurableApprovalCard.tsx` and `DurableInputCard.tsx` copies.

Extend/reuse:

- `frontend/src/features/shared/hitl/types.ts`
- `frontend/src/features/shared/hitl/HumanApprovalCard.tsx` and field/action/status components, or add one thin `DurableInterruptCard.tsx` in the same shared folder that composes them
- `frontend/src/features/assistant/api/index.ts`
- `frontend/src/features/assistant/types.ts`
- `frontend/src/features/assistant/stores/chat-store.ts`
- `frontend/src/features/assistant/hooks/useChat.ts`
- `frontend/src/features/assistant/components/MessageItem.tsx`

Required behavior:

- model Legacy approval and durable Interrupt as an explicit discriminated union; do not overload fields by guesswork;
- on active Run reload/reconnect, fetch pending durable Interrupts and attach them to the Run's assistant message;
- deduplicate SSE by Run/sequence/event key before upserting a card;
- fetch/rotate a token only when an authorized user opens/submits the pending action; keep it in component memory, never local/session storage;
- generate and retain one `resolutionRequestId` for network retry of the same click;
- if POST response is lost, GET current status and treat the exact stored request ID as success; a different winning request ID means another tab/action resolved it, not that this click should be retried as successful;
- disable actions after terminal status/expiry/cancellation and display safe server validation/conflict text;
- preserve current Legacy `HumanApprovalRecord` and workflow-test behavior.

Frontend tests cover reload, reconnect replay, duplicate event, token rotation, lost response/idempotent retry, stale revision, expiry, cancellation, editable approval, input submission, and Legacy parity.

---

## 15. Locked Golden Path

Do not attempt to reuse the full current `smart_capture` graph; it contains `create_entry`, `update_entry`, code execution, two approvals, and nested follow-up writes.

Create one hidden/reviewed built-in evaluation package and exact Workflow version, canonical name `durable-proposal-review` (adjust only if Plan 01's final naming rules require another valid canonical name):

~~~text
start
  -> exact-model structured proposal (compute)
  -> editable durable approval OR structured input
  -> output approved proposal as private Artifact + bounded user text
~~~

The proposal contains generic MindAtlas note fields only and is not inserted into any business table. The published Capability:

- has complete exact model/dependency/output Schema closure;
- is `parallel_safe=false`;
- is `interrupt_mode=durable`;
- has business side effect `compute`;
- is visible only through the reviewed hidden/evaluation Skill/Profile configuration until rollout evidence passes;
- creates no `Entry`, Tag, Relation, Draft, HTTP request, or external side effect.

End-to-end scenario:

1. Main Agent calls the golden Capability.
2. Workflow computes proposal and enters waiting.
3. Kill API and worker; restart compatible versions.
4. Reload UI, fetch pending Interrupt, rotate token.
5. Edit/approve or submit fields.
6. Kill worker after decision commit and restart.
7. Resume exact node/frame, return final Artifact/result, close the original Provider call, run any pending sibling, and complete.
8. Verify one Interrupt decision, one node continuation, one Tool Result, one final Artifact, preserved budgets/obligations, and zero business writes.

Also verify rejection, cancellation, expiry, malformed values, two sequential scripted Interrupts, and nested child frames.

---

## 16. File Responsibility Map

Confirm exact post-Plan-06 paths in Task 0.

Create:

- `backend/app/assistant/workflow/durable/__init__.py`
- `backend/app/assistant/workflow/durable/contracts.py`
- `backend/app/assistant/workflow/durable/codec.py`
- `backend/app/assistant/workflow/durable/planner.py`
- `backend/app/assistant/workflow/durable/context.py`
- `backend/app/assistant/workflow/durable/adapters.py`
- `backend/app/assistant/workflow/durable/runner.py`
- `backend/app/assistant/workflow/durable/interrupts.py`
- `backend/app/assistant/workflow/durable/resume.py`
- generated `backend/alembic/versions/<revision>_add_durable_run_interrupts.py`
- backend focused tests named below
- exact hidden golden Workflow/Skill fixture/seed files under the final Plan 01/Workflow versioning structure
- optional `frontend/src/features/shared/hitl/DurableInterruptCard.tsx` only as a composition layer
- `frontend/src/features/shared/hitl/__tests__/DurableInterruptCard.test.tsx`

Modify:

- `backend/app/assistant/durable/contracts.py`
- `backend/app/assistant/durable/codec.py`
- `backend/app/assistant/durable/repository.py`
- `backend/app/assistant/durable/checkpoints.py`
- `backend/app/assistant/durable/recovery.py`
- `backend/app/assistant/worker.py`
- exact Plan 02 Workflow/Agent descriptor/adapters and Plan 03 dispatcher integration files
- `backend/app/assistant/models.py`
- `backend/app/assistant/schemas.py`
- `backend/app/assistant/router.py`
- `backend/app/config.py`
- `backend/.env.example`
- `deploy/.env.example`
- `deploy/docker-compose.yml`
- `backend/app/assistant/workflow/engine/node_builders/human_in_loop_node.py` only to select explicit Legacy vs durable adapter; do not make durable execution call the compiled Legacy node
- `backend/app/assistant/workflow/human_approval_runtime.py` only for an explicit stable error if a durable Run reaches Legacy blocking runtime
- `frontend/src/features/shared/hitl/*` as needed without breaking Legacy consumers
- `frontend/src/features/assistant/api/index.ts`
- `frontend/src/features/assistant/types.ts`
- `frontend/src/features/assistant/stores/chat-store.ts`
- `frontend/src/features/assistant/hooks/useChat.ts`
- `frontend/src/features/assistant/components/MessageItem.tsx`
- `.github/workflows/ci.yml`

The old draft paths `backend/app/assistant/workflow/nodes/...` are wrong for the current repository; node builders live under `workflow/engine/node_builders/`.

---

## 17. Implementation Tasks

### Task 0: Freeze Plan 06 and Characterize Legacy Workflow/HITL

- [ ] Record branch/commit, Python/dependency versions, generated Plan 06 migration/head, worker/build/codec versions, feature flags, and passing Plan 06 smoke evidence.
- [ ] Re-run Plan 06's real PostgreSQL two-Session transition suite and record proof that stop increments `state_revision`, every result requires an exact allowed source status plus expected revision, no ordinary result accepts `cancelling`, and only the cancellation finalizer produces `cancelled` from it. Stop and amend Plan 06 if any vector fails; do not patch a second Run state machine into Plan 07.
- [ ] Re-run Plan 06 codec vectors for lossless `runtime_instruction | runtime_context | runtime_completion` discriminators/revision linkage and its durable activation vectors for `stage -> lineage -> lifecycle accept`, including zero residue on reject/crash/replay.
- [ ] Run current Workflow/Agent/HITL/workflow-test/Assistant frontend/backend tests.
- [ ] Reproduce current `HumanLoopCoordinator` poll/thread behavior and process loss while approval is pending.
- [ ] Enumerate every nonserializable field reachable from current `WorkflowState`; add failing serialization characterization tests.
- [ ] Inspect normalized Workflow node/edge/version snapshots and exact Plan 02/03/05/06 import paths.
- [ ] Confirm the full `smart_capture` path is unsafe for Plan 07 and record the golden path's exact minimal graph.
- [ ] Confirm one Alembic head and every production descriptor still `interrupt_mode=none` before this plan.
- [ ] Record Plan 02B status as `pending | observing | complete` for coordination only. A reviewed Plan 02A plus full Plans 03–06 is the hard path; do not wait for or import OpenClaw observation/cleanup contracts.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_workflow_human_in_loop_runtime.py \
  backend/tests/test_workflow_human_in_loop_node.py \
  backend/tests/test_workflow_execution_context.py \
  backend/tests/test_workflow_call_node.py \
  backend/tests/test_workflow_test_run_stream.py \
  backend/tests/test_durable_run_recovery.py \
  backend/tests/test_durable_run_repository.py \
  backend/tests/test_durable_run_events_postgres.py \
  backend/tests/test_durable_run_streaming.py \
  backend/tests/test_durable_checkpoint_codec.py \
  backend/tests/test_durable_main_agent_runner.py -q
npm --prefix frontend run test -- src/features/shared/hitl src/features/assistant
(cd backend && .venv/bin/alembic heads)
~~~

### Task 1: Add Checkpoint v2 and Durable Execution Contracts

- [ ] Write strict frozen/round-trip/digest/forbidden-object tests for execution plan, Workflow state, frames, node visits, branches, loop cursors, and nested Agent continuation.
- [ ] Implement exact `CheckpointV1 -> V2` migration fixed vectors and prove no v1 digest meaning changes.
- [ ] Add deterministic frame/node-visit/root-continuation identities.
- [ ] Add the exact pending Interrupt/suspension reference without embedding ad hoc suspension JSON or changing Plan 05 `BudgetLedgerState` fixed vectors.
- [ ] Add size/depth/Artifact projection and ephemeral-context rejection.
- [ ] Unknown durable plan/state versions route to `needs_reconciliation` before runtime work.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_checkpoint_v2.py \
  backend/tests/test_durable_workflow_codec.py -q
~~~

Commit: `feat(ai): define durable workflow state contracts`

### Task 2: Build and Validate Frozen Durable Execution Plans

- [ ] Derive plans from exact immutable Workflow/Agent snapshots and complete dependency closures.
- [ ] Implement the supported matrix and fail every unsupported/unsafe/ambiguous/cyclic/unbounded case.
- [ ] Add new-publish-only `interrupt_mode=durable` descriptor generation without changing old bindings.
- [ ] Prove durable human bookkeeping does not authorize a business Draft class.
- [ ] Prove full `smart_capture` remains denied while the golden proposal plan is accepted as compute/nonparallel/durable.
- [ ] Re-run Plan 01 binding digest and Plan 02 descriptor/classification tests.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_execution_planner.py \
  backend/tests/test_capability_classification.py \
  backend/tests/test_capability_contracts.py \
  backend/tests/test_agent_skill_publish.py -q
~~~

Commit: `feat(ai): publish reviewed durable capability plans`

### Task 3: Implement One-Boundary Workflow/Agent Runner

- [ ] Write tests first for pre/post Checkpoints, stable retries, branch commit, loop cursor, frame push/pop, nested Agent round, lease loss, cancellation, and unsafe node denial.
- [ ] Implement adapter registry with explicit exact-dependency context.
- [ ] Execute no more than one node/agent-round boundary per prepared unit.
- [ ] Persist branch/loop/child state before following it.
- [ ] Reuse Gateway/policy/budget/obligation/cancellation ports; never directly execute a business Tool.
- [ ] Keep Legacy compiled LangGraph path unchanged for `runtime_kind=legacy`.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_workflow_runner.py \
  backend/tests/test_durable_nested_frames.py \
  backend/tests/test_durable_agent_frames.py -q
~~~

Commit: `feat(ai): execute durable workflow boundaries`

### Task 4: Add Interrupt Model, Generated Migration, and Repository

- [ ] Generate the migration from the sole current head; review exact parent/ID.
- [ ] Write PostgreSQL tests for unique logical rows, the `status='pending'` partial unique, multiple sequential terminal Interrupt rows plus one later pending row, request/suspension immutability, Run-first lock order, one-shot resolution, token rotation, idempotent request ID, and controlled purge.
- [ ] Add HMAC token service with required stable pepper and redaction corpus tests.
- [ ] Add bounded schema normalization/render/submission validation.
- [ ] Implement canonical `BudgetSuspensionStateV1` on the Interrupt with exact parent budget revision/digest plus optional paired `resolution_budget_revision_id`/`resolution_checkpoint_id`; do not add fields to Plan 05 `BudgetLedgerState` or store an unversioned dict.
- [ ] Add fixed vectors for pause -> crash/wait -> token rotation -> resume, running downtime, second pause, zero-time pause refusal, parent tamper, typed expiry branch, terminal cancellation, and idempotent retry. Assert `remaining_active_ms` never increases and every non-time budget field/reservation remains byte-identical.
- [ ] Run upgrade -> downgrade -> upgrade; downgrade refuses active/waiting durable Runs or unacknowledged Interrupt history.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_interrupt_models.py \
  backend/tests/test_durable_interrupt_repository_postgres.py \
  backend/tests/test_durable_interrupt_security.py -q
~~~

Commit: `feat(ai): persist durable human interrupts`

### Task 5: Replace Main-Agent Blocking Wait with Durable Pause

- [ ] Add a typed durable human boundary result and worker-unit `DurablePauseEffectPort`; never call `HumanLoopRuntime.create_and_wait` from the durable runner.
- [ ] Stage one pure pause proposal keyed to the ordinary waiting result/root `ContinuationRef`, let Plan 03 build the complete waiting continuation, consume the exact proposal, then atomically commit Interrupt + Artifacts + Workflow state + both continuations + obligations/budget suspension + outer Checkpoint + waiting status + events.
- [ ] Require exact Plan 06 lease, expected `state_revision`, and source `status=running` for the pause result transaction; force stop-versus-pause in two PostgreSQL Sessions and prove stop-first leaves no Interrupt/open transcript while pause-first leaves one cancellable waiting Run.
- [ ] Reject missing, duplicate, mismatched, or leftover pause effects before a waiting Run can commit.
- [ ] Crash before the result transaction and prove no orphan Interrupt/open transcript exists; retry must reproduce the same proposal/key/digests.
- [ ] Clear lease and return immediately.
- [ ] Assert no polling loop, sleeping worker, retained Session, Future/Event, generator, or provider stream.
- [ ] Make an accidental durable call into Legacy blocking runtime fail with stable `durable_blocking_runtime_forbidden` before creating a Legacy approval row.
- [ ] Keep explicit Legacy/workflow-test behavior unchanged.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_workflow_pause.py \
  backend/tests/test_workflow_human_in_loop_runtime.py \
  backend/tests/test_workflow_human_in_loop_node.py -q
~~~

Commit: `feat(ai): pause durable workflows without polling`

### Task 6: Add Conversation-Scoped Token/Decision APIs

- [ ] Add pending/detail/token/resolve schemas/routes/services under the existing conversation boundary.
- [ ] Test every error code, ownership mismatch, token revision/rotation, deadline, request/Run revision, cancellation, schema value, and comment bound.
- [ ] Implement the Run-lock-first idempotency ordering: lookup request ID and compare the internal canonical digest before pending/token/current-revision validation; only an unknown ID may enter the first-resolution path.
- [ ] Test a lost-response retry after token consumption returns HTTP 200/current state with the same public `resolutionRequestId`, while altered reuse or reuse by another Interrupt conflicts and a different post-terminal request returns already-resolved.
- [ ] Prove GET/resolve terminal shapes expose `resolutionRequestId` but never internal `resolutionDigest`, token/suspension digests, values, or comments; test two tabs identify the winning request ID.
- [ ] Prove exact retry appends no second event/Checkpoint, derives no second budget revision, and performs no second `waiting_* -> queued` CAS.
- [ ] Ensure HTTP resolution queues only; mock assertions prove no Workflow/Provider/Gateway construction.
- [ ] Add expiry scanner through the same repository CAS and lock order.
- [ ] Preserve Legacy approval endpoints and payloads.

~~~bash
backend/.venv/bin/python -m pytest backend/tests/test_durable_interrupt_api.py -q
~~~

Commit: `feat(ai): resolve durable interrupts through cas`

### Task 7: Resume Exact Child and Provider Waiting Call

- [ ] Load the exact resume-ready Checkpoint, resolved Interrupt, original waiting Checkpoint/frame, and apply one typed result once.
- [ ] Verify suspension parent -> derived resolution budget lineage before applying the human result; reject missing/mismatched child revision before adapter/runtime construction.
- [ ] Test worker crash before/after continuation node commit.
- [ ] Test a root Workflow that pauses twice while the outer `ContinuationRef` stays unchanged.
- [ ] Test nested Workflow and Agent frame waits.
- [ ] Only after root terminal, build one exact `ProviderWaitingResolution` and use Plan 03 resume validation.
- [ ] Preserve completed sibling prefix/pending suffix/original surface/Manifest/round usage and issue fresh authorization for later siblings.
- [ ] Test two decisions, decision vs cancellation, decision vs expiry, two workers, stale event action, and tampered continuation/frame/surface.
- [ ] Force stop-versus-post-resume pause/result/root-completion in two PostgreSQL Sessions and prove every result still requires Plan 06 `status=running` plus expected revision; stop-first converges through the cancellation finalizer and cannot be overwritten.
- [ ] Route irreconcilable target/build/plan/Checkpoint/Artifact drift to `needs_reconciliation`.

~~~bash
backend/.venv/bin/python -m pytest \
  backend/tests/test_durable_interrupt_resume.py \
  backend/tests/test_durable_multiple_interrupts.py \
  backend/tests/test_durable_provider_waiting_resume.py -q
~~~

Commit: `feat(ai): resume exact durable capability continuations`

### Task 8: Integrate Existing Shared HITL UI

- [ ] Add discriminated durable Interrupt types and API calls without breaking `HumanApprovalRecord`.
- [ ] Reuse shared fields/actions/status rendering; add only a thin durable wrapper if needed.
- [ ] Fetch pending Interrupt on active Run reload/reconnect and attach it to the assistant message.
- [ ] Rotate token in memory at action time; generate stable resolution request ID for retry.
- [ ] Deduplicate replayed events/actions and recover from lost POST response by comparing the retained click's `resolutionRequestId` with GET terminal state; a different ID is rendered as another action having won.
- [ ] Test approval, editable approval, input, rejection, expiry, cancellation, stale revision, token rotation, reconnect, and Legacy parity.

~~~bash
npm --prefix frontend run test -- src/features/shared/hitl src/features/assistant
npm --prefix frontend run build
~~~

Commit: `feat(ai): render durable assistant interrupts`

### Task 9: Publish and Verify the Golden Path

- [ ] Create/import/publish the exact hidden `durable-proposal-review` package/Workflow/Profile fixtures through Plan 01 services; do not mutate a shadow/Legacy version.
- [ ] Freeze exact model/dependency/Schema/plan/descriptor/binding/Manifest/policy digests.
- [ ] Run create -> compute -> wait -> kill API/worker -> reload -> token -> edit/approve -> kill -> restart -> resume -> final Artifact -> completed.
- [ ] Verify one decision/continuation/Tool Result/final Artifact and exact budgets/obligations/events.
- [ ] Verify one immutable suspension state and one derived resume budget revision, unchanged call/round/token/depth/repeat usage, and no active-time consumption across the committed human wait.
- [ ] Verify zero rows changed in Entry/Tag/Relation/business Draft tables and zero external requests.
- [ ] Verify rejection, cancellation, expiry, malformed input, two sequential Interrupts, nested child, pending sibling, and memory timing.
- [ ] Keep all other Legacy blocking human Workflows unavailable to Main Agent.

Commit: `test(ai): prove durable proposal review recovery`

### Task 10: Crash/Race Matrix and Final Verification

Kill or race at:

- after node prepare before adapter;
- after read/compute result before frame commit;
- before Interrupt insert;
- after Interrupt insert before outer pointer CAS (transaction rollback injection);
- API stop versus the pause result transaction;
- after waiting commit with no client;
- during token rotation;
- two simultaneous resolution requests, including same-ID same-body and same-ID altered-body cases;
- after first resolution commit before its HTTP response, followed by exact retry;
- decision vs stop;
- decision vs expiry scanner;
- after decision commit before worker claim;
- after resume claim before node continuation;
- after continued node output before Checkpoint commit;
- API stop versus post-resume second pause/root completion result;
- second pause in the same root Capability;
- nested child wait/pop;
- root completion before Provider waiting resolution commit.

For each prove one logical Interrupt/result/continuation, exact committed events, at most one derived resume budget revision, nonincreasing active-time allowance, one legal Run status outcome under the inherited Plan 06 CAS, no retained process state, and no business write.

- [ ] Run focused backend/frontend suites and real PostgreSQL race tests.
- [ ] Run migration cycle and API + assistant-worker + private MinIO integration smoke.
- [ ] Run full backend suite, frontend test/build, Compose config, and Plan 04–06 compatibility/evaluation gates.
- [ ] Confirm Legacy approval/workflow-test behavior with Main Agent mode off.
- [ ] Confirm every enabled Plan 07 descriptor is `none | read | compute`, nonparallel if interrupting, and has exact durable plan evidence.
- [ ] Scan DB/events/logs/browser storage for token, pepper, submitted secret corpus, Provider credentials, runtime objects, and raw Artifact content.

~~~bash
backend/.venv/bin/python -m pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
docker compose -f deploy/docker-compose.yml config
git diff --check
~~~

---

## 18. Exit Criteria

- A reviewed waiting Workflow/Agent survives API/worker restart with no polling or in-memory waiter.
- Durable state contains only frozen portable data, exact version/plan/digest refs, and private Artifact refs.
- Existing compiled `WorkflowState` and `HumanLoopRuntime` are never serialized into Checkpoints.
- Approval/input resolution is conversation-scoped, token/HMAC protected, schema validated, revision CASed, auditable, idempotency-checked before consumed-token/pending validation, idempotent for lost-response retry, and one-shot for execution.
- Terminal API state exposes the winning `resolutionRequestId` for client correlation but never the internal decision digest, token/suspension digest, submitted values, or comment.
- Human wait uses one versioned immutable `BudgetSuspensionStateV1` bound to the exact parent Plan 05 budget revision/digest; it suspends only active execution time and cannot increase call/round/token/depth/repeat budgets or extend its own expiry.
- A continuing resolution derives at most one ordinary Plan 05 budget child whose non-time usage/reservations are byte-identical; terminal cancellation derives none, and an exact HTTP retry derives nothing.
- Resume continues the exact node visit/frame once; committed branch/loop/child progress is not recomputed.
- One root Capability may pause multiple times while preserving one stable outer continuation and one eventual Provider waiting resolution.
- Completed Provider sibling prefix is reused, pending suffix resumes in order, and no Provider sees an open transcript.
- Duplicate decisions, reconnects, two workers, cancellation, and expiry cannot duplicate continuation.
- Pause, post-resume result, stop, and cancellation finalizer all use Plan 06's allowed-source + expected-`state_revision` CAS; no result can overwrite `cancelling` and Plan 07 owns no second status machine.
- The golden proposal Artifact path completes after restart and changes no business table or external system.
- Newly published durable descriptors are exact/reviewed; old `legacy_blocking` bindings remain immutable.
- Legacy chat/workflow-test HITL behavior is unchanged when Main Agent mode is off.

## 19. Handoff to Plan 08

Plan 08 may:

- add a real `CapabilityCall` ledger/FK to the nullable Interrupt field;
- add a versioned `interrupt_origin=workflow_node|capability_call` discriminator, backfill every Plan 07 row as `workflow_node`, and relax Workflow frame/node/visit nullability only behind an exact XOR check for a non-null same-Run `capability_call_id`;
- introduce a `CapabilityCallPauseProposalV1`/call-owned `ContinuationRef` whose key derives from the persisted logical call rather than a Workflow node visit;
- give every logical call/attempt/idempotency/output/reconciliation state durable identity;
- enable one tightly scoped approved local write;
- replace Plan 07's safe re-execution rule for uncommitted reads/computes with ledger-based result reuse where applicable.

Plan 08 must preserve:

- Run-first lock/CAS ordering;
- internal-digest/idempotency lookup before consumed-token and pending-state validation;
- the exact parent-budget suspension link and single derived resume revision;
- Plan 07 Section 11.1 as the only owner of the durable waiting aggregate: a ledger dispatcher may stage a pure call-owned proposal, but only the outer worker may atomically commit call status, obligation, Interrupt, suspension, Provider continuation, Checkpoint, waiting Run state, and events after Plan 03 waiting-lineage validation;
- Run-first serialization and Plan 07's Interrupt-before-dependent-row order for existing call-owned waits; no Plan 08 repository may lock a Call and then attempt to acquire its Run/Interrupt in reverse;
- the checked Interrupt identity union: Workflow-origin rows keep non-null frame/node/visit with null call; call-origin rows require one same-Run call and null Workflow fields; mixed/empty profiles fail migration/repository validation;
- one approval mechanism per golden call. A call-owned approval path must not retain a Workflow `human_in_loop` node or create a second Plan 03 waiting resolution;
- approval as satisfaction of an exact pre-derived call obligation only; it may not create/widen a Plan 05 grant or rewrite authorization/input/owner/target digests;
- Plan 06/07's prohibition on ordinary results from `cancelling`. A local transactional write must commit effect-start, business mutation, call success, result/Checkpoint/events together; an already-started external call requires a Plan 08 no-new-I/O settlement/reconciliation join and cannot be falsely finalized as cancelled;
- stable root Capability continuation across multiple Interrupts;
- one-shot/idempotent human decision semantics;
- original Provider surface/sibling order/transcript pairing;
- no side effect before a durable call record and approved exact input digest;
- `unknown -> needs_reconciliation` rather than automatic retry/fallback.

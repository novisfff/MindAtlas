# MindAtlas Universal Skill & Agent Loop Production Closure Design

**Date:** 2026-07-28

**Status:** Draft for written review; design decisions approved

**Baseline:** `docs/superpowers/specs/2026-07-13-mindatlas-universal-skill-agent-loop-design.md`

**Audited code baseline:** local `main` at `ae35f4b`

## 1. Purpose

The universal Skill and Agent Loop architecture is substantially implemented, but the repository is not yet production-closed. The runtime, immutable Skill packages, Capability Gateway, dynamic Provider Loop, durable Run/Interrupt/Lease infrastructure, CapabilityCall ledger, L2 package identity, and administration workbench exist and have broad local test coverage. The remaining gaps are concentrated in the production control plane, fresh-install runtime activation, schema lifecycle, the supported write surface, reproducible dependencies, production-shaped verification, and release evidence.

This design defines the final pre-GA closure program. It does not reopen the Legacy Router/Supervisor architecture. It establishes one clean supported product baseline and decomposes the remaining work into four independently reviewable implementation plans.

The intended final claim is:

> MindAtlas installs directly onto a clean pre-GA schema, creates a verified single-operator control plane, activates only a durable Main Agent runtime, exposes only the approved `create_entry` write path, and cannot pass its launch gate until reproducible automated verification plus one production-shaped rehearsal succeeds.

## 2. Evidence Behind This Closure Program

The closure program responds to the following observed repository state:

1. `ASSISTANT_RUNTIME_MODE` and deployment examples still default to `legacy`, while new Chat admission requires `main_agent` and the Legacy execution path has been removed. A fresh deployment can start successfully and then reject Chat with HTTP 503.
2. Plan 09 administration and evaluation routes remain unmounted in staging/production because no project-wide authenticated principal/RBAC dependency exists.
3. Base Skill package and Main Agent Profile mutation routes are mounted without a verified principal.
4. Plan 10 destructive migration preflight does not always require a current passed durable `deploy_b2` gate or a cryptographically verified operator identity.
5. The system was not launched, so production canary, calendar soak, legacy-zero, production restore, and production paired-shadow evidence do not exist.
6. Only `create_entry` has a completed approved-write golden path. `update_entry`, `merge_entry`, and `relation_followup` are not production-qualified.
7. The local virtual environment does not match the repository dependency constraints and `pip check` reports Docling/Hugging Face/Torch conflicts.
8. Production-critical PostgreSQL, MinIO, multi-worker, process restart, and live integration tests are present but skipped when their external test services are unavailable.
9. `backend/tests/test_durable_run_streaming.py` can replace the real `fastapi` package in `sys.modules` without restoring it, making selected test orderings unreliable.

The existing architecture remains the source of truth for immutable Skills, Manifest revisions, Capability ownership, budgets, obligations, durable execution, side-effect settlement, and memory identity. This closure design changes the production boundary around that architecture; it does not replace those contracts.

## 3. Goals

1. Establish a secure single-user/self-hosted Operator identity and session boundary.
2. Protect every production control-plane mutation with authenticated Principal, role, CSRF, request identity, and audit evidence.
3. Make a fresh installation activate a durable Main Agent without manual Legacy-era migration commands.
4. Separate process/bootstrap availability from Assistant admission readiness and make release/deployment acceptance checks reflect whether Chat can actually accept work.
5. Replace the create-then-drop Legacy migration chain with one pre-GA clean schema baseline.
6. Make the supported schema family and application/worker compatibility explicit and machine-verifiable.
7. Productionize only `create_entry`; explicitly reject the three unevidenced write branches.
8. Reproduce the supported Python and dependency environment from committed lock files.
9. Run release-critical verification against PostgreSQL, MinIO, API, and two Assistant workers without release-critical skips.
10. Require one complete production-shaped rehearsal and a server-derived launch gate before first release.

## 4. Non-Goals

1. Reintroducing IntentRouter, SkillRouter, Supervisor, Legacy AssistantAgent, blocking Legacy HITL, or Legacy runtime fallback.
2. Supporting in-place upgrade from a Legacy schema or restoring a Legacy runtime.
3. Adding multi-user organizations, tenants, invitations, OIDC, or cloud identity federation.
4. Generalizing the approved write contract beyond `create_entry`.
5. Redesigning existing Workflow DAGs, Tools, Agents, immutable Skill contracts, Provider Loop semantics, or durable execution contracts.
6. Adding a calendar soak requirement. Release uses reproducible automation and one production-shaped rehearsal.
7. Upgrading LangGraph/LangChain as an implicit side effect. The closure must first prove the dependency line deliberately selected by the repository.

## 5. Locked Product and Architecture Decisions

### 5.1 Runtime

- Every newly admitted Assistant Run is `main_agent`.
- `legacy` is not a selectable runtime for new work.
- Historical strings may remain only in archived evidence outside the supported live schema.
- Absence of a usable Main Agent is a readiness/admission failure, never a reason to select a second runtime.
- A separate kill switch stops new Runs without changing the runtime kind of existing Runs.
- Once a Run exists, all recovery remains on that Run. No second runtime or second Run is spawned as fallback.

### 5.2 Schema

- The current clean schema lineage, plus only the additive closure state owned by Plans 1 and 2, becomes the sole supported pre-GA baseline family, identified as `pre_ga_v1`.
- Fresh installation applies a new Alembic root chain and never creates Legacy Skill/HITL/Router schema.
- The old migration chain is archived for audit and excluded from Alembic discovery.
- Existing development databases are reset by default. Guarded stamp is allowed only when an independently computed structural schema fingerprint proves exact equivalence.
- Legacy and unknown schemas are rejected with `legacy_upgrade_not_supported` or `schema_incompatible`; they are not automatically transformed.

### 5.3 Identity

- MindAtlas has one self-hosted Operator account.
- The authorization vocabulary keeps `viewer` and `operator` roles so read and mutation dependencies remain explicit, even though the first release creates one Operator.
- Browser authentication uses a password and an HttpOnly session cookie.
- Caller-asserted identity/role headers, CLI strings, feature flags, environment labels, loopback origin, and CORS never mint an `OperatorPrincipal`. A cryptographically verified machine credential may mint only its separately defined narrower Principal.
- First initialization requires a server-owned one-time Setup Token in addition to the new Operator password.

### 5.4 Writes

- `create_entry` is the only production write Capability in the first release.
- `update_entry`, `merge_entry`, and `relation_followup` are absent from the production Provider Tool Surface and fail closed when called directly.
- Unsupported write intent is visible to the user and is never silently translated into `create_entry`.

### 5.5 Release Evidence

- No seven-day or fourteen-day soak is required.
- No Legacy canary, Legacy-zero window, or Legacy restore is required.
- Release-critical automation must use real PostgreSQL and MinIO plus two independent worker processes.
- One complete production-shaped rehearsal is mandatory.
- A current, unexpired, server-derived `pre_ga_launch` candidate consumed by the authenticated Operator into production launch control is mandatory.

## 6. Four-Plan Architecture

The closure program is risk-gated:

```text
Plan 1: trusted production control plane
    ↓
Plan 2: deterministic Main Agent bootstrap and readiness
    ↓
Plan 3: reproducible pre-GA clean schema
    ↓
Plan 4: supported write surface, release automation, rehearsal, launch gate
```

Each plan has an independent file boundary, migration boundary, focused test gate, full regression gate, evidence output, and explicit rollback rule. Each is executable as a standalone reviewed work package from its declared prerequisite checkpoint; “independent” does not make the risk-gated order optional. Later plans consume only the stable interfaces listed here.

### 6.1 Migration Boundaries

- Plans 1 and 2 use additive revisions on the known clean starting chain so each plan can be implemented, tested, and reverted at its own checkpoint.
- Plan 3 defines the pre-squash clean head as the audited starting head plus the accepted Plan 1 and Plan 2 revisions, proves that head is structurally equivalent to a fresh root, then archives the entire superseded chain.
- The Plan 3 root directly creates all live schema owned through the Plan 3 cut point. It is the only root in configured Alembic discovery.
- A schema change owned by Plan 4 is a normal child of that root and leaves one linear, sole-head `pre_ga_v1` chain. It cannot amend the reviewed root or recreate a transitional/Legacy object.
- “Clean baseline” therefore describes the supported ancestry and direct-from-empty result, not a promise that no reviewed child revision may ever follow the root.

## 7. Plan 1 Architecture: Single-Operator Production Control Plane

### 7.1 Persistent Model

Plan 1 introduces three production-owned records.

#### Operator account

The schema permits at most one account row. An initialized system requires exactly one enabled account, created with role `operator`. Its minimum fields are:

- UUID identity;
- role `viewer | operator`;
- Argon2id encoded password hash;
- password revision;
- failed-login count;
- failure-window start;
- locked-until timestamp;
- created, updated, password-changed, and disabled timestamps;
- last successful login timestamp.

The account row is not stored in `AppSetting.value_json`. It has database constraints and lockable revisioned state.

#### Operator session

Each successful browser login, including the session issued after successful initialization, creates one session row with:

- UUID session identity;
- Operator FK and password revision;
- session-MAC key identifier;
- HMAC-SHA256 digest of the random session token;
- HMAC-SHA256 digest of the CSRF token;
- created, last-seen, idle-expiry, absolute-expiry, revoked, and revoked-reason fields;
- creation request ID, user-agent digest, and bounded network-context digest;
- session state revision.

The raw session and CSRF tokens never enter the database, application logs, audit details, metrics, or error payloads.

#### Operator audit event

Control-plane events are append-only and include:

- event UUID and monotonic account/session/object revision where applicable;
- Operator and Session IDs;
- request ID;
- action and object type/ID;
- safe before/after digests;
- outcome and stable reason code;
- application build, schema family, and UTC time;
- bounded safe details with no secret or business content.

### 7.2 Password Contract

- Use Argon2id through a maintained Python library.
- Minimum parameters are memory cost 64 MiB, time cost 3, parallelism 2, 16-byte random salt, and 32-byte hash.
- The encoded hash carries its algorithm parameters so a successful login can rehash when the configured minimum increases.
- Passwords are never normalized beyond treating the submitted Unicode string as an exact secret; leading/trailing characters are significant.
- Minimum password length is 12 Unicode code points and maximum UTF-8 length is 1024 bytes.
- Password comparison and Setup Token comparison use constant-time library primitives.

### 7.3 Session and CSRF Contract

- Production requires a deployment-stable session-MAC key loaded from the secret store, with at least 256 bits of entropy, no repository/default value, and no reuse as the Setup Token, password pepper, provider credential key, or Capability idempotency key.
- The active key identifier is stored with each session. A controlled key-ring rotation may verify a bounded previous key while issuing only with the current key; removing a previous key durably revokes the sessions that depend on it and records a safe maintenance audit event.
- A missing, malformed, or ephemeral production session-MAC key makes initialization/login unavailable and Assistant readiness false. Process restarts with the same key do not invalidate sessions.
- Session token: 256 random bits from the operating-system CSPRNG.
- Session cookie: host-only, `HttpOnly`, `SameSite=Strict`, `Path=/`, and `Secure` outside explicit local-development HTTP mode.
- CSRF token: separate 256-bit random value in a `SameSite=Strict`, `Secure` production cookie that is readable by the SPA and copied to `X-MindAtlas-CSRF` for mutations.
- Session and CSRF HMAC inputs use distinct fixed domain-separation labels. The server verifies both token digests against the same active session row.
- Production credential and cookie traffic requires HTTPS at the trusted ingress. Login and initialization accept JSON only, require the configured canonical same-origin `Origin`/Fetch Metadata policy, and run with explicit non-wildcard CORS; cross-site form and login-CSRF attempts fail before credential verification.
- Idle expiry is 12 hours; absolute expiry is 7 days.
- Five failed password attempts within 15 minutes lock login for 15 minutes using database time.
- Successful login clears the active failure window.
- Password change revokes every existing session.
- Logout and revoke-all are immediately durable.
- Session refresh never extends absolute expiry.

### 7.4 Setup Contract

- `MINDATLAS_INITIAL_SETUP_TOKEN` is required before initialization and must contain at least 32 UTF-8 bytes.
- Initialization transmits it only in a dedicated setup authorization header, never in a URL, query string, cookie, CLI flag, or logged request body. It is setup authorization, not a Principal.
- Initialization locks the singleton state and account identity so two concurrent requests cannot create two Operators.
- The token becomes unusable as soon as initialization commits.
- Missing or invalid Setup Token returns 401 without revealing whether the submitted password or other initialization fields were valid.
- Production startup warns and readiness remains false when the system is uninitialized and no valid Setup Token is configured.

### 7.5 Principal Interface

Downstream plans consume only:

```python
@dataclass(frozen=True)
class OperatorPrincipal:
    operator_id: UUID
    role: Literal["viewer", "operator"]
    session_id: UUID
    authentication_method: Literal["password_session"]
```

and these dependencies:

```python
def require_viewer_principal(...) -> OperatorPrincipal: ...
def require_operator_principal(...) -> OperatorPrincipal: ...
def require_csrf(...) -> None: ...
```

The dependency is the only production HTTP path that creates an `OperatorPrincipal`. A separately authenticated machine/connector entrypoint may create its existing narrower non-Operator Principal, but can never use that identity to access the control plane, resolve an approval, or satisfy an Operator dependency.

### 7.6 Route Boundary

Unauthenticated browser routes are limited to:

- liveness;
- safe readiness status;
- initialization status/defaults;
- initialization with Setup authorization;
- Operator login.

Authenticated viewer reads may include safe Skill/Profile/Eval/runtime state. Apart from the one-time Setup-authorized initialization transaction, every control-plane mutation requires Operator plus CSRF, including:

- Skill package create/import/draft/publish/restore/alias/catalog state;
- Main Agent Profile draft/publish/runtime state;
- evaluation creation/cancel/gate operations;
- runtime configuration mutation;
- rollout preparation/activation/kill-switch changes;
- reconciliation decisions;
- launch-gate candidate creation/use/drift invalidation;
- Operator password/session/logout/revoke operations;
- any retained cleanup or schema administrative action.

Plan 1 removes the split where base Skill/Profile mutations are always mounted while only aggregate admin routes are protected. Production has one protected control-plane boundary. Cookie-authenticated browser data mutations outside that boundary also require Operator plus CSRF. Existing machine/connector endpoints remain separately authenticated and capability-scoped; they are not public routes, do not accept the Session cookie as machine authority, and do not gain control-plane permissions.

### 7.7 CLI Boundary

- No CLI argument or environment variable can supply a verified Operator ID or role.
- Normal control-plane mutation moves behind authenticated HTTP services.
- A retained local maintenance command must interactively verify the Operator password through the same authenticator and must record the same audit event. It cannot accept the password as a command-line flag or environment variable.
- Historical Plan 10 cleanup/migration commands become read-only in Plan 3.

## 8. Plan 2 Architecture: Main Agent Bootstrap and Readiness

### 8.1 Runtime Configuration

- Remove runtime selection between `legacy` and `main_agent`.
- Remove the default `legacy` value and empty rollout label from application configuration, `.env` examples, Compose, and deployment documentation.
- The durable active rollout control pointer is the runtime fact source.
- Add one explicit kill switch, `ASSISTANT_NEW_RUNS_ENABLED`, whose only effect is rejecting creation of new Runs.
- Write mode remains independently controlled and defaults to `off` until Plan 4 qualifies `create_entry`.

### 8.2 Trusted System Seed

Fresh initialization must not depend on an already-active Agent Loop to publish its own default Profile. Plan 2 therefore defines a revision-controlled `AssistantSystemSeedManifest` containing:

- schema version;
- default Main Agent Profile content and digest;
- built-in universal Skill package/version/resource digests;
- required Capability bindings and immutable target versions;
- required Provider/Model binding slots;
- build compatibility range;
- complete manifest digest.

The initialization path may consume this manifest only when:

- the system is uninitialized;
- no Operator exists;
- no published Main Agent Profile exists;
- no active rollout exists;
- the embedded manifest digest matches the build-owned expected digest.

This path creates immutable published system versions and a `system_bootstrap` audit/gate-use event. It cannot publish arbitrary caller content and cannot run after initialization. Subsequent changes use the normal enforced evaluation and publication gates.

### 8.3 Initialization Transaction

The initialization unit of work creates or verifies, in order:

1. Locale and core system defaults;
2. Operator account;
3. AI Credential and LLM Model;
4. system Tool/Workflow/Agent catalog;
5. built-in universal Skill packages from the trusted seed;
6. default published Main Agent Profile;
7. immutable prepared Main Agent rollout revision;
8. initialization state and audit evidence.

The transaction commits all durable state or none of it. Initial browser session creation follows the successful commit and is returned in the initialization response.

### 8.4 Activation

A prepared rollout cannot become active until the server revalidates:

- authenticated Operator and CSRF;
- expected rollout control revision;
- application build revision;
- Main Agent runtime contract version;
- checkpoint codec version;
- Capability feature digest;
- published Profile and Model identities/digests;
- enabled Package closure and gate-use evidence;
- at least one fresh, non-draining compatible Worker registration.

Initialization itself never activates the rollout. If a compatible Worker already exists, the browser may immediately follow the committed initialization response with a separate authenticated, CSRF-protected activation request using the newly created session. Otherwise initialization completes with `assistantBootstrap=pending_worker`; the UI polls readiness and offers that activation request when the Worker becomes compatible.

### 8.5 Readiness

`/health` remains process-only liveness and does not require an initialized Assistant or compatible Worker. It does not claim that the database, Operator, or Agent Loop is ready. `/ready` is the Assistant admission contract and is represented by:

```python
class AssistantReadinessSnapshot(FrozenContract):
    ready: bool
    reason_codes: tuple[str, ...]
    active_rollout_revision_id: UUID | None
    profile_version_id: UUID | None
    model_id: UUID | None
    compatible_worker_ids: tuple[str, ...]
    build_revision: str
```

Stable readiness reasons include:

- `system_not_initialized`;
- `operator_missing`;
- `operator_auth_unavailable`;
- `system_seed_invalid`;
- `profile_unpublished`;
- `model_unbound`;
- `rollout_inactive`;
- `runtime_closure_drift`;
- `worker_unavailable`;
- `schema_incompatible`;
- `new_runs_disabled`.

Plan 4 adds `pre_ga_launch_unapproved` for production admission. The public `/ready` response returns only `ready` and safe reason codes. The authenticated control plane may return object identities and compatibility diagnostics.

Compose startup dependencies and the Web/API bootstrap healthcheck consume `/health`; they must not wait for `/ready`, because the Operator needs the Web and control plane to initialize and activate the system. Chat UI admission, release-profile smoke tests, and deployment acceptance checks consume `/ready`. This preserves a truthful “can Chat accept a new Run?” signal without creating a bootstrap dependency cycle.

### 8.6 Admission

Admission first evaluates readiness, then freezes the exact active rollout/Profile/Model/Package/Capability closure into the new Run. Every newly inserted row uses `runtime_kind=main_agent`.

Failure before Run insertion returns a stable 503 reason and rolls back the empty Assistant message placeholder. Failure after Run insertion remains within durable recovery, cancellation, failure, or reconciliation for that exact Run.

There is no `no_active_rollout → legacy` branch and no Legacy daemon/generation fallback.

## 9. Plan 3 Architecture: Pre-GA Clean Schema Baseline

### 9.1 Baseline Family

The supported family is `pre_ga_v1`. The new Alembic chain starts with one root revision whose `down_revision` is `None` and whose upgrade produces the complete live schema at the Plan 3 cut point directly.

The baseline includes:

- every live table and column;
- primary, foreign, unique, and check constraints;
- indexes and partial predicates;
- PostgreSQL functions and triggers;
- immutable-history protections;
- schema/build/runtime compatibility markers;
- required seed contract metadata, but not deployment secrets or Operator credentials.

It does not include Legacy Assistant Skill, Legacy Human Approval, Router/Supervisor, Legacy provenance, or create-then-drop transitional schema.

### 9.2 Generation and Equivalence Proof

The baseline is generated from the current final SQLAlchemy/Alembic model against an empty PostgreSQL database, then reviewed as an immutable migration artifact. Two independent disposable databases prove equivalence:

1. Database A reaches the known pre-squash clean head—the audited starting head plus accepted Plan 1 and Plan 2 additive revisions—using the existing chain.
2. Database B reaches the new head using only the new baseline chain.
3. A canonical introspector compares their final live schema.

The canonical structural fingerprint covers:

- PostgreSQL namespaces, required extensions, enum/domain types, sequences, views, and materialized views;
- table and column names, types, nullability, defaults, and identity behavior;
- PK/FK/unique/check definitions and FK actions;
- indexes, uniqueness, expressions, and predicates;
- PostgreSQL functions and trigger definitions, including bodies, timing, events, and enabled state.

Migration history table contents, the baseline-family marker, seed-contract identities, and intentionally removed Legacy objects are not part of structural equivalence. Every excluded Legacy object must appear in a committed exact-name exclusion manifest with its type, reason, old-chain definition digest, proof of no live model/runtime reference, and expected absence in Database B. The comparator fails on any unmanifested difference. After applying only that manifest, Database A and Database B must have identical structural fingerprints.

A separate runtime schema identity digest binds the structural fingerprint to `pre_ga_v1`, the exact Alembic revision, and known seed-contract digests. Database B must match the committed expected runtime identity. Database A exists only to prove structural equivalence; it does not become a supported `pre_ga_v1` database until the guarded-stamp procedure succeeds.

### 9.3 Archive

The old revision chain moves to a read-only archive outside configured `version_locations`. An archive manifest records:

- relative path;
- old revision and parent;
- file SHA-256;
- chronological order;
- original final head;
- reason for archival;
- the design-deviation evidence digest.

Archived files are not imported by application or test runtime and cannot be passed to ordinary `alembic upgrade`.

### 9.4 Development Rebaseline

Default development migration is database reset and fresh baseline upgrade.

Guarded stamp is permitted only when:

- the database identifies itself as non-production;
- the current revision is the known pre-squash clean head;
- no Legacy table, column, trigger, or invalid L2 identity exists;
- the canonical structural fingerprint exactly equals the expected pre-GA live schema;
- current data invariants pass;
- the operator supplies an explicit local-maintenance acknowledgement;
- a sanitized before/after report is written.

There is no `--force` bypass. A shared, unknown, drifted, or Legacy database must be reset or handled outside this supported product path.

### 9.5 Downgrade and Compatibility

- The baseline does not downgrade to Legacy.
- Destructive downgrade to an empty database is available only for disposable automated tests behind an explicit test-only guard.
- API and Worker compare baseline family, minimum schema revision, runtime contract, and build compatibility before becoming ready.
- A binary/schema mismatch makes readiness false and prevents Worker claims.

### 9.6 Design Deviation

Plan 3 records an explicit deviation from the original Plan 10 rollout sequence:

- the system was not launched;
- no supported production Legacy deployment or upgrade path exists;
- current clean schema is the first supported baseline;
- Legacy canary, legacy-zero, Legacy restore, and calendar soak are not claimed;
- release evidence is replaced by full automation and one production-shaped rehearsal;
- the original Plan 10 implementation sequence is not retroactively marked as executed.

## 10. Plan 4 Architecture: `create_entry` Production Qualification and Launch

### 10.1 Supported Capability Surface

`create_entry` is visible only when all of these are frozen for the Run:

- for a production-marked database, launch control points to a consumed current `pre_ga_launch` gate use; an isolated disposable rehearsal database uses only its server-owned non-production release-profile authorization;
- Capability Ledger mode `enforced`;
- durable Interrupt support enabled;
- server-owned Idempotency Secret configured;
- approved write cohort and policy closure;
- call-owned approval requirement;
- compatible worker feature digest;
- reconciliation verifier and Operator control plane available;
- no unresolved `unknown` or `needs_reconciliation` CapabilityCall exists; appearance of either state fails the new-write guard closed until reconciliation.

The execution path remains:

```text
Provider Tool Call
  → frozen Manifest ownership
  → authorization evidence
  → CapabilityCall proposal
  → call-owned approval Interrupt
  → authenticated Operator decision
  → server HMAC idempotency key
  → local transactional Entry write
  → Attempt/result/checkpoint commit
  → completion obligations
```

Duplicate Provider calls, browser retries, duplicate Resume, or Worker takeover converge on one logical call identity and at most one Entry.

### 10.2 Unsupported Write Branches

`update_entry`, `merge_entry`, and `relation_followup`:

- are absent from the production Capability Registry and Provider Tool Surface;
- are removed from built-in Skill instructions and write-branch selection;
- are identified as unsupported in user-facing product copy;
- return `capability_not_supported` from any retained direct service boundary;
- create no CapabilityCall side effect, Entry mutation, or implicit `create_entry` substitute.

An unsupported-call metric records only the stable branch identifier and safe request context.

### 10.3 Dependency Reproducibility

The supported runtime is Python 3.11. Dependency inputs and generated locks are separated:

- direct API/Assistant Worker inputs;
- direct parse-worker/Docling inputs;
- fully pinned API/Assistant Worker lock;
- fully pinned parse-worker lock with supported platform markers.

Docker and CI install from locks, not unconstrained input files. The first closure stays on the repository-selected LangGraph `0.3.34` line and compatible LangChain/Core/OpenAI versions unless an explicit compatibility experiment proves that line impossible. Any upgrade requires a separate recorded decision and the full Provider Loop/Checkpoint/HITL matrix.

`pip check`, import smoke, and platform compatibility are release gates. Docling, Transformers, Hugging Face Hub, and Torch versions must resolve without ignored conflicts.

### 10.4 Test Isolation

Tests may install optional dependency stubs only through scoped fixtures that restore `sys.modules`, environment, caches, and imported application modules. Test files cannot permanently replace an installed top-level package.

The ordering reproduction between `backend/tests/test_durable_run_streaming.py` and `backend/tests/test_ai_runtime_legacy_cleanup.py` becomes a regression test. The pair must pass in forward, reverse, isolated, randomized, and full-suite collection order.

### 10.5 Production-Shaped Automation

The release profile contains:

- PostgreSQL;
- MinIO;
- API process;
- two independently identified Assistant Worker processes;
- deterministic Scripted Provider fixtures;
- frontend build artifact;
- isolated artifact and audit storage.

Release-critical scenarios cover:

- clean baseline upgrade, structural fingerprint, and runtime schema identity;
- first initialization and Operator login;
- pending-worker and ready activation;
- concurrent Worker claim and lease expiry;
- process kill/restart at checkpoint and side-effect boundaries;
- approval/input Interrupt create, resolve, duplicate resolve, expire, and cancel;
- SSE disconnect/reconnect/cursor replay;
- duplicate Provider call, duplicate Resume, and duplicate client submission;
- `create_entry` success, rejection, cancellation, unknown, and reconciliation;
- Artifact write/read/GC and L2 commit;
- readiness and kill-switch behavior;
- launch-gate drift invalidation.

Release-critical tests may not skip because PostgreSQL, MinIO, or the second Worker is unavailable. Optional paid live-Provider probes remain separate and cannot substitute for deterministic release evidence.

### 10.6 Production-Shaped Rehearsal

One manually triggered rehearsal executes the same released images and lock files as the intended deployment. It records:

- image/build identifiers;
- schema family, revision, structural fingerprint, and runtime identity digest;
- Operator/RBAC negative and positive checks;
- active rollout/Profile/Model/Package closure;
- worker registrations and compatibility;
- all release-critical scenario outcomes;
- sanitized metrics and audit digests;
- start/end UTC times;
- evidence Artifact identities and aggregate digest.

No raw password, token, API key, Prompt, Entry content, Artifact body, or Idempotency Key appears in the report.

Qualifying test and rehearsal evidence can be produced only by the fixed server-owned release runner and scenario definitions. The Operator may trigger a run but cannot submit outcomes or assertion values. Every evidence manifest binds the build, lock files, schema identity, scenario-set digest, timestamps, Artifact digests, and runner identity. If evidence crosses from an isolated runner into the target control plane, the server verifies a canonical signed attestation against a configured release-evidence trust key; an unsigned upload, unknown runner/scenario digest, build mismatch, or Artifact mismatch cannot qualify a launch candidate.

### 10.7 Launch Gate

Plan 4 introduces three records: an immutable `pre_ga_launch` candidate, an append-only authenticated gate-use record, and a revisioned singleton production launch-control pointer. The server, not the client, derives each candidate decision and subject from:

- current build revision;
- current schema family/revision/runtime identity digest and immutable deployment-class marker;
- current auth contract version;
- active rollout/Profile/Model/Package closure;
- worker runtime/codec/feature contracts;
- dependency-lock digests and `pip check` result;
- backend/frontend/release-profile test Artifact digests;
- production-shaped rehearsal Artifact digest;
- an operational precondition snapshot containing open unknown, reconciliation, and active-Run counts.

The client cannot submit `passed`, override an assertion, or supply a replacement digest. A candidate is passing only when every Artifact verifies, every hard assertion passes, unknown/reconciliation counts are zero, and the pre-launch active-Run count is zero.

The authenticated Operator may consume only a passing candidate whose durable identity inputs are still current and whose operational preconditions pass again under lock. Consumption appends the gate-use record and advances launch control to its subject digest in one transaction. The use is bound to Operator, Session, request ID, bounded reason, prior launch-control revision, candidate ID, and subject digest; duplicate request replay is idempotent and a competing revision returns 409.

An unused candidate expires after 24 hours. Once consumed, that wall-clock expiry does not take an unchanged launched deployment offline. Changes to build, schema runtime identity, auth contract, rollout closure, Worker contract, dependency locks, or qualifying evidence invalidate launch control and require a new candidate and use. Normal active-Run count changes and Worker liveness changes do not rewrite the consumed subject: Worker loss makes readiness false, while a newly unresolved write makes the write safety guard fail closed and requires reconciliation.

For a production-marked database, initialization, login, authenticated administration, Worker registration, and rollout activation remain available before launch. New Chat admission and production write enablement remain locked, `/ready` reports `pre_ga_launch_unapproved`, and no runtime/write configuration flag can substitute for the durable launch-control check. The release runner may exercise the same images only against a disposable database with a durable non-production rehearsal marker; that path cannot create a production launch-control use.

## 11. End-to-End Data Flows

### 11.1 Fresh Install

```text
empty PostgreSQL
  → alembic upgrade head
  → pre_ga_v1 schema
  → API /health = 200
  → API /ready = 503 system_not_initialized
  → setup-authorized initialization
  → Operator + Model + system seed + prepared rollout
  → initial Operator session
  → compatible Worker observed
  → Operator activates rollout
  → API /ready = 503 pre_ga_launch_unapproved
  → verified automation + rehearsal evidence
  → Operator consumes current pre_ga_launch gate
  → API /ready = 200
  → Chat creates main_agent Run
```

### 11.2 Login and Mutation

```text
password login
  → database-time lockout checks
  → Argon2id verification
  → HMAC-backed session + CSRF cookies
  → mutation request with session cookie + CSRF header
  → Principal dependency
  → role/object validation
  → service mutation + audit event in one transaction boundary
```

### 11.3 Readiness and Admission

```text
Chat request
  → readiness evaluation
  → production launch-control verification
  → durable active rollout
  → frozen published closure
  → compatible Worker check
  → atomic Message + main_agent Run + initial event
  → durable Worker execution
```

### 11.4 Create Entry

```text
Tool Call
  → ledger proposal
  → approval Interrupt
  → Operator approval
  → idempotent transactional write
  → durable result/checkpoint
  → completion/memory finalization
```

## 12. Failure Semantics

### 12.1 Authentication and Authorization

- Missing/invalid/expired session: 401.
- Valid viewer attempting mutation: 403.
- Missing/invalid CSRF on mutation: 403.
- Initialization without valid Setup authorization: 401.
- Concurrent initialization loser: 409.
- Locked account login: 429 with bounded retry-after information.
- Password or token details never appear in error payloads.

### 12.2 Runtime

- No active rollout: 503 `assistant_rollout_inactive`.
- Closure drift: 503 `assistant_runtime_closure_drift`.
- No compatible Worker: 503 `assistant_worker_unavailable`.
- Kill switch: 503 `assistant_new_runs_disabled`.
- Missing/stale production launch control: 503 `pre_ga_launch_unapproved`.
- Pre-insert failure leaves no Chat Run or empty Message residue.
- Post-insert failure remains on the existing durable Run.

### 12.3 Schema

- Wrong baseline family: readiness false, Worker claim disabled.
- Drifted development schema: stamp denied.
- Legacy schema: `legacy_upgrade_not_supported`.
- Unknown/shared database: reset/stamp denied by default.
- Fresh install never requests B2 maintenance acknowledgement.

### 12.4 Writes

- Unsupported branch: `capability_not_supported`, no side effect.
- Duplicate logical call: replay existing durable result/state.
- Side-effect outcome unknown: `needs_reconciliation`, no automatic retry.
- Cancellation after side-effect start: settle or reconcile; never silently discard.
- Reconciliation requires authenticated Operator and signed evidence.

## 13. Observability and Audit

The production-safe metric set includes:

- login success/failure/lockout;
- session create/revoke/expire;
- auth 401, RBAC 403, and CSRF rejection;
- readiness by reason code;
- active rollout/Profile/Model/build identity;
- compatible Worker count and heartbeat age;
- Run status and recovery count;
- Interrupt status and resolution conflicts;
- CapabilityCall duplicate/unknown/reconciliation/write outcome;
- unsupported write branch attempts;
- schema family/structural-fingerprint/runtime-identity mismatch;
- launch-gate create/pass/fail/expire/consume/drift-invalidate.

Logs, metrics, audit events, and evidence exclude:

- password and password hash;
- Setup, Session, and CSRF tokens;
- AI/provider credentials;
- raw Prompt or Provider payload;
- Entry content;
- Artifact content;
- raw Idempotency Key.

## 14. Verification Strategy

### 14.1 Plan 1 Exit Gate

- PostgreSQL account/session constraints and concurrent initialization pass.
- Password, login-window, lockout, rehash, expiry, revoke, and password-change tests pass.
- Session-MAC restart/rotation, cookie, same-origin login/setup, CORS, and CSRF tests pass in development and production configuration.
- Route inventory proves that, apart from Setup-authorized initialization, every control-plane and cookie-authenticated browser mutation consumes Operator plus CSRF, while machine entrypoints retain only their explicit narrower authenticator.
- Forged Header/CLI identity tests fail closed.
- Frontend initialization/login/session-expiry tests and production build pass.

### 14.2 Plan 2 Exit Gate

- Default configuration has no Legacy selection.
- Fresh initialization creates trusted system versions and prepared rollout.
- Readiness correctly distinguishes pending Worker, incompatible Worker, drift, kill switch, and ready state.
- Operator activation is CAS/idempotency safe.
- Successful new Runs are always `main_agent`.
- Pre-insert failure leaves no residue; post-insert failure stays durable.
- Fresh Compose initialization-to-Chat smoke passes.

### 14.3 Plan 3 Exit Gate

- New baseline produces a sole Alembic head.
- Empty PostgreSQL upgrades directly without B2 acknowledgement.
- The pre-squash clean head and new baseline final schemas have identical canonical structural fingerprints after only the exact exclusion manifest, and the new head has the expected family-bound runtime schema identity.
- Guarded stamp preserves data and rejects every drift/Legacy/unknown case.
- Archive manifest digests verify.
- API and Worker reject the wrong baseline family.
- PostgreSQL migration verification has no release-critical skip.

### 14.4 Plan 4 Exit Gate

- Only `create_entry` is exposed as a production write Capability.
- Unsupported branches fail visibly and without side effect.
- Complete write/recovery/fault matrix passes against PostgreSQL and two Workers.
- Python 3.11 clean install and `pip check` pass from locks.
- Test-order pollution regression passes in all required orders.
- Full backend, frontend, build, MinIO, migration, and release-profile suites pass.
- One production-shaped rehearsal passes.
- A current `pre_ga_launch` candidate passes, is consumed by the Operator, and advances production launch control; stale/drifted subjects fail closed.

## 15. Rollback Rules

### 15.1 Before First Release

All four plans execute pre-GA. Code may be reverted and disposable development databases recreated. No plan assumes a supported production Legacy database.

### 15.2 Security Baseline

Once Operator authentication is enabled in a deployed environment, rolling back to an unprotected mutation surface is forbidden. Security regressions use forward fixes. Operator/session/audit schema remains additive.

### 15.3 Runtime

- Disable new Runs with `ASSISTANT_NEW_RUNS_ENABLED=false`.
- Disable new writes with `ASSISTANT_MAIN_AGENT_WRITE_MODE=off`.
- Existing Runs and CapabilityCalls continue to terminal/reconciliation states.
- Rollout/Profile change rollback creates or reactivates an immutable known-good Main Agent revision; it does not select Legacy.

### 15.4 Schema

- The clean root does not downgrade to Legacy.
- Failed development migration is recovered by database recreation or clean-baseline backup restore.
- Disposable downgrade-to-empty is test-only and deliberately guarded.

### 15.5 Launch Gate

An unused candidate may expire without affecting an already consumed, unchanged launch-control subject. Durable subject drift invalidates launch control. Before first launch, a failed, expired, unconsumed, or stale gate leaves new production Runs and writes disabled; it never bypasses verification. Runtime rollback still uses the new-Run and write kill switches and never selects Legacy.

## 16. Required Design Deviation Record

Plan 3 creates:

`docs/superpowers/evidence/2026-07-28-pre-ga-clean-baseline-deviation.md`

It records these accepted facts:

1. MindAtlas had not launched when Legacy code/schema were removed.
2. The project adopts the current clean schema as its first supported pre-GA baseline.
3. Legacy in-place upgrade and Legacy restore are unsupported.
4. The original Plan 10 production canary, legacy-zero, restore, B1/B2 sequence, and calendar soak were not executed and are not claimed.
5. Full deterministic automation and one production-shaped rehearsal replace the omitted pre-launch operational gates.
6. This deviation changes the release baseline; it does not retroactively mark the original Plan 10 checklist complete.

## 17. Implementation Plan Decomposition

After written-spec approval, implementation is specified in four files:

1. `docs/superpowers/plans/2026-07-28-single-operator-production-control-plane.md`
2. `docs/superpowers/plans/2026-07-28-main-agent-bootstrap-and-readiness.md`
3. `docs/superpowers/plans/2026-07-28-pre-ga-clean-schema-baseline.md`
4. `docs/superpowers/plans/2026-07-28-create-entry-production-qualification-and-launch.md`

Each plan must:

- begin with the Superpowers agentic-worker execution header;
- state exact prerequisites and stable interfaces consumed from earlier plans;
- map created/modified/tested files before task decomposition;
- use red-green-refactor steps with exact commands and expected results;
- end every task in one independently reviewable commit;
- include PostgreSQL/MinIO/multi-process requirements where relevant;
- distinguish release-critical tests from optional live-provider probes;
- provide focused, full, clean-install, and `git diff --check` verification;
- create sanitized evidence without secrets or business content;
- stop rather than weaken a security, schema, or release gate.

## 18. Final Acceptance Criteria

The production closure is complete only when all statements below are true:

1. A fresh install reaches the pre-GA clean head without Legacy acknowledgement or transitional Legacy schema.
2. The system cannot initialize without Setup authorization and cannot initialize twice.
3. Apart from Setup-authorized initialization, a verified Operator password session and CSRF protection guard every browser production mutation; separately authenticated machine entrypoints remain narrower and cannot satisfy an Operator dependency.
4. No caller-asserted identity/role Header, CLI flag, or environment label can mint an `OperatorPrincipal`; verified machine credentials remain confined to their narrower Principal type.
5. The initialization flow creates a published Main Agent baseline and a prepared durable rollout.
6. Production readiness remains false until a compatible Worker exists, the Operator activates the rollout, and production launch control references a consumed current gate use.
7. Every new Chat Run is `main_agent`; no Legacy fallback exists.
8. `create_entry` is the only production write Capability and is idempotent across retries, Resume, restart, and Worker takeover.
9. Unsupported write branches are absent from the Provider surface and fail visibly when called directly.
10. Python 3.11 clean installation and `pip check` succeed from committed locks.
11. Release-critical PostgreSQL, MinIO, dual-worker, restart, HITL, SSE, write, reconciliation, memory, and schema tests pass without skip.
12. The production-shaped rehearsal succeeds and produces a safe immutable evidence digest.
13. A current unexpired `pre_ga_launch` candidate passes, matches the current build/schema/runtime/evidence closure, and is consumed by the authenticated Operator to advance production launch control.
14. The pre-GA design deviation is committed and the original Plan 10 skipped gates are not misreported as completed.

## 19. Ambiguity Resolution

There are no open product choices in this closure design. The following interpretations are explicitly rejected:

- “Clean baseline” does not mean silently stamping an unknown database.
- “Single user” does not mean unauthenticated administration.
- “No soak” does not mean no production-shaped verification.
- “Main Agent only” does not mean retrying a failed side effect through a new Run.
- “Only `create_entry`” does not permit silent substitution for update/merge/relation intent.
- “Legacy unsupported” does not permit deleting unknown data; unsupported databases are rejected, not mutated.

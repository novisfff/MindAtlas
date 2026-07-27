# Create Entry Production Qualification and Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `create_entry` the only production Agent write, prove its approval/idempotency/recovery/reconciliation path under a complete PostgreSQL/MinIO/two-Worker fault matrix, lock Python 3.11 dependencies, run full deterministic automation plus one production-shaped rehearsal, and require an immutable signed-evidence `pre_ga_launch` candidate and authenticated Operator consumption before a production-marked database admits new Chat Runs or enables writes.

**Architecture:** The production Capability surface retains all approved reads and exactly one write declaration, `create_entry`; unsupported write branches terminate at a typed, side-effect-free boundary before CapabilityCall creation. A centralized production write guard freezes the create contract into rollout/Run closure and checks enforced ledger mode, durable call-owned approval, server HMAC, compatible Worker feature identity, reconciliation availability, launch authorization, and zero unresolved writes before proposal and again before execution. Python inputs compile into hashed API/Worker and parse-Worker locks. A fixed release runner drives a standalone PostgreSQL/MinIO/API/two-Worker/Scripted-Provider/frontend profile, stores content-addressed artifacts, and signs canonical automation/rehearsal manifests with an Ed25519 runner key. Additive revision `pre_ga_v1_0002` creates immutable candidates, append-only gate uses, and a revisioned singleton launch control while advancing Plan 3 schema identity. The server derives candidate decisions and durable subjects; the Operator can only select verified evidence references and consume a current passing candidate through Session/CSRF/CAS. Candidate expiry is checked only before consumption; a consumed unchanged subject remains launched until durable identity/evidence drift, while Worker liveness affects readiness and unresolved writes affect only the write guard.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 15, MinIO, LangGraph 0.3.34 line, pip-tools, hashed requirement locks, Ed25519/cryptography, Docker Compose, deterministic OpenAI-compatible Scripted Provider, React 18, TypeScript, TanStack Query, Vitest, GitHub Actions.

## Global Constraints

- Implement from approved design commit `ca925eeba569357ddb2c5c3aa63554b391efd21b` and reviewed implementations of Plans 1–3. Document commits `98accdb`, `2eb1006`, and `6e95938` identify the written contracts, not a substitute for implementation exit gates.
- The only production Agent write Capability is exactly `create_entry`. `update_entry`, `merge_entry`, `create_relation`, relation follow-up, and OpenClaw relation creation are absent from the production Capability Registry, Provider Tool Surface, trusted Skill, and retained production workflow assets.
- Normal human product APIs for an authenticated user to edit an Entry or manage Relations are outside the Agent Capability surface and remain available under Plan 1 route policy. Do not delete ordinary Entry/Relation REST behavior merely to close an Agent tool.
- Any retained direct Agent-service call for `update_entry`, `merge_entry`, `create_relation`, or `relation_followup` raises stable `capability_not_supported` before a CapabilityCall, Entry/Relation mutation, Artifact, Interrupt, or implicit `create_entry` substitute.
- The `create_entry` Provider declaration is not a direct database writer. Actual mutation is reachable only through the frozen CapabilityCall aggregate and local transactional adapter.
- Every `create_entry` write requires a call-owned durable approval Interrupt. A workflow-owned human node, global approval, previous approval, or approval for another call cannot authorize it.
- Duplicate Provider calls, duplicate browser submissions, duplicate approval Resume, and Worker takeover converge on one logical call and at most one Entry via server-owned HMAC identity and the unique `Entry.source_capability_call_id` link.
- Capability Ledger mode is `enforced`; durable Interrupts, reconciliation verification, Operator Session/CSRF control, a minimum-32-byte server Idempotency Secret, exact create policy/cohort closure, and compatible Worker feature digest are all mandatory.
- New-write admission takes a PostgreSQL transaction advisory lock and rejects when any CapabilityCall has status `unknown` or `needs_reconciliation`. Transitions into those states take the same lock. There is no configuration bypass.
- Reconciliation remains available even when new writes, new Runs, or launch authorization are blocked. It requires an authenticated Operator, CSRF, expected revisions/request-id idempotency, and server-verified evidence.
- `ASSISTANT_MAIN_AGENT_WRITE_MODE` becomes a process ceiling with values `off` or `create_entry`; it is necessary but never sufficient. A flag cannot create a production launch authorization.
- A production-marked database requires current durable launch control before new Chat admission or write enablement. Initialization, login, authenticated administration, reconciliation, Worker registration, rollout preparation/activation, and candidate inspection remain available before launch.
- A disposable `rehearsal` database may run the release matrix only with a short-lived server-owned release-profile authorization bound to the exact build/schema/locks/scenario set. That authorization is invalid for `production` and creates no launch gate use.
- Development remains non-production and defaults writes off. It does not manufacture production evidence or a launch-control use.
- Candidate decisions, passing state, failure codes, subject digests, operational counts, timestamps, and expiry are server-derived. Client request models contain evidence references, request ID, expected revision, and bounded reason only.
- Unused candidates expire exactly 24 hours after database `issued_at`. Consumption rechecks database time under lock. After consumption, wall-clock expiry is ignored for an unchanged subject.
- The durable launch subject excludes Worker heartbeat/liveness and volatile active/unknown/reconciliation counts. Worker loss changes readiness only; new unresolved writes block the write guard only. Candidate generation and consumption nevertheless require unknown, reconciliation, and active-Run counts all equal zero.
- Build, image/deployment artifact set, schema runtime identity, auth contract, rollout closure, Worker runtime/codec/feature contracts, dependency locks, scenario set, trust set, or qualifying evidence drift invalidates launch control and requires a new candidate/use.
- Final qualifying automation/rehearsal runs only after the fresh production target is initialized and its rollout is active but launch-blocked. A server-derived `ReleaseQualificationTargetV1` freezes that target's non-secret durable identity; both evidence manifests must bind its digest, and candidate creation recomputes it from current target state.
- The rehearsal profile recreates the exact target Profile/Model/Package/Capability identity from an ephemeral Operator-authenticated, credential-free provisioning bundle. A signed rehearsal-only transport port routes Provider I/O to the Scripted Provider without changing the frozen target Model identity; production rejects that transport port.
- Release evidence comes only from fixed server-owned scenarios/runner. Paid live-Provider probes are optional diagnostics and never satisfy a release assertion.
- The release profile contains PostgreSQL, MinIO, API, two separately identified Assistant Workers, fixed Scripted Provider, frontend artifact, and isolated database/artifact/evidence/audit storage. Missing infrastructure is a release-critical failure, not a skip.
- Python support is exactly 3.11. Docker and CI install hashed locks, not unconstrained inputs. API/Assistant Worker and parse Worker have separate direct inputs and locks.
- Stay on the repository-selected LangGraph `0.3.34` line unless an explicit separately reviewed compatibility decision proves it impossible. Do not silently upgrade LangGraph/LangChain/OpenAI to resolve the lock.
- `pip check`, direct import smoke, two clean installs, platform-marker validation, and Docling/Transformers/Hugging Face Hub/Torch conflict checks are release gates.
- Test dependency stubs are fixture-scoped and restore `sys.modules`, environment, caches, and imported application modules. No test file permanently replaces an installed top-level package.
- Full automated qualification plus one production-shaped rehearsal replaces soak. Do not add or claim 7/14-day waiting, Legacy canary, legacy-zero, restore, or old Plan 10 completion.
- Additive migration is exactly `pre_ga_v1_0002` with `down_revision = "pre_ga_v1_0001"`. Never edit `pre_ga_v1_0001` or reconnect the archived lineage.
- Migration advances the singleton `mindatlas_schema_identity` using Plan 3's transaction-local guard and a generated `pre_ga_v1_0002` structural/runtime identity. It does not accept caller digests.
- Evidence/logs/metrics/audit exclude passwords/hashes, Setup/Session/CSRF tokens, Provider/API keys, private signing keys, raw Prompts/Provider payloads, Entry/Artifact bodies, raw Idempotency Keys, and database URLs.
- Every Task follows red-green-refactor, uses roughly 2–5 minute steps, ends with focused verification and one independently reviewable commit.
- If surface closure, one-entry convergence, post-approval guard, lock reproducibility, test isolation, signed evidence, migration identity, launch CAS, expiry semantics, or production/rehearsal separation cannot be proven, stop. Never weaken a hard assertion, accept unsigned evidence, skip infrastructure, or use a runtime flag as launch authority.

---


## Prerequisites and Stable Interfaces

### Required checkpoint

Run from repository root after all Plan 3 implementation commits:

```bash
git status --short
git rev-parse HEAD
cd backend
.venv/bin/alembic roots
.venv/bin/alembic heads
.venv/bin/python scripts/archive_pre_ga_lineage.py --check
.venv/bin/python scripts/verify_pre_ga_schema.py runtime \
  --database-url-env MINDATLAS_SCHEMA_CLEAN_DATABASE_URL
```

Expected:

- worktree is clean;
- Alembic root/head are both exactly `pre_ga_v1_0001`;
- live version directory has one root and archive has exactly 60 verified non-importable files ending `b6e2d4f8a901`;
- the database marker is family `pre_ga_v1`, exact revision `pre_ga_v1_0001`, and Plan 3 runtime identity verifies;
- Plan 1 Operator Session/CSRF/audit and Plan 2 Main-Agent bootstrap/readiness/atomic admission exit gates pass;
- no `pre_ga_v1_0002` file/table exists;
- no production database has been launched.

Stop and refresh this plan through review if the checkpoint differs.

### Consumed Operator and runtime interfaces

```python
# backend/app/operator_auth/contracts.py
@dataclass(frozen=True)
class OperatorPrincipal:
    operator_id: UUID
    role: Literal["viewer", "operator"]
    session_id: UUID
    authentication_method: Literal["password_session"] = "password_session"


# backend/app/operator_auth/dependencies.py
def require_viewer_principal(...) -> OperatorPrincipal: ...
def require_operator_principal(...) -> OperatorPrincipal: ...
def require_csrf(...) -> None: ...


# backend/app/operator_auth/constants.py
OPERATOR_AUTH_CONTRACT_VERSION = "operator-auth-v1"


# backend/app/operator_auth/audit.py
class OperatorAuditRepository:
    def append(...) -> OperatorAuditEvent: ...


# backend/app/assistant/runtime/readiness.py
class RuntimeSchemaCompatibility(Protocol):
    def is_compatible(self, db: Session) -> bool: ...


# backend/app/assistant/runtime/contracts.py
class AssistantRuntimeClosure(FrozenContract):
    rollout_revision_id: UUID
    rollout_revision_digest: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    build_revision: str
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    closure_digest: str
```

Plan 4 extends rollout/Run closure with write-contract fields and updates its digest. It does not weaken or rename existing fields.

### Consumed schema identity interfaces

```python
# backend/app/schema/contracts.py
SCHEMA_FAMILY = "pre_ga_v1"
CLEAN_ROOT_REVISION = "pre_ga_v1_0001"
NEXT_RESERVED_REVISION = "pre_ga_v1_0002"


class DeploymentClass(StrEnum):
    DEVELOPMENT = "development"
    REHEARSAL = "rehearsal"
    PRODUCTION = "production"
```

Plan 4 creates `backend/app/schema/manifests/pre_ga_v1_0002-expected.json`, updates the code-owned compatibility requirement to known/minimum ordinal 2, and preserves the Plan 3 root expected manifest for downgrade verification.

### Produced supported-write contract

```python
SUPPORTED_PRODUCTION_WRITE_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"create_entry"}
)
UNSUPPORTED_PRODUCTION_WRITE_BRANCHES: Final[frozenset[str]] = frozenset(
    {"update_entry", "merge_entry", "create_relation", "relation_followup"}
)
CREATE_ENTRY_WRITE_CONTRACT_VERSION: Final[int] = 1


class CapabilityNotSupported(CapabilityDomainError):
    safe_code: Literal["capability_not_supported"] = "capability_not_supported"
    branch: Literal[
        "update_entry", "merge_entry", "create_relation", "relation_followup"
    ]


@dataclass(frozen=True)
class ProductionWriteGuardSnapshot:
    allowed: bool
    reason_code: str | None
    create_entry_contract_digest: str
    write_policy_digest: str
    write_cohort_digest: str
    unresolved_unknown_count: int
    unresolved_reconciliation_count: int
```

Unsupported boundaries never return this guard because they terminate before write admission.

### Produced qualification target and release evidence contracts

```python
class ReleaseQualificationTargetV1(FrozenContract):
    schema_version: Literal[1] = 1
    build_revision: str
    image_set_digest: str
    deployed_artifact_set_digest: str
    schema_family: Literal["pre_ga_v1"]
    schema_revision: Literal["pre_ga_v1_0002"]
    schema_application_fingerprint: str
    schema_control_fingerprint: str
    schema_identity_contract_version: int
    production_schema_deployment_class: Literal["production"]
    schema_seed_contract_digest: str
    schema_runtime_contract_version: int
    schema_checkpoint_codec_version: int
    schema_capability_feature_digest: str
    production_schema_runtime_identity_digest: str
    schema_contract_material_digest: str
    operator_auth_contract_version: str
    rollout_revision_id: UUID
    rollout_revision_digest: str
    runtime_closure_digest: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    worker_runtime_contract_version: int
    worker_checkpoint_codec_version: int
    worker_capability_feature_digest: str
    create_entry_contract_digest: str
    write_policy_digest: str
    write_cohort_digest: str
    reconciliation_contract_version: int
    dependency_lock_set_digest: str
    scenario_set_digest: str
    required_assertion_set_digest: str
    runner_contract_version: int
    runner_identity_digest: str
    evidence_trust_set_digest: str
    qualification_target_digest: str


class QualificationInfrastructureIdentityV1(FrozenContract):
    schema_version: Literal[1] = 1
    platform: Literal["linux/amd64"]
    scripted_provider_image_digest: str
    postgres_image_digest: str
    minio_image_digest: str
    minio_client_image_digest: str
    release_images_lock_digest: str
    compiler_image_digest: str
    compiler_bootstrap_lock_digest: str
    identity_digest: str


class ReleaseEvidenceManifestV1(FrozenContract):
    schema_version: Literal[1] = 1
    runner_contract_version: int
    runner_identity_digest: str
    release_run_id: UUID
    evidence_kind: Literal["automated_qualification", "production_rehearsal"]
    qualification_target_digest: str
    build_revision: str
    image_set_digest: str
    deployed_artifact_set_digest: str
    schema_family: Literal["pre_ga_v1"]
    schema_revision: Literal["pre_ga_v1_0002"]
    schema_application_fingerprint: str
    schema_control_fingerprint: str
    schema_identity_contract_version: int
    schema_contract_material_digest: str
    schema_deployment_class: Literal["rehearsal"]
    schema_seed_contract_digest: str
    schema_runtime_contract_version: int
    schema_checkpoint_codec_version: int
    schema_capability_feature_digest: str
    schema_runtime_identity_digest: str
    operator_auth_contract_version: str
    rollout_revision_id: UUID
    rollout_revision_digest: str
    runtime_closure_digest: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    worker_runtime_contract_version: int
    worker_checkpoint_codec_version: int
    worker_capability_feature_digest: str
    create_entry_contract_digest: str
    write_policy_digest: str
    write_cohort_digest: str
    reconciliation_contract_version: int
    dependency_lock_set_digest: str
    scenario_set_digest: str
    required_assertion_set_digest: str
    evidence_trust_set_digest: str
    qualification_infrastructure_identity: QualificationInfrastructureIdentityV1
    started_at: datetime
    ended_at: datetime
    assertion_results: tuple[ReleaseAssertionResultV1, ...]
    artifact_refs: tuple[ReleaseArtifactRefV1, ...]
    artifact_aggregate_digest: str
    manifest_digest: str


class SignedReleaseAttestationV1(FrozenContract):
    schema_version: Literal[1] = 1
    domain: Literal["mindatlas:release-evidence:v1"]
    key_id: str
    manifest_digest: str
    signature_base64url: str


class RehearsalAttemptSubjectV1(FrozenContract):
    schema_version: Literal[1] = 1
    qualification_target_digest: str
    build_revision: str
    image_set_digest: str
    deployed_artifact_set_digest: str
    dependency_lock_set_digest: str
    scenario_set_digest: str
    required_assertion_set_digest: str
    runner_contract_version: int
    runner_identity_digest: str
    evidence_trust_set_digest: str
    subject_digest: str
```

`ReleaseQualificationTargetV1.qualification_target_digest`,
`QualificationInfrastructureIdentityV1.identity_digest`, and
`RehearsalAttemptSubjectV1.subject_digest` each hash every preceding field of their
own contract and exclude the digest field itself. Their exact domains are,
respectively, `mindatlas:release-qualification-target:v1`,
`mindatlas:qualification-infrastructure:v1`, and
`mindatlas:rehearsal-attempt-subject:v1`. The
`schema_application_fingerprint` is the Plan 3 marker's exact
`structural_fingerprint` under a release-contract name; no second catalog digest is
computed. `schema_control_fingerprint` is the exact generated Plan 3/4
`marker_control_fingerprint`; it is likewise not recomputed under a second schema.
`schema_contract_material_digest` uses domain
`mindatlas:schema-contract-material:v1` over exactly schema family, revision,
application fingerprint, marker-control fingerprint, identity-contract version,
schema seed-contract digest, schema runtime-contract version, schema
checkpoint-codec version, schema Capability-feature digest, and Operator-auth
contract version; it excludes deployment class and the derived runtime-identity
digest. This is the only allowed rehearsal/production schema
comparison projection. The
qualification-infrastructure identity is evidence-only: it proves the pinned
Scripted Provider/PostgreSQL/MinIO/compiler environment used to qualify the
application, but it is not part of the production deployed-Artifact set or durable
launch subject. `runner_identity_digest` uses domain
`mindatlas:release-runner-identity:v1` over immutable `build_revision`,
`runner_contract_version`, `scenario_set_digest`, and
`required_assertion_set_digest`; the protected build rejects a dirty tree, so runner
code cannot change while retaining that identity. The private Ed25519 key exists
only in the runner secret mount. API
configuration contains public trust keys and a canonical trust-set digest.

### Produced launch contracts

```python
class PreGaLaunchSubjectV1(FrozenContract):
    schema_version: Literal[1] = 1
    qualification_target_digest: str
    build_revision: str
    image_set_digest: str
    deployed_artifact_set_digest: str
    schema_family: Literal["pre_ga_v1"]
    schema_revision: Literal["pre_ga_v1_0002"]
    schema_runtime_identity_digest: str
    deployment_class: Literal["production"]
    operator_auth_contract_version: str
    rollout_revision_id: UUID
    rollout_revision_digest: str
    runtime_closure_digest: str
    profile_version_id: UUID
    profile_content_digest: str
    model_id: UUID
    model_identity_digest: str
    package_closure_digest: str
    capability_closure_digest: str
    seed_manifest_digest: str
    worker_runtime_contract_version: int
    worker_checkpoint_codec_version: int
    worker_capability_feature_digest: str
    create_entry_contract_digest: str
    write_policy_digest: str
    write_cohort_digest: str
    reconciliation_contract_version: int
    dependency_lock_set_digest: str
    automated_evidence_manifest_digest: str
    rehearsal_evidence_manifest_digest: str
    scenario_set_digest: str
    required_assertion_set_digest: str
    runner_contract_version: int
    runner_identity_digest: str
    evidence_trust_set_digest: str
    subject_digest: str


class LaunchOperationalSnapshotV1(FrozenContract):
    schema_version: Literal[1] = 1
    unknown_capability_call_count: int
    needs_reconciliation_count: int
    active_run_count: int
    observed_at: datetime
    snapshot_digest: str


class CreatePreGaLaunchCandidateRequest(CamelModel):
    automated_evidence_ref: ContentAddressedEvidenceRef
    rehearsal_evidence_ref: ContentAddressedEvidenceRef
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class ConsumePreGaLaunchCandidateRequest(CamelModel):
    expected_control_revision: int = Field(ge=0)
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)
```

`PreGaLaunchSubjectV1.subject_digest` and `LaunchOperationalSnapshotV1.snapshot_digest` use domains `mindatlas:pre-ga-launch-subject:v1` and `mindatlas:pre-ga-launch-operational-snapshot:v1`, respectively; each hashes every prior field of its own contract and excludes itself. The snapshot is bound into the immutable candidate decision row and Operator audit digest—not retroactively into either release-evidence manifest—but is deliberately excluded from the durable control subject so normal post-launch activity does not invalidate launch.

### Fixed HTTP and failure contract

| Method/path | Policy | Result |
|---|---|---|
| `GET /api/pre-ga-launch/status` | viewer Session | safe control/current-subject/candidate summary |
| `GET /api/pre-ga-launch/qualification-target` | viewer Session | current prelaunch durable target identity/digest; no credentials/content |
| `GET /api/pre-ga-launch/candidates` | viewer Session | immutable safe summaries |
| `POST /api/pre-ga-launch/candidates` | Operator + CSRF | 201 server-derived pass/fail candidate |
| `POST /api/pre-ga-launch/candidates/{candidate_id}/consume` | Operator + CSRF | 200 idempotent CAS launch use/control |
| `GET /api/capability-calls/reconciliation` | viewer Session | safe unresolved-call summaries |
| `POST /api/capability-calls/{call_id}/reconcile` | Operator + CSRF | idempotent signed-evidence reconciliation |

Stable failures:

- 422 `capability_not_supported` at retained direct Agent boundaries; no CapabilityCall or side effect;
- 503 `create_entry_not_enabled`, `write_safety_blocked`, or `reconciliation_required` before a new supported write;
- 503 `pre_ga_launch_unapproved` for production Chat readiness/admission;
- 409 `launch_control_conflict`, `launch_request_reuse_conflict`, or `reconciliation_revision_conflict`;
- 422 `launch_evidence_invalid`, `launch_candidate_not_passing`, `launch_candidate_expired`, or `launch_subject_stale`;
- errors never contain evidence body, Entry content, tokens, secrets, raw request keys, or signature material beyond safe key ID/digest.

### Fixed migration and expiry boundary

```text
pre_ga_v1_0001
  -> pre_ga_v1_0002
```

`pre_ga_v1_0002` is generated/reviewed after the Plan 4 model/seed/contract closure is frozen. It adds launch state and advances schema identity. It does not recreate an old chain. Candidate expiry is `issued_at + interval '24 hours'` using database time; consumed control validity never compares current time to that expiry.

---

## File Structure

### Production write surface and guard

| Path | Responsibility |
|---|---|
| `backend/app/assistant/capabilities/supported_writes.py` | Sole create write constant, unsupported typed boundary, safe branch metric/event. |
| `backend/app/assistant/capability_calls/write_guard.py` | Locked pre-proposal/post-approval production write guard and unresolved query. |
| `backend/app/assistant/capability_calls/local_write.py` | Sole transactional Entry stage linked to CapabilityCall. |
| `backend/app/assistant/capability_calls/reconciliation_router.py` | Session/CSRF Operator reconciliation API; no asserted CLI identity. |
| `backend/app/assistant/tools/entry_tools.py` | Read declarations plus non-writing Provider `create_entry` declaration; unsupported update boundary. |
| `backend/app/assistant/tools/relation_tools.py` | Remove Provider relation tool; retain explicit unsupported Agent boundary only. |
| `backend/app/assistant/tools/__init__.py` | Export no unsupported write and exactly one write declaration. |
| `backend/app/assistant_config/registry.py` | ToolRegistry contains exactly one Agent write definition. |
| `backend/app/assistant/runtime/system_seed/` | Regenerated trusted Skill/Profile/manifest/expected digests with sole create write. |

### Dependency locks

| Path | Responsibility |
|---|---|
| `backend/requirements/api-worker.in` | Direct API/Assistant Worker inputs, including Plan 1 auth and release crypto. |
| `backend/requirements/parse-worker.in` | Direct parse Worker/Docling/OCR inputs. |
| `backend/requirements/constraints-python311.txt` | Python 3.11/LangGraph 0.3.34/compatibility constraints. |
| `backend/requirements/api-worker.lock` | Fully pinned hashed API/Assistant Worker resolution. |
| `backend/requirements/parse-worker.lock` | Fully pinned hashed parse Worker resolution with supported markers. |
| `backend/requirements/compiler-bootstrap.lock` | Hash-pinned `pip-tools==7.4.1` compiler bootstrap; not a deployed runtime lock. |
| `backend/requirements/README.md` | Exact generation/check/platform policy. |
| `backend/scripts/compile_requirements.py` | Pinned-container lock generation/check and lock-set digest module generation. |
| `backend/app/release/generated_lock_digests.py` | Generated literal per-lock and combined lock-set digests. |

### Release qualification and trust

| Path | Responsibility |
|---|---|
| `backend/app/release/__init__.py` | Stable release contract exports only. |
| `backend/app/release/contracts.py` | Frozen scenario, assertion, Artifact, manifest, attestation, and rehearsal authorization schemas. |
| `backend/app/release/scenarios.py` | Load/validate fixed scenario set and compute its digest. |
| `backend/app/release/scripted_provider.py` | Deterministic OpenAI-compatible Provider responses/fault schedule. |
| `backend/app/release/profile_authorization.py` | Short-lived server-owned rehearsal-only authorization; production reject. |
| `backend/app/release/evidence.py` | Content-addressed Artifact collection, allowlisting, aggregate digest, canonical manifest. |
| `backend/app/release/trust.py` | Ed25519 sign/verify, public trust-set parser/digest, key rotation/revocation. |
| `backend/app/release/target_fixture.py` | Validate captured target/provisioning material and feed exact IDs/config into the real rehearsal initialization transaction; never write directly. |
| `backend/app/release/runner.py` | Fixed orchestration; derives all assertion outcomes. |
| `backend/release/scenarios/pre_ga_launch.v1.json` | Immutable release-critical scenario definitions. |
| `backend/scripts/run_pre_ga_release.py` | Automation/rehearsal CLI wrapper with no outcome inputs. |
| `backend/scripts/verify_release_attestation.py` | Offline public-key/digest/allowlist verification. |
| `backend/scripts/render_release_deployment_identity.py` | Generate signed launch-relevant API/Worker/Web Artifact identity from exact image inspection. |
| `backend/scripts/lock_release_images.py` | Resolve/check immutable qualification-infrastructure image digests and platform. |
| `deploy/compose.release-qualification.yml` | Standalone PostgreSQL/MinIO/API/two-Worker/Scripted-Provider/Web profile. |
| `deploy/release.env.example` | Required variable names/generation guidance only; no usable secret. |
| `deploy/release-images.lock` | Immutable Linux/amd64 PostgreSQL/MinIO qualification-infrastructure image refs. |
| `.github/workflows/release-qualification.yml` | Required automated matrix and manual one-time rehearsal entrypoint. |

### Launch persistence and service

| Path | Responsibility |
|---|---|
| `backend/app/pre_ga_launch/__init__.py` | Stable launch contracts/service factory. |
| `backend/app/pre_ga_launch/contracts.py` | Subject, operational snapshot, candidate/use/control requests/results/reasons. |
| `backend/app/pre_ga_launch/models.py` | Immutable candidate, append-only gate use, revisioned singleton control. |
| `backend/app/pre_ga_launch/repository.py` | Locks, idempotent request lookup, immutable insert, CAS control update. |
| `backend/app/pre_ga_launch/qualification_target.py` | Build current non-secret release target identity used by automation/rehearsal/candidate comparison. |
| `backend/app/pre_ga_launch/subject.py` | Current durable subject construction/drift comparison; excludes volatile counts/liveness. |
| `backend/app/pre_ga_launch/service.py` | Evidence verification, candidate derivation, expiry/consumption, launch evaluation. |
| `backend/app/pre_ga_launch/router.py` | Viewer status/list and Operator+CSRF create/consume routes. |
| `backend/alembic/versions/pre_ga_v1_0002_create_entry_launch.py` | Add three launch tables/triggers, closure fields, and advance schema identity. |
| `backend/app/schema/manifests/pre_ga_v1_0002-expected.json` | Generated post-migration structural/control/runtime identity. |
| `backend/scripts/generate_pre_ga_v1_0002_identity.py` | Two-pass additive migration identity generator/check. |

### Frontend, tests, and evidence

| Path | Responsibility |
|---|---|
| `frontend/src/features/pre-ga-launch/api/launch.ts` | Typed status/candidate/consume clients. |
| `frontend/src/features/pre-ga-launch/queries.ts` | Polling, mutations, invalidation. |
| `frontend/src/features/pre-ga-launch/pages/PreGaLaunchPage.tsx` | Evidence references, derived result, expiry, CAS consume, drift status. |
| `frontend/src/features/pre-ga-launch/index.ts` | Feature exports. |
| `frontend/src/features/assistant/components/UnsupportedCapabilityNotice.tsx` | Explicit unsupported update/merge/relation copy. |
| `frontend/src/features/reconciliation/` | Safe unresolved list and Operator decision UI. |
| `backend/tests/test_production_capability_surface.py` | Cross-registry/provider/seed/asset write inventory. |
| `backend/tests/test_unsupported_write_boundaries.py` | No-call/no-Entry/no-Relation/no-substitution proof. |
| `backend/tests/test_create_entry_production_guard.py` | Full guard and unresolved-race matrix. |
| `backend/tests/test_create_entry_production_postgres.py` | Approval/idempotency/transaction/recovery convergence. |
| `backend/tests/test_dependency_locks.py` | Lock generation/hash/import/platform/source checks. |
| `backend/tests/test_test_order_isolation.py` | Forward/reverse/isolated/randomized/full collection regression. |
| `backend/tests/test_release_evidence.py` | Manifest/attestation/trust/allowlist vectors. |
| `backend/tests/test_release_profile.py` | Compose/service/image/secret/storage contract. |
| `backend/tests/test_release_qualification_e2e.py` | Complete two-Worker PostgreSQL/MinIO scenario run. |
| `backend/tests/test_pre_ga_launch_models.py` | Candidate/use/control constraints and immutability. |
| `backend/tests/test_pre_ga_launch_service.py` | Candidate/subject/expiry/CAS/drift unit matrix. |
| `backend/tests/test_pre_ga_launch_postgres.py` | Concurrent create/consume and DB-time semantics. |
| `backend/tests/test_pre_ga_launch_api.py` | Session/RBAC/CSRF/safe HTTP contract. |
| `backend/tests/test_plan4_migration_postgres.py` | `0001 -> 0002`, identity advance, root untouched, guarded downgrade. |
| `docs/superpowers/evidence/2026-07-28-create-entry-automated-qualification.json` | Signed safe automation manifest/ref summary. |
| `docs/superpowers/evidence/2026-07-28-production-shaped-rehearsal.json` | Signed safe one-time rehearsal manifest/ref summary. |
| `docs/superpowers/evidence/2026-07-28-pre-ga-launch.json` | Candidate/use/control/final acceptance evidence. |

---

### Task 1: Collapse the Production Agent Write Surface to `create_entry`

**Files:**

- Create: `backend/app/assistant/capabilities/supported_writes.py`
- Create: `backend/tests/test_production_capability_surface.py`
- Create: `backend/tests/test_unsupported_write_boundaries.py`
- Delete: `backend/app/assistant/workflow/system_assets/workflows/smart_capture.json`
- Delete: `backend/app/assistant/workflow/system_assets/workflows/smart_capture.en.json`
- Delete: `backend/app/assistant/workflow/system_assets/workflows/smart_capture_relation_followup.json`
- Delete: `backend/app/assistant/workflow/system_assets/workflows/smart_capture_relation_followup.en.json`
- Delete: `backend/app/assistant/workflow/system_assets/workflows/context_capture.json`
- Delete: `backend/app/assistant/workflow/system_assets/workflows/context_capture.en.json`
- Modify: `backend/app/assistant/tools/__init__.py`
- Modify: `backend/app/assistant/tools/entry_tools.py`
- Modify: `backend/app/assistant/tools/relation_tools.py`
- Modify: `backend/app/assistant_config/registry.py`
- Modify: `backend/app/assistant/capabilities/classification.py`
- Modify: `backend/app/assistant/capabilities/policy.py`
- Modify: `backend/app/assistant/capabilities/registry.py`
- Modify: `backend/app/assistant/workflow/system_assets/registry.py`
- Modify: `backend/app/openclaw_integration/registry.py`
- Modify: `backend/app/openclaw_integration/capability_adapter.py`
- Modify: `backend/app/openclaw_integration/service.py`
- Modify: `backend/app/assistant/runtime/system_seed/skills/mindatlas-universal/SKILL.md`
- Modify: `backend/app/assistant/runtime/system_seed/skills/mindatlas-universal/mindatlas.yaml`
- Modify: `backend/app/assistant/runtime/system_seed/manifest.v1.json`
- Modify: `backend/app/assistant/runtime/system_seed/expected.py`
- Modify: `backend/scripts/build_assistant_system_seed.py`
- Modify: affected Tool/Skill/workflow/OpenClaw tests.

**Interfaces:**

- Consumes: Plan 2 trusted seed and Main-Agent-only runtime, existing read Capability Registry, ordinary human Entry/Relation APIs, and current Tool/OpenClaw/system-asset inventories.
- Produces: one exact Agent write declaration, side-effect-free typed unsupported boundaries, no update/merge/relation Provider path, regenerated seed/tool contract digests, and an exhaustive AST/runtime surface inventory gate.

- [ ] **Step 1: Write the failing cross-surface inventory test**

```python
UNSUPPORTED = {
    "update_entry",
    "merge_entry",
    "create_relation",
    "relation_followup",
    "openclaw_create_relation",
}


def test_all_production_agent_surfaces_have_exactly_one_write():
    surfaces = collect_agent_capability_surfaces()
    assert surfaces.provider_write_names == {"create_entry"}
    assert surfaces.tool_registry_write_names == {"create_entry"}
    assert surfaces.assistant_exports_write_names == {"create_entry"}
    assert surfaces.trusted_seed_write_names == {"create_entry"}
    assert surfaces.system_asset_write_names == {"create_entry"}
    assert UNSUPPORTED.isdisjoint(surfaces.all_exposed_names)


def test_human_rest_entry_and_relation_routes_remain_present(app):
    paths = {(route.path, tuple(sorted(route.methods or ()))) for route in app.routes}
    assert any(path == "/api/entries/{id}" and "PUT" in methods for path, methods in paths)
    assert any(path == "/api/relations" and "POST" in methods for path, methods in paths)
```

- [ ] **Step 2: Run focused inventory and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_production_capability_surface.py -q
```

Expected: failure lists `update_entry`, `create_relation`, OpenClaw relation, and write-containing system assets.

- [ ] **Step 3: Define the closed supported/unsupported vocabulary**

```python
SUPPORTED_PRODUCTION_WRITE_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"create_entry"}
)
UNSUPPORTED_PRODUCTION_WRITE_BRANCHES: Final[frozenset[str]] = frozenset(
    {"update_entry", "merge_entry", "create_relation", "relation_followup"}
)


class CapabilityNotSupported(CapabilityDomainError):
    def __init__(self, branch: str) -> None:
        if branch not in UNSUPPORTED_PRODUCTION_WRITE_BRANCHES:
            raise ValueError("unsupported branch identifier is not allowlisted")
        super().__init__(
            CapabilityError(
                error_type="unsupported",
                safe_code="capability_not_supported",
                safe_message="This write capability is not supported.",
                retry_disposition="never",
                target_identity=f"unsupported-write:{branch}",
            )
        )
        self.branch = branch
```

Add `record_unsupported_write_attempt(branch, safe_entrypoint)` that accepts only enum values and increments/logs no prompt, IDs, arguments, titles, or content.

- [ ] **Step 4: Remove unsupported ToolRegistry and lazy exports**

Delete `update_entry` and `create_relation` definitions, schemas, output fields, registry mappings, classifier/policy write classifications, and `assistant.tools.__all__` entries. Delete OpenClaw relation definition/adapter/service dispatch. Keep OpenClaw read capabilities and any separately authenticated human/machine integration endpoint that is not exposed as an Agent capability only if its route policy remains explicit.

After the change:

```python
write_definitions = {
    key
    for key, definition in ToolRegistry.definitions().items()
    if definition.side_effect_class in {"write_local", "write_external"}
}
assert write_definitions == {"create_entry"}
```

- [ ] **Step 5: Convert retained direct functions to typed non-writing boundaries**

Remove `@tool` from update/relation functions and replace bodies:

```python
def update_entry(*args: object, **kwargs: object) -> NoReturn:
    record_unsupported_write_attempt("update_entry", "direct_agent_boundary")
    raise CapabilityNotSupported("update_entry")


def create_relation(*args: object, **kwargs: object) -> NoReturn:
    record_unsupported_write_attempt("create_relation", "direct_agent_boundary")
    raise CapabilityNotSupported("create_relation")
```

Add equivalent explicit boundaries for merge and relation follow-up in `supported_writes.py`. These functions do not import `EntryService`, `RelationService`, or CapabilityCall repositories.

- [ ] **Step 6: Prove every unsupported boundary has zero side effect**

```python
@pytest.mark.parametrize(
    "branch",
    ["update_entry", "merge_entry", "create_relation", "relation_followup"],
)
def test_unsupported_branch_creates_nothing(db, branch):
    before = database_effect_snapshot(db)
    with pytest.raises(CapabilityNotSupported) as exc:
        call_unsupported_boundary(branch, db=db, payload=valid_payload(branch))
    assert exc.value.error.safe_code == "capability_not_supported"
    assert database_effect_snapshot(db) == before
    assert db.query(AssistantCapabilityCall).count() == 0
    assert db.query(Entry).count() == 0
    assert db.query(Relation).count() == 0
```

Also assert the `create_entry` boundary was never invoked as a substitute and unsupported metric metadata contains only branch and safe entrypoint.

- [ ] **Step 7: Delete write-containing system assets and registry entries**

Delete the six files listed above and their `SystemAssetDefinition` entries. Retain `smart_capture_golden_create.json`/`.en.json` only if its audited graph has exactly one `create_entry`, no update/merge/relation/workflow-call branch, and no human node (approval is CapabilityCall-owned).

Scan every retained JSON asset recursively; fail if a tool name/asset key/node label equals or references an unsupported branch. Do not merely hide the assets in UI.

- [ ] **Step 8: Keep the trusted Skill explicit about unsupported intent**

The trusted `SKILL.md` says `create_entry` is the sole write and update/merge/relation requests are unsupported. It must not instruct the model to simulate an update by creating a new Entry. `mindatlas.yaml` binds exactly approved reads plus `create_entry`; no unsupported key or deleted workflow reference appears.

- [ ] **Step 9: Regenerate and verify seed/tool digests**

Run:

```bash
.venv/bin/python scripts/build_assistant_system_seed.py --write
.venv/bin/python scripts/build_assistant_system_seed.py --check
```

Expected: generated manifest/expected module change deterministically and check is byte-clean. The seed contract digest covers the new exact Tool contract set.

- [ ] **Step 10: Add AST and runtime regression gates**

Assert:

- no unsupported string is a key in ToolRegistry/OpenClaw/SystemAsset registries;
- no unsupported function has `@tool` decoration;
- Provider schema builder emits no unsupported function;
- trusted seed has one write binding;
- retained assets contain no unsupported write node;
- Capability Registry returns `capability_not_supported` for a forged frozen unsupported key without executing;
- human REST routes still exist and remain Session/CSRF protected per Plan 1.

- [ ] **Step 11: Run focused surface/boundary/seed tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_production_capability_surface.py \
  tests/test_unsupported_write_boundaries.py \
  tests/test_assistant_kb_tools.py \
  tests/test_capability_registry.py \
  tests/test_binding_json_schema.py \
  tests/test_main_agent_golden_create_entry.py -q
.venv/bin/python scripts/build_assistant_system_seed.py --check
cd ..
git diff --check
```

Expected: all tests pass; sole Agent write is `create_entry`; unsupported calls leave no durable row; seed check and formatting pass.

- [ ] **Step 12: Commit**

```bash
git add -A \
  backend/app/assistant/capabilities \
  backend/app/assistant/capability_calls \
  backend/app/assistant/tools \
  backend/app/assistant/workflow/system_assets \
  backend/app/assistant_config/registry.py \
  backend/app/openclaw_integration \
  backend/app/assistant/runtime/system_seed \
  backend/scripts/build_assistant_system_seed.py \
  backend/tests
git commit -m "refactor(write): expose only create entry"
```

---

### Task 2: Freeze the Create-Entry Contract and Add a Global Fail-Closed Write Guard

**Files:**

- Create: `backend/app/assistant/capability_calls/write_guard.py`
- Create: `backend/tests/test_create_entry_production_guard.py`
- Create: `backend/tests/test_create_entry_write_guard_postgres.py`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Modify: `backend/app/assistant/runtime/contracts.py`
- Modify: `backend/app/assistant/runtime/models.py`
- Modify: `backend/app/assistant/runtime/closure.py`
- Modify: `backend/app/assistant/runtime/bootstrap.py`
- Modify: `backend/app/assistant/models.py`
- Modify: `backend/app/assistant/run_service.py`
- Modify: `backend/app/assistant/capability_calls/release_admission.py`
- Modify: `backend/app/assistant/capability_calls/aggregate.py`
- Modify: `backend/app/assistant/capability_calls/repository.py`
- Modify: `backend/app/assistant/capability_calls/reconciliation.py`
- Modify: `backend/app/assistant/runtime/system_seed/manifest.v1.json`
- Modify: `backend/app/assistant/runtime/system_seed/expected.py`
- Modify: `backend/scripts/build_assistant_system_seed.py`

**Interfaces:**

- Consumes: sole create Provider declaration, CapabilityCall lookup/proposal, Plan 2 immutable rollout/Run closure, enforced ledger, durable Interrupt setting, server Idempotency Secret, reconciliation path, schema/launch authorization ports, and PostgreSQL.
- Produces: deterministic create/write-policy/cohort digests frozen into rollout and Run, `ProductionWriteGuard`, one advisory-lock protocol for new-write and unresolved transitions, zero-call rejection for blocked new proposals, and post-approval revalidation immediately before mutation.

- [ ] **Step 1: Write fixed contract/policy/cohort digest tests**

```python
def test_create_entry_contract_digest_binds_execution_safety_contracts():
    payload = create_entry_contract_payload()
    assert payload == {
        "schemaVersion": 1,
        "domainKey": "create_entry",
        "inputSchemaDigest": system_tool_input_schema_digest("create_entry"),
        "outputSchemaDigest": system_tool_output_schema_digest("create_entry"),
        "localAdapterContractVersion": 1,
        "capabilityLedgerContractVersion": CAPABILITY_LEDGER_CONTRACT_VERSION,
        "approvalBindingContractVersion": APPROVAL_BINDING_CONTRACT_VERSION,
        "idempotencyContractVersion": IDEMPOTENCY_CONTRACT_VERSION,
        "reconciliationContractVersion": RECONCILIATION_CONTRACT_VERSION,
    }
    assert CREATE_ENTRY_CONTRACT_DIGEST == sha256_canonical_json(payload)


def test_write_policy_and_cohort_are_code_owned():
    assert WRITE_COHORT_PAYLOAD == {
        "schemaVersion": 1,
        "cohort": "single_operator_main_agent",
        "supportedWrites": ["create_entry"],
    }
    assert WRITE_COHORT_DIGEST == sha256_canonical_json(WRITE_COHORT_PAYLOAD)
    assert WRITE_POLICY_DIGEST == sha256_canonical_json(write_policy_payload())
```

The policy payload binds `enforced` ledger, call-owned durable approval, local transactional execution, same-call idempotency/replay, post-approval guard, and reconciliation-on-uncertain semantics.

- [ ] **Step 2: Write the complete new-write guard matrix before implementation**

```python
@pytest.mark.parametrize(
    ("arrangement", "reason"),
    [
        ("write_mode_off", "create_entry_not_enabled"),
        ("ledger_not_enforced", "write_safety_blocked"),
        ("interrupts_disabled", "write_safety_blocked"),
        ("idempotency_secret_missing", "write_safety_blocked"),
        ("reconciliation_unavailable", "write_safety_blocked"),
        ("schema_incompatible", "write_safety_blocked"),
        ("production_launch_missing", "pre_ga_launch_unapproved"),
        ("rehearsal_authorization_missing", "write_safety_blocked"),
        ("closure_contract_drift", "write_safety_blocked"),
        ("binding_not_create", "capability_not_supported"),
        ("approval_not_call_owned", "write_safety_blocked"),
        ("unknown_call_open", "reconciliation_required"),
        ("needs_reconciliation_open", "reconciliation_required"),
    ],
)
def test_new_write_guard_fails_before_call(write_state, arrangement, reason):
    write_state.arrange(arrangement)
    before = write_state.effect_snapshot()
    result = write_state.propose_new_create()
    assert result.reason_code == reason
    assert write_state.effect_snapshot() == before
    assert write_state.capability_call_count() == 0
```

- [ ] **Step 3: Run focused tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_create_entry_production_guard.py -q
```

Expected: tests fail because contract digests/guard do not exist and old write mode is `golden`.

- [ ] **Step 4: Replace ambiguous golden/cohort configuration**

Change:

```python
AssistantMainAgentWriteMode = Literal["off", "create_entry"]
```

Remove environment-driven `ASSISTANT_MAIN_AGENT_WRITE_COHORT_DIGEST`; the approved single-Operator cohort is code-owned and frozen into closure. Remove the legacy configured reconciliation Operator ID; an Operator is authenticated from the Session at mutation time. Keep secrets blank by default and excluded from repr.

Configuration validator for `write_mode == "create_entry"` requires:

- `ASSISTANT_CAPABILITY_LEDGER_MODE=enforced`;
- durable Interrupts true with a stable pepper;
- reconciliation enabled with a minimum-32-byte evidence HMAC secret;
- Idempotency Secret at least 32 UTF-8 bytes;
- non-development immutable build revision in rehearsal/production;
- deployment class and launch/release-profile settings mutually valid.

It does not require a launch-control flag; durable launch state is database-owned.

- [ ] **Step 5: Extend immutable runtime Subject/closure**

Add exactly these fields to both `AssistantRuntimeSubject` and `AssistantRuntimeClosure`:

```python
create_entry_contract_digest: str
write_policy_digest: str
write_cohort_digest: str
reconciliation_contract_version: int
```

Add them to rollout revision rows and Run-frozen identity:

```python
required_create_entry_contract_digest = Column(String(64), nullable=False)
required_write_policy_digest = Column(String(64), nullable=False)
required_write_cohort_digest = Column(String(64), nullable=False)
required_reconciliation_contract_version = Column(Integer, nullable=False)
```

The Plan 4 migration adds physical columns/constraints. Extend rollout/Run immutability trigger definitions in that migration. Until Task 7 lands, every Task 2–6 database fixture—SQLite or PostgreSQL—must create a new throwaway schema from current `Base.metadata`; those commits must not run against an Alembic-upgraded `pre_ga_v1_0001` database. No mixed code/schema deployment, shared test database, migration rehearsal, or production startup is allowed in that interval. Task 7 then makes Alembic/model parity a hard test and reruns the PostgreSQL write suites against an upgraded `pre_ga_v1_0002` database before any deployable checkpoint exists.

- [ ] **Step 6: Include write fields in every canonical digest path**

`build_subject()` uses only code-owned values:

```python
subject = AssistantRuntimeSubject(
    **base_subject,
    create_entry_contract_digest=CREATE_ENTRY_CONTRACT_DIGEST,
    write_policy_digest=WRITE_POLICY_DIGEST,
    write_cohort_digest=WRITE_COHORT_DIGEST,
    reconciliation_contract_version=RECONCILIATION_CONTRACT_VERSION,
)
```

Rollout revision digest, closure digest, Run frozen identity, Worker claim compatibility input, seed contract manifest, and launch subject all include them. Drift in any one value fails closure/readiness rather than selecting a different write path.

- [ ] **Step 7: Define launch/rehearsal authorization as a port**

Avoid a circular dependency before Task 9:

```python
class WriteLaunchAuthorization(Protocol):
    def allows_current_subject(
        self,
        db: Session,
        *,
        closure: AssistantRuntimeClosure,
        deployment_class: DeploymentClass,
    ) -> bool: ...
```

Production composition later uses `PreGaLaunchService`; rehearsal composition uses the short-lived server-owned profile authorization from Task 6; development returns false unless an explicit test-only injected fake is used in unit tests. No environment boolean implements the production port.

- [ ] **Step 8: Implement one advisory-locked guard**

```python
WRITE_SAFETY_ADVISORY_LOCK_KEY = 0x4D41575249544531
UNRESOLVED_WRITE_STATUSES = ("unknown", "needs_reconciliation")


class ProductionWriteGuard:
    def evaluate_new_proposal_locked(
        self,
        *,
        run: AssistantChatRun,
        closure: AssistantRuntimeClosure,
        domain_key: str,
        binding: FrozenCapabilityBinding,
        approval_mode: str,
    ) -> ProductionWriteGuardSnapshot:
        if domain_key != "create_entry":
            raise CapabilityNotSupported(normalize_unsupported_branch(domain_key))
        self._lock()
        return self._evaluate(
            run=run,
            closure=closure,
            binding=binding,
            approval_mode=approval_mode,
        )
```

`_lock()` executes `pg_advisory_xact_lock` on PostgreSQL. SQLite tests use an injected lock port; production code must not silently skip a PostgreSQL lock because dialect detection failed.

- [ ] **Step 9: Query unresolved calls globally and safely**

Under the lock, execute one grouped count:

```python
rows = self.db.execute(
    select(AssistantCapabilityCall.status, func.count())
    .where(AssistantCapabilityCall.status.in_(UNRESOLVED_WRITE_STATUSES))
    .group_by(AssistantCapabilityCall.status)
).all()
counts = {str(status): int(count) for status, count in rows}
```

Any positive count returns `reconciliation_required`. The snapshot contains counts for internal diagnostics but public errors only contain the stable reason. Database/query failure is `write_safety_blocked` and creates no call.

- [ ] **Step 10: Order exact guard checks**

After exact supported branch and lock:

1. schema compatibility;
2. current immutable rollout/Run closure equality;
3. deployment-class-specific launch authorization;
4. process write mode `create_entry`;
5. frozen Run ledger `enforced`;
6. code/Run create contract, policy, cohort, reconciliation version equality;
7. exact binding/domain/input/output/target digest;
8. call-owned durable approval mode;
9. Interrupt/reconciliation/Operator control availability;
10. Idempotency Secret strength;
11. unresolved global counts zero.

The returned `allowed=True` snapshot binds all four write contract values. It does not include secret values.

- [ ] **Step 11: Replay existing calls before applying the new-write guard**

In the proposal flow:

```python
existing = repository.find_by_logical_identity(
    run_id=run.id,
    logical_call_key=logical_call_key,
    provider_tool_call_id=provider_tool_call_id,
)
if existing is not None:
    return replay_or_conflict_existing_call(existing, exact_request_digest)

guard = write_guard.evaluate_new_proposal_locked(
    run=run,
    closure=closure,
    domain_key=domain_key,
    binding=binding,
    approval_mode="call_owned_durable",
)
if not guard.allowed:
    return rejected_without_call(guard.reason_code)
return repository.create_proposed_call(...)
```

An exact duplicate can replay a prior succeeded/pending/failed/unknown state even while new writes are blocked. A reused logical/provider identity with different input remains a conflict and never creates another call/Entry.

- [ ] **Step 12: Take the same lock on transitions into unresolved states**

Before a repository transition sets `unknown` or `needs_reconciliation`, acquire `WRITE_SAFETY_ADVISORY_LOCK_KEY` in the same transaction. Reconciliation terminalization uses the lock as well. Thus either a new proposal commits before unresolved state appears or observes it and rejects; no proposal can race past the transition.

Add a two-session PostgreSQL test with barriers and prove both serial orders.

- [ ] **Step 13: Recheck guard after approval and immediately before local staging**

Inside the same transaction that will stage Entry/call attempt/result/checkpoint:

```python
post_approval = write_guard.evaluate_post_approval_locked(
    call=call,
    run=run,
    closure=closure,
    approved_interrupt=interrupt,
)
if not post_approval.allowed:
    repository.fail_before_side_effect(
        call,
        failure_code=post_approval.reason_code or "write_safety_blocked",
    )
    return capability_failure(post_approval.reason_code)
entry = aggregate.stage_create_entry_local(...)
```

The recheck validates the exact approval binding digest/interrupt origin/status and all current launch/unresolved facts. Failure sets no `side_effect_started_at`, creates no Entry, and does not auto-retry or reinterpret.

- [ ] **Step 14: Extend immutability and fixture assertions**

Update complete Run fixtures and tests so no default exists for the four new fields. PostgreSQL migration Task later proves UPDATE cannot change them. Test closure drift for each field and ensure activation/admission fail with existing `runtime_closure_drift`.

- [ ] **Step 15: Regenerate trusted seed after contract freeze**

Run:

```bash
.venv/bin/python scripts/build_assistant_system_seed.py --write
.venv/bin/python scripts/build_assistant_system_seed.py --check
```

Expected: seed contract now binds surface plus create/write-policy/cohort/reconciliation versions. No caller/environment digest enters generated bytes.

- [ ] **Step 16: Run unit and PostgreSQL race suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_create_entry_production_guard.py \
  tests/test_assistant_runtime_closure.py \
  tests/test_capability_call_write_admission.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
    tests/test_create_entry_write_guard_postgres.py -q
.venv/bin/python scripts/build_assistant_system_seed.py --check
```

Expected: all tests pass without PostgreSQL skip; blocked new proposals create no call; existing exact calls replay; unresolved transition races serialize; post-approval drift creates no Entry.

- [ ] **Step 17: Commit**

```bash
git add \
  backend/app/config.py \
  backend/.env.example \
  backend/app/assistant/runtime \
  backend/app/assistant/models.py \
  backend/app/assistant/run_service.py \
  backend/app/assistant/capability_calls \
  backend/scripts/build_assistant_system_seed.py \
  backend/tests/test_create_entry_production_guard.py \
  backend/tests/test_create_entry_write_guard_postgres.py \
  backend/tests/test_assistant_runtime_closure.py \
  backend/tests/test_capability_call_write_admission.py
git commit -m "feat(write): guard create entry production path"
```

---

### Task 3: Qualify the Call-Owned Approval, Transactional Write, Recovery, and Reconciliation Path

**Files:**

- Create: `backend/app/assistant/capability_calls/reconciliation_router.py`
- Create: `backend/app/assistant/capability_calls/reconciliation_schemas.py`
- Create: `backend/tests/test_create_entry_production_postgres.py`
- Create: `backend/tests/test_create_entry_reconciliation_api.py`
- Modify: `backend/app/assistant/capability_calls/aggregate.py`
- Modify: `backend/app/assistant/capability_calls/approval.py`
- Modify: `backend/app/assistant/capability_calls/contracts.py`
- Modify: `backend/app/assistant/capability_calls/dispatcher.py`
- Modify: `backend/app/assistant/capability_calls/idempotency.py`
- Modify: `backend/app/assistant/capability_calls/local_write.py`
- Modify: `backend/app/assistant/capability_calls/reconciliation.py`
- Modify: `backend/app/assistant/capability_calls/repository.py`
- Modify: `backend/app/assistant/capability_calls/settlement.py`
- Modify: `backend/app/assistant/capability_calls/uow.py`
- Modify: `backend/app/assistant/router.py`
- Modify: `backend/app/assistant/workflow/durable/interrupt_api.py`
- Modify: `backend/app/assistant/durable/repository.py`
- Modify: `backend/app/entry/models.py`
- Modify: `backend/app/entry/service.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_capability_call_fault_matrix.py`
- Modify: `backend/tests/test_capability_call_local_transaction.py`
- Modify: `backend/tests/test_capability_call_result_replay.py`
- Modify: `backend/tests/test_durable_interrupt_api.py`

**Interfaces:**

- Consumes: Task 1's sole `create_entry` declaration, Task 2's frozen write closure and locked `ProductionWriteGuard`, Plan 1's `OperatorPrincipal`/Session/CSRF/audit ports, Plan 2's Worker lease and checkpoint codec v3, existing CapabilityCall aggregate/Interrupt/Artifact/obligation repositories, and `Entry.source_capability_call_id`.
- Produces: one gateway-only create execution path; call-owned approval decisions; one-transaction Entry/Attempt/result Artifact/checkpoint/obligation settlement; deterministic duplicate replay; commit-ambiguity recovery; authenticated viewer/Operator reconciliation HTTP APIs; and PostgreSQL evidence that every duplicate/fault schedule creates at most one Entry.

- [ ] **Step 1: Freeze the execution-path invariants in failing architecture tests**

Add tests that import the Provider declaration and local adapter separately:

```python
def test_decorated_create_entry_cannot_write_outside_gateway(db):
    before = database_effect_snapshot(db)
    with pytest.raises(CapabilityGatewayRequired) as exc:
        invoke_declaration_directly("create_entry", valid_create_arguments())
    assert exc.value.safe_code == "capability_gateway_required"
    assert database_effect_snapshot(db) == before


def test_local_adapter_has_no_provider_or_committing_service_dependency():
    imports, calls = inspect_local_write_architecture()
    assert "app.assistant.tools.entry_tools" not in imports
    assert "EntryService.create" not in calls
    assert "EntryService.create_in_uow" in calls
```

The architecture collector parses the module AST and also executes the declaration with a Session spy whose `commit()` raises. A direct declaration may validate/normalize a payload, but it cannot open a Session, create a call, stage an Entry, or fall back to the old tool implementation.

- [ ] **Step 2: Run the execution-boundary tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_create_entry_production_postgres.py \
  tests/test_capability_call_local_transaction.py \
  -q -k 'gateway_required or architecture'
```

Expected: the existing decorated function remains directly executable or the gateway/local adapter contract is incomplete, so at least one new assertion fails before any implementation change.

- [ ] **Step 3: Make the Provider declaration a non-writing gateway envelope**

The Provider-facing declaration returns normalized request data only when invoked by a valid `CapabilityGatewayInvocation`; an ordinary Python call returns a typed failure:

```python
class CapabilityGatewayRequired(CapabilityDomainError):
    safe_code: Literal["capability_gateway_required"] = "capability_gateway_required"


@tool("create_entry", args_schema=CreateEntryCapabilityInput)
def create_entry_declaration(
    title: str,
    content: str,
    *,
    _gateway_invocation: CapabilityGatewayInvocation | None = None,
) -> CreateEntryProposal:
    if _gateway_invocation is None or not _gateway_invocation.verified:
        raise CapabilityGatewayRequired()
    return CreateEntryProposal.from_normalized(title=title, content=content)
```

The gateway constructs `_gateway_invocation`; Provider/model payloads cannot populate it because it is absent from JSON Schema. Do not expose a boolean or environment variable that can forge the marker.

- [ ] **Step 4: Write the failing call-owned approval matrix**

Parameterize approval origin and outcome:

```python
@pytest.mark.parametrize(
    ("origin", "outcome", "expected_status"),
    [
        ("capability_call", "approve", "authorized"),
        ("capability_call", "reject", "rejected"),
        ("capability_call", "expire", "expired"),
        ("capability_call", "cancel", "cancelled"),
        ("workflow", "approve", "awaiting_approval"),
        ("other_call", "approve", "awaiting_approval"),
    ],
)
def test_only_exact_call_owned_interrupt_can_authorize(...):
    ...
```

For every non-approved case assert `attempt_count == 0`, `side_effect_started_at is None`, and no Entry/result Artifact exists. Add binding-drift cases for call ID, logical key, input/descriptor/authorization/principal digest, request revision, and Interrupt origin/status.

- [ ] **Step 5: Bind approval creation to the exact frozen call**

Create the durable Interrupt in the same transaction as `proposed -> awaiting_approval`. Store the call ID in both the typed Interrupt payload and `AssistantCapabilityCall.interrupt_id`; compute the approval binding only from persisted frozen fields:

```python
binding = build_approval_binding(
    call_id=call.id,
    logical_call_key=call.logical_call_key,
    owner_digest=manifest.owner_digest,
    binding_contract_digest=manifest.binding_contract_digest,
    input_digest=call.input_digest,
    target_version_id=call.target_version_id,
    target_digest=manifest.target_digest,
    descriptor_digest=call.descriptor_digest,
    authorization_digest=call.authorization_digest,
    principal_digest=run.principal_digest,
    request_revision=interrupt.request_revision,
)
```

The API decision endpoint must load the persisted Interrupt and call, verify `origin == "capability_call"`, matching IDs, pending status, unexpired database time, and the exact binding digest before transition. Approval response bodies cannot override any digest.

- [ ] **Step 6: Require authenticated Operator Session and CSRF for approval mutation**

Replace any unauthenticated durable Interrupt mutation policy with Plan 1 dependencies. Viewer may list safe pending cards; only Operator plus valid CSRF may approve/reject/cancel. The audit append receives the authenticated `operator_id` and `session_id` from `OperatorPrincipal`, not request JSON or a configured admin UUID:

```python
principal = require_operator_principal(request, db)
require_csrf(request, principal)
decision = interrupt_service.decide_call_owned(
    interrupt_id=interrupt_id,
    expected_revision=body.expected_revision,
    request_id=body.request_id,
    decision=body.decision,
    actor=principal,
)
```

CSRF failure, viewer role, expired Session, request-ID reuse with a different digest, or stale revision leaves the call/Interrupt unchanged and creates no Entry.

- [ ] **Step 7: Write the failing atomic-settlement proof**

Add a SQLAlchemy event listener that captures commits and table changes. For a successful call assert exactly one commit makes all of these facts visible together:

```python
assert committed_bundle == {
    "assistant_capability_call": {"status": "succeeded"},
    "assistant_capability_call_attempt": {"status": "committed"},
    "entry": {"source_capability_call_id": str(call.id)},
    "assistant_run_artifact": {"kind": "capability_result"},
    "assistant_run_checkpoint": {"codec_version": 3},
    "assistant_run_obligation_revision": {"status": "satisfied"},
}
assert call.output_artifact_id == result_artifact.id
assert call.side_effect_started_at == attempt.side_effect_started_at
```

Before the transaction commits, a second PostgreSQL Session must observe none of the new Entry/success bundle. After commit it observes all of it.

- [ ] **Step 8: Refactor the local write into one ledger-owned unit of work**

Make `stage_create_entry_local()` the only business mutation and prohibit it from committing. In one locked Session:

1. reload Run, call, exact pending approval, and Worker lease;
2. call Task 2's post-approval guard;
3. claim/update the append-only Attempt;
4. set side-effect-start only as part of the local transactional settlement;
5. stage Entry with `source_capability_call_id=call.id`;
6. encode and stage safe result Artifact;
7. stage codec-v3 checkpoint and resolve the exact call obligation;
8. transition call and Run revisions;
9. commit once through `CapabilityUnitOfWork`.

Use an explicit result object:

```python
@dataclass(frozen=True, slots=True)
class LocalCreateEntrySettlement:
    call_id: UUID
    entry_id: UUID
    attempt_id: UUID
    output_artifact_id: UUID
    checkpoint_id: UUID
    resulting_call_revision: int
    resulting_run_revision: int
```

No event, response, or checkpoint contains Entry body/title; references and digests are sufficient.

- [ ] **Step 9: Enforce one Entry per CapabilityCall in repository and database tests**

Keep the partial unique index on `Entry.source_capability_call_id` and make the field immutable after insert in the `pre_ga_v1_0002` migration. Repository code first queries by this key when settling/recovering. Add PostgreSQL tests for two Sessions attempting the same call and prove one returns created and the other returns replay of the same Entry ID after the winning transaction commits.

Also prove a human-created Entry has `source_capability_call_id IS NULL`, and the human REST create/edit paths continue to work under Plan 1 authorization.

- [ ] **Step 10: Define duplicate identity and replay before write admission**

The server HMAC derives `logical_call_key`/idempotency identity from run, frozen manifest, capability key, Provider tool-call identity, and canonical input digest. It never stores/logs the raw Provider/browser idempotency value:

```python
identity = derive_capability_call_identity(
    secret=settings.capability_idempotency_secret_bytes(),
    run_id=run.id,
    manifest_revision_id=manifest.id,
    capability_key="create_entry",
    provider_tool_call_id=provider_tool_call_id,
    input_digest=input_artifact.digest,
)
existing = repository.find_exact_identity(identity)
if existing is not None:
    return replay_existing_call(existing)
write_guard.assert_new_write_allowed_locked(...)
```

Same identity with different canonical input is `idempotency_conflict`; it cannot replay, create a new call, or substitute another key. Exact replay is permitted while new writes are blocked because it is not a new write.

- [ ] **Step 11: Exercise duplicate Provider, browser, Resume, and Worker paths**

Use barriers to submit each pair concurrently through its real boundary:

- duplicate Provider tool calls with the same server-derived identity;
- duplicate approval browser POSTs with the same request ID;
- duplicate Resume delivery for the same Interrupt revision;
- Worker A lease expiry followed by Worker B takeover;
- response replay after the original caller loses its connection.

For each schedule assert one call, one approval Interrupt, one successful Attempt/settlement, one Entry, one result Artifact, one obligation resolution, and stable replayed IDs. A stale Worker cannot commit after lease takeover.

- [ ] **Step 12: Add deterministic fault injection around every write boundary**

Introduce a test-only injected `CapabilityFaultPort` with named points compiled out of normal construction:

```python
CreateEntryFaultPoint = Literal[
    "before_proposal",
    "after_proposal",
    "before_approval_decision",
    "after_approval_decision",
    "after_entry_stage_before_commit",
    "after_commit_before_ack",
    "after_commit_before_checkpoint_observation",
]
```

The port is supplied directly to test factories; no production setting, HTTP parameter, Provider argument, or dynamic import enables it. Test cancellation before side-effect start and cancellation racing after settlement separately.

- [ ] **Step 13: Classify recovery from rollback versus commit ambiguity**

For pre-commit injected failures, rollback leaves no Entry and a safe retry can use the same call/lease rules. For connection loss during/after commit, recovery first queries by `Entry.source_capability_call_id` in a fresh Session:

```python
entry = entries.find_by_source_capability_call_id(call.id)
if entry is not None:
    return settlement.recover_committed_local_write(call=call, entry=entry)
if database_commit_outcome_is_proven_rolled_back(observation):
    return settlement.resume_same_call(call)
return settlement.mark_needs_reconciliation_locked(
    call, failure_code="local_commit_outcome_unknown"
)
```

An ambiguous outcome is never automatically retried. Transition to `unknown`/`needs_reconciliation` uses Task 2's advisory lock and blocks new writes globally until terminalized.

- [ ] **Step 14: Define the reconciliation request and safe evidence envelope**

Expose only bounded fields:

```python
class ReconcileCapabilityCallRequest(CamelModel):
    expected_call_revision: int = Field(ge=0)
    expected_run_revision: int = Field(ge=0)
    decision: Literal["mark_succeeded", "mark_failed", "mark_compensated"]
    evidence_artifact_ids: list[UUID] = Field(min_length=1, max_length=8)
    request_id: UUID
    reason: str = Field(min_length=1, max_length=500)
```

`retry_same_key` is excluded for `local_transactional create_entry`; the server rejects it even if forged. Evidence Artifacts contain a server-signed HMAC envelope bound to call/Run IDs, exact Attempt, input digest, observed Entry ID or proven absence, issue time, and evidence contract version. Client fields cannot assert success or provide a signature string.

- [ ] **Step 15: Mount viewer list and Operator reconciliation routes**

`GET /api/capability-calls/reconciliation` uses `require_viewer_principal` and returns safe IDs, revisions, status, failure code, timestamps, mode, and evidence-required hints only. `POST .../{call_id}/reconcile` requires Operator+CSRF, takes the write-safety advisory lock plus call/Run row locks, verifies Artifact ownership/digests/signature/freshness, applies request replay/CAS, appends the reconciliation row, settles checkpoint/obligation, and appends `OperatorAuditRepository` in one transaction.

Remove the configured-operator/CLI authorizer from production construction. A maintenance CLI may inspect safe summaries only; it cannot manufacture an Operator identity or mutate reconciliation state.

- [ ] **Step 16: Prove reconciliation authorization, idempotency, and global unblocking**

Test viewer read, viewer mutation rejection, missing/wrong CSRF, expired Session, stale revisions, changed request body under reused request ID, unsigned/expired/wrong-call evidence, and safe error bodies. Then prove:

1. unresolved call blocks a new proposal without creating a row;
2. a valid Operator decision terminalizes the exact call under lock;
3. audit actor/session match the authenticated principal;
4. an exact replay returns the original reconciliation ID/revisions;
5. after all unresolved calls are terminal, a new create proposal can proceed;
6. reconciliation remains callable while Chat admission, writes, or launch readiness are blocked.

- [ ] **Step 17: Run focused unit/API/PostgreSQL fault suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_capability_call_local_transaction.py \
  tests/test_capability_call_result_replay.py \
  tests/test_capability_call_fault_matrix.py \
  tests/test_durable_interrupt_api.py \
  tests/test_create_entry_reconciliation_api.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
    tests/test_create_entry_production_postgres.py \
    tests/test_create_entry_write_guard_postgres.py -q
cd ..
git diff --check
```

Expected: all tests pass with no PostgreSQL skip; direct declarations write nothing; approval/rejection/cancellation cases match the matrix; every duplicate/fault schedule converges to at most one Entry; ambiguity never auto-retries; reconciliation requires the real Session/CSRF principal.

- [ ] **Step 18: Commit**

```bash
git add \
  backend/app/assistant/capability_calls \
  backend/app/assistant/router.py \
  backend/app/assistant/durable \
  backend/app/assistant/workflow/durable/interrupt_api.py \
  backend/app/entry \
  backend/app/main.py \
  backend/tests/test_capability_call_fault_matrix.py \
  backend/tests/test_capability_call_local_transaction.py \
  backend/tests/test_capability_call_result_replay.py \
  backend/tests/test_durable_interrupt_api.py \
  backend/tests/test_create_entry_production_postgres.py \
  backend/tests/test_create_entry_reconciliation_api.py
git commit -m "feat(write): qualify create entry transaction recovery"
```

---

### Task 4: Compile Reproducible Python 3.11 API/Worker and Parse-Worker Locks

**Files:**

- Create: `backend/requirements/api-worker.in`
- Create: `backend/requirements/parse-worker.in`
- Create: `backend/requirements/constraints-python311.txt`
- Create: `backend/requirements/api-worker.lock`
- Create: `backend/requirements/parse-worker.lock`
- Create: `backend/requirements/compiler-bootstrap.lock`
- Create: `backend/requirements/compiler-image.txt`
- Create: `backend/requirements/README.md`
- Create: `backend/scripts/compile_requirements.py`
- Create: `backend/app/release/generated_lock_digests.py`
- Create: `backend/tests/test_dependency_locks.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/requirements-parse-worker.txt`
- Delete: `backend/requirements-docling.txt`
- Modify: `backend/Dockerfile`
- Modify: `.github/workflows/ci.yml`
- Modify: dependency-install references in `deploy/` and developer documentation.

**Interfaces:**

- Consumes: Python 3.11 support, repository-selected LangGraph `0.3.34`, current API/Assistant Worker and Docling parse-Worker direct requirements, Plan 1's password/Session crypto dependencies, Task 3's runtime imports, and Task 6's Ed25519 implementation dependency.
- Produces: two code-reviewed `--generate-hashes` lock files for Linux/amd64; a pinned compiler container identity; compatibility shims for old requirement entrypoints; Docker/CI installation exclusively from locks; deterministic per-lock/combined SHA-256 constants; and clean-install/import/conflict gates used by release evidence and the launch subject.

- [ ] **Step 1: Inventory imports, current dependency intent, and supported platform**

Record a machine-generated report in the test failure output, not as production evidence:

```bash
cd backend
.venv/bin/python -m pip freeze --all
rg -n '^(from|import) ' app tests scripts \
  | rg 'fastapi|starlette|sqlalchemy|alembic|pydantic|cryptography|langgraph|langchain|openai|docling|transformers|huggingface|torch|minio|psycopg'
docker version --format '{{.Server.Os}}/{{.Server.Arch}}'
```

Expected planning target: CPython 3.11 and release images on `linux/amd64`. If deployment requires another architecture, stop and review a separate lock/build matrix; do not reuse an unproved lock under a different platform.

- [ ] **Step 2: Write failing lock-policy tests before creating lock files**

```python
LOCKS = ("api-worker.lock", "parse-worker.lock")


@pytest.mark.parametrize("name", LOCKS)
def test_lock_is_hashed_pinned_and_contains_no_index_or_credentials(name):
    parsed = parse_requirements_lock(REQUIREMENTS / name)
    assert parsed.require_hashes
    assert parsed.unpinned_requirements == ()
    assert parsed.missing_hashes == ()
    assert parsed.index_directives == ()
    assert parsed.credential_like_text == ()


def test_langgraph_line_is_frozen():
    assert locked_version("api-worker.lock", "langgraph") == "0.3.34"
```

Also fail on editable/VCS/path dependencies, unsafe environment expansion, duplicate normalized project names, unexpected Python markers, CRLF, non-UTF-8 bytes, and direct URLs unless a separately allowlisted immutable wheel hash and review rationale exist. The initial run must fail because the new layout does not exist.

- [ ] **Step 3: Split direct inputs without changing the approved LangGraph line**

`api-worker.in` lists direct API, migration, Assistant Worker, test/qualification, MinIO, authentication, and evidence dependencies. It includes exact `langgraph==0.3.34`; compatible LangChain/OpenAI bounds belong in constraints and are solved, not silently upgraded. `parse-worker.in` lists only attachment Worker dependencies, including Docling, RapidOCR, ModelScope if still imported, database/MinIO contracts, and CPU Torch/vision required by the selected Docling resolution.

The split obeys these invariants:

```python
assert "docling" not in direct_names("api-worker.in")
assert "lightrag-hku" not in direct_names("parse-worker.in")
assert direct_pin("api-worker.in", "langgraph") == "0.3.34"
assert python_requires == "==3.11.*"
```

Keep `pytest` and repository-required test plug-ins as explicit API/qualification inputs for this two-lock baseline. This makes the exact test runner environment hash-bound; application source images still omit the test tree.

- [ ] **Step 4: Encode reviewed Python 3.11 compatibility constraints**

The constraints file pins policy-sensitive intersections, including LangGraph/LangChain/OpenAI, `pydantic`, `httpx`, `cryptography`, Docling/Transformers/Hugging Face Hub, Torch/TorchVision, NumPy, and protobuf families. Each non-obvious constraint has a short compatibility reason and upstream issue/release identifier without an unauthenticated download URL.

Add tests for the critical compatibility tuple:

```python
EXPECTED_COMPATIBILITY = {
    "python": "3.11",
    "langgraph": "0.3.34",
    "torch_flavor": "cpu",
    "release_platform": "linux/amd64",
}
```

If the solver proves the tuple unsatisfiable, stop with the complete resolver trace and choose an explicitly reviewed version change; do not relax upper bounds iteratively until resolution happens by accident.

- [ ] **Step 5: Pin and record the lock-compiler container identity**

Use a Python 3.11 slim-bookworm base image and `pip-tools==7.4.1`. Resolve the registry manifest for `linux/amd64`, review its publisher/platform, and store an immutable reference consisting of `repository@sha256:` followed by exactly 64 lowercase hexadecimal characters in `requirements/compiler-image.txt`. Generate/review `compiler-bootstrap.lock` once with `pip-tools==7.4.1` and all of its bootstrap dependencies pinned and hashed; thereafter the compiler container installs that file with `--require-hashes` before compilation. The script rejects tags/unhashed bootstrap and validates the running Python/pip-tools/platform tuple:

```python
def assert_compiler_environment() -> None:
    assert sys.version_info[:2] == (3, 11)
    assert sys.platform == "linux"
    assert platform.machine() == "x86_64"
    if version("pip-tools") != "7.4.1":
        raise LockCompileError("pip-tools compiler version mismatch")
```

Use `docker buildx imagetools inspect` to obtain the registry-reported digest, then verify `docker image inspect` resolves the same platform manifest before compilation. Commit the actual resolved digest and bootstrap lock; a mutable tag or network-installed unverified compiler package is never accepted by `compile_requirements.py`.

- [ ] **Step 6: Implement deterministic compile/check modes**

`compile_requirements.py` invokes `python -m piptools compile` inside the pinned container with locale/timezone fixed, normalized absolute-free headers, backtracking resolver, no index directive emission, and hashes:

```python
COMMON_ARGS = (
    "--resolver=backtracking",
    "--generate-hashes",
    "--allow-unsafe",
    "--strip-extras",
    "--no-emit-index-url",
    "--no-emit-trusted-host",
    "--newline=lf",
)
TARGETS = {
    "api-worker": "requirements/api-worker.in",
    "parse-worker": "requirements/parse-worker.in",
}
```

`--write` compiles into a temporary directory, validates both outputs, then atomically replaces both runtime locks only if all checks pass. `--check` installs `compiler-bootstrap.lock` in the immutable base container, compiles to temporary files, and byte-compares them with committed locks. It never prints environment values, configured indexes, credentials, or database URLs.

- [ ] **Step 7: Compile both locks in the pinned Linux container**

Run:

```bash
.venv/bin/python scripts/compile_requirements.py --write
.venv/bin/python scripts/compile_requirements.py --check
```

Expected: both locks are generated with hashes and a deterministic header naming only the input, constraints, compiler version, Python version, and immutable compiler image digest. The immediate check reports `api-worker.lock: byte-identical` and `parse-worker.lock: byte-identical`.

- [ ] **Step 8: Replace the old files with unambiguous compatibility boundaries**

Convert `backend/requirements.txt` to a documented compatibility shim containing only `-r requirements/api-worker.lock`. Convert `backend/requirements-parse-worker.txt` similarly to `-r requirements/parse-worker.lock`. Delete `requirements-docling.txt` because all parse direct intent now lives in `parse-worker.in`; tests reject any Docker/CI/deploy reference to it.

No input file may recursively include a lock, and no lock may include another requirement file. The two old shims are developer compatibility only; Docker and CI name locks directly.

- [ ] **Step 9: Generate immutable lock digest constants**

Canonical digest is SHA-256 over exact committed bytes. The generator writes only literals and recomputes the deployed combined digest from `api-worker.lock` and `parse-worker.lock` in an ordered domain-separated envelope:

```python
def lock_set_digest(lock_digests: Mapping[str, str]) -> str:
    return sha256_canonical_json(
        {
            "domain": "mindatlas:python-lock-set:v1",
            "python": "3.11",
            "platform": "linux/amd64",
            "locks": [[name, lock_digests[name]] for name in sorted(lock_digests)],
        }
    )
```

`generated_lock_digests.py` exports `API_WORKER_LOCK_SHA256`, `PARSE_WORKER_LOCK_SHA256`, `COMPILER_BOOTSTRAP_LOCK_SHA256`, and `DEPENDENCY_LOCK_SET_SHA256`. Tests import the module and compare each literal with current bytes. The deployed combined digest covers only API/parse locks and later enters evidence/`PreGaLaunchSubjectV1`; runner evidence separately binds the compiler base/bootstrap digest used to reproduce them.

- [ ] **Step 10: Make Docker install only hash-verified locks**

Copy the `requirements/` directory before source, then install with:

```dockerfile
COPY requirements/api-worker.lock /tmp/requirements/api-worker.lock
RUN python -m pip install --disable-pip-version-check \
      --no-cache-dir --require-hashes \
      -r /tmp/requirements/api-worker.lock \
    && python -m pip check
```

The Assistant Worker and API share this exact venv. The parse builder installs `parse-worker.lock` with `--require-hashes`; remove the separate unpinned Torch/index commands. Both final images expose OCI labels for build revision, lock filename digest, combined lock-set digest, Python version, and platform. A build argument may provide only values checked against generated constants; mismatch stops the build.

- [ ] **Step 11: Make CI cache/install keys lock-owned**

Update every Python job to cache by the appropriate `.lock`, install with `--require-hashes`, and run `python -m pip check`. Remove `pip install ... pytest` and every use of an input/shim as an install source. Add a lock-check job that runs compilation check in the pinned container and asserts the Dockerfile has no unpinned `pip install` invocation.

CI logs may show package names/versions but must not echo index configuration or credentials.

- [ ] **Step 12: Add API/Assistant Worker import smoke in a clean environment**

The smoke imports application construction, migrations, Operator auth, Assistant Worker, create-entry gateway, release crypto, and checkpoint codec, then prints only package versions and a success token:

```bash
python -c 'import fastapi, sqlalchemy, alembic, cryptography, langgraph, openai; import app.main, app.assistant.worker; print("api-worker-import-smoke: ok")'
```

Test that installed LangGraph is exactly `0.3.34`, Pydantic major is 2, cryptography exposes Ed25519, OpenAI adapter imports, and Plan 1 password hashing/session dependencies load.

- [ ] **Step 13: Add parse-Worker conflict and import smoke**

In a clean parse environment import `docling`, `transformers`, `huggingface_hub`, `torch`, `torchvision`, `rapidocr_onnxruntime`, and `app.attachment.worker`. Assert Torch and TorchVision compatibility, CPU-only runtime, no CUDA dependency, and versions equal lock metadata.

Add explicit resolver assertions for the historically fragile set:

```python
assert compatible_docling_transformers_hub(working_set)
assert compatible_torch_and_vision(working_set)
assert not any(dist.normalized_name.startswith("nvidia-") for dist in working_set)
```

Any import ABI failure or `pip check` conflict is a release failure; the test cannot skip because the package is optional in other environments.

- [ ] **Step 14: Prove two from-empty clean installs**

Run the repository script in disposable Docker volumes, once per lock:

```bash
.venv/bin/python scripts/compile_requirements.py clean-install \
  --target api-worker --platform linux/amd64
.venv/bin/python scripts/compile_requirements.py clean-install \
  --target parse-worker --platform linux/amd64
```

Each invocation must create a fresh venv from the pinned compiler/base image, install only its lock with `--require-hashes`, run `pip check`, run the target import smoke, compare installed distributions with lock evaluation for Python 3.11/Linux, and destroy the volume. Reusing `.venv`, pip cache, or the first target's site-packages fails the test.

- [ ] **Step 15: Build and inspect both production image targets**

Run:

```bash
docker build --platform linux/amd64 --target runtime \
  --build-arg APP_BUILD_REVISION="$(git rev-parse HEAD)" \
  -t mindatlas-api:lock-check backend
docker build --platform linux/amd64 --target assistant-worker \
  --build-arg APP_BUILD_REVISION="$(git rev-parse HEAD)" \
  -t mindatlas-assistant-worker:lock-check backend
docker build --platform linux/amd64 --target parse-worker \
  -t mindatlas-parse-worker:lock-check backend
```

Expected: builds succeed without a secondary package index; image inspection reports the expected per-lock and combined digests; API and Assistant Worker distribution sets are identical; parse Worker has Docling/Torch and lacks LangGraph/LightRAG.

- [ ] **Step 16: Run lock policy, generator, and source-reference gates**

Run:

```bash
.venv/bin/python -m pytest tests/test_dependency_locks.py -q
.venv/bin/python scripts/compile_requirements.py --check
.venv/bin/python -m pip check
cd ..
rg -n 'pip install|requirements(-docling|-parse-worker)?\.txt' \
  backend/Dockerfile .github/workflows deploy backend/scripts
git diff --check
```

Expected: tests and compile check pass; `pip check` is clean; the source scan shows only the documented compatibility-shim test and hash-locked install commands, with no deleted Docling file, unpinned install, index URL, or credential.

- [ ] **Step 17: Commit**

```bash
git add \
  backend/requirements \
  backend/requirements.txt \
  backend/requirements-parse-worker.txt \
  backend/Dockerfile \
  backend/scripts/compile_requirements.py \
  backend/app/release/generated_lock_digests.py \
  backend/tests/test_dependency_locks.py \
  .github/workflows/ci.yml \
  deploy
git add -u backend/requirements-docling.txt
git commit -m "build(deps): lock python 3.11 release environments"
```

---

### Task 5: Remove Global Module Stubs and Make Test Order Deterministic

**Files:**

- Create: `backend/tests/scoped_modules.py`
- Create: `backend/tests/test_test_order_isolation.py`
- Create: `backend/scripts/run_test_order_regression.py`
- Modify: `backend/tests/test_durable_run_streaming.py`
- Modify: `backend/tests/test_assistant_service_l1_summary.py`
- Modify: `backend/tests/test_assistant_service_no_outer_fallback.py`
- Verify unchanged semantics: `backend/tests/test_ai_runtime_legacy_cleanup.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**

- Consumes: Task 4's real hash-locked FastAPI/Starlette test environment, Plan 3's rewritten `test_ai_runtime_legacy_cleanup.py` tombstone/import-boundary test, pytest collection, repository bootstrap/cache helpers, and the complete backend suite.
- Produces: no persistent replacement of installed top-level packages; one strictly scoped helper for genuinely optional modules; forward/reverse/isolated/seeded-order regression commands; CI order evidence; and a full-suite invariant that the streaming test cannot poison later imports.

- [ ] **Step 1: Capture the existing pollution as a failing same-process regression**

Write a subprocess-free characterization that imports/executes the streaming setup and then checks the real framework identity:

```python
FRAMEWORK_MODULES = (
    "fastapi",
    "fastapi.exceptions",
    "fastapi.responses",
    "starlette.requests",
    "starlette.exceptions",
    "starlette.status",
)


def test_streaming_test_does_not_replace_installed_framework_modules():
    expected = {name: importlib.import_module(name) for name in FRAMEWORK_MODULES}
    run_pytest_file_in_current_process("tests/test_durable_run_streaming.py")
    assert {name: sys.modules[name] for name in FRAMEWORK_MODULES} == expected
    assert all(getattr(module, "__file__", None) for module in expected.values())
```

The helper must run selected tests via `pytest.main` in one interpreter so a leak is observable. It snapshots only identities/digests and does not print environment values.

- [ ] **Step 2: Run streaming then the Plan 3 tombstone and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_durable_run_streaming.py \
  tests/test_ai_runtime_legacy_cleanup.py -q
.venv/bin/python -m pytest tests/test_test_order_isolation.py -q
```

Expected before the fix: the ordered pair or explicit identity assertion fails because `test_durable_run_streaming.py` permanently inserts synthetic FastAPI/Starlette modules. Preserve the exact failure signature in the commit description, not a release Artifact.

- [ ] **Step 3: Delete `_install_fastapi_stubs()` from the streaming test**

Remove `sys`/`types` imports used only by the stub, remove all assignments to the six framework module keys, and remove the `setUp()` call. Import the real packages from Task 4 when application code requires them. Do not replace the leak with a module-level monkeypatch, reload, import hook, or condition based on whether FastAPI was imported first.

After editing, this source assertion must hold:

```python
source = Path("tests/test_durable_run_streaming.py").read_text()
assert "_install_fastapi_stubs" not in source
assert "sys.modules" not in source
assert "types.ModuleType" not in source
```

- [ ] **Step 4: Remove the same installed-framework stubs from adjacent tests**

Apply the same rule to `test_assistant_service_l1_summary.py` and `test_assistant_service_no_outer_fallback.py`, which currently duplicate the helper. Run their tests against real FastAPI/Starlette and repair mocks at the narrow service port they actually exercise rather than replacing a package.

Add an AST inventory over all `backend/tests/**/*.py` that fails if any file assigns one of the six framework keys in `sys.modules`. This closes recurrence beyond the three known files.

- [ ] **Step 5: Keep a scoped helper only for truly absent optional dependencies**

If another test must emulate a package not present in either lock, use `scoped_modules()` as a context manager/fixture. It records every affected dimension before installing the stub:

```python
@contextmanager
def scoped_modules(
    replacements: Mapping[str, ModuleType],
    *,
    app_module_roots: tuple[str, ...],
) -> Iterator[None]:
    module_snapshot = {name: sys.modules.get(name, _MISSING) for name in replacements}
    environment_snapshot = dict(os.environ)
    preexisting_app_modules = snapshot_modules(app_module_roots)
    cache_snapshot = snapshot_registered_test_caches()
    try:
        sys.modules.update(replacements)
        importlib.invalidate_caches()
        yield
    finally:
        restore_modules(module_snapshot)
        remove_app_modules_loaded_under_stub(app_module_roots, preexisting_app_modules)
        restore_environment_exactly(environment_snapshot)
        restore_registered_test_caches(cache_snapshot)
        importlib.invalidate_caches()
```

The helper rejects `fastapi`, `starlette`, SQLAlchemy, Pydantic, cryptography, and every other lock-owned package. Tests verify restoration both after normal exit and after an exception.

- [ ] **Step 6: Protect application modules imported under an optional stub**

An optional stub can contaminate already-imported application modules even after its own key is removed. Require callers to declare affected `app_module_roots`; record preexisting modules and remove only newly loaded modules or restore exact prior objects. Restore environment keys including newly added/deleted values, call repository `reset_caches()`, and verify no stub class remains reachable through an application module global.

Nested scopes either restore in LIFO order or raise `ScopedModulesError` for overlapping module keys; parallel use is prohibited by an in-process lock.

- [ ] **Step 7: Preserve the Plan 3 tombstone test, not its old Legacy behavior**

`test_ai_runtime_legacy_cleanup.py` after Plan 3 is an architectural tombstone proving removed Legacy packages cannot import and the archive cannot execute. Do not restore cleanup, migration CLI, IntentRouter, Supervisor, or Legacy table assertions. Add only a top-level comment explaining why it participates in order regression if needed; its substantive assertions remain the Plan 3 version.

The order test asserts the file contains the Plan 3 tombstone marker and no call to a Legacy cleanup routine before executing it.

- [ ] **Step 8: Implement four explicit order modes**

`run_test_order_regression.py` supports a closed enum:

```python
MODES = {
    "streaming-then-tombstone": (
        "tests/test_durable_run_streaming.py",
        "tests/test_ai_runtime_legacy_cleanup.py",
    ),
    "tombstone-then-streaming": (
        "tests/test_ai_runtime_legacy_cleanup.py",
        "tests/test_durable_run_streaming.py",
    ),
    "isolated": (),
    "seeded": (),
}
```

Ordered modes invoke one pytest process with files in the specified order. Isolated mode invokes a fresh process for each file. Seeded mode obtains the collected backend test-file list, sorts it, shuffles with an explicit integer seed using `random.Random(seed)`, and passes the resulting file order to one pytest process. Unknown mode/seed omission exits 2.

- [ ] **Step 9: Make seeded collection stable and observable**

The script hashes the ordered relative path list with domain `mindatlas:test-order:v1` and prints only seed, file count, order digest, pytest exit code, and duration. It does not record test data or environment. Collection error, duplicate path, absolute path, empty suite, or a path outside `backend/tests` fails before execution.

Use fixed release seeds `1701`, `2701`, and `3701`; these are regression samples, not proof that arbitrary test state leakage is acceptable.

- [ ] **Step 10: Add framework identity sentinels before and after each pair**

`test_test_order_isolation.py` records for the six modules:

- object identity;
- installed `__file__` under the clean venv;
- selected real symbols (`FastAPI`, `RequestValidationError`, `JSONResponse`, `Request`, `HTTPException`);
- absence of test-stub marker classes;
- unchanged relevant environment-key names and values, compared in memory only.

After each in-process nested pytest run, assert all sentinels and repository cache baselines are restored. Failure output names the module/cache key but redacts its value.

- [ ] **Step 11: Run forward, reverse, and isolated modes**

Run:

```bash
.venv/bin/python scripts/run_test_order_regression.py \
  --mode streaming-then-tombstone
.venv/bin/python scripts/run_test_order_regression.py \
  --mode tombstone-then-streaming
.venv/bin/python scripts/run_test_order_regression.py \
  --mode isolated
```

Expected: every command exits 0; ordered runs use one process; isolated runs use two; all report real FastAPI/Starlette module identities after completion.

- [ ] **Step 12: Run the fixed seeded permutations**

Run:

```bash
for seed in 1701 2701 3701; do
  .venv/bin/python scripts/run_test_order_regression.py \
    --mode seeded --seed "$seed"
done
```

Expected: all three complete with distinct order digests and zero exit codes. A failure is fixed at its source and rerun with the same seed; do not remove the file or choose a different seed.

- [ ] **Step 13: Run the targeted files and complete backend suite normally**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_test_order_isolation.py \
  tests/test_durable_run_streaming.py \
  tests/test_ai_runtime_legacy_cleanup.py \
  tests/test_assistant_service_l1_summary.py \
  tests/test_assistant_service_no_outer_fallback.py -q
.venv/bin/python -m pytest -q
```

Expected: targeted and full suites pass under Task 4's real packages. There is no dependency-related skip and no warning about a module lacking `__file__`/`__spec__`.

- [ ] **Step 14: Add the order regression to CI after lock installation**

Run forward/reverse/isolated and one fixed seeded mode in ordinary CI; reserve the remaining two seeded modes for Task 7's full release qualification job. CI must use the API/Worker lock and a single test process for the paired modes. Upload only JUnit result, seed, order digest, and durations on failure/success.

- [ ] **Step 15: Run source guards and formatting checks**

Run:

```bash
rg -n 'sys\.modules\[("|\x27)(fastapi|starlette)' backend/tests
rg -n '_install_fastapi_stubs|types\.ModuleType\(("|\x27)(fastapi|starlette)' \
  backend/tests
git diff --check
```

Expected: both source searches return no matches outside the negative assertions in `test_test_order_isolation.py`; `git diff --check` is clean.

- [ ] **Step 16: Commit**

```bash
git add \
  backend/tests/scoped_modules.py \
  backend/tests/test_test_order_isolation.py \
  backend/tests/test_durable_run_streaming.py \
  backend/tests/test_assistant_service_l1_summary.py \
  backend/tests/test_assistant_service_no_outer_fallback.py \
  backend/scripts/run_test_order_regression.py \
  .github/workflows/ci.yml
git commit -m "test(isolation): remove global framework stubs"
```

---

### Task 6: Build Signed Release Evidence, Trust, Scripted Scenarios, and Rehearsal Authorization

**Files:**

- Create: `backend/app/release/__init__.py`
- Create: `backend/app/release/contracts.py`
- Create: `backend/app/release/scenarios.py`
- Create: `backend/app/release/scripted_provider.py`
- Create: `backend/app/release/profile_authorization.py`
- Create: `backend/app/release/evidence.py`
- Create: `backend/app/release/trust.py`
- Create: `backend/app/release/runner.py`
- Create: `backend/release/scenarios/pre_ga_launch.v1.json`
- Create: `backend/scripts/run_pre_ga_release.py`
- Create: `backend/scripts/verify_release_attestation.py`
- Create: `backend/tests/test_release_evidence.py`
- Create: `backend/tests/test_release_scenarios.py`
- Create: `backend/tests/test_scripted_release_provider.py`
- Create: `backend/tests/test_rehearsal_profile_authorization.py`
- Create: `backend/tests/fixtures/release/signed-passing-evidence.v1.json`
- Create: `backend/tests/fixtures/release/signed-passing-artifacts.v1.tar`
- Create: `backend/tests/fixtures/release/public-trust-set.v1.json`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`

**Interfaces:**

- Consumes: Task 4's combined lock-set digest and Ed25519-capable `cryptography`, Plan 3 schema runtime identity/deployment class, Plan 2 runtime closure/Worker contract identities, Task 3's fault injection and create-entry outcomes, MinIO Artifact storage port, safe audit/metrics ports, and code-owned clocks in tests.
- Produces: a versioned deterministic scenario set; an OpenAI-compatible Scripted Provider; canonical allowlisted evidence manifests; content-addressed evidence objects; Ed25519 attestations/public trust-set verification; server-derived assertions only; offline verification; and a short-lived signed authorization usable only by an exact `rehearsal` profile.

- [ ] **Step 1: Write failing canonical-manifest and signature vectors**

Use a fixed RFC 8032 test private key only in test fixtures and assert byte-level vectors:

```python
def test_manifest_digest_and_attestation_are_canonical():
    manifest = release_manifest_fixture(assertions_in_reverse_order=True)
    canonical = canonical_release_manifest_bytes(manifest)
    assert canonical == GOLDEN_MANIFEST_BYTES
    assert manifest.manifest_digest == sha256_bytes(canonical)
    attestation = signer.sign(manifest)
    assert attestation.domain == "mindatlas:release-evidence:v1"
    assert verifier.verify(manifest, attestation).manifest_digest == manifest.manifest_digest
```

Test timezone normalization, unordered input rejection/sorting rules, lowercase 64-hex digests, duplicate assertion/Artifact IDs, floats/NaN, unknown fields, and self-digest exclusion. The initial run fails because release contracts do not exist.

- [ ] **Step 2: Define strict frozen evidence contracts**

All contracts use `extra="forbid"`, UTC timestamps, bounded safe strings, tuple collections, and explicit version/domain values. Implement `ReleaseQualificationTargetV1`, `QualificationInfrastructureIdentityV1`, `ReleaseEvidenceManifestV1`, `SignedReleaseAttestationV1`, and `RehearsalAttemptSubjectV1` exactly as listed in the stable interface—no reduced runner-local variants. The target is the complete non-secret durable production identity excluding evidence digests and volatile state; the infrastructure identity is evidence-only; each documented self-digest excludes itself. Add these remaining reference contracts:

```python
class ContentAddressedEvidenceRef(FrozenContract):
    schema_version: Literal[1] = 1
    evidence_kind: Literal["automated_qualification", "production_rehearsal"]
    manifest_digest: LowercaseSha256
    attestation_digest: LowercaseSha256


class ReleaseAssertionResultV1(FrozenContract):
    assertion_id: SafeAssertionId
    passed: bool
    safe_failure_code: SafeFailureCode | None
    observation_digest: LowercaseSha256
    artifact_digests: tuple[LowercaseSha256, ...]
    duration_ms: int = Field(ge=0)


class ReleaseArtifactRefV1(FrozenContract):
    artifact_id: SafeArtifactId
    artifact_kind: Literal[
        "junit",
        "migration_summary",
        "audit_summary",
        "metric_summary",
        "scenario_trace",
        "test_order_summary",
        "profile_topology_summary",
        "secret_scan_summary",
        "teardown_summary",
    ]
    media_type: Literal["application/json", "application/xml"]
    sha256_digest: LowercaseSha256
    byte_size: int = Field(ge=1, le=10_485_760)
```

Artifact IDs are unique within a manifest; refs are sorted by `(artifact_kind, artifact_id, sha256_digest)` before aggregate hashing. Every assertion's `artifact_digests` must resolve to a listed ref, and every listed ref must be reachable from at least one assertion. The evidence object store derives all Artifact/evidence keys from digests; clients cannot provide bucket, endpoint, credentials, filesystem path, arbitrary object key, or a `passed` value.

- [ ] **Step 3: Specify canonicalization and digest domains once**

Reuse the repository canonical JSON implementation after adding conformance tests for UTF-8, key order, integer representation, timezone `Z`, enum values, and absent-versus-null. Define exact domains:

```python
MANIFEST_DIGEST_DOMAIN = "mindatlas:release-manifest:v1"
ATTESTATION_DOMAIN = b"mindatlas:release-evidence:v1\x00"
ATTESTATION_OBJECT_DIGEST_DOMAIN = "mindatlas:release-attestation-object:v1"
EVIDENCE_OBJECT_DOMAIN = "mindatlas:release-evidence-object:v1"
ARTIFACT_AGGREGATE_DOMAIN = "mindatlas:release-artifact-aggregate:v1"
TRUST_SET_DOMAIN = "mindatlas:release-trust-set:v1"
REHEARSAL_AUTH_DOMAIN = b"mindatlas:rehearsal-authorization:v1\x00"
```

Manifest digest hashes a canonical envelope containing the digest domain and every manifest field except `manifest_digest`. Ed25519 signs `ATTESTATION_DOMAIN + bytes.fromhex(manifest_digest)`; it does not sign a reserialized client object. `attestation_digest` hashes the full canonical `SignedReleaseAttestationV1` under `ATTESTATION_OBJECT_DIGEST_DOMAIN`, so a ref binds the exact key ID/signature wrapper as well as the manifest.

- [ ] **Step 4: Define the fixed scenario-set schema and loader**

`pre_ga_launch.v1.json` contains scenario IDs, contract version, ordered steps, fault point, required services/Worker count, timeout, and expected safe assertion IDs. The closed step vocabulary includes setup/login/CSRF, rollout activation, Worker claim/lease, Interrupt, SSE, local create, recovery, reconciliation, Artifact/GC, L2, readiness, and launch control. It contains no password, token, Provider key, prompt, Entry body, or expected arbitrary response text.

`scenarios.py` validates duplicate IDs, unknown steps/fault points, timeout ceilings, dependency cycles, missing teardown, non-release-critical flags, and exact required assertion coverage. Its digest is:

```python
SCENARIO_SET_DIGEST = sha256_canonical_json(
    {
        "domain": "mindatlas:release-scenario-set:v1",
        "contractVersion": 1,
        "scenarios": canonical_scenarios,
    }
)
```

The loader also derives `REQUIRED_ASSERTION_SET_DIGEST` under domain
`mindatlas:release-required-assertions:v1` from the sorted unique union of every
release-critical expected assertion ID plus the fixed topology, no-skip, secret-scan,
and teardown assertion IDs. A scenario-set digest and required-assertion digest are
both required because a loader/evaluator bug must not silently drop an assertion
while preserving scenario text.

- [ ] **Step 5: Write scenario mutation and coverage tests**

For every required scenario group, delete or alter it in memory and assert loader failure. Require PostgreSQL, MinIO, API, frontend artifact, two unique compatible Assistant Workers, and Scripted Provider on every release-critical run. Test that `skip`, `xfail`, paid Provider, environment-dependent assertion, or client-supplied outcome fields are rejected by schema/loader.

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_release_scenarios.py -q
```

Expected after defining only contracts: failures enumerate missing runner/step implementations, while schema mutation cases pass.

- [ ] **Step 6: Implement the deterministic OpenAI-compatible Scripted Provider**

The server supports only the endpoints/fields exercised by the frozen adapter. Each scenario maps a request-shape digest and ordinal to a code-owned response/fault; it never chooses based on prompt text:

```python
@dataclass(frozen=True)
class ScriptedProviderStep:
    scenario_id: str
    request_ordinal: int
    expected_tool_names: tuple[str, ...]
    response_kind: Literal["tool_call", "content", "transport_fault"]
    tool_name: Literal["create_entry"] | None
    fault_code: SafeProviderFault | None
```

The Provider validates exactly one write tool (`create_entry`), records request/tool-schema digests and timing only, and can reproduce connection drop, duplicate delivery, delayed response, and stream disconnect. It never persists Authorization headers, request bodies, prompts, tool arguments, Entry content, or raw tool-call IDs.

- [ ] **Step 7: Prove Scripted Provider determinism and safety**

Run each script twice with different request content but the same allowed structural shape and prove the same safe response schedule and evidence digests. Reject unknown scenario, ordinal reuse, extra tool declarations, unsupported update/relation name, malformed stream, or attempts to request a paid/live endpoint.

Add log capture assertions that secrets and sentinel prompt/Entry strings are absent from response, logs, metrics, and collected Artifacts.

- [ ] **Step 8: Implement an allowlisted evidence collector**

The runner emits typed `ReleaseObservation` values, and server-owned assertion evaluators derive `passed`, `safe_failure_code`, and observation digest. The collector permits only:

- qualification-target, build/application-image/deployed Artifact/schema/auth/dependency-lock/scenario/required-assertion/runner/trust/runtime/rollout/Profile/Model/Package/Capability identities;
- typed evidence-only Scripted Provider/PostgreSQL/MinIO/compiler `QualificationInfrastructureIdentityV1` fields and its self-excluding digest;
- safe service/Worker instance identities generated for the release run;
- assertion IDs, boolean results, safe failure codes, counts, revisions, durations, and UTC bounds;
- content-addressed refs/digests to allowlisted JUnit, migration, audit-summary, metric-summary, and scenario-trace Artifacts.

It rejects arbitrary dictionaries and scans serialized values/keys for password/token/key/authorization/database URL/raw idempotency/prompt/content markers. A failing assertion still creates signed evidence with `passed=false`; it cannot be omitted.

- [ ] **Step 9: Store immutable content-addressed evidence Artifacts and object**

Write every allowlisted safe Artifact first, with conditional create, to a key derived solely from its SHA-256 digest:

```text
release-evidence-artifacts/v1/{artifact_digest[0:2]}/{artifact_digest}
```

Read each object back and verify exact bytes/type/size/digest. Then build one canonical object containing manifest plus attestation and write it last as the commit marker to:

```text
release-evidence/v1/{evidence_kind}/{manifest_digest[0:2]}/{manifest_digest}.json
```

The configured bucket is server-owned and absent from the client ref. On an existing key, byte-compare and return idempotently; different bytes at the same digest are `release_evidence_collision` and stop. A failed partial attempt may leave unreferenced content-addressed Artifacts but never a manifest that references a missing object; GC may remove only artifacts unreachable from every committed manifest. Verify read-after-write digest and MinIO object version/ETag as transport metadata, but do not treat ETag as evidence identity.

- [ ] **Step 10: Implement Ed25519 signer and public trust-set verifier**

The signing constructor accepts private-key bytes from an already-open runner secret descriptor; it never reads a normal app environment variable or serializes the key. The API verifier loads a public trust-set file:

```python
class ReleaseEvidenceTrustKeyV1(FrozenContract):
    key_id: SafeKeyId
    public_key_base64url: str
    allowed_domains: tuple[
        Literal[
            "release_evidence",
            "deployed_artifact_identity",
            "rehearsal_authorization",
        ],
        ...,
    ]
    allowed_evidence_kinds: tuple[EvidenceKind, ...]
    not_before: datetime
    not_after: datetime
    revoked: bool = False
```

Trust-set digest binds sorted public key bytes, IDs, allowed domains/kinds, validity bounds, revocation state, and contract version. A `release_evidence` key requires at least one allowed evidence kind; a key without that domain must have an empty kind tuple. At verification time require known non-revoked key, exact signature domain, allowed kind when applicable, evidence `ended_at` within key validity, valid signature, content digest, Artifact aggregate digest, and exact schema/build/lock/scenario identities. Later wall-clock passage alone does not change an already built launch subject; changing/revoking the configured trust set changes its digest and invalidates launch.

- [ ] **Step 11: Add adversarial trust and evidence tests**

Cover unsigned, bit-flipped, wrong domain, unknown key, revoked key, wrong kind, before/after key validity, malformed public/private key, duplicate key ID, manifest self-digest mismatch, object-key mismatch, Artifact omission/reorder, wrong aggregate digest, schema/build/image/lock/scenario mismatch, noncanonical JSON, and stale object replacement. Every failure returns one stable safe code and never includes signature bytes or evidence body.

Use two fixed test keypairs to exercise rotation; neither private fixture is accepted outside tests.

- [ ] **Step 12: Define the short-lived rehearsal authorization contract**

```python
class RehearsalProfileAuthorizationV1(FrozenContract):
    schema_version: Literal[1] = 1
    authorization_id: UUID
    profile_run_id: UUID
    deployment_class: Literal["rehearsal"]
    qualification_target_digest: LowercaseSha256
    initialization_fixture_digest: LowercaseSha256
    build_revision: str
    image_set_digest: LowercaseSha256
    deployed_artifact_set_digest: LowercaseSha256
    schema_runtime_identity_digest: LowercaseSha256
    schema_contract_material_digest: LowercaseSha256
    dependency_lock_set_digest: LowercaseSha256
    scenario_set_digest: LowercaseSha256
    required_assertion_set_digest: LowercaseSha256
    runner_contract_version: int
    runner_identity_digest: LowercaseSha256
    evidence_trust_set_digest: LowercaseSha256
    issued_at: datetime
    expires_at: datetime
    nonce_digest: LowercaseSha256
    authorization_digest: LowercaseSha256


class SignedRehearsalProfileAuthorizationV1(FrozenContract):
    schema_version: Literal[1] = 1
    domain: Literal["mindatlas:rehearsal-authorization:v1"]
    key_id: SafeKeyId
    authorization: RehearsalProfileAuthorizationV1
    signature_base64url: str
```

`authorization_digest` hashes every prior authorization field under domain `mindatlas:rehearsal-authorization-claims:v1` and excludes itself. The runner signs `REHEARSAL_AUTH_DOMAIN + bytes.fromhex(authorization_digest)`; the envelope domain and key permission are checked independently. Maximum lifetime is 120 minutes. The authorization envelope is mounted read-only into API and Workers by Task 9; it is not accepted through HTTP, Provider input, ordinary environment text, database row, or runtime flag.

- [ ] **Step 13: Fail closed on deployment-class/profile mismatch**

Authorization validation is deliberately two-phase because a real rehearsal starts uninitialized. At startup, `profile_authorization.py` verifies the signature/envelope, captured qualification-target digest, server-derived current build/application image/deployed-Artifact set, schema runtime identity and comparable contract material, dependency lock, scenario, required-assertion, runner, and trust identities, database deployment class/time, exact profile-run identifier, and the signed initialization-fixture identity. It does not require rollout rows that must be created by the Setup scenario. After initialization/activation and before the first rehearsal Run or write, `allows_current_subject()` rebuilds the full target/closure from database state and requires exact equality with `qualification_target_digest`; it repeats this dynamic check before every release-only new Run/write. Rules:

- `development`: authorization path must be absent; release runner cannot manufacture launch evidence;
- `rehearsal`: valid exact unexpired authorization is required for release-only Run/write access;
- `production`: any configured/mounted rehearsal authorization is a configuration error, and an authorization can never satisfy production launch evaluation or create a gate use.

Expiry stops new rehearsal operations; it does not rewrite evidence already finalized before expiry.

- [ ] **Step 14: Prove authorization cannot become production launch authority**

Test file copying, initialization-fixture byte/digest drift, signature replay against another database, build/schema/lock/scenario/required-assertion/runner/trust drift, expired/future authorization, production deployment class, wrong domain/key, profile-run mismatch, pre-initialization write, post-initialization target-closure mismatch, and API request body injection. Assert no `pre_ga_launch_gate_use` row can be created through the authorization adapter and no production write guard port treats it as launched.

- [ ] **Step 15: Implement the fixed runner command surface**

At this Task's boundary, `run_pre_ga_release.py evidence run` accepts only:

| Argument | Accepted value |
|---|---|
| `--kind` | exactly `automated_qualification` or `production_rehearsal` |
| `--profile-url` | server-owned release profile endpoint URL |
| `--output-dir` | path to a new empty local directory |
| `--signing-key-fd` | decimal number of an already-open descriptor |
| `--trust-set` | path to the public trust-set file |

It does not accept assertion outcomes, pass status, scenario selection/removal, deployment-class override, database URL, service skip, expected digests, or raw credentials. Credentials are mounted/injected into the profile and runner obtains safe session material through the test setup contract without printing it. `production_rehearsal` additionally requires a valid mounted rehearsal authorization. Task 9 extends the same CLI with a closed set of profile/artifact/target subcommands; it does not weaken these input prohibitions.

- [ ] **Step 16: Implement offline attestation verification**

The verification script accepts an evidence object file, its deterministic sealed Artifact bundle, and public trust-set file; rejects path traversal, duplicate names/digests, unsafe types, extra/missing objects, and noncanonical archive metadata; reconstructs canonical bytes; verifies every Artifact/aggregate/digest/signature/allowlist rule; and prints a stable JSON summary containing only evidence kind, run ID, manifest digest, key ID, assertion pass/fail counts, build/schema/locks/scenario digests, and UTC interval. It exits 0 only if cryptographic/integrity verification succeeds; assertion failure remains visible and returns a separate nonzero code.

- [ ] **Step 17: Run contract/trust/provider/authorization unit suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_release_evidence.py \
  tests/test_release_scenarios.py \
  tests/test_scripted_release_provider.py \
  tests/test_rehearsal_profile_authorization.py -q
.venv/bin/python scripts/verify_release_attestation.py \
  --evidence tests/fixtures/release/signed-passing-evidence.v1.json \
  --artifact-bundle tests/fixtures/release/signed-passing-artifacts.v1.tar \
  --trust-set tests/fixtures/release/public-trust-set.v1.json
cd ..
git diff --check
```

Expected: all tests pass; verifier prints the fixed safe golden summary; all adversarial vectors fail with their expected codes; private key/sentinel secret scans find no output. If fixture files are used, create them deterministically in the same commit under `backend/tests/fixtures/release/` and list their digests in the unit test.

- [ ] **Step 18: Commit**

```bash
git add \
  backend/app/release \
  backend/release/scenarios/pre_ga_launch.v1.json \
  backend/scripts/run_pre_ga_release.py \
  backend/scripts/verify_release_attestation.py \
  backend/tests/test_release_evidence.py \
  backend/tests/test_release_scenarios.py \
  backend/tests/test_scripted_release_provider.py \
  backend/tests/test_rehearsal_profile_authorization.py \
  backend/tests/fixtures/release \
  backend/app/config.py \
  backend/.env.example
git commit -m "feat(release): sign qualification evidence"
```

---

### Task 7: Add `pre_ga_v1_0002` Launch Persistence and Advance Clean Schema Identity

**Files:**

- Create: `backend/app/pre_ga_launch/__init__.py`
- Create: `backend/app/pre_ga_launch/contracts.py`
- Create: `backend/app/pre_ga_launch/models.py`
- Create: `backend/alembic/versions/pre_ga_v1_0002_create_entry_launch.py`
- Create: `backend/app/schema/manifests/pre_ga_v1_0002-expected.json`
- Create: `backend/scripts/generate_pre_ga_v1_0002_identity.py`
- Create: `backend/tests/test_pre_ga_launch_models.py`
- Create: `backend/tests/test_plan4_migration_postgres.py`
- Modify: `backend/app/schema/contracts.py`
- Modify: `backend/app/schema/compatibility.py`
- Modify: `backend/app/schema/identity.py`
- Modify: `backend/app/assistant/runtime/models.py`
- Modify: `backend/app/assistant/models.py`
- Modify: `backend/alembic/env.py`
- Modify: `backend/tests/_db.py`
- Modify: `backend/tests/test_runtime_schema_compatibility.py`

**Interfaces:**

- Consumes: Plan 3's sole root `pre_ga_v1_0001`, PostgreSQL catalog canonicalizer, guarded `mindatlas_schema_identity`, deployment class, generated manifest format and `RuntimeSchemaCompatibility` port; Task 2's four frozen write fields; Task 6's evidence identity contracts; and Plan 1 Operator account identity.
- Produces: sole live head `pre_ga_v1_0002`; immutable `PreGaLaunchCandidate`, append-only `PreGaLaunchGateUse`, revisioned singleton `PreGaLaunchControl`; PostgreSQL constraints/triggers/CAS foundations; write-field persistence/immutability; generated `0002` structural/runtime identity; exact-family API/Worker compatibility; and test-only empty downgrade to `0001` with no Legacy edge.

- [ ] **Step 1: Assert the migration graph/root immutability before editing**

Run and record only digests/revisions:

```bash
cd backend
.venv/bin/alembic roots
.venv/bin/alembic heads
sha256sum alembic/versions/pre_ga_v1_0001_clean_baseline.py
.venv/bin/python scripts/archive_pre_ga_lineage.py --check
```

Expected: root/head both `pre_ga_v1_0001`; archive has exactly 60 verified files; root file digest is recorded in the new migration test. Stop if another live revision exists or the root differs from the reviewed Plan 3 implementation.

- [ ] **Step 2: Write failing ORM and migration-shape tests**

```python
def test_plan4_revision_is_additive_and_root_is_unchanged(alembic_script):
    rev = alembic_script.get_revision("pre_ga_v1_0002")
    assert rev.down_revision == "pre_ga_v1_0001"
    assert rev.branch_labels is None
    assert sha256_file(ROOT_REVISION) == REVIEWED_ROOT_SHA256


def test_launch_models_have_exact_table_names():
    assert PreGaLaunchCandidate.__tablename__ == "pre_ga_launch_candidate"
    assert PreGaLaunchGateUse.__tablename__ == "pre_ga_launch_gate_use"
    assert PreGaLaunchControl.__tablename__ == "pre_ga_launch_control"
```

Also assert there is one Alembic root and one head, no old revision import, no archive path in `version_locations`, and no modification to `pre_ga_v1_0001`.

- [ ] **Step 3: Freeze candidate columns and typed model contract**

`pre_ga_launch_candidate` has these exact identity/decision columns:

| Column group | Columns |
|---|---|
| Identity/request | `id`, `candidate_kind`, `creation_request_id`, `creation_request_digest`, `created_by_operator_id`, `created_by_session_id`, `reason` |
| Durable target/subject | `qualification_target_json`, `qualification_target_digest`, `subject_json`, `subject_digest`, `build_revision`, `image_set_digest`, `deployed_artifact_set_digest`, `schema_family`, `schema_revision`, `schema_runtime_identity_digest`, `deployment_class`, `operator_auth_contract_version`, `rollout_revision_id`, `rollout_revision_digest`, `runtime_closure_digest`, `profile_version_id`, `profile_content_digest`, `model_id`, `model_identity_digest`, `package_closure_digest`, `capability_closure_digest`, `seed_manifest_digest`, `worker_runtime_contract_version`, `worker_checkpoint_codec_version`, `worker_capability_feature_digest`, `create_entry_contract_digest`, `write_policy_digest`, `write_cohort_digest`, `reconciliation_contract_version`, `dependency_lock_set_digest`, `scenario_set_digest`, `required_assertion_set_digest`, `runner_contract_version`, `runner_identity_digest`, `evidence_trust_set_digest` |
| Evidence | `automated_evidence_ref_json`, `automated_evidence_manifest_digest`, `automated_attestation_digest`, `rehearsal_evidence_ref_json`, `rehearsal_evidence_manifest_digest`, `rehearsal_attestation_digest` |
| Decision/snapshot | `operational_snapshot_json`, `operational_snapshot_digest`, `unknown_call_count`, `needs_reconciliation_count`, `active_run_count`, `passed`, `safe_failure_codes` |
| Database time | `observed_at`, `issued_at`, `expires_at` |

Use PostgreSQL `JSONB`, UUIDs, timezone timestamps, bounded strings, nonnegative version/count checks, and lowercase SHA-256 checks. `candidate_kind` is exactly `pre_ga_launch`; schema family/revision/deployment class are exactly `pre_ga_v1`/`pre_ga_v1_0002`/`production`. `qualification_target_json` must parse as the exact stable target contract and hash to its mirror. Every denormalized subject column must equal the corresponding canonical `subject_json` value in repository construction and round-trip tests; PostgreSQL treats the immutable JSON plus constrained mirrors as one record, never as competing sources of truth.

- [ ] **Step 4: Freeze gate-use and singleton-control columns**

`pre_ga_launch_gate_use` contains `id`, `candidate_id`, `subject_digest`, `operator_id`, `session_id`, `consumption_request_id`, `consumption_request_digest`, bounded `reason`, `expected_control_revision`, `resulting_control_revision`, and database `used_at`.

`pre_ga_launch_control` contains one row:

```python
singleton_key: Literal["pre_ga_launch"]
active_subject_digest: str | None
active_candidate_id: UUID | None
active_gate_use_id: UUID | None
revision: int
launched_at: datetime | None
updated_at: datetime
```

At revision 0 all active/launched fields are null. At revision greater than 0 all are non-null. There is no enabled boolean, expiry column, Worker identity, unresolved count, active-Run count, or runtime flag in control.

- [ ] **Step 5: Add relational and digest constraints before repositories exist**

Require:

- unique candidate `creation_request_id` and lowercase `creation_request_digest`;
- unique gate-use `consumption_request_id` and lowercase digest;
- candidate/gate-use Operator IDs reference `operator_account.id ON DELETE RESTRICT`; their Session UUIDs are retained immutable values without a foreign key so later Session pruning cannot erase launch history;
- candidate unique `(id, subject_digest)`;
- gate-use composite FK `(candidate_id, subject_digest)` to candidate;
- gate-use unique `(id, candidate_id, subject_digest, resulting_control_revision)`;
- control composite FK `(active_gate_use_id, active_candidate_id, active_subject_digest, revision)` to that gate-use tuple using `MATCH SIMPLE`; revision-0 null active fields bypass it, while every active control must point to the use that produced exactly its revision;
- control singleton key check and one-row primary key;
- `resulting_control_revision = expected_control_revision + 1`;
- candidate `expires_at = issued_at + INTERVAL '24 hours'`;
- `passed` implies all three counts zero and `safe_failure_codes` is an empty JSON array; `passed=false` requires a nonempty JSON array whose uniqueness/order/allowlist is revalidated on ORM load;
- every digest column matches `^[0-9a-f]{64}$`;
- reason length 1–500 and runner contract/count/revisions nonnegative.

Use named constraints and assert their exact names in PostgreSQL introspection tests.

- [ ] **Step 6: Write PostgreSQL mutation-rejection tests first**

Insert a failed candidate and verify direct UPDATE/DELETE both raise. Insert a valid gate use fixture and verify UPDATE/DELETE raise. For control, verify direct changes that keep revision unchanged or jump by more than one raise; a single valid `0 -> 1` transition with consistent candidate/use succeeds. Roll back after every expected database error so the remainder runs in a clean transaction.

- [ ] **Step 7: Add immutable/append-only/CAS trigger functions**

Create three dedicated functions/triggers:

```sql
CREATE FUNCTION mindatlas_reject_pre_ga_launch_candidate_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'pre-GA launch candidate is immutable';
END;
$$;

CREATE FUNCTION mindatlas_reject_pre_ga_launch_gate_use_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'pre-GA launch gate use is append-only';
END;
$$;

CREATE FUNCTION mindatlas_guard_pre_ga_launch_control_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.singleton_key <> OLD.singleton_key OR NEW.revision <> OLD.revision + 1 THEN
    RAISE EXCEPTION 'pre-GA launch control revision invalid';
  END IF;
  RETURN NEW;
END;
$$;
```

Candidate and use triggers reject UPDATE OR DELETE. Control rejects DELETE and validates every UPDATE. Application CAS still uses `WHERE revision=:expected`; the trigger is a second database boundary.

- [ ] **Step 8: Add Task 2 write fields and immutability to rollout/Run rows**

Add non-null fields to both immutable rollout revision and Run-frozen identity:

```text
required_create_entry_contract_digest varchar(64)
required_write_policy_digest varchar(64)
required_write_cohort_digest varchar(64)
required_reconciliation_contract_version integer
```

The fresh-only migration precondition means no row backfill/default is allowed. Extend rollout immutability and Run identity triggers so direct UPDATE of any field is rejected while legitimate Run state-machine fields remain mutable. Tests insert complete rows and try each field independently.

- [ ] **Step 9: Reject initialized or nonempty `0001` databases**

At the top of `upgrade()`, verify current schema identity is exact healthy `pre_ga_v1_0001`, then count all application/business/runtime tables. Allow only the Alembic version row and one schema identity marker. If Operator, initialization, Entry, rollout, Run, call, Interrupt, Artifact, audit, or any other application row exists, raise `pre_ga_reset_required` before DDL.

This is a deliberate pre-GA reset boundary: Tool/seed/runtime closure changed. The migration does not synthesize new rollout digests or reinterpret old data. Test one representative row in every table group plus a race where a second Session inserts after preflight; take PostgreSQL advisory and table locks before counting so the DDL cannot race a writer.

- [ ] **Step 10: Render a self-contained additive revision**

The revision header is exact:

```python
revision = "pre_ga_v1_0002"
down_revision = "pre_ga_v1_0001"
branch_labels = None
depends_on = None
```

It imports Alembic/SQLAlchemy/Python standard library only, contains literal generated expected fingerprints/contracts, creates tables/functions/triggers/indexes in dependency order, inserts the control singleton at revision 0, and advances the marker in the same transaction. It never imports `app.*`, reads caller-provided digest values, stamps, or touches archive/root files.

- [ ] **Step 11: Implement the two-pass identity generator**

`generate_pre_ga_v1_0002_identity.py --write` performs:

1. assert reviewed root digest and exact one-root/one-head input state;
2. create fresh PostgreSQL Database A with requested deployment class;
3. upgrade A to `0001`;
4. apply the code-owned `0002` DDL stage without marker advancement;
5. canonicalize application structure and marker-control definitions;
6. read current seed/runtime/codec `3`/feature/auth/write contract constants;
7. render final revision literals and `pre_ga_v1_0002-expected.json` atomically;
8. create fresh Database B, run real Alembic `0001 -> 0002`, and recompute every value;
9. byte-compare a second render.

`--check` repeats the two-database verification without replacing committed files. Generated JSON contains application fingerprint, marker-control fingerprint, seed/runtime/codec/feature/auth versions/digests, and expected runtime identity variants for all three deployment classes.

- [ ] **Step 12: Advance the protected schema marker atomically**

Immediately before one marker update execute:

```sql
SET LOCAL mindatlas.schema_migration_revision = 'pre_ga_v1_0002';
```

Update `schema_revision`, application fingerprint, expected seed contract digest, runtime contract version, checkpoint codec `3`, Capability feature digest, Operator auth contract version, and runtime identity digest. Preserve `schema_family='pre_ga_v1'`, immutable `deployment_class`, and marker singleton ID. Recompute runtime identity from the complete canonical material; do not accept any value from environment except the root's already-persisted deployment class.

After update, clear/reset the transaction-local setting and assert exactly one marker row changed. Any mismatch rolls back DDL, control seed, and marker together.

- [ ] **Step 13: Make runtime compatibility exact to `0002` for the Plan 4 binary**

Update code-owned requirements so known/minimum/current ordinal is 2 and `pre_ga_v1_0002` maps to the generated application/marker fingerprints and contracts. `pre_ga_v1_0001` remains loadable only by Plan 3 test tooling; the Plan 4 API and Worker return `schema_revision_incompatible` at runtime on `0001`.

Test wrong family/revision/fingerprint/control definition/seed/codec/feature/auth/deployment class/runtime identity independently for API and Worker. `/health` remains process-only; `/ready` and Worker claim fail closed.

- [ ] **Step 14: Implement test-only empty downgrade to `0001`**

`downgrade()` requires `APP_ENV=test` and exact `MINDATLAS_PRE_GA_0002_TEST_DOWNGRADE_ACK=I_ACKNOWLEDGE_EMPTY_PRE_GA_0002_DOWNGRADE`. It refuses if any candidate/use/control revision above 0, rollout, Run, call, Entry, Operator, audit, or other application row exists. Then it drops launch triggers/functions/tables, removes four columns/trigger clauses, uses the same transaction-local marker guard to restore the generated `0001` identity, and returns Alembic to `pre_ga_v1_0001`.

This is test cleanup, not operational rollback. It never recreates Legacy tables, reconnects the 60-revision archive, edits the root, or accepts a production/rehearsal database.

- [ ] **Step 15: Generate final revision/manifest and run byte check**

Run:

```bash
MINDATLAS_SCHEMA_GENERATOR_POSTGRES_URL="$MINDATLAS_SCHEMA_GENERATOR_POSTGRES_URL" \
  .venv/bin/python scripts/generate_pre_ga_v1_0002_identity.py --write
MINDATLAS_SCHEMA_GENERATOR_POSTGRES_URL="$MINDATLAS_SCHEMA_GENERATOR_POSTGRES_URL" \
  .venv/bin/python scripts/generate_pre_ga_v1_0002_identity.py --check
.venv/bin/alembic roots
.venv/bin/alembic heads
```

Expected: generator reports byte-identical second render; root remains `pre_ga_v1_0001`; sole head is `pre_ga_v1_0002`; manifest/revision literals match fresh PostgreSQL introspection.

- [ ] **Step 16: Run fresh upgrade, refusal, trigger, identity, and downgrade tests**

Run:

```bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
    tests/test_pre_ga_launch_models.py \
    tests/test_plan4_migration_postgres.py \
    tests/test_schema_baseline_migration_postgres.py \
    tests/test_runtime_schema_compatibility.py -q
.venv/bin/python scripts/archive_pre_ga_lineage.py --check
cd ..
git diff --check
```

Expected: no PostgreSQL skip; fresh `0001 -> 0002` succeeds for development/rehearsal/production markers; any nonempty state refuses before DDL; all mutation/CAS triggers fire; exact test-only empty downgrade/re-upgrade succeeds; archive/root digests stay unchanged.

- [ ] **Step 17: Commit**

```bash
git add \
  backend/app/pre_ga_launch \
  backend/alembic/versions/pre_ga_v1_0002_create_entry_launch.py \
  backend/app/schema \
  backend/app/assistant/runtime/models.py \
  backend/app/assistant/models.py \
  backend/alembic/env.py \
  backend/scripts/generate_pre_ga_v1_0002_identity.py \
  backend/tests/_db.py \
  backend/tests/test_pre_ga_launch_models.py \
  backend/tests/test_plan4_migration_postgres.py \
  backend/tests/test_runtime_schema_compatibility.py
git commit -m "feat(schema): add pre-GA launch state"
```

---

### Task 8: Derive and Consume Launch Candidates, Then Enforce Durable Launch Control

**Files:**

- Create: `backend/app/pre_ga_launch/repository.py`
- Create: `backend/app/pre_ga_launch/qualification_target.py`
- Create: `backend/app/pre_ga_launch/subject.py`
- Create: `backend/app/pre_ga_launch/service.py`
- Create: `backend/app/pre_ga_launch/router.py`
- Create: `backend/tests/test_pre_ga_launch_service.py`
- Create: `backend/tests/test_pre_ga_launch_postgres.py`
- Create: `backend/tests/test_pre_ga_launch_api.py`
- Modify: `backend/app/pre_ga_launch/__init__.py`
- Modify: `backend/app/pre_ga_launch/contracts.py`
- Modify: `backend/app/assistant/runtime/contracts.py`
- Modify: `backend/app/assistant/runtime/readiness.py`
- Modify: `backend/app/assistant/runtime/admission.py`
- Modify: `backend/app/assistant/runtime/router.py`
- Modify: `backend/app/assistant/capability_calls/write_guard.py`
- Modify: `backend/app/operator_auth/route_policy.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Modify: `backend/tests/test_assistant_runtime_readiness.py`
- Modify: `backend/tests/test_assistant_atomic_admission.py`
- Modify: `backend/tests/test_route_auth_inventory.py`

**Interfaces:**

- Consumes: Task 7 launch tables/constraints and exact `0002` schema identity; Task 6 evidence/trust/content-addressed objects; Plan 1 viewer/Operator/CSRF/audit; Plan 2 active rollout/closure/readiness/atomic admission; Task 2 write guard; database time; Task 4 lock digest; and a server-owned deployed Artifact identity provider.
- Produces: canonical durable launch subjects, separate operational snapshots, immutable server-derived candidates, idempotent Operator consumption under advisory/row locks, durable launch evaluation, production readiness/admission/write enforcement, exact expiry semantics, and safe viewer/Operator HTTP contracts with no client-controlled decision fields.

- [ ] **Step 1: Write fixed subject and snapshot digest vectors**

```python
def test_launch_subject_excludes_volatile_state_and_self_digest():
    subject = build_subject_fixture()
    payload = subject.digest_payload()
    assert "subjectDigest" not in payload
    assert "workerHeartbeat" not in payload
    assert "activeRunCount" not in payload
    assert "unknownCapabilityCallCount" not in payload
    assert "needsReconciliationCount" not in payload
    assert "candidateExpiresAt" not in payload
    assert subject.qualification_target_digest == qualification_target_fixture().qualification_target_digest
    assert subject.subject_digest == EXPECTED_SUBJECT_DIGEST


def test_operational_snapshot_is_separate_and_complete():
    snapshot = LaunchOperationalSnapshotV1.from_counts(unknown=0, reconciliation=0, active=0)
    assert snapshot.snapshot_digest == EXPECTED_SNAPSHOT_DIGEST
```

Use static UUIDs/digests/timestamps for golden vectors. Mutate every subject field one at a time and assert digest change; mutate liveness/count/current-time fixture inputs and assert subject stability.

- [ ] **Step 2: Define one server-owned deployed Artifact identity port**

`DeploymentArtifactIdentityProvider.current()` returns build revision, API/Worker/Web image digests, and a combined deployed Artifact-set digest from immutable image labels plus a read-only signed deployment manifest. It rejects tags, missing service identities, mismatch with `APP_BUILD_REVISION`, and client/environment digest overrides.

The provider may read configured manifest path, but production startup requires that path to be an absolute read-only regular file owned by the deployment boundary. Request handlers cannot select another file. Task 9 generates and mounts the manifest; tests inject a frozen port directly.

- [ ] **Step 3: Build `ReleaseQualificationTargetV1` and launch subject only from authoritative ports**

In one database Session load:

- deployed Artifact identity;
- exact healthy production `pre_ga_v1_0002` schema identity, including family/revision/application and marker-control fingerprints/identity-contract version/deployment class, schema seed/runtime/codec/feature material, runtime digest, and comparable-material digest;
- `OPERATOR_AUTH_CONTRACT_VERSION`;
- active rollout row and recomputed `AssistantRuntimeClosure`;
- Profile/Model/Package/Capability/seed identities from that closure;
- Worker runtime/codec `3`/feature contract constants, not a heartbeat row;
- create/write/cohort/reconciliation constants from Task 2;
- generated combined dependency lock digest;
- code-owned scenario set, required-assertion set, runner contract/identity, and configured public trust-set digest.

First canonicalize these evidence-independent fields as `ReleaseQualificationTargetV1`, compute its self-excluding digest, and expose a safe viewer projection. Then construct `PreGaLaunchSubjectV1` from the exact target fields/digest plus the selected verified automated/rehearsal evidence manifest digests; specifically, subject `schema_runtime_identity_digest` is the target's `production_schema_runtime_identity_digest`, never either rehearsal digest. Reject missing active rollout, closure drift, wrong deployment class, invalid schema, non-lowercase digest, or deployed Artifact mismatch. No caller can submit/override a target or subject field.

- [ ] **Step 4: Make rehearsal-to-production schema comparison explicit**

Both qualifying evidence runs use isolated `rehearsal` databases, so their deployment-bound runtime identity differs from the target production marker by design. Candidate derivation requires exact equality of family, revision `pre_ga_v1_0002`, application fingerprint, marker-control fingerprint, identity-contract version, and `schema_contract_material_digest`; requires each evidence manifest to state `schema_deployment_class="rehearsal"`; recomputes the Plan 3 runtime identity from the explicit `schema_seed_contract_digest`, schema runtime-contract, schema codec, schema Capability-feature, and Operator-auth fields; requires those schema contract fields to equal their corresponding target/runtime-closure constants; requires the two rehearsal runtime digests to equal each other; then binds the independently verified current production runtime identity into the launch subject. Assert in contract tests that the manifest-to-`SchemaRuntimeIdentityMaterial` mapping is total and one-to-one. The only permitted rehearsal-versus-production runtime-material difference is immutable deployment class.

Never compare only revision strings, and never copy a rehearsal runtime identity into the production subject.

- [ ] **Step 5: Write the evidence decision matrix before candidate creation**

Classify input in two layers:

| Condition | Persistence/result |
|---|---|
| Missing object, object digest mismatch, malformed manifest, unsigned/invalid/unknown/revoked attestation | 422 `launch_evidence_invalid`; no candidate |
| Valid signed evidence with a required assertion failed | immutable candidate with `passed=false`, `qualification_assertion_failed` |
| Valid evidence whose `qualification_target_digest` or build/image/schema-comparable/auth/lock/scenario/required-assertion/runner/trust identities differ from current | immutable candidate with `passed=false`, exact safe mismatch codes |
| Individually valid target-matching evidence whose evidence-only `QualificationInfrastructureIdentityV1` values differ from each other | immutable candidate with `passed=false`, `qualification_infrastructure_mismatch` |
| Valid matching evidence but one operational count is nonzero | immutable candidate with `passed=false`, count-specific safe codes |
| Both evidence objects valid/passing/matching and all counts zero | immutable candidate with `passed=true`, no failure codes |

Failure code ordering is code-owned and deterministic. Never persist exception text, Artifact bodies, signatures, prompts, or secret values.

- [ ] **Step 6: Implement repository database-time, request replay, and locks**

Define constants for a 64-bit launch advisory lock and reuse Task 2's write-safety lock plus Plan 2's runtime lock. Acquire only the applicable subset of one fixed order everywhere: launch advisory lock, write-safety advisory lock, runtime advisory lock, runtime/launch control rows, then candidate/Run/call rows. No path may acquire an earlier lock after a later one. `database_now()` uses a PostgreSQL database clock and returns one UTC value per decision.

Repository methods include:

```python
def find_candidate_by_request_id(request_id: UUID, *, for_update: bool = False) -> PreGaLaunchCandidate | None: ...
def insert_candidate(candidate: NewCandidate) -> PreGaLaunchCandidate: ...
def lock_control() -> PreGaLaunchControl: ...
def find_gate_use_by_request_id(request_id: UUID) -> PreGaLaunchGateUse | None: ...
def append_use_and_advance_control(..., expected_revision: int) -> LaunchConsumptionResult: ...
```

Request digest hashes the canonical request fields excluding `request_id`, plus route/action and authenticated Operator ID. Same ID/same digest returns the existing result; same ID/different digest is `launch_request_reuse_conflict`.

Atomic Chat admission, every new production write proposal, launch candidate create/consume, and rollout activation acquire the same launch advisory lock before reading or changing a launch-relevant pointer; a new write/candidate decision then acquires the write-safety lock. The global order is launch advisory lock, write-safety advisory lock when needed, existing runtime advisory lock, runtime/launch control rows, Run/call rows. Plan 2 activation is updated to take the launch lock before its runtime lock/control CAS, so a qualification-target read cannot race an active-rollout change. Preparing an inactive immutable revision and changing the volatile new-Run switch do not change the target and need no launch lock. Unresolved-state transitions and reconciliation need only the write-safety lock and never acquire the launch lock, so they cannot invert the order.

Audit every Plan 1/2 mutation boundary against a closed ownership table. Active rollout pointer changes and any permitted in-place Model/credential revision/config mutation take the launch lock before their existing row/runtime locks. Published Profile/package/capability/seed versions referenced by a rollout are immutable; a new version affects production only through locked rollout activation. Build/deployed-Artifact/schema/trust/scenario/runner identities have no live mutation route and change only through a stopped migration/redeployment. Password/session/audit state, Worker heartbeat, new-Run switch, active-Run count, and unresolved-call state are explicitly non-subject operational values and retain their own locks. Add architecture tests that fail if a launch-relevant mutation route is mounted without this ownership classification.

- [ ] **Step 7: Derive the operational snapshot under the same locks**

Count all CapabilityCalls in `unknown` and `needs_reconciliation` separately and all nonterminal Chat Runs according to Plan 2's status constants. Use exact SQL while holding both locks: the write-safety lock serializes unresolved transitions/new write proposals, and the launch lock serializes Plan 2 atomic Chat admission through Run insertion. Snapshot `observed_at` and candidate `issued_at` use the same database time; `expires_at` is exactly 24 hours later and is validated by the Task 7 check.

Worker registrations/heartbeats are not counted. Candidate generation does not fail solely because no Worker is currently live.

- [ ] **Step 8: Create immutable candidates from references only**

Service signature:

```python
def create_candidate(
    request: CreatePreGaLaunchCandidateRequest,
    *,
    principal: OperatorPrincipal,
) -> PreGaLaunchCandidateResult:
    ...
```

Within one transaction: acquire locks; check exact request replay first; fetch evidence objects by derived content-addressed keys; verify object/Artifact/signature/trust; validate every required `ReleaseEvidenceManifestV1` field; build the current qualification target and require both manifests to bind its digest; require the two evidence-only qualification-infrastructure identities to match byte-for-byte while keeping that identity out of the production target/subject; build current subject; collect snapshot; derive ordered failure codes/pass; insert all denormalized identity columns plus canonical JSON; append safe Operator audit; commit once. Evidence refs and reasons are bounded; `passed`, failures, counts, digests, timestamps, target, subject, and expiry are absent from request schema.

- [ ] **Step 9: Write candidate replay/concurrency tests on PostgreSQL**

With two Sessions and barriers, prove:

- same request ID/body creates one candidate and returns the same ID;
- same request ID/different ref/reason returns conflict and one candidate;
- different request IDs may create distinct immutable observations;
- unresolved transition racing candidate creation is serialized and the candidate either precedes it as passing or observes it as failed;
- active Run count racing creation is likewise observed in one serial order;
- rollout activation or Model/credential mutation racing creation yields a candidate for exactly one complete old/new target, never mixed fields;
- candidate UPDATE/DELETE remains database-rejected.

- [ ] **Step 10: Define exact consumption validation order**

After exact request replay, validate in this order under locks:

1. authenticated candidate exists;
2. candidate `passed` is true;
3. database time is strictly earlier than `expires_at`;
4. `expected_control_revision` equals locked control revision;
5. both evidence objects still verify and match candidate digests;
6. current server-derived subject equals candidate subject byte-for-byte/digest-for-digest;
7. current unknown, reconciliation, and active-Run counts are all zero;
8. append use, advance control exactly one revision, and append Operator audit in one commit.

Errors are respectively stable `launch_candidate_not_passing`, `launch_candidate_expired`, `launch_control_conflict`, `launch_evidence_invalid`, `launch_subject_stale`, or `launch_operational_state_changed`. Do not reorder expiry after control mutation.

- [ ] **Step 11: Implement one-transaction gate use and control CAS**

Use UUIDv5 over the consumption request ID/domain for a deterministic use ID. Insert `PreGaLaunchGateUse` with candidate/subject, actual principal IDs, request digest, reason, expected/resulting revision, and database time. Update singleton control with `WHERE revision=:expected`, set candidate/use/subject, set `launched_at` to this current subject's database `used_at`, set `updated_at` to the same decision time, and require one affected row. Historical launch times remain in append-only gate uses; control describes only the active subject. Stage `OperatorAuditRepository.append()` before commit.

If insert, CAS, FK, or audit fails, the entire transaction rolls back. No use may exist without matching control and audit.

- [ ] **Step 12: Prove concurrent consumption and exact replay**

Test two Operators are impossible by account model, but two Sessions for the same Operator can race:

- same request ID/body: one use/control revision, both responses identical;
- different request IDs/same expected revision: one succeeds, one `launch_control_conflict`;
- request ID reused for another candidate/reason: conflict;
- candidate expires while waiting for control lock: database time recheck rejects;
- subject/operational state changes while waiting: recheck rejects;
- rollout/Model/credential mutation waiting on the launch lock occurs wholly before or after consumption; an after-consume change immediately makes authorization stale before admission;
- no failed path leaves gate use/audit/control residue.

- [ ] **Step 13: Implement durable launch evaluation without expiry/liveness/count coupling**

`evaluate_current_launch()` loads control and active candidate/use, verifies relational identity and evidence object integrity, rebuilds current qualification target plus durable subject using the candidate's two evidence digests, and compares exact target/subject digests. It does **not** compare current time with candidate expiry and does not query Worker liveness, active Runs, unknown calls, or reconciliation calls.

Return a frozen result:

```python
@dataclass(frozen=True)
class PreGaLaunchAuthorization:
    launched: bool
    reason_code: str | None
    control_revision: int
    active_subject_digest: str | None
```

Absent control use is internal reason `launch_control_missing`; durable mismatch is `launch_subject_stale`; evidence missing/invalid/unavailable has its own bounded internal authenticated-status reason. `AssistantReadinessService`, Chat admission, and public write failures map every `launched=false` result to the single stable public code `pre_ga_launch_unapproved`; they never expand `RUNTIME_READINESS_REASON_CODES` with storage/cryptography diagnostics. The authenticated launch-status endpoint may show the bounded internal reason without exception/object content.

- [ ] **Step 14: Add volatile-versus-durable regression matrix**

After consuming a candidate, prove all of these independently:

- advance database time 48 hours: still launched;
- add/finish active Runs: launch subject remains valid;
- expire/remove Worker heartbeat: launch subject remains valid, readiness becomes `worker_unavailable`;
- create an `unknown` or `needs_reconciliation` call: launch subject remains valid, new-write guard returns `reconciliation_required`;
- change build/image/deployed Artifact/schema/auth/rollout/Profile/Model/package/capability/seed/Worker contract/create/write/cohort/reconciliation/lock/scenario/runner/trust/evidence identity: launch becomes stale.

Do not use monkeypatched subject comparison to prove this; mutate each authoritative port/row and run the real evaluator.

- [ ] **Step 15: Integrate launch into production readiness and atomic Chat admission**

For `deployment_class=production`, `AssistantReadinessService` includes launch authorization as a dependency and reports `pre_ga_launch_unapproved` when base identity/control is healthy but no current launch use exists. Preserve every Plan 2 code and their relative order, inserting the new code at this exact position:

```python
RUNTIME_READINESS_REASON_CODES = (
    "system_not_initialized",
    "operator_missing",
    "operator_auth_unavailable",
    "system_seed_invalid",
    "profile_unpublished",
    "model_unbound",
    "rollout_inactive",
    "runtime_closure_drift",
    "pre_ga_launch_unapproved",
    "worker_unavailable",
    "schema_incompatible",
    "new_runs_disabled",
)
```

`schema_incompatible` remains the first structural check and therefore returns alone before downstream evaluation; its historical tuple position is retained only for compatibility. Launch is evaluated after structural closure and before the independent Worker/new-Run checks, so a healthy prelaunch system may report launch plus those independent reasons in the exact tuple order. Add the stable admission/API mapping. Activation-candidate evaluation deliberately ignores launch authorization, because launch is downstream of a prepared/active closure. `/health` remains process-only. Atomic Chat admission acquires the launch advisory lock before its existing control/closure locks, evaluates readiness, and retains that lock through Message/Run insertion; launch failure leaves no conversation/message/run/event residue. This makes the zero-active-Run candidate/consume snapshot linearizable.

Initialization, login/session/password, administration, reconciliation, Worker registration, rollout prepare/activate, candidate list/create/consume, and readiness remain mounted and callable before launch. Do not put launch authorization into activation eligibility. Activation nevertheless takes the launch advisory lock before Plan 2's runtime lock and active-control CAS; a concurrent candidate/create/consume therefore observes either the old or new active rollout as one serial order, never a torn target.

- [ ] **Step 16: Integrate launch into the create-entry write guard**

For production, pre-proposal and post-approval checks require `PreGaLaunchAuthorization.launched`. Exact existing-call/result replay remains first and may return an already-settled result while current launch is stale; it performs no new side effect. A genuinely new proposal acquires the launch lock before the write-safety lock and holds both through its locked launch/write admission decision. New proposal failure creates no CapabilityCall. Post-approval execution follows the same lock order before call/Run row locks; subject drift before staging safely fails the call before `side_effect_started_at` and creates no Entry.

For rehearsal, only Task 6's exact profile authorization can authorize scenario writes; it never reports production launched. Development defaults write mode off.

- [ ] **Step 17: Mount safe viewer and Operator HTTP routes**

Routes use Plan 1 protected browser parents:

- viewer `GET /api/pre-ga-launch/status`, `/qualification-target`, and `/candidates`;
- Operator+CSRF `POST /candidates` and `POST /candidates/{id}/consume`.

Status/list response includes safe IDs, digests, pass/failure codes, counts, revisions, issued/expiry/used times, and stale/current state. It omits subject JSON internals not needed by UI, signatures/public keys, evidence body/object location, request digests, session ID, reason if policy considers it audit-only, and all secrets/content. Pagination is stable by `(issued_at,id)` with bounded page size.

- [ ] **Step 18: Prove HTTP policy and client-non-authority**

Test no Session, viewer reads, viewer mutation, Operator missing/wrong CSRF, expired Session, wrong Origin/content type, stale CAS, exact replay, request-reuse conflict, failed/expired/stale candidates, and safe errors. Send extra `passed`, `failureCodes`, `subjectDigest`, `snapshot`, `issuedAt`, `expiresAt`, `operatorId`, or signature fields and require 422 with no persistence.

Add all five routes to exhaustive route-policy inventory and assert no unauthenticated alias exists. The qualification-target response contains only the stable contract's IDs/digests/versions and never Provider credential material, Profile/Skill text, prompts, or content.

- [ ] **Step 19: Run unit/API/readiness and PostgreSQL concurrency suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_pre_ga_launch_service.py \
  tests/test_pre_ga_launch_api.py \
  tests/test_assistant_runtime_readiness.py \
  tests/test_assistant_atomic_admission.py \
  tests/test_create_entry_production_guard.py \
  tests/test_route_auth_inventory.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
    tests/test_pre_ga_launch_postgres.py \
    tests/test_create_entry_write_guard_postgres.py -q
cd ..
git diff --check
```

Expected: all tests pass with no PostgreSQL skip; candidate decisions are server-derived; consumption is one-use/CAS/audit atomic; 24-hour unused expiry is enforced; consumed +48-hour authorization remains valid; liveness/count changes obey the separated semantics; durable drift blocks production.

- [ ] **Step 20: Commit**

```bash
git add \
  backend/app/pre_ga_launch \
  backend/app/assistant/runtime \
  backend/app/assistant/capability_calls/write_guard.py \
  backend/app/operator_auth/route_policy.py \
  backend/app/main.py \
  backend/app/config.py \
  backend/.env.example \
  backend/tests/test_pre_ga_launch_service.py \
  backend/tests/test_pre_ga_launch_postgres.py \
  backend/tests/test_pre_ga_launch_api.py \
  backend/tests/test_assistant_runtime_readiness.py \
  backend/tests/test_assistant_atomic_admission.py \
  backend/tests/test_route_auth_inventory.py
git commit -m "feat(launch): require consumed pre-GA evidence"
```

---

### Task 9: Run the Complete Automated Qualification in a Standalone Release Profile

**Files:**

- Create: `deploy/compose.release-qualification.yml`
- Create: `deploy/release.env.example`
- Create: `deploy/release-images.lock`
- Create: `backend/scripts/lock_release_images.py`
- Create: `backend/scripts/render_release_deployment_identity.py`
- Create: `backend/app/release/target_fixture.py`
- Create: `.github/workflows/release-qualification.yml`
- Create: `backend/tests/test_release_profile.py`
- Create: `backend/tests/test_release_qualification_e2e.py`
- Create: `backend/tests/test_release_target_fixture.py`
- Modify: `backend/app/release/contracts.py`
- Modify: `backend/app/release/runner.py`
- Modify: `backend/app/release/profile_authorization.py`
- Modify: `backend/scripts/run_pre_ga_release.py`
- Modify: `backend/Dockerfile`
- Modify: `frontend/Dockerfile`
- Modify: `.github/workflows/ci.yml`
- Modify: `deploy/README.md`

**Interfaces:**

- Consumes: Tasks 1–8's production code and exact `pre_ga_v1_0002`; Task 4's locks/images; Task 5 order runner; Task 6 scenarios/evidence/trust/rehearsal authorization; Plan 1 setup/password/Session/CSRF; Plan 2 two-Worker readiness/claims/checkpoint/SSE/L2; and fresh isolated Docker volumes.
- Produces: pinned standalone PostgreSQL/MinIO/API/Worker A/Worker B/Scripted Provider/Web profile; one immutable launch-relevant deployment-identity manifest; authenticated qualification-target capture/exact rehearsal clone; a complete deterministic fault/scenario matrix; required CI automation; a signed target-bound `automated_qualification` evidence object; and an OCI image bundle that Task 11 reuses without rebuilding.

- [ ] **Step 1: Write failing Compose topology and isolation tests**

Parse Compose YAML as data and assert exact required roles:

```python
REQUIRED_SERVICES = {
    "postgres",
    "minio",
    "minio-init",
    "schema-migrate",
    "scripted-provider",
    "api",
    "assistant-worker-a",
    "assistant-worker-b",
    "web",
}


def test_release_profile_is_standalone_and_two_worker():
    compose = load_release_compose()
    assert REQUIRED_SERVICES <= compose.services.keys()
    assert compose.worker_id("assistant-worker-a") != compose.worker_id("assistant-worker-b")
    assert compose.network("release-internal").internal is True
    assert not compose.references_main_deploy_volumes()
```

Reject `extends`/include of ordinary Compose, default credentials, host database/MinIO endpoints, Neo4j/paid Provider dependencies, `latest` tags, mutable infrastructure tags, a single Worker alias, or a shared named volume.

- [ ] **Step 2: Pin external release-profile images by registry digest**

`release-images.lock` is a canonical JSON document listing exact image references formed by `repository@sha256:` plus 64 lowercase hexadecimal characters, platform `linux/amd64`, and safe role for PostgreSQL 15, MinIO server, and MinIO client initialization. `lock_release_images.py --write` resolves reviewed publisher tags to platform manifests, stores immutable digests, and verifies pulls; `--check` re-resolves the committed digest and rejects tag-only Compose references.

The lock's canonical digest is recorded in the qualification-profile assertion/evidence, while Python `DEPENDENCY_LOCK_SET_SHA256` remains a separate launch-subject field. Qualification infrastructure images do not enter the target's launch-relevant application Artifact-set digest. Do not invent digest values in source or allow environment substitution of image repositories.

- [ ] **Step 3: Define a no-default release environment contract**

`release.env.example` lists non-secret variable names and generation commands only. Required values include unique Compose project/run ID, loopback API/Web ports, build revision, OCI image IDs/digests, deployment manifest path, public trust-set path, rehearsal authorization path, code-owned production target alias, descriptor numbers for signing/promotion credentials, and paths to ephemeral secret files. It contains no usable password/token/key/default, production evidence endpoint, bucket, prefix, or credential.

`run_pre_ga_release.py profile prepare` creates a new `0700` run directory and `0600` secret files using OS CSPRNG for database/MinIO credentials, Setup Token, Operator password, Session MAC ring, CSRF/session material, Fernet key, idempotency secret, Interrupt pepper, and reconciliation HMAC. It refuses an existing/nonempty directory and never prints values.

- [ ] **Step 4: Build each application Artifact once from the reviewed source**

Use BuildKit on `linux/amd64` to build the backend runtime target once, the separate Scripted Provider target once, and the Web target once. Load the same backend runtime image by immutable digest for the API and both Assistant Workers; `api_image_digest` and `assistant_worker_image_digest` are role bindings and must be equal in v1. Worker A/B identity is runtime registration state, never another image build. Record source commit, dirty-tree rejection, Dockerfile/frontend lock digests, image IDs, OCI manifest/config/layer digests, architecture, and Task 4 lock-set digest.

Export the exact images to one OCI archive bundle and hash the archive index/manifest identities. Task 11 imports this bundle; it may not rebuild. If source is dirty or an image label differs from generated lock/build identity, stop before starting services.

- [ ] **Step 5: Generate and sign the deployed Artifact identity manifest**

`render_release_deployment_identity.py` accepts image inspection JSON from local Docker by file descriptor, verifies expected labels/lock constants, and emits:

```python
class DeployedArtifactIdentityV1(FrozenContract):
    schema_version: Literal[1] = 1
    build_revision: str
    platform: Literal["linux/amd64"]
    api_image_digest: LowercaseSha256
    assistant_worker_image_digest: LowercaseSha256
    web_image_digest: LowercaseSha256
    dependency_lock_set_digest: LowercaseSha256
    image_set_digest: LowercaseSha256
    deployed_artifact_set_digest: LowercaseSha256


class QualificationInfrastructureIdentityV1(FrozenContract):
    schema_version: Literal[1] = 1
    platform: Literal["linux/amd64"]
    scripted_provider_image_digest: LowercaseSha256
    postgres_image_digest: LowercaseSha256
    minio_image_digest: LowercaseSha256
    minio_client_image_digest: LowercaseSha256
    release_images_lock_digest: LowercaseSha256
    compiler_image_digest: LowercaseSha256
    compiler_bootstrap_lock_digest: LowercaseSha256
    identity_digest: LowercaseSha256


class SignedDeployedArtifactIdentityV1(FrozenContract):
    schema_version: Literal[1] = 1
    domain: Literal["mindatlas:deployed-artifact-identity:v1"]
    key_id: SafeKeyId
    identity: DeployedArtifactIdentityV1
    signature_base64url: str
```

`image_set_digest` uses domain `mindatlas:application-image-set:v1` over the three role/digest pairs. `deployed_artifact_set_digest` uses domain `mindatlas:deployed-artifact-set:v1` over every preceding deployment-identity field except itself. The two application set digests cover only launch-relevant deployable roles `api`, `assistant-worker`, and `web`. The second contract covers the rehearsal-only Scripted Provider, pinned PostgreSQL/MinIO helpers, and the exact compiler image/bootstrap lock used to produce dependency locks; its self-excluding digest is embedded in both evidence manifests and must match across automation/rehearsal. It is never copied into `ReleaseQualificationTargetV1`, `PreGaLaunchSubjectV1`, or production launch control. Sign the deployment identity digest under domain `mindatlas:deployed-artifact-identity:v1` with a release-runner key whose public entry belongs to the same configured trust-set digest bound into the target but has an explicit deployment-identity domain permission distinct from evidence kinds. Mount the signed identity read-only into API/Workers and verify signature, trust permission, and each service's own immutable image labels at startup. A service cannot claim another image digest through environment text.

- [ ] **Step 6: Add release-only Docker targets without changing production commands**

Add `scripted-provider` target that runs only `app.release.scripted_provider` and a health endpoint; it uses the API lock and has no Provider credentials. Add a `release-migrate` target that verifies deployment class, performs exact `0002` migration, and exits. Keep ordinary runtime/assistant-worker targets unchanged except immutable OCI labels/identity verification.

Frontend image exposes a static build-content digest label generated from normalized `dist/` bytes. Its Nginx config proxies only the profile API and serves the real production frontend artifact.

- [ ] **Step 7: Compose a fresh isolated `rehearsal` database and object stores**

Use a unique project name and volumes such as `${RELEASE_RUN_ID}_postgres`, `_minio`, `_evidence`, and `_audit`; labels identify the run for exact cleanup. Database deployment class is `rehearsal`. MinIO init creates separate private buckets/prefixes for runtime Artifacts, release evidence, and safe audit export. Neither container binds a public interface; API/Web bind `127.0.0.1` random allocated ports only.

All service-to-service traffic is on `release-internal: {internal: true}`. Scripted Provider is the sole model endpoint; the profile cannot resolve/reach a paid Provider host.

- [ ] **Step 8: Start two separately identified compatible Assistant Workers**

Worker A/B have distinct code-owned IDs, instance UUID files, lease state paths, and health checks but identical build/runtime/codec `3`/feature/lock/deployment identities. Health checks query their own fresh registration, not another Worker or PID. Configure short bounded lease/heartbeat durations suitable for deterministic takeover tests while preserving Plan 2's `heartbeat < lease/3` invariant.

Tests fail if both services register the same identity, only one is live, either has a different feature digest, or a health check passes on stale registration.

- [ ] **Step 9: Capture/clone the exact qualification target and mount release-profile authorization**

`run_pre_ga_release.py target capture` logs into the already initialized/activated but launch-blocked production target using an Operator password supplied by file descriptor, receives the HttpOnly Session/CSRF cookies in an in-memory jar, fetches the server-derived `/api/pre-ga-launch/qualification-target`, and reads the exact non-secret Profile/Model/Package/Capability configuration through authenticated existing APIs. It writes a safe target identity file plus a `0600` ephemeral provisioning bundle; the latter may contain Skill/Profile configuration text but contains no Provider credential secret, user Entry/Artifact/prompt data, or cookies and is never evidence/uploaded.

`target_fixture.py` validates that provisioning bytes recompute every target ID/digest, then exposes a signed-run-bound `RehearsalInitializationFixturePort` to the normal initialization services. It does **not** insert a Model/Profile/seed/rollout row, stamp initialization, or commit directly. The rehearsal database remains uninitialized until the real Setup-Token HTTP scenario invokes Plan 1's coordinator; inside that one transaction, the fixture port supplies the captured immutable IDs/revisions/non-secret configuration so the normal bootstrap creates the exact target closure and audit chain. It supplies a fresh dummy encrypted credential secret only because execution is routed below model identity resolution through the signed rehearsal-only `ScriptedProviderTransportPort`; target Model identity remains exact and the dummy secret is excluded from that identity. Production composition rejects both ports and the provisioning bundle. Task 9 tests use a deterministic staged target fixture; Task 11 captures the real prelaunch target.

Before service start, the runner issues the exact Task 6 authorization bound to profile-run ID, `qualification_target_digest`, validated initialization-fixture digest, rehearsal schema runtime/comparable identity material, built launch-relevant image/deployed Artifact set, dependency lock, scenario/required-assertion, runner contract/identity, and trust set. Because final rehearsal runtime identity exists only after migration, use a two-stage startup: migrate/verify the still-uninitialized `0002` database, validate and mount the initialization fixture, read the server-derived rehearsal schema identity through a local migration result file, issue authorization, then start API/Workers with that read-only envelope. The real setup scenario performs all application seeding afterward.

No service starts release writes until signature/time/identity verification succeeds. Production deployment class or an authorization copied to another run/database fails startup.

- [ ] **Step 10: Encode the complete release assertion inventory**

The scenario set must emit at least these assertion groups, with every group release-critical:

| Group | Required proof |
|---|---|
| Schema | fresh `0002` upgrade, one root/head, application/control/runtime fingerprints, archive/root unchanged |
| Operator | one-time Setup Token, password initialization, HttpOnly/Secure/SameSite cookie, login/logout/rotation, viewer/operator RBAC, CSRF/origin, audit |
| Bootstrap | prepared rollout, pending-Worker reason, Worker registration, activation CAS, exact closure, ready after launch control in the dedicated launch phase |
| Worker | concurrent claim, lease heartbeat/expiry, Worker A kill and Worker B takeover, stale Worker commit rejection, identity compatibility |
| Interrupt | create/read, call ownership, approve/reject, duplicate decision, expiry, cancel before/after side-effect boundary |
| Streaming | SSE reconnect, cursor replay, duplicate cursor, internal event filtering, disconnect without cancelling Run |
| Create Entry | success, rejection, cancellation, duplicate Provider/browser/Resume, post-approval guard, one Entry, output replay |
| Recovery | process kill before stage, after stage/before commit, after commit/before ack, checkpoint observation loss, unknown/no-auto-retry, signed reconciliation |
| Artifact | private write/read digest, result/checkpoint refs, read-after-write, GC keeps reachable and removes eligible unreachable objects |
| Memory | codec-v3 checkpoint and L2 commit/replay without a Legacy path |
| Controls | readiness, new-Run switch, write-mode ceiling, launch unapproved, consumed launch, durable drift invalidation |
| Isolation | PostgreSQL/MinIO/two Workers all observed; test-order forward/reverse/isolated/three seeds; no installed-package stubs |

Each scenario has a bounded timeout and teardown assertion. There is no pass-by-absence, optional release group, or live Provider assertion.

- [ ] **Step 11: Add setup/login/RBAC/CSRF scenarios through real HTTP**

Use the generated Setup Token exactly once to initialize the Operator password and core bootstrap; assert second use fails. Capture cookies in an in-memory secure jar, assert attributes from raw response headers, and use CSRF cookie/header for mutations. Test unauthenticated/viewer/wrong CSRF/wrong Origin/expired Session and verify safe audit events by digest/count.

Never write raw setup token, password, Session/CSRF cookie, or password hash into traces/evidence. The runner overwrites in-memory bytearrays where practical and deletes secret files during teardown after services stop.

- [ ] **Step 12: Exercise rollout/readiness and the prelaunch boundary**

Start API before Workers and assert `/health=200`, `/ready=503` with dependency reasons. Start Worker A/B, prepare/activate the exact rollout, and prove production-marked logic would still report `pre_ga_launch_unapproved`; in rehearsal, the exact profile authorization permits only scenario operations. Run service-level launch candidate/consume scenarios against a separate production-marked phase database in Task 12, not by treating rehearsal auth as a gate use.

Within this automated evidence run, exercise candidate derivation/consume/time/drift using the service and HTTP contract on an isolated test transaction whose production marker is generated fresh and whose result is reset/destroyed; never persist it as a real deployment authorization.

- [ ] **Step 13: Exercise two-Worker claim, lease, kill, and restart schedules**

Use host orchestration to pause/kill Worker A only after the runner observes a safe event/row boundary. Wait for database lease expiry, prove Worker B claims with a new generation, and ensure A cannot heartbeat/commit after restart with stale generation. Repeat at checkpoint and post-commit/pre-ack boundaries.

All synchronization uses safe event IDs/revisions/digests from PostgreSQL, not arbitrary sleeps. Bounded polling uses database time and fails with a safe timeout code.

- [ ] **Step 14: Exercise every Interrupt and idempotency duplicate path**

Drive exact call-owned approval cards through Operator Session/CSRF; cover approve, reject, expiry using database clock control in the isolated profile, cancellation before effect, and cancellation racing committed local effect. Submit duplicate Provider tool-call delivery, browser decision POST, Resume event, client Chat request, and Worker takeover. Assert stable replay identities and one call/Interrupt/Entry/result/obligation outcome.

An unsupported update/merge/relation tool name is also injected by Scripted Provider and must terminate `capability_not_supported` with no call/Entry/Relation/substitute.

- [ ] **Step 15: Exercise transaction ambiguity and reconciliation**

Use the signed profile scenario schedule to activate Task 3's release-only fault port at named boundaries. For rollback-proven faults assert no Entry and safe same-call recovery; after-commit acknowledgement loss assert lookup by `source_capability_call_id` settles/replays one Entry; for deliberately indeterminate connection outcome assert transition to unresolved and no auto-retry/new writes.

Then list as viewer, reject mutation as viewer/wrong CSRF, submit server-signed evidence as Operator, assert one reconciliation/audit/checkpoint transaction, exact replay, and global write unblocking.

- [ ] **Step 16: Exercise Artifact, SSE, checkpoint, L2, and GC paths**

Reconnect SSE at older/equal/newer cursors and through internal sequence gaps. Kill/restart at a codec-v3 checkpoint and prove deterministic resume. Store/read result Artifacts in MinIO by digest, run GC with reachable and unreachable fixtures, and prove only eligible unreachable content disappears. Complete L2 memory commit and replay by durable references, with no Router/Supervisor/Legacy module import.

Evidence records only counts, IDs scoped to release run, safe event names, and aggregate digests—not messages, Entry bodies, memory facts, or Artifact payloads.

- [ ] **Step 17: Exercise kill switches and durable-versus-volatile launch semantics**

Toggle durable new-Run switch and process ceilings using authenticated control boundaries, then restore through reviewed paths. Test prelaunch false, consume current candidate, durable identity drift false, and corrected new candidate/use. After consumption, advance database time beyond candidate expiry and prove launch remains; lose Worker and prove only readiness changes; create unresolved call and prove only new writes block.

The runner restores each control by a normal CAS/audited mutation. There is no direct SQL repair of a tested state except fixture database-clock setup owned by the release harness.

- [ ] **Step 18: Make missing infrastructure a hard failure**

Before scenarios, require healthy PostgreSQL/MinIO/API/Scripted Provider/Web and two distinct fresh Workers. During scenarios continuously sample safe liveness and service identity. A missing/unhealthy service, MinIO fallback to memory/filesystem, SQLite fallback, one-Worker execution, Provider endpoint mismatch, scenario timeout, `pytest.skip`, or `xfail` produces a failed signed assertion and nonzero runner exit.

Run a negative test for each missing service and prove the runner cannot emit `passed=true`.

- [ ] **Step 19: Generate signed automated evidence and verify it twice**

On a fully passing run, derive all assertions server-side, require the observed rollout/Profile/Model/Package/Capability closure to equal the captured `qualification_target_digest`, upload the content-addressed object to the isolated evidence bucket, write a safe local copy, and verify:

1. offline with `verify_release_attestation.py`, the sealed Artifact bundle, and the committed/selected public trust set;
2. through the producing rehearsal-profile API evidence-verification service before teardown.

Both must report identical manifest/Artifact aggregate digests and assertion counts. The automated signing key is restricted to `automated_qualification`; the manifest states `schema_deployment_class=rehearsal` and exact comparable schema material. The live runner invokes `tests/test_release_qualification_e2e.py` against the running profile, rejects any skip/xfail/not-run case, and stores only its safe content-addressed JUnit/scenario-completeness projection. Implement `profile verify-complete-run` as a later read-only check that cross-verifies those Artifact digests and required service/scenario/assertion identities against both signed manifests; it cannot synthesize or alter a run result.

Implement the one-shot boundary for the other allowed kind in the same runner: before a `production_rehearsal` starts, verify a selected passing automation manifest, then instantiate the stable `RehearsalAttemptSubjectV1` contract from `qualification_target_digest`, exact application OCI/deployed Artifact set, dependency lock, scenario, required-assertion, runner contract/identity, and trust-set identities. Its domain-separated digest excludes only `subject_digest`; it deliberately excludes automation run ID, timestamps, manifest/attestation digest, request ID, and qualification-infrastructure identity, so rerunning identical automation or swapping only rehearsal helpers cannot mint another rehearsal attempt. Conditionally create an immutable `rehearsal-attempt-started.v1` receipt in a protected append-only attempt ledger at a key derived only from `subject_digest`. The ledger alias is code-owned; its conditional-create-only credential arrives by descriptor; the runner exposes no overwrite/delete/reset operation. Existing identical or different receipt bytes both refuse another attempt after reporting only `rehearsal_already_attempted`. A failed attempt still finalizes signed failing rehearsal evidence; another attempt requires a genuinely new target/build/application Artifact/lock/scenario/required-assertion/runner/trust subject and new passing automation.

The receipt contract is exact:

```python
class RehearsalAttemptStartedReceiptV1(FrozenContract):
    schema_version: Literal[1] = 1
    attempt_id: UUID
    subject: RehearsalAttemptSubjectV1
    selected_automation_manifest_digest: LowercaseSha256
    selected_automation_attestation_digest: LowercaseSha256
    started_at: datetime
    receipt_digest: LowercaseSha256
```

`attempt_id` is UUIDv5 over the fixed attempt namespace plus `subject_digest`.
`receipt_digest` uses domain `mindatlas:rehearsal-attempt-receipt:v1` and excludes
itself. Automation evidence fields and time are recorded for audit but are outside
the stable subject/key, so changing them cannot bypass the conditional-create gate.

- [ ] **Step 20: Implement host runner teardown and evidence preservation**

On success or failure, collect allowlisted logs/metrics/audit summaries, stop services, remove unique volumes/network/secrets, and prove no labeled resource remains. Preserve only the signed evidence object, deterministic sealed bundle of referenced safe JUnit/summary Artifacts, deployment identity manifest/signature, OCI image bundle/index digests, and runner log scrub report.

Implement `evidence promote` for Task 11 as a host-runner-only command. It accepts a locally verified canonical evidence object plus its sealed allowlisted Artifact bundle, a code-owned production-target alias, public trust-set path, and a narrowly scoped destination credential by already-open file descriptor. It re-verifies canonical bytes/signature, every Artifact byte/type/size/digest, aggregate, kind, and target digest. It conditionally creates all referenced Artifacts under derived `release-evidence-artifacts/v1/` keys first and the canonical manifest/attestation object under its derived `release-evidence/v1/` key last. Existing identical bytes are idempotent; any different existing bytes, missing/extra Artifact, mutable overwrite/delete capability, arbitrary bucket/key/endpoint input, or unverified object fails. The bucket is created with versioning plus an application-level append-only policy: the promotion principal can conditionally create only under those two prefixes, the API can only read, and neither can overwrite/delete/change retention. Profile and policy tests prove those denials. Promotion creates no candidate/use/control row and supplies no pass/outcome field.

If teardown cannot remove a resource, exit nonzero and print only resource label/ID. Never upload Compose-expanded config or secret env files.

Implement and test the host-only `production-clone negative-acceptance` subcommand used in Task 12: it streams `pg_dump` directly to an isolated `pg_restore` process through pipes, accepts source URL only by open descriptor, verifies source is production `0002`/prelaunch/control revision 0 and destination target digest is exact, runs the fixed negative matrix, then destroys destination. It refuses a file output, remote destination, nonempty destination, launched source, or any Legacy revision. This harness is not copied into the runtime image or exposed through HTTP.

- [ ] **Step 21: Add a required GitHub Actions release workflow**

The workflow uses Python 3.11/Node 20, Task 4 locks, Docker BuildKit, and a protected automated-evidence signing key. Jobs are ordered:

1. clean lock installs/import/conflict checks;
2. backend/frontend unit/build/test-order gates;
3. one source-clean image build/export;
4. full release-profile qualification;
5. offline/API evidence verification and secret scan;
6. safe Artifact upload.

Use `workflow_dispatch` and reusable `workflow_call` with an exact checked-out commit. Reject branch drift after checkout, dirty source, missing signing secret, and fork/untrusted contexts for evidence signing. No job uses `continue-on-error`; release qualification is a required status for a launch candidate.

The build/unit stages may run on ordinary protected CI. Final target capture, target-bound automation signing, and manual rehearsal run only on the authorized self-hosted release runner/environment that can reach the prelaunch target. Workflow input selects a code-owned target alias and release source revision only; it cannot submit a target digest, outcome, evidence digest, scenario subset, or pass value. Secrets are materialized to locked descriptors by the runner wrapper, never echoed as command-line values.

- [ ] **Step 22: Run profile contract and negative topology tests**

Run:

```bash
cd backend
.venv/bin/python -m pytest \
  tests/test_release_profile.py \
  tests/test_release_scenarios.py \
  tests/test_rehearsal_profile_authorization.py \
  tests/test_release_target_fixture.py -q
.venv/bin/python scripts/lock_release_images.py --check
.venv/bin/python scripts/run_pre_ga_release.py profile validate-compose \
  --compose ../deploy/compose.release-qualification.yml \
  --image-lock ../deploy/release-images.lock
cd ..
git diff --check
```

Expected: contract/negative tests pass; image locks resolve; the validator uses an ephemeral redacted contract environment, runs Compose `config --quiet` without printing expansion, and proves two unique Workers plus isolated required services.

- [ ] **Step 23: Run the complete automated qualification locally or on the protected runner**

Run:

```bash
cd backend
.venv/bin/python scripts/run_pre_ga_release.py profile prepare \
  --kind automated_qualification \
  --run-dir "$MINDATLAS_RELEASE_RUN_DIR" \
  --qualification-target "$MINDATLAS_QUALIFICATION_TARGET_FILE" \
  --target-provisioning-bundle "$MINDATLAS_TARGET_PROVISIONING_BUNDLE" \
  --signing-key-fd "$MINDATLAS_AUTOMATION_SIGNING_KEY_FD" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE"
.venv/bin/python scripts/run_pre_ga_release.py profile run \
  --kind automated_qualification \
  --run-dir "$MINDATLAS_RELEASE_RUN_DIR"
.venv/bin/python scripts/run_pre_ga_release.py profile verify \
  --kind automated_qualification \
  --run-dir "$MINDATLAS_RELEASE_RUN_DIR"
```

Expected: all release-critical scenarios execute with PostgreSQL, MinIO, two Workers, Scripted Provider, and Web; runner exits 0; signed automated evidence verifies offline/API; OCI bundle and deployment identity are retained; teardown reports zero residual resources. If the key is provided by an already-open descriptor, the variable contains the descriptor number, not key bytes.

- [ ] **Step 24: Commit**

```bash
git add \
  deploy/compose.release-qualification.yml \
  deploy/release.env.example \
  deploy/release-images.lock \
  deploy/README.md \
  backend/app/release \
  backend/scripts/lock_release_images.py \
  backend/scripts/render_release_deployment_identity.py \
  backend/scripts/run_pre_ga_release.py \
  backend/Dockerfile \
  frontend/Dockerfile \
  backend/tests/test_release_profile.py \
  backend/tests/test_release_qualification_e2e.py \
  backend/tests/test_release_target_fixture.py \
  .github/workflows/ci.yml \
  .github/workflows/release-qualification.yml
git commit -m "test(release): automate production qualification profile"
```

---

### Task 10: Ship Operator Launch/Reconciliation UX and Safe Production Observability

**Files:**

- Create: `frontend/src/features/pre-ga-launch/api/launch.ts`
- Create: `frontend/src/features/pre-ga-launch/queries.ts`
- Create: `frontend/src/features/pre-ga-launch/pages/PreGaLaunchPage.tsx`
- Create: `frontend/src/features/pre-ga-launch/pages/PreGaLaunchPage.test.tsx`
- Create: `frontend/src/features/pre-ga-launch/index.ts`
- Create: `frontend/src/features/reconciliation/api/reconciliation.ts`
- Create: `frontend/src/features/reconciliation/queries.ts`
- Create: `frontend/src/features/reconciliation/pages/ReconciliationPage.tsx`
- Create: `frontend/src/features/reconciliation/pages/ReconciliationPage.test.tsx`
- Create: `frontend/src/features/reconciliation/index.ts`
- Create: `frontend/src/features/assistant/components/UnsupportedCapabilityNotice.tsx`
- Create: `frontend/src/features/assistant/components/UnsupportedCapabilityNotice.test.tsx`
- Create: `backend/app/pre_ga_launch/observability.py`
- Create: `backend/app/assistant/capability_calls/observability.py`
- Create: `backend/tests/test_production_control_observability.py`
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`
- Modify: `frontend/src/features/settings/components/SettingsShell.tsx`
- Modify: `frontend/src/features/assistant/components/ToolCallDisplay.tsx`
- Modify: `frontend/src/lib/api/client.ts`
- Modify: `frontend/src/features/system-setup/runtimeRules.ts`
- Modify: `frontend/src/features/assistant-runtime/api/runtime.ts`
- Modify: `frontend/src/features/assistant-runtime/components/AssistantReadinessGate.tsx`
- Modify: `frontend/src/features/assistant-runtime/components/AssistantRuntimeActivationCard.tsx`
- Modify: `frontend/src/features/assistant/AssistantPage.tsx`
- Modify: `frontend/src/locales/en/common.json`
- Modify: `frontend/src/locales/zh/common.json`
- Modify: `backend/app/pre_ga_launch/service.py`
- Modify: `backend/app/assistant/capabilities/supported_writes.py`
- Modify: `backend/app/assistant/capability_calls/write_guard.py`

**Interfaces:**

- Consumes: Task 8's safe launch/status/list/create/consume APIs; Task 3's reconciliation list/mutation APIs; Plan 1 session role/CSRF/error handling; Task 1 unsupported safe code; backend safe metric/event sink; and current React/TanStack Query/i18n/layout conventions.
- Produces: viewer-readable launch and reconciliation pages; Operator-only CSRF mutations; evidence-ref-only candidate creation; explicit expiry/current/stale/failed/consumed states; CAS-safe consumption; unsupported-write copy with no substitution; bounded metrics/audit/logs; and UI/observability tests proving no secret/content fields cross the boundary.

- [ ] **Step 1: Write failing typed-client contract tests**

Define response/request types directly from the frozen HTTP contract and use mock fetch assertions:

```typescript
type EvidenceRef = {
  schemaVersion: 1
  evidenceKind: 'automated_qualification' | 'production_rehearsal'
  manifestDigest: string
  attestationDigest: string
}

type CreateLaunchCandidateInput = {
  automatedEvidenceRef: EvidenceRef
  rehearsalEvidenceRef: EvidenceRef
  requestId: string
  reason: string
}
```

Assert candidate creation sends exactly these four top-level fields and consume sends exactly `expectedControlRevision`, `requestId`, and `reason`. Tests fail if `passed`, assertion results, subject/snapshot/digest override, Operator ID, issued/expiry time, or evidence object location can be serialized.

- [ ] **Step 2: Add strict safe API projections and parsers**

Client parsers validate UUIDs, lowercase 64-hex digests, bounded reason/error code lists, nonnegative counts/revisions, and timezone timestamps. Treat malformed server data as `invalid_control_plane_response`; do not coerce arbitrary strings or render raw response JSON.

Status distinguishes:

```typescript
type LaunchState =
  | 'unapproved'
  | 'current'
  | 'stale'
  | 'evidence_unavailable'

type CandidateState =
  | 'passing_unused'
  | 'failed'
  | 'expired_unused'
  | 'consumed_current'
  | 'consumed_stale'
```

Server state is authoritative; browser time is display-only and never changes a candidate/control action locally.

- [ ] **Step 3: Reuse the Plan 1 Session/CSRF client boundary**

All browser requests use `credentials: 'include'`. Safe mutations obtain the CSRF cookie value through Plan 1's client helper and send its fixed header; page components cannot pass a token argument. Handle 401 by updating session state/login routing, 403 CSRF/role with fixed copy, 409 CAS/request-reuse as explicit conflicts, 422 candidate/evidence errors, and 503 launch/readiness errors.

Do not retry POST automatically. A network-ambiguous user retry reuses the same retained request ID/body; after a completed/changed action, generate a new request ID.

- [ ] **Step 4: Build the launch status summary first**

Render deployment class, qualification-target digest, schema revision/runtime digest prefix, active rollout/closure digest prefix, control revision, launch state/reason, active candidate/use IDs, launch/update times, evidence manifest digest prefixes, and trust/scenario/lock identity prefixes. Provide copy buttons only for safe full digests/IDs returned by the API.

For prelaunch Chat failure, show fixed bilingual explanation for `pre_ga_launch_unapproved`: initialization and rollout administration remain available, but Chat/create-entry require a current consumed candidate. Do not suggest a flag or direct database change.

Extend Plan 2's closed readiness-reason TypeScript union and status components with `pre_ga_launch_unapproved`; an unknown reason still renders generic fail-closed copy. Preserve the backend reason order rather than sorting in the browser.

- [ ] **Step 5: Build evidence-ref-only candidate creation**

The form accepts two typed evidence refs (manifest and attestation digests), a bounded reason, and a generated read-only request ID. Evidence kind is fixed by its section and cannot be changed. Local validation checks format only; it never previews/accepts `passed` or assertion data.

On submit, disable duplicate clicks while in flight but retain request ID/body through network ambiguity. Display the server-derived candidate result, ordered safe failure codes, observed counts, issue/expiry times, and evidence identities. A failed candidate remains visible; there is no “mark passing” action.

- [ ] **Step 6: Build immutable candidate history and filtering**

Use cursor pagination and server classifications. Filters are local/query parameters over safe state/kind/time, not authorization. Each row shows candidate ID, state, pass/failure codes, count snapshot, subject digest prefix, evidence digest prefixes, creator-safe label, issued/expiry/consumed times, and whether it matches current subject.

Never render canonical subject JSON, signatures, request digest, Session ID, Artifact body/key, raw reason in a public/shared context, or evidence object path. Viewer can inspect but cannot mutate.

- [ ] **Step 7: Build explicit CAS launch consumption**

Only a `passing_unused` unexpired current-subject candidate gets an Operator action. Confirmation displays candidate ID/subject digest, current control revision, expiry, and a bounded reason input. Request ID is generated/read-only; expected revision is copied from the latest status and shown.

On 409, refetch status/list and show “control changed; review again”; do not transparently resubmit with the new revision. On expiry/stale/evidence failure, refetch and preserve the failed attempt's safe message. Exact replay shows the original use/control result.

- [ ] **Step 8: Enforce viewer-versus-Operator UI capabilities**

Viewer sessions see status/history/reconciliation queue but no enabled candidate/consume/reconcile controls. Operator sessions see actions. UI role checks improve clarity only; tests also prove server 403 is handled if a viewer forges/calls a mutation client.

Session expiry during a confirmation closes mutation state, retains no CSRF/token value, and routes to login without claiming the action succeeded.

- [ ] **Step 9: Write launch page state/CAS/accessibility tests**

Use Testing Library/MSW to cover loading, unapproved/current/stale, passing/failed/expired/consumed candidates, viewer/operator roles, 401/403/409/422/503, network ambiguity exact retry, bilingual copy keys, keyboard confirmation, focus restoration, and screen-reader status announcements.

Inspect every mocked request body and assert the closed field set. Inject sentinel password/token/prompt/Entry values into rejected server extensions and assert they are neither rendered nor logged.

- [ ] **Step 10: Build the reconciliation queue client and page**

Viewer list rows contain safe call/Run IDs, revisions, mode, status, failure code, attempt/time summary, and server-provided evidence Artifact refs. Operator decision form accepts only allowed terminal decision, selected verified Artifact IDs, reason, generated request ID, and expected call/Run revisions.

For local `create_entry`, hide/disable `retry_same_key` and explain that commit ambiguity must be verified/terminalized; server remains authoritative. On successful decision refetch launch status/write safety counts and queue.

- [ ] **Step 11: Test reconciliation CSRF, revisions, replay, and safe rendering**

Cover viewer read/mutation denial, Operator decision, missing/expired Session, CSRF failure, stale call/Run revision, exact request replay, request reuse conflict, invalid/expired evidence, queue becoming empty, and unresolved count badge. UI must not accept a typed Operator ID, signature, raw idempotency key, Entry body, or arbitrary evidence JSON.

- [ ] **Step 12: Render unsupported Capability failures explicitly**

When a safe Assistant event/tool result contains `capability_not_supported` for update/merge/relation/follow-up, render `UnsupportedCapabilityNotice` with the exact unsupported action and fixed guidance. Do not offer “create a replacement Entry,” relation fallback, direct tool retry, or an Agent-side workaround. A separate link may navigate to ordinary human Entry/Relation UI if the authenticated product route exists; label it as a manual user action, not an Agent continuation.

Unknown failure codes use generic safe copy and never render backend detail/arguments.

- [ ] **Step 13: Add launch/reconciliation routes to navigation**

Mount `/settings/pre-ga-launch` and `/settings/reconciliation` behind the authenticated application shell. Add settings/sidebar entries under a “Production control” section with status/count badges. Initialization/login routes remain reachable when launch is unapproved; ordinary unauthenticated users cannot load page data.

Use lazy route chunks if consistent with the app, but route-policy behavior must not depend on hiding links.

- [ ] **Step 14: Define a closed safe metric vocabulary**

Backend adapters emit only allowlisted names/labels:

```text
mindatlas_agent_unsupported_write_total{branch,entrypoint}
mindatlas_create_entry_write_guard_rejection_total{reason_code,phase}
mindatlas_capability_unresolved{status}
mindatlas_pre_ga_launch_candidate_total{result_code}
mindatlas_pre_ga_launch_consume_total{result_code}
mindatlas_pre_ga_launch_state{state}
mindatlas_pre_ga_launch_drift_total{dimension}
```

Allowed labels are closed enums. No call/Run/Entry/candidate/operator/session/request ID, digest, reason, timestamp, evidence key, HTTP body, idempotency material, signature, prompt, or content appears as a label. Gauges derive counts from database queries; clients cannot increment them.

- [ ] **Step 15: Stage safe audit and structured log events**

Mutation audit remains Plan 1's same-transaction durable events. Action-specific metadata contains action enum, created/consumed/reconciliation safe object ID if permitted by audit policy, expected/resulting revisions, safe result/failure code, and subject/evidence digest prefixes or complete digests only where the audit schema explicitly permits them. Authentication actor/session come from principal.

Structured application logs use event name, phase, safe reason, duration bucket, and count; they exclude exception text unless mapped to a reviewed safe code. Reads/status polling do not generate durable audit rows.

- [ ] **Step 16: Prove observability redaction with sentinel values**

Inject unique sentinel values for password, Setup/Session/CSRF tokens, Provider/private keys, prompt, Entry title/body, Artifact body, raw idempotency key, reconciliation envelope, reason, and database URL through failed/success paths. Capture metrics, logs, API errors, audit safe metadata, and release evidence; assert no sentinel/substrings occur.

Also fuzz unrecognized branch/reason/dimension labels and require mapping to one bounded `other` label or rejection—never raw interpolation.

- [ ] **Step 17: Run frontend and backend focused suites**

Run:

```bash
cd frontend
npm run test -- --run \
  src/features/pre-ga-launch/pages/PreGaLaunchPage.test.tsx \
  src/features/reconciliation/pages/ReconciliationPage.test.tsx \
  src/features/assistant/components/UnsupportedCapabilityNotice.test.tsx
npm run build
cd ../backend
.venv/bin/python -m pytest \
  tests/test_pre_ga_launch_api.py \
  tests/test_create_entry_reconciliation_api.py \
  tests/test_production_control_observability.py \
  tests/test_route_auth_inventory.py -q
cd ..
git diff --check
```

Expected: focused UI/backend tests pass; TypeScript build passes; request bodies are closed; viewer/Operator behavior and safe observability assertions hold; formatting is clean.

- [ ] **Step 18: Run full frontend tests and production build**

Run:

```bash
cd frontend
npm run test
npm run build
```

Expected: all frontend tests pass; production build contains launch/reconciliation lazy assets and no embedded evidence object, key, password, token, database URL, or source map if production policy disables public maps.

- [ ] **Step 19: Commit**

```bash
git add \
  frontend/src/features/pre-ga-launch \
  frontend/src/features/reconciliation \
  frontend/src/features/assistant/components/UnsupportedCapabilityNotice.tsx \
  frontend/src/features/assistant/components/UnsupportedCapabilityNotice.test.tsx \
  frontend/src/features/assistant/components/ToolCallDisplay.tsx \
  frontend/src/app/App.tsx \
  frontend/src/components/layout/Sidebar.tsx \
  frontend/src/features/settings/components/SettingsShell.tsx \
  frontend/src/lib/api/client.ts \
  frontend/src/features/system-setup/runtimeRules.ts \
  frontend/src/features/assistant-runtime \
  frontend/src/features/assistant/AssistantPage.tsx \
  frontend/src/locales \
  backend/app/pre_ga_launch \
  backend/app/assistant/capability_calls/observability.py \
  backend/app/assistant/capabilities/supported_writes.py \
  backend/tests/test_production_control_observability.py
git commit -m "feat(ui): operate pre-GA launch and reconciliation"
```

---

### Task 11: Execute the One-Time Production-Shaped Rehearsal and Preserve Signed Evidence

**Files:**

- Create: `docs/superpowers/evidence/2026-07-28-create-entry-automated-qualification.json`
- Create: `docs/superpowers/evidence/2026-07-28-production-shaped-rehearsal.json`
- Verify unchanged: application source, Dockerfiles, dependency locks, scenario set, runner, Compose profile, migrations, and frontend source.

**Interfaces:**

- Consumes: a clean Task 10 product-source commit; authority to stage the fresh production target through migration/initialization/rollout activation while launch remains blocked; Task 9's build/profile/target-capture workflow, immutable one-shot rehearsal receipt, and standalone profile; Task 6's rehearsal-only key/authorization; and the complete scenario matrix.
- Produces: one exact release OCI bundle containing the launch-relevant API/Assistant-Worker/Web images plus a separately identified rehearsal-only Scripted Provider image; a fresh production target held at `pre_ga_launch_unapproved`; a server-derived `ReleaseQualificationTargetV1`; target-bound signed automated evidence; exactly one attempted target-bound production-shaped rehearsal; conditional append-only promotion of both verified objects into the production release-evidence store; offline and target-container verification; safe evidence-reference summaries; and proof that no rehearsal rebuild or calendar soak occurred.

- [ ] **Step 1: Freeze the release source revision before evidence-only commits**

From a clean Task 10 implementation commit, set and record:

```bash
git status --short
export RELEASE_SOURCE_REVISION="$(git rev-parse HEAD)"
git show --no-patch --format='%H %s' "$RELEASE_SOURCE_REVISION"
```

Expected: worktree is clean and the subject is the Task 10 implementation commit. From this point through final launch, application/runtime/frontend/migration/lock/scenario/runner/Compose files may not change. Later commits contain safe evidence summaries only; deployed build identity remains `RELEASE_SOURCE_REVISION`, not the later documentation-only HEAD.

- [ ] **Step 2: Run clean gates and build the exact launch-relevant OCI bundle once**

Run Task 9's protected workflow through clean installs, backend/frontend/order gates, and its build/export stage for exactly `RELEASE_SOURCE_REVISION`; stop before target-bound qualification scenarios. Require a clean checkout and protected signing/build context:

```bash
cd backend
.venv/bin/python scripts/run_pre_ga_release.py artifact build \
  --source-revision "$RELEASE_SOURCE_REVISION" \
  --output-dir "$MINDATLAS_RELEASE_ARTIFACT_DIR"
.venv/bin/python scripts/run_pre_ga_release.py artifact verify \
  --deployment-identity "$DEPLOYMENT_IDENTITY_FILE" \
  --oci-bundle "$RELEASE_OCI_BUNDLE"
```

Expected: one API/Assistant-Worker/Web application image set, one separately hashed rehearsal-only Scripted Provider image, pinned external qualification-infrastructure and compiler identities, deployment manifest/signature, and OCI bundle/index verify. The Scripted Provider and qualification infrastructure are not part of the launch-relevant deployed Artifact digest or production target. No automated/rehearsal evidence is emitted yet because the production qualification target is not active.

- [ ] **Step 3: Stage the fresh production target through exact `pre_ga_v1_0002`**

Import the verified OCI bundle with `--no-build`, create a new isolated production database/Artifact prefix, and run the release migration image with explicit `deployment_class=production`. Start API/Web and two exact Assistant Workers without any rehearsal authorization or Scripted Provider. Verify `/health=200`, `/ready=503 system_not_initialized`, schema family/revision/application/control/runtime identity, control revision 0, and empty candidate/use/Run/call/Entry state.

Any existing schema/data/Artifact prefix, Legacy revision/object, image-label drift, or mounted rehearsal authorization stops with no cleanup/reinterpretation of user data.

- [ ] **Step 4: Initialize/activate the target, prove prelaunch blocking, and capture its exact target identity**

Use the one-time Setup Token to set the real Operator password and create the production Model/system seed/Profile V2/prepared rollout atomically. Login through the HttpOnly Session Cookie flow, observe both compatible Workers, and activate the exact rollout through Operator+CSRF/CAS. Require `/ready=503 pre_ga_launch_unapproved`, no Chat/CapabilityCall/Entry residue, and zero unknown/reconciliation/active-Run counts.

Then capture the server-derived target and ephemeral credential-free provisioning bundle:

```bash
.venv/bin/python scripts/run_pre_ga_release.py target capture \
  --base-url "$MINDATLAS_PRODUCTION_BASE_URL" \
  --operator-password-fd "$MINDATLAS_OPERATOR_PASSWORD_FD" \
  --qualification-target "$MINDATLAS_QUALIFICATION_TARGET_FILE" \
  --provisioning-bundle "$MINDATLAS_TARGET_PROVISIONING_BUNDLE"
```

Expected: the safe target digest binds current build/application images; production schema family/revision/application and marker-control fingerprints/identity-contract version/runtime/comparable material; auth; rollout/Profile/Model/Package/Capability/seed; Worker/create/write; dependency lock; scenario/required-assertion; runner contract/identity; and trust-set identities. The `0600` provisioning bundle recomputes that digest and contains no Provider secret or Entry/Chat/Artifact business data; any frozen Skill/Profile configuration text it must carry is treated as sensitive, never logged/uploaded/evidenced, and later destroyed. The target remains unlaunched.

- [ ] **Step 5: Run and verify final target-bound automated qualification**

Use the exact imported images and target bundle; the signed rehearsal authorization enables only the lower-level Scripted Provider transport while preserving the target Model identity. Do not rebuild:

```bash
.venv/bin/python scripts/run_pre_ga_release.py profile prepare \
  --kind automated_qualification \
  --run-dir "$MINDATLAS_AUTOMATION_RUN_DIR" \
  --qualification-target "$MINDATLAS_QUALIFICATION_TARGET_FILE" \
  --target-provisioning-bundle "$MINDATLAS_TARGET_PROVISIONING_BUNDLE" \
  --signing-key-fd "$MINDATLAS_AUTOMATION_SIGNING_KEY_FD" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE" \
  --no-build
.venv/bin/python scripts/run_pre_ga_release.py profile run \
  --kind automated_qualification \
  --run-dir "$MINDATLAS_AUTOMATION_RUN_DIR" \
  --no-build
.venv/bin/python scripts/run_pre_ga_release.py profile verify \
  --kind automated_qualification \
  --run-dir "$MINDATLAS_AUTOMATION_RUN_DIR"
```

Expected: complete automation passes and signed evidence exactly binds `qualification_target_digest`, release source/application image set, rehearsal-comparable schema, exact cloned auth/rollout/Profile/Model/Package/Capability/seed/create-write identities, dependency locks, scenario/required-assertion, runner/trust, evidence-only qualification infrastructure, and safe Artifacts. Offline verification passes before the one-shot rehearsal. A prior generic/earlier-target evidence object cannot qualify.

- [ ] **Step 6: Claim the immutable one-shot attempt before service startup**

The dedicated private Ed25519 key is supplied as an already-open secret descriptor and is allowed only for `production_rehearsal`; the trust-set digest must equal the target/automated manifest. The runner verifies the selected automation evidence, materializes `RehearsalAttemptSubjectV1` from `qualification_target_digest`, exact application image/deployed Artifact set, dependency lock, required-assertion set, scenario set, runner contract/identity, and trust set while excluding automation run/timestamp/manifest/attestation/request and qualification-infrastructure identities, then conditionally writes `rehearsal-attempt-started.v1` before any profile service starts:

```bash
.venv/bin/python scripts/run_pre_ga_release.py profile prepare \
  --kind production_rehearsal \
  --run-dir "$MINDATLAS_REHEARSAL_RUN_DIR" \
  --qualification-target "$MINDATLAS_QUALIFICATION_TARGET_FILE" \
  --target-provisioning-bundle "$MINDATLAS_TARGET_PROVISIONING_BUNDLE" \
  --automation-evidence "$AUTOMATED_EVIDENCE_FILE" \
  --oci-bundle "$RELEASE_OCI_BUNDLE" \
  --signing-key-fd "$MINDATLAS_REHEARSAL_SIGNING_KEY_FD" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE" \
  --attempt-ledger-alias "$MINDATLAS_REHEARSAL_ATTEMPT_LEDGER_ALIAS" \
  --attempt-ledger-credential-fd "$MINDATLAS_REHEARSAL_ATTEMPT_LEDGER_CREDENTIAL_FD" \
  --no-build
```

Expected: no receipt exists and one is created. If a receipt already exists—even for an interrupted/failed rehearsal—the runner returns `rehearsal_already_attempted` and stops. Do not delete/rename the receipt or choose a new request ID; a retry requires a new automated release subject.

Never persist private key bytes in the run directory, environment value, Compose file, log, Artifact, provisioning bundle, or evidence summary.

- [ ] **Step 7: Create a fresh isolated rehearsal database/storage set**

Prepare a new run directory and unique Compose project. Assert no prior PostgreSQL/MinIO/evidence/audit volume is attached. Generate fresh Setup Token, Operator password, Session/CSRF keys, database/MinIO credentials, idempotency secret, Interrupt pepper, and reconciliation secret. Database deployment class is exactly `rehearsal`. Validate and mount the server-owned target fixture, but require the application tables and initialization marker to remain empty; no fixture code may open a database Session. During the real Setup-Token scenario, `RehearsalInitializationFixturePort` supplies the exact captured IDs/non-secret configuration to the normal coordinator transaction. After setup, Worker observation, and normal rollout activation, the runner recomputes and proves the resulting closure equals `qualification_target_digest` before any create-entry qualification step.

The durable one-shot receipt/evidence destination is external to the disposable profile volumes; runtime database/Artifact/audit data remain isolated and are destroyed after evidence finalization.

- [ ] **Step 8: Issue and mount the exact short-lived rehearsal authorization**

Migrate the fresh database to `pre_ga_v1_0002`, verify family/revision/fingerprint/runtime/comparable identity, then sign the exact `RehearsalProfileAuthorizationV1` bound to profile-run ID, `qualification_target_digest`, validated initialization-fixture digest, loaded application image/deployed Artifact set, build, rehearsal runtime/comparable schema identity, dependency lock, scenario/required-assertion, runner contract/identity, and trust set. Mount it read-only into API/Workers and ensure its lifetime covers the bounded scenario timeout. The authorization also permits only the exact Scripted Provider transport adapter below Model identity resolution.

Assert a production-class negative startup using the same authorization fails before HTTP/Worker claim and creates no launch gate use.

- [ ] **Step 9: Start the production-shaped profile with production settings where applicable**

Start pinned PostgreSQL 15, MinIO, migration, API, two Assistant Workers, Scripted Provider, and the exact Web artifact. Use production process commands, non-root users, hashed locks, real PostgreSQL/MinIO, codec 3, enforced ledger, durable Interrupts, and write mode `create_entry`; only deployment class/short-lived authorization and deterministic Provider/fault schedule identify it as rehearsal.

No SQLite, in-memory Artifact store, mock framework package, single Worker, development server, live/paid Provider, Router/Supervisor, or Legacy runtime is permitted.

- [ ] **Step 10: Run the same complete scenario set without selection changes**

Run:

```bash
cd backend
.venv/bin/python scripts/run_pre_ga_release.py profile run \
  --kind production_rehearsal \
  --run-dir "$MINDATLAS_REHEARSAL_RUN_DIR" \
  --automation-evidence "$AUTOMATED_EVIDENCE_FILE" \
  --no-build
```

The runner loads the same `pre_ga_launch.v1.json` digest and executes every release-critical group from Task 9. It cannot accept scenario include/exclude, expected result, pass, assertion, Worker-count, fault-outcome, or service-skip inputs.

- [ ] **Step 11: Require production-shaped Operator and create-entry observations**

Among complete matrix results, explicitly verify:

- Setup Token used once; Operator password initialization; HttpOnly Session login/rotation/logout; RBAC/CSRF/origin/audit;
- rollout pending Worker, two distinct compatible registrations, activation, readiness reasons;
- create-entry call-owned approval success/reject/cancel/expire;
- duplicate Provider/browser/Resume/Worker schedules converge to one Entry;
- post-approval guard, transaction rollback/commit ambiguity, unknown/no-auto-retry, signed Operator reconciliation;
- SSE/checkpoint/restart/lease takeover, private Artifact/GC, L2 commit;
- unsupported update/merge/relation branches create nothing;
- launch expiry/liveness/unresolved/durable-drift semantics.

All results come from server/database/object-store observations and fixed evaluators, not human checkboxes.

- [ ] **Step 12: Finalize signed evidence on both pass and failure**

At scenario end—or bounded failure—the runner gathers the allowlisted observation set, derives every assertion outcome, stores safe Artifacts content-addressed, computes aggregate/manifest digests, and signs `production_rehearsal` evidence. Populate every field of `ReleaseEvidenceManifestV1`: UTC `started_at`/`ended_at`; exact `qualification_target_digest`; build/application-image/deployed Artifact; rehearsal schema family/revision/application and marker-control fingerprints/identity-contract version/deployment class/seed-contract/runtime-contract/codec/feature/runtime digest/comparable material; auth; rollout/Profile/Model/Package/Capability/seed-manifest closure; Worker/create/write/cohort/reconciliation contracts; dependency lock; scenario/required-assertion; runner contract/identity; trust set; evidence-only `QualificationInfrastructureIdentityV1`; assertion outcomes; and safe Artifact refs/aggregate. Worker instance observations and Operator/RBAC, metric, and audit proofs remain typed assertion observations, not extra manifest fields.

If any assertion fails, evidence is signed with failures and the release subject is not launchable. Do not rerun this subject.

- [ ] **Step 13: Verify rehearsal evidence offline before teardown**

Run:

```bash
.venv/bin/python scripts/verify_release_attestation.py \
  --evidence "$REHEARSAL_EVIDENCE_FILE" \
  --artifact-bundle "$REHEARSAL_EVIDENCE_ARTIFACT_BUNDLE" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE"
.venv/bin/python scripts/run_pre_ga_release.py compare \
  --automation-evidence "$AUTOMATED_EVIDENCE_FILE" \
  --rehearsal-evidence "$REHEARSAL_EVIDENCE_FILE"
```

Expected for launchable output: signature/object/Artifact verification passes; assertion failures are zero; `qualification_target_digest` and build/application-image/deployed Artifact/lock/schema-comparable/auth/rollout/Profile/Model/Package/Capability/seed/create-write/scenario/required-assertion/runner/trust identities equal automation; `QualificationInfrastructureIdentityV1` matches separately; evidence kinds/run IDs/times differ as expected. Because both qualifying databases are `rehearsal` and use identical schema material, their rehearsal runtime-identity digests must also match exactly; that shared digest differs from the production target runtime identity only through the immutable deployment-class input.

- [ ] **Step 14: Promote verified evidence append-only and verify it with both containers**

Step 5 already verified the automation object through its producing profile. Before stopping the rehearsal API, verify the rehearsal object through that profile's release-evidence service. Then promote both already offline/profile-verified canonical objects into the production target's durable release-evidence bucket using the Task 9 host-only command and a write-only, conditional-create credential supplied by descriptor:

```bash
.venv/bin/python scripts/run_pre_ga_release.py evidence promote \
  --evidence "$AUTOMATED_EVIDENCE_FILE" \
  --artifact-bundle "$AUTOMATED_EVIDENCE_ARTIFACT_BUNDLE" \
  --kind automated_qualification \
  --target-alias "$MINDATLAS_PRODUCTION_TARGET_ALIAS" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE" \
  --destination-credential-fd "$MINDATLAS_EVIDENCE_PROMOTION_CREDENTIAL_FD"
.venv/bin/python scripts/run_pre_ga_release.py evidence promote \
  --evidence "$REHEARSAL_EVIDENCE_FILE" \
  --artifact-bundle "$REHEARSAL_EVIDENCE_ARTIFACT_BUNDLE" \
  --kind production_rehearsal \
  --target-alias "$MINDATLAS_PRODUCTION_TARGET_ALIAS" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE" \
  --destination-credential-fd "$MINDATLAS_EVIDENCE_PROMOTION_CREDENTIAL_FD"
```

The target alias resolves a reviewed bucket/prefix; the command accepts no endpoint, bucket, object key, pass value, outcome, or overwrite flag. Execute the same verifier inside the still-unlaunched production API container, using its read-only store credential/public trust set, and require manifest digest/key ID/assertion counts and `qualification_target_digest` to equal offline verification. This promotion/verification is not candidate creation, so the 24-hour candidate clock has not started. Client never submits `passed` or outcomes.

Assert both rehearsal and production candidate/use tables remain empty and no `pre_ga_launch_gate_use` exists.

- [ ] **Step 15: Run the evidence/log secret and content scan**

Scan signed manifests, summaries, JUnit, safe traces, runner/application logs, metrics, audit export, deployment manifest, and workflow output for generated sentinel forms. Require absence of password/hash, Setup/Session/CSRF tokens, Provider/private key, database/MinIO credentials/URL, raw idempotency key, reconciliation signature/envelope, prompt, Entry title/body, Artifact body, and memory facts.

Finding one sentinel fails the rehearsal evidence assertion and burns the one-shot subject; do not redact after signing and claim the original run passed.

- [ ] **Step 16: Teardown and prove isolation cleanup**

Stop rehearsal services, remove only the unique rehearsal project network/volumes/secret directory, and query Docker labels to prove zero rehearsal resources remain. Keep the real production target running but launch-blocked with its active target closure and zero candidate/use rows. Preserve immutable attempt receipt, signed evidence objects, sealed allowlisted Artifact bundles, safe summaries, automation OCI bundle, qualification target, and deployment identity; independently prove every promoted production-store Artifact/object is readable and digest-complete before deleting its disposable source copy. Destroy the ephemeral provisioning bundle after both evidence objects finalize/verify. Run-directory permissions and retained-file allowlist are verified.

Failure to clean up is a signed failing assertion and nonzero outcome.

- [ ] **Step 17: Write the two safe repository evidence summaries**

Each JSON file is canonical, contains no private/runtime data, and is generated from this closed projection of the verified content-addressed manifest:

```python
class SafeReleaseEvidenceSummaryV1(FrozenContract):
    schema_version: Literal[1] = 1
    evidence_kind: Literal["automated_qualification", "production_rehearsal"]
    release_source_revision: str
    qualification_target_digest: LowercaseSha256
    manifest_digest: LowercaseSha256
    attestation_digest: LowercaseSha256
    artifact_aggregate_digest: LowercaseSha256
    key_id: SafeKeyId
    assertion_passed: int = Field(gt=0)
    assertion_failed: Literal[0] = 0
    started_at: datetime
    ended_at: datetime
    offline_verification: Literal["passed"] = "passed"
    target_container_verification: Literal["passed"] = "passed"
    soak_claimed: Literal[False] = False
```

The summary generator reads actual values from verified manifests and refuses hand-authored/missing/extra fields. The automation and rehearsal summaries use the same shape with their respective kind/digests; a check command byte-compares committed canonical JSON with regeneration.

- [ ] **Step 18: State the no-soak conclusion precisely**

The evidence summary and release notes say: “complete automated qualification plus one production-shaped rehearsal completed.” They must not claim 7-day, 14-day, calendar, traffic, multi-user, hosted-SaaS, Legacy canary, or restore soak coverage. Timestamps describe only actual run duration.

- [ ] **Step 19: Verify evidence-only diff and commit**

Run:

```bash
git diff --name-only "$RELEASE_SOURCE_REVISION"
cd backend
.venv/bin/python scripts/run_pre_ga_release.py evidence-summary check \
  --evidence "$AUTOMATED_EVIDENCE_FILE" \
  --artifact-bundle "$AUTOMATED_EVIDENCE_ARTIFACT_BUNDLE" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE" \
  --summary ../docs/superpowers/evidence/2026-07-28-create-entry-automated-qualification.json
.venv/bin/python scripts/run_pre_ga_release.py evidence-summary check \
  --evidence "$REHEARSAL_EVIDENCE_FILE" \
  --artifact-bundle "$REHEARSAL_EVIDENCE_ARTIFACT_BUNDLE" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE" \
  --summary ../docs/superpowers/evidence/2026-07-28-production-shaped-rehearsal.json
cd ..
git diff --check
```

Expected: since the release source commit, only the two safe evidence summaries are new; both checks and formatting pass. Then commit:

Before committing, query the production target once more and require active rollout/qualification-target digest unchanged, `/ready=503 pre_ga_launch_unapproved`, zero candidate/use/active-Run/unresolved counts, and no rehearsal authorization. This preserves the design flow “activate target → qualify/rehearse exact target → create candidate.”

```bash
git add \
  docs/superpowers/evidence/2026-07-28-create-entry-automated-qualification.json \
  docs/superpowers/evidence/2026-07-28-production-shaped-rehearsal.json
git commit -m "docs(evidence): record pre-GA production rehearsal"
```

---

### Task 12: Execute the Final Clean Gate, Consume Launch Control, and Record Acceptance

**Files:**

- Create: `docs/superpowers/evidence/2026-07-28-pre-ga-launch.json`
- Verify unchanged: all product/backend/frontend/deploy/migration/lock/scenario/runner files from `RELEASE_SOURCE_REVISION`.
- Verify: `docs/superpowers/evidence/2026-07-28-create-entry-automated-qualification.json`
- Verify: `docs/superpowers/evidence/2026-07-28-production-shaped-rehearsal.json`

**Interfaces:**

- Consumes: exact Task 11 release source/images/deployment identity, passing target-bound signed automation/rehearsal evidence refs, public trust set, the fresh single-Operator production target already initialized/activated but launch-blocked, Task 8 candidate/consume/readiness/write enforcement, and all verification scripts.
- Produces: final clean-install/full-suite evidence; a disposable production-class negative acceptance run; revalidation of the staged production `pre_ga_v1_0002` target/qualification digest; one passing candidate created and consumed within 24 hours; current launch control; ready compatible Workers; one approved `create_entry` smoke with one Entry; safe launch evidence; and explicit operational rollback/stop boundaries.

- [ ] **Step 1: Prove product bytes still equal the rehearsed release source**

Set `RELEASE_SOURCE_REVISION` from the verified evidence and compare every Artifact-producing path:

```bash
git diff --exit-code "$RELEASE_SOURCE_REVISION" -- \
  backend/app backend/alembic backend/requirements backend/Dockerfile \
  backend/scripts frontend/src frontend/package.json frontend/package-lock.json \
  frontend/Dockerfile deploy .github/workflows
git status --short
```

Expected: no product/deploy/workflow difference; only the two Task 11 safe evidence summary files may differ from that earlier commit. If any runtime-producing byte changed, stop, create a new release source revision, and repeat automation plus a new one-shot rehearsal subject.

- [ ] **Step 2: Reverify locks, manifests, seed, migration, and archive without writes**

Run:

```bash
cd backend
.venv/bin/python scripts/compile_requirements.py --check
.venv/bin/python scripts/build_assistant_system_seed.py --check
MINDATLAS_SCHEMA_GENERATOR_POSTGRES_URL="$MINDATLAS_SCHEMA_GENERATOR_POSTGRES_URL" \
  .venv/bin/python scripts/generate_pre_ga_v1_0002_identity.py --check
.venv/bin/python scripts/archive_pre_ga_lineage.py --check
.venv/bin/alembic roots
.venv/bin/alembic heads
```

Expected: lock/seed/schema generators are byte-clean; archive is exact 60 files; root is `pre_ga_v1_0001`; sole head is `pre_ga_v1_0002`; no Legacy revision is live/importable.

- [ ] **Step 3: Repeat both from-empty Python 3.11 installations**

Run:

```bash
.venv/bin/python scripts/compile_requirements.py clean-install \
  --target api-worker --platform linux/amd64
.venv/bin/python scripts/compile_requirements.py clean-install \
  --target parse-worker --platform linux/amd64
```

Expected: each fresh isolated venv installs with `--require-hashes`, `pip check` passes, API/Worker or Docling/Torch import smoke passes, installed distribution set matches its lock, and no cache/site-packages is shared.

- [ ] **Step 4: Run the complete backend suite with real dependencies**

Use a fresh PostgreSQL 15 test database and MinIO service where required:

```bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
MINDATLAS_TEST_MINIO_ENDPOINT="$MINDATLAS_TEST_MINIO_ENDPOINT" \
  .venv/bin/python -m pytest -q \
    --ignore=tests/test_release_qualification_e2e.py
```

Expected: the full ordinary backend suite passes with no dependency/PostgreSQL/MinIO release-critical skip. The profile-owned E2E module is excluded only to prevent the ordinary collection from trying to own the already-qualified standalone topology. This ordinary-suite gate and Task 11's signed live-profile execution are complementary and both mandatory; neither result can substitute for the other. The E2E module itself contains no `pytest.skip`/`xfail` or missing-infrastructure escape.

- [ ] **Step 5: Run all deterministic test-order modes**

Run:

```bash
.venv/bin/python scripts/run_test_order_regression.py --mode streaming-then-tombstone
.venv/bin/python scripts/run_test_order_regression.py --mode tombstone-then-streaming
.venv/bin/python scripts/run_test_order_regression.py --mode isolated
for seed in 1701 2701 3701; do
  .venv/bin/python scripts/run_test_order_regression.py --mode seeded --seed "$seed"
done
```

Expected: all modes exit 0, real FastAPI/Starlette identities remain intact, and three seeded order digests are recorded safely.

- [ ] **Step 6: Run complete frontend tests and rebuild only as a verification comparison**

Run in a clean Node work directory:

```bash
cd ../frontend
npm ci
npm run test
npm run build
```

Hash normalized `dist/` and compare with the Web image label/deployment identity. This local build is a verification reproduction and is not deployed; exact OCI bundle images remain the launch Artifacts. Any byte mismatch stops release rather than replacing the bundle.

- [ ] **Step 7: Reverify both signed qualification manifests and summaries**

Run offline verification for automation and rehearsal, check their safe repository projections, and compare identities:

```bash
cd ../backend
.venv/bin/python scripts/verify_release_attestation.py \
  --evidence "$AUTOMATED_EVIDENCE_FILE" \
  --artifact-bundle "$AUTOMATED_EVIDENCE_ARTIFACT_BUNDLE" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE"
.venv/bin/python scripts/verify_release_attestation.py \
  --evidence "$REHEARSAL_EVIDENCE_FILE" \
  --artifact-bundle "$REHEARSAL_EVIDENCE_ARTIFACT_BUNDLE" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE"
.venv/bin/python scripts/run_pre_ga_release.py compare \
  --automation-evidence "$AUTOMATED_EVIDENCE_FILE" \
  --rehearsal-evidence "$REHEARSAL_EVIDENCE_FILE"
```

Expected: both signatures/objects/Artifacts pass, assertions have zero failures, comparable build/image/schema/locks/scenario/runner/trust identities match, and summaries byte-match verified projections.

- [ ] **Step 8: Prove the complete live-profile gate executed without repeating the rehearsal**

The one-time production rehearsal is not repeated. The already completed automated run and rehearsal remain the qualifying evidence. If organizational policy requires a final automated rerun, it must use the same source/image bundle and may create a new automated evidence manifest, but that changes the selected automation digest and therefore requires candidate inputs to use the new verified pair; it does not authorize a second rehearsal for the same attempt subject.

Run the runner's read-only completeness verifier:

```bash
.venv/bin/python scripts/run_pre_ga_release.py profile verify-complete-run \
  --qualification-target "$MINDATLAS_QUALIFICATION_TARGET_FILE" \
  --automation-evidence "$AUTOMATED_EVIDENCE_FILE" \
  --rehearsal-evidence "$REHEARSAL_EVIDENCE_FILE" \
  --scenario-set release/scenarios/pre_ga_launch.v1.json \
  --required-e2e-module tests/test_release_qualification_e2e.py \
  --source-revision "$RELEASE_SOURCE_REVISION"
```

This verifier checks the signed assertion inventory, run provenance, safe JUnit/scenario Artifact digests, exact required service identities, both Worker identities, scenario-set mutation digest, and an attested zero exit from the live E2E module in each qualifying run. It fails if any required service/scenario/assertion/test case was absent or skipped. It does not replay a transcript as if infrastructure succeeded and does not start a second rehearsal.

- [ ] **Step 9: Run a disposable production-class negative acceptance database**

Before creating a candidate on the real target, take a transaction-consistent prelaunch database stream from the quiescent target directly into a new isolated disposable PostgreSQL database (no durable dump file), attach a fresh empty Artifact prefix, and deploy the exact OCI bundle with `--no-build`. The clone preserves the exact qualification IDs/digests and control revision 0. It has no rehearsal authorization/Scripted transport; any mounted authorization must fail startup. Production-class negative cases operate at candidate/control/readiness/repository boundaries and do not require Provider I/O.

The clone command is release-harness-only, runs on the self-hosted Operator host over local protected connections, never logs the stream, and verifies source/destination marker and target digests before tests. It is not a product restore capability, rollback procedure, or Legacy restore; no restore endpoint/script is shipped to production.

```bash
cd backend
.venv/bin/python scripts/run_pre_ga_release.py production-clone negative-acceptance \
  --source-database-url-fd "$MINDATLAS_PRODUCTION_DATABASE_URL_FD" \
  --run-dir "$MINDATLAS_PRODUCTION_CLONE_RUN_DIR" \
  --qualification-target "$MINDATLAS_QUALIFICATION_TARGET_FILE" \
  --automation-evidence "$AUTOMATED_EVIDENCE_FILE" \
  --rehearsal-evidence "$REHEARSAL_EVIDENCE_FILE" \
  --oci-bundle "$RELEASE_OCI_BUNDLE"
```

Use normal HTTP/service APIs plus database clock control available only to the disposable harness to prove:

- before consume, readiness/admission/write return `pre_ga_launch_unapproved`;
- a valid passing candidate expires unused at exactly 24 hours and cannot consume;
- a stale-subject candidate cannot consume after durable identity change;
- CAS/request-ID conflicts leave no use/control/audit residue;
- a consumed candidate remains launched after 48 hours;
- Worker loss changes readiness but not launch control;
- new unresolved call blocks new writes but not launch control;
- every durable subject/evidence identity dimension invalidates launch;
- reconciliation remains available.

Destroy this database/volumes after signed safe assertions and prove no dump stream/file remains. Do not copy its candidate/use/control into the target.

- [ ] **Step 10: Revalidate the production target staged before qualification**

Require the same isolated database/Artifact prefix and imported application images from Task 11. Verify its database was originally fresh, migration/init/activation audit chain is intact, deployment class is explicitly `production`, and no product/config/trust/image change occurred while automation/rehearsal ran. Any Legacy schema/revision, unknown identity, shared Artifact prefix, or unexpected Entry/Run/call/candidate/use stops; do not rebaseline, restore, import Legacy, or delete data automatically.

- [ ] **Step 11: Verify exact `0002`, target digest, and empty launch state**

Run runtime verification and compare the server-derived target with both signed evidence manifests:

```bash
docker compose -f "$PRODUCTION_COMPOSE_FILE" exec -T api \
  python scripts/verify_pre_ga_schema.py runtime \
  --database-url-env DATABASE_URL
cd backend
.venv/bin/python scripts/run_pre_ga_release.py target verify \
  --base-url "$MINDATLAS_PRODUCTION_BASE_URL" \
  --qualification-target "$MINDATLAS_QUALIFICATION_TARGET_FILE" \
  --automation-evidence "$AUTOMATED_EVIDENCE_FILE" \
  --rehearsal-evidence "$REHEARSAL_EVIDENCE_FILE"
```

Expected: one Alembic version `pre_ga_v1_0002`; production marker/application/control/runtime identity is exact; qualification-target digest equals both manifests; launch control revision is 0; candidate/use/Run/call/Entry/unresolved state is empty; no archived/Legacy object exists.

- [ ] **Step 12: Verify exact running API/MinIO/Web/two-Worker deployment remains fail-closed**

Inspect the already running imported images (`--no-build`), production deployment manifest, public trust set, enforced ledger, durable Interrupts, write ceiling `create_entry`, and absence of rehearsal authorization/transport. Both Workers must have distinct fresh compatible registrations; Scripted Provider is not part of production. `/health=200` and `/ready=503 pre_ga_launch_unapproved`.

Paid/live Provider probe remains disabled as qualification evidence. The configured production Model identity is the exact target-bound value exercised via Scripted transport in automation/rehearsal; actual Provider reachability remains an operational health concern, not a release assertion.

- [ ] **Step 13: Reauthenticate the existing singleton Operator by password**

Log out any old Session, login through the real HttpOnly Cookie flow using the password established in Task 11, verify Session rotation/CSRF/origin policy, and retain a current Operator Session for candidate/consume. Confirm Setup Token reuse remains rejected, the immutable singleton Operator ID matches the initialization/audit chain, and `OPERATOR_AUTH_CONTRACT_VERSION` matches the qualification target. Password hash/revision and live Session state are intentionally not target/launch-subject fields—normal protected password rotation revokes Sessions but does not require product requalification—although this controlled launch performs no rotation between evidence and consumption. Raw token/password/hash/cookies never enter evidence.

- [ ] **Step 14: Revalidate active rollout and prelaunch readiness without changing it**

Inspect the already active rollout/closure and Worker compatibility. Verify Profile/Model/Package/Capability/seed/build/runtime/codec `3`/feature/create/write/cohort/reconciliation digests equal `ReleaseQualificationTargetV1` and both evidence manifests. Do not prepare/activate another revision between evidence and candidate creation.

Before candidate creation/consumption:

```text
GET /health -> 200
GET /ready  -> 503 with pre_ga_launch_unapproved
new Chat admission -> 503, no Message/Run
new create_entry proposal -> 503, no CapabilityCall/Entry
```

Initialization/login/admin/reconciliation/Worker/rollout/candidate endpoints remain usable.

- [ ] **Step 15: Create a server-derived passing candidate from the two evidence refs**

Use Operator+CSRF and a fresh request ID/reason; submit only the automated/rehearsal `ContentAddressedEvidenceRef` values. The server fetches/verifies objects, derives current production subject and zero-count snapshot, and inserts one immutable candidate.

Require:

- `passed=true`, empty safe failure codes;
- schema current production runtime identity plus matching rehearsal comparable material;
- exact `qualification_target_digest`, release source/build/application-image/deployed Artifact/schema/auth/rollout closure/locks/scenario/required-assertion/runner/trust/evidence digests;
- automated/rehearsal `QualificationInfrastructureIdentityV1` values match each other but remain absent from the production subject/control;
- unknown/reconciliation/active-Run counts all zero;
- database `expires_at - issued_at = 24 hours`;
- no client-authored decision/subject/time field.

- [ ] **Step 16: Consume the candidate within 24 hours using Operator Session/CSRF/CAS**

Immediately reload status, then POST candidate consume with current expected control revision, fresh request ID, and bounded reason. Under locks the server rechecks evidence, subject, database time, and all counts; appends one gate use; advances control `0 -> 1`; and appends Operator audit in one transaction.

Verify exact replay returns the same use/result without another revision. Do not wait near expiry; record database issue/use times proving consumption occurred within the allowed window.

- [ ] **Step 17: Prove launch/current readiness and no automatic expiry shutdown**

After consumption with both Workers compatible:

```text
launch status -> current, control revision 1
GET /ready -> 200
new Chat admission -> allowed by launch/readiness
create_entry write guard -> allowed only after exact call approval
```

The disposable production-class acceptance in Step 9 proves +48-hour semantics; do not change the real production database clock. On the target, inspect evaluator inputs to confirm candidate expiry is absent from current control evaluation.

- [ ] **Step 18: Execute one real post-launch `create_entry` smoke**

Through the normal Main Agent Chat boundary and configured production Model, request a harmless clearly labeled launch-verification Entry. This is an operational smoke, not qualifying Provider evidence. Inspect the call-owned approval card, approve with current Operator Session/CSRF, and wait through SSE for terminal success.

Verify transactionally:

- exactly one supported `create_entry` CapabilityCall and one call-owned approval Interrupt;
- exactly one successful Attempt and one result Artifact/checkpoint/obligation settlement;
- exactly one Entry with `source_capability_call_id=call.id`;
- no Relation, update, merge, fallback, second Entry, unresolved call, or retry;
- duplicate safe replay returns the same result/Entry;
- readiness remains current.

Do not record the Chat prompt, approval fields, Entry title/body, Provider request/response, or Artifact content in launch evidence.

- [ ] **Step 19: Exercise one reversible Worker-liveness readiness check**

Stop Worker A and wait for its registration to expire while Worker B remains compatible: readiness may remain true because one compatible Worker exists. Then stop Worker B and prove readiness becomes `worker_unavailable` while launch status/control stays current. Restart both exact Workers and prove readiness returns without a new candidate/use/control revision.

Do not create a real unresolved call or durable identity drift on the target; those fail-closed cases were proven in signed automation/rehearsal/disposable acceptance.

- [ ] **Step 20: Generate the safe pre-GA launch evidence projection**

Use a read-only authenticated generator that recomputes schema/deployment/closure/launch state and cross-checks database constraints. The JSON contains:

- schema version/evidence kind and `RELEASE_SOURCE_REVISION`;
- qualification-target, deployed application-image/Artifact/lock/schema/runtime/closure/subject digests;
- automation/rehearsal manifest and attestation digests plus trust key IDs;
- candidate/use/control IDs and subject digest;
- candidate issued/expiry and use times, proving under 24 hours;
- expected/resulting control revisions and current status;
- readiness before/after safe reason/status;
- Worker compatibility count/contract digest, not heartbeat details;
- create-entry call/Entry/result reference digests or safe IDs/counts, not content;
- Operator audit event digest/count, not session/token/reason;
- verification command versions/results and UTC generation time.

It excludes all secrets/content/raw idempotency/signatures/evidence bodies/database URLs and does not claim soak.

- [ ] **Step 21: Reverify live target and evidence projection**

Run:

```bash
cd backend
.venv/bin/python scripts/verify_pre_ga_schema.py runtime \
  --database-url-env MINDATLAS_PRODUCTION_DATABASE_URL
.venv/bin/python scripts/run_pre_ga_release.py launch verify \
  --base-url "$MINDATLAS_PRODUCTION_BASE_URL" \
  --summary ../docs/superpowers/evidence/2026-07-28-pre-ga-launch.json
.venv/bin/python scripts/verify_release_attestation.py \
  --evidence "$AUTOMATED_EVIDENCE_FILE" \
  --artifact-bundle "$AUTOMATED_EVIDENCE_ARTIFACT_BUNDLE" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE"
.venv/bin/python scripts/verify_release_attestation.py \
  --evidence "$REHEARSAL_EVIDENCE_FILE" \
  --artifact-bundle "$REHEARSAL_EVIDENCE_ARTIFACT_BUNDLE" \
  --trust-set "$MINDATLAS_RELEASE_TRUST_SET_FILE"
```

Expected: schema/runtime and both signed evidence objects verify; live launch subject/control/current evidence projection match; readiness is healthy with Workers; unresolved counts are zero; create smoke shows one Entry.

- [ ] **Step 22: Run final secret/content and product-drift checks**

Scan the three repository evidence files and staged diff for all generated sentinel/secrets/content. Then run:

```bash
git diff --exit-code "$RELEASE_SOURCE_REVISION" -- \
  backend/app backend/alembic backend/requirements backend/Dockerfile \
  backend/scripts frontend/src frontend/package.json frontend/package-lock.json \
  frontend/Dockerfile deploy .github/workflows
git diff --check
```

Expected: only safe evidence JSON files differ from release source; no product drift or whitespace error; evidence scan passes.

- [ ] **Step 23: Commit only the safe launch evidence**

```bash
git add docs/superpowers/evidence/2026-07-28-pre-ga-launch.json
git commit -m "docs(evidence): record pre-GA launch acceptance"
```

Do not rebuild/redeploy because of this evidence-only commit. The running build remains the exact rehearsed `RELEASE_SOURCE_REVISION` recorded in all three evidence files.

---

## Plan Exit Gate

This plan is complete only when every Task commit exists in order and all of the following are simultaneously true:

- Agent production write inventory is exactly `{create_entry}` across ToolRegistry, Provider schema, Assistant exports, trusted seed/Skill, system assets, workflows, and OpenClaw; normal human Entry/Relation REST remains Session/CSRF protected.
- Unsupported update/merge/relation/follow-up attempts return `capability_not_supported` before CapabilityCall/Entry/Relation/Artifact/Interrupt and never substitute `create_entry`.
- `create_entry` executes only through frozen Manifest/authorization/CapabilityCall/call-owned approval/server HMAC/local transactional adapter; duplicate and fault schedules create at most one Entry.
- Post-approval guard, unresolved advisory lock, commit-ambiguity classification, no-auto-retry, authenticated signed-evidence reconciliation, and safe replay are proven on PostgreSQL with two Sessions/two Workers.
- Python 3.11 API/Assistant Worker and parse Worker locks are pinned/hashed/reproducible; LangGraph is `0.3.34`; two clean installs, `pip check`, import smoke, Docling/Transformers/HF Hub/Torch compatibility, Docker/CI lock use, and combined digest pass.
- No test permanently replaces FastAPI/Starlette or another lock-owned package; forward/reverse/isolated/three seeded orders and full suite pass with the Plan 3 tombstone intact.
- Release scenarios, Scripted Provider, allowlisted Artifact collector, Ed25519 evidence/trust set, content addressing, and rehearsal-only authorization pass adversarial tests; production rejects rehearsal authority.
- Alembic has sole root `pre_ga_v1_0001`, sole head `pre_ga_v1_0002`, exact untouched root/archive, generated structural/control/runtime identity, immutable candidate/use state, revisioned control, and no Legacy edge.
- Candidate pass/failure is server-derived; request replay/CAS/audit are atomic; unused expiry is exactly 24 database hours; current consumed launch ignores expiry; volatile Worker/Run/unresolved state is separated from durable subject exactly as specified.
- Standalone automation executes PostgreSQL, MinIO, API, two distinct Workers, Scripted Provider, Web, full faults/scenarios/order modes, clones the server-derived prelaunch qualification target exactly, and produces passing target-bound signed automation evidence without a release-critical skip.
- The one selected stable target/build/application-Artifact/lock/scenario/required-assertion/runner/trust subject has exactly one attempted production-shaped rehearsal, reuses the exact OCI bundle without rebuild, runs the same complete matrix/target closure, and produces passing signed rehearsal evidence verified offline and by the target container.
- Fresh production target is exact `0002`, initialized with one Operator password/one-time Setup Token, authenticated by HttpOnly Session/CSRF, rollout active, prelaunch fail-closed, candidate consumed within 24 hours, control current, readiness healthy, and one approved `create_entry` converges to one Entry.
- All three safe evidence summaries verify and contain no secret/content/raw identity material or soak/Legacy claim.

## Operational Rollback Boundary

- If target-bound automation or the one-shot rehearsal fails after production initialization/activation, keep launch control at revision 0, new Chat/writes blocked, and preserve safe failure evidence. The failed stable subject is not retried; correct the product/config through a new reviewed target subject and repeat automation plus its one allowed rehearsal.
- Before candidate consumption, leave writes/new Chat blocked, discard the unused candidate by non-use, and deploy only a newly automated/rehearsed subject if product bytes/identity change. Candidates are immutable; they are never edited into passing state.
- After consumption, there is no time-based automatic rollback and no destructive database downgrade. To contain an incident, use authenticated durable `new_runs_enabled=false`, process write ceiling `off`, and ordinary service stop while preserving login/admin/reconciliation/evidence access. Record the Operator/audit action.
- If Worker liveness is lost, restore an exact compatible Worker; launch control remains current but readiness stays false until liveness returns.
- If unresolved writes appear, keep new writes blocked and resolve them through authenticated CSRF/signed-evidence reconciliation; do not clear rows or retry unknown effects directly.
- If durable subject/evidence identity drifts, launch becomes stale automatically. Restore the exact verified deployment bytes/config/trust set when that is the intended state, or complete new automation plus a new one-time rehearsal subject, create a new candidate, and consume it by CAS.
- `pre_ga_v1_0002 -> pre_ga_v1_0001` is test-only, empty/uninitialized cleanup. It is never a production rollback and never reconnects Legacy. A production data restore from Legacy is unsupported.
- Evidence-only documentation commits do not alter/deploy runtime bytes; the deployed build remains the recorded release source revision.

## Implementation Stop Conditions

Stop the current implementation/release step and preserve diagnostics safely if any of these occurs:

- worktree/product bytes differ from the reviewed release source or an image/lock/manifest cannot be reproduced;
- Tool/Provider/seed/asset inventory exposes another Agent write or human REST protection is removed;
- direct Provider declaration writes, approval is not exact call-owned, a duplicate can create two Entries, or post-approval failure can start a side effect;
- unresolved transitions/proposals are not serialized, commit ambiguity auto-retries, or reconciliation can use configured/CLI/caller identity or unsigned evidence;
- a lock is unpinned/unhashed, resolver changes LangGraph line without review, clean install/import/`pip check`/ABI tests fail, or build uses a secondary unreviewed index;
- global module/cache/environment pollution remains or test order changes outcomes;
- release assertion/outcome/pass can be supplied by client, evidence is unsigned/noncanonical/not content-addressed, trust/private-key separation fails, or safe output contains secret/content;
- PostgreSQL/MinIO/two Workers/Web/Scripted Provider are missing, skipped, mocked, or silently replaced;
- root/archive changes, migration is not exact `0001 -> 0002`, schema marker/runtime identity differs, nonempty `0001` is auto-reinterpreted, or any Legacy path is required;
- candidate/control constraints, request replay, advisory locks, expiry, subject/snapshot separation, or atomic audit cannot be proven under PostgreSQL concurrency;
- production accepts rehearsal authorization/flag authority, candidate is consumed after expiry, current subject differs, or counts are nonzero at creation/consumption;
- one-shot rehearsal receipt already exists, rehearsal needs rebuild/scenario changes, or any rehearsal assertion fails;
- production target is not fresh/isolated, candidate is not server-passing, `/ready` becomes true before control use, or create smoke does not converge to exactly one Entry;
- a requested “fix” would weaken a hard assertion, add a skip/bypass/force path, expose sensitive evidence, restore Legacy, or claim soak not actually performed.

## Authoring Self-Review Record

- Scope/order check: 12 sequential Tasks cover write-surface closure, write guard, transactional approval/recovery/reconciliation, Python locks, test isolation, evidence/trust, `0002` schema, candidate/control enforcement, full automation, Operator UI/observability, one-time rehearsal, and final production acceptance.
- Task structure check: every Task names exact Create/Modify/Delete/Verify paths, explicit Consumes/Produces interfaces, checkbox steps at implementation granularity, red/green or acceptance evidence, exact commands/expected results, `git diff --check`, and one independently reviewable commit.
- Contract check: `OperatorPrincipal`, HttpOnly Session/CSRF, Operator audit, Profile V2, active rollout/revision/closure, checkpoint codec `3`, Worker feature identity, `RuntimeSchemaCompatibility`, create/write/cohort/reconciliation fields, and `Entry.source_capability_call_id` keep one canonical producer/consumer chain.
- Cross-plan handoff check: Plan 1 owns `operator-auth-v1` and the sole initialization transaction; Plan 2 owns Profile V2, non-circular runtime closure, activation/readiness/admission and codec `3`; Plan 3 owns sole root `pre_ga_v1_0001`, exact 60-file archive and schema identity; this Plan only extends those contracts and owns `pre_ga_v1_0002` plus launch state.
- Schema check: Plan 3 root/archive remain untouched; `pre_ga_v1_0002` has exact parent `pre_ga_v1_0001`; launch tables/constraints/triggers, schema marker guard, two-pass identity, runtime exact compatibility, fresh-only upgrade, and test-only empty downgrade are explicit.
- Launch semantics check: durable subject and operational snapshot are separate; pass/expiry/counts are server/database-derived; consume rechecks under locks/CAS; consumed expiry is ignored; liveness affects readiness only; unresolved state affects new writes only; durable/evidence drift invalidates launch.
- Launch concurrency check: launch, write-safety, runtime, control, and Run/call lock order is total; active-rollout and mutable Model/credential changes join the launch lock; immutable/config-redeploy identities have no live mutation route; candidate/admission races are explicitly tested.
- Target-order check: the fresh production target is migrated, initialized, and rollout-activated while launch-blocked before final automation/rehearsal; `ReleaseQualificationTargetV1` is server-derived; both evidence manifests bind its exact closure; only then is a candidate created/consumed.
- Rehearsal initialization check: target provisioning never writes the database directly; the signed fixture feeds deterministic IDs/config into the real Setup-authorized coordinator transaction, and dynamic authorization verifies the resulting active closure before any Run/write.
- Evidence check: exact manifest fields, fixed scenarios/server outcomes, content addressing, Ed25519 domains, runner-only private keys, API public trust set, comparable rehearsal schema material versus exact production runtime identity, separate evidence-only qualification-infrastructure identity, stable one-shot attempt subject/append-only receipt, same image bundle, Artifact-first/manifest-last promotion into an append-only production store, offline/API verification, and safe Artifact allowlist are explicit.
- Safety check: no Router/Supervisor or Legacy fallback/restore/rebaseline path; no extra Agent write; no configured/CLI Operator identity; no paid Provider result as qualification evidence; no secret/content/raw idempotency/signature/database URL in evidence/log/metric/audit.
- Verification check: focused/full backend/frontend, two clean installs, `pip check`, import/ABI, PostgreSQL concurrency/migrations, MinIO/two-Worker profile, test-order modes, generator byte checks, signed evidence, disposable production-class negative acceptance, live readiness, and one-Entry smoke all have commands and expected outcomes.
- Rollback check: pre-consume non-use, authenticated kill switches, liveness recovery, signed reconciliation, durable drift fail-closed/new evidence flow, evidence-only commits, and no production downgrade/Legacy restore are explicit.

---

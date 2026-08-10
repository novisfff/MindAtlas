# Pre-GA Clean Schema Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unpublished 60-revision development lineage with one generated, audited, family-bound `pre_ga_v1` root that installs the complete clean schema directly, proves structural equivalence after an exact Legacy-object exclusion manifest, rejects unknown or drifted databases, and gives API and Worker processes one fail-closed runtime schema compatibility contract.

**Architecture:** A PostgreSQL-catalog canonicalizer produces exact version-1 physical evidence and SHA-256 fingerprints. Before the squash, it captures the exact `b6e2d4f8a901` schema, exact Legacy exclusion definitions, and every retained function/trigger definition. An explicitly versioned logical projection removes only verified controls and physical dropped-column numbering, producing the version-2 application contract used by the deterministic root generator and runtime. Disposable Database A (old chain) and Database B (staged root) must have byte-equal version-2 application documents after exact exclusions and independently validated controls; their raw version-1 physical documents are evidence and may differ. One atomic archive activation moves all 60 old revisions outside Alembic's configured version location and makes the clean root the sole live revision. Fresh install is the default; a separate non-production-only rebaseline command may remove only verified, empty-of-business-state exclusion objects and stamp only after the resulting schema and retained data are proven unchanged. Runtime compatibility recomputes family, revision, logical application fingerprint, control fingerprint, seed/runtime contracts, deployment class, and code compatibility before API readiness or Worker claims.

**Tech Stack:** Python 3.11, PostgreSQL 15 catalogs, SQLAlchemy 2, Alembic autogenerate/render APIs, Pydantic v2, canonical JSON, SHA-256, pytest, Docker Compose, POSIX shell, GitHub Actions.

## Global Constraints

- Implement from approved design commit `ca925eeba569357ddb2c5c3aa63554b391efd21b`, completed Plan 1 document commit `98accdb`, completed Plan 2 document commit `2eb1006`, and the reviewed implementations of Plans 1 and 2. A document commit is not an implementation prerequisite by itself.
- The known pre-squash head is exactly `b6e2d4f8a901`, whose parent is `9f3c1a7e2b40`; the old lineage contains exactly 60 revisions after Plans 1 and 2 are implemented.
- The supported schema family starts at `pre_ga_v1`; `pre_ga_v1_0001` has `down_revision = None` and becomes the sole configured Alembic root and head at this plan's exit.
- Plan 4 owns additive revision `pre_ga_v1_0002` with parent `pre_ga_v1_0001`. This plan must not create Plan 4 tables or edit the root after Plan 4 starts.
- No Legacy in-place upgrade, Legacy restore, Legacy runtime fallback, Router/Supervisor schema, create-then-drop transition, or second Alembic branch is supported.
- Default development migration is reset plus fresh `alembic upgrade head`. Guarded rebaseline is an exceptional local-maintenance path, never an automatic deployment path.
- `deploy/migrate.sh` must never stamp a non-empty unversioned database and must never infer compatibility from table presence.
- Canonical structural identity comes from PostgreSQL catalogs, not only SQLAlchemy Inspector and not text search over migration files.
- Canonicalization includes namespaces, extensions, enums/domains, sequences, views/materialized views, tables/columns, constraints, indexes, functions with full bodies, and triggers with timing/events/enabled state/function linkage.
- Canonicalization excludes owner, OID, ACL, comments, statistics, physical page/storage parameters, row counts, data, and timestamps. Every remaining field is structural identity.
- SQL expressions come from `pg_get_expr`, function definitions from `pg_get_functiondef`, view definitions from `pg_get_viewdef`, index definitions from `pg_get_indexdef`, constraint definitions from `pg_get_constraintdef`, and trigger definitions from `pg_get_triggerdef`.
- Arrays and object lists use explicit stable sort keys. JSON serialization is UTF-8, `sort_keys=True`, `ensure_ascii=False`, and separators `(',', ':')`; SHA-256 is lowercase hexadecimal.
- Database A exclusions are accepted only when every exact object identity and its definition digest match the committed manifest. Any missing, additional, renamed, or drifted exclusion object fails closed.
- Database B must contain none of the Legacy exclusion objects. The schema identity table and its guard function/trigger are validated by their own committed control-plane contract and are excluded only from the A/B application-schema comparison.
- The schema marker contains no credential, Operator secret, password hash, Setup/Session/CSRF token, Prompt, Provider payload, Entry/Artifact content, or raw request key.
- `deployment_class` is exactly one of `development`, `rehearsal`, or `production`; it is immutable for a database. A database is never inferred to be non-production from a hostname alone.
- Runtime schema identity binds family, exact revision, application structural fingerprint, expected seed contract digest, deployment class, runtime contract version, checkpoint codec version `3`, Capability feature digest, and Operator auth contract version.
- The clean root is self-contained. Its `upgrade()` may use Python/SQLAlchemy/Alembic standard libraries and literals emitted by the generator; it must not import application services, live ORM models, archived revisions, or network resources.
- Root downgrade is destructive-to-empty only, requires both `APP_ENV=test` and an exact test-only acknowledgement, and refuses any retained business/runtime row. It never reconstructs an old revision.
- API public readiness, authenticated readiness, activation/admission, Worker startup, and every Worker claim retain stable public reason `schema_incompatible`; raw SQL, catalog definitions, or database errors never enter HTTP responses.
- Archive files are non-importable artifacts outside configured `version_locations`; ordinary `alembic upgrade`, application code, and tests cannot execute them.
- The design-deviation record states only accepted facts. It does not claim the old Plan 10 rollout, B1/B2, restore, canary, legacy-zero, or calendar soak occurred.
- Every Task uses red-green-refactor, is split into roughly 2–5 minute implementation/check steps, ends with focused verification, and produces one independently reviewable commit.
- If the old head/count, exact exclusion set, model/catalog equivalence, generated bytes, retained-data invariance, archive chain, family marker, or runtime compatibility cannot be proven, stop and preserve sanitized diagnostics. Never add `--force`, skip the check, widen an exclusion, or stamp by hand.

## Approved 2026-08-05 Contract Amendment

The approved design `docs/superpowers/specs/2026-08-05-pre-ga-clean-logical-schema-baseline-design.md` and implementation plan `docs/superpowers/plans/2026-08-05-pre-ga-clean-logical-application-contract.md` supersede any statement below that requires raw version-1 physical equality between the old chain and clean root.

- Existing pre-squash snapshot, exclusion, and SQL-object manifest bytes remain immutable version-1 historical evidence.
- The new `pre_ga_v1-clean-application-contract.json` contains the canonicalization-version-2 application document and fingerprint.
- Version 2 removes exactly column `ordinal` and index key/include attribute-number arrays after exact Legacy and control validation. It retains all semantic fields and object identities.
- `alembic_version` and Task 6 schema-identity controls are validated by exact stage-specific contracts and excluded from the application document only after validation.
- Task 5 model-reference, Task 6 expected application fingerprint, Task 7 equivalence, Task 9 target fingerprint, Task 10 runtime compatibility, and Task 12 evidence consume the version-2 application contract.
- Raw version-1 fingerprints remain required for source-head capture, archive evidence, and guarded rebaseline source authorization.
- The generic comparator remains byte-exact. Projection is a separate producer and never derives table DDL from a discrepancy.
- The two-task amendment plan executes after Task 4 and before resuming Task 5; its commits are independently reviewable additions to this plan's task sequence.

---

## Prerequisites and Stable Interfaces

### Required checkpoint

Run from the repository root after Plan 1 and Plan 2 implementation commits are present:

```bash
git status --short
git rev-parse HEAD
git log -6 --format='%H %s'
find backend/alembic/versions -maxdepth 1 -type f -name '*.py' | wc -l
cd backend
.venv/bin/alembic heads
.venv/bin/alembic history --verbose | rg '^Rev:|^Parent:'
```

Expected:

- `git status --short` prints nothing;
- exactly 60 Python revision files exist;
- Alembic reports exactly `b6e2d4f8a901 (head)` and no branch point or second head;
- the last links are `3bd7bc4257c9 -> 9f3c1a7e2b40 -> b6e2d4f8a901`;
- Plan 1 and Plan 2 focused/full exit gates pass against PostgreSQL 15;
- no `pre_ga_v1_0001` or `pre_ga_v1_0002` live revision exists.

Stop before editing if any expected fact differs. Record the actual non-secret revision graph and refresh this plan through review rather than changing a constant locally.

### Consumed Plan 1 contracts

The implemented Plan 1 paths export:

```python
# backend/app/operator_auth/constants.py
OPERATOR_AUTH_CONTRACT_VERSION = "operator-auth-v1"


# backend/app/operator_auth/contracts.py
@dataclass(frozen=True)
class OperatorPrincipal:
    operator_id: UUID
    role: OperatorRole
    session_id: UUID
    authentication_method: Literal["password_session"] = "password_session"
```

`backend/app/system_settings/initialization_coordinator.py` remains the sole initialization transaction owner. This plan does not change setup, password, HttpOnly cookie, CSRF, or audit semantics.

### Consumed Plan 2 contracts

The implemented Plan 2 paths export:

```python
# backend/app/assistant/runtime/readiness.py
class RuntimeSchemaCompatibility(Protocol):
    def is_compatible(self, db: Session) -> bool: ...


# backend/app/assistant/durable/codec.py
CURRENT_CHECKPOINT_CODEC_VERSION: Final[int] = 3


# backend/app/assistant/durable/worker_registry.py
RUNTIME_CONTRACT_VERSION: Final[int]

def default_capability_feature_digest() -> str: ...


# backend/app/assistant/runtime/system_seed/expected.py
SEED_CONTRACT_DIGEST: Final[str]
```

Plan 3 replaces only `Plan2AlembicHeadCompatibility` behind the existing `RuntimeSchemaCompatibility` interface. It does not rename the protocol, change `AssistantReadinessService` signatures, or add a new public readiness reason.

Worker startup and claim paths already call the compatibility port. Plan 3 makes those calls family-bound and verifies them with PostgreSQL tests.

### Produced schema identity contract

`backend/app/schema/contracts.py` exports:

```python
SCHEMA_FAMILY = "pre_ga_v1"
PRE_SQUASH_HEAD = "b6e2d4f8a901"
CLEAN_ROOT_REVISION = "pre_ga_v1_0001"
NEXT_RESERVED_REVISION = "pre_ga_v1_0002"
ARCHIVED_REVISION_COUNT = 60
SCHEMA_IDENTITY_SINGLETON_KEY = "current"
SCHEMA_IDENTITY_CONTRACT_VERSION = 1


class DeploymentClass(StrEnum):
    DEVELOPMENT = "development"
    REHEARSAL = "rehearsal"
    PRODUCTION = "production"


@dataclass(frozen=True)
class SchemaRuntimeIdentityMaterial:
    schema_family: str
    schema_revision: str
    structural_fingerprint: str
    seed_contract_digest: str
    deployment_class: DeploymentClass
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    operator_auth_contract_version: str


@dataclass(frozen=True)
class SchemaCompatibilitySnapshot:
    compatible: bool
    safe_reason: Literal["schema_incompatible"] | None
    diagnostic_code: str | None
    schema_family: str | None
    schema_revision: str | None
    deployment_class: DeploymentClass | None
    structural_fingerprint: str | None
    runtime_identity_digest: str | None
```

`diagnostic_code` is internal and bounded to a committed allowlist such as `marker_missing`, `head_mismatch`, `family_mismatch`, `fingerprint_mismatch`, `contract_mismatch`, or `catalog_unavailable`. HTTP responses expose only `schema_incompatible`.

### Fixed marker table

The clean root creates `mindatlas_schema_identity` with exactly one row and these columns:

| Column | Type and rule |
|---|---|
| `singleton_key` | `varchar(32)` primary key, exactly `current` |
| `schema_family` | `varchar(32)`, exactly `pre_ga_v1` |
| `schema_revision` | `varchar(64)`, initially `pre_ga_v1_0001` |
| `structural_fingerprint` | `char(64)`, lowercase SHA-256 |
| `runtime_identity_digest` | `char(64)`, lowercase SHA-256 |
| `seed_contract_digest` | `char(64)`, lowercase SHA-256 |
| `deployment_class` | `varchar(16)`, immutable enum value |
| `runtime_contract_version` | positive integer |
| `checkpoint_codec_version` | positive integer, initially `3` |
| `capability_feature_digest` | `char(64)`, lowercase SHA-256 |
| `operator_auth_contract_version` | `varchar(64)`, exactly the code contract |
| `identity_contract_version` | positive integer, initially `1` |
| `created_at` | `timestamptz`, database time, immutable |
| `updated_at` | `timestamptz`, database time |

The marker guard rejects delete, family/deployment/created-at changes, and arbitrary updates. A later same-family Alembic revision may advance revision/fingerprints only after setting a transaction-local expected-revision guard; Plan 4 uses this path in `pre_ga_v1_0002` and never edits the root.

### Fixed migration sequence and rollback boundary

```text
a7d8f1424a99 ... 3bd7bc4257c9
  -> 9f3c1a7e2b40
  -> b6e2d4f8a901
  -> archive exactly 60 old revisions

pre_ga_v1_0001 (down_revision = None; sole live root/head)
  -> pre_ga_v1_0002 (Plan 4 only)
```

There is no graph edge between `b6e2d4f8a901` and `pre_ga_v1_0001`. The guarded rebaseline tool verifies the old database independently and stamps only after it has become structurally identical to the clean family. Production/unknown/shared databases are rejected.

---

## File Structure

### New schema package and committed manifests

| Path | Responsibility |
|---|---|
| `backend/app/schema/__init__.py` | Export stable identity and compatibility factory only. |
| `backend/app/schema/contracts.py` | Family/revision constants, deployment enum, canonical object and compatibility dataclasses. |
| `backend/app/schema/canonical.py` | Canonical JSON, digest, normalization, sorting, and comparison primitives. |
| `backend/app/schema/catalog.py` | Complete PostgreSQL catalog reader using `pg_get_*` functions. |
| `backend/app/schema/exclusions.py` | Exact old Legacy source allowlist and manifest validation; no runtime selector behavior. |
| `backend/app/schema/sql_objects.py` | Load and validate retained function/trigger registry for generation. |
| `backend/app/schema/identity.py` | Runtime identity material/digest, marker read/validation, marker DDL contract. |
| `backend/app/schema/compatibility.py` | Family-bound `RuntimeSchemaCompatibility` implementation and safe diagnostics. |
| `backend/app/schema/rebaseline.py` | Guarded non-production preflight, data invariants, exact exclusion cleanup, stamp transaction. |
| `backend/app/schema/manifests/pre_ga_v1-exclusions.json` | Generated exact object identities, old definitions/digests, reasons, reference proof, expected absence. |
| `backend/app/schema/manifests/pre_ga_v1-pre-squash-schema.json` | Sanitized canonical old-head schema snapshot and normalized clean application document. |
| `backend/app/schema/manifests/pre_ga_v1-sql-objects.json` | Retained full PostgreSQL function/trigger definitions and creation order. |
| `backend/app/schema/manifests/pre_ga_v1-expected.json` | Expected application fingerprint, marker contract digest, source fingerprint, and contract inputs. |

### Generation, archive, migration, and operational scripts

| Path | Responsibility |
|---|---|
| `backend/scripts/capture_pre_ga_schema.py` | Capture old head, generate/check exclusions, retained SQL objects, and canonical snapshot. |
| `backend/scripts/generate_pre_ga_baseline.py` | Render/check byte-identical staged clean-root migration from live metadata and manifests. |
| `backend/scripts/archive_pre_ga_lineage.py` | Parse, order, move, rename, and manifest exactly 60 revisions; later check archive integrity. |
| `backend/scripts/rebaseline_pre_ga_v1.py` | Expose only `inspect` and `apply` guarded local-maintenance commands. |
| `backend/scripts/schema_database_state.py` | Classify only empty, versioned, or unsupported non-empty unversioned deployment state. |
| `backend/scripts/verify_pre_ga_schema.py` | Run fresh/equivalence/archive/compatibility checks and emit allowlisted evidence. |
| `backend/alembic/baseline_staging/pre_ga_v1_0001_clean_baseline.py` | Generated root before atomic archive activation; removed from staging when activated. |
| `backend/alembic/versions/pre_ga_v1_0001_clean_baseline.py` | Final self-contained sole root revision. |
| `backend/alembic/archive/pre_ga_v1_superseded/manifest.v1.json` | Archive chain metadata, file hashes, old head, reason, and deviation digest. |
| `backend/alembic/archive/pre_ga_v1_superseded/README.md` | Non-executable archive policy and restoration prohibition. |
| `backend/alembic/archive/pre_ga_v1_superseded/*.py.archived` | Exact old bytes under non-importable suffix. |

### Tests and evidence

| Path | Responsibility |
|---|---|
| `backend/tests/test_schema_canonical.py` | Canonical JSON/sorting/digest and comparator unit vectors. |
| `backend/tests/test_schema_catalog_postgres.py` | PostgreSQL catalog coverage for every supported object type/attribute. |
| `backend/tests/test_schema_exclusion_manifest.py` | Exact source allowlist, manifest definitions, absence, and unmanifested-diff failure. |
| `backend/tests/test_schema_baseline_generator.py` | Deterministic generation, anonymous-object rejection, self-contained artifact checks. |
| `backend/tests/test_schema_baseline_migration_postgres.py` | Fresh root upgrade, marker, sole head, and test-only downgrade guard. |
| `backend/tests/test_schema_equivalence_postgres.py` | Database A/B canonical equivalence and expected identity. |
| `backend/tests/test_schema_archive.py` | 60-file chain/digest/config/import isolation verification. |
| `backend/tests/test_schema_rebaseline_postgres.py` | Verify clean-root idempotence and reject clean-root databases as historical rebaseline sources without executing archived revisions. |
| `backend/tests/test_runtime_schema_compatibility.py` | Marker/runtime identity unit matrix and safe failures. |
| `backend/tests/test_runtime_schema_compatibility_postgres.py` | API/Worker family, fingerprint, deployment, and claim checks. |
| `backend/tests/test_deploy_migrate_clean_only.py` | Fresh upgrade and non-empty unversioned database refusal. |
| `docs/superpowers/evidence/2026-07-28-pre-ga-clean-baseline-deviation.md` | Required six-point approved design deviation. |
| `docs/superpowers/evidence/2026-07-28-pre-ga-clean-baseline.json` | Generated safe Plan 3 exit evidence. |

### Existing files modified or removed

| Path | Change |
|---|---|
| `backend/alembic.ini` | Pin the sole live version location to `backend/alembic/versions`. |
| `backend/alembic/env.py` | Register only live ORM metadata; remove Legacy migration-model import. |
| `backend/app/assistant/runtime/readiness.py` | Replace interim head-only compatibility factory without changing the protocol. |
| `backend/app/assistant/worker.py` | Consume family-bound compatibility at startup. |
| `backend/app/assistant/durable/leases.py` | Recheck family-bound compatibility immediately before each claim. |
| `backend/tests/_db.py` | Remove Legacy migration-model registration; register schema identity model only if SQLite tests require it. |
| `deploy/migrate.sh` | Delete automatic stamping; allow only versioned upgrade or empty fresh upgrade. |
| `deploy/docker-compose.yml` | Set explicit deployment class for development profile and keep migration failure fatal. |
| `backend/.env.example` | Document deployment class and test-only downgrade acknowledgement safety. |
| `.github/workflows/ci.yml` | Replace old-chain downgrade job with clean-root, archive, equivalence, and wrong-family PostgreSQL gates. |
| `backend/app/assistant/migration/` | Delete the entire unpublished Plan 10 migration/runtime package after capture. |
| `backend/tests/fixtures/ai_runtime_migration/` | Delete obsolete Legacy migration fixtures after their non-execution facts are recorded. |
| Legacy Plan 10 test files listed in Task 4 | Delete tests that execute unsupported migration/rollout/restore behavior; retain `test_ai_runtime_legacy_cleanup.py` only as a no-import tombstone/order-isolation test required by Plan 4. |

---

### Task 1: Freeze Family, Exclusion, Identity, and Deviation Contracts

**Files:**

- Create: `backend/app/schema/__init__.py`
- Create: `backend/app/schema/contracts.py`
- Create: `backend/app/schema/exclusions.py`
- Create: `backend/tests/test_schema_contracts.py`
- Create: `backend/tests/test_schema_exclusion_manifest.py`
- Create: `docs/superpowers/evidence/2026-07-28-pre-ga-clean-baseline-deviation.md`

**Interfaces:**

- Consumes: fixed old head/count, `pre_ga_v1` family decision, Plan 1 auth contract, Plan 2 seed/runtime/codec/feature contracts, and design §16.
- Produces: one canonical family/revision vocabulary, immutable deployment-class enum, exact old Legacy source allowlist, schema identity material, and the required six-point deviation record.

- [ ] **Step 1: Write failing constant and deployment-class tests**

```python
def test_pre_ga_family_and_revision_boundary_is_exact():
    assert SCHEMA_FAMILY == "pre_ga_v1"
    assert PRE_SQUASH_HEAD == "b6e2d4f8a901"
    assert CLEAN_ROOT_REVISION == "pre_ga_v1_0001"
    assert NEXT_RESERVED_REVISION == "pre_ga_v1_0002"
    assert ARCHIVED_REVISION_COUNT == 60


def test_deployment_class_is_closed():
    assert tuple(item.value for item in DeploymentClass) == (
        "development",
        "rehearsal",
        "production",
    )
    with pytest.raises(ValueError):
        DeploymentClass("staging")
```

- [ ] **Step 2: Run the focused tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_schema_contracts.py -q
```

Expected: collection fails because `app.schema.contracts` does not exist.

- [ ] **Step 3: Create the frozen contract module**

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

SCHEMA_FAMILY = "pre_ga_v1"
PRE_SQUASH_HEAD = "b6e2d4f8a901"
CLEAN_ROOT_REVISION = "pre_ga_v1_0001"
NEXT_RESERVED_REVISION = "pre_ga_v1_0002"
ARCHIVED_REVISION_COUNT = 60
SCHEMA_IDENTITY_SINGLETON_KEY = "current"
SCHEMA_IDENTITY_CONTRACT_VERSION = 1


class DeploymentClass(StrEnum):
    DEVELOPMENT = "development"
    REHEARSAL = "rehearsal"
    PRODUCTION = "production"


@dataclass(frozen=True)
class SchemaRuntimeIdentityMaterial:
    schema_family: str
    schema_revision: str
    structural_fingerprint: str
    seed_contract_digest: str
    deployment_class: DeploymentClass
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    operator_auth_contract_version: str


@dataclass(frozen=True)
class SchemaCompatibilitySnapshot:
    compatible: bool
    safe_reason: Literal["schema_incompatible"] | None
    diagnostic_code: str | None
    schema_family: str | None
    schema_revision: str | None
    deployment_class: DeploymentClass | None
    structural_fingerprint: str | None
    runtime_identity_digest: str | None
```

Validate every digest with `re.fullmatch(r"[0-9a-f]{64}", value)` in `__post_init__`. Validate positive integer contract/codec values and exact family/revision syntax. Export only stable types/constants from `app/schema/__init__.py`.

- [ ] **Step 4: Encode the exact Legacy source allowlist**

```python
LEGACY_TABLE_NAMES = (
    "assistant_runtime_migration_item",
    "assistant_runtime_migration_event",
    "assistant_runtime_migration_batch",
    "assistant_runtime_rollout_revision",
    "assistant_runtime_rollout_event",
    "assistant_runtime_rollout_control",
    "assistant_runtime_rollout_assignment",
    "assistant_runtime_admission_fallback_event",
    "assistant_runtime_shadow_comparison",
    "assistant_legacy_approval_archive",
    "assistant_runtime_cleanup_gate",
)

PLAN10_IMMUTABLE_TABLES = (
    "assistant_runtime_rollout_revision",
    "assistant_runtime_rollout_assignment",
    "assistant_runtime_admission_fallback_event",
    "assistant_legacy_approval_archive",
    "assistant_runtime_cleanup_gate",
    "assistant_runtime_migration_event",
    "assistant_runtime_rollout_event",
)

PLAN10_UPDATE_ONLY_TABLES = (
    "assistant_runtime_shadow_comparison",
)

LEGACY_FUNCTION_KEYS = (
    ("function", "public", "mindatlas_reject_plan10_immutable_mutation", ""),
)


def expected_legacy_object_keys() -> tuple[tuple[str, str, str, str], ...]:
    keys = [("table", "public", name, "") for name in LEGACY_TABLE_NAMES]
    keys.extend(LEGACY_FUNCTION_KEYS)
    for name in PLAN10_IMMUTABLE_TABLES:
        keys.append(("trigger", "public", f"trg_{name}_reject_update", name))
        keys.append(("trigger", "public", f"trg_{name}_reject_delete", name))
    for name in PLAN10_UPDATE_ONLY_TABLES:
        keys.append(("trigger", "public", f"trg_{name}_reject_update", name))
    return tuple(sorted(keys))
```

The 11 table records contain their columns, constraints, indexes, and owned sequence references inside each table definition digest. The manifest also has 15 trigger records and one function record, for 27 exact top-level exclusion identities. The capture command must prove the generated key set equals this source set exactly; it may not accept a prefix or glob.

- [ ] **Step 5: Write the failing allowlist cardinality and no-prefix tests**

```python
def test_legacy_source_allowlist_is_exact():
    keys = expected_legacy_object_keys()
    assert len(keys) == 27
    assert len(set(keys)) == 27
    assert all("*" not in part for key in keys for part in key)
    assert all(not part.endswith("_") for key in keys for part in key if part)


def test_plan4_revision_is_reserved_not_live():
    assert NEXT_RESERVED_REVISION != CLEAN_ROOT_REVISION
    assert not (ALEMBIC_VERSIONS / "pre_ga_v1_0002.py").exists()
```

- [ ] **Step 6: Add the exact design-deviation record**

Create the Markdown evidence file with this factual body:

```markdown
# Pre-GA Clean Baseline Design Deviation

1. MindAtlas had not launched when Legacy code and schema were removed.
2. The current clean schema is the first supported `pre_ga_v1` baseline.
3. Legacy in-place upgrade and Legacy restore are unsupported.
4. The original Plan 10 production canary, legacy-zero, restore, B1/B2 sequence, and calendar soak were not executed and are not claimed.
5. Full deterministic automation and one production-shaped rehearsal replace those omitted pre-launch operational gates.
6. This deviation changes the release baseline and does not retroactively mark the original Plan 10 checklist complete.
```

Do not add checked boxes, completion dates for the omitted sequence, or language implying historical execution.

- [ ] **Step 7: Test deviation content and digestability**

```python
def test_deviation_record_has_exact_accepted_facts():
    text = DEVIATION_PATH.read_text("utf-8")
    assert text.count("\n1.") == 1
    assert all(f"\n{number}." in text for number in range(1, 7))
    assert "were not executed and are not claimed" in text
    assert "does not retroactively mark" in text
    assert hashlib.sha256(text.encode("utf-8")).hexdigest().islower()
```

- [ ] **Step 8: Run focused tests and static scans**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_schema_contracts.py \
  tests/test_schema_exclusion_manifest.py -q
cd ..
rg -n 'Plan 10|Legacy|B1/B2|calendar soak' \
  docs/superpowers/evidence/2026-07-28-pre-ga-clean-baseline-deviation.md
git diff --check
```

Expected: tests pass; the scan shows all six accepted facts and no claim of execution; `git diff --check` prints nothing.

- [ ] **Step 9: Commit**

```bash
git add \
  backend/app/schema/__init__.py \
  backend/app/schema/contracts.py \
  backend/app/schema/exclusions.py \
  backend/tests/test_schema_contracts.py \
  backend/tests/test_schema_exclusion_manifest.py \
  docs/superpowers/evidence/2026-07-28-pre-ga-clean-baseline-deviation.md
git commit -m "feat(schema): freeze pre-ga baseline contracts"
```

---

### Task 2: Build the Complete PostgreSQL Canonical Introspector

**Files:**

- Create: `backend/app/schema/canonical.py`
- Create: `backend/app/schema/catalog.py`
- Create: `backend/tests/test_schema_canonical.py`
- Create: `backend/tests/test_schema_catalog_postgres.py`
- Modify: `backend/app/schema/contracts.py`

**Interfaces:**

- Consumes: PostgreSQL 15 connection, exact application/control namespaces, and family canonicalization policy.
- Produces: `PostgresCatalogReader.read_document()`, `canonical_json_bytes()`, `structural_fingerprint()`, exact object keys/digests, and fail-closed document comparison.

- [ ] **Step 1: Write fixed canonical JSON and digest vectors**

```python
def test_canonical_json_is_utf8_sorted_and_compact():
    payload = {"z": [2, 1], "ä": {"b": True, "a": None}}
    assert canonical_json_bytes(payload) == (
        b'{"z":[2,1],"\xc3\xa4":{"a":null,"b":true}}'
    )


def test_structural_digest_has_fixed_vector():
    document = CanonicalSchemaDocument(
        canonicalization_version=1,
        postgres_major=15,
        objects=(
            CanonicalSchemaObject(
                kind="namespace",
                schema="public",
                name="public",
                qualifier="",
                definition={"name": "public"},
            ),
        ),
    )
    assert structural_fingerprint(document) == hashlib.sha256(
        canonical_json_bytes(document.to_payload())
    ).hexdigest()
```

- [ ] **Step 2: Write comparator failure tests before implementation**

```python
def test_comparator_rejects_unmanifested_difference():
    left = document_with_table("entry")
    right = document_with_table("entry", nullable_drift=True)
    with pytest.raises(SchemaComparisonError) as exc:
        compare_documents(left, right, exclusions=None)
    assert exc.value.safe_code == "unmanifested_schema_difference"


def test_exclusion_digest_must_match_before_removal():
    left = document_with_table("assistant_runtime_migration_item")
    manifest = manifest_for(left, definition_digest="0" * 64)
    with pytest.raises(SchemaComparisonError) as exc:
        normalize_document(left, manifest=manifest, side="old")
    assert exc.value.safe_code == "exclusion_definition_mismatch"
```

- [ ] **Step 3: Run unit tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_schema_canonical.py -q
```

Expected: tests fail because canonical document and comparison functions are absent.

- [ ] **Step 4: Implement frozen object/document contracts**

```python
@dataclass(frozen=True, order=True)
class CanonicalObjectKey:
    kind: str
    schema: str
    name: str
    qualifier: str = ""


@dataclass(frozen=True)
class CanonicalSchemaObject:
    key: CanonicalObjectKey
    definition: Mapping[str, JsonValue]

    @property
    def definition_digest(self) -> str:
        return sha256_hex(canonical_json_bytes(self.definition))


@dataclass(frozen=True)
class CanonicalSchemaDocument:
    canonicalization_version: Literal[1]
    postgres_major: int
    objects: tuple[CanonicalSchemaObject, ...]

    def __post_init__(self) -> None:
        keys = tuple(item.key for item in self.objects)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("canonical schema objects must be unique and sorted")
```

Reject floats, bytes, sets, non-string mapping keys, NaN, and timezone-naive datetimes before serialization. SQL definitions remain strings exactly as normalized by `normalize_catalog_sql()`; that function converts CRLF to LF, strips trailing spaces on each line, strips only outer blank lines, and appends no semicolon.

- [ ] **Step 5: Define the catalog coverage matrix**

Implement one reader method for each row; do not collapse unsupported kinds into a generic Inspector record:

| Reader method | Required catalog/API fields |
|---|---|
| `_read_namespaces` | non-system namespace names |
| `_read_extensions` | name, version, schema, relocatable |
| `_read_enum_and_domain_types` | kind, formatted base type, enum labels/order, domain default/not-null/collation/checks |
| `_read_sequences` | type, start/increment/min/max/cache/cycle, owned-by relation/column |
| `_read_views` | ordinary/materialized kind, `pg_get_viewdef`, check option, security barrier/invoker where available |
| `_read_tables` | persistence/partition kind; nested sorted columns, constraints, indexes |
| `_read_functions` | identity arguments, result type, language, volatility, strictness, security, parallel, full `pg_get_functiondef` |
| `_read_triggers` | table, name, enabled state, internal flag false, function identity, complete `pg_get_triggerdef` |

Filter `pg_catalog`, `information_schema`, `pg_toast*`, and temporary namespaces. Keep `public` and any future explicitly configured application namespace. Capture `plpgsql` as an extension but do not capture extension-owned internal functions as application functions.

- [ ] **Step 6: Implement columns/defaults/identity/generated attributes from catalogs**

Use a query with these exact sources:

```sql
SELECT
  ns.nspname AS schema_name,
  cls.relname AS table_name,
  attr.attnum AS ordinal,
  attr.attname AS column_name,
  pg_catalog.format_type(attr.atttypid, attr.atttypmod) AS formatted_type,
  attr.attnotnull AS not_null,
  attr.attidentity AS identity_kind,
  attr.attgenerated AS generated_kind,
  coll.collname AS collation_name,
  pg_catalog.pg_get_expr(def.adbin, def.adrelid, false) AS default_expression
FROM pg_catalog.pg_attribute AS attr
JOIN pg_catalog.pg_class AS cls ON cls.oid = attr.attrelid
JOIN pg_catalog.pg_namespace AS ns ON ns.oid = cls.relnamespace
LEFT JOIN pg_catalog.pg_attrdef AS def
  ON def.adrelid = attr.attrelid AND def.adnum = attr.attnum
LEFT JOIN pg_catalog.pg_collation AS coll ON coll.oid = attr.attcollation
WHERE attr.attnum > 0
  AND NOT attr.attisdropped
  AND cls.relkind IN ('r', 'p')
  AND ns.nspname = ANY(:schemas)
ORDER BY ns.nspname, cls.relname, attr.attnum
```

The canonical column record includes `ordinal`, name, formatted type, nullability, normalized default expression, identity kind (`''`, `a`, `d`), generated kind, and non-default collation. Never use Python `repr()` of a SQLAlchemy type.

- [ ] **Step 7: Implement constraints and FK actions**

Query `pg_constraint` and include:

```python
constraint = {
    "name": row.conname,
    "type": row.contype,
    "definition": normalize_catalog_sql(row.definition),
    "deferrable": row.condeferrable,
    "initiallyDeferred": row.condeferred,
    "validated": row.convalidated,
    "foreignKeyUpdateAction": row.confupdtype if row.contype == "f" else None,
    "foreignKeyDeleteAction": row.confdeltype if row.contype == "f" else None,
    "foreignKeyMatchType": row.confmatchtype if row.contype == "f" else None,
}
```

Obtain `definition` with `pg_get_constraintdef(con.oid, true)`. Include primary, foreign, unique, check, and exclusion constraints. Sort by `(type, name, definition)`; reject an unnamed application constraint because a stable baseline cannot safely address it later.

- [ ] **Step 8: Implement indexes with expressions and predicates**

Query `pg_index`, `pg_class`, `pg_am`, and include:

```sql
pg_catalog.pg_get_indexdef(idx.indexrelid, 0, false) AS definition,
pg_catalog.pg_get_expr(idx.indexprs, idx.indrelid, false) AS expressions,
pg_catalog.pg_get_expr(idx.indpred, idx.indrelid, false) AS predicate
```

Canonical index fields are name, access method, unique, primary, exclusion, valid, ready, definition, expression, predicate, key/include attribute numbers, nulls-not-distinct when supported by the server, and parent table. Exclude `relpages`, `reltuples`, fillfactor, tablespace location, and other physical tuning.

- [ ] **Step 9: Implement functions and triggers with complete bodies/linkage**

Function query core:

```sql
SELECT
  ns.nspname AS schema_name,
  proc.proname AS function_name,
  pg_catalog.pg_get_function_identity_arguments(proc.oid) AS identity_arguments,
  pg_catalog.pg_get_function_result(proc.oid) AS result_type,
  lang.lanname AS language,
  proc.provolatile AS volatility,
  proc.proisstrict AS is_strict,
  proc.prosecdef AS security_definer,
  proc.proparallel AS parallel_safety,
  proc.prokind AS function_kind,
  pg_catalog.pg_get_functiondef(proc.oid) AS definition
FROM pg_catalog.pg_proc AS proc
JOIN pg_catalog.pg_namespace AS ns ON ns.oid = proc.pronamespace
JOIN pg_catalog.pg_language AS lang ON lang.oid = proc.prolang
WHERE ns.nspname = ANY(:schemas)
ORDER BY ns.nspname, proc.proname,
         pg_catalog.pg_get_function_identity_arguments(proc.oid)
```

Trigger query core:

```sql
SELECT
  ns.nspname AS schema_name,
  rel.relname AS table_name,
  trg.tgname AS trigger_name,
  trg.tgenabled AS enabled_state,
  proc.proname AS function_name,
  pg_catalog.pg_get_function_identity_arguments(proc.oid) AS function_arguments,
  pg_catalog.pg_get_triggerdef(trg.oid, true) AS definition
FROM pg_catalog.pg_trigger AS trg
JOIN pg_catalog.pg_class AS rel ON rel.oid = trg.tgrelid
JOIN pg_catalog.pg_namespace AS ns ON ns.oid = rel.relnamespace
JOIN pg_catalog.pg_proc AS proc ON proc.oid = trg.tgfoid
WHERE NOT trg.tgisinternal
  AND ns.nspname = ANY(:schemas)
ORDER BY ns.nspname, rel.relname, trg.tgname
```

Store trigger timing/events/orientation parsed from `tgtype` as explicit booleans in addition to the full definition. Store linkage as schema/name/identity-arguments, not only function OID.

- [ ] **Step 10: Build PostgreSQL fixtures covering every kind**

In a disposable PostgreSQL 15 schema create:

- one enum and one domain with a check;
- one identity column and one generated column;
- one sequence with ownership;
- one ordinary view and one materialized view;
- PK/FK/unique/check constraints with explicit update/delete actions;
- one expression index and one partial unique index;
- one PL/pgSQL trigger function containing a multi-line body;
- one `BEFORE UPDATE OR DELETE` trigger, then `ALTER TABLE ... DISABLE TRIGGER` to test enabled state.

Assert every field changes the object digest when changed. Restore/drop the disposable schema in a finalizer even after failure.

- [ ] **Step 11: Prove excluded noise does not change identity**

```python
def test_owner_acl_comments_statistics_and_fillfactor_are_not_identity(pg_schema):
    before = read_document(pg_schema)
    pg_schema.execute("COMMENT ON TABLE sample IS 'non structural note'")
    pg_schema.execute("ALTER TABLE sample SET (fillfactor = 70)")
    pg_schema.execute("ANALYZE sample")
    after = read_document(pg_schema)
    assert structural_fingerprint(before) == structural_fingerprint(after)
```

Run owner/ACL assertions only with a disposable role that CI can create. If role creation is unavailable, the release-critical catalog suite must fail with a clear setup error rather than skip.

- [ ] **Step 12: Run focused unit and PostgreSQL suites**

Run:

```bash
.venv/bin/python -m pytest tests/test_schema_canonical.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest tests/test_schema_catalog_postgres.py -q
```

Expected: all tests pass; the PostgreSQL file reports tests executed, not skipped; modifying a function body, FK action, partial predicate, or trigger enabled state changes the fingerprint.

- [ ] **Step 13: Commit**

```bash
git add \
  backend/app/schema/contracts.py \
  backend/app/schema/canonical.py \
  backend/app/schema/catalog.py \
  backend/tests/test_schema_canonical.py \
  backend/tests/test_schema_catalog_postgres.py
git commit -m "feat(schema): canonicalize postgres structure"
```

---

### Task 3: Capture the Exact Pre-Squash Schema, Exclusions, and Retained SQL Objects

**Files:**

- Create: `backend/scripts/capture_pre_ga_schema.py`
- Create: `backend/app/schema/sql_objects.py`
- Create: `backend/app/schema/manifests/pre_ga_v1-exclusions.json`
- Create: `backend/app/schema/manifests/pre_ga_v1-pre-squash-schema.json`
- Create: `backend/app/schema/manifests/pre_ga_v1-sql-objects.json`
- Create: `backend/tests/test_schema_capture_postgres.py`
- Modify: `backend/tests/test_schema_exclusion_manifest.py`
- Modify: `backend/alembic/env.py`
- Modify: `backend/tests/_db.py`

**Interfaces:**

- Consumes: an empty disposable PostgreSQL 15 database upgraded through exact old head `b6e2d4f8a901`, complete catalog introspection, exact 27-object source allowlist, and deviation-record bytes.
- Produces: deterministic old-head source snapshot, definition-locked exclusion manifest, retained SQL-object creation registry, source/normalized fingerprints, and an AST proof that no live module outside the retirement boundary imports Legacy migration models.

- [ ] **Step 1: Write capture CLI failure tests before the CLI exists**

```python
def test_capture_refuses_wrong_head(postgres_url, tmp_path):
    upgrade(postgres_url, "9f3c1a7e2b40")
    result = run_capture(postgres_url, output_dir=tmp_path)
    assert result.returncode != 0
    assert "pre_squash_head_mismatch" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_capture_refuses_nonempty_legacy_evidence(postgres_at_old_head, tmp_path):
    postgres_at_old_head.execute(
        "INSERT INTO assistant_runtime_migration_item "
        "(id, subject_kind, source_type, source_id, source_name, "
        " source_name_normalized, source_digest, evidence_json, "
        " source_revision, target_revision, attempt_count, state_revision, "
        " state, created_at, updated_at) "
        "VALUES (gen_random_uuid(), 'skill', 'legacy', 'x', '', '', "
        " repeat('a', 64), '{}'::json, 0, 0, 0, 0, 'discovered', NOW(), NOW())"
    )
    result = run_capture(postgres_at_old_head.url, output_dir=tmp_path)
    assert result.returncode != 0
    assert "legacy_exclusion_data_present" in result.stderr
```

Use a fixed UUID literal instead of `gen_random_uuid()` if the reference database does not require `pgcrypto`; the test must not add an extension merely to create a row.

- [ ] **Step 2: Run the focused tests and verify red**

Run:

```bash
cd backend
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest tests/test_schema_capture_postgres.py -q
```

Expected: tests fail because `capture_pre_ga_schema.py` and manifest loaders do not exist.

- [ ] **Step 3: Remove live metadata registration of Legacy migration models**

Delete this import block from `backend/alembic/env.py`:

```python
from app.assistant.migration.models import (
    AssistantLegacyApprovalArchive,
    AssistantRuntimeAdmissionFallbackEvent,
    AssistantRuntimeCleanupGate,
    AssistantRuntimeMigrationBatch,
    AssistantRuntimeMigrationEvent,
    AssistantRuntimeMigrationItem,
    AssistantRuntimeRolloutAssignment,
    AssistantRuntimeRolloutControl,
    AssistantRuntimeRolloutEvent,
    AssistantRuntimeRolloutRevision,
    AssistantRuntimeShadowComparison,
)
```

Delete `import app.assistant.migration.models` from `backend/tests/_db.py`. The old Alembic scripts create their own tables and do not need those ORM models registered. Keep every Plan 1/2 live model registered.

Add an AST test that scans `backend/app` outside `backend/app/assistant/migration` and proves there is no import from that package; this extends the Plan 2 live-import boundary and records a SHA-256 of the sorted scan result in the exclusion manifest.

- [ ] **Step 4: Define deterministic manifest schemas**

The exclusion manifest top-level payload is exactly:

```python
exclusion_payload = {
    "schemaVersion": 1,
    "canonicalizationVersion": source_document.canonicalization_version,
    "schemaFamily": SCHEMA_FAMILY,
    "sourceHead": PRE_SQUASH_HEAD,
    "sourceStructuralFingerprint": structural_fingerprint(source_document),
    "normalizedStructuralFingerprint": structural_fingerprint(normalized_document),
    "referenceScanDigest": reference_scan_digest,
    "objects": [item.to_payload() for item in exclusion_items],
    "deviationEvidenceDigest": sha256_file(DEVIATION_PATH),
}
manifest = {
    **exclusion_payload,
    "manifestDigest": sha256_canonical_json(exclusion_payload),
}
```

Each exclusion item contains only:

```python
{
    "key": {
        "kind": obj.key.kind,
        "schema": obj.key.schema,
        "name": obj.key.name,
        "qualifier": obj.key.qualifier,
    },
    "definition": obj.definition,
    "definitionDigest": obj.definition_digest,
    "reasonCode": "unpublished_plan10_legacy_runtime_evidence",
    "sourceRevision": "6417df0243be",
    "liveReferenceCount": 0,
    "expectedInCleanBaseline": False,
}
```

There is no generated timestamp, machine path, database URL, owner, OID, or data value. `sourceRevision` is fixed because all 27 objects originate from that old revision; later drop-column revisions changed other schema, not these object definitions.

- [ ] **Step 5: Discover and validate the exact exclusion closure**

```python
def build_exclusion_items(
    document: CanonicalSchemaDocument,
) -> tuple[CanonicalSchemaObject, ...]:
    by_key = {item.key: item for item in document.objects}
    expected = tuple(CanonicalObjectKey(*parts) for parts in expected_legacy_object_keys())
    missing = sorted(set(expected) - set(by_key))
    discovered = tuple(by_key[key] for key in expected if key in by_key)
    if missing:
        raise CaptureError("legacy_exclusion_object_missing")
    if tuple(item.key for item in discovered) != tuple(sorted(expected)):
        raise CaptureError("legacy_exclusion_allowlist_mismatch")
    return discovered
```

Separately query dependencies for the 11 tables and the function. Fail if an attached non-internal trigger is not one of the 15 exact trigger keys, if an excluded function is linked from a retained table, or if a retained foreign key depends on an excluded table. Never use `name LIKE 'assistant_runtime_%'` as authorization to exclude an object.

- [ ] **Step 6: Require exclusion tables to be empty of business/evidence state**

Capture is a schema proof, not a data migration. Database A created for capture is empty except for migration-owned seed rows. Require:

- zero rows in ten exclusion tables;
- `assistant_runtime_rollout_control` contains exactly one row whose singleton key is `singleton`, active pointer is null, and state revision is zero;
- no excluded row references a retained row;
- no active/nonterminal old migration, shadow, approval, or fallback state.

Record only `legacyBusinessRowCount = 0` and `knownInertSeedRowCount = 1`; do not serialize the row or its UUID. A different count/value is `legacy_exclusion_data_present` and aborts capture.

- [ ] **Step 7: Build the retained SQL-object registry**

The registry contains every retained non-internal function and trigger after removing exact exclusions. Creation ordering is deterministic: functions first sorted by schema/name/identity arguments, then triggers sorted by schema/table/name. Each entry contains its canonical key, complete definition, definition digest, and dependencies.

```python
retained_sql_objects = tuple(
    item
    for item in normalized_document.objects
    if item.key.kind in {"function", "trigger", "view", "materialized_view"}
)
registry_payload = {
    "schemaVersion": 1,
    "canonicalizationVersion": 1,
    "sourceHead": PRE_SQUASH_HEAD,
    "objects": [renderable_sql_object(item) for item in retained_sql_objects],
}
registry = {
    **registry_payload,
    "registryDigest": sha256_canonical_json(registry_payload),
}
```

For a view, generate `CREATE VIEW schema.name AS <pg_get_viewdef>`; for a materialized view, generate `CREATE MATERIALIZED VIEW ... AS ... WITH NO DATA`. If the old head contains either kind, add a dedicated populated-state policy test; data/populated state is not structural identity. Fail on any retained non-table object kind that the renderer does not understand.

- [ ] **Step 8: Build the pre-squash snapshot without database data**

Write both the complete source object document and the normalized application document in `pre_ga_v1-pre-squash-schema.json`. The normalized document removes only the 27 verified exclusion keys; it keeps all retained tables, constraints, indexes, functions, and triggers. The snapshot top level includes its own digest excluding the digest field.

The capture command writes all three outputs to temporary files, fsyncs them, validates by loading them through production parsers, then atomically renames all files. If any validation fails, no destination file changes.

- [ ] **Step 9: Implement CLI modes with no acceptance bypass**

```text
python scripts/capture_pre_ga_schema.py \
  --database-url-env MINDATLAS_SCHEMA_SOURCE_DATABASE_URL \
  --write

python scripts/capture_pre_ga_schema.py \
  --database-url-env MINDATLAS_SCHEMA_SOURCE_DATABASE_URL \
  --check
```

`--database-url-env` names an environment variable; the URL itself never appears in argv, logs, or evidence. Parser choices contain only `--write` and `--check`; there is no acceptance/force flag. `--write` validates that all destinations are regular repository files and replaces only the three declared manifests. `--check` regenerates in memory and requires byte equality.

- [ ] **Step 10: Create a clean disposable Database A and capture**

Run:

```bash
cd backend
MINDATLAS_DEPLOYMENT_CLASS=development \
DATABASE_URL="$MINDATLAS_SCHEMA_SOURCE_DATABASE_URL" \
  .venv/bin/alembic upgrade b6e2d4f8a901
DATABASE_URL="$MINDATLAS_SCHEMA_SOURCE_DATABASE_URL" \
  .venv/bin/alembic current
MINDATLAS_SCHEMA_SOURCE_DATABASE_URL="$MINDATLAS_SCHEMA_SOURCE_DATABASE_URL" \
  .venv/bin/python scripts/capture_pre_ga_schema.py \
    --database-url-env MINDATLAS_SCHEMA_SOURCE_DATABASE_URL --write
```

Expected: current head is `b6e2d4f8a901`; capture reports 27 exact exclusions, one inert seed row, zero Legacy business/evidence rows, and a lowercase normalized fingerprint; it prints no connection URL or SQL definition.

- [ ] **Step 11: Prove drift and prefix objects fail closed**

Tests make one change at a time in rolled-back/disposable databases:

- alter one excluded function-body byte;
- rename one excluded trigger;
- create `assistant_runtime_unreviewed`;
- create a retained FK to one excluded table;
- insert one Legacy evidence row;
- change old head to another revision.

Every case must fail capture with a bounded code and leave committed manifests byte-identical. The prefix-only extra object must appear as an unmanifested difference; it is never auto-added.

- [ ] **Step 12: Run capture checks and manifest tests**

Run:

```bash
MINDATLAS_SCHEMA_SOURCE_DATABASE_URL="$MINDATLAS_SCHEMA_SOURCE_DATABASE_URL" \
  .venv/bin/python scripts/capture_pre_ga_schema.py \
    --database-url-env MINDATLAS_SCHEMA_SOURCE_DATABASE_URL --check
.venv/bin/python -m pytest \
  tests/test_schema_exclusion_manifest.py \
  tests/test_schema_capture_postgres.py -q
cd ..
git diff --check
```

Expected: regeneration is byte-identical; exactly 27 exclusion entries validate; all tests pass with no PostgreSQL skip; formatting is clean.

- [ ] **Step 13: Commit**

```bash
git add \
  backend/alembic/env.py \
  backend/app/schema/sql_objects.py \
  backend/app/schema/manifests/pre_ga_v1-exclusions.json \
  backend/app/schema/manifests/pre_ga_v1-pre-squash-schema.json \
  backend/app/schema/manifests/pre_ga_v1-sql-objects.json \
  backend/scripts/capture_pre_ga_schema.py \
  backend/tests/_db.py \
  backend/tests/test_schema_capture_postgres.py \
  backend/tests/test_schema_exclusion_manifest.py
git commit -m "test(schema): capture pre-squash structural contract"
```

---

### Task 4: Remove the Unpublished Legacy Migration Package from Live Code and Tests

**Files:**

- Create: `backend/tests/test_no_legacy_schema_runtime.py`
- Delete: `backend/app/assistant/migration/__init__.py`
- Delete: `backend/app/assistant/migration/approvals.py`
- Delete: `backend/app/assistant/migration/cleanup.py`
- Delete: `backend/app/assistant/migration/cli.py`
- Delete: `backend/app/assistant/migration/contracts.py`
- Delete: `backend/app/assistant/migration/discovery.py`
- Delete: `backend/app/assistant/migration/gates.py`
- Delete: `backend/app/assistant/migration/inventory.py`
- Delete: `backend/app/assistant/migration/l2.py`
- Delete: `backend/app/assistant/migration/legacy_names.py`
- Delete: `backend/app/assistant/migration/metrics.py`
- Delete: `backend/app/assistant/migration/models.py`
- Delete: `backend/app/assistant/migration/ownership.py`
- Delete: `backend/app/assistant/migration/packages.py`
- Delete: `backend/app/assistant/migration/repository.py`
- Delete: `backend/app/assistant/migration/rollout.py`
- Delete: `backend/app/assistant/migration/shadow.py`
- Delete: `backend/app/assistant/migration/verification.py`
- Delete: `backend/tests/fixtures/ai_runtime_migration/backup_export_manifest.json`
- Delete: `backend/tests/fixtures/ai_runtime_migration/dynamic_import_markers.json`
- Delete: `backend/tests/fixtures/ai_runtime_migration/sanitized_skill_records.json`
- Delete: `backend/tests/test_ai_runtime_cleanup_preflight.py`
- Delete: `backend/tests/test_ai_runtime_hitl_migration.py`
- Delete: `backend/tests/test_ai_runtime_l2_migration.py`
- Modify: `backend/tests/test_ai_runtime_legacy_cleanup.py`
- Delete: `backend/tests/test_ai_runtime_migration_inventory.py`
- Delete: `backend/tests/test_ai_runtime_migration_repository_postgres.py`
- Delete: `backend/tests/test_ai_runtime_shadow.py`
- Delete: `backend/tests/test_ai_runtime_skill_migration.py`
- Delete: `backend/tests/test_main_agent_rollout.py`
- Modify: `backend/tests/test_main_agent_only_live_imports.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**

- Consumes: captured old-head canonical artifacts, Plan 2 replacement `memory_migration_state.py`, new Main-Agent rollout package, and no-live-import proof.
- Produces: no `app.assistant.migration` package, no executable Legacy migration/restore test surface, no ORM registration for 11 excluded tables, and AST-enforced source/test isolation.

- [ ] **Step 1: Strengthen the boundary test before deleting files**

```python
FORBIDDEN_MODULE_PREFIX = "app.assistant.migration"
FORBIDDEN_TABLE_NAMES = set(LEGACY_TABLE_NAMES)


def test_legacy_migration_package_is_absent():
    assert not (APP_ROOT / "assistant" / "migration").exists()


def test_live_metadata_has_no_legacy_tables():
    load_all_live_models()
    assert FORBIDDEN_TABLE_NAMES.isdisjoint(Base.metadata.tables)


def test_source_and_tests_do_not_import_legacy_migration_package():
    assert scan_python_imports(BACKEND_ROOT, FORBIDDEN_MODULE_PREFIX) == []
```

The scanner excludes only `backend/alembic/archive` because it is non-Python-suffixed after Task 8. It does not exclude tests, scripts, or `alembic/env.py`.

- [ ] **Step 2: Run the boundary test and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_no_legacy_schema_runtime.py -q
```

Expected: failure lists the existing package and Legacy test imports. It must not list Plan 2's `app.assistant.memory_migration_state` because that module has no Legacy-package dependency.

- [ ] **Step 3: Delete the complete package and old execution fixtures**

Remove every file listed for deletion above. Do not move Python modules into another importable namespace. The committed catalog snapshot, exclusion manifest, archive bytes, and factual deviation record are the retained history.

Rewrite `backend/tests/test_ai_runtime_legacy_cleanup.py` completely as a small tombstone test: it imports no `app.assistant.migration` symbol, asserts that package is absent, asserts the 11 Legacy tables are absent from live `Base.metadata`, and imports the installed `fastapi`/`starlette` modules without replacing `sys.modules`. Plan 4 keeps this filename solely to reproduce the previously observed test-order pollution against `test_durable_run_streaming.py`; it does not preserve Legacy cleanup behavior.

Delete obsolete CI commands that invoke the removed tests or set these acknowledgements:

```text
MINDATLAS_PLAN10_B2_TEST_OVERRIDE
MINDATLAS_PLAN10_MIGRATION_DOWNGRADE_ACK
MINDATLAS_PLAN10_B2_DOWNGRADE_ACK
MINDATLAS_PLAN10_B2_SKILL_DROP_DOWNGRADE_ACK
MINDATLAS_PLAN10_B2_LEGACY_ID_DROP_DOWNGRADE_ACK
MINDATLAS_PLAN10_B2_LEGACY_DIGEST_DROP_DOWNGRADE_ACK
```

Do not replace them with a clean-baseline bypass. Task 11 adds the new root-only CI job.

- [ ] **Step 4: Preserve only current runtime behavior tests**

If a removed file contained a still-valid Main Agent invariant, move the invariant—not its migration helper—into the owning current test before deletion:

- no Legacy pre-insert fallback belongs in `test_assistant_atomic_admission.py` as zero-residue failure;
- L2 readable-state behavior belongs in `test_assistant_service_l2_memory.py` through `memory_migration_state.py`;
- immutable Main Agent rollout belongs in `test_assistant_runtime_repository.py`;
- Worker compatibility belongs in Plan 2 Worker tests.

The replacement test imports only current packages. No test constructs old migration batches, cohort rollout assignments, shadow comparisons, Legacy approval archives, or cleanup gates.

- [ ] **Step 5: Scan model metadata and source symbols**

Run:

```bash
rg -n 'app\.assistant\.migration|AssistantRuntimeMigration|AssistantLegacyApprovalArchive|AssistantRuntimeAdmissionFallbackEvent' \
  backend/app backend/tests backend/scripts backend/alembic/env.py || true
rg -n 'assistant_runtime_(migration|rollout|admission_fallback|shadow|cleanup)|assistant_legacy_approval_archive' \
  backend/app --glob '*.py' || true
```

Expected: no matches. Matches remain only inside the committed exclusion/snapshot JSON and old live revision files awaiting archive; those paths are deliberately outside this scan.

- [ ] **Step 6: Run focused current-runtime tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_no_legacy_schema_runtime.py \
  tests/test_ai_runtime_legacy_cleanup.py \
  tests/test_main_agent_only_live_imports.py \
  tests/test_assistant_atomic_admission.py \
  tests/test_assistant_runtime_repository.py \
  tests/test_assistant_service_l2_memory.py \
  tests/test_assistant_worker_runtime_compatibility.py -q
```

Expected: all tests pass; no current behavior depends on the deleted package.

- [ ] **Step 7: Run the full backend suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: full suite passes. A failure caused by an import of the deleted package is a missed live dependency and must be removed or replaced by its current owner; do not restore the package.

- [ ] **Step 8: Commit**

```bash
git add -A \
  backend/app/assistant/migration \
  backend/tests \
  .github/workflows/ci.yml
git commit -m "refactor(schema): remove unpublished legacy migration runtime"
```

---

### Task 5: Build the Deterministic Clean-Baseline Generator

**Files:**

- Create: `backend/app/model_registry.py`
- Create: `backend/scripts/generate_pre_ga_baseline.py`
- Create: `backend/tests/test_schema_baseline_generator.py`
- Modify: `backend/alembic/env.py`
- Modify: `backend/tests/_db.py`
- Modify: `backend/app/schema/sql_objects.py`

**Interfaces:**

- Consumes: all live `Base.metadata`, retained SQL-object registry, committed version-2 logical application contract, and fixed family/identity constants.
- Produces: one byte-deterministic self-contained staged root, one central live-model registration function, and a model-reference PostgreSQL schema whose fingerprint must equal the captured clean application fingerprint.

- [ ] **Step 1: Write failing centralized model-registration tests**

```python
def test_live_model_registry_loads_every_expected_table():
    load_all_live_models()
    names = set(Base.metadata.tables)
    assert "operator_account" in names
    assert "assistant_main_agent_rollout_revision" in names
    assert "assistant_chat_run" in names
    assert "assistant_runtime_migration_item" not in names


def test_alembic_env_uses_only_central_registry():
    source = ALEMBIC_ENV.read_text("utf-8")
    assert "from app.model_registry import load_all_live_models" in source
    assert "app.assistant.migration" not in source
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_schema_baseline_generator.py -q
```

Expected: failure because `app.model_registry` and the generator do not exist.

- [ ] **Step 3: Centralize all live ORM imports**

Implement one idempotent function:

```python
def load_all_live_models() -> None:
    import app.ai_provider.models
    import app.ai_registry.models
    import app.assistant.models
    import app.assistant.capability_calls.models
    import app.assistant.durable.models
    import app.assistant.evaluation.models
    import app.assistant.runtime.models
    import app.assistant.skills.models
    import app.assistant_config.models
    import app.attachment.models
    import app.entry.models
    import app.entry_type.models
    import app.lightrag.models
    import app.openclaw_integration.models
    import app.operator_auth.models
    import app.relation.models
    import app.report.models
    import app.system_settings.models
    import app.tag.models

    modules = (
        app.ai_provider.models,
        app.ai_registry.models,
        app.assistant.models,
        app.assistant.capability_calls.models,
        app.assistant.durable.models,
        app.assistant.evaluation.models,
        app.assistant.runtime.models,
        app.assistant.skills.models,
        app.assistant_config.models,
        app.attachment.models,
        app.entry.models,
        app.entry_type.models,
        app.lightrag.models,
        app.openclaw_integration.models,
        app.operator_auth.models,
        app.relation.models,
        app.report.models,
        app.system_settings.models,
        app.tag.models,
    )
    if any(module is None for module in modules):
        raise RuntimeError("live model import failed")
```

Use this function in `alembic/env.py`, the generator, and `_db.py`. Keep SQLite-specific type/constraint normalization in test support, not in the production registry.

- [ ] **Step 4: Write the model-reference PostgreSQL equivalence test**

```python
def test_live_metadata_plus_retained_sql_matches_captured_clean_schema(
    empty_postgres_database,
):
    load_all_live_models()
    Base.metadata.create_all(empty_postgres_database.engine)
    install_retained_sql_objects(empty_postgres_database.connection)
    raw_actual = PostgresCatalogReader(
        empty_postgres_database.connection
    ).read_document()
    actual = project_logical_application_document(
        raw_actual,
        control_stage=SchemaControlStage.MODEL_REFERENCE,
    )
    expected = load_logical_application_contract().logical_application_document
    compare_documents(expected, actual, exclusions=None)
```

This test is release-critical. If it reports a real table/constraint/index mismatch, stop and review the named ORM contract before generating a root. Do not make the comparator ignore it and do not source table DDL from the discrepancy.

- [ ] **Step 5: Implement strict retained SQL-object installation**

```python
def install_retained_sql_objects(connection: Connection) -> None:
    registry = load_retained_sql_object_registry()
    for item in registry.creation_order:
        if sha256_canonical_json(item.canonical_definition) != item.definition_digest:
            raise SqlObjectRegistryError("sql_object_definition_digest_mismatch")
        connection.execute(text(item.create_sql))
```

Validate function dependencies exist before triggers; validate each trigger's target table and function linkage after creation by re-introspection. Do not use `IF NOT EXISTS`, because a collision must fail.

- [ ] **Step 6: Implement generator preconditions**

The generator must verify:

1. Python major/minor is `3.11`;
2. source manifests and their self-digests validate;
3. live metadata contains no exclusion table;
4. target reference database is empty except PostgreSQL system objects;
5. model-reference version-2 fingerprint equals the committed logical application fingerprint;
6. no second Alembic root is being written into the live version directory;
7. deployment-specific values are represented as validated runtime inputs, never captured from the author's shell into generated bytes.

Failure uses a bounded code and does not alter the output file.

Use one transaction on the empty generator database to create the model-reference schema, install retained SQL objects, introspect it, and then roll that transaction back. Assert the database is empty again before opening a new transaction for Alembic autogeneration against the empty catalog. This keeps model proof and render input independent while requiring only one disposable database URL.

- [ ] **Step 7: Produce Alembic operations against an empty database**

Use Alembic's programmatic APIs against the empty model-reference database:

```python
context = MigrationContext.configure(
    connection,
    opts={
        "compare_type": True,
        "compare_server_default": True,
        "target_metadata": Base.metadata,
        "include_schemas": True,
    },
)
migration_script = produce_migrations(context, Base.metadata)
body = render_python_code(
    migration_script.upgrade_ops,
    sqlalchemy_module_prefix="sa.",
    alembic_module_prefix="op.",
)
```

The connection database must be empty when `produce_migrations` runs. Render imports in a fixed order, normalize quoting/line endings, and reject any custom operation the generator has not explicitly rendered. Append retained function DDL before retained triggers.

- [ ] **Step 8: Render a self-contained revision header and fixed downgrade guard**

The staged artifact begins:

```python
"""Install the first supported MindAtlas pre-GA schema directly."""

from __future__ import annotations

import hashlib
import json
import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "pre_ga_v1_0001"
down_revision = None
branch_labels = ("pre_ga_v1",)
depends_on = None

SCHEMA_FAMILY = "pre_ga_v1"
SCHEMA_REVISION = "pre_ga_v1_0001"
TEST_DOWNGRADE_ACK = "MINDATLAS_TEST_ALLOW_EMPTY_SCHEMA_DOWNGRADE"
```

`downgrade()` first requires `APP_ENV == "test"` and acknowledgement value `I_ACKNOWLEDGE_EMPTY_SCHEMA_DESTRUCTION`. It counts rows in every live table except `alembic_version` and the one schema marker row, rejects any count greater than zero, then drops triggers, functions, views, constraints/tables, types, and marker in exact reverse dependency order. No old revision/table is created.

- [ ] **Step 9: Keep deployment class dynamic but closed**

The root reads only:

```python
deployment_class = os.environ.get("MINDATLAS_DEPLOYMENT_CLASS", "").strip()
if deployment_class not in {"development", "rehearsal", "production"}:
    raise RuntimeError("schema_deployment_class_invalid")
```

The chosen value changes only the singleton marker row/runtime identity digest, not migration bytes or structural fingerprint. The artifact never defaults production to development.

- [ ] **Step 10: Generate twice and prove byte determinism**

```python
def test_generator_is_byte_reproducible(generator_context, tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    generate_baseline(generator_context, first)
    generate_baseline(generator_context, second)
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(
        second.read_bytes()
    ).digest()
```

Also run with different working directories, `PYTHONHASHSEED`, locale, and deployment class. Generated bytes remain identical.

- [ ] **Step 11: Reject application imports and transitional DDL**

Parse the generated AST and SQL literals. Assert imports are limited to the header above; `down_revision` is literal `None`; no `DROP` appears in `upgrade()` except generated cleanup inside exception-safe local test helpers (prefer none); and no exclusion name appears anywhere in the artifact.

```python
forbidden = set(LEGACY_TABLE_NAMES) | {
    "b6e2d4f8a901",
    "9f3c1a7e2b40",
    "app.assistant.migration",
}
assert all(value not in source for value in forbidden)
```

The old revision IDs may appear in archive/evidence metadata, but never in root migration code.

- [ ] **Step 12: Generate the staged root and check it**

Run:

```bash
MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL="$MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL" \
  .venv/bin/python scripts/generate_pre_ga_baseline.py \
    --database-url-env MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL \
    --output alembic/baseline_staging/pre_ga_v1_0001_clean_baseline.py \
    --write
MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL="$MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL" \
  .venv/bin/python scripts/generate_pre_ga_baseline.py \
    --database-url-env MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL \
    --output alembic/baseline_staging/pre_ga_v1_0001_clean_baseline.py \
    --check
```

Expected: model-reference equivalence passes; the second command reports byte-identical output; the live Alembic version directory still contains only the old chain, so no temporary multiple-head state is committed.

- [ ] **Step 13: Run focused generator and PostgreSQL model tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_schema_baseline_generator.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
    tests/test_schema_baseline_generator.py::test_live_metadata_plus_retained_sql_matches_captured_clean_schema -q
cd ..
git diff --check
```

Expected: all tests pass, PostgreSQL test executes, and formatting is clean.

- [ ] **Step 14: Commit**

```bash
git add \
  backend/app/model_registry.py \
  backend/app/schema/sql_objects.py \
  backend/alembic/env.py \
  backend/alembic/baseline_staging/pre_ga_v1_0001_clean_baseline.py \
  backend/scripts/generate_pre_ga_baseline.py \
  backend/tests/_db.py \
  backend/tests/test_schema_baseline_generator.py
git commit -m "feat(schema): generate clean pre-ga root"
```

---

### Task 6: Add the Family-Bound Schema Identity Marker and Verify Fresh Root Migration

**Files:**

- Create: `backend/app/schema/identity.py`
- Create: `backend/app/schema/manifests/pre_ga_v1-expected.json`
- Create: `backend/tests/schema_baseline_support.py`
- Create: `backend/tests/test_schema_baseline_migration_postgres.py`
- Modify: `backend/app/schema/contracts.py`
- Modify: `backend/scripts/generate_pre_ga_baseline.py`
- Modify: `backend/alembic/baseline_staging/pre_ga_v1_0001_clean_baseline.py`
- Modify: `backend/tests/test_schema_baseline_generator.py`

**Interfaces:**

- Consumes: committed version-2 logical application fingerprint, Plan 1 auth version, Plan 2 expected seed/runtime/codec/feature values, deployment class, and staged root.
- Produces: exact marker table/row/guard contract, runtime identity digest, expected-contract manifest, fresh PostgreSQL upgrade proof, and test-only empty downgrade behavior.

- [ ] **Step 1: Write fixed runtime identity digest vectors**

```python
def test_runtime_identity_digest_binds_every_required_input():
    material = SchemaRuntimeIdentityMaterial(
        schema_family="pre_ga_v1",
        schema_revision="pre_ga_v1_0001",
        structural_fingerprint="1" * 64,
        seed_contract_digest="2" * 64,
        deployment_class=DeploymentClass.REHEARSAL,
        runtime_contract_version=1,
        checkpoint_codec_version=3,
        capability_feature_digest="3" * 64,
        operator_auth_contract_version="operator-auth-v1",
    )
    payload = schema_runtime_identity_payload(material)
    assert set(payload) == {
        "schemaFamily",
        "schemaRevision",
        "structuralFingerprint",
        "seedContractDigest",
        "deploymentClass",
        "runtimeContractVersion",
        "checkpointCodecVersion",
        "capabilityFeatureDigest",
        "operatorAuthContractVersion",
    }
    assert schema_runtime_identity_digest(material) == sha256_canonical_json(payload)
```

Parametrize over every field, change one value, and assert the digest changes. Assert dictionary insertion order does not change the digest.

- [ ] **Step 2: Write fresh migration and invalid-class tests before implementation**

```python
@pytest.mark.parametrize("deployment_class", ["development", "rehearsal", "production"])
def test_clean_root_upgrades_empty_postgres_and_writes_exact_marker(
    empty_postgres_database,
    deployment_class,
):
    run_staged_root_upgrade(empty_postgres_database, deployment_class)
    marker = read_marker(empty_postgres_database)
    assert marker.schema_family == "pre_ga_v1"
    assert marker.schema_revision == "pre_ga_v1_0001"
    assert marker.deployment_class.value == deployment_class
    assert marker.checkpoint_codec_version == 3
    assert marker.runtime_identity_digest == schema_runtime_identity_digest(
        marker.to_identity_material()
    )


def test_clean_root_refuses_missing_or_unknown_deployment_class(
    empty_postgres_database,
):
    result = run_staged_root_upgrade(empty_postgres_database, "shared")
    assert result.returncode != 0
    assert "schema_deployment_class_invalid" in result.stderr
    assert application_table_names(empty_postgres_database) == set()
```

- [ ] **Step 3: Run focused tests and verify red**

Run:

```bash
cd backend
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
    tests/test_schema_baseline_migration_postgres.py -q
```

Expected: tests fail because marker DDL/identity loader is not implemented.

- [ ] **Step 4: Implement the exact identity payload and marker reader**

```python
def schema_runtime_identity_payload(
    material: SchemaRuntimeIdentityMaterial,
) -> dict[str, JsonValue]:
    return {
        "schemaFamily": material.schema_family,
        "schemaRevision": material.schema_revision,
        "structuralFingerprint": material.structural_fingerprint,
        "seedContractDigest": material.seed_contract_digest,
        "deploymentClass": material.deployment_class.value,
        "runtimeContractVersion": material.runtime_contract_version,
        "checkpointCodecVersion": material.checkpoint_codec_version,
        "capabilityFeatureDigest": material.capability_feature_digest,
        "operatorAuthContractVersion": material.operator_auth_contract_version,
    }


def schema_runtime_identity_digest(
    material: SchemaRuntimeIdentityMaterial,
) -> str:
    return sha256_canonical_json(schema_runtime_identity_payload(material))
```

`read_schema_identity(db)` uses one parameterized `SELECT`, requires exactly one `singleton_key='current'` row, validates all shapes/types, and returns a frozen `SchemaIdentityRecord`. Zero/multiple/malformed rows raise `SchemaIdentityError` with a bounded diagnostic code; no raw row enters the exception.

- [ ] **Step 5: Generate the marker table with complete constraints**

The staged root emits explicit Alembic operations equivalent to:

```python
op.create_table(
    "mindatlas_schema_identity",
    sa.Column("singleton_key", sa.String(32), primary_key=True, nullable=False),
    sa.Column("schema_family", sa.String(32), nullable=False),
    sa.Column("schema_revision", sa.String(64), nullable=False),
    sa.Column("structural_fingerprint", sa.CHAR(64), nullable=False),
    sa.Column("runtime_identity_digest", sa.CHAR(64), nullable=False),
    sa.Column("seed_contract_digest", sa.CHAR(64), nullable=False),
    sa.Column("deployment_class", sa.String(16), nullable=False),
    sa.Column("runtime_contract_version", sa.Integer(), nullable=False),
    sa.Column("checkpoint_codec_version", sa.Integer(), nullable=False),
    sa.Column("capability_feature_digest", sa.CHAR(64), nullable=False),
    sa.Column("operator_auth_contract_version", sa.String(64), nullable=False),
    sa.Column("identity_contract_version", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("singleton_key = 'current'", name="ck_schema_identity_singleton"),
    sa.CheckConstraint("schema_family = 'pre_ga_v1'", name="ck_schema_identity_family"),
    sa.CheckConstraint(
        "deployment_class IN ('development','rehearsal','production')",
        name="ck_schema_identity_deployment_class",
    ),
    sa.CheckConstraint(
        "structural_fingerprint ~ '^[0-9a-f]{64}$' "
        "AND runtime_identity_digest ~ '^[0-9a-f]{64}$' "
        "AND seed_contract_digest ~ '^[0-9a-f]{64}$' "
        "AND capability_feature_digest ~ '^[0-9a-f]{64}$'",
        name="ck_schema_identity_digest_shapes",
    ),
    sa.CheckConstraint(
        "runtime_contract_version > 0 AND checkpoint_codec_version > 0 "
        "AND identity_contract_version > 0",
        name="ck_schema_identity_positive_versions",
    ),
)
```

Use named constraints exactly as shown. Marker/control objects are omitted from the application structural fingerprint and included in the separate marker-control fingerprint.

Extend `SchemaControlStage` with `CLEAN_ROOT_MIGRATED`. Its exact contract requires the Alembic version table plus the identity table, guard function, and guard trigger. `project_logical_application_document()` validates all four definitions before extracting them; missing, additional, or drifted controls fail closed.

- [ ] **Step 6: Add the marker mutation guard for future same-family revisions**

Generate a PL/pgSQL function and trigger:

```sql
CREATE FUNCTION mindatlas_guard_schema_identity_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  expected_revision text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'schema identity deletion is forbidden';
  END IF;
  IF NEW.singleton_key <> OLD.singleton_key
     OR NEW.schema_family <> OLD.schema_family
     OR NEW.deployment_class <> OLD.deployment_class
     OR NEW.created_at <> OLD.created_at
     OR NEW.identity_contract_version < OLD.identity_contract_version THEN
    RAISE EXCEPTION 'schema identity immutable field changed';
  END IF;
  expected_revision := current_setting(
    'mindatlas.schema_migration_revision', true
  );
  IF expected_revision IS NULL OR expected_revision = ''
     OR NEW.schema_revision <> expected_revision
     OR NEW.schema_revision = OLD.schema_revision
     OR NEW.updated_at <= OLD.updated_at THEN
    RAISE EXCEPTION 'schema identity advance is not migration-authorized';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_mindatlas_schema_identity_guard
BEFORE UPDATE OR DELETE ON mindatlas_schema_identity
FOR EACH ROW EXECUTE FUNCTION mindatlas_guard_schema_identity_mutation();
```

Plan 4 sets `SET LOCAL mindatlas.schema_migration_revision = 'pre_ga_v1_0002'` only inside its Alembic transaction and atomically advances every revision-bound identity field. Runtime code never sets that guard.

- [ ] **Step 7: Insert the singleton row from only committed/literal inputs**

The generated root includes the literal version-2 logical application fingerprint, seed contract digest, runtime contract version, codec `3`, Capability feature digest, and Operator auth version. At upgrade time it validates deployment class, computes canonical runtime identity with the same compact sorted JSON algorithm, and inserts:

```sql
INSERT INTO mindatlas_schema_identity (
  singleton_key, schema_family, schema_revision,
  structural_fingerprint, runtime_identity_digest,
  seed_contract_digest, deployment_class,
  runtime_contract_version, checkpoint_codec_version,
  capability_feature_digest, operator_auth_contract_version,
  identity_contract_version, created_at, updated_at
) VALUES (
  'current', :family, :revision,
  :fingerprint, :runtime_identity_digest,
  :seed_digest, :deployment_class,
  :runtime_contract_version, 3,
  :feature_digest, :operator_auth_version,
  1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
)
```

No `ON CONFLICT`, caller-supplied digest, or network/database-derived seed identity is allowed.

- [ ] **Step 8: Generate and validate the expected-contract manifest**

After upgrading a fresh Database B, introspect it and write deterministic `pre_ga_v1-expected.json`:

```python
expected_payload = {
    "schemaVersion": 1,
    "schemaFamily": "pre_ga_v1",
    "schemaRevision": "pre_ga_v1_0001",
    "applicationStructuralFingerprint": logical_application_fingerprint,
    "schemaIdentityControlFingerprint": marker_control_fingerprint,
    "seedContractDigest": SEED_CONTRACT_DIGEST,
    "runtimeContractVersion": RUNTIME_CONTRACT_VERSION,
    "checkpointCodecVersion": 3,
    "capabilityFeatureDigest": default_capability_feature_digest(),
    "operatorAuthContractVersion": OPERATOR_AUTH_CONTRACT_VERSION,
    "canonicalizationVersion": 2,
}
expected_manifest = {
    **expected_payload,
    "manifestDigest": sha256_canonical_json(expected_payload),
}
```

The deployment class is deliberately not fixed in this manifest; the runtime identity function binds the row's one immutable enum value. Test all three expected runtime digest variants.

- [ ] **Step 9: Implement fresh migration helper without exposing archive revisions**

`tests/schema_baseline_support.py` builds a temporary Alembic script directory containing a copy of `env.py`, `script.py.mako`, and only the staged root. It never copies old revisions. It passes the database URL through an environment variable and deletes the temporary directory in a finalizer.

Assert `alembic heads` in that directory returns only `pre_ga_v1_0001` before upgrading Database B.

- [ ] **Step 10: Implement and test destructive-to-empty downgrade guard**

Required cases:

```python
def test_root_downgrade_refuses_without_test_guard(fresh_baseline_db):
    result = downgrade_base(fresh_baseline_db, app_env="production", ack=None)
    assert result.returncode != 0
    assert "schema_test_downgrade_forbidden" in result.stderr


def test_root_downgrade_refuses_business_rows(fresh_baseline_db):
    fresh_baseline_db.insert_entry_type_fixture()
    result = downgrade_base(
        fresh_baseline_db,
        app_env="test",
        ack="I_ACKNOWLEDGE_EMPTY_SCHEMA_DESTRUCTION",
    )
    assert result.returncode != 0
    assert "schema_test_downgrade_nonempty" in result.stderr


def test_root_downgrade_to_empty_is_test_only(fresh_baseline_db):
    result = downgrade_base(
        fresh_baseline_db,
        app_env="test",
        ack="I_ACKNOWLEDGE_EMPTY_SCHEMA_DESTRUCTION",
    )
    assert result.returncode == 0
    assert application_table_names(fresh_baseline_db) == set()
```

The marker singleton is the sole allowed row. Alembic's own version row is not business data. After downgrade, upgrade the same database again and require the original fingerprint.

- [ ] **Step 11: Regenerate the staged root and expected manifest**

Run:

```bash
MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL="$MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL" \
  .venv/bin/python scripts/generate_pre_ga_baseline.py \
    --database-url-env MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL \
    --output alembic/baseline_staging/pre_ga_v1_0001_clean_baseline.py \
    --write --write-expected-manifest
MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL="$MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL" \
  .venv/bin/python scripts/generate_pre_ga_baseline.py \
    --database-url-env MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL \
    --output alembic/baseline_staging/pre_ga_v1_0001_clean_baseline.py \
    --check --check-expected-manifest
```

Expected: both artifact and expected manifest regenerate byte-for-byte; output reports one application fingerprint and one marker-control fingerprint without printing DDL.

- [ ] **Step 12: Run focused fresh-upgrade/downgrade tests**

Run:

```bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
    tests/test_schema_baseline_generator.py \
    tests/test_schema_baseline_migration_postgres.py -q
cd ..
git diff --check
```

Expected: all tests execute and pass for PostgreSQL; invalid deployment class leaves an empty database; test-only downgrade refuses data and succeeds only when empty; formatting is clean.

- [ ] **Step 13: Commit**

```bash
git add \
  backend/app/schema/contracts.py \
  backend/app/schema/identity.py \
  backend/app/schema/manifests/pre_ga_v1-expected.json \
  backend/alembic/baseline_staging/pre_ga_v1_0001_clean_baseline.py \
  backend/scripts/generate_pre_ga_baseline.py \
  backend/tests/schema_baseline_support.py \
  backend/tests/test_schema_baseline_generator.py \
  backend/tests/test_schema_baseline_migration_postgres.py
git commit -m "feat(schema): bind clean root identity"
```

---

### Task 7: Prove Old-Head and Clean-Root Structural Equivalence on Two Databases

**Files:**

- Create: `backend/tests/test_schema_equivalence_postgres.py`
- Create: `backend/scripts/verify_pre_ga_schema.py`
- Modify: `backend/app/schema/canonical.py`
- Modify: `backend/app/schema/exclusions.py`
- Modify: `backend/tests/schema_baseline_support.py`

**Interfaces:**

- Consumes: live old chain through `b6e2d4f8a901`, staged clean root, exclusion manifest, pre-squash physical snapshot, version-2 logical application contract, expected identity manifest, and canonical introspector.
- Produces: independent Database A/B equivalence proof, expected clean fingerprint proof, Legacy absence proof, and a reusable verification runner that later emits exit evidence.

- [ ] **Step 1: Write the failing two-database golden test**

```python
def test_old_head_normalized_equals_clean_root(
    empty_postgres_database_factory,
):
    database_a = empty_postgres_database_factory("old_chain")
    database_b = empty_postgres_database_factory("clean_root")
    upgrade_live_old_chain(database_a.url, "b6e2d4f8a901")
    upgrade_staged_clean_root(database_b.url, deployment_class="rehearsal")

    old_document = read_document(database_a)
    clean_document = read_document(database_b)
    old_without_legacy = normalize_document(
        old_document,
        manifest=load_exclusion_manifest(),
        side="old",
    )
    logical_old = project_logical_application_document(
        old_without_legacy,
        control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
    )
    logical_clean = project_logical_application_document(
        clean_document,
        control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
    )
    compare_documents(logical_old, logical_clean, exclusions=None)
```

Do not initialize either application or insert business data. Migration-owned schema marker/control rows are data and do not enter structural documents.

- [ ] **Step 2: Run the test and verify red on the first real discrepancy**

Run:

```bash
cd backend
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
    tests/test_schema_equivalence_postgres.py::test_old_head_normalized_equals_clean_root -q
```

Expected before the comparator/root is complete: failure reports one bounded difference class and an internal test-only object key. It must not silently produce a new exclusion.

- [ ] **Step 3: Validate Database A before normalization**

Require all of these facts:

```python
assert read_single_alembic_version(database_a) == "b6e2d4f8a901"
assert structural_fingerprint(old_document) == manifest.source_structural_fingerprint
assert set(manifest.object_keys) == set(expected_legacy_object_keys())
for item in manifest.objects:
    actual = old_document.object_by_key(item.key)
    assert actual.definition_digest == item.definition_digest
assert count_legacy_business_rows(database_a) == 0
assert known_inert_legacy_seed_rows(database_a) == 1
```

Only then remove exact manifest objects from the comparison document.

- [ ] **Step 4: Validate Database B control objects separately**

Require:

- live Alembic head `pre_ga_v1_0001` in the staged script environment;
- zero exclusion keys and zero table names from `LEGACY_TABLE_NAMES`;
- one marker row with family/revision/deployment class;
- exact marker-control fingerprint from expected manifest;
- recomputed version-2 application fingerprint from B equals the committed logical application fingerprint;
- recomputed runtime identity digest equals the marker row.

Only marker table/function/trigger objects are removed for A/B application comparison. Any second control object is an unmanifested difference.

- [ ] **Step 5: Compare exact canonical bytes, not only hash strings**

```python
left_bytes = canonical_json_bytes(logical_old.to_payload())
right_bytes = canonical_json_bytes(logical_clean.to_payload())
if left_bytes != right_bytes:
    raise SchemaComparisonError.from_documents(
        logical_old,
        logical_clean,
        safe_code="logical_application_schema_difference",
    )
assert hashlib.sha256(left_bytes).hexdigest() == expected_fingerprint
assert hashlib.sha256(right_bytes).hexdigest() == expected_fingerprint
```

The test-only error may list object keys and field paths but never database URLs or data. Production compatibility later returns only a bounded diagnostic code.

- [ ] **Step 6: Add mutation tests for every structural category**

After B upgrade, make one transactional mutation and prove comparison fails:

- column nullability/default/type;
- FK delete action;
- check constraint body;
- unique/index expression or predicate;
- function body;
- trigger enabled state;
- enum label order;
- sequence increment;
- view definition when a retained view exists;
- extra namespace or extension.

Rollback each mutation or recreate B. No mutation is accepted by extending a normalization rule.

- [ ] **Step 7: Implement verification runner modes**

```text
python scripts/verify_pre_ga_schema.py equivalence \
  --old-database-url-env MINDATLAS_SCHEMA_SOURCE_DATABASE_URL \
  --clean-database-url-env MINDATLAS_SCHEMA_CLEAN_DATABASE_URL

python scripts/verify_pre_ga_schema.py fresh \
  --clean-database-url-env MINDATLAS_SCHEMA_CLEAN_DATABASE_URL \
  --deployment-class rehearsal
```

The runner returns nonzero on any mismatch and prints only safe counts, revision IDs, and digests. Evidence writing is added in Task 12. There is no flag that ignores missing old objects or accepts the current fingerprint.

- [ ] **Step 8: Run complete equivalence and mutation suites**

Run:

```bash
MINDATLAS_SCHEMA_SOURCE_DATABASE_URL="$MINDATLAS_SCHEMA_SOURCE_DATABASE_URL" \
MINDATLAS_SCHEMA_CLEAN_DATABASE_URL="$MINDATLAS_SCHEMA_CLEAN_DATABASE_URL" \
  .venv/bin/python scripts/verify_pre_ga_schema.py equivalence \
    --old-database-url-env MINDATLAS_SCHEMA_SOURCE_DATABASE_URL \
    --clean-database-url-env MINDATLAS_SCHEMA_CLEAN_DATABASE_URL
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest tests/test_schema_equivalence_postgres.py -q
```

Expected: version-2 logical application bytes and fingerprints are identical; Database B contains no exclusion object; raw version-1 physical differences are preserved as evidence; all semantic drift cases fail as expected; no PostgreSQL test skips.

- [ ] **Step 9: Commit**

```bash
git add \
  backend/app/schema/canonical.py \
  backend/app/schema/exclusions.py \
  backend/scripts/verify_pre_ga_schema.py \
  backend/tests/schema_baseline_support.py \
  backend/tests/test_schema_equivalence_postgres.py
git commit -m "test(schema): prove clean root equivalence"
```

---

### Task 8: Archive the 60-Revision Lineage and Atomically Activate the Clean Root

**Files:**

- Create: `backend/scripts/archive_pre_ga_lineage.py`
- Create: `backend/alembic/archive/pre_ga_v1_superseded/README.md`
- Create: `backend/alembic/archive/pre_ga_v1_superseded/manifest.v1.json`
- Create: `backend/alembic/archive/pre_ga_v1_superseded/*.py.archived` (exactly 60 files)
- Create: `backend/alembic/versions/pre_ga_v1_0001_clean_baseline.py`
- Create: `backend/tests/test_schema_archive.py`
- Delete: `backend/alembic/baseline_staging/pre_ga_v1_0001_clean_baseline.py`
- Delete: all 60 old `backend/alembic/versions/*.py` revision files
- Modify: `backend/alembic.ini`
- Modify: `backend/alembic/env.py`

**Interfaces:**

- Consumes: exact old live revision graph, staged generated root, deviation digest, old-head snapshot digest, and clean equivalence proof.
- Produces: one non-importable verified archive, one manifest-locked 60-link old graph, explicit live `version_locations`, and sole live root/head `pre_ga_v1_0001`.

- [ ] **Step 1: Write archive graph/parser tests before moving files**

```python
def test_old_lineage_is_one_exact_60_revision_chain():
    graph = parse_revision_files(LIVE_VERSION_DIR.glob("*.py"))
    ordered = graph.require_linear_chain(final_head="b6e2d4f8a901")
    assert len(ordered) == 60
    assert ordered[0].revision == "a7d8f1424a99"
    assert ordered[0].parent is None
    assert ordered[-1].revision == "b6e2d4f8a901"
    for parent, child in pairwise(ordered):
        assert child.parent == parent.revision


def test_staged_root_is_not_connected_to_old_chain():
    revision = parse_revision_file(STAGED_ROOT)
    assert revision.revision == "pre_ga_v1_0001"
    assert revision.parent is None
```

Parse assignments with Python AST and `ast.literal_eval`; never import a revision file.

- [ ] **Step 2: Run archive tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_schema_archive.py -q
```

Expected: tests fail because archive script/manifest do not exist; pre-move graph assertions pass.

- [ ] **Step 3: Define the archive manifest exactly**

```python
archive_payload = {
    "schemaVersion": 1,
    "archiveId": "pre_ga_v1_superseded",
    "revisionCount": 60,
    "firstRevision": ordered[0].revision,
    "originalFinalHead": "b6e2d4f8a901",
    "archivalReason": "unpublished_lineage_replaced_by_first_supported_pre_ga_baseline",
    "designDeviationEvidenceDigest": sha256_file(DEVIATION_PATH),
    "preSquashSnapshotDigest": pre_squash_snapshot.manifest_digest,
    "revisions": [
        {
            "order": index,
            "relativePath": archive_relative_path(item),
            "originalRelativePath": item.relative_path,
            "revision": item.revision,
            "parent": item.parent,
            "sha256": sha256_file(item.path),
        }
        for index, item in enumerate(ordered, start=1)
    ],
}
manifest = {
    **archive_payload,
    "manifestDigest": sha256_canonical_json(archive_payload),
}
```

No authored timestamp or author machine path enters the manifest. The chronological order comes only from parent links, never lexicographic filenames.

- [ ] **Step 4: Implement a transactional filesystem activation**

`archive_pre_ga_lineage.py --write` performs:

1. require exact 60-link graph and no uncommitted change under the source revision directory, staged root, committed manifests, or deviation record;
2. verify all Task 3/5/6/7 manifests and staged root bytes;
3. copy each old file byte-for-byte into a sibling temporary archive using suffix `.py.archived`;
4. fsync and re-hash every copy;
5. write/fsync/validate temporary manifest and README;
6. copy staged root into a temporary live version directory;
7. verify that temporary Alembic directory has one root/head;
8. atomically rename the old live version directory to a rollback-temporary name, new live directory into place, and temporary archive into final place;
9. delete the rollback-temporary directory only after all postconditions pass.

On any pre-commit failure, restore the original live directory and leave no partial archive. The script refuses if archive destination already exists with different bytes.

- [ ] **Step 5: Make archived files non-importable**

The archive README states:

- artifacts preserve unpublished development history only;
- suffix `.py.archived` is intentional;
- the directory has no `__init__.py`;
- it is excluded from Alembic `version_locations`;
- it is not a supported upgrade/restore source;
- verification parses metadata and hashes without importing/executing files.

Tests assert no archive path ends in `.py`, `.pyc`, or contains `__pycache__`.

- [ ] **Step 6: Pin Alembic's sole live version location**

Set in `backend/alembic.ini`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
version_path_separator = os
version_locations = %(here)s/alembic/versions
```

Do not add the archive or a recursive glob. `env.py` loads only live model registry and contains no archive path logic.

- [ ] **Step 7: Execute archive activation**

Run:

```bash
cd backend
.venv/bin/python scripts/archive_pre_ga_lineage.py --write
.venv/bin/python scripts/archive_pre_ga_lineage.py --check
.venv/bin/alembic roots
.venv/bin/alembic heads
.venv/bin/alembic history --verbose
```

Expected: archive check reports exactly 60 entries/digests and one continuous old graph ending `b6e2d4f8a901`; live Alembic reports only root/head `pre_ga_v1_0001`; history contains no old revision.

- [ ] **Step 8: Prove ordinary Alembic cannot reach the old chain**

Run:

```bash
.venv/bin/alembic upgrade b6e2d4f8a901
```

Expected: nonzero exit with “Can't locate revision identified by `b6e2d4f8a901`” or Alembic's equivalent. The test asserts nonzero status and does not depend on exact punctuation.

Also assert:

```python
assert ScriptDirectory.from_config(config).get_revision("b6e2d4f8a901") is None
assert ScriptDirectory.from_config(config).get_revision("pre_ga_v1_0001") is not None
```

- [ ] **Step 9: Verify archive bytes and imports**

```python
def test_archive_manifest_rehashes_every_file():
    manifest = load_archive_manifest()
    assert manifest.revision_count == 60
    for item in manifest.revisions:
        assert sha256_file(REPO_ROOT / item.relative_path) == item.sha256


def test_no_live_python_import_or_alembic_location_reaches_archive():
    assert scan_python_imports(BACKEND_ROOT, "alembic.archive") == []
    assert "archive" not in configured_version_locations()
```

Parse each archived file's `revision`/`down_revision` AST from raw text and require it matches manifest. Ensure revision IDs and parents are unique.

- [ ] **Step 10: Re-run fresh root upgrade after archive activation**

Run:

```bash
MINDATLAS_DEPLOYMENT_CLASS=development \
DATABASE_URL="$MINDATLAS_SCHEMA_CLEAN_DATABASE_URL" \
  .venv/bin/alembic upgrade head
DATABASE_URL="$MINDATLAS_SCHEMA_CLEAN_DATABASE_URL" \
  .venv/bin/alembic current
```

Expected: an empty PostgreSQL database upgrades directly to `pre_ga_v1_0001` without any B2/Legacy acknowledgement and produces expected marker/fingerprint.

- [ ] **Step 11: Run archive, generator-check, and migration suites**

Run:

```bash
.venv/bin/python scripts/archive_pre_ga_lineage.py --check
MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL="$MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL" \
  .venv/bin/python scripts/generate_pre_ga_baseline.py \
    --database-url-env MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL \
    --output alembic/versions/pre_ga_v1_0001_clean_baseline.py \
    --check --check-expected-manifest
.venv/bin/python -m pytest \
  tests/test_schema_archive.py \
  tests/test_schema_baseline_generator.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
    tests/test_schema_baseline_migration_postgres.py -q
cd ..
git diff --check
```

Expected: every archive/root digest verifies, sole head/root is clean, fresh migration passes with no skip, and formatting is clean.

- [ ] **Step 12: Commit the atomic lineage replacement**

```bash
git add -A \
  backend/alembic.ini \
  backend/alembic/env.py \
  backend/alembic/versions \
  backend/alembic/baseline_staging \
  backend/alembic/archive/pre_ga_v1_superseded \
  backend/scripts/archive_pre_ga_lineage.py \
  backend/tests/test_schema_archive.py
git commit -m "feat(schema): replace old lineage with clean root"
```

---

### Task 9: Implement the Guarded Non-Production Rebaseline and Exact Stamp

**Files:**

- Create: `backend/app/schema/rebaseline.py`
- Create: `backend/scripts/rebaseline_pre_ga_v1.py`
- Create: `backend/tests/test_schema_rebaseline.py`
- Create: `backend/tests/test_schema_rebaseline_postgres.py`
- Modify: `backend/app/schema/identity.py`
- Modify: `backend/app/schema/exclusions.py`
- Modify: `backend/.env.example`
- Modify: `deploy/README.md`

**Interfaces:**

- Consumes: an existing database at exact old head `b6e2d4f8a901`, old/full and normalized fingerprints, exact exclusion manifest, current clean root, explicit environment plus database-local non-production identity, and a literal maintenance acknowledgement.
- Produces: read-only `inspect`, atomic `apply`, exact exclusion-object pruning, retained-data invariance proof, clean-family marker/stamp, idempotent already-clean result, and sanitized before/after report.

- [ ] **Step 1: Write the CLI surface test with no force option**

```python
def test_rebaseline_cli_has_only_inspect_and_apply():
    parser = build_parser()
    help_text = parser.format_help()
    assert "inspect" in help_text
    assert "apply" in help_text
    assert "--force" not in help_text
    assert "--skip" not in help_text


def test_apply_requires_exact_literal_acknowledgement():
    args = parse_apply_args(
        ["apply", "--acknowledge-local-maintenance", "yes"]
    )
    with pytest.raises(RebaselineRefused) as exc:
        validate_acknowledgement(args)
    assert exc.value.safe_code == "maintenance_acknowledgement_missing"
```

The accepted literal is exactly `I_ACKNOWLEDGE_THIS_IS_A_RESETTABLE_NON_PRODUCTION_DATABASE`. It is not secret and may appear in shell history.

- [ ] **Step 2: Write production/unknown/wrong-head rejection tests**

```python
@pytest.mark.parametrize(
    ("env_class", "database_comment", "safe_code"),
    [
        ("production", "mindatlas:deployment_class=production", "production_rebaseline_forbidden"),
        ("development", None, "database_deployment_identity_missing"),
        ("development", "mindatlas:deployment_class=shared", "database_deployment_identity_unknown"),
        ("development", "mindatlas:deployment_class=rehearsal", "deployment_identity_mismatch"),
    ],
)
def test_apply_rejects_non_local_identity(
    old_head_database,
    env_class,
    database_comment,
    safe_code,
):
    set_database_comment(old_head_database, database_comment)
    result = run_rebaseline(old_head_database, env_class=env_class)
    assert result.returncode != 0
    assert safe_code in result.stderr
    assert read_single_alembic_version(old_head_database) == "b6e2d4f8a901"
```

Add wrong/multiple/missing Alembic version tests. A database whose name looks local but lacks the exact comment is rejected.

- [ ] **Step 3: Run focused tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_schema_rebaseline.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest tests/test_schema_rebaseline_postgres.py -q
```

Expected: failures because service/CLI do not exist.

- [ ] **Step 4: Require two matching non-production identities**

`validate_deployment_identity()` requires:

1. process setting `MINDATLAS_DEPLOYMENT_CLASS` parses as `development` or `rehearsal`;
2. database-local comment from `shobj_description(pg_database.oid, 'pg_database')` is exactly `mindatlas:deployment_class=<same-value>`;
3. value is not `production`, `shared`, empty, or unknown;
4. explicit maintenance acknowledgement matches exactly;
5. database is not in recovery and current transaction is read-write.

The database comment is provisioned deliberately by the local/rehearsal database owner. The tool never creates or changes it. Production deployment documentation forbids the non-production comment.

- [ ] **Step 5: Acquire one transaction and advisory lock before inspection**

```python
REBASELINE_ADVISORY_LOCK_KEY = 0x4D41534348454D41


def apply_rebaseline(connection: Connection, request: RebaselineRequest) -> RebaselineReport:
    with connection.begin():
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": REBASELINE_ADVISORY_LOCK_KEY},
        )
        if acquired is not True:
            raise RebaselineRefused("rebaseline_lock_unavailable")
        return _apply_locked(connection, request)
```

Set `lock_timeout` and `statement_timeout` to committed bounded values with `SET LOCAL`. If any application session prevents required locks, abort without partial DDL.

- [ ] **Step 6: Verify exact old head and source fingerprint before mutation**

Under the lock:

```python
if read_single_alembic_version(connection) != PRE_SQUASH_HEAD:
    raise RebaselineRefused("pre_squash_head_mismatch")
source = PostgresCatalogReader(connection).read_document()
if structural_fingerprint(source) != exclusion_manifest.source_structural_fingerprint:
    raise RebaselineRefused("pre_squash_fingerprint_mismatch")
verify_manifest_objects(source, exclusion_manifest)
```

If the database is already exact `pre_ga_v1_0001` with matching marker/runtime identity, return `already_rebaselined` without writes. A clean revision with a missing/drifted marker is rejected rather than repaired.

- [ ] **Step 7: Enforce exact pre-clean data invariants**

Create named, parameter-free invariant queries and require all to pass:

```python
DATA_INVARIANTS = (
    DataInvariant(
        "main_agent_runs_only",
        "SELECT NOT EXISTS (SELECT 1 FROM assistant_chat_run "
        "WHERE runtime_kind <> 'main_agent')",
    ),
    DataInvariant(
        "run_runtime_identity_complete",
        "SELECT NOT EXISTS (SELECT 1 FROM assistant_chat_run WHERE "
        "main_agent_rollout_revision_id IS NULL OR "
        "main_agent_profile_version_id IS NULL OR resolved_model_id IS NULL OR "
        "runtime_closure_digest IS NULL OR runtime_contract_version IS NULL OR "
        "required_checkpoint_codec_version IS NULL OR "
        "required_capability_feature_digest IS NULL OR "
        "required_app_build_revision IS NULL)",
    ),
    DataInvariant(
        "l2_native_identity_complete",
        "SELECT NOT EXISTS (SELECT 1 "
        "FROM assistant_conversation_skill_l2_memory WHERE "
        "skill_package_id IS NULL OR memory_namespace IS NULL "
        "OR length(trim(memory_namespace)) = 0)",
    ),
    DataInvariant(
        "operator_singleton",
        "SELECT count(*) <= 1 FROM operator_account",
    ),
    DataInvariant(
        "new_rollout_control_singleton",
        "SELECT count(*) <= 1 FROM assistant_main_agent_rollout_control",
    ),
)
```

Also validate all live FK constraints are validated and no active Profile V1 is referenced by a Main Agent rollout. Use existing Plan 1/2 repository validators where they are pure reads; do not invoke a service that commits.

- [ ] **Step 8: Treat the old Plan 10 control seed explicitly and nothing else**

All exclusion tables must have zero rows except `assistant_runtime_rollout_control`, which must contain exactly the one migration-owned inert row:

```python
def is_known_inert_old_control(row: Mapping[str, object]) -> bool:
    return (
        row["singleton_key"] == "singleton"
        and row["active_rollout_revision_id"] is None
        and row["state_revision"] == 0
    )
```

Require no additional columns with non-default state as defined by the captured source snapshot. The tool deletes this one inert row immediately before dropping its table. Any rollout revision, assignment, migration item/event/batch, fallback, shadow, approval archive, cleanup gate, active pointer, or altered control row is `legacy_exclusion_data_present` and aborts.

The report records `removedKnownInertSeedRows: 1` and `removedLegacyBusinessRows: 0`. It never serializes the row.

- [ ] **Step 9: Snapshot retained data with an in-memory keyed checksum**

Before DDL, lock every retained application table in sorted order with `LOCK TABLE ... IN ACCESS EXCLUSIVE MODE`. Exclude only `alembic_version`, `mindatlas_schema_identity`, and the 11 manifest tables.

Stream `SELECT row_to_json(t)::text FROM <quoted table> AS t` with server-side cursors. For each row, compute `HMAC-SHA256(ephemeral_key, utf8_row_json)`, sort only the fixed-length row MACs, and HMAC their concatenation plus row count/table identity. Generate `ephemeral_key` with `secrets.token_bytes(32)` and never persist/log it.

```python
@dataclass(frozen=True)
class RetainedTableSnapshot:
    table_key: str
    row_count: int
    keyed_digest: bytes


def compare_snapshots(
    before: tuple[RetainedTableSnapshot, ...],
    after: tuple[RetainedTableSnapshot, ...],
) -> None:
    if before != after:
        raise RebaselineRefused("retained_data_changed")
```

Evidence contains only table count, aggregate row count, and `retainedDataUnchanged: true`; it contains no keyed digest or per-row material.

- [ ] **Step 10: Drop only manifest-verified objects without cascade**

Compute reverse table dependency order from the canonical document. Execute exact quoted names:

1. delete the one verified inert control row;
2. drop the 15 exact triggers;
3. drop 11 exact tables in child-first order with `DROP TABLE schema.name` and no `CASCADE`;
4. drop `mindatlas_reject_plan10_immutable_mutation()` with its exact identity arguments and no `CASCADE`.

After each category, re-introspect affected keys. Any dependency error rolls back the transaction. Never derive a drop target from a name prefix.

- [ ] **Step 11: Prove clean fingerprint before creating the marker or stamping**

```python
clean_application = PostgresCatalogReader(connection).read_document()
if contains_any_exclusion_object(clean_application, exclusion_manifest):
    raise RebaselineRefused("legacy_exclusion_object_remains")
if structural_fingerprint(clean_application) != expected.application_fingerprint:
    raise RebaselineRefused("clean_fingerprint_mismatch")
```

At this exact point there is no Legacy table, trigger, function, or data. Only then may the tool continue. This ordering prevents a database that still contains exclusions from ever receiving the clean family marker.

- [ ] **Step 12: Install marker control DDL, stamp on the same connection, and insert identity**

Use the same generated marker DDL literals as the root and verify their committed control fingerprint. Stamp through Alembic's API on the existing transaction/connection:

```python
migration_context = MigrationContext.configure(connection)
script = ScriptDirectory.from_config(load_live_alembic_config())
if script.get_revision(CLEAN_ROOT_REVISION) is None:
    raise RebaselineRefused("clean_root_unavailable")
migration_context.stamp(script, CLEAN_ROOT_REVISION)
insert_schema_identity(
    connection,
    deployment_class=request.deployment_class,
    expected=expected_contract,
)
```

Do not shell out to `alembic stamp` and do not directly update `alembic_version` with handwritten SQL. Recompute marker control and runtime identity after insertion.

- [ ] **Step 13: Recompute retained data and all postconditions before commit**

With locks still held:

```python
after = snapshot_retained_tables(connection, ephemeral_key)
compare_snapshots(before, after)
assert read_single_alembic_version(connection) == CLEAN_ROOT_REVISION
assert current_application_fingerprint(connection) == expected.application_fingerprint
assert current_marker_control_fingerprint(connection) == expected.marker_control_fingerprint
assert schema_identity_is_valid(connection, request.deployment_class)
```

If any assertion fails, raise a typed refusal so PostgreSQL rolls back DDL, row deletion, marker creation, and stamp together.

- [ ] **Step 14: Write a sanitized report with pre-commit path validation**

Before opening the database transaction, require the output parent exists, is owned/writable by the current process, and the destination does not exist unless its bytes match an idempotent prior result. Create/fsync a provisional file containing request-independent safe inputs and a unique operation ID. After database commit, atomically replace it with the final report and fsync the directory.

The final JSON allowlist is:

```python
SAFE_REPORT_FIELDS = {
    "schemaVersion",
    "operationId",
    "result",
    "deploymentClass",
    "beforeRevision",
    "afterRevision",
    "beforeStructuralFingerprint",
    "afterStructuralFingerprint",
    "runtimeIdentityDigest",
    "exclusionManifestDigest",
    "excludedObjectCount",
    "removedKnownInertSeedRows",
    "removedLegacyBusinessRows",
    "retainedTableCount",
    "retainedRowCount",
    "retainedDataUnchanged",
    "archiveManifestDigest",
    "buildRevision",
}
```

No database URL/name/comment, table row, password hash, session/audit row, business content, SQL text, or HMAC appears.

- [ ] **Step 15: Implement inspect/apply commands**

```text
python scripts/rebaseline_pre_ga_v1.py inspect \
  --database-url-env DATABASE_URL \
  --report-file ../docs/superpowers/evidence/local-pre-ga-rebaseline-inspect.json

python scripts/rebaseline_pre_ga_v1.py apply \
  --database-url-env DATABASE_URL \
  --report-file ../docs/superpowers/evidence/local-pre-ga-rebaseline-apply.json \
  --acknowledge-local-maintenance \
    I_ACKNOWLEDGE_THIS_IS_A_RESETTABLE_NON_PRODUCTION_DATABASE
```

`inspect` is always read-only and may safely report rejection codes. `apply` reads deployment class from validated settings/env, not a CLI enum that could disagree with runtime configuration.

- [ ] **Step 16: Test the complete acceptance/rejection matrix**

Required PostgreSQL cases:

- exact development old head succeeds and preserves retained fixture rows byte-for-byte;
- exact rehearsal old head succeeds;
- second `apply` returns `already_rebaselined` and changes no row/timestamp;
- production, shared, missing/mismatched database identity reject;
- wrong/missing/multiple head rejects;
- source structural drift rejects;
- exclusion definition drift or extra Legacy-prefix object rejects;
- any Legacy evidence/business row or non-inert old control rejects;
- invalid L2/Main-Agent/Operator invariant rejects;
- lock contention rejects without waiting beyond configured timeout;
- report path failure happens before database mutation;
- a forced retained-data snapshot mismatch rolls back;
- parser has no force/skip option.

After every rejected case, assert old revision, object set, marker absence, and retained-data snapshot are unchanged.

- [ ] **Step 17: Run focused unit/PostgreSQL tests and one disposable apply**

Run:

```bash
.venv/bin/python -m pytest tests/test_schema_rebaseline.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest tests/test_schema_rebaseline_postgres.py -q
MINDATLAS_DEPLOYMENT_CLASS=development \
  .venv/bin/python scripts/rebaseline_pre_ga_v1.py inspect \
    --database-url-env MINDATLAS_SCHEMA_REBASELINE_DATABASE_URL \
    --report-file /tmp/mindatlas-pre-ga-rebaseline-inspect.json
```

Expected: all tests execute/pass; inspect returns eligible or an exact bounded rejection for the deliberately arranged database; no URL/content is printed.

- [ ] **Step 18: Commit**

```bash
git add \
  backend/app/schema/rebaseline.py \
  backend/app/schema/identity.py \
  backend/app/schema/exclusions.py \
  backend/scripts/rebaseline_pre_ga_v1.py \
  backend/tests/test_schema_rebaseline.py \
  backend/tests/test_schema_rebaseline_postgres.py \
  backend/.env.example \
  deploy/README.md
git commit -m "feat(schema): guard non-production rebaseline"
```

---

### Task 10: Replace Head-Only Checks with Family-Bound API and Worker Compatibility

**Files:**

- Create: `backend/app/schema/compatibility.py`
- Create: `backend/tests/test_runtime_schema_compatibility.py`
- Create: `backend/tests/test_runtime_schema_compatibility_postgres.py`
- Modify: `backend/app/schema/__init__.py`
- Modify: `backend/app/assistant/runtime/readiness.py`
- Modify: `backend/app/assistant/worker.py`
- Modify: `backend/app/assistant/durable/leases.py`
- Modify: `backend/tests/test_assistant_worker_runtime_compatibility.py`
- Modify: `backend/tests/test_assistant_worker_claim_compatibility_postgres.py`
- Modify: `backend/tests/test_health_readiness_api.py`

**Interfaces:**

- Consumes: unchanged Plan 2 `RuntimeSchemaCompatibility` protocol, expected manifest, canonical catalog reader, identity marker, runtime settings/build revision, readiness, Worker startup, and claim paths.
- Produces: `FamilyBoundRuntimeSchemaCompatibility`, one factory consumed by API and Worker, full compatibility snapshots, stable `schema_incompatible` behavior, and zero claim/admission on mismatch.

- [ ] **Step 1: Write the complete compatibility matrix before implementation**

```python
@pytest.mark.parametrize(
    ("drift", "diagnostic"),
    [
        ("missing_marker", "marker_missing"),
        ("multiple_alembic_heads", "head_ambiguous"),
        ("wrong_family", "family_mismatch"),
        ("old_revision", "revision_incompatible"),
        ("fingerprint", "fingerprint_mismatch"),
        ("marker_control", "marker_control_mismatch"),
        ("runtime_identity", "runtime_identity_mismatch"),
        ("seed_contract", "seed_contract_mismatch"),
        ("runtime_contract", "runtime_contract_mismatch"),
        ("checkpoint_codec", "checkpoint_codec_mismatch"),
        ("capability_feature", "capability_feature_mismatch"),
        ("operator_auth", "operator_auth_contract_mismatch"),
        ("deployment_class", "deployment_class_mismatch"),
        ("unknown_build", "build_identity_invalid"),
        ("legacy_object", "legacy_object_present"),
    ],
)
def test_family_compatibility_fails_closed(schema_state, drift, diagnostic):
    schema_state.apply(drift)
    snapshot = schema_state.compatibility.evaluate(schema_state.db)
    assert snapshot.compatible is False
    assert snapshot.safe_reason == "schema_incompatible"
    assert snapshot.diagnostic_code == diagnostic
```

- [ ] **Step 2: Run focused tests and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_runtime_schema_compatibility.py -q
```

Expected: failure because only Plan 2's head-only implementation exists.

- [ ] **Step 3: Implement a code-owned compatibility requirement**

```python
@dataclass(frozen=True)
class SchemaCompatibilityRequirement:
    schema_family: str
    minimum_revision_ordinal: int
    compatible_revisions: Mapping[str, int]
    expected_application_fingerprints: Mapping[str, str]
    expected_marker_control_fingerprints: Mapping[str, str]
    seed_contract_digest: str
    runtime_contract_version: int
    checkpoint_codec_version: int
    capability_feature_digest: str
    operator_auth_contract_version: str


PLAN3_SCHEMA_REQUIREMENT = SchemaCompatibilityRequirement(
    schema_family="pre_ga_v1",
    minimum_revision_ordinal=1,
    compatible_revisions={"pre_ga_v1_0001": 1},
    expected_application_fingerprints={
        "pre_ga_v1_0001": load_expected_manifest().application_fingerprint,
    },
    expected_marker_control_fingerprints={
        "pre_ga_v1_0001": load_expected_manifest().marker_control_fingerprint,
    },
    seed_contract_digest=SEED_CONTRACT_DIGEST,
    runtime_contract_version=RUNTIME_CONTRACT_VERSION,
    checkpoint_codec_version=CURRENT_CHECKPOINT_CODEC_VERSION,
    capability_feature_digest=default_capability_feature_digest(),
    operator_auth_contract_version=OPERATOR_AUTH_CONTRACT_VERSION,
)
```

Plan 4 replaces this constant with minimum/exact revision `pre_ga_v1_0002` and its generated fingerprints. Unknown later revisions never pass merely because their names sort after the minimum.

- [ ] **Step 4: Evaluate every required dimension with no positive cache**

`FamilyBoundRuntimeSchemaCompatibility.evaluate(db)` performs, in order:

1. load/validate committed expected manifest;
2. read exactly one Alembic version;
3. read/validate exactly one marker;
4. match settings deployment class to marker;
5. require known revision ordinal at or above minimum;
6. reject any exact Legacy exclusion key;
7. introspect and recompute application fingerprint;
8. recompute marker-control fingerprint;
9. match expected seed/runtime/codec/feature/auth contracts;
10. recompute runtime identity digest;
11. require a non-empty immutable build revision, and reject literal `development` for rehearsal/production.

Do not cache a positive result between calls in Plan 3. API readiness and each Worker claim therefore observe catalog/marker drift before admission/lease mutation. One evaluation may cache catalog subqueries only within its current transaction.

- [ ] **Step 5: Keep the existing protocol signature stable**

```python
class FamilyBoundRuntimeSchemaCompatibility:
    def evaluate(self, db: Session) -> SchemaCompatibilitySnapshot:
        try:
            return self._evaluate(db)
        except (SQLAlchemyError, SchemaIdentityError, CatalogReadError):
            return incompatible_snapshot("catalog_unavailable")

    def is_compatible(self, db: Session) -> bool:
        return self.evaluate(db).compatible
```

Remove `Plan2AlembicHeadCompatibility` and make `runtime_schema_compatibility()` return a process-immutable singleton service with code-owned requirement. The service itself holds no database session and no mutable compatibility result.

- [ ] **Step 6: Map all failures to safe readiness**

`AssistantReadinessService` continues:

```python
if not self.schema_compatibility.is_compatible(self.db):
    return self._blocked("schema_incompatible")
```

Authenticated diagnostics may include bounded `diagnosticCode`, family, revision, deployment class, and digests. Public `/ready` includes only `ready=false` and reason `schema_incompatible`. `/health` remains database-free and returns process liveness even when catalog reads fail.

- [ ] **Step 7: Fail Worker startup before registration**

```python
snapshot = runtime_schema_compatibility().evaluate(db)
if not snapshot.compatible:
    logger.error(
        "assistant_worker_schema_incompatible diagnostic=%s",
        snapshot.diagnostic_code,
    )
    return WORKER_SCHEMA_INCOMPATIBLE_EXIT
registry.register(identity)
```

Log only bounded diagnostic code, current family/revision if safely parsed, and build revision. Never log SQL, exception text, marker raw row, or object definitions.

- [ ] **Step 8: Recheck immediately before every Worker claim mutation**

Inside the same transaction that selects/locks a candidate Run:

```python
if not runtime_schema_compatibility().is_compatible(self.db):
    self.db.rollback()
    return None
candidate = self._lock_next_candidate()
if candidate is None:
    return None
if not runtime_schema_compatibility().is_compatible(self.db):
    self.db.rollback()
    return None
return self._claim_locked(candidate)
```

The second check closes catalog/marker drift between queue scan and lease update. A mismatch leaves status, owner, generation, heartbeat, and events unchanged. Retain Plan 2's frozen Run/Worker build-contract check as an independent gate.

- [ ] **Step 9: Prove API admission and activation also remain blocked**

Plan 2 readiness is shared by activation candidate evaluation and Chat admission. Add tests that drift the schema after a compatible Worker registers, then assert:

- `/health` remains 200;
- `/ready` is 503 with only `schema_incompatible`;
- authenticated readiness has one bounded diagnostic;
- activation/prepare/new-Run mutations return mapped safe 503;
- zero Message, Run, rollout event/control change, or audit content leak occurs.

- [ ] **Step 10: Prove incompatible Workers register/claim nothing**

PostgreSQL tests cover wrong family, revision, structural fingerprint, marker control, runtime identity, deployment class, and build identity. For each:

```python
before = run_state(postgres_runtime, run_id)
assert start_worker(postgres_runtime) == WORKER_SCHEMA_INCOMPATIBLE_EXIT
assert worker_registration_count(postgres_runtime) == 0
assert claim_once(postgres_runtime) is None
assert run_state(postgres_runtime, run_id) == before
```

Also test a catalog permission denial. The worker reports `catalog_unavailable` internally and claims nothing; test restores permissions in a finalizer.

- [ ] **Step 11: Run compatibility, readiness, startup, and claim suites**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_runtime_schema_compatibility.py \
  tests/test_health_readiness_api.py \
  tests/test_assistant_worker_runtime_compatibility.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest \
    tests/test_runtime_schema_compatibility_postgres.py \
    tests/test_assistant_worker_claim_compatibility_postgres.py -q
```

Expected: all tests pass with no PostgreSQL skip; every mismatch blocks API readiness/admission and Worker registration/claim while `/health` remains process-only.

- [ ] **Step 12: Commit**

```bash
git add \
  backend/app/schema/__init__.py \
  backend/app/schema/compatibility.py \
  backend/app/assistant/runtime/readiness.py \
  backend/app/assistant/worker.py \
  backend/app/assistant/durable/leases.py \
  backend/tests/test_runtime_schema_compatibility.py \
  backend/tests/test_runtime_schema_compatibility_postgres.py \
  backend/tests/test_assistant_worker_runtime_compatibility.py \
  backend/tests/test_assistant_worker_claim_compatibility_postgres.py \
  backend/tests/test_health_readiness_api.py
git commit -m "feat(schema): enforce family-bound runtime compatibility"
```

---

### Task 11: Make Fresh Clean Migration and Schema Verification Release-Critical in CI

**Files:**

- Create: `backend/tests/test_deploy_migrate_clean_only.py`
- Create: `backend/tests/test_schema_ci_contract.py`
- Create: `backend/scripts/schema_database_state.py`
- Modify: `deploy/migrate.sh`
- Modify: `deploy/docker-compose.yml`
- Modify: `deploy/docker-compose.override.yml`
- Modify: `backend/.env.example`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_schema_equivalence_postgres.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**

- Consumes: sole clean root, archive verifier, generator check, snapshot-vs-root equivalence, family compatibility, PostgreSQL 15, and explicit deployment class.
- Produces: clean-only deployment migration behavior, deterministic CI job with no Legacy acknowledgement, no release-critical PostgreSQL skip, fresh upgrade/downgrade guard coverage, and ongoing root-vs-captured-old-schema proof after archival.

- [ ] **Step 1: Characterize and fail the current auto-stamp behavior**

```python
def test_migrate_script_refuses_nonempty_unversioned_database(
    unversioned_postgres_with_table,
):
    result = run_deploy_migrate(unversioned_postgres_with_table.url)
    assert result.returncode != 0
    assert "unsupported_nonempty_unversioned_database" in result.stderr
    assert not has_alembic_version(unversioned_postgres_with_table)


def test_migrate_script_upgrades_empty_database_directly(empty_postgres_database):
    result = run_deploy_migrate(
        empty_postgres_database.url,
        deployment_class="development",
    )
    assert result.returncode == 0
    assert read_single_alembic_version(empty_postgres_database) == "pre_ga_v1_0001"
```

- [ ] **Step 2: Run the focused test and verify red**

Run:

```bash
cd backend
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv/bin/python -m pytest tests/test_deploy_migrate_clean_only.py -q
```

Expected: the non-empty unversioned case currently succeeds by stamping and therefore fails the assertion.

- [ ] **Step 3: Replace `deploy/migrate.sh` with clean-only branching**

The script logic becomes:

```sh
#!/bin/sh
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${MINDATLAS_DEPLOYMENT_CLASS:?MINDATLAS_DEPLOYMENT_CLASS is required}"

case "$MINDATLAS_DEPLOYMENT_CLASS" in
  development|rehearsal|production) ;;
  *) echo "schema_deployment_class_invalid" >&2; exit 64 ;;
esac

status="$(python scripts/schema_database_state.py --database-url-env DATABASE_URL)"
case "$status" in
  empty)
    alembic upgrade head
    ;;
  versioned)
    alembic upgrade head
    ;;
  nonempty_unversioned)
    echo "unsupported_nonempty_unversioned_database" >&2
    exit 65
    ;;
  *)
    echo "schema_database_state_unknown" >&2
    exit 66
    ;;
esac

python scripts/verify_pre_ga_schema.py runtime \
  --database-url-env DATABASE_URL
```

Implement the read-only `backend/scripts/schema_database_state.py` helper used above. It prints exactly one enum word and never prints a URL/table name. There is no inline database Python and no `alembic stamp` invocation.

- [ ] **Step 4: Test all deployment migration states**

Required cases:

- empty database upgrades to root;
- exact root is idempotently upgraded/verified;
- older same-family revision is upgraded only when a live forward revision exists (Plan 3 has none before root, so no fabricated case);
- non-empty unversioned database rejects;
- old head `b6e2d4f8a901` rejects because current Alembic cannot locate it and the script never calls rebaseline;
- wrong-family marker rejects;
- missing/invalid deployment class rejects;
- database/catalog connection failure rejects without exposing URL.

The optional guarded rebaseline remains a separately invoked maintenance command and is never called by deployment startup.

- [ ] **Step 5: Set explicit Compose deployment identity**

The production-like Compose base leaves the deployment identity empty so
missing configuration fails closed. Development Compose sets the value only
through the local override:

```yaml
environment:
  MINDATLAS_DEPLOYMENT_CLASS: development
```

The main-agent smoke overlay sets `rehearsal` explicitly. No image default is
production. Postgres/API/Worker services receive the same value; tests assert
disagreement makes runtime compatibility false.

- [ ] **Step 6: Convert post-archive equivalence to snapshot-vs-root proof**

After Task 8, tests must not execute archived revisions. Replace the live-old-chain fixture with:

```python
def test_clean_root_matches_committed_normalized_old_snapshot(
    empty_postgres_database,
):
    upgrade_clean_root(empty_postgres_database, "rehearsal")
    clean = strip_schema_identity_control(read_document(empty_postgres_database))
    old_normalized = load_pre_squash_snapshot().normalized_application_document
    assert_documents_equal(old_normalized, clean)
```

The archive manifest binds all 60 old bytes to the pre-squash snapshot digest. `test_schema_archive.py` parses/hashes archive bytes but never imports or executes them. The one-time A/B proof from Task 7 remains represented by the committed byte-equal version-2 logical application document/fingerprint and final evidence; raw version-1 documents are not claimed to be identical.

- [ ] **Step 7: Make missing PostgreSQL prerequisites a failure in release-critical files**

Mark schema PostgreSQL tests with `@pytest.mark.schema_release_postgres`. In `conftest.py`, when `MINDATLAS_REQUIRE_SCHEMA_POSTGRES=1`, convert a missing URL/service/setup or skip of that marker into a session failure:

```python
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if (
        os.getenv("MINDATLAS_REQUIRE_SCHEMA_POSTGRES") == "1"
        and report.when == "setup"
        and report.skipped
        and "schema_release_postgres" in report.keywords
    ):
        pytest.fail("release-critical schema PostgreSQL test skipped", pytrace=False)
```

Prefer fixtures that raise a setup error over `pytest.skip`. The CI command additionally checks pytest's collected/executed counts.

- [ ] **Step 8: Add a dedicated PostgreSQL 15 CI job**

The job provisions separate databases for generator, fresh root, compatibility, and rebaseline tests. Its core commands are:

```yaml
- name: Verify clean schema artifacts
  working-directory: backend
  env:
    MINDATLAS_REQUIRE_SCHEMA_POSTGRES: "1"
    MINDATLAS_DEPLOYMENT_CLASS: rehearsal
  run: |
    set -euo pipefail
    python scripts/archive_pre_ga_lineage.py --check
    python scripts/generate_pre_ga_baseline.py \
      --database-url-env MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL \
      --output alembic/versions/pre_ga_v1_0001_clean_baseline.py \
      --check --check-expected-manifest
    alembic roots
    alembic heads
    python -m pytest -q \
      tests/test_schema_catalog_postgres.py \
      tests/test_schema_baseline_migration_postgres.py \
      tests/test_schema_equivalence_postgres.py \
      tests/test_schema_rebaseline_postgres.py \
      tests/test_runtime_schema_compatibility_postgres.py \
      tests/test_deploy_migrate_clean_only.py
```

The service is PostgreSQL 15. The job creates databases through `createdb`/`psql` using CI-only credentials and never prints URL values.

- [ ] **Step 9: Remove old-chain downgrade/acknowledgement CI**

Delete the job steps that downgrade to `a7b8c9d0e1f2`, set Plan 08/09/10 destructive acknowledgements, or run archived migration repository suites. Keep unrelated current PostgreSQL capability/durability tests and point their setup to a fresh clean root.

CI must not copy `.py.archived` back to `.py`, add the archive as `version_locations`, or execute old revisions.

- [ ] **Step 10: Add CI contract tests over workflow and scripts**

```python
def test_ci_has_release_critical_clean_schema_job():
    workflow = CI_PATH.read_text("utf-8")
    assert "MINDATLAS_REQUIRE_SCHEMA_POSTGRES: \"1\"" in workflow
    assert "test_schema_equivalence_postgres.py" in workflow
    assert "archive_pre_ga_lineage.py --check" in workflow
    assert "MINDATLAS_PLAN10_" not in workflow


def test_deploy_migration_has_no_stamp_path():
    source = DEPLOY_MIGRATE.read_text("utf-8")
    assert "alembic stamp" not in source
    assert "unsupported_nonempty_unversioned_database" in source
```

- [ ] **Step 11: Run focused deployment/CI tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_schema_ci_contract.py -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
MINDATLAS_REQUIRE_SCHEMA_POSTGRES=1 \
  .venv/bin/python -m pytest \
    tests/test_deploy_migrate_clean_only.py \
    tests/test_schema_baseline_migration_postgres.py \
    tests/test_schema_equivalence_postgres.py \
    tests/test_runtime_schema_compatibility_postgres.py -q
cd ..
git diff --check
```

Expected: all marked PostgreSQL tests execute/pass, deployment refuses auto-stamp, CI contract has no Plan 10 acknowledgement, and formatting is clean.

- [ ] **Step 12: Commit**

```bash
git add \
  deploy/migrate.sh \
  deploy/docker-compose.yml \
  deploy/docker-compose.override.yml \
  backend/.env.example \
  backend/scripts/schema_database_state.py \
  backend/tests/conftest.py \
  backend/tests/test_deploy_migrate_clean_only.py \
  backend/tests/test_schema_ci_contract.py \
  backend/tests/test_schema_equivalence_postgres.py \
  .github/workflows/ci.yml
git commit -m "ci(schema): gate fresh clean baseline"
```

---

### Task 12: Run Clean-Install Exit Verification and Produce Safe Schema Evidence

**Files:**

- Create: `docs/superpowers/evidence/2026-07-28-pre-ga-clean-baseline.json`
- Modify: `backend/scripts/verify_pre_ga_schema.py`
- Create: `backend/tests/test_schema_evidence.py`
- Modify: `deploy/README.md`

**Interfaces:**

- Consumes: all Plan 3 implementation commits, fresh PostgreSQL 15 databases, archive/generator/manifest checks, complete test suites, Node/npm frontend toolchain, and safe evidence allowlist.
- Produces: one reproducible clean-install exit command, one allowlisted evidence artifact, and the verified handoff contract for Plan 4 revision `pre_ga_v1_0002`.

- [ ] **Step 1: Write evidence allowlist tests first**

```python
ALLOWED_EVIDENCE_KEYS = {
    "schemaVersion",
    "schemaFamily",
    "schemaRevision",
    "applicationStructuralFingerprint",
    "schemaIdentityControlFingerprint",
    "runtimeIdentityDigest",
    "seedContractDigest",
    "deploymentClass",
    "runtimeContractVersion",
    "checkpointCodecVersion",
    "capabilityFeatureDigest",
    "operatorAuthContractVersion",
    "oldRevisionCount",
    "oldFinalHead",
    "archiveManifestDigest",
    "archiveVerified",
    "exclusionObjectCount",
    "exclusionManifestDigest",
    "logicalEquivalenceVerified",
    "freshUpgradeVerified",
    "testOnlyDowngradeGuardVerified",
    "guardedRebaselineMatrixVerified",
    "wrongFamilyRejected",
    "workerClaimRejectedOnDrift",
    "deployAutoStampAbsent",
    "postgresMajor",
    "buildRevision",
    "verificationDigest",
}


def test_schema_evidence_is_allowlisted_and_self_digesting():
    payload = json.loads(EVIDENCE_PATH.read_text("utf-8"))
    assert set(payload) == ALLOWED_EVIDENCE_KEYS
    claimed = payload.pop("verificationDigest")
    assert claimed == sha256_canonical_json(payload)
```

- [ ] **Step 2: Run the evidence test and verify red**

Run:

```bash
cd backend
.venv/bin/python -m pytest tests/test_schema_evidence.py -q
```

Expected: failure because final evidence does not exist.

- [ ] **Step 3: Add a fixed `exit` verification mode**

```text
python scripts/verify_pre_ga_schema.py exit \
  --fresh-database-url-env MINDATLAS_SCHEMA_CLEAN_DATABASE_URL \
  --rebaseline-database-url-env MINDATLAS_SCHEMA_REBASELINE_DATABASE_URL \
  --deployment-class rehearsal \
  --output ../docs/superpowers/evidence/2026-07-28-pre-ga-clean-baseline.json \
  --proof-file "$RUNNER_TEMP/pre-ga-schema-exit-proof.json"
```

The mode performs or consumes machine-readable results for:

- manifest self-digests and expected constants;
- archive 60-link/file digest verification;
- clean root generator byte check;
- empty direct upgrade and marker/runtime identity;
- committed old version-2 logical application contract versus fresh-root version-2 equality;
- no Legacy exclusion object;
- root downgrade refusal/success/re-upgrade on a disposable empty database;
- guarded rebaseline rejection matrix and one success on disposable non-production data;
- wrong-family API/Worker rejection;
- deploy migration no-stamp source contract.

It refuses to overwrite an existing different evidence file unless all verification steps pass and then uses atomic replacement. There is no evidence-only or accept-current mode.

- [ ] **Step 4: Sanitize evidence construction by schema**

Construct a frozen Pydantic model with `extra='forbid'`. Every digest is lowercase 64-hex; booleans must be true; revision/count values are exact. The writer has no field accepting SQL, database URLs, exceptions, object definitions, data checksums, passwords, tokens, prompts, entries, artifacts, or raw rows.

Before write, recursively scan string values for URL schemes, `password`, `token`, `cookie`, `authorization`, PEM headers, and known test secret literals. A match aborts evidence generation.

- [ ] **Step 5: Run clean Python 3.11 installation**

From repository root:

```bash
rm -rf backend/.venv-plan3-clean
python3.11 -m venv backend/.venv-plan3-clean
backend/.venv-plan3-clean/bin/python -m pip install --upgrade pip
backend/.venv-plan3-clean/bin/python -m pip install -r backend/requirements.txt pytest
backend/.venv-plan3-clean/bin/python -m pip check
```

Expected: install and `pip check` succeed. Plan 4 introduces split lockfiles; Plan 3 verifies the current dependency source without claiming lock reproducibility.

- [ ] **Step 6: Run artifact, root, and focused PostgreSQL gates in the clean environment**

```bash
cd backend
.venv-plan3-clean/bin/python scripts/archive_pre_ga_lineage.py --check
MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL="$MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL" \
  .venv-plan3-clean/bin/python scripts/generate_pre_ga_baseline.py \
    --database-url-env MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL \
    --output alembic/versions/pre_ga_v1_0001_clean_baseline.py \
    --check --check-expected-manifest
.venv-plan3-clean/bin/alembic roots
.venv-plan3-clean/bin/alembic heads
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
MINDATLAS_REQUIRE_SCHEMA_POSTGRES=1 \
  .venv-plan3-clean/bin/python -m pytest -q \
    tests/test_schema_catalog_postgres.py \
    tests/test_schema_baseline_migration_postgres.py \
    tests/test_schema_equivalence_postgres.py \
    tests/test_schema_rebaseline_postgres.py \
    tests/test_runtime_schema_compatibility_postgres.py \
    tests/test_deploy_migrate_clean_only.py
```

Expected: roots/heads both show only `pre_ga_v1_0001`; all release-critical tests execute and pass.

- [ ] **Step 7: Run full backend and frontend regression suites**

```bash
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
  .venv-plan3-clean/bin/python -m pytest -q
cd ../frontend
npm ci
npm test
npm run build
cd ..
```

Expected: full backend passes, frontend tests pass, and production frontend build succeeds. No deleted Legacy test is restored to make the count match an earlier run.

- [ ] **Step 8: Generate final Plan 3 evidence**

```bash
cd backend
MINDATLAS_SCHEMA_CLEAN_DATABASE_URL="$MINDATLAS_SCHEMA_CLEAN_DATABASE_URL" \
MINDATLAS_SCHEMA_REBASELINE_DATABASE_URL="$MINDATLAS_SCHEMA_REBASELINE_DATABASE_URL" \
MINDATLAS_DEPLOYMENT_CLASS=rehearsal \
  .venv-plan3-clean/bin/python scripts/verify_pre_ga_schema.py exit \
    --fresh-database-url-env MINDATLAS_SCHEMA_CLEAN_DATABASE_URL \
    --rebaseline-database-url-env MINDATLAS_SCHEMA_REBASELINE_DATABASE_URL \
    --deployment-class rehearsal \
    --output ../docs/superpowers/evidence/2026-07-28-pre-ga-clean-baseline.json \
    --proof-file "$RUNNER_TEMP/pre-ga-schema-exit-proof.json"
.venv-plan3-clean/bin/python -m pytest tests/test_schema_evidence.py -q
```

Expected: evidence validates, all required booleans are true, revision count is 60, exclusion count is 27, and no secret/content scanner finding occurs.

- [ ] **Step 9: Perform final source, revision, and formatting scans**

Run:

```bash
git status --short
find backend/alembic/versions -maxdepth 1 -type f -name '*.py' -print
find backend/alembic/archive/pre_ga_v1_superseded \
  -maxdepth 1 -type f -name '*.py.archived' | wc -l
rg -n 'b6e2d4f8a901|9f3c1a7e2b40' backend/alembic/versions || true
rg -n 'app\.assistant\.migration|MINDATLAS_PLAN10_|alembic stamp head' \
  backend/app backend/scripts deploy .github/workflows backend/tests || true
rg -n 'pre_ga_v1_0002' backend/alembic/versions || true
git diff --check
```

Expected:

- before adding evidence, only the evidence JSON is untracked/modified;
- exactly one live revision file is printed;
- archive count is exactly 60;
- no old revision ID in root, no Legacy package/Plan10 acknowledgement/auto-stamp in live paths;
- no Plan 4 revision exists;
- `git diff --check` prints nothing.

- [ ] **Step 10: Review rollback and Plan 4 handoff**

Confirm in `deploy/README.md`:

- fresh database reset/restore is the default recovery before GA;
- clean root never downgrades to Legacy;
- test-only downgrade is destructive and not an operational rollback;
- guarded rebaseline is non-production local maintenance only;
- API/Worker mismatch is fixed forward or by restoring a compatible clean-family backup;
- Plan 4 must create `pre_ga_v1_0002`, update expected fingerprints/identity through the guarded marker advance, and must not edit `pre_ga_v1_0001`.

- [ ] **Step 11: Commit final verification evidence**

```bash
git add \
  backend/scripts/verify_pre_ga_schema.py \
  backend/tests/test_schema_evidence.py \
  deploy/README.md \
  docs/superpowers/evidence/2026-07-28-pre-ga-clean-baseline.json
git commit -m "test(schema): attest clean pre-ga baseline"
```

---

## Plan 3 Exit Gate

Run from a fresh checkout with Python 3.11, PostgreSQL 15, Docker, Node/npm, and disposable databases:

```bash
git status --short
python3.11 -m venv backend/.venv-plan3-exit
backend/.venv-plan3-exit/bin/python -m pip install --upgrade pip
backend/.venv-plan3-exit/bin/python -m pip install -r backend/requirements.txt pytest
backend/.venv-plan3-exit/bin/python -m pip check
cd backend
.venv-plan3-exit/bin/python scripts/archive_pre_ga_lineage.py --check
MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL="$MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL" \
  .venv-plan3-exit/bin/python scripts/generate_pre_ga_baseline.py \
    --database-url-env MINDATLAS_SCHEMA_GENERATOR_DATABASE_URL \
    --output alembic/versions/pre_ga_v1_0001_clean_baseline.py \
    --check --check-expected-manifest
.venv-plan3-exit/bin/alembic heads
.venv-plan3-exit/bin/python - <<'PY'
import ast
from pathlib import Path

roots = []
for path in Path("alembic/versions").glob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = {
        node.targets[0].id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
        and isinstance(node.value, ast.Constant)
    }
    if values.get("down_revision") is None:
        roots.append(values.get("revision"))
assert roots == ["pre_ga_v1_0001"]
PY
.venv-plan3-exit/bin/python -m pytest -q
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
MINDATLAS_REQUIRE_SCHEMA_POSTGRES=1 \
  .venv-plan3-exit/bin/python -m pytest -q \
    tests/test_schema_catalog_postgres.py \
    tests/test_schema_baseline_migration_postgres.py \
    tests/test_schema_equivalence_postgres.py \
    tests/test_schema_rebaseline_postgres.py \
    tests/test_runtime_schema_compatibility_postgres.py \
    tests/test_deploy_migrate_clean_only.py
MINDATLAS_SCHEMA_FRESH_DATABASE_URL="$MINDATLAS_SCHEMA_FRESH_DATABASE_URL" \
MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL="$MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL" \
MINDATLAS_DEPLOYMENT_CLASS=rehearsal \
  .venv-plan3-exit/bin/python scripts/verify_pre_ga_schema.py exit \
    --fresh-database-url-env MINDATLAS_SCHEMA_FRESH_DATABASE_URL \
    --rebaseline-database-url-env MINDATLAS_SCHEMA_REBASELINE_SOURCE_URL \
    --deployment-class rehearsal \
    --output ../docs/superpowers/evidence/2026-07-28-pre-ga-clean-baseline.json \
    --proof-file "$RUNNER_TEMP/pre-ga-schema-exit-proof.json"
cd ../frontend
npm ci
npm test
npm run build
cd ..
git diff --check
```

Expected:

- the clean install and `pip check` pass under Python 3.11;
- Alembic has one configured root/head, both `pre_ga_v1_0001`;
- an empty PostgreSQL database upgrades directly without B2/Legacy acknowledgement;
- the root contains no old revision ID or excluded Legacy object;
- the archived graph has exactly 60 continuous, uniquely identified, re-hashed files ending at `b6e2d4f8a901`;
- ordinary Alembic cannot locate an old revision and no live code/test imports the archive;
- the exact old and fresh-root version-2 logical application documents are byte-equal, while raw version-1 physical evidence remains independently verified;
- all 27 exclusions match old definition digests and are absent from the clean database;
- marker control definition, family, revision, structural fingerprint, deployment class, seed/runtime/codec/feature/auth contracts, and runtime identity all match;
- guarded rebaseline preserves all retained row counts/keyed checksums, removes only one known inert Legacy seed row, and rejects production/shared/unknown/drifted/nonempty-Legacy cases;
- deployment startup never auto-stamps;
- API readiness/admission and Worker startup/claim reject wrong family or drift with stable `schema_incompatible`;
- release-critical PostgreSQL tests execute with no skip;
- full backend/frontend tests and frontend build pass;
- evidence contains only allowlisted safe fields and verifies its own digest;
- `git diff --check` is clean.

## Post-Audit Completion Record (2026-08-10)

The external PR audit was checked against the live tree. The following
release-facing gaps are now closed:

- [x] Unified all destructive PostgreSQL fixtures, CI services, and temporary
  databases under the `mindatlas_test_pre_ga_v1_` prefix.
- [x] Moved operator, readiness, activation, admission, lease, worker-claim,
  health, and initialization PostgreSQL fixtures to an empty clean-root
  upgrade (`pre_ga_v1_0001`); archived lineage is never executed by these
  gates.
- [x] Rebuilt the pre-squash PostgreSQL source fixture from the committed,
  self-digested catalog manifest (without importing or executing archived
  migrations), then restored the guarded rebaseline acceptance/rejection,
  lock, retained-data, and rollback matrix against that fixture.
- [x] Made the smoke runner and operator-control-plane evidence runner import
  and compare `CLEAN_ROOT_REVISION`, accept opaque Alembic IDs, and fail on any
  non-exact head; the operator evidence runner now runs in CI and is uploaded
  with PR-head build binding.
- [x] Removed the development deployment-class fallback from the production-like
  Compose base; local override and main-agent smoke files set `development`
  and `rehearsal` explicitly, respectively.
- [x] Consolidated build identity on `APP_BUILD_REVISION` and wired schema and
  operator evidence to the checked-out/PR head identity.
- [x] Added PostgreSQL shell integration coverage for empty, versioned,
  non-empty-unversioned, old-head, wrong-family, invalid-class, and connection
  failure migration states.
- [x] Added a concrete PostgreSQL exit-proof runner whose self-digested
  observations drive `verify_pre_ga_schema.py exit`; CI validates the proof
  binding, runs the complete matrix, and uploads both proof and sanitized
  evidence artifacts.
- [x] Migrated the seven current capability/durability/capture PostgreSQL
  suites to clean-root fixtures, archived only their historical migration
  variants as `.py.archived`, and included every live suite in the schema
  release-critical job.

The local Docker daemon was unavailable during this audit pass; PostgreSQL
integration remains enforced by the dedicated CI jobs with
`MINDATLAS_REQUIRE_POSTGRES=1` / `MINDATLAS_REQUIRE_SCHEMA_POSTGRES=1`.

## Exit-Gate Reconciliation (2026-08-10)

- [x] Frontend dependencies were installed from the lockfile; Vitest passed
  36 files / 201 tests, and `npm run build` passed.
- [x] The first CI run exposed a real operator evidence-runner bug: its
  SQLite rehearsal bypassed the shared PostgreSQL-to-SQLite metadata shim and
  failed on the live `JSONB` server default. The runner now calls
  `tests._db.create_sqlite_schema`, with a regression test covering restart,
  rotation, and revocation rehearsal.
- [x] CI run `31351421076` executed the PostgreSQL exit and sanitized-evidence
  gates successfully. This checkout still has no running Docker daemon, so the
  local replay remains unavailable without changing the environment.

## 复审补充完成记录 (2026-08-10)

本节是对上方原始 Task 1–12 编写清单的实际状态归档；原始清单中的
`Step 2` 红灯演示和独立 commit 命令是实施过程说明，不把“尚未在本机重放”
误报成 release gate 证据。所有 release-facing outcome 均由代码、定向测试或
CI job 证明：

| Task | 当前状态 | 证据/边界 |
| --- | --- | --- |
| 1–2 | 已完成 | family/exclusion/identity contracts 与 PostgreSQL canonical catalog tests |
| 3 | 已完成 | 四份 capture artifact、manifest digest、`pre_squash_fixture.py` byte-equivalent 校验 |
| 4–5 | 已完成 | live lineage 删除、deterministic clean root、archive/sole-head checks |
| 6–8 | 已完成 | marker/runtime identity、logical equivalence、60-revision archive CI gate |
| 9 | 已完成 | rehearsal/development old-head success、retained rows/checksums、production/shared/unknown/wrong-head/drift/non-empty/lock rejection、snapshot rollback；无拒绝场景变更源库 |
| 10 | 已完成 | API/readiness/admission/worker compatibility 与 drift claim rejection |
| 11 | 已完成 | clean-only deploy state machine、Compose identity、七个 live PostgreSQL suites、schema release job |
| 12 | 已完成 | `run_pre_ga_schema_exit_proof.py` + proof digest validation + PR-head-bound sanitized evidence |

未执行且仍明确不宣称执行的内容只有 deviation record 中列出的原 Plan 10
production canary、legacy-zero、restore、B1/B2 以及 calendar soak；它们不是
本次 clean-baseline release gate 的隐含前置条件。

## Rollback Boundary

- Before the first supported pre-GA deployment, revert code and recreate disposable databases from the clean root. Do not reactivate the old archive.
- A failed fresh migration is recovered by dropping/recreating the disposable database or restoring a backup already identified as the same clean family/revision.
- The root never downgrades to Legacy. Test-only downgrade destroys an empty schema and is not a deployment procedure.
- A guarded rebaseline transaction either leaves the exact old database untouched or commits a fully verified clean family marker/stamp; PostgreSQL transactional DDL prevents a partial object removal.
- After a guarded rebaseline commit, rollback uses a pre-command database backup or a forward clean-family fix. It never stamps back to `b6e2d4f8a901`.
- Once Operator auth is deployed, rolling back to unprotected routes remains forbidden. This plan changes schema lineage, not the security rollback rule.
- A wrong family, marker, fingerprint, deployment class, or contract keeps API/Worker fail closed until a compatible binary/schema/backup is deployed.
- Plan 4 migration rollback may return only to `pre_ga_v1_0001` under its own pre-GA guards; it must never modify or reconnect the archived lineage.

## Implementation Stop Conditions

Stop and preserve sanitized diagnostics if any condition occurs:

- Plan 1/2 implementations or old head `b6e2d4f8a901` are absent;
- old revision count is not exactly 60 or the graph is not one linear chain;
- a second Alembic head/root exists;
- canonical introspection cannot cover an object kind present in the application schema;
- PostgreSQL `pg_get_*` output cannot be made stable under the pinned major version;
- an exclusion key is missing/additional/drifted, has live dependencies, or contains business/evidence data;
- ORM plus retained SQL registry differs from the committed version-2 logical application document;
- generated root is not byte-reproducible or imports application/archive code;
- Database A/B version-2 logical application documents differ after exact exclusions and stage-specific control validation;
- archive bytes, parents, order, deviation digest, or snapshot digest do not verify;
- ordinary Alembic can locate an archived revision;
- a non-empty unversioned database would be stamped automatically;
- guarded rebaseline cannot prove non-production identity, source/clean fingerprints, invariants, or retained-data equality;
- a production/shared/unknown database reaches a mutation statement;
- marker/control/runtime identity does not recompute exactly;
- API and Worker use different compatibility requirements;
- an incompatible Worker can register or claim, or admission writes residue;
- any release-critical PostgreSQL test skips;
- evidence/logs include URL, SQL definition, secret, password hash, token, Prompt, Entry/Artifact content, raw row, or unkeyed data checksum.

## Authoring Self-Review Record

- Spec coverage: Task 1 freezes the family/exclusion/deviation contracts; Task 2 implements complete PostgreSQL canonical introspection; Task 3 captures exact old definitions and retained SQL; Task 4 deletes the unpublished migration runtime; Task 5 generates a deterministic staged root; Task 6 adds family-bound identity and fresh migration/downgrade proof; Task 7 proves two-database equivalence; Task 8 archives exactly 60 revisions and activates the sole root; Task 9 implements guarded non-production rebaseline; Task 10 wires API/Worker compatibility; Task 11 makes clean migration release-critical; Task 12 executes clean-install/full verification and safe evidence.
- Canonical coverage check: version-1 evidence covers namespaces, extensions, enums/domains, sequences, views/materialized views, physical columns/defaults/identity/generated state, PK/FK/unique/check/exclusion constraints, FK actions, indexes/expressions/predicates/attribute numbers, complete functions, and complete trigger/linkage/enabled state; version 2 removes only physical numbering after exact control validation.
- Exclusion check: the source allowlist is 11 tables plus one function plus 15 exact triggers (27 top-level keys). Definitions and digests come from Database A; no guessed digest or prefix authorization appears. Unknown differences fail closed.
- Fingerprint check: application structure, marker control structure, manifest digests, and runtime identity are separate non-circular values with one producer and explicit consumers. Marker rows/data never contaminate structural equivalence.
- Migration check: the archived graph ends `3bd7bc4257c9 -> 9f3c1a7e2b40 -> b6e2d4f8a901`; the live graph starts independently at `pre_ga_v1_0001` with `down_revision=None`; `pre_ga_v1_0002` remains reserved for Plan 4.
- Archive check: exact raw bytes, revision/parent/order/path hashes, old final head, deviation digest, and pre-squash snapshot digest are committed; archive suffix is non-importable and outside explicit `version_locations`.
- Rebaseline check: mutation occurs only after dual deployment identity, literal acknowledgement, advisory/table locks, exact old head/fingerprint, exclusion definitions, data invariants, and in-memory keyed retained-data snapshot. Clean fingerprint precedes marker/stamp, and postconditions precede commit. No force/skip path exists.
- Runtime compatibility check: the existing `RuntimeSchemaCompatibility.is_compatible(db)` signature remains stable. Family, known revision ordinal, structural/control fingerprints, seed/runtime/codec/feature/auth contracts, deployment class, runtime identity, and build identity are checked by API and Worker from one requirement.
- Security/evidence check: deployment cannot auto-stamp; production rebaseline is forbidden; public errors remain `schema_incompatible`; evidence schemas forbid URLs, secrets, content, SQL, rows, and data digests.
- Deviation check: the required six accepted facts are recorded without marking old Plan 10 canary, legacy-zero, restore, B1/B2, or calendar soak as executed.
- Execution granularity check: every Task has exact paths, Consumes/Produces, red/green commands, expected output, stop behavior, focused tests, and an independent commit.
- Placeholder scan:

```bash
rg -n -i '\b(T[B]D|T[O]DO|F[I]XME|implement[[:space:]]+later|fill[[:space:]]+in|similar[[:space:]]+to[[:space:]]+Task[[:space:]]+[0-9]+)\b' \
  docs/superpowers/plans/2026-07-28-pre-ga-clean-schema-baseline.md
```

Expected: no output.

- Revision/type/signature scan:

```bash
rg -n 'b6e2d4f8a901|pre_ga_v1_0001|pre_ga_v1_0002|RuntimeSchemaCompatibility|DeploymentClass|SchemaRuntimeIdentityMaterial' \
  docs/superpowers/plans/2026-07-28-pre-ga-clean-schema-baseline.md
```

Expected: old head is archival/source-only; clean root/Plan 4 parent are consistent; compatibility protocol and identity types have one canonical spelling.

- Formatting check:

```bash
git diff --check -- \
  docs/superpowers/plans/2026-07-28-pre-ga-clean-schema-baseline.md
```

Expected: no output.

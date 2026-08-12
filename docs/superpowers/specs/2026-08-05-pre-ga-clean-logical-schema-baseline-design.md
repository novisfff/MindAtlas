# Pre-GA Clean Logical Schema Baseline Design

**Status:** Approved direction A; written design pending user review

**Date:** 2026-08-05

**Supersedes:** The physical-catalog equality assumption in Task 5 and downstream
equivalence gates of
`docs/superpowers/plans/2026-07-28-pre-ga-clean-schema-baseline.md`

## Context

The pre-GA baseline implementation captured the PostgreSQL 15 catalog at old
head `b6e2d4f8a901` and then required a new root generated from live SQLAlchemy
metadata to reproduce that catalog document exactly. Task 5 proved that this
requirement combines two different contracts:

1. historical physical evidence, including PostgreSQL attribute numbers left
   behind by dropped columns and the Alembic control table; and
2. the clean application schema that a new root must install directly.

Those contracts cannot be byte-equal under the existing rules. For example,
the old `ai_provider` table has active column ordinals `1..6,8,9` because an
old migration dropped ordinal 7. A clean `CREATE TABLE` from final metadata
produces continuous attribute numbers. Reproducing the gap would require a
transitional create/drop sequence, which the clean-root contract forbids.

Task 5 also found genuine ORM drift unrelated to physical history: PostgreSQL
types, server defaults, check semantics, constraint names, and index identities
do not always match the captured database. These differences remain
release-blocking and must not be hidden by the logical projection introduced
below.

## Decision

Adopt two explicit schema representations with different responsibilities:

- **Raw physical catalog document, canonicalization version 1.** This remains
  exact historical evidence. It preserves `pg_attribute.attnum`, raw index
  attribute numbers, `alembic_version`, object definitions, and PostgreSQL 15
  catalog identity. Existing source snapshots and their digests remain
  immutable evidence of old head `b6e2d4f8a901`.
- **Logical application document, canonicalization version 2.** This is the
  structural compatibility contract for the generated clean root and runtime.
  It is produced by a separate, deterministic, fail-closed projection from a
  validated raw document. It excludes verified schema-control objects and
  removes only physical numbering fields that encode dropped-column history,
  while retaining every semantic field needed to detect real schema drift.

The generic exact comparator remains exact. It receives already-produced
logical documents and performs byte equality; it does not ignore mismatches or
apply ad hoc normalization.

## Goals

- Preserve the old head's exact physical catalog as immutable evidence.
- Generate a clean root from final live metadata without transitional `DROP`
  statements or historical placeholder columns.
- Prove old and clean databases have the same logical application schema after
  only exact Legacy exclusions and separately validated control extraction.
- Keep real table, column, type, default, constraint, index, function, trigger,
  enum, domain, sequence, and view differences release-blocking.
- Give generator, equivalence, expected-manifest, evidence, and runtime checks
  one versioned logical application fingerprint.
- Keep all projection behavior explicit, tested, deterministic, and bounded;
  there is no force, skip, accept-current, or discrepancy-derived DDL path.

## Non-Goals

- Reproducing dropped-column `attnum` gaps in the clean root.
- Treating `alembic_version` or the MindAtlas identity marker as application
  tables.
- Replacing retained SQL-object definitions with ORM-generated approximations.
- Accepting current ORM metadata merely because it is current code.
- Removing constraint or index names from structural identity.
- Normalizing SQL expressions beyond the already committed PostgreSQL 15 SQL
  normalization rules.
- Generating table DDL from a diff against the old snapshot.

## Contract Layers

### 1. Raw physical source evidence

The existing version-1 catalog reader and pre-squash snapshot continue to
represent exactly what PostgreSQL reported at `b6e2d4f8a901`.

The version-1 document retains:

- physical column `ordinal` from `pg_attribute.attnum`;
- index `keyAttributeNumbers` and `includeAttributeNumbers` from `pg_index`;
- all schema-control and application objects visible in the configured
  namespace;
- full normalized catalog definitions and their exact digests.

The existing files remain byte-stable historical inputs:

- `backend/app/schema/manifests/pre_ga_v1-pre-squash-schema.json`
- `backend/app/schema/manifests/pre_ga_v1-exclusions.json`
- `backend/app/schema/manifests/pre_ga_v1-sql-objects.json`

Their current fingerprints continue to identify raw and exact-exclusion
evidence. They are not reused as the clean runtime application fingerprint.

### 2. Exact Legacy exclusion

Legacy removal remains definition-locked. On the old side, all 27 committed
top-level exclusions must exist with the committed definition digests before
they are removed. On the clean side, all 27 must be absent.

The logical projection cannot authorize a new exclusion, a prefix, a renamed
object, a nested table field, or a drifted definition. Legacy exclusion happens
before application projection and remains independently auditable.

### 3. Schema-control extraction

Schema-control objects are not application objects, but they are never silently
ignored.

The pre-squash control contract contains the exact `public.alembic_version`
table definition and digest. The clean migrated database additionally has the
MindAtlas schema-identity table, guard function, and guard trigger introduced
by Task 6. Each stage validates the controls expected at that stage before
extracting them from the application document:

- **Old migrated database:** exact old `alembic_version` control contract.
- **Task 5 model-reference transaction:** no control objects are expected,
  because it uses `Base.metadata.create_all()` directly.
- **Fresh Alembic root database:** exact `alembic_version` plus the committed
  Task 6 identity-control contract.

Missing, additional, drifted, or stage-inappropriate control objects fail with
bounded diagnostic codes. Control definitions and control fingerprints remain
separate from the application structural fingerprint.

### 4. Logical application projection

The version-2 logical document is produced only after raw document validation,
Legacy validation, and stage-appropriate control validation.

For every table, projection removes these physical-history fields:

- `columns[*].ordinal`;
- `indexes[*].keyAttributeNumbers`;
- `indexes[*].includeAttributeNumbers`.

The projected arrays remain deterministically ordered by stable logical
identity:

- columns by column name;
- constraints by `(type, name, definition)`;
- indexes by index name and definition;
- top-level objects by `CanonicalObjectKey`.

Removing numeric index attribute arrays does not remove index semantics. The
full normalized index definition, expression, predicate, uniqueness, primary
and exclusion state, access method, parent table, validity, readiness, and
nulls-not-distinct state remain in the document. The normalized definition
preserves key order and included columns.

All other table fields remain exact, including:

- column name, formatted type, nullability, default expression, identity kind,
  generated kind, and non-default collation;
- constraint name, type, definition, validation, deferrability, initial mode,
  and foreign-key actions/match type;
- relation kind, persistence, partition strategy, and partition bound;
- every retained index semantic field listed above.

Non-table objects retain their complete version-1 definitions. Functions and
triggers continue to come from the committed retained SQL registry for the
model-reference and generated root.

The projected document declares `canonicalizationVersion: 2`. Version 1 and
version 2 fingerprints are never compared or substituted for each other.

## ORM Contract Authority

After physical fields and controls are separated, every remaining difference
is a real contract review item.

The default authority rule is:

1. The captured `b6e2d4f8a901` PostgreSQL semantic definition is authoritative
   when it implements the reviewed Plans 1 and 2 behavior.
2. Live ORM metadata must be corrected to express that semantic definition,
   including PostgreSQL types, server defaults, named constraints, and named
   indexes.
3. SQLite-only compatibility stays in test support and does not weaken the
   PostgreSQL metadata contract.
4. If repository evidence proves the current ORM intentionally supersedes the
   captured definition, execution stops for an explicit design amendment. The
   generator never chooses a side automatically and never rewrites the expected
   contract from the discrepancy.

Initial confirmed ORM review items include:

- `ai_model_capability_probe`: `created_at` server default, SHA-256 hex checks,
  JSONB-object check, explicit foreign-key and index identities;
- `assistant_chat_run`: server defaults for `status`, `last_event_seq`, and
  `checkpoint_seq`;
- Main-Agent rollout tables: JSONB rather than JSON where captured, server
  defaults, explicitly named unique constraints, and exact digest checks;
- model, skill, evaluation, profile, workflow, attachment, relation, tag, and
  Operator tables whose constraint or index identities currently rely on
  `unique=True`, `index=True`, or unnamed `ForeignKey` defaults;
- mixin-based model order differences only insofar as they expose another
  semantic mismatch; column declaration order itself is not part of version-2
  identity.

Each coherent ORM correction uses its own focused failing assertion inside the
Task 5 model-reference equivalence suite, followed by the minimal model change
and a focused PostgreSQL pass. The final version-2 document comparison remains
the aggregate release-critical gate.

## Artifacts and Ownership

Add one new committed manifest:

`backend/app/schema/manifests/pre_ga_v1-clean-application-contract.json`

It contains only deterministic, non-secret structural fields:

| Field | Required value |
|---|---|
| `schemaVersion` | integer `1` |
| `schemaFamily` | `pre_ga_v1` |
| `sourceHead` | `b6e2d4f8a901` |
| `sourceSnapshotDigest` | `3ee2120ded35e7e550f947f726bc38a5eb5f6d3c88bf8b78e191a27b1e634346` |
| `exclusionManifestDigest` | `f27f89bcfe248aa1e29fce60d1d19a51bafee857d1db7b3dff01c9ecfa7321f4` |
| `controlContractDigest` | digest of a canonical payload containing the exact `alembic_version` key and its definition digest `c215428519337adf9885ec83e0da716e1e2ea82f058b4b162a89941e22965149` |
| `canonicalizationVersion` | integer `2` |
| `logicalApplicationDocument` | complete version-2 canonical document |
| `logicalApplicationFingerprint` | SHA-256 of `logicalApplicationDocument` |
| `manifestDigest` | SHA-256 of the complete manifest payload excluding only `manifestDigest` |

The checked-in manifest is generated only from a freshly captured and validated
old-head PostgreSQL 15 database. The loader revalidates all self-digests and
cross-references before returning the document.

Task 6's expected-contract manifest consumes the version-2 logical application
fingerprint. It continues to carry a separate schema-identity control
fingerprint. Runtime compatibility uses the same separation.

## Data Flow

### Old-head capture

1. Verify the database has exactly old head `b6e2d4f8a901`.
2. Read one version-1 raw catalog document in the existing read-only,
   repeatable-read transaction.
3. Validate and remove the exact 27 Legacy exclusion objects.
4. Validate and extract the exact pre-squash schema-control contract.
5. Project the remaining application objects to version 2.
6. Write or check the deterministic clean-application contract manifest.

### Task 5 model reference

1. Verify the disposable PostgreSQL 15 database is empty.
2. Load all live models through the central registry.
3. Run `Base.metadata.create_all()` and install the retained SQL registry in one
   transaction.
4. Read the version-1 raw catalog document.
5. Require all Legacy and schema-control objects to be absent.
6. Project to version 2 and compare exactly with the committed logical
   application document.
7. Roll back and prove the database is empty before autogeneration.

### Fresh clean root

1. Upgrade an empty database through the staged root.
2. Validate the Alembic and Task 6 identity-control contracts separately.
3. Require all Legacy exclusions to be absent.
4. Project the application catalog to version 2.
5. Compare exactly with the same committed logical application document.

## Failure Model

All new public control-flow failures use bounded safe codes. Representative
codes are:

- `schema_control_contract_missing`
- `schema_control_contract_additional`
- `schema_control_contract_drift`
- `schema_control_stage_invalid`
- `logical_schema_projection_invalid`
- `logical_schema_manifest_invalid`
- `logical_schema_manifest_digest_mismatch`
- `logical_schema_cross_reference_mismatch`
- `logical_application_schema_difference`

Exceptions never contain database URLs, raw rows, SQL bodies, object
definitions, passwords, tokens, Prompt/Entry/Artifact content, or data
checksums. Tests may report object keys and stable field paths, but not full SQL
definitions.

## Testing Strategy

### Projection unit tests

- Two synthetic raw tables that differ only by dropped-column ordinal gaps
  project to byte-identical version-2 objects.
- Two indexes that differ only by raw attribute numbers but have identical full
  definitions project identically.
- Changes to column type/default/nullability, constraint name/definition,
  index name/definition/predicate/uniqueness, or any retained non-table object
  continue to fail exact comparison.
- Version 1 and version 2 documents cannot be accidentally compared as equal.

### Control-contract tests

- Exact old `alembic_version` validates and is extracted.
- Missing, additional, or definition-drifted control objects fail closed.
- Model-reference stage rejects any control object.
- Fresh-root stage requires the exact Alembic and identity-control objects.

### Artifact tests

- The new logical manifest validates all self-digests and cross-references.
- Capture `--check` is byte-identical against the source database.
- No URL, secret, SQL body, or row content can enter the manifest.
- Existing raw snapshot and retained SQL registry bytes remain unchanged.

### ORM reconciliation tests

- Every semantic mismatch gets a focused RED before its model correction.
- The aggregate PostgreSQL model-reference equality test executes without skip.
- Alembic autogenerate against the empty generator database produces the exact
  reviewed metadata operations and retained SQL appendage.
- Full backend regression remains green after each coherent correction batch.

### End-to-end gates

- Old and clean version-1 physical documents are expected to differ where the
  old schema has dropped-column history and controls.
- Old and clean version-2 logical application documents must be byte-equal.
- Controls must independently match their committed stage-specific contracts.
- Legacy exclusions must independently match old definitions and be absent from
  the clean database.
- Runtime application fingerprint uses version 2; raw version-1 fingerprints
  remain evidence-only.

## Migration and Compatibility Consequences

- The clean root contains no placeholder columns and no transitional `DROP`.
- The clean root remains self-contained and generated from reviewed live
  metadata plus the retained SQL registry.
- Task 7 compares version-2 application documents, not raw physical documents.
- Task 8 archives the old lineage without changing raw evidence.
- Task 9 rebaseline validates both the exact old raw fingerprint and the target
  logical application fingerprint before mutation or stamp.
- Task 10 runtime compatibility recomputes the version-2 logical application
  fingerprint and the separate control fingerprint.
- Task 12 evidence reports both immutable old raw evidence digests and the
  clean logical application fingerprint, without claiming physical equality.

## Rollback and Audit Boundary

The existing four implementation commits remain independently reviewable. The
new design and subsequent plan amendment are additive commits; historical
snapshot bytes are not rewritten.

If logical projection cannot preserve a semantic field, if an ORM mismatch has
no unambiguous authority, or if clean and old logical documents still differ
after reviewed ORM corrections, execution stops with sanitized object keys and
field paths. The implementation must not add another ignored field to make a
test pass without a new design review.

## Alternatives Rejected

### Recreate historical attribute-number gaps

This preserves raw equality but requires placeholder columns and transitional
`DROP COLUMN` operations. It carries unpublished migration debris into the new
root and contradicts the clean-root goal.

### Use the old snapshot as generated table DDL

This bypasses live ORM authority, duplicates table definitions, and violates
the rule against sourcing DDL from a discrepancy.

### Make the comparator ignore current failures

This would mix projection policy with equality, hide genuine type/default/name
drift, and create an unbounded normalization surface. The comparator therefore
remains byte-exact.

## Acceptance Criteria

- Existing raw snapshot, exclusion manifest, and retained SQL registry remain
  byte-identical.
- A committed version-2 clean-application contract is reproducibly generated
  from old head `b6e2d4f8a901` on PostgreSQL 15.
- Physical ordinal gaps and raw index attribute numbers do not enter the
  version-2 fingerprint.
- No other semantic field is removed from the version-2 document.
- `alembic_version` and Task 6 identity controls are validated separately and
  never enter the application fingerprint.
- All real ORM semantic drift is reviewed and resolved with focused TDD.
- Task 5 model-reference PostgreSQL equality passes without skip.
- Generated root has no Legacy name, old revision ID, placeholder column, or
  transitional `DROP` in `upgrade()`.
- Old-chain and clean-root version-2 logical documents compare byte-for-byte.
- Runtime and evidence consume the same committed version-2 application
  fingerprint and separate control contract.

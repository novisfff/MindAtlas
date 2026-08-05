# Pre-GA Clean Logical Application Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a versioned, fail-closed logical application schema contract that preserves the exact pre-squash physical snapshot as evidence while allowing a clean metadata-generated root to omit historical dropped-column slots and schema-control objects.

**Architecture:** PostgreSQL capture remains canonicalization version 1 and byte-immutable. A new `app.schema.application_contract` unit validates stage-specific controls, removes only their exact definitions, projects tables to version 2 by deleting only physical attribute-number fields, and strictly loads a self-digested logical manifest. Capture writes a fourth artifact from the same read-only transaction; exact comparison remains unchanged and receives projected documents.

**Tech Stack:** Python 3.11, PostgreSQL 15, SQLAlchemy 2, frozen dataclasses, canonical JSON, SHA-256, pytest.

## Global Constraints

- Implement `docs/superpowers/specs/2026-08-05-pre-ga-clean-logical-schema-baseline-design.md`.
- Work only in the existing isolated worktree and branch.
- Do not change the bytes of the existing exclusion, pre-squash, or SQL-object manifests.
- Version 1 is raw physical evidence; version 2 is the logical application contract.
- Projection removes exactly `columns[*].ordinal`, `indexes[*].keyAttributeNumbers`, and `indexes[*].includeAttributeNumbers`.
- Every type, default, nullability, name, definition, expression, predicate, constraint, index, function, trigger, view, sequence, enum, domain, extension, and namespace remains exact.
- `public.alembic_version` must have definition digest `c215428519337adf9885ec83e0da716e1e2ea82f058b4b162a89941e22965149` before extraction.
- Exact Legacy validation precedes projection; there is no force, skip, accept-current, prefix, or nested-field exclusion.
- Public failures use bounded safe codes and contain no URL, SQL, definition, raw row, secret, or application content.
- Strict TDD applies; each task ends in one independent commit.
- Do not use implementation subagents; only a final read-only audit may use one.

---

### Task 1: Project Raw Catalogs into Logical Application Documents

**Files:**

- Create: `backend/app/schema/application_contract.py`
- Create: `backend/tests/test_schema_application_contract.py`
- Modify: `backend/app/schema/contracts.py`

**Interfaces:**

- Consumes: `CanonicalSchemaDocument` and exact version-1 catalog definitions.
- Produces: `SchemaControlStage`, `LogicalApplicationContractError`, `PRE_SQUASH_CONTROL_CONTRACT_DIGEST`, and `project_logical_application_document()`.

- [ ] **Step 1: Write failing projection and version tests**

Create exact catalog-shaped table helpers and add:

```python
def test_projection_removes_only_physical_attribute_numbers() -> None:
    old = _document(_table(column_ordinals=(1, 3), index_numbers=(3,)))
    clean = _document(_table(column_ordinals=(1, 2), index_numbers=(2,)))
    old_logical = project_logical_application_document(
        old, control_stage=SchemaControlStage.MODEL_REFERENCE
    )
    clean_logical = project_logical_application_document(
        clean, control_stage=SchemaControlStage.MODEL_REFERENCE
    )
    assert old_logical.canonicalization_version == 2
    assert old_logical.to_payload() == clean_logical.to_payload()
    table = old_logical.objects[0].definition
    assert all("ordinal" not in column for column in table["columns"])
    assert all("keyAttributeNumbers" not in index for index in table["indexes"])
    assert all("includeAttributeNumbers" not in index for index in table["indexes"])


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("columns", 0, "defaultExpression"), "1"),
        (("constraints", 0, "name"), "ck_changed"),
        (("indexes", 0, "name"), "idx_changed"),
        (("indexes", 0, "predicate"), "id IS NOT NULL"),
    ],
)
def test_projection_preserves_semantic_differences(path, value) -> None:
    left = _document(_table())
    right = _mutated_document(left, path, value)
    left = project_logical_application_document(
        left, control_stage=SchemaControlStage.MODEL_REFERENCE
    )
    right = project_logical_application_document(
        right, control_stage=SchemaControlStage.MODEL_REFERENCE
    )
    with pytest.raises(SchemaComparisonError) as exc:
        compare_documents(left, right, exclusions=None)
    assert exc.value.safe_code == "unmanifested_schema_difference"


def test_document_versions_are_closed() -> None:
    assert CanonicalSchemaDocument(1, 15, ()).canonicalization_version == 1
    assert CanonicalSchemaDocument(2, 15, ()).canonicalization_version == 2
    with pytest.raises(ValueError, match="unsupported canonicalization version"):
        CanonicalSchemaDocument(3, 15, ())
```

- [ ] **Step 2: Run tests and verify RED**

```bash
cd backend
.venv/bin/python -m pytest tests/test_schema_application_contract.py -q
```

Expected: collection fails because the module is absent and version 2 is unsupported.

- [ ] **Step 3: Allow only versions 1 and 2**

Change `CanonicalSchemaDocument` to use `Literal[1, 2]` and validate:

```python
if self.canonicalization_version not in (1, 2):
    raise ValueError("unsupported canonicalization version")
```

Keep `PostgresCatalogReader.read_document()` fixed at version 1.

- [ ] **Step 4: Implement exact control constants**

Create `application_contract.py` with:

```python
class SchemaControlStage(StrEnum):
    PRE_SQUASH_MIGRATED = "pre_squash_migrated"
    MODEL_REFERENCE = "model_reference"


class LogicalApplicationContractError(RuntimeError):
    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


ALEMBIC_VERSION_KEY = CanonicalObjectKey("table", "public", "alembic_version")
ALEMBIC_VERSION_DEFINITION_DIGEST = (
    "c215428519337adf9885ec83e0da716e1e2ea82f058b4b162a89941e22965149"
)
PRE_SQUASH_CONTROL_CONTRACT_PAYLOAD = {
    "schemaVersion": 1,
    "objects": [{
        "key": ALEMBIC_VERSION_KEY.to_payload(),
        "definitionDigest": ALEMBIC_VERSION_DEFINITION_DIGEST,
    }],
}
PRE_SQUASH_CONTROL_CONTRACT_DIGEST = sha256_canonical_json(
    PRE_SQUASH_CONTROL_CONTRACT_PAYLOAD
)
```

- [ ] **Step 5: Implement strict projection**

```python
def project_logical_application_document(
    document: CanonicalSchemaDocument,
    *,
    control_stage: SchemaControlStage,
) -> CanonicalSchemaDocument:
    if document.canonicalization_version != 1:
        raise LogicalApplicationContractError("logical_schema_projection_invalid")
    by_key = {item.key: item for item in document.objects}
    if control_stage is SchemaControlStage.PRE_SQUASH_MIGRATED:
        control = by_key.pop(ALEMBIC_VERSION_KEY, None)
        if control is None:
            raise LogicalApplicationContractError("schema_control_contract_missing")
        if control.definition_digest != ALEMBIC_VERSION_DEFINITION_DIGEST:
            raise LogicalApplicationContractError("schema_control_contract_drift")
    elif control_stage is SchemaControlStage.MODEL_REFERENCE:
        if ALEMBIC_VERSION_KEY in by_key:
            raise LogicalApplicationContractError("schema_control_stage_invalid")
    else:
        raise LogicalApplicationContractError("schema_control_stage_invalid")

    _reject_reserved_identity_controls(by_key)
    projected = tuple(
        CanonicalSchemaObject(
            key=item.key,
            definition=(
                _project_table_definition(item.definition)
                if item.key.kind == "table"
                else item.definition
            ),
        )
        for item in sorted(by_key.values(), key=lambda value: value.key)
    )
    return CanonicalSchemaDocument(2, document.postgres_major, projected)
```

Implement `_reject_reserved_identity_controls()` with the three exact Task 6 keys. Implement `_project_table_definition()` with explicit exact-key assertions for the catalog table, column, constraint, and index payloads; copy all values except the three approved physical fields; sort columns by name, constraints by `(type, name, definition)`, and indexes by `(name, definition)`.

Malformed shapes raise `logical_schema_projection_invalid`. Control failures use `schema_control_contract_missing`, `schema_control_contract_drift`, or `schema_control_stage_invalid`.

- [ ] **Step 6: Add control and malformed-shape tests**

Load the committed snapshot and assert exact pre-squash extraction. Mutate the Alembic definition and assert drift. Cover missing control, control in model-reference stage, reserved identity table/function/trigger, version-2 input, and every additional/missing nested field. Exceptions must contain only the safe code.

- [ ] **Step 7: Run focused regression gates**

```bash
.venv/bin/python -m pytest -q \
  tests/test_schema_application_contract.py \
  tests/test_schema_canonical.py \
  tests/test_schema_catalog_postgres.py
```

Expected: all pass; raw catalog tests retain version 1 physical fields.

- [ ] **Step 8: Commit Task 1**

```bash
git add \
  backend/app/schema/application_contract.py \
  backend/app/schema/contracts.py \
  backend/tests/test_schema_application_contract.py
git commit -m "feat(schema): project clean logical contracts"
```

---

### Task 2: Freeze the Version-2 Logical Application Manifest

**Files:**

- Create: `backend/app/schema/manifests/pre_ga_v1-clean-application-contract.json`
- Modify: `backend/app/schema/application_contract.py`
- Modify: `backend/scripts/capture_pre_ga_schema.py`
- Modify: `backend/tests/test_schema_application_contract.py`
- Modify: `backend/tests/test_schema_capture_postgres.py`
- Modify: `backend/tests/test_schema_exclusion_manifest.py`

**Interfaces:**

- Consumes: the immutable three manifests, version-2 projection, and the old-head PostgreSQL 15 capture transaction.
- Produces: `LogicalApplicationContract`, `DEFAULT_LOGICAL_APPLICATION_CONTRACT_PATH`, `load_logical_application_contract()`, and a fourth atomic artifact.

- [ ] **Step 1: Write failing strict-loader tests**

Add:

```python
def test_committed_logical_contract_is_self_validating() -> None:
    contract = load_logical_application_contract()
    assert contract.schema_family == "pre_ga_v1"
    assert contract.source_head == "b6e2d4f8a901"
    assert contract.source_snapshot_digest == (
        "3ee2120ded35e7e550f947f726bc38a5eb5f6d3c88bf8b78e191a27b1e634346"
    )
    assert contract.exclusion_manifest_digest == (
        "f27f89bcfe248aa1e29fce60d1d19a51bafee857d1db7b3dff01c9ecfa7321f4"
    )
    assert contract.control_contract_digest == PRE_SQUASH_CONTROL_CONTRACT_DIGEST
    assert contract.logical_application_document.canonicalization_version == 2
    assert contract.logical_application_fingerprint == structural_fingerprint(
        contract.logical_application_document
    )
```

Parametrize duplicate-member, boolean-version, extra-field, cross-reference-digest, logical-document, logical-fingerprint, and self-digest mutations. Require only `logical_schema_manifest_invalid`, `logical_schema_manifest_digest_mismatch`, or `logical_schema_cross_reference_mismatch`.

- [ ] **Step 2: Write the failing four-manifest capture test**

Rename the PostgreSQL test to `test_capture_writes_four_sanitized_manifests_and_checks_byte_identity`. Require:

```python
assert paths == [
    "pre_ga_v1-clean-application-contract.json",
    "pre_ga_v1-exclusions.json",
    "pre_ga_v1-pre-squash-schema.json",
    "pre_ga_v1-sql-objects.json",
]
assert logical["canonicalizationVersion"] == 2
assert logical["sourceSnapshotDigest"] == snapshot["snapshotDigest"]
assert logical["exclusionManifestDigest"] == exclusions["manifestDigest"]
assert logical["controlContractDigest"] == PRE_SQUASH_CONTROL_CONTRACT_DIGEST
assert len(logical["logicalApplicationDocument"]["objects"]) == 179
assert logical["logicalApplicationFingerprint"] == sha256_canonical_json(
    logical["logicalApplicationDocument"]
)
```

Also retain URL/secret/path/DDL sanitization and byte-identical `--check` assertions.

- [ ] **Step 3: Run tests and verify RED**

```bash
.venv/bin/python -m pytest -q \
  tests/test_schema_application_contract.py \
  tests/test_schema_capture_postgres.py::test_capture_writes_four_sanitized_manifests_and_checks_byte_identity
```

Expected: the loader/manifest are absent and capture still writes three files.

- [ ] **Step 4: Implement the strict loader**

Add:

```python
@dataclass(frozen=True)
class LogicalApplicationContract:
    schema_family: str
    source_head: str
    source_snapshot_digest: str
    exclusion_manifest_digest: str
    control_contract_digest: str
    logical_application_document: CanonicalSchemaDocument
    logical_application_fingerprint: str
    manifest_digest: str
```

`load_logical_application_contract()` rejects duplicate JSON members, requires exact keys and integer versions, validates lowercase SHA-256 fields, parses only version 2, verifies logical fingerprint/self-digest, and cross-checks the committed snapshot/exclusion/control digests.

- [ ] **Step 5: Extend capture without changing old bytes**

Append the new filename to `_FILENAMES`. After producing the old payloads unchanged, project `normalized_document` at `PRE_SQUASH_MIGRATED` and build:

```python
logical_payload = {
    "schemaVersion": 1,
    "schemaFamily": SCHEMA_FAMILY,
    "sourceHead": PRE_SQUASH_HEAD,
    "sourceSnapshotDigest": snapshot["snapshotDigest"],
    "exclusionManifestDigest": exclusion_manifest["manifestDigest"],
    "controlContractDigest": PRE_SQUASH_CONTROL_CONTRACT_DIGEST,
    "canonicalizationVersion": 2,
    "logicalApplicationDocument": logical_document.to_payload(),
    "logicalApplicationFingerprint": structural_fingerprint(logical_document),
}
logical_manifest = {
    **logical_payload,
    "manifestDigest": sha256_canonical_json(logical_payload),
}
```

Validate all four temporary outputs before rename. Add one atomic extension case: exactly the three verified old files may coexist with an absent fourth; verify their bytes against fresh output and install only the fourth. Every other partial set fails.

- [ ] **Step 6: Generate the committed manifest from the source database**

```bash
sha256sum app/schema/manifests/pre_ga_v1-{exclusions,pre-squash-schema,sql-objects}.json
MINDATLAS_SCHEMA_CAPTURE_DATABASE_URL="$MINDATLAS_SCHEMA_CAPTURE_DATABASE_URL" \
  .venv/bin/python scripts/capture_pre_ga_schema.py \
    --database-url-env MINDATLAS_SCHEMA_CAPTURE_DATABASE_URL --write
sha256sum app/schema/manifests/pre_ga_v1-{exclusions,pre-squash-schema,sql-objects}.json
```

Expected: four manifests, 179 logical objects, and unchanged old hashes.

- [ ] **Step 7: Run byte and PostgreSQL gates**

```bash
MINDATLAS_SCHEMA_CAPTURE_DATABASE_URL="$MINDATLAS_SCHEMA_CAPTURE_DATABASE_URL" \
  .venv/bin/python scripts/capture_pre_ga_schema.py \
    --database-url-env MINDATLAS_SCHEMA_CAPTURE_DATABASE_URL --check
.venv/bin/python -m pytest -q \
  tests/test_schema_application_contract.py \
  tests/test_schema_exclusion_manifest.py
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
MINDATLAS_TEST_POSTGRES_DESTRUCTIVE=1 \
  .venv/bin/python -m pytest -q tests/test_schema_capture_postgres.py
git diff --check
```

Expected: all pass without a release-critical PostgreSQL skip; old manifest bytes remain unchanged.

- [ ] **Step 8: Commit Task 2**

```bash
git add \
  backend/app/schema/application_contract.py \
  backend/app/schema/manifests/pre_ga_v1-clean-application-contract.json \
  backend/scripts/capture_pre_ga_schema.py \
  backend/tests/test_schema_application_contract.py \
  backend/tests/test_schema_capture_postgres.py \
  backend/tests/test_schema_exclusion_manifest.py
git commit -m "test(schema): freeze clean logical contract"
```

---

## Exit Gate

```bash
cd backend
.venv/bin/python -m pytest -q \
  tests/test_schema_canonical.py \
  tests/test_schema_catalog_postgres.py \
  tests/test_schema_application_contract.py \
  tests/test_schema_exclusion_manifest.py
MINDATLAS_SCHEMA_CAPTURE_DATABASE_URL="$MINDATLAS_SCHEMA_CAPTURE_DATABASE_URL" \
  .venv/bin/python scripts/capture_pre_ga_schema.py \
    --database-url-env MINDATLAS_SCHEMA_CAPTURE_DATABASE_URL --check
MINDATLAS_TEST_POSTGRES_URL="$MINDATLAS_TEST_POSTGRES_URL" \
MINDATLAS_TEST_POSTGRES_DESTRUCTIVE=1 \
  .venv/bin/python -m pytest -q tests/test_schema_capture_postgres.py
git diff --check
```

Expected: raw version 1 remains exact; logical version 2 contains 179 objects; only the approved physical fields are removed; control extraction is exact; semantic mutations remain visible; capture checks four files atomically; all tests pass.

## Parent-Plan Handoff

Resume Task 5 of `2026-07-28-pre-ga-clean-schema-baseline.md` with:

- expected: `load_logical_application_contract().logical_application_document`;
- actual: `project_logical_application_document(raw_model_document, control_stage=SchemaControlStage.MODEL_REFERENCE)`;
- comparator: unchanged exact `compare_documents(expected, actual, exclusions=None)`;
- any remaining mismatch is semantic ORM/catalog drift and gets focused RED before correction;
- generator and Task 6/7/9/10/12 application fingerprints use version 2;
- raw version-1 fingerprints remain evidence and rebaseline source guards.

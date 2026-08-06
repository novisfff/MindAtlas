"""Guarded non-production pre-GA schema rebaseline contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from collections.abc import Iterable
import json
from pathlib import Path
import re
import secrets
from typing import Protocol
import uuid

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.schema.application_contract import (
    LogicalApplicationContractError,
    SchemaControlStage,
    project_logical_application_document,
)
from app.schema.canonical import (
    SchemaComparisonError,
    normalize_document,
    sha256_canonical_json,
    structural_fingerprint,
)
from app.schema.catalog import CatalogReadError, PostgresCatalogReader
from app.schema.contracts import (
    CLEAN_ROOT_REVISION,
    PRE_SQUASH_HEAD,
    DeploymentClass,
)
from app.schema.exclusions import LEGACY_TABLE_NAMES
from app.schema.identity import (
    SchemaIdentityError,
    insert_schema_identity,
    install_schema_identity_controls,
    load_expected_schema_contract,
    read_schema_identity,
    schema_runtime_identity_digest,
)
from app.schema.sql_objects import (
    SchemaExclusionManifest,
    SchemaManifestError,
    load_exclusion_manifest,
)


MAINTENANCE_ACKNOWLEDGEMENT = (
    "I_ACKNOWLEDGE_THIS_IS_A_RESETTABLE_NON_PRODUCTION_DATABASE"
)
REBASELINE_ADVISORY_LOCK_KEY = 0x4D41534348454D41
REBASELINE_LOCK_TIMEOUT = "5s"
REBASELINE_STATEMENT_TIMEOUT = "120s"
_LEGACY_CONTROL_TABLE = "assistant_runtime_rollout_control"
_SAFE_BUILD_REVISION = re.compile(r"[A-Za-z0-9._/@+\-]{1,128}\Z")
_SECRET_BUILD_REVISION = re.compile(
    r"(?:password|passwd|token|secret|api[_-]?key|bearer|credential|postgresql)"
    r"|(?:sk|pk|rk|ghp|github_pat|xox[baprs]|AKIA)[_-]",
    re.IGNORECASE,
)
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ARCHIVE_MANIFEST_PATH = (
    _BACKEND_ROOT
    / "alembic"
    / "archive"
    / "pre_ga_v1_superseded"
    / "manifest.v1.json"
)
SAFE_REPORT_FIELDS = frozenset(
    {
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
)


class RebaselineRefused(RuntimeError):
    """Bounded refusal safe for command output and automation."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


@dataclass(frozen=True)
class DataInvariant:
    name: str
    query: str


@dataclass(frozen=True)
class RetainedTableSnapshot:
    table_key: str
    row_count: int
    keyed_digest: bytes

    def __post_init__(self) -> None:
        if not self.table_key or type(self.row_count) is not int:
            raise ValueError("retained table snapshot identity is invalid")
        if self.row_count < 0 or len(self.keyed_digest) != 32:
            raise ValueError("retained table snapshot digest is invalid")


DATA_INVARIANTS = (
    DataInvariant(
        "main_agent_runs_only",
        "SELECT NOT EXISTS (SELECT 1 FROM \"public\".\"assistant_chat_run\" "
        "WHERE runtime_kind <> 'main_agent')",
    ),
    DataInvariant(
        "run_runtime_identity_complete",
        "SELECT NOT EXISTS (SELECT 1 FROM \"public\".\"assistant_chat_run\" WHERE "
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
        "FROM \"public\".\"assistant_conversation_skill_l2_memory\" WHERE "
        "skill_package_id IS NULL OR memory_namespace IS NULL "
        "OR length(trim(memory_namespace)) = 0)",
    ),
    DataInvariant(
        "operator_singleton",
        "SELECT count(*) <= 1 FROM \"public\".\"operator_account\"",
    ),
    DataInvariant(
        "new_rollout_control_singleton",
        "SELECT count(*) <= 1 FROM "
        "\"public\".\"assistant_main_agent_rollout_control\"",
    ),
    DataInvariant(
        "all_foreign_keys_validated",
        "SELECT NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint "
        "WHERE contype = 'f' AND NOT convalidated)",
    ),
    DataInvariant(
        "no_active_profile_v1_rollout",
        "SELECT NOT EXISTS ("
        "SELECT 1 FROM \"public\".\"assistant_main_agent_rollout_control\" AS control "
        "JOIN \"public\".\"assistant_main_agent_rollout_revision\" AS rollout "
        "ON rollout.id = control.active_rollout_revision_id "
        "JOIN \"public\".\"assistant_main_agent_profile_version\" AS profile_version "
        "ON profile_version.id = rollout.profile_version_id "
        "WHERE control.active_rollout_revision_id IS NOT NULL "
        "AND (profile_version.snapshot ->> 'schemaVersion') "
        "IS DISTINCT FROM '2')",
    ),
)


class _AcknowledgementArguments(Protocol):
    acknowledge_local_maintenance: str


@dataclass(frozen=True)
class RebaselineRequest:
    deployment_class: DeploymentClass
    acknowledgement: str
    build_revision: str
    operation_id: str | None = None

    def __post_init__(self) -> None:
        validate_build_revision(self.build_revision)
        if self.operation_id is not None and re.fullmatch(
            r"[0-9a-f]{32}", self.operation_id
        ) is None:
            raise ValueError("operation_id is unsafe")

    @property
    def acknowledge_local_maintenance(self) -> str:
        return self.acknowledgement


def validate_build_revision(build_revision: str) -> None:
    """Require a bounded, non-secret report/build identifier."""
    if (
        not isinstance(build_revision, str)
        or _SAFE_BUILD_REVISION.fullmatch(build_revision) is None
        or _SECRET_BUILD_REVISION.search(build_revision) is not None
    ):
        raise ValueError("build_revision is unsafe")


@dataclass(frozen=True)
class RebaselineReport:
    operation_id: str
    result: str
    deployment_class: DeploymentClass
    before_revision: str
    after_revision: str
    before_structural_fingerprint: str
    after_structural_fingerprint: str
    runtime_identity_digest: str
    exclusion_manifest_digest: str
    excluded_object_count: int
    removed_known_inert_seed_rows: int
    removed_legacy_business_rows: int
    retained_table_count: int
    retained_row_count: int
    retained_data_unchanged: bool
    archive_manifest_digest: str
    build_revision: str

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schemaVersion": 1,
            "operationId": self.operation_id,
            "result": self.result,
            "deploymentClass": self.deployment_class.value,
            "beforeRevision": self.before_revision,
            "afterRevision": self.after_revision,
            "beforeStructuralFingerprint": self.before_structural_fingerprint,
            "afterStructuralFingerprint": self.after_structural_fingerprint,
            "runtimeIdentityDigest": self.runtime_identity_digest,
            "exclusionManifestDigest": self.exclusion_manifest_digest,
            "excludedObjectCount": self.excluded_object_count,
            "removedKnownInertSeedRows": self.removed_known_inert_seed_rows,
            "removedLegacyBusinessRows": self.removed_legacy_business_rows,
            "retainedTableCount": self.retained_table_count,
            "retainedRowCount": self.retained_row_count,
            "retainedDataUnchanged": self.retained_data_unchanged,
            "archiveManifestDigest": self.archive_manifest_digest,
            "buildRevision": self.build_revision,
        }
        if set(payload) != SAFE_REPORT_FIELDS:
            raise RebaselineRefused("rebaseline_report_invalid")
        return payload


def validate_acknowledgement(args: _AcknowledgementArguments) -> None:
    """Require the exact explicit local-maintenance acknowledgement."""
    if args.acknowledge_local_maintenance != MAINTENANCE_ACKNOWLEDGEMENT:
        raise RebaselineRefused("maintenance_acknowledgement_missing")


def load_archive_manifest_digest() -> str:
    try:
        raw = json.loads(_ARCHIVE_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise RebaselineRefused("archive_manifest_invalid") from None
    if not isinstance(raw, dict):
        raise RebaselineRefused("archive_manifest_invalid")
    claimed = raw.get("manifestDigest")
    payload = {key: value for key, value in raw.items() if key != "manifestDigest"}
    if (
        not isinstance(claimed, str)
        or re.fullmatch(r"[0-9a-f]{64}", claimed) is None
        or sha256_canonical_json(payload) != claimed
    ):
        raise RebaselineRefused("archive_manifest_invalid")
    return claimed


def _read_database_state(connection) -> tuple[str | None, bool, bool]:  # noqa: ANN001
    try:
        row = connection.execute(
            text(
                "SELECT "
                "shobj_description(oid, 'pg_database') AS database_comment, "
                "pg_is_in_recovery() AS in_recovery, "
                "current_setting('transaction_read_only') = 'on' AS read_only "
                "FROM pg_database WHERE datname = current_database()"
            )
        ).one()
    except SQLAlchemyError:
        raise RebaselineRefused("database_identity_unavailable") from None
    return row.database_comment, bool(row.in_recovery), bool(row.read_only)


def _pin_public_search_path(connection) -> None:  # noqa: ANN001
    try:
        connection.execute(text("SET LOCAL search_path = public"))
    except SQLAlchemyError:
        raise RebaselineRefused("database_identity_unavailable") from None


def validate_deployment_identity(
    connection,  # noqa: ANN001
    request: RebaselineRequest,
) -> None:
    """Require matching process/database identities for a writable local DB."""
    validate_acknowledgement(request)
    if request.deployment_class is DeploymentClass.PRODUCTION:
        raise RebaselineRefused("production_rebaseline_forbidden")

    database_comment, in_recovery, read_only = _read_database_state(connection)
    if in_recovery:
        raise RebaselineRefused("database_in_recovery")
    if read_only:
        raise RebaselineRefused("database_read_only")
    if database_comment is None:
        raise RebaselineRefused("database_deployment_identity_missing")

    prefix = "mindatlas:deployment_class="
    if not database_comment.startswith(prefix):
        raise RebaselineRefused("database_deployment_identity_unknown")
    raw_database_class = database_comment.removeprefix(prefix)
    try:
        database_class = DeploymentClass(raw_database_class)
    except ValueError:
        raise RebaselineRefused(
            "database_deployment_identity_unknown"
        ) from None
    if database_class is DeploymentClass.PRODUCTION:
        raise RebaselineRefused("production_rebaseline_forbidden")
    if database_class is not request.deployment_class:
        raise RebaselineRefused("deployment_identity_mismatch")


def read_single_alembic_version(connection) -> str:  # noqa: ANN001
    """Read exactly one Alembic version or refuse without leaking DB details."""
    try:
        revisions = tuple(
            str(item)
            for item in connection.execute(
                text(
                    "SELECT version_num FROM \"public\".\"alembic_version\" "
                    "ORDER BY version_num"
                )
            ).scalars()
        )
    except SQLAlchemyError:
        raise RebaselineRefused("pre_squash_head_mismatch") from None
    if len(revisions) != 1:
        raise RebaselineRefused("pre_squash_head_mismatch")
    return revisions[0]


def validate_data_invariants(connection) -> None:  # noqa: ANN001
    """Require every fixed retained-data precondition before mutation."""
    for invariant in DATA_INVARIANTS:
        try:
            valid = connection.scalar(text(invariant.query))
        except SQLAlchemyError:
            raise RebaselineRefused("data_invariant_failed") from None
        if valid is not True:
            raise RebaselineRefused("data_invariant_failed")


def build_retained_table_snapshot(
    table_key: str,
    row_documents: Iterable[str],
    ephemeral_key: bytes,
) -> RetainedTableSnapshot:
    """Build an in-memory keyed aggregate without retaining raw row data."""
    if not isinstance(table_key, str) or not table_key:
        raise ValueError("retained table key is invalid")
    if not isinstance(ephemeral_key, bytes) or len(ephemeral_key) != 32:
        raise ValueError("ephemeral snapshot key must contain 32 bytes")
    row_macs: list[bytes] = []
    for row in row_documents:
        if not isinstance(row, str):
            raise ValueError("retained row document must be text")
        row_macs.append(
            hmac.new(
                ephemeral_key,
                row.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        )
    row_macs.sort()
    identity = table_key.encode("utf-8")
    aggregate = (
        len(identity).to_bytes(4, "big")
        + identity
        + len(row_macs).to_bytes(8, "big")
        + b"".join(row_macs)
    )
    return RetainedTableSnapshot(
        table_key=table_key,
        row_count=len(row_macs),
        keyed_digest=hmac.new(
            ephemeral_key,
            aggregate,
            hashlib.sha256,
        ).digest(),
    )


def compare_snapshots(
    before: tuple[RetainedTableSnapshot, ...],
    after: tuple[RetainedTableSnapshot, ...],
) -> None:
    if before != after:
        raise RebaselineRefused("retained_data_changed")


def _quote_identifier(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def snapshot_retained_tables(
    connection,  # noqa: ANN001
    ephemeral_key: bytes,
) -> tuple[RetainedTableSnapshot, ...]:
    """Lock and stream every retained table into in-memory keyed evidence."""
    try:
        document = PostgresCatalogReader(connection).read_document()
        table_keys = tuple(
            item.key
            for item in document.objects
            if item.key.kind == "table"
            and item.key.name not in {
                *LEGACY_TABLE_NAMES,
                "alembic_version",
                "mindatlas_schema_identity",
            }
        )
        snapshots: list[RetainedTableSnapshot] = []
        for key in table_keys:
            qualified = (
                f"{_quote_identifier(key.schema)}."
                f"{_quote_identifier(key.name)}"
            )
            connection.execute(
                text(f"LOCK TABLE {qualified} IN ACCESS EXCLUSIVE MODE")
            )
            rows = connection.execute(
                text(
                    f"SELECT row_to_json(t)::text FROM {qualified} AS t"
                ).execution_options(stream_results=True)
            ).scalars()
            snapshots.append(
                build_retained_table_snapshot(
                    f"{key.schema}.{key.name}",
                    rows,
                    ephemeral_key,
                )
            )
        return tuple(snapshots)
    except (CatalogReadError, SQLAlchemyError, ValueError):
        raise RebaselineRefused("retained_data_snapshot_failed") from None


def validate_legacy_exclusion_data(connection) -> None:  # noqa: ANN001
    """Allow only the one captured migration-owned inert control row."""
    try:
        for table_name in LEGACY_TABLE_NAMES:
            if table_name == _LEGACY_CONTROL_TABLE:
                continue
            count = connection.scalar(
                text(
                    f"SELECT count(*) FROM {_quote_identifier('public')}."
                    f"{_quote_identifier(table_name)}"
                )
            )
            if count != 0:
                raise RebaselineRefused("legacy_exclusion_data_present")
        controls = connection.execute(
            text(
                "SELECT singleton_key, active_rollout_revision_id, "
                "state_revision FROM "
                '"public"."assistant_runtime_rollout_control"'
            )
        ).mappings().all()
    except RebaselineRefused:
        raise
    except SQLAlchemyError:
        raise RebaselineRefused("legacy_exclusion_data_present") from None
    if len(controls) != 1:
        raise RebaselineRefused("legacy_exclusion_data_present")
    control = controls[0]
    if (
        control["singleton_key"] != "singleton"
        or control["active_rollout_revision_id"] is not None
        or control["state_revision"] != 0
    ):
        raise RebaselineRefused("legacy_exclusion_data_present")


def lock_legacy_tables(
    connection,  # noqa: ANN001
    manifest: SchemaExclusionManifest,
) -> None:
    """Lock every manifest table before validating and deleting legacy data."""
    try:
        tables = sorted(
            (
                item.key.schema,
                item.key.name,
            )
            for item in manifest.objects
            if item.key.kind == "table"
        )
        for schema, name in tables:
            connection.execute(
                text(
                    f"LOCK TABLE {_quote_identifier(schema)}."
                    f"{_quote_identifier(name)} IN ACCESS EXCLUSIVE MODE"
                )
            )
    except SQLAlchemyError:
        raise RebaselineRefused("legacy_exclusion_cleanup_failed") from None


def _legacy_table_drop_order(
    manifest: SchemaExclusionManifest,
) -> tuple[str, ...]:
    table_items = {
        item.key.name: item
        for item in manifest.objects
        if item.key.kind == "table"
    }
    dependencies: dict[str, set[str]] = {
        name: set() for name in table_items
    }
    for name, item in table_items.items():
        constraints = item.definition.get("constraints")
        if not isinstance(constraints, list):
            raise RebaselineRefused("rebaseline_manifest_invalid")
        for constraint in constraints:
            if not isinstance(constraint, dict) or constraint.get("type") != "f":
                continue
            definition = constraint.get("definition")
            if not isinstance(definition, str):
                raise RebaselineRefused("rebaseline_manifest_invalid")
            for target in table_items:
                pattern = (
                    r"\bREFERENCES\s+(?:(?:\"?public\"?)\.)?\"?"
                    + re.escape(target)
                    + r"\"?\s*\("
                )
                if re.search(pattern, definition):
                    dependencies[name].add(target)

    remaining = set(table_items)
    ordered: list[str] = []
    while remaining:
        leaves = sorted(
            candidate
            for candidate in remaining
            if not any(
                candidate in dependencies[other]
                for other in remaining
                if other != candidate
            )
        )
        if not leaves:
            raise RebaselineRefused("rebaseline_manifest_invalid")
        selected = leaves[0]
        ordered.append(selected)
        remaining.remove(selected)
    return tuple(ordered)


def drop_verified_legacy_objects(
    connection,  # noqa: ANN001
    manifest: SchemaExclusionManifest,
) -> None:
    """Drop only definition-locked Legacy objects, without cascade."""
    validate_legacy_exclusion_data(connection)
    try:
        deleted = connection.execute(
            text(
                'DELETE FROM "public"."assistant_runtime_rollout_control" '
                "WHERE singleton_key = 'singleton' "
                "AND active_rollout_revision_id IS NULL "
                "AND state_revision = 0"
            )
        )
        if deleted.rowcount != 1:
            raise RebaselineRefused("legacy_exclusion_data_present")

        for item in manifest.objects:
            if item.key.kind != "trigger":
                continue
            connection.execute(
                text(
                    f"DROP TRIGGER {_quote_identifier(item.key.name)} ON "
                    f"{_quote_identifier(item.key.schema)}."
                    f"{_quote_identifier(item.key.qualifier)}"
                )
            )

        table_items = {
            item.key.name: item
            for item in manifest.objects
            if item.key.kind == "table"
        }
        for table_name in _legacy_table_drop_order(manifest):
            item = table_items[table_name]
            connection.execute(
                text(
                    f"DROP TABLE {_quote_identifier(item.key.schema)}."
                    f"{_quote_identifier(item.key.name)}"
                )
            )

        for item in manifest.objects:
            if item.key.kind != "function":
                continue
            connection.execute(
                text(
                    f"DROP FUNCTION {_quote_identifier(item.key.schema)}."
                    f"{_quote_identifier(item.key.name)}"
                    f"({item.key.qualifier})"
                )
            )
    except RebaselineRefused:
        raise
    except SQLAlchemyError:
        raise RebaselineRefused("legacy_exclusion_cleanup_failed") from None

    try:
        current = PostgresCatalogReader(connection).read_document()
    except (CatalogReadError, SQLAlchemyError):
        raise RebaselineRefused("legacy_exclusion_cleanup_failed") from None
    current_keys = {item.key for item in current.objects}
    if any(key in current_keys for key in manifest.object_keys):
        raise RebaselineRefused("legacy_exclusion_object_remains")


def _projected_application_fingerprint(
    connection,  # noqa: ANN001
    *,
    control_stage: SchemaControlStage,
) -> str:
    try:
        document = PostgresCatalogReader(connection).read_document()
        projected = project_logical_application_document(
            document,
            control_stage=control_stage,
        )
        return structural_fingerprint(projected)
    except (
        CatalogReadError,
        LogicalApplicationContractError,
        SQLAlchemyError,
    ):
        raise RebaselineRefused("clean_fingerprint_mismatch") from None


def _stamp_clean_root(connection) -> None:  # noqa: ANN001
    try:
        config = Config(str(_BACKEND_ROOT / "alembic.ini"))
        script = ScriptDirectory.from_config(config)
        if script.get_revision(CLEAN_ROOT_REVISION) is None:
            raise RebaselineRefused("clean_root_unavailable")
        migration_context = MigrationContext.configure(connection)
        migration_context._ensure_version_table(purge=True)
        migration_context.stamp(script, CLEAN_ROOT_REVISION)
    except RebaselineRefused:
        raise
    except Exception:
        raise RebaselineRefused("clean_root_stamp_failed") from None


def _already_rebaselined_report(
    connection,  # noqa: ANN001
    request: RebaselineRequest,
    expected,
    *,
    operation_id: str,
    archive_manifest_digest: str,
    exclusion_manifest_digest: str,
) -> RebaselineReport:  # noqa: ANN001
    try:
        marker = read_schema_identity(connection)
    except SchemaIdentityError:
        raise RebaselineRefused("marker_contract_mismatch") from None
    if (
        marker.schema_family != expected.schema_family
        or marker.schema_revision != expected.schema_revision
        or marker.structural_fingerprint
        != expected.application_structural_fingerprint
        or marker.seed_contract_digest != expected.seed_contract_digest
        or marker.deployment_class is not request.deployment_class
        or marker.runtime_contract_version != expected.runtime_contract_version
        or marker.checkpoint_codec_version != expected.checkpoint_codec_version
        or marker.capability_feature_digest
        != expected.capability_feature_digest
        or marker.operator_auth_contract_version
        != expected.operator_auth_contract_version
        or schema_runtime_identity_digest(marker.to_identity_material())
        != marker.runtime_identity_digest
    ):
        raise RebaselineRefused("marker_contract_mismatch")
    clean_fingerprint = _projected_application_fingerprint(
        connection,
        control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
    )
    if clean_fingerprint != expected.application_structural_fingerprint:
        raise RebaselineRefused("clean_fingerprint_mismatch")
    retained = snapshot_retained_tables(connection, secrets.token_bytes(32))
    return RebaselineReport(
        operation_id=operation_id,
        result="already_rebaselined",
        deployment_class=request.deployment_class,
        before_revision=CLEAN_ROOT_REVISION,
        after_revision=CLEAN_ROOT_REVISION,
        before_structural_fingerprint=clean_fingerprint,
        after_structural_fingerprint=clean_fingerprint,
        runtime_identity_digest=marker.runtime_identity_digest,
        exclusion_manifest_digest=exclusion_manifest_digest,
        excluded_object_count=27,
        removed_known_inert_seed_rows=0,
        removed_legacy_business_rows=0,
        retained_table_count=len(retained),
        retained_row_count=sum(item.row_count for item in retained),
        retained_data_unchanged=True,
        archive_manifest_digest=archive_manifest_digest,
        build_revision=request.build_revision,
    )


def validate_rebaseline_source(
    connection,  # noqa: ANN001
    request: RebaselineRequest,
) -> None:
    """Validate the non-mutating boundary required before source inspection."""
    _pin_public_search_path(connection)
    validate_deployment_identity(connection, request)
    if read_single_alembic_version(connection) != PRE_SQUASH_HEAD:
        raise RebaselineRefused("pre_squash_head_mismatch")
    try:
        manifest = load_exclusion_manifest()
    except SchemaManifestError:
        raise RebaselineRefused("rebaseline_manifest_invalid") from None
    try:
        source = PostgresCatalogReader(connection).read_document()
    except (CatalogReadError, SQLAlchemyError):
        raise RebaselineRefused("pre_squash_catalog_unavailable") from None
    if (
        structural_fingerprint(source)
        != manifest.source_structural_fingerprint
    ):
        raise RebaselineRefused("pre_squash_fingerprint_mismatch")
    try:
        normalize_document(source, manifest=manifest, side="old")
    except SchemaComparisonError:
        raise RebaselineRefused("pre_squash_fingerprint_mismatch") from None
    validate_data_invariants(connection)
    validate_legacy_exclusion_data(connection)


def apply_rebaseline(
    connection,  # noqa: ANN001
    request: RebaselineRequest,
) -> RebaselineReport:
    """Enter the atomic rebaseline transaction and acquire its global lock."""
    validate_acknowledgement(request)
    operation_id = request.operation_id or uuid.uuid4().hex
    archive_manifest_digest = load_archive_manifest_digest()
    try:
        with connection.begin():
            _pin_public_search_path(connection)
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{REBASELINE_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text(
                    "SET LOCAL statement_timeout = "
                    f"'{REBASELINE_STATEMENT_TIMEOUT}'"
                )
            )
            acquired = connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": REBASELINE_ADVISORY_LOCK_KEY},
            )
            if acquired is not True:
                raise RebaselineRefused("rebaseline_lock_unavailable")
            validate_deployment_identity(connection, request)
            current_revision = read_single_alembic_version(connection)
            if current_revision == CLEAN_ROOT_REVISION:
                exclusion_manifest = load_exclusion_manifest()
                return _already_rebaselined_report(
                    connection,
                    request,
                    load_expected_schema_contract(),
                    operation_id=operation_id,
                    archive_manifest_digest=archive_manifest_digest,
                    exclusion_manifest_digest=exclusion_manifest.manifest_digest,
                )
            if current_revision != PRE_SQUASH_HEAD:
                raise RebaselineRefused("pre_squash_head_mismatch")
            validate_rebaseline_source(connection, request)
            before_revision = read_single_alembic_version(connection)
            manifest = load_exclusion_manifest()
            expected = load_expected_schema_contract()
            ephemeral_key = secrets.token_bytes(32)
            before = snapshot_retained_tables(connection, ephemeral_key)
            lock_legacy_tables(connection, manifest)
            validate_data_invariants(connection)
            drop_verified_legacy_objects(connection, manifest)
            pre_marker_fingerprint = _projected_application_fingerprint(
                connection,
                control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
            )
            if pre_marker_fingerprint != (
                expected.application_structural_fingerprint
            ):
                raise RebaselineRefused("clean_fingerprint_mismatch")
            install_schema_identity_controls(connection)
            _stamp_clean_root(connection)
            marker = insert_schema_identity(
                connection,
                deployment_class=request.deployment_class,
                expected=expected,
            )
            after = snapshot_retained_tables(connection, ephemeral_key)
            compare_snapshots(before, after)
            clean_fingerprint = _projected_application_fingerprint(
                connection,
                control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
            )
            if clean_fingerprint != expected.application_structural_fingerprint:
                raise RebaselineRefused("clean_fingerprint_mismatch")
            if read_single_alembic_version(connection) != CLEAN_ROOT_REVISION:
                raise RebaselineRefused("clean_root_stamp_failed")
            return RebaselineReport(
                operation_id=operation_id,
                result="rebaselined",
                deployment_class=request.deployment_class,
                before_revision=before_revision,
                after_revision=CLEAN_ROOT_REVISION,
                before_structural_fingerprint=(
                    manifest.source_structural_fingerprint
                ),
                after_structural_fingerprint=clean_fingerprint,
                runtime_identity_digest=marker.runtime_identity_digest,
                exclusion_manifest_digest=manifest.manifest_digest,
                excluded_object_count=len(manifest.objects),
                removed_known_inert_seed_rows=1,
                removed_legacy_business_rows=0,
                retained_table_count=len(after),
                retained_row_count=sum(item.row_count for item in after),
                retained_data_unchanged=True,
                archive_manifest_digest=archive_manifest_digest,
                build_revision=request.build_revision,
            )
    except RebaselineRefused:
        raise
    except SchemaIdentityError:
        raise RebaselineRefused("marker_contract_mismatch") from None
    except SQLAlchemyError:
        raise RebaselineRefused("rebaseline_transaction_unavailable") from None

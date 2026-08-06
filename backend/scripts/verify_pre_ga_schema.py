"""Fail-closed verification for the pre-GA clean schema lineage."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
import warnings

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.schema.application_contract import (
    LogicalApplicationContractError,
    SchemaControlStage,
    load_logical_application_contract,
    project_logical_application_document,
)
from app.schema.canonical import (
    SchemaComparisonError,
    canonical_json_bytes,
    normalize_document,
    structural_fingerprint,
)
from app.schema.catalog import CatalogReadError, PostgresCatalogReader
from app.schema.contracts import (
    CLEAN_ROOT_REVISION,
    PRE_SQUASH_HEAD,
    SCHEMA_FAMILY,
    SCHEMA_IDENTITY_CONTRACT_VERSION,
    CanonicalObjectKey,
    CanonicalSchemaDocument,
    DeploymentClass,
)
from app.schema.exclusions import (
    LEGACY_TABLE_NAMES,
    expected_legacy_object_keys,
)
from app.schema.identity import (
    SchemaIdentityError,
    load_expected_schema_contract,
    read_schema_identity,
    schema_runtime_identity_digest,
)
from app.schema.sql_objects import (
    SchemaManifestError,
    load_exclusion_manifest,
    load_pre_squash_snapshot,
)


_LEGACY_CONTROL_TABLE = "assistant_runtime_rollout_control"
_ENV_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
_KNOWN_CLI_WARNING = (
    "The default value of `allowed_objects` will change in a future version. "
    "Pass an explicit value (e.g., allowed_objects='messages' or "
    "allowed_objects='core') to suppress this warning."
)


class SchemaVerificationError(RuntimeError):
    """Bounded verification failure safe for automation and logs."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


@dataclass(frozen=True)
class EquivalenceVerification:
    old_application_fingerprint: str
    clean_application_fingerprint: str
    clean_control_fingerprint: str
    exclusion_count: int


@dataclass(frozen=True)
class FreshVerification:
    application_fingerprint: str
    control_fingerprint: str
    deployment_class: str
    exclusion_count: int


def _read_database_url(env_name: str) -> str:
    if _ENV_NAME_PATTERN.fullmatch(env_name) is None:
        raise SchemaVerificationError("database_url_env_invalid")
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise SchemaVerificationError("database_url_missing")
    return value


def _is_known_cli_warning(item: warnings.WarningMessage) -> bool:
    return (
        item.category.__module__ == "langchain_core._api.deprecation"
        and item.category.__name__ == "LangChainPendingDeprecationWarning"
        and str(item.message) == _KNOWN_CLI_WARNING
    )


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _read_single_head(connection, expected: str, safe_code: str) -> None:  # noqa: ANN001
    rows = tuple(
        str(value)
        for value in connection.execute(
            text("SELECT version_num FROM alembic_version ORDER BY version_num")
        ).scalars()
    )
    if rows != (expected,):
        raise SchemaVerificationError(safe_code)


def _application_schemas(connection) -> tuple[str, ...]:  # noqa: ANN001
    schemas = tuple(
        str(value)
        for value in connection.execute(
            text(
                "SELECT nspname FROM pg_catalog.pg_namespace "
                "WHERE nspname <> 'information_schema' "
                "AND nspname NOT LIKE 'pg_catalog' "
                "AND nspname NOT LIKE 'pg_toast%' "
                "AND nspname NOT LIKE 'pg_temp%' "
                "ORDER BY nspname"
            )
        ).scalars()
    )
    if "public" not in schemas:
        raise SchemaVerificationError("application_namespace_missing")
    return schemas


def _require_inert_legacy_state(connection) -> None:  # noqa: ANN001
    for table_name in LEGACY_TABLE_NAMES:
        if table_name == _LEGACY_CONTROL_TABLE:
            continue
        count = int(
            connection.execute(
                text(f'SELECT count(*) FROM "{table_name}"')
            ).scalar_one()
        )
        if count != 0:
            raise SchemaVerificationError("legacy_exclusion_data_present")

    controls = connection.execute(
        text(
            "SELECT singleton_key, active_rollout_revision_id, state_revision "
            "FROM assistant_runtime_rollout_control"
        )
    ).mappings().all()
    if len(controls) != 1:
        raise SchemaVerificationError("legacy_exclusion_data_present")
    control = controls[0]
    if (
        control["singleton_key"] != "singleton"
        or control["active_rollout_revision_id"] is not None
        or control["state_revision"] != 0
    ):
        raise SchemaVerificationError("legacy_exclusion_data_present")


def _read_old_database(database_url: str):  # noqa: ANN201
    engine = None
    try:
        engine = create_engine(
            _sqlalchemy_url(database_url),
            future=True,
            pool_pre_ping=True,
            isolation_level="REPEATABLE READ",
        )
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            _read_single_head(
                connection,
                PRE_SQUASH_HEAD,
                "pre_squash_head_mismatch",
            )
            _require_inert_legacy_state(connection)
            return PostgresCatalogReader(
                connection,
                schemas=_application_schemas(connection),
            ).read_document()
    except SchemaVerificationError:
        raise
    except (CatalogReadError, SQLAlchemyError):
        raise SchemaVerificationError("schema_source_unavailable") from None
    finally:
        if engine is not None:
            engine.dispose()


def _read_clean_database(database_url: str):  # noqa: ANN201
    engine = None
    try:
        engine = create_engine(
            _sqlalchemy_url(database_url),
            future=True,
            pool_pre_ping=True,
            isolation_level="REPEATABLE READ",
        )
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            _read_single_head(
                connection,
                CLEAN_ROOT_REVISION,
                "clean_root_head_mismatch",
            )
            document = PostgresCatalogReader(
                connection,
                schemas=_application_schemas(connection),
            ).read_document()
            marker = read_schema_identity(connection)
            return document, marker
    except SchemaVerificationError:
        raise
    except SchemaIdentityError as exc:
        raise SchemaVerificationError(exc.safe_code) from None
    except (CatalogReadError, SQLAlchemyError):
        raise SchemaVerificationError("clean_schema_unavailable") from None
    finally:
        if engine is not None:
            engine.dispose()


def _verify_clean_contract(
    clean_document,  # noqa: ANN001
    marker,  # noqa: ANN001
    *,
    exclusions,  # noqa: ANN001
    logical_contract,  # noqa: ANN001
    expected,  # noqa: ANN001
    required_deployment_class: DeploymentClass | None,
) -> tuple[FreshVerification, CanonicalSchemaDocument]:
    normalize_document(
        clean_document,
        manifest=exclusions,
        side="clean",
    )
    logical_clean = project_logical_application_document(
        clean_document,
        control_stage=SchemaControlStage.CLEAN_ROOT_MIGRATED,
    )

    logical_keys = {item.key for item in logical_clean.objects}
    clean_controls = tuple(
        item for item in clean_document.objects if item.key not in logical_keys
    )
    control_document = CanonicalSchemaDocument(
        canonicalization_version=1,
        postgres_major=clean_document.postgres_major,
        objects=clean_controls,
    )
    control_fingerprint = structural_fingerprint(control_document)
    if control_fingerprint != expected.schema_identity_control_fingerprint:
        raise SchemaVerificationError("schema_control_contract_drift")

    logical_bytes = canonical_json_bytes(logical_clean.to_payload())
    expected_bytes = canonical_json_bytes(
        logical_contract.logical_application_document.to_payload()
    )
    application_fingerprint = structural_fingerprint(logical_clean)
    if (
        logical_bytes != expected_bytes
        or application_fingerprint
        != logical_contract.logical_application_fingerprint
        or application_fingerprint
        != expected.application_structural_fingerprint
    ):
        raise SchemaVerificationError("logical_application_schema_difference")

    if (
        marker.schema_family != SCHEMA_FAMILY
        or marker.schema_revision != CLEAN_ROOT_REVISION
        or marker.structural_fingerprint != application_fingerprint
        or marker.seed_contract_digest != expected.seed_contract_digest
        or marker.runtime_contract_version != expected.runtime_contract_version
        or marker.checkpoint_codec_version
        != expected.checkpoint_codec_version
        or marker.capability_feature_digest
        != expected.capability_feature_digest
        or marker.operator_auth_contract_version
        != expected.operator_auth_contract_version
        or marker.identity_contract_version
        != SCHEMA_IDENTITY_CONTRACT_VERSION
        or (
            required_deployment_class is not None
            and marker.deployment_class is not required_deployment_class
        )
        or marker.runtime_identity_digest
        != schema_runtime_identity_digest(marker.to_identity_material())
    ):
        raise SchemaVerificationError("marker_contract_mismatch")

    return (
        FreshVerification(
            application_fingerprint=application_fingerprint,
            control_fingerprint=control_fingerprint,
            deployment_class=marker.deployment_class.value,
            exclusion_count=len(exclusions.objects),
        ),
        logical_clean,
    )


def verify_equivalence(
    *,
    old_database_url: str,
    clean_database_url: str,
) -> EquivalenceVerification:
    """Prove old-head and clean-root logical application bytes are identical."""
    try:
        exclusions = load_exclusion_manifest()
        snapshot = load_pre_squash_snapshot()
        logical_contract = load_logical_application_contract()
        expected = load_expected_schema_contract()

        expected_exclusion_keys = {
            CanonicalObjectKey(*parts)
            for parts in expected_legacy_object_keys()
        }
        if set(exclusions.object_keys) != expected_exclusion_keys:
            raise SchemaVerificationError(
                "legacy_exclusion_allowlist_mismatch"
            )

        old_document = _read_old_database(old_database_url)
        if (
            structural_fingerprint(old_document)
            != exclusions.source_structural_fingerprint
            or canonical_json_bytes(old_document.to_payload())
            != canonical_json_bytes(snapshot.source_document.to_payload())
        ):
            raise SchemaVerificationError("pre_squash_schema_drift")
        old_without_legacy = normalize_document(
            old_document,
            manifest=exclusions,
            side="old",
        )
        logical_old = project_logical_application_document(
            old_without_legacy,
            control_stage=SchemaControlStage.PRE_SQUASH_MIGRATED,
        )

        clean_document, marker = _read_clean_database(clean_database_url)
        clean_proof, logical_clean = _verify_clean_contract(
            clean_document,
            marker,
            exclusions=exclusions,
            logical_contract=logical_contract,
            expected=expected,
            required_deployment_class=None,
        )

        old_bytes = canonical_json_bytes(logical_old.to_payload())
        clean_bytes = canonical_json_bytes(logical_clean.to_payload())
        expected_bytes = canonical_json_bytes(
            logical_contract.logical_application_document.to_payload()
        )
        if old_bytes != clean_bytes or old_bytes != expected_bytes:
            raise SchemaVerificationError(
                "logical_application_schema_difference"
            )

        old_fingerprint = structural_fingerprint(logical_old)
        clean_fingerprint = structural_fingerprint(logical_clean)
        expected_fingerprint = logical_contract.logical_application_fingerprint
        if (
            old_fingerprint != expected_fingerprint
            or clean_fingerprint != expected_fingerprint
            or expected.application_structural_fingerprint
            != expected_fingerprint
        ):
            raise SchemaVerificationError(
                "logical_application_schema_difference"
            )

        return EquivalenceVerification(
            old_application_fingerprint=old_fingerprint,
            clean_application_fingerprint=clean_fingerprint,
            clean_control_fingerprint=clean_proof.control_fingerprint,
            exclusion_count=len(exclusions.objects),
        )
    except SchemaVerificationError:
        raise
    except (
        LogicalApplicationContractError,
        SchemaComparisonError,
        SchemaIdentityError,
        SchemaManifestError,
    ) as exc:
        raise SchemaVerificationError(exc.safe_code) from None


def verify_fresh(
    *,
    clean_database_url: str,
    deployment_class: str,
) -> FreshVerification:
    try:
        try:
            required_deployment_class = DeploymentClass(deployment_class)
        except ValueError:
            raise SchemaVerificationError(
                "schema_deployment_class_invalid"
            ) from None
        exclusions = load_exclusion_manifest()
        logical_contract = load_logical_application_contract()
        expected = load_expected_schema_contract()
        clean_document, marker = _read_clean_database(clean_database_url)
        proof, _logical_clean = _verify_clean_contract(
            clean_document,
            marker,
            exclusions=exclusions,
            logical_contract=logical_contract,
            expected=expected,
            required_deployment_class=required_deployment_class,
        )
        return proof
    except SchemaVerificationError:
        raise
    except (
        LogicalApplicationContractError,
        SchemaComparisonError,
        SchemaIdentityError,
        SchemaManifestError,
    ) as exc:
        raise SchemaVerificationError(exc.safe_code) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    equivalence = subparsers.add_parser("equivalence", allow_abbrev=False)
    equivalence.add_argument("--old-database-url-env", required=True)
    equivalence.add_argument("--clean-database-url-env", required=True)
    fresh = subparsers.add_parser("fresh", allow_abbrev=False)
    fresh.add_argument("--clean-database-url-env", required=True)
    fresh.add_argument("--deployment-class", required=True)
    runtime = subparsers.add_parser("runtime", allow_abbrev=False)
    runtime.add_argument("--database-url-env", required=True)
    args = parser.parse_args(argv)

    try:
        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            if args.mode == "equivalence":
                result = verify_equivalence(
                    old_database_url=_read_database_url(
                        args.old_database_url_env
                    ),
                    clean_database_url=_read_database_url(
                        args.clean_database_url_env
                    ),
                )
                success_message = (
                    "schema_equivalence_ok "
                    f"old_head={PRE_SQUASH_HEAD} "
                    f"clean_head={CLEAN_ROOT_REVISION} "
                    "application_fingerprint="
                    f"{result.clean_application_fingerprint} "
                    f"control_fingerprint={result.clean_control_fingerprint} "
                    f"exclusions={result.exclusion_count}"
                )
            else:
                deployment_class = (
                    os.environ.get("MINDATLAS_DEPLOYMENT_CLASS", "").strip()
                    if args.mode == "runtime"
                    else args.deployment_class
                )
                fresh_result = verify_fresh(
                    clean_database_url=_read_database_url(
                        args.database_url_env
                        if args.mode == "runtime"
                        else args.clean_database_url_env
                    ),
                    deployment_class=deployment_class,
                )
                success_message = (
                    "schema_fresh_ok "
                    f"clean_head={CLEAN_ROOT_REVISION} "
                    f"deployment_class={fresh_result.deployment_class} "
                    "application_fingerprint="
                    f"{fresh_result.application_fingerprint} "
                    f"control_fingerprint={fresh_result.control_fingerprint} "
                    f"exclusions={fresh_result.exclusion_count}"
                )
        if any(
            not _is_known_cli_warning(item) for item in caught_warnings
        ):
            raise SchemaVerificationError("verification_warning_unexpected")
        print(success_message)
        return 0
    except SchemaVerificationError as exc:
        print(exc.safe_code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

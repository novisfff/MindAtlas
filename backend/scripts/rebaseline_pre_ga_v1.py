#!/usr/bin/env python3
"""Inspect or apply the guarded non-production pre-GA rebaseline."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import create_engine

from app.schema.canonical import canonical_json_bytes
from app.schema.contracts import (
    CLEAN_ROOT_REVISION,
    PRE_SQUASH_HEAD,
    DeploymentClass,
    SchemaRuntimeIdentityMaterial,
)
from app.schema.rebaseline import (
    MAINTENANCE_ACKNOWLEDGEMENT,
    RebaselineRefused,
    RebaselineRequest,
    RebaselineReport,
    SAFE_REPORT_FIELDS,
    apply_rebaseline,
    load_archive_manifest_digest,
    validate_build_revision,
    validate_rebaseline_source,
)
from app.schema.identity import (
    load_expected_schema_contract,
    schema_runtime_identity_digest,
)
from app.schema.sql_objects import load_exclusion_manifest


_REPORT_SECRET_PATTERN = re.compile(
    r"(?:://|password|token|cookie|authorization|\b(?:select|insert|update|delete|drop)\s)",
    re.IGNORECASE,
)
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_OPERATION_ID = re.compile(r"(?:inspect|[0-9a-f]{32})\Z")


def _validated_report_content(report: RebaselineReport) -> bytes:
    payload = report.to_payload()
    _validate_report_payload(payload)
    return canonical_json_bytes(payload) + b"\n"


def _validate_report_payload(payload: object) -> None:
    if not isinstance(payload, dict) or set(payload) != SAFE_REPORT_FIELDS:
        raise RebaselineRefused("rebaseline_report_invalid")
    if payload["schemaVersion"] != 1 or type(payload["schemaVersion"]) is not int:
        raise RebaselineRefused("rebaseline_report_invalid")
    if not isinstance(payload["operationId"], str) or _OPERATION_ID.fullmatch(
        payload["operationId"]
    ) is None:
        raise RebaselineRefused("rebaseline_report_invalid")
    if payload["result"] not in {"eligible", "rebaselined", "already_rebaselined"}:
        raise RebaselineRefused("rebaseline_report_invalid")
    if payload["deploymentClass"] not in {item.value for item in DeploymentClass}:
        raise RebaselineRefused("rebaseline_report_invalid")
    if payload["beforeRevision"] not in {PRE_SQUASH_HEAD, CLEAN_ROOT_REVISION}:
        raise RebaselineRefused("rebaseline_report_invalid")
    if payload["afterRevision"] not in {PRE_SQUASH_HEAD, CLEAN_ROOT_REVISION}:
        raise RebaselineRefused("rebaseline_report_invalid")
    for key in (
        "beforeStructuralFingerprint",
        "afterStructuralFingerprint",
        "runtimeIdentityDigest",
        "exclusionManifestDigest",
        "archiveManifestDigest",
    ):
        if not isinstance(payload[key], str) or _HEX64.fullmatch(payload[key]) is None:
            raise RebaselineRefused("rebaseline_report_invalid")
    for key in (
        "excludedObjectCount",
        "removedKnownInertSeedRows",
        "removedLegacyBusinessRows",
        "retainedTableCount",
        "retainedRowCount",
    ):
        if type(payload[key]) is not int or payload[key] < 0:
            raise RebaselineRefused("rebaseline_report_invalid")
    if type(payload["retainedDataUnchanged"]) is not bool:
        raise RebaselineRefused("rebaseline_report_invalid")
    try:
        validate_build_revision(payload["buildRevision"])
    except (TypeError, ValueError):
        raise RebaselineRefused("rebaseline_report_invalid") from None
    for value in payload.values():
        if isinstance(value, str) and _REPORT_SECRET_PATTERN.search(value):
            raise RebaselineRefused("rebaseline_report_invalid")


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--report-file", required=True, type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Guarded pre-GA non-production schema rebaseline."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = commands.add_parser("inspect")
    _add_common_arguments(inspect_parser)
    apply_parser = commands.add_parser("apply")
    _add_common_arguments(apply_parser)
    apply_parser.add_argument(
        "--acknowledge-local-maintenance",
        required=True,
    )
    return parser


def parse_apply_args(argv: list[str]) -> argparse.Namespace:
    args = build_parser().parse_args(argv)
    if args.command != "apply":
        raise ValueError("apply arguments required")
    return args


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_report_path(destination: Path) -> None:
    try:
        parent = destination.parent
        stat = parent.stat()
        if (
            not parent.is_dir()
            or stat.st_uid != os.getuid()
            or not os.access(parent, os.W_OK)
        ):
            raise RebaselineRefused("report_path_invalid")
    except RebaselineRefused:
        raise
    except OSError:
        raise RebaselineRefused("report_path_invalid") from None


def _is_safe_prior_report(
    content: bytes,
    *,
    deployment_class: DeploymentClass,
    build_revision: str,
    command: str,
    enforce_contract: bool = True,
) -> bool:
    try:
        payload = json.loads(content)
        _validate_report_payload(payload)
        if payload.get("deploymentClass") != deployment_class.value:
            return False
        if command == "inspect" and payload.get("result") != "eligible":
            return False
        if command == "apply" and payload.get("result") == "eligible":
            return False
        prior_build_revision = payload.get("buildRevision")
        if prior_build_revision != build_revision:
            return False
        if canonical_json_bytes(payload) + b"\n" != content:
            return False
        return not enforce_contract or _prior_report_matches_contract(
            payload,
            deployment_class=deployment_class,
            build_revision=build_revision,
            command=command,
        )
    except Exception:
        return False


def _prior_report_matches_contract(
    payload: dict[str, object],
    *,
    deployment_class: DeploymentClass,
    build_revision: str,
    command: str,
) -> bool:
    operation_id = payload["operationId"]
    if not isinstance(operation_id, str):
        return False
    request = RebaselineRequest(
        deployment_class=deployment_class,
        acknowledgement=MAINTENANCE_ACKNOWLEDGEMENT,
        build_revision=build_revision,
        operation_id=(operation_id if operation_id != "inspect" else None),
    )
    exclusions = load_exclusion_manifest()
    expected = load_expected_schema_contract()
    material = SchemaRuntimeIdentityMaterial(
        schema_family=expected.schema_family,
        schema_revision=expected.schema_revision,
        structural_fingerprint=expected.application_structural_fingerprint,
        seed_contract_digest=expected.seed_contract_digest,
        deployment_class=deployment_class,
        runtime_contract_version=expected.runtime_contract_version,
        checkpoint_codec_version=expected.checkpoint_codec_version,
        capability_feature_digest=expected.capability_feature_digest,
        operator_auth_contract_version=expected.operator_auth_contract_version,
    )
    if command == "inspect":
        prior = _inspect_report(
            request,
            before_structural_fingerprint=(
                exclusions.source_structural_fingerprint
            ),
        ).to_payload()
        prior["operationId"] = operation_id
        return prior == payload
    result = payload["result"]
    before_is_old = result == "rebaselined"
    return all(
        (
            payload["beforeRevision"]
            == (PRE_SQUASH_HEAD if before_is_old else CLEAN_ROOT_REVISION),
            payload["afterRevision"] == CLEAN_ROOT_REVISION,
            payload["beforeStructuralFingerprint"]
            == (
                exclusions.source_structural_fingerprint
                if before_is_old
                else expected.application_structural_fingerprint
            ),
            payload["afterStructuralFingerprint"]
            == expected.application_structural_fingerprint,
            payload["runtimeIdentityDigest"]
            == schema_runtime_identity_digest(material),
            payload["exclusionManifestDigest"]
            == exclusions.manifest_digest,
            payload["excludedObjectCount"] == len(exclusions.objects),
            payload["removedKnownInertSeedRows"]
            == (1 if before_is_old else 0),
            payload["removedLegacyBusinessRows"] == 0,
            payload["retainedDataUnchanged"] is True,
            payload["archiveManifestDigest"] == load_archive_manifest_digest(),
        )
    )


@dataclass
class ReportReservation:
    """Durable same-directory provisional report reserved before DB access."""

    destination: Path
    provisional: Path
    prior_destination: bytes | None
    operation_id: str
    lock_descriptor: int

    def publish(self, report: RebaselineReport) -> None:
        if report.operation_id != self.operation_id:
            raise RebaselineRefused("rebaseline_report_invalid")
        content = _validated_report_content(report)
        publish_temp: Path | None = None
        try:
            if self.prior_destination is None:
                if not self.destination.exists() or os.stat(
                    self.destination
                ).st_ino != os.stat(self.provisional).st_ino:
                    raise RebaselineRefused("report_destination_collision")
            elif self.destination.read_bytes() != self.prior_destination:
                raise RebaselineRefused("report_destination_collision")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.destination.name}.publish-",
                dir=self.destination.parent,
            )
            publish_temp = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(publish_temp, self.destination)
            publish_temp = None
            _fsync_directory(self.destination.parent)
            self.provisional.unlink()
            _fsync_directory(self.destination.parent)
        except RebaselineRefused:
            raise
        except (OSError, UnicodeError, TypeError, ValueError):
            raise RebaselineRefused("report_write_failed") from None
        finally:
            if publish_temp is not None and publish_temp.exists():
                try:
                    publish_temp.unlink()
                except OSError:
                    pass

    def discard(self) -> None:
        try:
            if (
                self.prior_destination is None
                and self.destination.exists()
                and self.provisional.exists()
                and os.stat(self.destination).st_ino
                == os.stat(self.provisional).st_ino
            ):
                self.destination.unlink()
            if self.provisional.exists():
                self.provisional.unlink()
                _fsync_directory(self.destination.parent)
        except OSError:
            raise RebaselineRefused("report_write_failed") from None

    def release(self) -> None:
        try:
            fcntl.flock(self.lock_descriptor, fcntl.LOCK_UN)
            os.close(self.lock_descriptor)
        except OSError:
            raise RebaselineRefused("report_write_failed") from None


def reserve_report_path(
    destination: Path,
    *,
    deployment_class: DeploymentClass,
    build_revision: str,
    command: str,
    operation_id: str,
    enforce_contract: bool = True,
) -> ReportReservation:
    """Provision and fsync the report destination before database access."""
    validate_report_path(destination)
    parent = destination.parent
    lock_path = parent / f".{destination.name}.lock"
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_descriptor)
            raise RebaselineRefused("report_destination_busy") from None
    except RebaselineRefused:
        raise
    except OSError:
        raise RebaselineRefused("report_write_failed") from None
    try:
        prior_destination: bytes | None = None
        if destination.exists():
            prior_destination = destination.read_bytes()
            if not _is_safe_prior_report(
                prior_destination,
                deployment_class=deployment_class,
                build_revision=build_revision,
                command=command,
                enforce_contract=enforce_contract,
            ):
                raise RebaselineRefused("report_destination_collision")
        pending = canonical_json_bytes(
            {
                "schemaVersion": 1,
                "operationId": operation_id,
                "result": "pending",
            }
        ) + b"\n"
        descriptor, provisional_name = tempfile.mkstemp(
            prefix=f".{destination.name}.pending-",
            dir=parent,
        )
        provisional = Path(provisional_name)
        destination_linked = False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(pending)
                stream.flush()
                os.fsync(stream.fileno())
            if prior_destination is None:
                try:
                    os.link(provisional, destination)
                except FileExistsError:
                    raise RebaselineRefused(
                        "report_destination_collision"
                    ) from None
                destination_linked = True
            _fsync_directory(parent)
        except Exception:
            if destination_linked and destination.exists():
                try:
                    if os.stat(destination).st_ino == os.stat(provisional).st_ino:
                        destination.unlink()
                except OSError:
                    pass
            if provisional.exists():
                provisional.unlink()
                _fsync_directory(parent)
            raise
        return ReportReservation(
            destination=destination,
            provisional=provisional,
            prior_destination=prior_destination,
            operation_id=operation_id,
            lock_descriptor=lock_descriptor,
        )
    except RebaselineRefused:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        except OSError:
            pass
        raise
    except (OSError, UnicodeError, TypeError, ValueError):
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        except OSError:
            pass
        raise RebaselineRefused("report_write_failed") from None


def write_report_atomic(report: RebaselineReport, destination: Path) -> None:
    """Write only the bounded report payload with atomic replacement."""
    reservation = reserve_report_path(
        destination,
        deployment_class=report.deployment_class,
        build_revision=report.build_revision,
        command="inspect" if report.result == "eligible" else "apply",
        operation_id=report.operation_id,
        enforce_contract=False,
    )
    try:
        reservation.publish(report)
        reservation.release()
    except RebaselineRefused:
        try:
            reservation.discard()
        except RebaselineRefused:
            pass
        try:
            reservation.release()
        except RebaselineRefused:
            pass
        raise


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _inspect_report(
    request: RebaselineRequest,
    *,
    before_structural_fingerprint: str,
) -> RebaselineReport:
    expected = load_expected_schema_contract()
    exclusions = load_exclusion_manifest()
    material = SchemaRuntimeIdentityMaterial(
        schema_family=expected.schema_family,
        schema_revision=expected.schema_revision,
        structural_fingerprint=expected.application_structural_fingerprint,
        seed_contract_digest=expected.seed_contract_digest,
        deployment_class=request.deployment_class,
        runtime_contract_version=expected.runtime_contract_version,
        checkpoint_codec_version=expected.checkpoint_codec_version,
        capability_feature_digest=expected.capability_feature_digest,
        operator_auth_contract_version=expected.operator_auth_contract_version,
    )
    return RebaselineReport(
        operation_id=request.operation_id or uuid.uuid4().hex,
        result="eligible",
        deployment_class=request.deployment_class,
        before_revision="b6e2d4f8a901",
        after_revision="b6e2d4f8a901",
        before_structural_fingerprint=before_structural_fingerprint,
        after_structural_fingerprint=expected.application_structural_fingerprint,
        runtime_identity_digest=schema_runtime_identity_digest(material),
        exclusion_manifest_digest=exclusions.manifest_digest,
        excluded_object_count=len(exclusions.objects),
        removed_known_inert_seed_rows=0,
        removed_legacy_business_rows=0,
        retained_table_count=0,
        retained_row_count=0,
        retained_data_unchanged=True,
        archive_manifest_digest=load_archive_manifest_digest(),
        build_revision=request.build_revision,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reservation: ReportReservation | None = None
    database_committed = False
    operation_id = uuid.uuid4().hex
    try:
        deployment_raw = os.environ.get("MINDATLAS_DEPLOYMENT_CLASS", "")
        try:
            deployment_class = DeploymentClass(deployment_raw)
        except ValueError:
            raise RebaselineRefused("deployment_class_invalid") from None
        env_name = args.database_url_env
        if _ENV_NAME.fullmatch(env_name) is None:
            raise RebaselineRefused("database_url_env_invalid")
        database_url = os.environ.get(env_name, "").strip()
        if not database_url:
            raise RebaselineRefused("database_url_missing")
        try:
            request = RebaselineRequest(
                deployment_class=deployment_class,
                acknowledgement=(
                    args.acknowledge_local_maintenance
                    if args.command == "apply"
                    else MAINTENANCE_ACKNOWLEDGEMENT
                ),
                build_revision=os.environ.get(
                    "MINDATLAS_BUILD_REVISION", "unknown"
                ),
                operation_id=operation_id,
            )
        except ValueError:
            raise RebaselineRefused("build_revision_invalid") from None
        reservation = reserve_report_path(
            args.report_file,
            deployment_class=request.deployment_class,
            build_revision=request.build_revision,
            command=args.command,
            operation_id=operation_id,
        )
        engine = None
        try:
            engine = create_engine(_sqlalchemy_url(database_url), future=True)
            try:
                with engine.connect() as connection:
                    if args.command == "apply":
                        report = apply_rebaseline(connection, request)
                        database_committed = True
                    else:
                        validate_rebaseline_source(connection, request)
                        manifest = load_exclusion_manifest()
                        report = _inspect_report(
                            request,
                            before_structural_fingerprint=(
                                manifest.source_structural_fingerprint
                            ),
                        )
            finally:
                if engine is not None:
                    engine.dispose()
        except RebaselineRefused:
            raise
        except Exception:
            raise RebaselineRefused(
                "rebaseline_database_unavailable"
            ) from None
        reservation.publish(report)
        reservation.release()
        reservation = None
    except RebaselineRefused as exc:
        if reservation is not None and not database_committed:
            try:
                reservation.discard()
            except RebaselineRefused:
                pass
        if reservation is not None:
            try:
                reservation.release()
            except RebaselineRefused:
                pass
        print(exc.safe_code, file=sys.stderr)
        return 2
    except Exception:
        if reservation is not None and not database_committed:
            try:
                reservation.discard()
            except RebaselineRefused:
                pass
        if reservation is not None:
            try:
                reservation.release()
            except RebaselineRefused:
                pass
        print("rebaseline_failed", file=sys.stderr)
        return 2
    print(f"rebaseline_{report.result}_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

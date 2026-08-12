#!/usr/bin/env python3
"""Execute the concrete PostgreSQL checks consumed by schema exit evidence.

This runner deliberately produces observations, rather than accepting a set of
caller-supplied booleans.  The companion ``verify_pre_ga_schema.py exit``
command validates the exact check set and its digest before deriving evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import timedelta

from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tests._bootstrap import bootstrap_backend_imports, reset_caches  # noqa: E402

bootstrap_backend_imports()
reset_caches()

from app.schema.canonical import (  # noqa: E402
    canonical_json_bytes,
    structural_fingerprint,
)
from app.schema.catalog import PostgresCatalogReader  # noqa: E402
from app.schema.compatibility import runtime_schema_compatibility  # noqa: E402
from app.schema.contracts import (  # noqa: E402
    CLEAN_ROOT_REVISION,
    PRE_SQUASH_HEAD,
    DeploymentClass,
)
from app.schema.rebaseline import (  # noqa: E402
    MAINTENANCE_ACKNOWLEDGEMENT,
    REBASELINE_ADVISORY_LOCK_KEY,
    RebaselineRefused,
    RebaselineRequest,
    apply_rebaseline,
)
import app.schema.rebaseline as rebaseline_module  # noqa: E402
from tests.main_agent_postgres_support import (
    insert_complete_main_agent_run,
)  # noqa: E402
from tests.postgres_destructive_guard import (
    reset_disposable_public_schema,
)  # noqa: E402
from tests.pre_squash_fixture import install_pre_squash_fixture  # noqa: E402
from tests.schema_baseline_support import upgrade_clean_root_checked  # noqa: E402


class ExitProofFailure(RuntimeError):
    """Bounded proof-stage failure safe for CI output."""

    def __init__(self, safe_code: str) -> None:
        super().__init__(safe_code)
        self.safe_code = safe_code


def _sqlalchemy_url(url: str) -> str:
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def _read_url(env_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise RuntimeError("exit_proof_database_url_missing")
    return value


def _engine(url: str):  # noqa: ANN202
    return create_engine(_sqlalchemy_url(url), future=True, pool_pre_ping=True)


def _head(engine):  # noqa: ANN001, ANN202
    with engine.connect() as connection:
        if not connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='alembic_version'"
            )
        ).first():
            return None
        return connection.execute(
            text("SELECT version_num FROM alembic_version ORDER BY version_num")
        ).scalar()


def _set_database_comment(
    engine,
    deployment_class: str | None,
) -> None:  # noqa: ANN001
    with engine.begin() as connection:
        name = connection.scalar(text("SELECT current_database()"))
        if not isinstance(name, str):
            raise RuntimeError("exit_proof_database_identity_unavailable")
        escaped_name = name.replace(chr(34), chr(34) * 2)
        if deployment_class is None:
            connection.exec_driver_sql(f'COMMENT ON DATABASE "{escaped_name}" IS NULL')
            return
        connection.exec_driver_sql(
            f'COMMENT ON DATABASE "{escaped_name}" IS %s',
            (f"mindatlas:deployment_class={deployment_class}",),
        )


def _upgrade_clean(url: str, *, build: str) -> None:
    engine = _engine(url)
    try:
        reset_disposable_public_schema(engine)
    finally:
        engine.dispose()
    upgrade_clean_root_checked(
        url,
        deployment_class="rehearsal",
        app_env="test",
        build_revision=build,
    )


def _run_alembic(url: str, command: str, *, acknowledgement: str | None = None):
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": url,
            "MINDATLAS_DEPLOYMENT_CLASS": "rehearsal",
            "APP_ENV": "test",
            "APP_BUILD_REVISION": "schema-exit-proof",
        }
    )
    if acknowledgement is None:
        env.pop("MINDATLAS_TEST_ALLOW_EMPTY_SCHEMA_DOWNGRADE", None)
    else:
        env["MINDATLAS_TEST_ALLOW_EMPTY_SCHEMA_DOWNGRADE"] = acknowledgement
    return subprocess.run(
        [sys.executable, "-m", "alembic", *command.split()],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _fresh_upgrade(url: str, *, build: str) -> dict[str, object]:
    engine = _engine(url)
    try:
        reset_disposable_public_schema(engine)
        before = _head(engine)
    finally:
        engine.dispose()
    upgrade_clean_root_checked(
        url,
        deployment_class="rehearsal",
        app_env="test",
        build_revision=build,
    )
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            marker_revision = connection.execute(
                text("SELECT schema_revision FROM mindatlas_schema_identity")
            ).scalar_one()
        after = _head(engine)
    finally:
        engine.dispose()
    return {
        "name": "fresh_upgrade",
        "result": "pass",
        "observations": {
            "beforeHead": before,
            "afterHead": after,
            "markerRevision": str(marker_revision),
        },
    }


def _downgrade_guard(url: str) -> dict[str, object]:
    _upgrade_clean(url, build="schema-exit-downgrade")
    rejected = _run_alembic(url, "downgrade -1")
    if rejected.returncode == 0 or "schema_test_downgrade_forbidden" not in (
        rejected.stdout + rejected.stderr
    ):
        raise RuntimeError("exit_proof_downgrade_guard_failed")
    rejected_engine = _engine(url)
    try:
        head_after_rejected = _head(rejected_engine)
    finally:
        rejected_engine.dispose()
    acknowledged = _run_alembic(
        url,
        "downgrade -1",
        acknowledgement="I_ACKNOWLEDGE_EMPTY_SCHEMA_DESTRUCTION",
    )
    if acknowledged.returncode != 0:
        raise RuntimeError("exit_proof_downgrade_ack_failed")
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            tables = tuple(
                connection.execute(
                    text(
                        "SELECT tablename FROM pg_catalog.pg_tables "
                        "WHERE schemaname='public' "
                        "AND tablename <> 'alembic_version'"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()
    if tables:
        raise RuntimeError("exit_proof_downgrade_left_tables")
    return {
        "name": "test_only_downgrade_guard",
        "result": "pass",
        "observations": {
            "rejectedError": "schema_test_downgrade_forbidden",
            "headAfterRejected": head_after_rejected,
            "emptyAfterAcknowledged": True,
        },
    }


def _request(deployment_class: DeploymentClass) -> RebaselineRequest:
    return RebaselineRequest(
        deployment_class=deployment_class,
        acknowledgement=MAINTENANCE_ACKNOWLEDGEMENT,
        build_revision="schema-exit-proof",
    )


def _apply_and_code(engine, request: RebaselineRequest) -> str:  # noqa: ANN001
    with engine.connect() as connection:
        try:
            apply_rebaseline(connection, request)
        except RebaselineRefused as exc:
            return exc.safe_code
    return "accepted"


def _quote_identifier(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _source_state_digest(engine) -> str:  # noqa: ANN001
    """Hash the full source catalog and row set without recording row values."""
    with engine.connect() as connection:
        database_comment = connection.scalar(
            text(
                "SELECT shobj_description(oid, 'pg_database') "
                "FROM pg_database WHERE datname = current_database()"
            )
        )
        heads = tuple(
            str(item)
            for item in connection.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            ).scalars()
        )
        table_names = tuple(
            str(item)
            for item in connection.execute(
                text(
                    "SELECT tablename FROM pg_catalog.pg_tables "
                    "WHERE schemaname = 'public' ORDER BY tablename"
                )
            ).scalars()
        )
        rows: dict[str, list[str]] = {}
        for table_name in table_names:
            qualified = f'"public".{_quote_identifier(table_name)}'
            rows[table_name] = sorted(
                str(item)
                for item in connection.execute(
                    text(f"SELECT row_to_json(t)::text FROM {qualified} AS t")
                ).scalars()
            )
        catalog = structural_fingerprint(
            PostgresCatalogReader(connection).read_document()
        )
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "databaseComment": database_comment,
                "heads": heads,
                "catalog": catalog,
                "rows": rows,
            }
        )
    ).hexdigest()


def _assert_source_state_unchanged(
    engine,
    *,
    before: str,
) -> None:  # noqa: ANN001
    if _source_state_digest(engine) != before:
        raise RuntimeError("exit_proof_rebaseline_mutated_rejected_source")


def _assert_presquash_unchanged(engine, *, expected_head: str) -> None:  # noqa: ANN001
    """Compatibility helper retained for focused unit-like proof stages."""
    with engine.connect() as connection:
        head = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        legacy_table = connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' "
                "AND table_name='assistant_runtime_migration_item'"
            )
        ).first()
        identity_table = connection.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' "
                "AND table_name='mindatlas_schema_identity'"
            )
        ).first()
    if head != expected_head or legacy_table is None or identity_table is not None:
        raise RuntimeError("exit_proof_rebaseline_mutated_rejected_source")


_SAFE_STAGE_CODE = re.compile(r"[a-z0-9_]{1,96}\Z")


def _run_stage(label: str, callback):  # noqa: ANN001, ANN202
    """Run one proof stage and retain only a bounded diagnostic code."""
    try:
        return callback()
    except ExitProofFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        raw_code = getattr(exc, "safe_code", None) or str(exc)
        code = raw_code if isinstance(raw_code, str) else "unexpected"
        if _SAFE_STAGE_CODE.fullmatch(code) is None:
            code = "unexpected"
        raise ExitProofFailure(f"exit_proof_{label}_{code}") from None


_REBASELINE_REJECTION_SCENARIOS = (
    ("production", "production_rebaseline_forbidden"),
    ("missing_identity", "database_deployment_identity_missing"),
    ("unknown_identity", "database_deployment_identity_unknown"),
    ("mismatched_identity", "deployment_identity_mismatch"),
    ("missing_head", "pre_squash_head_mismatch"),
    ("multiple_heads", "pre_squash_head_mismatch"),
    ("legacy_business_row", "legacy_exclusion_data_present"),
    ("non_inert_legacy_control", "legacy_exclusion_data_present"),
    ("source_schema_drift", "pre_squash_fingerprint_mismatch"),
    ("extra_legacy_prefix_object", "pre_squash_fingerprint_mismatch"),
    ("data_invariant", "data_invariant_failed"),
    ("lock_contention", "rebaseline_lock_unavailable"),
    ("retained_snapshot_rollback", "retained_data_changed"),
)


def _install_presquash_source(engine, url: str) -> None:  # noqa: ANN001
    reset_disposable_public_schema(engine)
    install_pre_squash_fixture(url)
    _set_database_comment(engine, "rehearsal")


def _insert_legacy_business_row(engine) -> None:  # noqa: ANN001
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO assistant_runtime_migration_item "
                "(id, subject_kind, source_type, source_id, source_name, "
                "source_name_normalized, source_digest, evidence_json, "
                "source_revision, target_revision, attempt_count, state_revision, "
                "state, created_at, updated_at) VALUES "
                "(:id, 'skill', 'legacy', 'proof', '', '', :digest, '{}'::json, "
                "0, 0, 0, 0, 'discovered', NOW(), NOW())"
            ),
            {"id": "00000000-0000-0000-0000-000000000003", "digest": "a" * 64},
        )


def _rebaseline_report_path_rejected_before_database() -> bool:
    from scripts import rebaseline_pre_ga_v1 as rebaseline_script

    original_create_engine = rebaseline_script.create_engine
    original_environment = {
        name: os.environ.get(name)
        for name in (
            "MINDATLAS_DEPLOYMENT_CLASS",
            "APP_BUILD_REVISION",
            "MINDATLAS_EXIT_PROOF_DATABASE_URL",
        )
    }
    connected = False

    def unexpected_create_engine(*args, **kwargs):  # noqa: ANN001, ANN002
        nonlocal connected
        connected = True
        raise AssertionError("report-path rejection must precede database access")

    try:
        rebaseline_script.create_engine = unexpected_create_engine
        os.environ["MINDATLAS_DEPLOYMENT_CLASS"] = "development"
        os.environ["APP_BUILD_REVISION"] = "schema-exit-proof"
        os.environ["MINDATLAS_EXIT_PROOF_DATABASE_URL"] = "postgresql://unused"
        with tempfile.TemporaryDirectory() as directory:
            result = rebaseline_script.main(
                [
                    "inspect",
                    "--database-url-env",
                    "MINDATLAS_EXIT_PROOF_DATABASE_URL",
                    "--report-file",
                    str(Path(directory) / "missing-parent" / "report.json"),
                ]
            )
    finally:
        rebaseline_script.create_engine = original_create_engine
        for name, value in original_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    if result != 2 or connected:
        raise RuntimeError("exit_proof_report_path_guard_failed")
    return True


def _rebaseline_parser_has_no_bypass() -> bool:
    from scripts.rebaseline_pre_ga_v1 import build_parser

    help_text = build_parser().format_help()
    if "--force" in help_text or "--skip" in help_text:
        raise RuntimeError("exit_proof_rebaseline_parser_bypass_present")
    return True


def _rebaseline_matrix(url: str) -> dict[str, object]:
    engine = _engine(url)
    rejection_scenarios: list[dict[str, object]] = []
    try:
        _run_stage("source_fixture", lambda: _install_presquash_source(engine, url))
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO app_setting "
                    "(id, key, value_json, created_at, updated_at) "
                    "VALUES (:id, :key, '{}'::json, NOW(), NOW())"
                ),
                {
                    "id": "00000000-0000-0000-0000-000000000002",
                    "key": "schema-exit-retained",
                },
            )
        with engine.connect() as connection:
            rehearsal_report = apply_rebaseline(
                connection,
                _request(DeploymentClass.REHEARSAL),
            )
        if (
            rehearsal_report.before_revision != PRE_SQUASH_HEAD
            or rehearsal_report.after_revision != CLEAN_ROOT_REVISION
            or rehearsal_report.retained_data_unchanged is not True
        ):
            raise RuntimeError("exit_proof_rebaseline_success_failed")
        before_second_apply = _source_state_digest(engine)
        with engine.connect() as connection:
            idempotent_report = apply_rebaseline(
                connection,
                _request(DeploymentClass.REHEARSAL),
            )
        if idempotent_report.result != "already_rebaselined":
            raise RuntimeError("exit_proof_rebaseline_idempotence_failed")
        _assert_source_state_unchanged(engine, before=before_second_apply)

        _run_stage(
            "development_fixture", lambda: _install_presquash_source(engine, url)
        )
        _set_database_comment(engine, "development")
        with engine.connect() as connection:
            development_report = apply_rebaseline(
                connection,
                _request(DeploymentClass.DEVELOPMENT),
            )
        if (
            development_report.before_revision != PRE_SQUASH_HEAD
            or development_report.after_revision != CLEAN_ROOT_REVISION
        ):
            raise RuntimeError("exit_proof_development_rebaseline_failed")

        for name, expected_code in _REBASELINE_REJECTION_SCENARIOS:
            _run_stage(
                f"{name}_fixture",
                lambda: _install_presquash_source(engine, url),
            )
            request = _request(DeploymentClass.REHEARSAL)
            if name == "production":
                request = _request(DeploymentClass.PRODUCTION)
            elif name == "missing_identity":
                _set_database_comment(engine, None)
            elif name == "unknown_identity":
                _set_database_comment(engine, "shared")
            elif name == "mismatched_identity":
                _set_database_comment(engine, "development")
            elif name == "missing_head":
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM alembic_version"))
            elif name == "multiple_heads":
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "INSERT INTO alembic_version (version_num) "
                            "VALUES ('unreviewed_head')"
                        )
                    )
            elif name == "legacy_business_row":
                _insert_legacy_business_row(engine)
            elif name == "non_inert_legacy_control":
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE assistant_runtime_rollout_control "
                            "SET state_revision = 1"
                        )
                    )
            elif name == "source_schema_drift":
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "ALTER TABLE app_setting "
                            "ADD COLUMN schema_exit_drift integer"
                        )
                    )
            elif name == "extra_legacy_prefix_object":
                with engine.begin() as connection:
                    connection.execute(
                        text("CREATE TABLE legacy_exit_proof_unreviewed (id integer)")
                    )

            before_rejection = _source_state_digest(engine)
            if name == "lock_contention":
                holder = engine.connect()
                transaction = holder.begin()
                try:
                    holder.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"),
                        {"key": REBASELINE_ADVISORY_LOCK_KEY},
                    )
                    code = _apply_and_code(engine, request)
                finally:
                    transaction.rollback()
                    holder.close()
            elif name == "data_invariant":
                original_invariants = rebaseline_module.validate_data_invariants

                def rejected_invariant(connection):  # noqa: ANN001
                    raise RebaselineRefused("data_invariant_failed")

                try:
                    rebaseline_module.validate_data_invariants = rejected_invariant
                    code = _apply_and_code(engine, request)
                finally:
                    rebaseline_module.validate_data_invariants = original_invariants
            elif name == "retained_snapshot_rollback":
                original_snapshot = rebaseline_module.snapshot_retained_tables
                calls = 0

                def changed_snapshot(connection, ephemeral_key):  # noqa: ANN001
                    nonlocal calls
                    calls += 1
                    snapshot = original_snapshot(connection, ephemeral_key)
                    if calls == 2:
                        if not snapshot:
                            raise RuntimeError("exit_proof_snapshot_empty")
                        return (
                            *snapshot[:-1],
                            replace(
                                snapshot[-1],
                                row_count=snapshot[-1].row_count + 1,
                            ),
                        )
                    return snapshot

                try:
                    rebaseline_module.snapshot_retained_tables = changed_snapshot
                    code = _apply_and_code(engine, request)
                finally:
                    rebaseline_module.snapshot_retained_tables = original_snapshot
                if calls != 2:
                    raise RuntimeError("exit_proof_snapshot_rollback_not_reached")
            else:
                code = _apply_and_code(engine, request)
            if code != expected_code:
                raise RuntimeError("exit_proof_rebaseline_rejection_matrix_failed")
            _assert_source_state_unchanged(engine, before=before_rejection)
            rejection_scenarios.append(
                {
                    "name": name,
                    "error": code,
                    "sourceStateUnchanged": True,
                }
            )

        report_path_rejected = _rebaseline_report_path_rejected_before_database()
        parser_has_no_bypass = _rebaseline_parser_has_no_bypass()
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()
    upgrade_clean_root_checked(
        url,
        deployment_class="rehearsal",
        app_env="test",
        build_revision="schema-exit-proof",
    )
    return {
        "name": "guarded_rebaseline_matrix",
        "result": "pass",
        "observations": {
            "beforeHead": PRE_SQUASH_HEAD,
            "afterHead": CLEAN_ROOT_REVISION,
            "retainedDataUnchanged": True,
            "rehearsalSuccess": True,
            "developmentSuccess": True,
            "idempotentSecondApply": True,
            "snapshotRollback": True,
            "reportPathRejectedBeforeDatabase": report_path_rejected,
            "parserHasNoBypass": parser_has_no_bypass,
            "rejectionsNoMutation": True,
            "rejectionCodes": sorted(
                {code for _, code in _REBASELINE_REJECTION_SCENARIOS}
            ),
            "rejectionScenarios": rejection_scenarios,
        },
    }


def _compatibility_proofs(url: str) -> tuple[dict[str, object], dict[str, object]]:
    _upgrade_clean(url, build="schema-exit-compatibility")
    engine = _engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "DROP TRIGGER trg_mindatlas_schema_identity_guard "
                    "ON mindatlas_schema_identity"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE mindatlas_schema_identity "
                    "DROP CONSTRAINT ck_schema_identity_family"
                )
            )
            connection.execute(
                text(
                    "UPDATE mindatlas_schema_identity "
                    "SET schema_family='wrong_family'"
                )
            )
        with engine.connect() as connection:
            snapshot = runtime_schema_compatibility().evaluate(connection)
        if snapshot.safe_reason != "schema_incompatible":
            raise RuntimeError("exit_proof_wrong_family_failed")
        wrong_family = {
            "name": "wrong_family_rejected",
            "result": "pass",
            "observations": {
                "error": snapshot.safe_reason,
                "mutationBlocked": True,
            },
        }

        # The same fail-closed compatibility result is wired into the real
        # worker lease service; no queued run may be claimed while the marker
        # is drifted.
        insert_complete_main_agent_run(
            engine,
            status="queued",
            required_app_build_revision="schema-exit-compatibility",
        )
        from app.assistant.durable.leases import RunLeaseService
        from app.assistant.durable.worker_registry import WorkerIdentity

        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE mindatlas_schema_identity "
                    "SET schema_family='wrong_family'"
                )
            )

        class WorkerSchema:
            def is_compatible(self, db):  # noqa: ANN001
                return runtime_schema_compatibility().is_compatible(db)

        # The lease service only needs an identity and a SQLAlchemy session;
        # use the application worker identity contract directly.
        from sqlalchemy.orm import Session

        session = Session(bind=engine)
        try:
            worker = WorkerIdentity(
                worker_id="schema-exit-worker",
                app_build_revision="schema-exit-compatibility",
                runtime_contract_version=1,
                supported_checkpoint_codec_versions=(1, 2, 3),
                capability_feature_digest="e" * 64,
                hostname_label="schema-exit-proof",
            )
            claimed = RunLeaseService(
                session,
                identity=worker,
                lease_ttl=timedelta(seconds=30),
                schema_compatibility=WorkerSchema(),
            ).claim_next()
        finally:
            session.close()
        if claimed is not None:
            raise RuntimeError("exit_proof_worker_drift_claimed")
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, lease_owner, lease_expires_at "
                    "FROM assistant_chat_run "
                    "WHERE required_app_build_revision = :build"
                ),
                {"build": "schema-exit-compatibility"},
            ).one()
        if (
            row.status != "queued"
            or row.lease_owner is not None
            or row.lease_expires_at is not None
        ):
            raise RuntimeError("exit_proof_worker_drift_mutated")
        worker_drift = {
            "name": "worker_claim_rejected_on_drift",
            "result": "pass",
            "observations": {
                "error": "schema_incompatible",
                "mutationBlocked": True,
            },
        }
        return wrong_family, worker_drift
    finally:
        reset_disposable_public_schema(engine)
        engine.dispose()


def _build_revision_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def build_proof(
    *, fresh_url: str, downgrade_url: str, rebaseline_url: str, build_revision: str
) -> dict[str, object]:
    checks = [
        _run_stage(
            "fresh_upgrade",
            lambda: _fresh_upgrade(fresh_url, build=build_revision),
        ),
        _run_stage("downgrade_guard", lambda: _downgrade_guard(downgrade_url)),
        _run_stage("rebaseline_matrix", lambda: _rebaseline_matrix(rebaseline_url)),
    ]
    wrong_family, worker_drift = _run_stage(
        "compatibility",
        lambda: _compatibility_proofs(downgrade_url),
    )
    checks.extend((wrong_family, worker_drift))
    source = (BACKEND_ROOT.parent / "deploy" / "migrate.sh").read_text("utf-8")
    checks.append(
        {
            "name": "deploy_auto_stamp_absent",
            "result": "pass" if "alembic stamp" not in source else "fail",
            "observations": {"sourceContainsAutoStamp": "alembic stamp" in source},
        }
    )
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "deploymentClass": "rehearsal",
        "buildRevision": build_revision,
        "checks": checks,
    }
    payload["proofDigest"] = _build_revision_digest(payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--fresh-upgrade-database-url-env", required=True)
    parser.add_argument("--downgrade-database-url-env", required=True)
    parser.add_argument("--rebaseline-database-url-env", required=True)
    parser.add_argument("--build-revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        proof = build_proof(
            fresh_url=_read_url(args.fresh_upgrade_database_url_env),
            downgrade_url=_read_url(args.downgrade_database_url_env),
            rebaseline_url=_read_url(args.rebaseline_database_url_env),
            build_revision=args.build_revision,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_json_bytes(proof))
    except Exception as exc:  # noqa: BLE001
        print(getattr(exc, "safe_code", "exit_proof_failed"), file=sys.stderr)
        return 2
    print("schema_exit_proof_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

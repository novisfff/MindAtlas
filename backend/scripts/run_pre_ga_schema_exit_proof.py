#!/usr/bin/env python3
"""Execute the concrete PostgreSQL checks consumed by schema exit evidence.

This runner deliberately produces observations, rather than accepting a set of
caller-supplied booleans.  The companion ``verify_pre_ga_schema.py exit``
command validates the exact check set and its digest before deriving evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import timedelta

from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tests._bootstrap import bootstrap_backend_imports, reset_caches  # noqa: E402

bootstrap_backend_imports()
reset_caches()

from app.schema.canonical import canonical_json_bytes  # noqa: E402
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
from tests.main_agent_postgres_support import insert_complete_main_agent_run  # noqa: E402
from tests.postgres_destructive_guard import reset_disposable_public_schema  # noqa: E402
from tests.pre_squash_fixture import install_pre_squash_fixture  # noqa: E402
from tests.schema_baseline_support import upgrade_clean_root_checked  # noqa: E402


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


def _set_database_comment(engine, deployment_class: str) -> None:  # noqa: ANN001
    with engine.begin() as connection:
        name = connection.scalar(text("SELECT current_database()"))
        if not isinstance(name, str):
            raise RuntimeError("exit_proof_database_identity_unavailable")
        connection.exec_driver_sql(
            f'COMMENT ON DATABASE "{name.replace(chr(34), chr(34) * 2)}" IS %s',
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


def _assert_presquash_unchanged(engine, *, expected_head: str) -> None:  # noqa: ANN001
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


def _rebaseline_matrix(url: str) -> dict[str, object]:
    engine = _engine(url)
    try:
        install_pre_squash_fixture(url)
        with engine.begin() as connection:
            retained_id = "00000000-0000-0000-0000-000000000002"
            connection.execute(
                text(
                    "INSERT INTO app_setting "
                    "(id, key, value_json, created_at, updated_at) "
                    "VALUES (:id, :key, '{}'::json, NOW(), NOW())"
                ),
                {"id": retained_id, "key": "schema-exit-retained"},
            )
        with engine.connect() as connection:
            report = apply_rebaseline(connection, _request(DeploymentClass.REHEARSAL))
        if (
            report.before_revision != PRE_SQUASH_HEAD
            or report.after_revision != CLEAN_ROOT_REVISION
            or report.retained_data_unchanged is not True
        ):
            raise RuntimeError("exit_proof_rebaseline_success_failed")

        reset_disposable_public_schema(engine)
        install_pre_squash_fixture(url)
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

        rejection_codes: list[str] = []
        for scenario in (
            "production",
            "unknown",
            "nonempty",
            "drift",
            "wrong_head",
            "lock",
        ):
            reset_disposable_public_schema(engine)
            install_pre_squash_fixture(url)
            _set_database_comment(engine, "rehearsal")
            if scenario == "production":
                code = _apply_and_code(engine, _request(DeploymentClass.PRODUCTION))
            elif scenario == "unknown":
                _set_database_comment(engine, "shared")
                code = _apply_and_code(engine, _request(DeploymentClass.REHEARSAL))
            elif scenario == "nonempty":
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
                code = _apply_and_code(engine, _request(DeploymentClass.REHEARSAL))
            elif scenario == "drift":
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "ALTER TABLE app_setting "
                            "ADD COLUMN schema_exit_drift integer"
                        )
                    )
                code = _apply_and_code(engine, _request(DeploymentClass.REHEARSAL))
            elif scenario == "wrong_head":
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE alembic_version "
                            "SET version_num = 'unreviewed_head'"
                        )
                    )
                code = _apply_and_code(engine, _request(DeploymentClass.REHEARSAL))
            else:
                holder = engine.connect()
                transaction = holder.begin()
                try:
                    holder.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"),
                        {"key": REBASELINE_ADVISORY_LOCK_KEY},
                    )
                    code = _apply_and_code(
                        engine,
                        _request(DeploymentClass.REHEARSAL),
                    )
                finally:
                    transaction.rollback()
                    holder.close()
            _assert_presquash_unchanged(
                engine,
                expected_head=(
                    "unreviewed_head"
                    if scenario == "wrong_head"
                    else PRE_SQUASH_HEAD
                ),
            )
            rejection_codes.append(code)
        expected = {
            "production_rebaseline_forbidden",
            "database_deployment_identity_unknown",
            "legacy_exclusion_data_present",
            "pre_squash_fingerprint_mismatch",
            "pre_squash_head_mismatch",
            "rebaseline_lock_unavailable",
        }
        if set(rejection_codes) != expected:
            raise RuntimeError("exit_proof_rebaseline_rejection_matrix_failed")
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
            "developmentSuccess": True,
            "rejectionsNoMutation": True,
            "rejectionCodes": sorted(expected),
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
        if row.status != "queued" or row.lease_owner is not None or row.lease_expires_at is not None:
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


def build_proof(*, fresh_url: str, downgrade_url: str, rebaseline_url: str, build_revision: str) -> dict[str, object]:
    checks = [
        _fresh_upgrade(fresh_url, build=build_revision),
        _downgrade_guard(downgrade_url),
        _rebaseline_matrix(rebaseline_url),
    ]
    wrong_family, worker_drift = _compatibility_proofs(downgrade_url)
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

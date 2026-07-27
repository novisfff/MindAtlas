"""Unit + contract tests for Plan 10 rollout evidence (Task 1).

SQLite-friendly repository logic for assignment immutability and control pointer.
PostgreSQL immutability triggers are covered in
test_ai_runtime_migration_repository_postgres.py when URL is set.
"""

from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64


def _sqlite_session():
    from app.database import Base
    from app.assistant.migration import models as migration_models  # noqa: F401

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    # Only create migration evidence tables (avoid full app graph FKs).
    tables = [
        migration_models.AssistantRuntimeMigrationItem.__table__,
        migration_models.AssistantRuntimeMigrationEvent.__table__,
        migration_models.AssistantRuntimeMigrationBatch.__table__,
        migration_models.AssistantRuntimeRolloutRevision.__table__,
        migration_models.AssistantRuntimeRolloutControl.__table__,
        migration_models.AssistantRuntimeRolloutEvent.__table__,
        migration_models.AssistantRuntimeRolloutAssignment.__table__,
        migration_models.AssistantLegacyApprovalArchive.__table__,
        migration_models.AssistantRuntimeCleanupGate.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(bind=engine, autoflush=True, autocommit=False, future=True)
    return factory(), engine


class RolloutRevisionAndControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.engine = _sqlite_session()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_prepare_revision_is_idempotent_for_same_label_and_digest(self) -> None:
        from app.assistant.migration.repository import RuntimeMigrationRepository

        repo = RuntimeMigrationRepository(self.session)
        rev1 = repo.prepare_rollout_revision(
            revision_label="legacy-default-v1",
            runtime_mode="legacy",
            eligible_closure_digest=_DIGEST_A,
            build_revision="development",
            cohort_salt_fingerprint=_DIGEST_B,
            actor_principal="op-1",
            reason="task1 default",
        )
        rev2 = repo.prepare_rollout_revision(
            revision_label="legacy-default-v1",
            runtime_mode="legacy",
            eligible_closure_digest=_DIGEST_A,
            build_revision="development",
            cohort_salt_fingerprint=_DIGEST_B,
            actor_principal="op-1",
            reason="task1 default",
        )
        self.assertEqual(rev1.id, rev2.id)
        self.assertEqual(rev1.config_digest, rev2.config_digest)
        self.session.commit()

    def test_prepare_revision_conflicts_on_label_digest_drift(self) -> None:
        from app.assistant.migration.repository import (
            CODE_CONFLICT,
            RuntimeMigrationRepository,
            RuntimeMigrationRepositoryError,
        )

        repo = RuntimeMigrationRepository(self.session)
        repo.prepare_rollout_revision(
            revision_label="legacy-default-v1",
            runtime_mode="legacy",
            eligible_closure_digest=_DIGEST_A,
            build_revision="development",
            cohort_salt_fingerprint=_DIGEST_B,
        )
        with self.assertRaises(RuntimeMigrationRepositoryError) as ctx:
            repo.prepare_rollout_revision(
                revision_label="legacy-default-v1",
                runtime_mode="legacy",
                shadow_percent=1,
                eligible_closure_digest=_DIGEST_A,
                build_revision="development",
                cohort_salt_fingerprint=_DIGEST_B,
            )
        self.assertEqual(ctx.exception.code, CODE_CONFLICT)

    def test_plan04_compat_rejects_nonzero_canary(self) -> None:
        from app.assistant.migration.repository import (
            CODE_INVALID_INPUT,
            RuntimeMigrationRepository,
            RuntimeMigrationRepositoryError,
        )

        repo = RuntimeMigrationRepository(self.session)
        with self.assertRaises(RuntimeMigrationRepositoryError) as ctx:
            repo.prepare_rollout_revision(
                revision_label="compat-shadow",
                runtime_mode="legacy",
                config_origin="plan04_compat",
                shadow_percent=5,
                eligible_closure_digest=_DIGEST_A,
                build_revision="development",
                cohort_salt_fingerprint=_DIGEST_B,
            )
        self.assertEqual(ctx.exception.code, CODE_INVALID_INPUT)

    def test_activate_advances_singleton_control_pointer(self) -> None:
        from app.assistant.migration.repository import RuntimeMigrationRepository

        repo = RuntimeMigrationRepository(self.session)
        control = repo.ensure_rollout_control()
        self.assertEqual(int(control.state_revision), 0)
        self.assertIsNone(control.active_rollout_revision_id)

        rev = repo.prepare_rollout_revision(
            revision_label="legacy-default-v1",
            runtime_mode="legacy",
            eligible_closure_digest=_DIGEST_A,
            build_revision="development",
            cohort_salt_fingerprint=_DIGEST_B,
        )
        control = repo.activate_rollout_revision(
            rollout_revision_id=rev.id,
            expected_control_revision=0,
            actor_principal="op-1",
            reason="activate default",
        )
        self.assertEqual(control.active_rollout_revision_id, rev.id)
        self.assertGreaterEqual(int(control.state_revision), 1)

        active = repo.get_active_rollout_revision()
        assert active is not None
        self.assertEqual(active.id, rev.id)

        # Stale expected control revision fails.
        from app.assistant.migration.repository import (
            CODE_STALE_REVISION,
            RuntimeMigrationRepositoryError,
        )

        rev2 = repo.prepare_rollout_revision(
            revision_label="legacy-default-v2",
            runtime_mode="legacy",
            eligible_closure_digest=_DIGEST_C,
            build_revision="development",
            cohort_salt_fingerprint=_DIGEST_B,
        )
        with self.assertRaises(RuntimeMigrationRepositoryError) as ctx:
            repo.activate_rollout_revision(
                rollout_revision_id=rev2.id,
                expected_control_revision=0,
            )
        self.assertEqual(ctx.exception.code, CODE_STALE_REVISION)

        control2 = repo.activate_rollout_revision(
            rollout_revision_id=rev2.id,
            expected_control_revision=int(control.state_revision),
        )
        self.assertEqual(control2.active_rollout_revision_id, rev2.id)
        self.session.commit()

    def test_assignment_immutable_for_scope_and_revision(self) -> None:
        from app.assistant.migration.repository import (
            CODE_IMMUTABLE,
            RuntimeMigrationRepository,
            RuntimeMigrationRepositoryError,
        )

        repo = RuntimeMigrationRepository(self.session)
        rev = repo.prepare_rollout_revision(
            revision_label="legacy-default-v1",
            runtime_mode="legacy",
            eligible_closure_digest=_DIGEST_A,
            build_revision="development",
            cohort_salt_fingerprint=_DIGEST_B,
        )
        conv = uuid.uuid4()
        a1 = repo.create_assignment(
            conversation_id=conv,
            rollout_revision_id=rev.id,
            assigned_runtime_kind="legacy",
            assignment_reason="hash",
            cohort_key_digest=_DIGEST_C,
        )
        a2 = repo.create_assignment(
            conversation_id=conv,
            rollout_revision_id=rev.id,
            assigned_runtime_kind="legacy",
            assignment_reason="hash",
            cohort_key_digest=_DIGEST_C,
        )
        self.assertEqual(a1.id, a2.id)

        with self.assertRaises(RuntimeMigrationRepositoryError) as ctx:
            repo.create_assignment(
                conversation_id=conv,
                rollout_revision_id=rev.id,
                assigned_runtime_kind="main_agent",
                assignment_reason="hash",
                cohort_key_digest=_DIGEST_C,
            )
        self.assertEqual(ctx.exception.code, CODE_IMMUTABLE)
        self.session.commit()

    def test_cleanup_gate_append_only_and_safe_counts(self) -> None:
        from app.assistant.migration.repository import (
            CODE_INVALID_INPUT,
            RuntimeMigrationRepository,
            RuntimeMigrationRepositoryError,
        )

        repo = RuntimeMigrationRepository(self.session)
        gate = repo.append_cleanup_gate(
            gate_kind="deploy_b1",
            decision="failed",
            schema_revision="6417df0243be",
            build_revision="development",
            inventory_digest=_DIGEST_A,
            evidence_digest=_DIGEST_B,
            snapshot_counts={"nonterminalRuns": 0, "blockers": 3},
            actor_principal="op-1",
            reason="not ready",
        )
        self.assertEqual(gate.decision, "failed")
        with self.assertRaises(RuntimeMigrationRepositoryError) as ctx:
            repo.append_cleanup_gate(
                gate_kind="deploy_b1",
                decision="failed",
                schema_revision="6417df0243be",
                build_revision="development",
                inventory_digest=_DIGEST_A,
                evidence_digest=_DIGEST_B,
                snapshot_counts={"system_prompt": "LEAK"},
            )
        self.assertEqual(ctx.exception.code, CODE_INVALID_INPUT)


class MigrationItemAndBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.engine = _sqlite_session()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_discovered_upsert_and_digest_drift_blocks(self) -> None:
        from app.assistant.migration.repository import RuntimeMigrationRepository

        repo = RuntimeMigrationRepository(self.session)
        item, outcome = repo.upsert_discovered_item(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id="skill-1",
            source_name="quick_stats",
            source_name_normalized="quick_stats",
            source_digest=_DIGEST_A,
            actor_principal="op-1",
            build_revision="development",
        )
        self.assertEqual(outcome, "created")
        self.assertEqual(item.state, "discovered")
        events = repo.list_item_events(item.id)
        self.assertEqual(len(events), 1)

        item2, outcome2 = repo.upsert_discovered_item(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id="skill-1",
            source_name="quick_stats",
            source_name_normalized="quick_stats",
            source_digest=_DIGEST_A,
        )
        self.assertEqual(outcome2, "unchanged")
        self.assertEqual(item2.id, item.id)

        item3, outcome3 = repo.upsert_discovered_item(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id="skill-1",
            source_name="quick_stats",
            source_name_normalized="quick_stats",
            source_digest=_DIGEST_B,
        )
        self.assertEqual(outcome3, "drifted")
        self.assertEqual(item3.state, "blocked")
        self.assertEqual(item3.reason_code, "source_digest_drift")
        self.assertEqual(len(repo.list_item_events(item.id)), 2)

    def test_item_transition_requires_expected_revision(self) -> None:
        from app.assistant.migration.repository import (
            CODE_FORBIDDEN_TRANSITION,
            CODE_STALE_REVISION,
            RuntimeMigrationRepository,
            RuntimeMigrationRepositoryError,
        )

        repo = RuntimeMigrationRepository(self.session)
        item, _ = repo.upsert_discovered_item(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id="skill-2",
            source_name="x",
            source_name_normalized="x",
            source_digest=_DIGEST_A,
        )
        mapped = repo.transition_item(
            item_id=item.id,
            expected_revision=int(item.state_revision),
            to_state="mapped",
            target_type="package",
            target_id="pkg-1",
            target_digest=_DIGEST_C,
        )
        self.assertEqual(mapped.state, "mapped")
        with self.assertRaises(RuntimeMigrationRepositoryError) as ctx:
            repo.transition_item(
                item_id=item.id,
                expected_revision=0,
                to_state="migrated",
            )
        self.assertEqual(ctx.exception.code, CODE_STALE_REVISION)
        with self.assertRaises(RuntimeMigrationRepositoryError) as ctx2:
            repo.transition_item(
                item_id=item.id,
                expected_revision=int(mapped.state_revision),
                to_state="verified",  # must go migrated first
            )
        self.assertEqual(ctx2.exception.code, CODE_FORBIDDEN_TRANSITION)

    def test_batch_request_id_idempotent_and_resume_drift(self) -> None:
        from app.assistant.migration.repository import (
            CODE_CONFLICT,
            CODE_DRIFT,
            RuntimeMigrationRepository,
            RuntimeMigrationRepositoryError,
        )

        repo = RuntimeMigrationRepository(self.session)
        batch = repo.prepare_batch(
            command_kind="inventory",
            source_snapshot_digest=_DIGEST_A,
            configuration_digest=_DIGEST_B,
            build_revision="development",
            schema_revision="6417df0243be",
            environment="test",
            database_fingerprint="fp-1",
            request_id="req-1",
            batch_size=50,
        )
        same = repo.prepare_batch(
            command_kind="inventory",
            source_snapshot_digest=_DIGEST_A,
            configuration_digest=_DIGEST_B,
            build_revision="development",
            schema_revision="6417df0243be",
            environment="test",
            database_fingerprint="fp-1",
            request_id="req-1",
            batch_size=50,
        )
        self.assertEqual(batch.id, same.id)
        with self.assertRaises(RuntimeMigrationRepositoryError) as ctx:
            repo.prepare_batch(
                command_kind="inventory",
                source_snapshot_digest=_DIGEST_C,
                configuration_digest=_DIGEST_B,
                build_revision="development",
                schema_revision="6417df0243be",
                environment="test",
                database_fingerprint="fp-1",
                request_id="req-1",
            )
        self.assertEqual(ctx.exception.code, CODE_CONFLICT)

        with self.assertRaises(RuntimeMigrationRepositoryError) as ctx2:
            repo.resume_batch(
                batch_id=batch.id,
                expected_revision=0,
                source_snapshot_digest=_DIGEST_C,
                configuration_digest=_DIGEST_B,
                build_revision="development",
                schema_revision="6417df0243be",
            )
        self.assertEqual(ctx2.exception.code, CODE_DRIFT)

    def test_discovery_backfill_dry_run_and_apply(self) -> None:
        from app.assistant.migration.discovery import backfill_discovered_from_records
        from app.assistant.migration.repository import RuntimeMigrationRepository

        fixture = Path(__file__).resolve().parents[0] / "fixtures" / "ai_runtime_migration" / "sanitized_skill_records.json"
        import json

        records = json.loads(fixture.read_text(encoding="utf-8"))
        dry = backfill_discovered_from_records(
            self.session,
            records,
            request_id="dry-1",
            dry_run=True,
            batch_size=100,
        )
        self.assertGreater(dry.created, 0)
        self.assertIsNone(dry.batch_id)
        # dry-run does not persist
        repo = RuntimeMigrationRepository(self.session)
        self.assertIsNone(
            repo.get_item_by_source(
                subject_kind="skill",
                source_type="legacy_skill",
                source_id="11111111-1111-4111-8111-111111111111",
            )
        )

        applied = backfill_discovered_from_records(
            self.session,
            records,
            request_id="apply-1",
            actor_principal="op-1",
            dry_run=False,
            batch_size=100,
        )
        self.session.commit()
        self.assertGreater(applied.created, 0)
        self.assertIsNotNone(applied.batch_id)
        item = repo.get_item_by_source(
            subject_kind="skill",
            source_type="legacy_skill",
            source_id="11111111-1111-4111-8111-111111111111",
        )
        self.assertIsNotNone(item)

        # Idempotent re-apply
        again = backfill_discovered_from_records(
            self.session,
            records,
            request_id="apply-2",
            actor_principal="op-1",
            dry_run=False,
            batch_size=100,
        )
        self.session.commit()
        self.assertEqual(again.created, 0)
        self.assertGreater(again.unchanged, 0)


class CliPrepareApplyTests(unittest.TestCase):
    def test_cli_prepare_dry_run_exit_0(self) -> None:
        import tempfile
        from app.assistant.migration.cli import main

        fixture = (
            Path(__file__).resolve().parents[0]
            / "fixtures"
            / "ai_runtime_migration"
            / "sanitized_skill_records.json"
        )
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            code = main(
                [
                    "inventory",
                    "prepare",
                    "--environment",
                    "test",
                    "--database-fingerprint",
                    "fp",
                    "--source-snapshot-digest",
                    "0" * 64,
                    "--expected-schema-head",
                    "6417df0243be",
                    "--expected-build-revision",
                    "development",
                    "--request-id",
                    "cli-prep-1",
                    "--batch-size",
                    "50",
                    "--dry-run",
                    "--report-json",
                    str(report),
                    "--fixture-json",
                    str(fixture),
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(report.exists())

    def test_cli_apply_fail_closed_without_operator(self) -> None:
        import tempfile
        from app.assistant.migration.cli import main

        fixture = (
            Path(__file__).resolve().parents[0]
            / "fixtures"
            / "ai_runtime_migration"
            / "sanitized_skill_records.json"
        )
        os.environ.pop("MINDATLAS_MIGRATION_OPERATOR_PRINCIPAL", None)
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            code = main(
                [
                    "inventory",
                    "apply",
                    "--environment",
                    "test",
                    "--database-fingerprint",
                    "fp",
                    "--source-snapshot-digest",
                    "a" * 64,
                    "--expected-schema-head",
                    "6417df0243be",
                    "--expected-build-revision",
                    "development",
                    "--request-id",
                    "cli-apply-1",
                    "--batch-size",
                    "50",
                    "--apply",
                    "--report-json",
                    str(report),
                    "--fixture-json",
                    str(fixture),
                    "--prepared-batch-id",
                    str(uuid.uuid4()),
                    "--prepared-batch-digest",
                    "b" * 64,
                ]
            )
            self.assertEqual(code, 3)

    def test_cli_apply_rejects_wildcard_source_digest(self) -> None:
        import tempfile
        from app.assistant.migration.cli import main

        fixture = (
            Path(__file__).resolve().parents[0]
            / "fixtures"
            / "ai_runtime_migration"
            / "sanitized_skill_records.json"
        )
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            code = main(
                [
                    "inventory",
                    "apply",
                    "--environment",
                    "test",
                    "--database-fingerprint",
                    "fp",
                    "--source-snapshot-digest",
                    "discover",
                    "--expected-schema-head",
                    "6417df0243be",
                    "--expected-build-revision",
                    "development",
                    "--request-id",
                    "cli-apply-wildcard",
                    "--batch-size",
                    "50",
                    "--apply",
                    "--operator-principal",
                    "op-1",
                    "--report-json",
                    str(report),
                    "--fixture-json",
                    str(fixture),
                    "--prepared-batch-id",
                    str(uuid.uuid4()),
                    "--prepared-batch-digest",
                    "c" * 64,
                ]
            )
            self.assertEqual(code, 4)

    def test_cli_apply_defaults_session_factory_past_db_gate(self) -> None:
        """I1: apply without injected session_factory defaults to SessionLocal.

        Use a thin factory that records defaulting and returns real sqlite
        sessions so the path can pass db_session_required_for_evidence_write.
        """
        import tempfile
        from unittest.mock import patch

        from sqlalchemy.orm import sessionmaker

        from app.assistant.migration.cli import main
        from app.assistant.migration.discovery import backfill_discovered_from_snapshot
        from app.assistant.migration.inventory import scan_inventory_from_records
        from app.assistant.migration.repository import RuntimeMigrationRepository

        fixture = (
            Path(__file__).resolve().parents[0]
            / "fixtures"
            / "ai_runtime_migration"
            / "sanitized_skill_records.json"
        )
        import json

        records = json.loads(fixture.read_text(encoding="utf-8"))
        # Bind env fields the same way the CLI does so snapshot digest matches.
        bound = {
            **records,
            "environment": "test",
            "database_fingerprint": "fp",
            "schema_head": "6417df0243be",
            "build_revision": "development",
        }
        snapshot = scan_inventory_from_records(bound)

        setup_session, engine = _sqlite_session()
        factory = sessionmaker(
            bind=engine, autoflush=True, autocommit=False, future=True
        )
        calls: list[str] = []

        def recording_session_local():
            calls.append("defaulted")
            return factory()

        # Prepare a durable prepared batch for apply binding.
        prepared = backfill_discovered_from_snapshot(
            setup_session,
            snapshot,
            request_id="cli-prep-default-sf",
            actor_principal="op-1",
            dry_run=True,
            prepare_only=True,
            batch_size=50,
        )
        setup_session.commit()
        prepared_batch_id = prepared.batch_id
        prepared_digest = prepared.report_digest
        setup_session.close()
        self.assertIsNotNone(prepared_batch_id)

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            with patch(
                "app.database.SessionLocal",
                side_effect=recording_session_local,
            ):
                code = main(
                    [
                        "inventory",
                        "apply",
                        "--environment",
                        "test",
                        "--database-fingerprint",
                        "fp",
                        "--source-snapshot-digest",
                        snapshot.snapshot_digest,
                        "--expected-schema-head",
                        "6417df0243be",
                        "--expected-build-revision",
                        "development",
                        "--request-id",
                        "cli-apply-default-sf",
                        "--batch-size",
                        "50",
                        "--apply",
                        "--operator-principal",
                        "op-1",
                        "--report-json",
                        str(report),
                        "--fixture-json",
                        str(fixture),
                        "--prepared-batch-id",
                        str(prepared_batch_id),
                        "--prepared-batch-digest",
                        prepared_digest,
                    ],
                    # Intentionally no session_factory — must default.
                )
            self.assertNotEqual(
                code,
                3,
                "must not fail db_session_required_for_evidence_write",
            )
            # Either completed (0/2) after writing; default factory was used.
            self.assertIn("defaulted", calls)
            self.assertIn(code, {0, 2})
            verify = factory()
            try:
                repo = RuntimeMigrationRepository(verify)
                batch = repo.get_batch(prepared_batch_id)
                self.assertIsNotNone(batch)
                self.assertEqual(str(batch.status), "completed")
            finally:
                verify.close()
        engine.dispose()

    def test_cli_resume_completes_same_batch_despite_new_request_id(self) -> None:
        """I2: resume continues batch A; a different CLI --request-id does not open B."""
        import tempfile

        from sqlalchemy import select
        from sqlalchemy.orm import sessionmaker

        from app.assistant.domain.digests import sha256_canonical_json
        from app.assistant.migration.cli import main
        from app.assistant.migration.inventory import scan_inventory_from_records
        from app.assistant.migration.models import AssistantRuntimeMigrationBatch
        from app.assistant.migration.repository import RuntimeMigrationRepository

        fixture = (
            Path(__file__).resolve().parents[0]
            / "fixtures"
            / "ai_runtime_migration"
            / "sanitized_skill_records.json"
        )
        import json

        records = json.loads(fixture.read_text(encoding="utf-8"))
        bound = {
            **records,
            "environment": "test",
            "database_fingerprint": "fp",
            "schema_head": "6417df0243be",
            "build_revision": "development",
        }
        snapshot = scan_inventory_from_records(bound)
        setup_session, engine = _sqlite_session()
        factory = sessionmaker(
            bind=engine, autoflush=True, autocommit=False, future=True
        )

        # Start batch A as prepared → running without completing (simulate interrupt).
        repo = RuntimeMigrationRepository(setup_session)
        config_digest = sha256_canonical_json(
            {
                "command": "inventory.backfill",
                "batchSize": 50,
                "snapshotDigest": snapshot.snapshot_digest,
            }
        )
        batch_a = repo.prepare_batch(
            command_kind="inventory",
            source_snapshot_digest=snapshot.snapshot_digest,
            configuration_digest=config_digest,
            build_revision=snapshot.build_revision,
            schema_revision=snapshot.schema_head,
            environment=snapshot.environment,
            database_fingerprint=snapshot.database_fingerprint,
            request_id="batch-a-request",
            batch_size=50,
            started_by="op-1",
        )
        batch_a = repo.transition_batch(
            batch_id=batch_a.id,
            expected_revision=int(batch_a.state_revision),
            to_status="running",
        )
        setup_session.commit()
        batch_a_id = batch_a.id
        revision = int(batch_a.state_revision)
        setup_session.close()

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "resume.json"
            code = main(
                [
                    "inventory",
                    "resume",
                    "--environment",
                    "test",
                    "--database-fingerprint",
                    "fp",
                    "--source-snapshot-digest",
                    snapshot.snapshot_digest,
                    "--expected-schema-head",
                    "6417df0243be",
                    "--expected-build-revision",
                    "development",
                    # Different CLI request-id must not open batch B.
                    "--request-id",
                    "cli-resume-different-request-id",
                    "--batch-size",
                    "50",
                    "--apply",
                    "--operator-principal",
                    "op-1",
                    "--report-json",
                    str(report),
                    "--fixture-json",
                    str(fixture),
                    "--batch-id",
                    str(batch_a_id),
                    "--expected-state-revision",
                    str(revision),
                ],
                session_factory=factory,
            )
            self.assertIn(code, {0, 2})
            report_payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(report_payload["batchId"], str(batch_a_id))

            verify = factory()
            try:
                vrepo = RuntimeMigrationRepository(verify)
                completed = vrepo.get_batch(batch_a_id)
                self.assertIsNotNone(completed)
                self.assertEqual(str(completed.status), "completed")
                self.assertEqual(str(completed.request_id), "batch-a-request")

                # No second batch was created for the CLI request id.
                other = vrepo.get_batch_by_request_id(
                    "cli-resume-different-request-id"
                )
                self.assertIsNone(other)
                all_batches = verify.execute(
                    select(AssistantRuntimeMigrationBatch)
                ).scalars().all()
                self.assertEqual(len(all_batches), 1)
            finally:
                verify.close()
        engine.dispose()

    def test_cli_apply_rejects_wrong_prepared_digest(self) -> None:
        import tempfile

        from sqlalchemy.orm import sessionmaker

        from app.assistant.migration.cli import main
        from app.assistant.migration.discovery import backfill_discovered_from_snapshot
        from app.assistant.migration.inventory import scan_inventory_from_records

        fixture = (
            Path(__file__).resolve().parents[0]
            / "fixtures"
            / "ai_runtime_migration"
            / "sanitized_skill_records.json"
        )
        import json

        records = json.loads(fixture.read_text(encoding="utf-8"))
        bound = {
            **records,
            "environment": "test",
            "database_fingerprint": "fp",
            "schema_head": "6417df0243be",
            "build_revision": "development",
        }
        snapshot = scan_inventory_from_records(bound)
        setup_session, engine = _sqlite_session()
        factory = sessionmaker(
            bind=engine, autoflush=True, autocommit=False, future=True
        )
        prepared = backfill_discovered_from_snapshot(
            setup_session,
            snapshot,
            request_id="prep-wrong-digest",
            actor_principal="op-1",
            dry_run=True,
            prepare_only=True,
            batch_size=50,
        )
        setup_session.commit()
        prepared_batch_id = prepared.batch_id
        setup_session.close()
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "report.json"
            code = main(
                [
                    "inventory",
                    "apply",
                    "--environment",
                    "test",
                    "--database-fingerprint",
                    "fp",
                    "--source-snapshot-digest",
                    snapshot.snapshot_digest,
                    "--expected-schema-head",
                    "6417df0243be",
                    "--expected-build-revision",
                    "development",
                    "--request-id",
                    "apply-wrong-digest",
                    "--batch-size",
                    "50",
                    "--apply",
                    "--operator-principal",
                    "op-1",
                    "--report-json",
                    str(report),
                    "--fixture-json",
                    str(fixture),
                    "--prepared-batch-id",
                    str(prepared_batch_id),
                    "--prepared-batch-digest",
                    "f" * 64,
                ],
                session_factory=factory,
            )
            self.assertEqual(code, 4)
        engine.dispose()

    def test_runtime_mode_config_defaults_legacy(self) -> None:
        from app.config import Settings

        s = Settings()
        self.assertEqual(s.assistant_runtime_mode, "legacy")
        self.assertEqual(s.assistant_runtime_rollout_revision, "")

    def test_removed_main_agent_mode_env_is_rejected(self) -> None:
        from pydantic import ValidationError

        from app.config import Settings

        with patch.dict(
            os.environ,
            {"ASSISTANT_MAIN_AGENT_MODE": "read_only"},
            clear=True,
        ):
            with self.assertRaises(ValidationError) as ctx:
                Settings(_env_file=None)
        self.assertIn("ASSISTANT_MAIN_AGENT_MODE has been removed", str(ctx.exception))



class DeterministicAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.engine = _sqlite_session()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_bucket_is_stable_for_scope_and_revision(self) -> None:
        from app.assistant.migration.rollout import compute_cohort_bucket, prepare_revision

        rev = prepare_revision(
            self.session,
            revision_label="assign-stable-v1",
            runtime_mode="main_agent",
            eligible_closure_digest=_DIGEST_A,
            build_revision="development",
            read_canary_percent=100,
            cohort_salt="test-salt-1",
        )
        conv = uuid.uuid4()
        b1, d1 = compute_cohort_bucket(
            conversation_id=conv,
            rollout_revision_id=rev.id,
            salt="test-salt-1",
        )
        b2, d2 = compute_cohort_bucket(
            conversation_id=conv,
            rollout_revision_id=rev.id,
            salt="test-salt-1",
        )
        self.assertEqual(b1, b2)
        self.assertEqual(d1, d2)
        self.assertGreaterEqual(b1, 0)
        self.assertLess(b1, 100)

    def test_assignment_main_at_100_percent(self) -> None:
        from app.assistant.migration.rollout import ensure_assignment, prepare_revision

        rev = prepare_revision(
            self.session,
            revision_label="main-100-v1",
            runtime_mode="main_agent",
            eligible_closure_digest=_DIGEST_A,
            build_revision="development",
            read_canary_percent=100,
            cohort_salt="salt-a",
        )
        result = ensure_assignment(
            self.session,
            conversation_id=uuid.uuid4(),
            revision=rev,
            salt="salt-a",
        )
        self.assertEqual(result.assigned_runtime_kind, "main_agent")
        self.assertEqual(result.assignment_reason, "hash")
        # Idempotent
        again = ensure_assignment(
            self.session,
            conversation_id=result.assignment.conversation_id,
            revision=rev,
            salt="salt-a",
        )
        self.assertEqual(again.assignment.id, result.assignment.id)

    def test_assignment_legacy_at_zero_percent(self) -> None:
        from app.assistant.migration.rollout import ensure_assignment, prepare_revision

        rev = prepare_revision(
            self.session,
            revision_label="main-0-v1",
            runtime_mode="main_agent",
            eligible_closure_digest=_DIGEST_A,
            build_revision="development",
            read_canary_percent=0,
            cohort_salt="salt-b",
        )
        # prepare_revision auto-bumps main_agent 0 → 100 for local/dev; force 0 via repo
        from app.assistant.migration.repository import RuntimeMigrationRepository

        rev2 = RuntimeMigrationRepository(self.session).prepare_rollout_revision(
            revision_label="main-0-forced",
            runtime_mode="main_agent",
            read_canary_percent=0,
            eligible_closure_digest=_DIGEST_C,
            build_revision="development",
            cohort_salt_fingerprint=_DIGEST_B,
        )
        result = ensure_assignment(
            self.session,
            conversation_id=uuid.uuid4(),
            revision=rev2,
            salt="salt-b",
        )
        self.assertEqual(result.assigned_runtime_kind, "legacy")

    def test_startup_config_must_match_active_durable_revision(self) -> None:
        from app.assistant.migration.rollout import (
            RolloutError,
            activate_revision,
            prepare_revision,
            validate_runtime_rollout_startup,
        )

        revision = prepare_revision(
            self.session,
            revision_label="startup-main-v1",
            runtime_mode="main_agent",
            eligible_closure_digest=_DIGEST_A,
            build_revision="development",
            read_canary_percent=100,
        )
        activate_revision(
            self.session,
            rollout_revision_id=revision.id,
            expected_control_revision=0,
        )
        settings = SimpleNamespace(
            assistant_runtime_mode="main_agent",
            assistant_runtime_rollout_revision="startup-main-v1",
        )
        active = validate_runtime_rollout_startup(self.session, settings=settings)
        self.assertEqual(active.id, revision.id)

        with self.assertRaises(RolloutError) as ctx:
            validate_runtime_rollout_startup(
                self.session,
                settings=SimpleNamespace(
                    assistant_runtime_mode="main_agent",
                    assistant_runtime_rollout_revision="different-revision",
                ),
            )
        self.assertEqual(ctx.exception.code, "runtime_rollout_revision_mismatch")

        with self.assertRaises(RolloutError) as mode_ctx:
            validate_runtime_rollout_startup(
                self.session,
                settings=SimpleNamespace(
                    assistant_runtime_mode="legacy",
                    assistant_runtime_rollout_revision="startup-main-v1",
                ),
            )
        self.assertEqual(mode_ctx.exception.code, "runtime_rollout_mode_mismatch")

    def test_activate_and_rollback_wrappers(self) -> None:
        from app.assistant.migration.rollout import (
            activate_revision,
            prepare_revision,
            rollback_to_legacy,
        )

        rev = prepare_revision(
            self.session,
            revision_label="main-local-v1",
            runtime_mode="main_agent",
            eligible_closure_digest=_DIGEST_A,
            build_revision="development",
            read_canary_percent=100,
        )
        control = activate_revision(
            self.session,
            rollout_revision_id=rev.id,
            expected_control_revision=0,
            actor_principal="op-1",
            reason="local main",
        )
        self.assertEqual(control.active_rollout_revision_id, rev.id)

        rb_rev, rb_control = rollback_to_legacy(
            self.session,
            revision_label="rollback-legacy-v1",
            eligible_closure_digest=_DIGEST_C,
            build_revision="development",
            actor_principal="op-1",
            expected_control_revision=int(control.state_revision),
        )
        self.assertEqual(rb_rev.runtime_mode, "legacy")
        self.assertEqual(rb_control.active_rollout_revision_id, rb_rev.id)
        self.session.commit()

    def test_cli_rollout_prepare_dry_run(self) -> None:
        import tempfile
        from app.assistant.migration.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "rollout.json"
            code = main(
                [
                    "rollout",
                    "prepare",
                    "--environment",
                    "test",
                    "--database-fingerprint",
                    "fp",
                    "--source-snapshot-digest",
                    "a" * 64,
                    "--expected-schema-head",
                    "6417df0243be",
                    "--expected-build-revision",
                    "development",
                    "--request-id",
                    "rollout-prep-1",
                    "--batch-size",
                    "1",
                    "--dry-run",
                    "--report-json",
                    str(report),
                    "--revision-label",
                    "cli-main-v1",
                    "--runtime-mode",
                    "main_agent",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(report.exists())

if __name__ == "__main__":
    unittest.main()

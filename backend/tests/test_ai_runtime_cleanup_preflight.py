"""Plan 10 Task 10 — cleanup gate evaluate / B2 preflight unit tests."""

from __future__ import annotations

import os
import runpy
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_B2_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "ca6f564ef4bd_remove_legacy_assistant_skill_runtime.py"
)


def _sqlite_session():
    from app.database import Base
    from app.assistant.migration import models as migration_models  # noqa: F401

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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


class CleanupEvaluateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.engine = _sqlite_session()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_evaluate_passes_when_hard_counts_zero(self) -> None:
        from app.assistant.migration.cleanup import evaluate_cleanup_gate

        result = evaluate_cleanup_gate(
            self.session,
            gate_kind="deploy_b1",
            schema_revision="6417df0243be",
            build_revision="test-build",
            environment="test",
            database_fingerprint="sqlite-test",
            request_id="req-eval-1",
            actor_principal="op-1",
            dry_run=True,
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.decision, "passed")
        self.assertEqual(result.blockers, [])
        self.assertEqual(result.snapshot_counts["pendingLegacyApprovals"], 0)
        self.assertEqual(result.snapshot_counts["invalidL2Rows"], 0)
        self.assertTrue(result.evidence_digest)
        self.assertEqual(len(result.evidence_digest), 64)

    def test_evaluate_apply_persists_gate_row(self) -> None:
        from app.assistant.migration.cleanup import evaluate_cleanup_gate
        from app.assistant.migration.models import AssistantRuntimeCleanupGate

        result = evaluate_cleanup_gate(
            self.session,
            gate_kind="deploy_b2",
            schema_revision="6417df0243be",
            build_revision="test-build",
            environment="test",
            database_fingerprint="sqlite-test",
            request_id="req-eval-2",
            actor_principal="op-1",
            dry_run=False,
        )
        self.session.commit()
        self.assertTrue(result.valid)
        self.assertIsNotNone(result.gate_id)
        row = self.session.get(
            AssistantRuntimeCleanupGate, uuid.UUID(result.gate_id)
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.decision, "passed")
        self.assertEqual(row.gate_kind, "deploy_b2")
        self.assertEqual(row.snapshot_counts["blockerCount"], 0)

    def test_evaluate_fails_on_blocked_migration_items(self) -> None:
        from app.assistant.migration.cleanup import evaluate_cleanup_gate
        from app.assistant.migration.models import AssistantRuntimeMigrationItem
        from app.assistant.migration.repository import RuntimeMigrationRepository
        from uuid import uuid4

        # Direct insert of a blocked item (discovered->blocked transition needs
        # intermediate states depending on path; insert is fine for count query).
        item = AssistantRuntimeMigrationItem(
            id=uuid4(),
            subject_kind="skill",
            source_type="assistant_skill",
            source_id="skill-blocked-1",
            source_name="blocked-skill",
            source_name_normalized="blocked-skill",
            source_digest=_DIGEST_A,
            state="blocked",
            reason_code="test_block",
            evidence_json={"reason": "test"},
            source_revision=0,
            target_revision=0,
            attempt_count=0,
            state_revision=1,
            actor_principal="op-1",
            build_revision="test-build",
        )
        self.session.add(item)
        self.session.commit()

        result = evaluate_cleanup_gate(
            self.session,
            gate_kind="deploy_b1",
            schema_revision="6417df0243be",
            build_revision="test-build",
            environment="test",
            database_fingerprint="sqlite-test",
            request_id="req-eval-3",
            actor_principal="op-1",
            dry_run=True,
        )
        self.assertFalse(result.valid)
        self.assertEqual(result.decision, "failed")
        self.assertTrue(
            any(b.startswith("blocked_migration_items=") for b in result.blockers)
        )


class CleanupPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.engine = _sqlite_session()
        self._prev_ack = os.environ.pop("MINDATLAS_PLAN10_B2_MAINTENANCE_ACK", None)
        self._prev_override = os.environ.pop("MINDATLAS_PLAN10_B2_TEST_OVERRIDE", None)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        if self._prev_ack is None:
            os.environ.pop("MINDATLAS_PLAN10_B2_MAINTENANCE_ACK", None)
        else:
            os.environ["MINDATLAS_PLAN10_B2_MAINTENANCE_ACK"] = self._prev_ack
        if self._prev_override is None:
            os.environ.pop("MINDATLAS_PLAN10_B2_TEST_OVERRIDE", None)
        else:
            os.environ["MINDATLAS_PLAN10_B2_TEST_OVERRIDE"] = self._prev_override

    def test_destructive_migration_blocks_nonterminal_legacy_runs_with_ack(self) -> None:
        migration = runpy.run_path(str(_B2_MIGRATION))

        class _Result:
            def __init__(self, value: int) -> None:
                self.value = value

            def fetchone(self):
                return (self.value,)

        class _Connection:
            def execute(self, statement):
                return _Result(1 if "assistant_chat_run" in str(statement) else 0)

        with patch.dict(
            os.environ,
            {"MINDATLAS_PLAN10_B2_TEST_OVERRIDE": "1"},
            clear=True,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                migration["_preflight"](_Connection())
        self.assertIn("nonterminal_legacy_runs=1", str(ctx.exception))

    def test_destructive_migration_preflight_propagates_count_query_errors(self) -> None:
        migration = runpy.run_path(str(_B2_MIGRATION))

        class _BrokenConnection:
            def execute(self, _statement):
                raise RuntimeError("count query failed")

        with patch.dict(
            os.environ,
            {"MINDATLAS_PLAN10_B2_TEST_OVERRIDE": "1"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "count query failed"):
                migration["_preflight"](_BrokenConnection())

    def test_preflight_fails_without_maintenance_ack(self) -> None:
        from app.assistant.migration.cleanup import preflight_deploy_b2

        result = preflight_deploy_b2(
            self.session,
            schema_revision="6417df0243be",
            build_revision="test-build",
            environment="test",
            database_fingerprint="sqlite-test",
            request_id="req-pre-1",
            actor_principal="op-1",
            dry_run=True,
            environ={},
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.maintenance_ack)
        self.assertTrue(
            any("MINDATLAS_PLAN10_B2_MAINTENANCE_ACK" in b for b in result.blockers)
        )

    def test_preflight_passes_with_ack_and_zero_counts(self) -> None:
        from app.assistant.migration.cleanup import preflight_deploy_b2

        result = preflight_deploy_b2(
            self.session,
            schema_revision="6417df0243be",
            build_revision="test-build",
            environment="test",
            database_fingerprint="sqlite-test",
            request_id="req-pre-2",
            actor_principal="op-1",
            dry_run=True,
            environ={"MINDATLAS_PLAN10_B2_MAINTENANCE_ACK": "1"},
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.maintenance_ack)
        self.assertEqual(result.blockers, [])

    def test_ack_alone_does_not_pass_when_blocked_items_exist(self) -> None:
        from app.assistant.migration.cleanup import preflight_deploy_b2
        from app.assistant.migration.models import AssistantRuntimeMigrationItem
        from uuid import uuid4

        item = AssistantRuntimeMigrationItem(
            id=uuid4(),
            subject_kind="l2_memory",
            source_type="assistant_conversation_skill_l2_memory",
            source_id="l2-1",
            source_name="x",
            source_name_normalized="x",
            source_digest=_DIGEST_B,
            state="blocked",
            reason_code="unmapped",
            evidence_json={"reason": "test"},
            source_revision=0,
            target_revision=0,
            attempt_count=0,
            state_revision=1,
            actor_principal="op-1",
            build_revision="test-build",
        )
        self.session.add(item)
        self.session.commit()

        result = preflight_deploy_b2(
            self.session,
            schema_revision="6417df0243be",
            build_revision="test-build",
            environment="test",
            database_fingerprint="sqlite-test",
            request_id="req-pre-3",
            actor_principal="op-1",
            dry_run=True,
            environ={"MINDATLAS_PLAN10_B2_MAINTENANCE_ACK": "1"},
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.maintenance_ack)
        self.assertTrue(
            any(b.startswith("blocked_migration_items=") for b in result.blockers)
        )


class CleanupCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session, self.engine = _sqlite_session()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.report_path = str(Path(self._tmpdir.name) / "report.json")

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self._tmpdir.cleanup()

    def _factory(self):
        return self.session

    def test_cli_evaluate_dry_run(self) -> None:
        from app.assistant.migration.cli import main

        code = main(
            [
                "cleanup",
                "evaluate",
                "--gate",
                "deploy_b1",
                "--environment",
                "test",
                "--database-fingerprint",
                "sqlite-test",
                "--source-snapshot-digest",
                _DIGEST_A,
                "--expected-schema-head",
                "6417df0243be",
                "--expected-build-revision",
                "test-build",
                "--request-id",
                "cli-eval-1",
                "--batch-size",
                "10",
                "--dry-run",
                "--report-json",
                self.report_path,
            ],
            session_factory=self._factory,
        )
        self.assertIn(code, {0, 2})
        self.assertTrue(Path(self.report_path).is_file())

    def test_cli_preflight_without_ack_blockers(self) -> None:
        from app.assistant.migration.cli import main

        os.environ.pop("MINDATLAS_PLAN10_B2_MAINTENANCE_ACK", None)
        os.environ.pop("MINDATLAS_PLAN10_B2_TEST_OVERRIDE", None)
        code = main(
            [
                "cleanup",
                "preflight",
                "--gate",
                "deploy_b2",
                "--environment",
                "test",
                "--database-fingerprint",
                "sqlite-test",
                "--source-snapshot-digest",
                _DIGEST_A,
                "--expected-schema-head",
                "6417df0243be",
                "--expected-build-revision",
                "test-build",
                "--request-id",
                "cli-pre-1",
                "--batch-size",
                "10",
                "--dry-run",
                "--report-json",
                self.report_path,
            ],
            session_factory=self._factory,
        )
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

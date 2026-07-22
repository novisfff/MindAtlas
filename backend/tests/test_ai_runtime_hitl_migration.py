"""Plan 10 Task 4 — HITL entrypoint matrix, creation cutoff, archive/verify."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_BUILD_REVISION", "test-build-plan10-task4")
os.environ.setdefault("APP_ENV", "test")

_DIGEST_A = "a" * 64


def _sqlite_full_session():
    """SQLite session with human approval + migration archive tables."""
    from app.database import Base
    from app.assistant.migration import models as migration_models  # noqa: F401
    from app.assistant_config import models as config_models  # noqa: F401
    from app.assistant import models as assistant_models  # noqa: F401

    # File-backed SQLite so sessionmaker connections (HumanLoopRuntime) share schema.
    tmp = tempfile.NamedTemporaryFile(prefix="hitl-mig-", suffix=".db", delete=False)
    tmp.close()
    path = tmp.name
    engine = create_engine(
        f"sqlite+pysqlite:///{path}",
        future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    tables = [
        assistant_models.Conversation.__table__,
        config_models.AssistantHumanApproval.__table__,
        migration_models.AssistantLegacyApprovalArchive.__table__,
        migration_models.AssistantRuntimeMigrationBatch.__table__,
        migration_models.AssistantRuntimeMigrationItem.__table__,
        migration_models.AssistantRuntimeMigrationEvent.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(bind=engine, autoflush=True, autocommit=False, future=True)
    return factory(), engine, factory, path


def _make_approval(
    db,
    *,
    status: str = "pending",
    decision: str | None = None,
    channel_type: str = "workflow_test",
    run_id: str | None = None,
    request_payload: dict | None = None,
):
    from app.assistant_config.models import AssistantHumanApproval
    from app.common.time import utcnow

    row = AssistantHumanApproval(
        run_id=run_id or f"run-{uuid.uuid4().hex[:8]}",
        channel_type=channel_type,
        node_id="human_1",
        node_label="Confirm",
        status=status,
        decision=decision,
        request_payload=request_payload or {"instruction": "confirm", "secret": "do-not-archive-raw"},
        field_schema=[{"name": "title", "type": "string", "required": True}],
        initial_values={"title": "draft"},
        resolved_at=utcnow() if status != "pending" else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _common_kwargs(**overrides):
    base = dict(
        request_id=f"hitl-req-{uuid.uuid4()}",
        actor_principal="operator:task4",
        build_revision="test-build-plan10-task4",
        environment="test",
        database_fingerprint="sqlite-test",
        schema_head="6417df0243be",
        dry_run=False,
        batch_size=100,
        source_snapshot_digest=_DIGEST_A,
    )
    base.update(overrides)
    return base


class EntrypointMatrixTests(unittest.TestCase):
    def test_matrix_invariants_and_required_pins(self) -> None:
        from app.assistant.migration.approvals import (
            ENTRYPOINT_MATRIX,
            assert_entrypoint_matrix_invariants,
            classify_entrypoint,
            entrypoint_allows_blocking_runtime,
            matrix_report,
        )

        assert_entrypoint_matrix_invariants()
        report = matrix_report()
        self.assertTrue(report["ok"])
        self.assertGreaterEqual(report["count"], 8)
        self.assertEqual(len(report["matrixDigest"]), 64)

        # Concrete classifications.
        self.assertEqual(classify_entrypoint("main_agent_chat"), "durable")
        self.assertEqual(classify_entrypoint("plan09_workbench"), "eval_simulated")
        self.assertEqual(classify_entrypoint("workflow_test"), "unsupported_interrupt")
        self.assertEqual(classify_entrypoint("openclaw"), "unsupported_interrupt")
        self.assertEqual(classify_entrypoint("capability_runtime"), "unsupported_interrupt")
        # Unknown defaults unsupported.
        self.assertEqual(classify_entrypoint("totally_unknown_ep"), "unsupported_interrupt")

        # No pinned path may import blocking runtime for new work.
        for name in ("openclaw", "workflow_test", "main_agent_chat", "plan09_workbench"):
            self.assertFalse(entrypoint_allows_blocking_runtime(name), name)

        names = {s.name for s in ENTRYPOINT_MATRIX}
        self.assertIn("openclaw", names)
        self.assertIn("workflow_test", names)

    def test_channel_type_mapping(self) -> None:
        from app.assistant.migration.approvals import classify_channel_type

        self.assertEqual(classify_channel_type("assistant_chat"), "unsupported_interrupt")
        self.assertEqual(classify_channel_type("main_agent"), "durable")
        self.assertEqual(classify_channel_type("workflow_test"), "unsupported_interrupt")
        self.assertEqual(classify_channel_type("openclaw"), "unsupported_interrupt")
        self.assertEqual(classify_channel_type("plan09_workbench"), "eval_simulated")
        self.assertEqual(classify_channel_type("weird_channel"), "unsupported_interrupt")

    def test_every_matrix_row_is_one_of_three_classes(self) -> None:
        from app.assistant.migration.approvals import ENTRYPOINT_MATRIX

        allowed = {"durable", "eval_simulated", "unsupported_interrupt"}
        for spec in ENTRYPOINT_MATRIX:
            self.assertIn(spec.classification, allowed)
            if spec.classification == "durable":
                self.assertIsNotNone(spec.decision_channel)
            if spec.classification == "eval_simulated":
                self.assertIsNotNone(spec.decision_channel)
            if spec.classification == "unsupported_interrupt":
                self.assertFalse(spec.may_import_blocking_runtime)


class CreationCutoffTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.assistant.migration.approvals import set_legacy_approval_creation_cutoff

        set_legacy_approval_creation_cutoff(None)
        self.session, self.engine, self.factory, self.db_path = _sqlite_full_session()

    def tearDown(self) -> None:
        from app.assistant.migration.approvals import set_legacy_approval_creation_cutoff

        set_legacy_approval_creation_cutoff(None)
        self.session.close()
        self.engine.dispose()
        try:
            Path(self.db_path).unlink(missing_ok=True)
        except OSError:
            pass

    def test_create_and_wait_blocked_after_cutoff(self) -> None:
        from app.assistant.migration.approvals import (
            LegacyApprovalCreationCutoffError,
            set_legacy_approval_creation_cutoff,
        )
        from app.assistant.workflow.human_approval_runtime import (
            HumanLoopContext,
            HumanLoopRuntime,
        )
        from app.assistant_config.models import AssistantHumanApproval

        set_legacy_approval_creation_cutoff(True)
        runtime = HumanLoopRuntime(
            self.factory,
            context=HumanLoopContext(run_id="run-cutoff-1", channel_type="workflow_test"),
        )
        with self.assertRaises(LegacyApprovalCreationCutoffError) as ctx:
            runtime.create_and_wait(
                node_id="n1",
                node_label="Confirm",
                request_payload={"instruction": "go"},
                field_schema=[{"name": "title", "type": "string", "required": True}],
                initial_values={"title": "x"},
            )
        self.assertEqual(ctx.exception.reason_code, "legacy_approval_creation_cutoff")
        rows = self.session.query(AssistantHumanApproval).all()
        self.assertEqual(rows, [])

    def test_create_allowed_before_cutoff(self) -> None:
        from app.assistant.migration.approvals import set_legacy_approval_creation_cutoff
        from app.assistant.workflow.human_approval_runtime import (
            GLOBAL_HUMAN_LOOP_COORDINATOR,
            HumanLoopContext,
            HumanLoopRuntime,
            submit_human_approval_decision,
        )
        from app.assistant_config.models import AssistantHumanApproval

        set_legacy_approval_creation_cutoff(False)
        results: list[dict] = []
        errors: list[BaseException] = []

        def _worker() -> None:
            try:
                runtime = HumanLoopRuntime(
                    self.factory,
                    context=HumanLoopContext(
                        run_id="run-pre-cutoff",
                        channel_type="workflow_test",
                    ),
                )
                payload = runtime.create_and_wait(
                    node_id="n1",
                    node_label="Confirm",
                    request_payload={"instruction": "go", "requireRejectComment": False},
                    field_schema=[{"name": "title", "type": "string", "required": True}],
                    initial_values={"title": "x"},
                )
                results.append(payload)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=_worker)
        t.start()
        # Wait until the pending row exists, then resolve it.
        for _ in range(50):
            row = (
                self.session.query(AssistantHumanApproval)
                .filter(AssistantHumanApproval.run_id == "run-pre-cutoff")
                .one_or_none()
            )
            if row is not None and row.status == "pending":
                submit_human_approval_decision(
                    self.session,
                    approval_id=row.id,
                    decision="approved",
                    values={"title": "done"},
                    comment=None,
                    expected_run_id="run-pre-cutoff",
                )
                break
            t.join(timeout=0.05)
        t.join(timeout=5)
        self.assertEqual(errors, [], msg=str(errors))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "approved")
        # Coordinator should not leak waiters.
        self.assertFalse(GLOBAL_HUMAN_LOOP_COORDINATOR.has_waiter(results[0]["id"]))

    def test_cutoff_race_no_new_pending_and_existing_resolves(self) -> None:
        from app.assistant.migration.approvals import (
            LegacyApprovalCreationCutoffError,
            set_legacy_approval_creation_cutoff,
        )
        from app.assistant.workflow.human_approval_runtime import (
            HumanLoopContext,
            HumanLoopRuntime,
            submit_human_approval_decision,
        )
        from app.assistant_config.models import AssistantHumanApproval

        # Pre-existing pending (created before cutoff) must still resolve.
        existing = _make_approval(self.session, status="pending", run_id="run-existing")
        set_legacy_approval_creation_cutoff(True)

        runtime = HumanLoopRuntime(
            self.factory,
            context=HumanLoopContext(run_id="run-new", channel_type="assistant_chat"),
        )
        with self.assertRaises(LegacyApprovalCreationCutoffError):
            runtime.create_and_wait(
                node_id="n2",
                node_label="Later",
                request_payload={"instruction": "nope"},
                field_schema=[{"name": "title", "type": "string", "required": True}],
                initial_values={"title": "y"},
            )

        resolved = submit_human_approval_decision(
            self.session,
            approval_id=existing.id,
            decision="rejected",
            values={"title": "stop"},
            comment="drain",
            expected_run_id="run-existing",
        )
        self.assertEqual(resolved["status"], "rejected")

        pending = (
            self.session.query(AssistantHumanApproval)
            .filter(AssistantHumanApproval.status == "pending")
            .all()
        )
        self.assertEqual(pending, [])
        # Never fabricate durable resume from legacy row.
        self.assertNotIn("resumeToken", resolved)
        self.assertNotIn("resume_token", resolved)


class ArchiveVerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.assistant.migration.approvals import set_legacy_approval_creation_cutoff

        set_legacy_approval_creation_cutoff(None)
        self.session, self.engine, self.factory, self.db_path = _sqlite_full_session()

    def tearDown(self) -> None:
        from app.assistant.migration.approvals import set_legacy_approval_creation_cutoff

        set_legacy_approval_creation_cutoff(None)
        self.session.close()
        self.engine.dispose()
        try:
            Path(self.db_path).unlink(missing_ok=True)
        except OSError:
            pass

    def test_archive_terminal_skips_pending_and_digests_only(self) -> None:
        from app.assistant.migration.approvals import (
            archive_terminal_approvals,
            safe_approval_payload_digest,
            verify_approvals,
        )
        from app.assistant.migration.models import AssistantLegacyApprovalArchive

        pending = _make_approval(self.session, status="pending", run_id="run-p")
        approved = _make_approval(
            self.session,
            status="approved",
            decision="approved",
            run_id="run-a",
            request_payload={"secret": "raw-secret-must-not-land-in-archive"},
        )
        cancelled = _make_approval(
            self.session, status="cancelled", run_id="run-c", decision=None
        )

        report = archive_terminal_approvals(self.session, **_common_kwargs())
        self.session.commit()
        self.assertEqual(report.failed, 0)
        self.assertEqual(report.terminal_count, 2)
        self.assertEqual(report.pending_count, 1)
        self.assertGreaterEqual(report.succeeded, 2)
        # Pending blocked, not archived.
        self.assertTrue(any(b.endswith("pending_not_terminal") for b in report.blockers))

        archives = self.session.query(AssistantLegacyApprovalArchive).all()
        self.assertEqual(len(archives), 2)
        source_ids = {a.source_row_id for a in archives}
        self.assertIn(str(approved.id), source_ids)
        self.assertIn(str(cancelled.id), source_ids)
        self.assertNotIn(str(pending.id), source_ids)

        for a in archives:
            # No raw secret / continuation token fields on archive row.
            blob = " ".join(
                str(getattr(a, col, "") or "")
                for col in (
                    "safe_payload_digest",
                    "migration_evidence_digest",
                    "status",
                    "decision",
                    "source_row_id",
                )
            )
            self.assertNotIn("raw-secret", blob)
            self.assertNotIn("resume", blob.lower())
            self.assertEqual(len(a.safe_payload_digest), 64)
            self.assertEqual(len(a.migration_evidence_digest), 64)

        # Digests match live terminal rows.
        self.assertEqual(
            archives[0].safe_payload_digest
            if archives[0].source_row_id == str(approved.id)
            else next(a for a in archives if a.source_row_id == str(approved.id)).safe_payload_digest,
            safe_approval_payload_digest(approved),
        )

        # Idempotent re-archive.
        report2 = archive_terminal_approvals(
            self.session, **_common_kwargs(request_id=f"hitl-req-{uuid.uuid4()}")
        )
        self.session.commit()
        self.assertEqual(self.session.query(AssistantLegacyApprovalArchive).count(), 2)
        self.assertEqual(report2.failed, 0)

        # Verify with pending still present fails zero-pending gate.
        v = verify_approvals(
            self.session,
            **_common_kwargs(request_id=f"hitl-req-{uuid.uuid4()}", dry_run=True),
            require_zero_pending=True,
        )
        self.assertFalse(v.to_dict()["ok"])
        self.assertTrue(any("pending_remaining" in b for b in v.blockers))

        # Drain pending then verify green.
        from app.assistant.workflow.human_approval_runtime import submit_human_approval_decision

        submit_human_approval_decision(
            self.session,
            approval_id=pending.id,
            decision="approved",
            values={"title": "ok"},
            comment=None,
        )
        archive_terminal_approvals(
            self.session, **_common_kwargs(request_id=f"hitl-req-{uuid.uuid4()}")
        )
        self.session.commit()
        v2 = verify_approvals(
            self.session,
            **_common_kwargs(request_id=f"hitl-req-{uuid.uuid4()}", dry_run=True),
            require_zero_pending=True,
        )
        self.assertTrue(v2.to_dict()["ok"], v2.blockers)
        self.assertEqual(v2.pending_count, 0)
        self.assertIsNotNone(v2.archive_count_digest)
        self.assertEqual(len(v2.archive_count_digest or ""), 64)

    def test_archive_digest_conflict_blocks(self) -> None:
        from app.assistant.migration.approvals import archive_terminal_approvals
        from app.assistant.migration.models import AssistantLegacyApprovalArchive
        from app.assistant.migration.repository import RuntimeMigrationRepository

        approved = _make_approval(
            self.session, status="approved", decision="approved", run_id="run-conf"
        )
        repo = RuntimeMigrationRepository(self.session)
        repo.archive_legacy_approval(
            source_row_id=str(approved.id),
            safe_payload_digest="b" * 64,
            status="approved",
            migration_evidence_digest="c" * 64,
            actor_principal="op",
        )
        self.session.commit()

        report = archive_terminal_approvals(self.session, **_common_kwargs())
        self.session.commit()
        self.assertGreaterEqual(report.blocked, 1)
        self.assertTrue(any("conflict" in b for b in report.blockers))
        # Original wrong digest preserved (immutable).
        row = (
            self.session.query(AssistantLegacyApprovalArchive)
            .filter(AssistantLegacyApprovalArchive.source_row_id == str(approved.id))
            .one()
        )
        self.assertEqual(row.safe_payload_digest, "b" * 64)

    def test_zero_pending_gate_helper(self) -> None:
        from app.assistant.migration.approvals import (
            set_legacy_approval_creation_cutoff,
            zero_pending_gate,
        )

        self.assertTrue(zero_pending_gate(self.session)["ok"])
        _make_approval(self.session, status="pending")
        gate = zero_pending_gate(self.session)
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["pendingCount"], 1)
        set_legacy_approval_creation_cutoff(True)
        self.assertTrue(zero_pending_gate(self.session)["cutoffActive"])


class ApprovalsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.assistant.migration.approvals import set_legacy_approval_creation_cutoff

        set_legacy_approval_creation_cutoff(None)
        self.session, self.engine, self.factory, self.db_path = _sqlite_full_session()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.report_path = str(Path(self._tmpdir.name) / "report.json")

    def tearDown(self) -> None:
        from app.assistant.migration.approvals import set_legacy_approval_creation_cutoff

        set_legacy_approval_creation_cutoff(None)
        self.session.close()
        self.engine.dispose()
        self._tmpdir.cleanup()
        try:
            Path(self.db_path).unlink(missing_ok=True)
        except OSError:
            pass

    def _base_argv(self, group_cmd: list[str], *, apply: bool = False) -> list[str]:
        mode = ["--apply"] if apply else ["--dry-run"]
        return [
            *group_cmd,
            "--environment",
            "test",
            "--database-fingerprint",
            "sqlite-test",
            "--source-snapshot-digest",
            _DIGEST_A,
            "--expected-schema-head",
            "6417df0243be",
            "--expected-build-revision",
            "test-build-plan10-task4",
            "--request-id",
            f"cli-{uuid.uuid4()}",
            "--batch-size",
            "50",
            *mode,
            "--report-json",
            self.report_path,
            "--operator-principal",
            "operator:task4",
        ]

    def test_cli_archive_and_verify(self) -> None:
        from app.assistant.migration.cli import main

        _make_approval(self.session, status="approved", decision="approved")
        self.session.commit()

        def factory():
            return self.session

        code = main(self._base_argv(["approvals", "archive"], apply=True), session_factory=factory)
        self.assertEqual(code, 0)
        code = main(
            self._base_argv(["approvals", "verify"], apply=False)
            + ["--allow-pending"],
            session_factory=factory,
        )
        # zero pending already; ok
        self.assertIn(code, {0, 2})  # 0 ok or 2 with blockers if any


class OpenClawAndWorkflowTestPinTests(unittest.TestCase):
    def test_openclaw_legacy_blocking_is_unsupported(self) -> None:
        """OpenClaw must not retain a blocking HumanLoopRuntime path."""
        from app.assistant.migration.approvals import (
            classify_entrypoint,
            entrypoint_allows_blocking_runtime,
        )

        self.assertEqual(classify_entrypoint("openclaw"), "unsupported_interrupt")
        self.assertFalse(entrypoint_allows_blocking_runtime("openclaw"))

    def test_workflow_test_is_unsupported_for_new_blocking(self) -> None:
        from app.assistant.migration.approvals import (
            classify_entrypoint,
            entrypoint_allows_blocking_runtime,
        )

        self.assertEqual(classify_entrypoint("workflow_test"), "unsupported_interrupt")
        self.assertFalse(entrypoint_allows_blocking_runtime("workflow_test"))

    def test_workflow_adapter_source_rejects_legacy_blocking_unconditionally(self) -> None:
        """Regression pin: shared capability path never re-opens OpenClaw-only blocking."""
        from pathlib import Path

        import app.assistant.capabilities.adapters.workflow as workflow_adapter

        src = Path(workflow_adapter.__file__).read_text(encoding="utf-8")
        # Plan 10 Task 4 removed the OpenClaw exception that admitted blocking HITL.
        self.assertNotIn("and not _is_openclaw_compat(request)", src)
        self.assertIn('interrupt_mode == "legacy_blocking"', src)
        self.assertIn('error_type="unsupported_interrupt"', src)
        # Must not emit legacy_blocking compatibility child events for OpenClaw.
        self.assertNotIn('safe_status="legacy_blocking"', src)


if __name__ == "__main__":
    unittest.main()

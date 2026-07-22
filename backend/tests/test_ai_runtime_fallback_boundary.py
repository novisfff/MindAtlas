"""Pre-insert-only fallback boundary tests (Plan 10 Task 6).

Automatic Legacy fallback is allowed only before any durable Chat Run insert.
After a Main Agent Run row exists, failures stay on that Run's recover/fail/
cancel/reconcile path — no fallback event, no second Legacy Run, no remap.
"""

from __future__ import annotations

import unittest
import uuid

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

    tables = [
        migration_models.AssistantRuntimeMigrationItem.__table__,
        migration_models.AssistantRuntimeMigrationEvent.__table__,
        migration_models.AssistantRuntimeMigrationBatch.__table__,
        migration_models.AssistantRuntimeRolloutRevision.__table__,
        migration_models.AssistantRuntimeRolloutControl.__table__,
        migration_models.AssistantRuntimeRolloutEvent.__table__,
        migration_models.AssistantRuntimeRolloutAssignment.__table__,
        # Fallback event has FK to chat_run; unit tests evaluate decision only
        # (repository insert covered in PG suite). Skip creating fallback table
        # when FK targets are unavailable.
        migration_models.AssistantLegacyApprovalArchive.__table__,
        migration_models.AssistantRuntimeCleanupGate.__table__,
    ]
    Base.metadata.create_all(engine, tables=tables)
    factory = sessionmaker(bind=engine, autoflush=True, autocommit=False, future=True)
    return factory(), engine


class PreInsertFallbackBoundaryTests(unittest.TestCase):
    def test_preinsert_fallback_allowed_when_admission_fails(self) -> None:
        from app.assistant.migration.rollout import evaluate_preinsert_fallback

        rev_id = uuid.uuid4()
        assign_id = uuid.uuid4()
        decision = evaluate_preinsert_fallback(
            assigned_runtime_kind="main_agent",
            admission_ok=False,
            admission_failure_code="no_compatible_worker",
            chat_run_already_inserted=False,
            request_id="req-pre-1",
            rollout_revision_id=rev_id,
            assignment_id=assign_id,
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.candidate_runtime_kind, "main_agent")
        self.assertEqual(decision.selected_runtime_kind, "legacy")
        self.assertEqual(decision.selection_reason, "preinsert_fallback")
        self.assertIsNotNone(decision.admission_failure_digest)
        self.assertIsNotNone(decision.resulting_legacy_run_id)
        rd = decision.to_rollout_decision()
        self.assertEqual(rd.assigned_runtime_kind, "main_agent")
        self.assertEqual(rd.selected_runtime_kind, "legacy")
        self.assertEqual(rd.selection_reason, "preinsert_fallback")

    def test_postinsert_fallback_forbidden(self) -> None:
        from app.assistant.migration.rollout import evaluate_preinsert_fallback

        decision = evaluate_preinsert_fallback(
            assigned_runtime_kind="main_agent",
            admission_ok=False,
            admission_failure_code="worker_claim_failed",
            chat_run_already_inserted=True,
            request_id="req-post-1",
            rollout_revision_id=uuid.uuid4(),
            assignment_id=uuid.uuid4(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.selected_runtime_kind, "main_agent")
        self.assertEqual(decision.selection_reason, "assigned")
        self.assertIsNone(decision.resulting_legacy_run_id)

    def test_assigned_legacy_never_opens_fallback(self) -> None:
        from app.assistant.migration.rollout import evaluate_preinsert_fallback

        decision = evaluate_preinsert_fallback(
            assigned_runtime_kind="legacy",
            admission_ok=False,
            admission_failure_code="anything",
            chat_run_already_inserted=False,
            request_id="req-leg-1",
            rollout_revision_id=uuid.uuid4(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.selected_runtime_kind, "legacy")
        self.assertEqual(decision.selection_reason, "assigned")

    def test_admission_ok_selects_main_without_fallback(self) -> None:
        from app.assistant.migration.rollout import evaluate_preinsert_fallback

        decision = evaluate_preinsert_fallback(
            assigned_runtime_kind="main_agent",
            admission_ok=True,
            admission_failure_code=None,
            chat_run_already_inserted=False,
            request_id="req-ok-1",
            rollout_revision_id=uuid.uuid4(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.selected_runtime_kind, "main_agent")

    def test_assignment_unchanged_by_fallback_decision(self) -> None:
        """Fallback is per-request evidence; assignment row is not rewritten."""
        from app.assistant.migration.rollout import (
            ensure_assignment,
            evaluate_preinsert_fallback,
            prepare_revision,
        )

        session, engine = _sqlite_session()
        try:
            rev = prepare_revision(
                session,
                revision_label="fb-assign-v1",
                runtime_mode="main_agent",
                eligible_closure_digest=_DIGEST_A,
                build_revision="development",
                read_canary_percent=100,
                cohort_salt="fb-salt",
            )
            conv = uuid.uuid4()
            assigned = ensure_assignment(
                session,
                conversation_id=conv,
                revision=rev,
                salt="fb-salt",
            )
            self.assertEqual(assigned.assigned_runtime_kind, "main_agent")
            decision = evaluate_preinsert_fallback(
                assigned_runtime_kind=assigned.assigned_runtime_kind,
                admission_ok=False,
                admission_failure_code="adapter_unavailable_before_request",
                chat_run_already_inserted=False,
                request_id="req-fb-assign",
                rollout_revision_id=rev.id,
                assignment_id=assigned.assignment.id,
            )
            self.assertTrue(decision.allowed)
            # Re-fetch assignment — still main_agent.
            again = ensure_assignment(
                session,
                conversation_id=conv,
                revision=rev,
                salt="fb-salt",
            )
            self.assertEqual(again.assignment.id, assigned.assignment.id)
            self.assertEqual(again.assigned_runtime_kind, "main_agent")
            self.assertEqual(
                again.assignment.assigned_runtime_kind, "main_agent"
            )
        finally:
            session.close()
            engine.dispose()

    def test_postinsert_failure_points_enumerated(self) -> None:
        """Every post-insert failure class must refuse fallback."""
        from app.assistant.migration.rollout import evaluate_preinsert_fallback

        post_insert_codes = (
            "worker_claim_failed",
            "manifest_reconstruction_failed",
            "provider_construction_failed",
            "provider_attempt_failed",
            "interrupt_failed",
            "visible_output_failed",
            "effect_failed",
            "config_drift_after_insert",
        )
        rev = uuid.uuid4()
        for code in post_insert_codes:
            with self.subTest(code=code):
                decision = evaluate_preinsert_fallback(
                    assigned_runtime_kind="main_agent",
                    admission_ok=False,
                    admission_failure_code=code,
                    chat_run_already_inserted=True,
                    request_id=f"req-{code}",
                    rollout_revision_id=rev,
                )
                self.assertFalse(decision.allowed)
                self.assertIsNone(decision.fallback_event_id)
                self.assertIsNone(decision.resulting_legacy_run_id)

    def test_admit_with_rollout_fallback_metadata_preinsert(self) -> None:
        """When assignment is main_agent and preflight fails, kwargs carry fallback."""
        from unittest.mock import patch

        from app.assistant.migration.rollout import (
            activate_revision,
            admit_with_rollout,
            prepare_revision,
        )

        session, engine = _sqlite_session()
        try:
            rev = prepare_revision(
                session,
                revision_label="admit-fb-v1",
                runtime_mode="main_agent",
                eligible_closure_digest=_DIGEST_A,
                build_revision="development",
                read_canary_percent=100,
            )
            activate_revision(
                session,
                rollout_revision_id=rev.id,
                expected_control_revision=0,
            )
            session.commit()

            with patch(
                "app.assistant.durable.admission.admit_and_select_runtime",
                return_value=("legacy", "no_compatible_worker", {}),
            ):
                # Call through admit_with_rollout which uses use_rollout_assignment=False
                # on the nested preflight — patch the plan04 path via mode force.
                kind, reason, kwargs, decision = admit_with_rollout(
                    session,
                    conversation_id=uuid.uuid4(),
                    request_id="req-admit-fb",
                    require_compatible_worker=True,
                )
            # Depending on nested call, kind should be legacy via preinsert fallback
            # when assigned main_agent and nested returns non-main.
            self.assertEqual(kind, "legacy")
            self.assertIsNotNone(decision)
            assert decision is not None
            self.assertEqual(decision.assigned_runtime_kind, "main_agent")
            self.assertEqual(decision.selected_runtime_kind, "legacy")
            self.assertEqual(decision.selection_reason, "preinsert_fallback")
            self.assertIn("_preinsert_fallback", kwargs)
        finally:
            session.close()
            engine.dispose()

    def test_postinsert_flag_blocks_fallback_in_admit_with_rollout(self) -> None:
        from unittest.mock import patch

        from app.assistant.migration.rollout import (
            activate_revision,
            admit_with_rollout,
            prepare_revision,
        )

        session, engine = _sqlite_session()
        try:
            rev = prepare_revision(
                session,
                revision_label="admit-post-v1",
                runtime_mode="main_agent",
                eligible_closure_digest=_DIGEST_A,
                build_revision="development",
                read_canary_percent=100,
            )
            activate_revision(
                session,
                rollout_revision_id=rev.id,
                expected_control_revision=0,
            )
            session.commit()

            with patch(
                "app.assistant.durable.admission.admit_and_select_runtime",
                return_value=("legacy", "no_compatible_worker", {}),
            ):
                kind, reason, kwargs, decision = admit_with_rollout(
                    session,
                    conversation_id=uuid.uuid4(),
                    request_id="req-post-block",
                    chat_run_already_inserted=True,
                )
            # Post-insert: no fallback metadata; selected stays candidate path.
            self.assertNotIn("_preinsert_fallback", kwargs)
            self.assertEqual(kind, "main_agent")
            self.assertEqual(reason, "no_compatible_worker")
        finally:
            session.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()

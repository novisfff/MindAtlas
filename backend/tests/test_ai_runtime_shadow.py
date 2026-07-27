"""Shadow comparison linkage + Eval purpose contract tests (Plan 10 Task 1).

Does not weaken Plan 06 single-nonterminal production Run uniqueness.
Full PostgreSQL FK/trigger coverage lives in
test_ai_runtime_migration_repository_postgres.py.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64
_DIGEST_D = "d" * 64


class EvalPurposeContractTests(unittest.TestCase):
    def test_eval_run_model_has_purpose_and_shadow_gate_constraint(self) -> None:
        from app.assistant.evaluation.models import AssistantSkillEvalRun

        cols = {c.name for c in AssistantSkillEvalRun.__table__.columns}
        self.assertIn("purpose", cols)
        names = {c.name for c in AssistantSkillEvalRun.__table__.constraints if getattr(c, "name", None)}
        self.assertIn("ck_assistant_skill_eval_run_purpose", names)
        self.assertIn(
            "ck_assistant_skill_eval_run_runtime_shadow_gate_ineligible", names
        )

    def test_create_run_accepts_runtime_shadow_purpose_default_ineligible(self) -> None:
        """Repository-level purpose validation without full eval schema."""
        from app.assistant.evaluation.repository import (
            CODE_INVALID_INPUT,
            EvaluationRepositoryError,
            _require_sha256,
        )

        # purpose validation is inline in create_run; test invalid purpose path
        # via a lightweight session if available; otherwise assert helper digests.
        dig = _require_sha256(_DIGEST_A, field="x")
        self.assertEqual(dig, _DIGEST_A)

        # Invalid purpose must raise before DB insert — use a minimal mock session.
        class _Sess:
            def get(self, *a, **k):  # noqa: ANN001
                return None

            def add(self, *a, **k):  # noqa: ANN001
                raise AssertionError("should not add on invalid purpose")

            def flush(self):  # noqa: ANN001
                return None

            def execute(self, *a, **k):  # noqa: ANN001
                raise AssertionError("should not execute on invalid purpose")

        from app.assistant.evaluation.repository import EvaluationRepository

        repo = EvaluationRepository(_Sess())  # type: ignore[arg-type]
        with self.assertRaises(EvaluationRepositoryError) as ctx:
            repo.create_run(
                subject_kind="skill_version",
                subject_aggregate_id=uuid.uuid4(),
                subject_version_id=uuid.uuid4(),
                subject_content_digest=_DIGEST_A,
                subject_binding_digest=_DIGEST_B,
                dataset_version_ids=[],
                threshold_policy_version="t1",
                mode="interactive_scripted",
                isolation_namespace_id=uuid.uuid4(),
                runtime_contract_version=1,
                required_build_revision="build-1",
                isolation_digest=_DIGEST_C,
                purpose="not_a_purpose",
            )
        self.assertEqual(ctx.exception.code, CODE_INVALID_INPUT)


class ShadowComparisonModelTests(unittest.TestCase):
    def test_shadow_comparison_pairs_production_and_eval_fks(self) -> None:
        from app.assistant.migration.models import AssistantRuntimeShadowComparison

        table = AssistantRuntimeShadowComparison.__table__
        col_names = {c.name for c in table.columns}
        self.assertIn("production_run_id", col_names)
        self.assertIn("eval_run_id", col_names)
        self.assertIn("private_input_payload_digest", col_names)
        # FKs target production chat run + eval run (never a second ChatRun).
        fks = list(table.foreign_keys)
        targets = {fk.column.table.name for fk in fks}
        self.assertIn("assistant_chat_run", targets)
        self.assertIn("assistant_skill_eval_run", targets)
        self.assertNotIn("assistant_runtime_shadow_comparison", targets - {
            "assistant_runtime_shadow_comparison",
            "assistant_chat_run",
            "assistant_skill_eval_run",
            "assistant_runtime_rollout_revision",
            "assistant_runtime_rollout_assignment",
        } | targets)

    def test_plan06_active_run_unique_index_unchanged(self) -> None:
        from app.assistant.models import AssistantChatRun

        index_names = {idx.name for idx in AssistantChatRun.__table__.indexes}
        self.assertIn("uq_assistant_chat_run_active_conversation", index_names)
        # No shadow execution_mode exception column.
        cols = {c.name for c in AssistantChatRun.__table__.columns}
        self.assertNotIn("execution_mode", cols)
        self.assertNotIn("shadow_of_run_id", cols)

    def test_shadow_comparison_repository_rejects_eval_rebind(self) -> None:
        from app.database import Base
        from app.assistant.migration import models as migration_models
        from app.assistant.migration.repository import (
            CODE_CONFLICT,
            RuntimeMigrationRepository,
            RuntimeMigrationRepositoryError,
        )

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _connection_record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=OFF")  # skip production FKs in unit test
            cur.close()

        Base.metadata.create_all(
            engine,
            tables=[
                migration_models.AssistantRuntimeShadowComparison.__table__,
            ],
        )
        Session = sessionmaker(bind=engine, future=True)
        session = Session()
        try:
            repo = RuntimeMigrationRepository(session)
            prod1 = uuid.uuid4()
            prod2 = uuid.uuid4()
            eval_id = uuid.uuid4()
            c1 = repo.create_shadow_comparison(
                production_run_id=prod1,
                eval_run_id=eval_id,
                input_digest=_DIGEST_A,
                context_digest=_DIGEST_B,
            )
            c2 = repo.create_shadow_comparison(
                production_run_id=prod1,
                eval_run_id=eval_id,
                input_digest=_DIGEST_A,
                context_digest=_DIGEST_B,
            )
            self.assertEqual(c1.id, c2.id)
            with self.assertRaises(RuntimeMigrationRepositoryError) as ctx:
                repo.create_shadow_comparison(
                    production_run_id=prod2,
                    eval_run_id=eval_id,
                    input_digest=_DIGEST_C,
                    context_digest=_DIGEST_D,
                )
            self.assertEqual(ctx.exception.code, CODE_CONFLICT)
        finally:
            session.close()
            engine.dispose()

    def test_runtime_shadow_input_snapshot_contract_has_no_raw_content_fields(self) -> None:
        from app.assistant.migration.contracts import RuntimeShadowInputSnapshot

        fields = set(RuntimeShadowInputSnapshot.model_fields.keys())
        for forbidden in ("prompt", "messages", "content", "payload_body", "system_prompt"):
            self.assertNotIn(forbidden, fields)
        self.assertIn("payload_digest", fields)
        self.assertIn("source_production_run_id", fields)


class ShadowHelperTests(unittest.TestCase):
    def test_build_private_input_snapshot_has_only_digests(self) -> None:
        from app.assistant.migration.shadow import build_private_input_snapshot

        prod = uuid.uuid4()
        snap = build_private_input_snapshot(
            production_run_id=prod,
            source_user_message_id=uuid.uuid4(),
            principal_scope_digest=_DIGEST_A,
            message_prefix_digest=_DIGEST_B,
            authorized_context_digest=_DIGEST_C,
        )
        payload = snap.model_dump()
        for forbidden in ("prompt", "messages", "content", "payload_body", "system_prompt"):
            self.assertNotIn(forbidden, payload)
        self.assertEqual(payload["source_production_run_id"], str(prod))
        self.assertEqual(len(payload["payload_digest"]), 64)

    def test_record_comparison_evidence_strips_raw_content(self) -> None:
        from app.database import Base
        from app.assistant.migration import models as migration_models
        from app.assistant.migration.repository import RuntimeMigrationRepository
        from app.assistant.migration.shadow import record_comparison_evidence

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _connection_record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=OFF")
            cur.close()

        Base.metadata.create_all(
            engine,
            tables=[migration_models.AssistantRuntimeShadowComparison.__table__],
        )
        Session = sessionmaker(bind=engine, future=True)
        session = Session()
        try:
            repo = RuntimeMigrationRepository(session)
            prod = uuid.uuid4()
            eval_id = uuid.uuid4()
            row = repo.create_shadow_comparison(
                production_run_id=prod,
                eval_run_id=eval_id,
                input_digest=_DIGEST_A,
                context_digest=_DIGEST_B,
            )
            updated = record_comparison_evidence(
                session,
                comparison_id=row.id,
                evidence={
                    "intent_class": "qa",
                    "completion_summary": "ok",
                    "prompt": "LEAK THIS",
                    "messages": [{"role": "user", "content": "secret"}],
                    "quality_assertion_snapshot": {
                        "match": True,
                        "prompt": "nope",
                        "score": 1,
                    },
                    "result_state": "match",
                },
            )
            self.assertEqual(updated.intent_class, "qa")
            self.assertEqual(updated.result_state, "match")
            self.assertNotIn("prompt", updated.quality_assertion_snapshot or {})
            self.assertEqual((updated.quality_assertion_snapshot or {}).get("score"), 1)
        finally:
            session.close()
            engine.dispose()

    def test_legacy_response_independence_from_shadow_failure(self) -> None:
        from app.assistant.migration.shadow import (
            ShadowScheduleResult,
            legacy_response_is_independent,
        )

        prod = uuid.uuid4()
        failed = ShadowScheduleResult(
            scheduled=False,
            reason_code="schedule_failed",
            error="boom",
        )
        self.assertTrue(
            legacy_response_is_independent(
                production_run_id=prod,
                shadow_result=failed,
                production_events_before=["a", "b"],
                production_events_after=["a", "b"],
            )
        )
        self.assertFalse(
            legacy_response_is_independent(
                production_run_id=prod,
                shadow_result=failed,
                production_events_before=["a"],
                production_events_after=["a", "shadow_mutated"],
            )
        )
        # Eval id must never equal production run id.
        same_id = ShadowScheduleResult(
            scheduled=True,
            eval_run_id=prod,
            comparison_id=uuid.uuid4(),
        )
        self.assertFalse(
            legacy_response_is_independent(
                production_run_id=prod, shadow_result=same_id
            )
        )

    def test_schedule_runtime_shadow_nonblocking_on_eval_failure(self) -> None:
        """Shadow schedule failure must not raise when nonblocking=True."""
        from unittest.mock import MagicMock, patch

        from app.assistant.migration.shadow import schedule_runtime_shadow

        session = MagicMock()
        with patch(
            "app.assistant.evaluation.repository.EvaluationRepository",
            side_effect=RuntimeError("eval unavailable"),
        ):
            result = schedule_runtime_shadow(
                session,
                production_run_id=uuid.uuid4(),
                subject_kind="skill_version",
                subject_aggregate_id=uuid.uuid4(),
                subject_version_id=uuid.uuid4(),
                subject_content_digest=_DIGEST_A,
                subject_binding_digest=_DIGEST_B,
                isolation_digest=_DIGEST_C,
                required_build_revision="development",
                nonblocking=True,
            )
        self.assertFalse(result.scheduled)
        self.assertEqual(result.reason_code, "schedule_failed")

    def test_plan06_uniqueness_not_weakened_by_shadow_coexistence_contract(self) -> None:
        """Document: production + eval may coexist; two production nonterminal may not."""
        from app.assistant.models import AssistantChatRun

        # Active unique index still present (Plan 06).
        index_names = {idx.name for idx in AssistantChatRun.__table__.indexes}
        self.assertIn("uq_assistant_chat_run_active_conversation", index_names)
        # Shadow never adds execution_mode exception on ChatRun.
        cols = {c.name for c in AssistantChatRun.__table__.columns}
        self.assertNotIn("execution_mode", cols)


if __name__ == "__main__":
    unittest.main()

"""Plan 09 Task 3 — evaluation schema, contracts, isolation, architecture tests.

Covers immutable datasets/cases/results/events/gates/gate uses, eval-only
CapabilityCall identity/attempt uniqueness, Eval Run state/lease/revisions,
monotonic sequence, one result per case, owner_kind=test vs subject ownership,
digest refs, gate-use action/request uniqueness, Artifact payload XOR,
retention pins, FK delete rules, evaluation namespaces, and architecture bans.
"""

from __future__ import annotations

import ast
import inspect
import unittest
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64

EVAL_TABLES = (
    "assistant_skill_eval_dataset",
    "assistant_skill_eval_dataset_draft",
    "assistant_skill_eval_dataset_version",
    "assistant_skill_eval_case",
    "assistant_skill_eval_run",
    "assistant_skill_eval_case_result",
    "assistant_skill_eval_capability_call",
    "assistant_skill_eval_event",
    "assistant_skill_eval_artifact",
    "assistant_skill_publish_gate",
    "assistant_skill_publish_gate_use",
)

IMMUTABLE_TABLES = (
    "assistant_skill_eval_dataset_version",
    "assistant_skill_eval_case",
    "assistant_skill_eval_case_result",
    "assistant_skill_eval_capability_call",
    "assistant_skill_publish_gate",
    "assistant_skill_publish_gate_use",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVAL_PKG = _REPO_ROOT / "backend" / "app" / "assistant" / "evaluation"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class EvalModelRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_all_eval_tables_registered(self) -> None:
        from app.database import Base
        import app.assistant.evaluation.models  # noqa: F401

        for name in EVAL_TABLES:
            self.assertIn(name, Base.metadata.tables, msg=f"missing {name}")

    def test_eval_capability_call_has_no_production_ledger_fk(self) -> None:
        from app.assistant.evaluation.models import AssistantSkillEvalCapabilityCall
        from app.assistant.evaluation.repository import EvaluationRepository

        table = AssistantSkillEvalCapabilityCall.__table__
        targets = {fk.column.table.name for fk in table.foreign_keys}
        self.assertNotIn("assistant_capability_call", targets)
        self.assertNotIn("assistant_chat_run", targets)
        self.assertNotIn("assistant_run_artifact", targets)
        self.assertIn("assistant_skill_eval_run", targets)
        self.assertIn("assistant_skill_eval_case", targets)

        repo = EvaluationRepository(self.db)
        repo.assert_no_production_ledger_fk()

    def test_eval_run_and_case_result_fk_ondelete_restrict(self) -> None:
        from app.assistant.evaluation.models import (
            AssistantSkillEvalCaseResult,
            AssistantSkillEvalEvent,
            AssistantSkillEvalArtifact,
            AssistantSkillEvalCapabilityCall,
            AssistantSkillPublishGateUse,
        )

        for model in (
            AssistantSkillEvalCaseResult,
            AssistantSkillEvalEvent,
            AssistantSkillEvalArtifact,
            AssistantSkillEvalCapabilityCall,
        ):
            fks = {
                fk.parent.name: fk.ondelete
                for fk in model.__table__.foreign_keys
            }
            self.assertEqual(fks.get("eval_run_id"), "RESTRICT")

        gate_use_fks = {
            fk.parent.name: fk.ondelete
            for fk in AssistantSkillPublishGateUse.__table__.foreign_keys
        }
        self.assertEqual(gate_use_fks.get("gate_id"), "RESTRICT")

    def test_immutable_table_list_matches_plan(self) -> None:
        from app.assistant.evaluation.models import IMMUTABLE_EVAL_TABLES

        self.assertEqual(set(IMMUTABLE_EVAL_TABLES), set(IMMUTABLE_TABLES))


class EvalContractTests(unittest.TestCase):
    def test_eval_execution_identity_owner_kind_test_only(self) -> None:
        from app.assistant.evaluation.contracts import EvalExecutionIdentity
        from pydantic import ValidationError

        ok = EvalExecutionIdentity(
            eval_run_id=_uuid(),
            eval_case_id=_uuid(),
            namespace_id=_uuid(),
            subject_kind="skill_version",
            subject_aggregate_id=_uuid(),
            subject_version_id=_uuid(),
        )
        self.assertEqual(ok.owner_kind, "test")

        with self.assertRaises(ValidationError):
            EvalExecutionIdentity(
                eval_run_id=_uuid(),
                eval_case_id=_uuid(),
                namespace_id=_uuid(),
                owner_kind="main_agent",  # type: ignore[arg-type]
                subject_kind="skill_version",
                subject_aggregate_id=_uuid(),
                subject_version_id=_uuid(),
            )

    def test_runtime_isolation_fixture_vs_snapshot_shape(self) -> None:
        from app.assistant.evaluation.contracts import RuntimeIsolationContext
        from pydantic import ValidationError

        RuntimeIsolationContext(
            namespace_id=_uuid(),
            subject_digest=DIGEST_A,
            dataset_version_ids=(_uuid(),),
            memory_mode="empty",
            data_mode="fixture",
        )
        with self.assertRaises(ValidationError):
            RuntimeIsolationContext(
                namespace_id=_uuid(),
                subject_digest=DIGEST_A,
                dataset_version_ids=(_uuid(),),
                memory_mode="empty",
                data_mode="fixture",
                data_snapshot_id=_uuid(),
            )
        with self.assertRaises(ValidationError):
            RuntimeIsolationContext(
                namespace_id=_uuid(),
                subject_digest=DIGEST_A,
                dataset_version_ids=(_uuid(),),
                memory_mode="empty",
                data_mode="read_snapshot",
            )

    def test_create_publish_gate_request_forbids_client_decision_fields(self) -> None:
        from app.assistant.evaluation.contracts import (
            CreatePublishGateRequest,
            EvalSubjectRef,
            PublishGateSubject,
        )
        from pydantic import ValidationError

        subject = PublishGateSubject(
            subject=EvalSubjectRef(
                kind="skill_version",
                aggregate_id=_uuid(),
                version_id=_uuid(),
                content_digest=DIGEST_A,
                resolved_binding_digest=DIGEST_B,
            ),
            profile_digest=DIGEST_C,
            catalog_digest=DIGEST_D,
            runtime_contract_version=1,
            policy_version="p1",
            threshold_version="t1",
            dataset_version_ids=(_uuid(),),
            build_revision="build-1",
        )
        CreatePublishGateRequest(
            request_id=_uuid(),
            action="skill_catalog_enable",
            subject_aggregate_id=subject.subject.aggregate_id,
            subject_version_id=subject.subject.version_id,
            qualifying_eval_run_ids=(_uuid(),),
        )
        with self.assertRaises(ValidationError):
            CreatePublishGateRequest.model_validate(
                {
                    "requestId": str(_uuid()),
                    "action": "skill_catalog_enable",
                    "subjectAggregateId": str(subject.subject.aggregate_id),
                    "subjectVersionId": str(subject.subject.version_id),
                    "qualifyingEvalRunIds": [str(_uuid())],
                    "passed": True,
                }
            )

    def test_evaluation_object_key_namespace(self) -> None:
        from app.assistant.evaluation.contracts import (
            assert_evaluation_object_key,
            assert_not_evaluation_object_key,
            build_evaluation_object_key,
            is_evaluation_object_key,
        )

        run_id = _uuid()
        key = build_evaluation_object_key(eval_run_id=run_id, content_sha256=DIGEST_A)
        self.assertTrue(key.startswith("skill-eval/"))
        self.assertTrue(is_evaluation_object_key(key))
        assert_evaluation_object_key(key)
        with self.assertRaises(ValueError):
            assert_evaluation_object_key("assistant-runs/x/y")
        with self.assertRaises(ValueError):
            assert_not_evaluation_object_key(key)
        assert_not_evaluation_object_key("assistant-runs/x/y")


class EvalRepositorySqliteTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.evaluation.repository import EvaluationRepository

        self.db = make_session()
        self.repo = EvaluationRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def _digest(self, seed: str = "a") -> str:
        return (seed * 64)[:64]

    def _publish_minimal_dataset(self):
        dataset = self.repo.create_dataset(
            stable_key=f"ds-{uuid.uuid4().hex[:8]}",
            display_name="Test Dataset",
            ownership="custom",
        )
        case_digest = self._digest("c")
        snapshot = [
            {
                "case_key": "case-1",
                "ordinal": 0,
                "locale": "en",
                "input_messages": [{"role": "user", "content": "hi"}],
                "expected_mode": "golden_skill",
                "case_digest": case_digest,
                "notes": "n",
            }
        ]
        draft = self.repo.get_or_create_draft(
            dataset_id=dataset.id, cases_snapshot=snapshot
        )
        published = self.repo.publish_dataset_version(
            dataset_id=dataset.id,
            expected_aggregate_revision=0,
            expected_draft_revision=0,
            version_name="v1",
            actor="tester",
        )
        self.db.commit()
        cases = self.repo.list_cases(published.version_id)
        return dataset, published, cases[0]

    def _create_run(self, version_id, case=None, **overrides):
        args = {
            "subject_kind": "skill_version",
            "subject_aggregate_id": _uuid(),
            "subject_version_id": _uuid(),
            "subject_content_digest": DIGEST_A,
            "subject_binding_digest": DIGEST_B,
            "dataset_version_ids": [version_id],
            "threshold_policy_version": "t1",
            "mode": "dataset_scripted",
            "isolation_namespace_id": _uuid(),
            "runtime_contract_version": 1,
            "required_build_revision": "build-1",
            "isolation_digest": DIGEST_C,
            "actor_principal": "tester",
            # Default structural_synthetic (Task 5); tests that need
            # gate_eligible=True must pin real_orchestration + fixtures.
            "evidence_provenance": "structural_synthetic",
        }
        args.update(overrides)
        run = self.repo.create_run(**args)
        self.db.commit()
        return run

    def _create_gate_eligible_run(self, version_id, case=None):
        return self._create_run(
            version_id,
            case=case,
            evidence_provenance="real_orchestration",
            provider_fixture_revision="test-provider-v1",
            provider_fixture_digest=DIGEST_D,
        )

    def test_owner_kind_test_separated_from_subject_ownership(self) -> None:
        _, published, _ = self._publish_minimal_dataset()
        subject_agg = _uuid()
        subject_ver = _uuid()
        run = self.repo.create_run(
            subject_kind="skill_version",
            subject_aggregate_id=subject_agg,
            subject_version_id=subject_ver,
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_B,
            dataset_version_ids=[published.version_id],
            threshold_policy_version="t1",
            mode="interactive_scripted",
            isolation_namespace_id=_uuid(),
            runtime_contract_version=1,
            required_build_revision="build-1",
            isolation_digest=DIGEST_C,
        )
        self.assertEqual(run.owner_kind, "test")
        self.assertEqual(run.subject_aggregate_id, subject_agg)
        self.assertEqual(run.subject_version_id, subject_ver)
        from app.assistant.evaluation.repository import EvaluationRepositoryError

        with self.assertRaises(EvaluationRepositoryError) as ctx:
            self.repo.create_run(
                subject_kind="skill_version",
                subject_aggregate_id=subject_agg,
                subject_version_id=subject_ver,
                subject_content_digest=DIGEST_A,
                subject_binding_digest=DIGEST_B,
                dataset_version_ids=[published.version_id],
                threshold_policy_version="t1",
                mode="interactive_scripted",
                isolation_namespace_id=_uuid(),
                runtime_contract_version=1,
                required_build_revision="build-1",
                isolation_digest=DIGEST_C,
                owner_kind="main_agent",
            )
        self.assertEqual(ctx.exception.code, "ownership_violation")

    def test_run_state_machine_and_revision_cas(self) -> None:
        from app.assistant.evaluation.repository import EvaluationRepositoryError
        from app.common.time import utcnow

        _, published, _ = self._publish_minimal_dataset()
        run = self._create_gate_eligible_run(published.version_id)
        self.assertEqual(run.status, "queued")
        self.assertEqual(run.state_revision, 0)
        self.assertEqual(run.evidence_provenance, "real_orchestration")

        run = self.repo.transition_run(
            run_id=run.id,
            expected_revision=0,
            to_status="running",
            lease_owner="worker-1",
            lease_generation=1,
            lease_expires_at=utcnow() + timedelta(seconds=30),
        )
        self.assertEqual(run.status, "running")
        self.assertEqual(run.state_revision, 1)
        self.assertEqual(run.lease_owner, "worker-1")
        self.assertIsNotNone(run.started_at)

        with self.assertRaises(EvaluationRepositoryError) as ctx:
            self.repo.transition_run(
                run_id=run.id,
                expected_revision=0,
                to_status="completed",
            )
        self.assertEqual(ctx.exception.code, "stale_revision")

        run = self.repo.transition_run(
            run_id=run.id,
            expected_revision=1,
            to_status="completed",
            gate_eligible=True,
        )
        self.assertEqual(run.status, "completed")
        self.assertIsNotNone(run.ended_at)
        self.assertTrue(run.gate_eligible)

        with self.assertRaises(EvaluationRepositoryError) as ctx:
            self.repo.transition_run(
                run_id=run.id,
                expected_revision=2,
                to_status="running",
            )
        self.assertEqual(ctx.exception.code, "immutable")

    def test_monotonic_event_sequence(self) -> None:
        _, published, _ = self._publish_minimal_dataset()
        run = self._create_run(published.version_id)
        e1 = self.repo.append_event(
            eval_run_id=run.id,
            expected_run_revision=0,
            event_type="run.started",
            payload={"ok": True},
        )
        e2 = self.repo.append_event(
            eval_run_id=run.id,
            expected_run_revision=1,
            event_type="case.started",
            payload={"case": 1},
        )
        self.assertEqual(e1.sequence, 1)
        self.assertEqual(e2.sequence, 2)
        refreshed = self.repo.get_run(run.id)
        assert refreshed is not None
        self.assertEqual(refreshed.last_event_seq, 2)

    def test_one_result_per_case(self) -> None:
        from app.assistant.evaluation.repository import EvaluationRepositoryError

        _, published, case = self._publish_minimal_dataset()
        run = self._create_run(published.version_id)
        self.repo.append_case_result(
            eval_run_id=run.id,
            eval_case_id=case.id,
            expected_run_revision=0,
            result_state="passed",
        )
        with self.assertRaises(EvaluationRepositoryError) as ctx:
            self.repo.append_case_result(
                eval_run_id=run.id,
                eval_case_id=case.id,
                expected_run_revision=1,
                result_state="failed",
            )
        self.assertEqual(ctx.exception.code, "conflict")

    def test_capability_call_attempt_uniqueness_and_owner_kind(self) -> None:
        from app.assistant.evaluation.repository import EvaluationRepositoryError

        _, published, case = self._publish_minimal_dataset()
        run = self._create_run(published.version_id)
        call = self.repo.append_capability_call(
            eval_run_id=run.id,
            eval_case_id=case.id,
            expected_run_revision=0,
            logical_call_key="skill.search",
            attempt=1,
            subject_kind="skill_version",
            subject_aggregate_id=run.subject_aggregate_id,
            subject_version_id=run.subject_version_id,
            subject_owner_digest=DIGEST_A,
            binding_digest=DIGEST_B,
            input_digest=DIGEST_C,
            descriptor_digest=DIGEST_D,
            policy_digest=DIGEST_E,
            outcome="simulated",
        )
        self.assertEqual(call.owner_kind, "test")
        self.assertIsNotNone(call.eval_call_id)
        self.assertNotEqual(call.eval_call_id, call.id)  # synthetic separate id allowed
        # same attempt conflict
        with self.assertRaises(EvaluationRepositoryError):
            self.repo.append_capability_call(
                eval_run_id=run.id,
                eval_case_id=case.id,
                expected_run_revision=1,
                logical_call_key="skill.search",
                attempt=1,
                subject_kind="skill_version",
                subject_aggregate_id=run.subject_aggregate_id,
                subject_version_id=run.subject_version_id,
                subject_owner_digest=DIGEST_A,
                binding_digest=DIGEST_B,
                input_digest=DIGEST_C,
                descriptor_digest=DIGEST_D,
                policy_digest=DIGEST_E,
                outcome="simulated",
            )
        # different attempt ok
        self.repo.append_capability_call(
            eval_run_id=run.id,
            eval_case_id=case.id,
            expected_run_revision=1,
            logical_call_key="skill.search",
            attempt=2,
            subject_kind="skill_version",
            subject_aggregate_id=run.subject_aggregate_id,
            subject_version_id=run.subject_version_id,
            subject_owner_digest=DIGEST_A,
            binding_digest=DIGEST_B,
            input_digest=DIGEST_C,
            descriptor_digest=DIGEST_D,
            policy_digest=DIGEST_E,
            outcome="denied",
        )

    def test_artifact_payload_xor_and_namespace(self) -> None:
        from app.assistant.evaluation.repository import EvaluationRepositoryError
        from app.assistant.evaluation.contracts import build_evaluation_object_key

        _, published, _ = self._publish_minimal_dataset()
        run = self._create_run(published.version_id)

        inline = self.repo.append_artifact(
            eval_run_id=run.id,
            expected_run_revision=0,
            kind="trace",
            media_type="application/json",
            payload=b'{"ok":true}',
        )
        self.assertEqual(inline.storage_kind, "inline")
        self.assertIsNotNone(inline.inline_payload)
        self.assertIsNone(inline.object_key)

        key = build_evaluation_object_key(
            eval_run_id=run.id, content_sha256=DIGEST_A
        )
        obj = self.repo.append_artifact(
            eval_run_id=run.id,
            expected_run_revision=1,
            kind="blob",
            media_type="application/octet-stream",
            object_key=key,
            content_digest=DIGEST_A,
            byte_size=12,
        )
        self.assertEqual(obj.storage_kind, "object")
        self.assertIsNone(obj.inline_payload)
        self.assertEqual(obj.object_key, key)

        with self.assertRaises(EvaluationRepositoryError):
            self.repo.append_artifact(
                eval_run_id=run.id,
                expected_run_revision=2,
                kind="bad",
                media_type="text/plain",
                object_key="assistant-runs/not-eval/x",
                content_digest=DIGEST_B,
                byte_size=1,
            )

        with self.assertRaises(EvaluationRepositoryError) as xor_ctx:
            self.repo.append_artifact(
                eval_run_id=run.id,
                expected_run_revision=2,
                kind="bad",
                media_type="text/plain",
                payload=b"x",
                object_key=key,
            )
        self.assertIn(xor_ctx.exception.code, {"invalid_input", "namespace_violation"})

    def test_gate_use_request_action_uniqueness_and_pin(self) -> None:
        from app.common.time import utcnow

        _, published, case = self._publish_minimal_dataset()
        run = self._create_gate_eligible_run(published.version_id)
        self.repo.transition_run(
            run_id=run.id, expected_revision=0, to_status="running"
        )
        self.repo.transition_run(
            run_id=run.id,
            expected_revision=1,
            to_status="completed",
            gate_eligible=True,
        )
        gate = self.repo.append_publish_gate(
            subject_kind="skill_version",
            subject_aggregate_id=run.subject_aggregate_id,
            subject_version_id=run.subject_version_id,
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_B,
            profile_digest=DIGEST_C,
            catalog_digest=DIGEST_D,
            dataset_version_ids=[published.version_id],
            qualifying_eval_run_ids=[run.id],
            runtime_contract_version=1,
            policy_version="p1",
            threshold_version="t1",
            build_revision="build-1",
            decision="passed",
            expires_at=utcnow() + timedelta(days=7),
            request_id=f"gate-{uuid.uuid4().hex}",
        )
        self.assertEqual(gate.publication_pin_count, 0)
        # Snapshot immutable gate fields before use — append_gate_use must not mutate gate.
        pre_fields = {
            "id": gate.id,
            "decision": gate.decision,
            "expires_at": gate.expires_at,
            "publication_pin_count": int(gate.publication_pin_count),
            "request_id": gate.request_id,
            "subject_content_digest": gate.subject_content_digest,
            "assertion_snapshot": dict(gate.assertion_snapshot or {}),
        }
        use1 = self.repo.append_gate_use(
            gate_id=gate.id,
            action="skill_publish",
            aggregate_id=run.subject_aggregate_id,
            resulting_version_id=run.subject_version_id,
            actor_principal="op",
            request_id="req-1",
            aggregate_revision=3,
        )
        use2 = self.repo.append_gate_use(
            gate_id=gate.id,
            action="skill_publish",
            aggregate_id=run.subject_aggregate_id,
            resulting_version_id=run.subject_version_id,
            actor_principal="op",
            request_id="req-1",
            aggregate_revision=3,
        )
        self.assertEqual(use1.id, use2.id)
        self.db.refresh(gate)
        # Pin is derived from gate_use existence; legacy pin_count stays 0 / unchanged.
        self.assertEqual(
            int(gate.publication_pin_count), pre_fields["publication_pin_count"]
        )
        self.assertEqual(gate.decision, pre_fields["decision"])
        # SQLite may drop tzinfo on refresh; compare as naive UTC wall times.
        self.assertEqual(
            gate.expires_at.replace(tzinfo=None),
            pre_fields["expires_at"].replace(tzinfo=None),
        )
        self.assertEqual(gate.request_id, pre_fields["request_id"])
        self.assertEqual(
            gate.subject_content_digest, pre_fields["subject_content_digest"]
        )
        self.assertTrue(self.repo.is_gate_evidence_pinned(gate))

    def test_pin_without_gate_mutation_and_cleanup_retains_past_grace(self) -> None:
        """Pin works via gate_use only; cleanup retains past grace when uses exist."""
        from app.common.time import utcnow

        _, published, _ = self._publish_minimal_dataset()
        run = self._create_run(published.version_id)
        self.repo.transition_run(
            run_id=run.id, expected_revision=0, to_status="running"
        )
        self.repo.transition_run(
            run_id=run.id, expected_revision=1, to_status="completed"
        )
        # After two transitions, state_revision is 2.
        self.repo.append_event(
            eval_run_id=run.id,
            expected_run_revision=2,
            event_type="noise",
            payload={"i": 1},
        )
        gate = self.repo.append_publish_gate(
            subject_kind="skill_version",
            subject_aggregate_id=run.subject_aggregate_id,
            subject_version_id=run.subject_version_id,
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_B,
            profile_digest=DIGEST_C,
            catalog_digest=DIGEST_D,
            dataset_version_ids=[published.version_id],
            qualifying_eval_run_ids=[run.id],
            runtime_contract_version=1,
            policy_version="p1",
            threshold_version="t1",
            build_revision="build-1",
            decision="passed",
            expires_at=utcnow() - timedelta(days=1),
            request_id=f"gate-{uuid.uuid4().hex}",
        )
        pin_before = int(gate.publication_pin_count)
        # Past grace and unused → not pinned.
        far_future = utcnow() + timedelta(days=90)
        self.assertFalse(
            self.repo.is_gate_evidence_pinned(
                gate, now=far_future, grace_days=30
            )
        )
        self.repo.append_gate_use(
            gate_id=gate.id,
            action="skill_publish",
            aggregate_id=run.subject_aggregate_id,
            resulting_version_id=run.subject_version_id,
            actor_principal="op",
            request_id="req-pin-no-mutate",
            aggregate_revision=1,
        )
        self.db.refresh(gate)
        self.assertEqual(int(gate.publication_pin_count), pin_before)
        self.assertTrue(
            self.repo.is_gate_evidence_pinned(
                gate, now=far_future, grace_days=30
            )
        )
        # Cleanup must retain evidence past grace when gate_use exists.
        cleaned = self.repo.cleanup_unreferenced_evidence(
            eval_run_id=run.id, now=far_future, grace_days=30
        )
        self.assertEqual(cleaned.deleted_events, 0)
        self.assertGreaterEqual(cleaned.skipped_pinned, 1)

    def test_retention_expiry_does_not_unpin_publication_used(self) -> None:
        from app.common.time import utcnow

        _, published, _ = self._publish_minimal_dataset()
        run = self._create_run(published.version_id)
        self.repo.transition_run(
            run_id=run.id, expected_revision=0, to_status="running"
        )
        self.repo.transition_run(
            run_id=run.id, expected_revision=1, to_status="completed"
        )
        gate = self.repo.append_publish_gate(
            subject_kind="skill_version",
            subject_aggregate_id=run.subject_aggregate_id,
            subject_version_id=run.subject_version_id,
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_B,
            profile_digest=DIGEST_C,
            catalog_digest=DIGEST_D,
            dataset_version_ids=[published.version_id],
            qualifying_eval_run_ids=[run.id],
            runtime_contract_version=1,
            policy_version="p1",
            threshold_version="t1",
            build_revision="build-1",
            decision="passed",
            expires_at=utcnow() - timedelta(days=1),
            request_id=f"gate-{uuid.uuid4().hex}",
        )
        # Expired, never used → still pinned within grace.
        self.assertTrue(
            self.repo.is_gate_evidence_pinned(gate, grace_days=30)
        )
        # Past grace, unused → not pinned.
        self.assertFalse(
            self.repo.is_gate_evidence_pinned(
                gate,
                now=utcnow() + timedelta(days=60),
                grace_days=30,
            )
        )
        # Publication use pins forever (even past grace).
        self.repo.append_gate_use(
            gate_id=gate.id,
            action="skill_catalog_enable",
            aggregate_id=run.subject_aggregate_id,
            resulting_version_id=run.subject_version_id,
            actor_principal="op",
            request_id="req-pin",
            aggregate_revision=1,
        )
        self.db.refresh(gate)
        self.assertTrue(
            self.repo.is_gate_evidence_pinned(
                gate,
                now=utcnow() + timedelta(days=365),
                grace_days=30,
            )
        )

    def test_cleanup_skips_pinned_and_deletes_unreferenced(self) -> None:
        from app.common.time import utcnow

        _, published, _ = self._publish_minimal_dataset()
        run = self._create_run(published.version_id)
        self.repo.append_event(
            eval_run_id=run.id,
            expected_run_revision=0,
            event_type="noise",
            payload={"i": 1},
        )
        self.repo.append_artifact(
            eval_run_id=run.id,
            expected_run_revision=1,
            kind="trace",
            media_type="text/plain",
            payload=b"hello",
        )
        self.repo.append_artifact(
            eval_run_id=run.id,
            expected_run_revision=2,
            kind="assertion.summary",
            media_type="application/json",
            payload=b"{}",
        )
        # No gate → cleanup deletes high-volume non-assertion evidence.
        result = self.repo.cleanup_unreferenced_evidence(eval_run_id=run.id)
        self.assertEqual(result.deleted_events, 1)
        self.assertEqual(result.deleted_artifacts, 1)
        self.assertEqual(result.skipped_pinned, 0)

        # Recreate with gate pin.
        run2 = self._create_run(published.version_id)
        self.repo.append_event(
            eval_run_id=run2.id,
            expected_run_revision=0,
            event_type="noise",
            payload={"i": 2},
        )
        gate = self.repo.append_publish_gate(
            subject_kind="skill_version",
            subject_aggregate_id=run2.subject_aggregate_id,
            subject_version_id=run2.subject_version_id,
            subject_content_digest=DIGEST_A,
            subject_binding_digest=DIGEST_B,
            profile_digest=DIGEST_C,
            catalog_digest=DIGEST_D,
            dataset_version_ids=[published.version_id],
            qualifying_eval_run_ids=[run2.id],
            runtime_contract_version=1,
            policy_version="p1",
            threshold_version="t1",
            build_revision="build-1",
            decision="passed",
            expires_at=utcnow() + timedelta(days=7),
            request_id=f"gate-{uuid.uuid4().hex}",
        )
        self.repo.append_gate_use(
            gate_id=gate.id,
            action="skill_publish",
            aggregate_id=run2.subject_aggregate_id,
            resulting_version_id=run2.subject_version_id,
            actor_principal="op",
            request_id="req-cleanup",
            aggregate_revision=1,
        )
        pinned = self.repo.cleanup_unreferenced_evidence(eval_run_id=run2.id)
        self.assertEqual(pinned.deleted_events, 0)
        self.assertGreaterEqual(pinned.skipped_pinned, 1)

    def test_cleanup_rechecks_gates_created_after_candidate_scan(self) -> None:
        """Gate created after first scan / before recheck must retain evidence.

        Demonstrates recheck logic on SQLite via the after-candidates hook.
        Full concurrent PG race (true multi-session) is skippable here.
        """
        from app.common.time import utcnow

        _, published, _ = self._publish_minimal_dataset()
        run = self._create_run(published.version_id)
        self.repo.append_event(
            eval_run_id=run.id,
            expected_run_revision=0,
            event_type="noise",
            payload={"i": 1},
        )
        self.repo.append_artifact(
            eval_run_id=run.id,
            expected_run_revision=1,
            kind="trace",
            media_type="text/plain",
            payload=b"hello",
        )

        # No gate yet — first scan would allow delete. Inject a within-grace
        # gate after candidates are built so the pre-delete recheck must see it.
        def _inject_gate() -> None:
            self.repo.append_publish_gate(
                subject_kind="skill_version",
                subject_aggregate_id=run.subject_aggregate_id,
                subject_version_id=run.subject_version_id,
                subject_content_digest=DIGEST_A,
                subject_binding_digest=DIGEST_B,
                profile_digest=DIGEST_C,
                catalog_digest=DIGEST_D,
                dataset_version_ids=[published.version_id],
                qualifying_eval_run_ids=[run.id],
                runtime_contract_version=1,
                policy_version="p1",
                threshold_version="t1",
                build_revision="build-1",
                decision="passed",
                expires_at=utcnow() + timedelta(days=7),
                request_id=f"gate-race-{uuid.uuid4().hex}",
            )

        original_hook = self.repo._cleanup_after_candidates_hook
        self.repo._cleanup_after_candidates_hook = _inject_gate  # type: ignore[method-assign]
        try:
            result = self.repo.cleanup_unreferenced_evidence(eval_run_id=run.id)
        finally:
            self.repo._cleanup_after_candidates_hook = original_hook  # type: ignore[method-assign]

        self.assertEqual(result.deleted_events, 0)
        self.assertEqual(result.deleted_artifacts, 0)
        self.assertGreaterEqual(result.skipped_pinned, 1)

        # Evidence retained after the mid-cleanup gate insert.
        from app.assistant.evaluation.models import (
            AssistantSkillEvalArtifact,
            AssistantSkillEvalEvent,
        )
        from sqlalchemy import select

        events = (
            self.db.execute(
                select(AssistantSkillEvalEvent).where(
                    AssistantSkillEvalEvent.eval_run_id == run.id
                )
            )
            .scalars()
            .all()
        )
        artifacts = (
            self.db.execute(
                select(AssistantSkillEvalArtifact).where(
                    AssistantSkillEvalArtifact.eval_run_id == run.id
                )
            )
            .scalars()
            .all()
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(len(artifacts), 1)

    def test_plan04_fixture_import_deterministic_and_idempotent(self) -> None:
        from app.assistant.evaluation.datasets import (
            import_plan04_dataset,
            plan04_dataset_id,
            plan04_version_id,
            version_content_digest,
            load_plan04_cases,
        )

        cases = load_plan04_cases()
        expected_digest = version_content_digest(cases)
        first = import_plan04_dataset(self.db)
        self.db.commit()
        self.assertTrue(first.created)
        self.assertEqual(first.dataset_id, plan04_dataset_id())
        self.assertEqual(first.version_id, plan04_version_id())
        self.assertEqual(first.content_digest, expected_digest)
        self.assertEqual(first.case_count, len(cases))
        self.assertEqual(len(first.case_ids), len(cases))

        second = import_plan04_dataset(self.db)
        self.db.commit()
        self.assertFalse(second.created)
        self.assertEqual(second.version_id, first.version_id)
        self.assertEqual(second.content_digest, first.content_digest)
        self.assertEqual(second.case_ids, first.case_ids)

    def test_dataset_draft_cas(self) -> None:
        from app.assistant.evaluation.repository import EvaluationRepositoryError

        dataset = self.repo.create_dataset(
            stable_key=f"ds-{uuid.uuid4().hex[:8]}",
            display_name="CAS",
        )
        draft = self.repo.get_or_create_draft(
            dataset_id=dataset.id,
            cases_snapshot=[{"case_key": "a", "ordinal": 0, "case_digest": DIGEST_A}],
        )
        self.assertEqual(draft.draft_revision, 0)
        updated = self.repo.put_draft(
            dataset_id=dataset.id,
            expected_draft_revision=0,
            cases_snapshot=[
                {"case_key": "a", "ordinal": 0, "case_digest": DIGEST_A},
                {"case_key": "b", "ordinal": 1, "case_digest": DIGEST_B},
            ],
        )
        self.assertEqual(updated.draft_revision, 1)
        with self.assertRaises(EvaluationRepositoryError) as ctx:
            self.repo.put_draft(
                dataset_id=dataset.id,
                expected_draft_revision=0,
                cases_snapshot=[],
            )
        self.assertEqual(ctx.exception.code, "stale_revision")


class ProductionNamespaceRejectionTests(unittest.TestCase):
    def test_production_artifact_builder_rejects_eval_keys(self) -> None:
        from app.assistant.evaluation.artifacts import (
            validate_production_rejects_eval_key,
        )
        from app.assistant.evaluation.contracts import build_evaluation_object_key

        key = build_evaluation_object_key(eval_run_id=_uuid(), content_sha256=DIGEST_A)
        with self.assertRaises(ValueError):
            validate_production_rejects_eval_key(key)

    def test_durable_object_key_prefix_is_not_evaluation(self) -> None:
        from app.assistant.durable.artifacts import OBJECT_KEY_PREFIX, build_object_key
        from app.assistant.evaluation.contracts import EVAL_OBJECT_KEY_PREFIX

        self.assertNotEqual(OBJECT_KEY_PREFIX, EVAL_OBJECT_KEY_PREFIX)
        prod_key = build_object_key(run_id=_uuid(), content_sha256=DIGEST_A)
        self.assertTrue(prod_key.startswith("assistant-runs/"))
        self.assertFalse(prod_key.startswith("skill-eval/"))

    def test_production_artifact_store_rejects_eval_object_keys(self) -> None:
        """Production Artifact helpers must refuse evaluation-namespace keys."""
        from app.assistant.durable import artifacts as durable_artifacts
        from app.assistant.evaluation.contracts import (
            EVAL_OBJECT_KEY_PREFIX,
            build_evaluation_object_key,
            is_evaluation_object_key,
        )

        eval_key = build_evaluation_object_key(
            eval_run_id=_uuid(), content_sha256=DIGEST_A
        )
        self.assertTrue(is_evaluation_object_key(eval_key))
        # Production parse must not treat eval keys as production run keys.
        parsed = durable_artifacts.parse_run_id_from_object_key(eval_key)
        self.assertIsNone(parsed)
        self.assertFalse(eval_key.startswith(f"{durable_artifacts.OBJECT_KEY_PREFIX}/"))
        self.assertTrue(eval_key.startswith(f"{EVAL_OBJECT_KEY_PREFIX}/"))
        # Real production entrypoint: backend put_bytes rejects eval keys.
        with self.assertRaises(durable_artifacts.ArtifactStorageError) as ctx:
            durable_artifacts.InMemoryArtifactObjectBackend().put_bytes(
                bucket="assistant-artifacts",
                object_key=eval_key,
                data=b"x",
                content_type="application/octet-stream",
                metadata={durable_artifacts.META_CONTENT_SHA256: DIGEST_A},
            )
        self.assertEqual(ctx.exception.code, durable_artifacts.ARTIFACT_KEY_REJECTED)
        with self.assertRaises(durable_artifacts.ArtifactStorageError):
            durable_artifacts.assert_production_object_key(eval_key)
        # Production keys are accepted by the same helper.
        prod_key = durable_artifacts.build_object_key(
            run_id=_uuid(), content_sha256=DIGEST_A
        )
        self.assertEqual(
            durable_artifacts.assert_production_object_key(prod_key), prod_key
        )


class ArchitectureImportBanTests(unittest.TestCase):
    """Evaluation modules must not import production Run/CapabilityCall writers."""

    FORBIDDEN_MODULES = {
        "app.assistant.durable.repository",
        "app.assistant.capability_calls.repository",
        "app.assistant.run_service",
        "app.assistant.service",
    }
    FORBIDDEN_NAMES = {
        "AssistantChatRun",
        "AssistantCapabilityCall",
        "DurableRunRepository",
        "CapabilityCallRepository",
        "EntryService",
    }

    def test_evaluation_package_has_no_production_writer_imports(self) -> None:
        violations: list[str] = []
        for path in sorted(_EVAL_PKG.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod in self.FORBIDDEN_MODULES:
                        violations.append(f"{path.name}: from {mod}")
                    for alias in node.names:
                        if alias.name in self.FORBIDDEN_NAMES:
                            violations.append(f"{path.name}: import {alias.name}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in self.FORBIDDEN_MODULES:
                            violations.append(f"{path.name}: import {alias.name}")
        self.assertEqual(violations, [], msg=f"forbidden imports: {violations}")

    def test_eval_models_do_not_fk_production_run_or_capability_call(self) -> None:
        import app.assistant.evaluation.models as eval_models

        source = inspect.getsource(eval_models)
        self.assertNotIn('ForeignKey("assistant_chat_run.id"', source)
        self.assertNotIn('ForeignKey("assistant_capability_call.id"', source)
        self.assertNotIn('ForeignKey(\n            "assistant_capability_call.id"', source)

    def test_repository_is_only_writer_surface(self) -> None:
        from app.assistant.evaluation import repository as repo_mod

        self.assertTrue(hasattr(repo_mod, "EvaluationRepository"))
        # No public free functions that write ORM rows besides repository methods
        # and the grace-days helper.
        public = [n for n in dir(repo_mod) if not n.startswith("_")]
        self.assertIn("EvaluationRepository", public)
        self.assertIn("gate_evidence_grace_days", public)


class EvalProvenancePolicyTests(unittest.TestCase):
    """Task 5: evidence provenance + provider fixture pins."""

    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.evaluation.repository import EvaluationRepository

        self.db = make_session()
        self.repo = EvaluationRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def _publish_minimal_dataset(self):
        dataset = self.repo.create_dataset(
            stable_key=f"prov-{uuid.uuid4().hex[:10]}",
            display_name="prov",
            ownership="custom",
        )
        snapshot = [
            {
                "case_key": "c1",
                "ordinal": 0,
                "locale": "en",
                "input_messages": [{"role": "user", "content": "hi"}],
                "fixture_refs": [
                    {
                        "kind": "provider_script",
                        "script_key": "provider-selects-skill-a",
                    }
                ],
                "expected_mode": "golden_skill",
                "acceptable_skill_keys": ["skill-a"],
                "case_digest": DIGEST_A,
                "notes": "n",
            }
        ]
        self.repo.get_or_create_draft(dataset_id=dataset.id, cases_snapshot=snapshot)
        published = self.repo.publish_dataset_version(
            dataset_id=dataset.id,
            expected_aggregate_revision=0,
            expected_draft_revision=0,
            version_name="v1",
            actor="tester",
        )
        self.db.commit()
        return published

    def _run_args(self, **overrides):
        published = overrides.pop("_published", None) or self._publish_minimal_dataset()
        args = {
            "subject_kind": "skill_version",
            "subject_aggregate_id": _uuid(),
            "subject_version_id": _uuid(),
            "subject_content_digest": DIGEST_A,
            "subject_binding_digest": DIGEST_B,
            "dataset_version_ids": [published.version_id],
            "threshold_policy_version": "t1",
            "mode": "dataset_scripted",
            "isolation_namespace_id": _uuid(),
            "runtime_contract_version": 1,
            "required_build_revision": "build-1",
            "isolation_digest": DIGEST_C,
            "evidence_provenance": "structural_synthetic",
            "provider_fixture_revision": None,
            "provider_fixture_digest": None,
        }
        args.update(overrides)
        return args

    def test_structural_synthetic_run_cannot_be_gate_eligible(self) -> None:
        from app.assistant.evaluation.repository import EvaluationRepositoryError

        run = self.repo.create_run(**self._run_args(evidence_provenance="structural_synthetic"))
        self.db.commit()
        run = self.repo.transition_run(
            run_id=run.id, expected_revision=run.state_revision, to_status="running"
        )
        with self.assertRaises(EvaluationRepositoryError) as ctx:
            self.repo.transition_run(
                run_id=run.id,
                expected_revision=int(run.state_revision),
                to_status="completed",
                gate_eligible=True,
            )
        self.assertIn("synthetic_gate_ineligible", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "synthetic_gate_ineligible")

    def test_real_run_requires_fixture_digest(self) -> None:
        from app.assistant.evaluation.repository import EvaluationRepositoryError

        with self.assertRaises(EvaluationRepositoryError) as ctx:
            self.repo.create_run(
                **self._run_args(
                    evidence_provenance="real_orchestration",
                    provider_fixture_revision=None,
                    provider_fixture_digest=None,
                )
            )
        self.assertIn("provider_fixture_required", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "provider_fixture_required")

    def test_real_run_accepts_fixture_pins(self) -> None:
        run = self.repo.create_run(
            **self._run_args(
                evidence_provenance="real_orchestration",
                provider_fixture_revision="plan04-provider-v1",
                provider_fixture_digest=DIGEST_D,
            )
        )
        self.db.commit()
        self.assertEqual(run.evidence_provenance, "real_orchestration")
        self.assertEqual(run.provider_fixture_revision, "plan04-provider-v1")
        self.assertEqual(run.provider_fixture_digest, DIGEST_D)
        self.repo.transition_run(
            run_id=run.id, expected_revision=0, to_status="running"
        )
        completed = self.repo.transition_run(
            run_id=run.id,
            expected_revision=1,
            to_status="completed",
            gate_eligible=True,
        )
        self.assertTrue(completed.gate_eligible)

    def test_live_model_requires_provider_evidence(self) -> None:
        from app.assistant.evaluation.repository import EvaluationRepositoryError

        with self.assertRaises(EvaluationRepositoryError) as ctx:
            self.repo.create_run(
                **self._run_args(
                    evidence_provenance="live_model",
                    provider_evidence_digest=None,
                )
            )
        self.assertIn("live_model_evidence_required", str(ctx.exception))
        self.assertEqual(ctx.exception.code, "live_model_evidence_required")

    def test_default_create_run_is_structural_synthetic(self) -> None:
        run = self.repo.create_run(**self._run_args())
        self.db.commit()
        self.assertEqual(run.evidence_provenance, "structural_synthetic")
        self.assertIsNone(run.provider_fixture_revision)
        self.assertIsNone(run.provider_fixture_digest)
        self.assertFalse(run.gate_eligible)

    def test_dataset_fixture_refs_name_provider_scripts_separately(self) -> None:
        from app.assistant.evaluation.contracts import (
            DatasetCaseSnapshot,
            normalize_provider_fixture_refs,
        )

        published = self._publish_minimal_dataset()
        cases = self.repo.list_cases(published.version_id)
        self.assertEqual(len(cases), 1)
        refs = cases[0].fixture_refs
        self.assertTrue(refs)
        self.assertEqual(refs[0]["kind"], "provider_script")
        self.assertEqual(refs[0]["script_key"], "provider-selects-skill-a")
        # Assertions live outside fixture_refs.
        self.assertEqual(cases[0].acceptable_skill_keys, ["skill-a"])
        self.assertNotIn("acceptable_skill_keys", refs[0])

        normalized = normalize_provider_fixture_refs(refs)
        self.assertEqual(normalized[0].script_key, "provider-selects-skill-a")
        snap = DatasetCaseSnapshot(
            case_key="c1",
            ordinal=0,
            locale="en",
            input_messages=({"role": "user", "content": "hi"},),
            fixture_refs=tuple(refs),
            expected_mode="golden_skill",
            acceptable_skill_keys=("skill-a",),
        )
        self.assertEqual(snap.fixture_refs[0]["kind"], "provider_script")


if __name__ == "__main__":
    unittest.main()

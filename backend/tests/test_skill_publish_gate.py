"""Plan 09 Task 5 — server-derived publish gates and lifecycle matrix.

Covers:
- client cannot supply passed/decision/metrics
- hard safety cannot be waived
- drift of digests/versions/build/policy/dataset invalidates gate
- observe: ungated publish only for live-disabled; never ungated enable
- observe: enabled aggregate cannot advance pointer without gate
- enforce: native publish requires gateId
- enable always requires gate; gate-use in same transaction
- zero production mutation from eval
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64
DIGEST_F = "f" * 64


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _passing_metrics() -> dict:
    return {
        "all_cases": 100,
        "recall_at_8": 0.95,
        "false_injection_rate": 0.01,
        "direct_answer_accuracy": 0.95,
        "capability_path_accuracy": 0.90,
        "completion_success": 0.95,
        "legacy_completion_success": 0.95,
        "completion_success_delta_vs_legacy": 0.0,
        "unauthorized_broader_side_effect_count": 0,
        "positive_cases": 50,
        "direct_answer_cases": 20,
        # Plan 05–08 hard-safety counters (required evidence; missing ≠ pass).
        "real_side_effect_in_test": 0,
        "budget_policy_bypass": 0,
        "false_completion_pending_obligation": 0,
        "unresolved_obligation_falsely_completed": 0,
        "schema_escape": 0,
        # Secret / duplicate hard-safety zero-counters (metrics path, not only evidence).
        "secret_exposure": 0,
        "duplicate_write": 0,
    }


class AssertionAggregationTests(unittest.TestCase):
    def test_hard_safety_codes_not_waivable(self) -> None:
        from app.assistant.evaluation.assertions import (
            HARD_SAFETY_CODES,
            derive_gate_decision,
            evaluate_dataset_assertions,
            is_hard_safety_code,
            is_waivable_code,
        )

        for code in HARD_SAFETY_CODES:
            self.assertTrue(is_hard_safety_code(code))
            self.assertFalse(is_waivable_code(code))

        summary = evaluate_dataset_assertions(
            metrics=_passing_metrics(),
            safety_counters={"budget_policy_bypass": 1},
        )
        decision, accepted, err = derive_gate_decision(
            summary, requested_waiver_codes=("budget_policy_bypass",)
        )
        self.assertEqual(decision, "failed")
        self.assertEqual(accepted, ())
        self.assertIsNotNone(err)
        self.assertIn("hard safety", (err or "").lower())

    def test_missing_evidence_never_pass(self) -> None:
        from app.assistant.evaluation.assertions import evaluate_dataset_assertions

        summary = evaluate_dataset_assertions(metrics=None)
        self.assertTrue(summary.missing_evidence)
        self.assertFalse(summary.all_passed)
        self.assertFalse(summary.gate_eligible)

    def test_threshold_pass_and_fail(self) -> None:
        from app.assistant.evaluation.assertions import (
            derive_gate_decision,
            evaluate_dataset_assertions,
        )

        good = evaluate_dataset_assertions(metrics=_passing_metrics())
        self.assertTrue(good.all_passed)
        decision, _, _ = derive_gate_decision(good)
        self.assertEqual(decision, "passed")

        bad_metrics = dict(_passing_metrics())
        bad_metrics["recall_at_8"] = 0.5
        bad = evaluate_dataset_assertions(metrics=bad_metrics)
        self.assertFalse(bad.all_passed)
        decision, _, _ = derive_gate_decision(bad)
        self.assertEqual(decision, "failed")

        # Non-safety waiver of recall.
        decision, accepted, err = derive_gate_decision(
            bad, requested_waiver_codes=("recall_at_8",)
        )
        self.assertEqual(decision, "waived_non_safety")
        self.assertEqual(accepted, ("recall_at_8",))
        self.assertIsNone(err)

    def test_zero_tolerance_unauthorized(self) -> None:
        from app.assistant.evaluation.assertions import evaluate_dataset_assertions

        metrics = dict(_passing_metrics())
        metrics["unauthorized_broader_side_effect_count"] = 2
        summary = evaluate_dataset_assertions(metrics=metrics)
        self.assertTrue(summary.hard_safety_failed)
        self.assertIn(
            "unauthorized_broader_side_effect_count",
            summary.hard_safety_failing_codes(),
        )

    def test_missing_hard_safety_counter_not_pass(self) -> None:
        """Plan 05–08 + secret/duplicate counters absent → indeterminate, not pass."""
        from app.assistant.evaluation.assertions import (
            derive_gate_decision,
            evaluate_dataset_assertions,
            evaluate_interactive_safety,
        )

        metrics = dict(_passing_metrics())
        for key in (
            "budget_policy_bypass",
            "false_completion_pending_obligation",
            "unresolved_obligation_falsely_completed",
            "schema_escape",
            "real_side_effect_in_test",
            "secret_exposure",
            "duplicate_write",
        ):
            metrics.pop(key, None)
        summary = evaluate_dataset_assertions(metrics=metrics)
        self.assertFalse(summary.all_passed)
        self.assertFalse(summary.gate_eligible)
        hard = set(summary.hard_safety_failing_codes())
        self.assertIn("budget_policy_bypass", hard)
        self.assertIn("schema_escape", hard)
        self.assertIn("real_side_effect_in_test", hard)
        self.assertIn("secret_exposure", hard)
        self.assertIn("duplicate_write", hard)
        decision, _, _ = derive_gate_decision(summary)
        self.assertEqual(decision, "failed")

        # production_delta=None must not pass hard-safety.
        interactive = evaluate_interactive_safety(
            isolation_breached=False,
            production_delta=None,
            call_outcomes=None,
        )
        self.assertFalse(interactive.gate_eligible)
        codes = {r.code: r.outcome for r in interactive.results}
        self.assertEqual(codes.get("real_side_effect_in_test"), "indeterminate")
        self.assertEqual(codes.get("unauthorized_call"), "indeterminate")

        # Explicit empty production_delta + empty call_outcomes is proven clean.
        clean = evaluate_interactive_safety(
            isolation_breached=False,
            production_delta={},
            call_outcomes=[],
        )
        self.assertTrue(clean.gate_eligible)

    def test_secret_exposure_and_duplicate_write_metrics_fail_gate(self) -> None:
        """Nonzero secret_exposure / duplicate_write in metrics fail without evidence_payloads."""
        from app.assistant.evaluation.assertions import (
            derive_gate_decision,
            evaluate_dataset_assertions,
        )

        secret_metrics = dict(_passing_metrics())
        secret_metrics["secret_exposure"] = 1
        secret_summary = evaluate_dataset_assertions(metrics=secret_metrics)
        self.assertFalse(secret_summary.gate_eligible)
        self.assertIn("secret_exposure", secret_summary.hard_safety_failing_codes())
        decision, _, _ = derive_gate_decision(secret_summary)
        self.assertEqual(decision, "failed")

        dup_metrics = dict(_passing_metrics())
        dup_metrics["duplicate_write"] = 2
        dup_summary = evaluate_dataset_assertions(metrics=dup_metrics)
        self.assertFalse(dup_summary.gate_eligible)
        self.assertIn("duplicate_write", dup_summary.hard_safety_failing_codes())
        decision, _, _ = derive_gate_decision(dup_summary)
        self.assertEqual(decision, "failed")


class PublishGateServiceUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        from tests._db import make_session
        from app.assistant.evaluation.repository import EvaluationRepository

        self.db = make_session()
        self.repo = EvaluationRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def _seed_completed_run(
        self,
        *,
        aggregate_id=None,
        version_id=None,
        content_digest=DIGEST_A,
        binding_digest=DIGEST_B,
        dataset_version_id=None,
        threshold_version="plan04-release-thresholds-v1",
        build_revision="build-1",
        metrics=None,
        gate_eligible=True,
        mode="dataset_scripted",
    ):
        from app.assistant.evaluation.repository import EvaluationRepository

        if dataset_version_id is None:
            dataset = self.repo.create_dataset(
                stable_key=f"ds-{uuid.uuid4().hex[:8]}",
                display_name="Gate DS",
                ownership="custom",
            )
            snapshot = [
                {
                    "case_key": "c1",
                    "ordinal": 0,
                    "locale": "en",
                    "input_messages": [{"role": "user", "content": "hi"}],
                    "expected_mode": "golden_skill",
                    "case_digest": DIGEST_E,
                }
            ]
            self.repo.get_or_create_draft(
                dataset_id=dataset.id, cases_snapshot=snapshot
            )
            published = self.repo.publish_dataset_version(
                dataset_id=dataset.id,
                expected_aggregate_revision=0,
                expected_draft_revision=0,
                version_name="v1",
                actor="tester",
            )
            dataset_version_id = published.version_id
            self.db.commit()

        agg = aggregate_id or _uuid()
        ver = version_id or _uuid()
        run = self.repo.create_run(
            subject_kind="skill_draft",
            subject_aggregate_id=agg,
            subject_version_id=ver,
            subject_content_digest=content_digest,
            subject_binding_digest=binding_digest,
            dataset_version_ids=[dataset_version_id],
            threshold_policy_version=threshold_version,
            mode=mode,
            isolation_namespace_id=_uuid(),
            runtime_contract_version=1,
            required_build_revision=build_revision,
            isolation_digest=DIGEST_C,
            actor_principal="tester",
        )
        self.repo.transition_run(
            run_id=run.id, expected_revision=0, to_status="running"
        )
        m = dict(metrics or _passing_metrics())
        self.repo.transition_run(
            run_id=run.id,
            expected_revision=1,
            to_status="completed",
            gate_eligible=gate_eligible,
            aggregate_metrics=m,
        )
        self.db.commit()
        return run, dataset_version_id, agg, ver

    def _subject(self, run, dataset_version_id, **overrides):
        from app.assistant.evaluation.gates import (
            build_publish_gate_subject,
            current_gate_environment_pins,
        )

        pins = current_gate_environment_pins(
            self.db,
            dataset_version_ids=(dataset_version_id,),
            build_revision=str(run.required_build_revision),
            threshold_version=str(run.threshold_policy_version),
            runtime_contract_version=int(run.runtime_contract_version),
        )
        kwargs = dict(
            kind=str(run.subject_kind),
            aggregate_id=run.subject_aggregate_id,
            version_id=run.subject_version_id,
            content_digest=str(run.subject_content_digest),
            binding_digest=str(run.subject_binding_digest),
            profile_digest=pins.profile_digest,
            catalog_digest=pins.catalog_digest,
            dataset_version_ids=pins.dataset_version_ids,
            runtime_contract_version=pins.runtime_contract_version,
            policy_version=pins.policy_version,
            threshold_version=pins.threshold_version,
            build_revision=pins.build_revision,
        )
        kwargs.update(overrides)
        return build_publish_gate_subject(**kwargs)

    def test_create_gate_server_derives_passed(self) -> None:
        from app.assistant.evaluation.gates import (
            PublishGateService,
            make_create_gate_request,
        )

        run, ds_id, _, _ = self._seed_completed_run()
        subject = self._subject(run, ds_id)
        svc = PublishGateService(self.db)
        result = svc.create_gate(
            make_create_gate_request(
                subject=subject,
                qualifying_eval_run_ids=(run.id,),
            ),
            actor_principal="op-1",
        )
        self.db.commit()
        self.assertEqual(result.decision, "passed")
        self.assertEqual(result.gate.decision, "passed")
        self.assertEqual(result.gate.actor_principal, "op-1")

    def test_create_gate_rejects_hard_safety_waiver(self) -> None:
        from app.assistant.evaluation.gates import (
            PublishGateError,
            PublishGateService,
            make_create_gate_request,
        )

        metrics = dict(_passing_metrics())
        metrics["unauthorized_broader_side_effect_count"] = 1
        run, ds_id, _, _ = self._seed_completed_run(metrics=metrics)
        subject = self._subject(run, ds_id)
        svc = PublishGateService(self.db)
        with self.assertRaises(PublishGateError) as ctx:
            svc.create_gate(
                make_create_gate_request(
                    subject=subject,
                    qualifying_eval_run_ids=(run.id,),
                    requested_non_safety_waiver_codes=("unauthorized_call",),
                    waiver_reason="please",
                ),
                actor_principal="op-1",
            )
        self.assertEqual(ctx.exception.code, "hard_safety_not_waivable")

    def test_create_gate_fails_on_secret_exposure_metric(self) -> None:
        """Gate create must not pass when aggregate_metrics.secret_exposure is nonzero."""
        from app.assistant.evaluation.gates import (
            PublishGateService,
            make_create_gate_request,
        )

        metrics = dict(_passing_metrics())
        metrics["secret_exposure"] = 1
        metrics["duplicate_write"] = 2
        run, ds_id, _, _ = self._seed_completed_run(metrics=metrics)
        subject = self._subject(run, ds_id)
        svc = PublishGateService(self.db)
        result = svc.create_gate(
            make_create_gate_request(
                subject=subject,
                qualifying_eval_run_ids=(run.id,),
            ),
            actor_principal="op-1",
        )
        self.db.commit()
        self.assertEqual(result.decision, "failed")
        failing = set(result.assertion_snapshot.get("hard_safety_failing_codes") or [])
        self.assertIn("secret_exposure", failing)
        self.assertIn("duplicate_write", failing)

    def test_create_gate_fails_when_secret_counters_missing(self) -> None:
        """Missing secret_exposure / duplicate_write counters must not silent-pass."""
        from app.assistant.evaluation.gates import (
            PublishGateService,
            make_create_gate_request,
        )

        metrics = dict(_passing_metrics())
        metrics.pop("secret_exposure", None)
        metrics.pop("duplicate_write", None)
        run, ds_id, _, _ = self._seed_completed_run(metrics=metrics)
        subject = self._subject(run, ds_id)
        svc = PublishGateService(self.db)
        result = svc.create_gate(
            make_create_gate_request(
                subject=subject,
                qualifying_eval_run_ids=(run.id,),
            ),
            actor_principal="op-1",
        )
        self.db.commit()
        self.assertEqual(result.decision, "failed")
        hard = set(result.assertion_snapshot.get("hard_safety_failing_codes") or [])
        self.assertIn("secret_exposure", hard)
        self.assertIn("duplicate_write", hard)

    def test_create_gate_rejects_client_passed_field_via_contract(self) -> None:
        from app.assistant.evaluation.contracts import CreatePublishGateRequest
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            CreatePublishGateRequest.model_validate(
                {
                    "requestId": str(_uuid()),
                    "subject": {
                        "subject": {
                            "kind": "skill_draft",
                            "aggregateId": str(_uuid()),
                            "versionId": str(_uuid()),
                            "contentDigest": DIGEST_A,
                            "resolvedBindingDigest": DIGEST_B,
                        },
                        "profileDigest": DIGEST_C,
                        "catalogDigest": DIGEST_D,
                        "runtimeContractVersion": 1,
                        "policyVersion": "p",
                        "thresholdVersion": "t",
                        "datasetVersionIds": [str(_uuid())],
                        "buildRevision": "b",
                    },
                    "qualifyingEvalRunIds": [str(_uuid())],
                    "passed": True,
                    "decision": "passed",
                }
            )

    def test_drift_invalidates_gate(self) -> None:
        from app.assistant.evaluation.gates import (
            PublishGateError,
            PublishGateService,
            make_create_gate_request,
        )

        run, ds_id, _, _ = self._seed_completed_run()
        subject = self._subject(run, ds_id)
        svc = PublishGateService(self.db)
        result = svc.create_gate(
            make_create_gate_request(
                subject=subject, qualifying_eval_run_ids=(run.id,)
            ),
            actor_principal="op",
        )
        self.db.commit()

        # Drift content digest.
        drifted = self._subject(run, ds_id, content_digest=DIGEST_F)
        with self.assertRaises(PublishGateError) as ctx:
            svc.recompute_and_verify(
                result.gate.id, subject=drifted, action="skill_publish"
            )
        self.assertEqual(ctx.exception.code, "gate_subject_drift")
        self.assertIn("subject_content_digest", ctx.exception.details.get("drifts", []))

        # Drift build revision.
        drifted_build = self._subject(run, ds_id, build_revision="other-build")
        with self.assertRaises(PublishGateError) as ctx2:
            svc.recompute_and_verify(
                result.gate.id, subject=drifted_build, action="skill_publish"
            )
        self.assertEqual(ctx2.exception.code, "gate_subject_drift")

        # Drift dataset.
        other_ds = _uuid()
        drifted_ds = self._subject(run, other_ds)
        with self.assertRaises(PublishGateError) as ctx3:
            svc.recompute_and_verify(
                result.gate.id, subject=drifted_ds, action="skill_publish"
            )
        self.assertEqual(ctx3.exception.code, "gate_subject_drift")

    def test_expired_gate_rejected(self) -> None:
        from app.assistant.evaluation.gates import (
            PublishGateError,
            PublishGateService,
            make_create_gate_request,
        )
        from app.common.time import utcnow

        run, ds_id, _, _ = self._seed_completed_run()
        subject = self._subject(run, ds_id)
        svc = PublishGateService(self.db, ttl_days=0)
        # Force already-expired by setting expires in the past after create.
        result = svc.create_gate(
            make_create_gate_request(
                subject=subject, qualifying_eval_run_ids=(run.id,)
            ),
            actor_principal="op",
        )
        result.gate.expires_at = utcnow() - timedelta(hours=1)
        self.db.commit()

        with self.assertRaises(PublishGateError) as ctx:
            svc.assert_gate_usable(
                result.gate, subject=subject, action="skill_publish"
            )
        self.assertEqual(ctx.exception.code, "gate_expired")

    def test_gate_required_matrix(self) -> None:
        from app.assistant.evaluation.gates import (
            gate_required_for_enable,
            gate_required_for_publish,
        )

        self.assertTrue(gate_required_for_publish(live_enabled=True, mode="observe"))
        self.assertTrue(gate_required_for_publish(live_enabled=True, mode="enforce"))
        self.assertFalse(gate_required_for_publish(live_enabled=False, mode="observe"))
        self.assertTrue(gate_required_for_publish(live_enabled=False, mode="enforce"))
        self.assertTrue(gate_required_for_enable(mode="observe"))
        self.assertTrue(gate_required_for_enable(mode="enforce"))

    def test_consume_appends_gate_use(self) -> None:
        from app.assistant.evaluation.gates import (
            PublishGateService,
            make_create_gate_request,
        )
        from app.assistant.evaluation.models import AssistantSkillPublishGateUse

        run, ds_id, agg, ver = self._seed_completed_run()
        subject = self._subject(run, ds_id)
        svc = PublishGateService(self.db)
        result = svc.create_gate(
            make_create_gate_request(
                subject=subject, qualifying_eval_run_ids=(run.id,)
            ),
            actor_principal="op",
        )
        self.db.commit()
        consume = svc.consume_gate(
            gate_id=result.gate.id,
            action="skill_publish",
            subject=subject,
            aggregate_id=agg,
            resulting_version_id=ver,
            actor_principal="op",
            request_id="req-consume-1",
            aggregate_revision=1,
        )
        self.db.commit()
        self.assertEqual(consume.use.gate_id, result.gate.id)
        self.assertEqual(consume.use.action, "skill_publish")
        rows = (
            self.db.query(AssistantSkillPublishGateUse)
            .filter(AssistantSkillPublishGateUse.gate_id == result.gate.id)
            .all()
        )
        self.assertEqual(len(rows), 1)


class PublishLifecycleMatrixTests(unittest.TestCase):
    """End-to-end publish/enable matrix against real package aggregates."""

    def setUp(self) -> None:
        reset_caches()
        # Default observe for bootstrap paths; individual tests may override.
        os.environ.pop("ASSISTANT_SKILL_PUBLISH_GATE_MODE", None)
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session
        from app.assistant.skills.service import AgentSkillService
        from app.assistant.skills.admin_service import SkillAdminService
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.assistant.evaluation.repository import EvaluationRepository

        self.db = make_session()
        self.pkg_svc = AgentSkillService(self.db)
        self.admin = SkillAdminService(self.db)
        self.repo = EvaluationRepository(self.db)

        skill_md = (
            "---\nname: gate-skill\ndescription: "
            "Gate matrix skill for publish tests and evaluation coverage.\n---\n\n"
            "# Gate skill\n\nBody.\n"
        ).encode("utf-8")
        mindatlas = (
            "version: 1\n"
            "display_name: Gate Skill\n"
            "legacy_aliases: []\n"
            "routing:\n"
            "  include_examples: []\n"
            "  exclude_examples: []\n"
            "  conflict_rules: []\n"
            "capabilities:\n"
            "  - type: tool\n"
            "    key: search_entries\n"
            "policy:\n"
            "  allowed_side_effects:\n"
            "    - read\n"
            "    - compute\n"
            "  max_skill_calls: 16\n"
            "  max_same_read_calls: 3\n"
            "  requires_terminal_output: true\n"
            "  terminal_text_allowed: true\n"
            "provider_aliases: {}\n"
        ).encode("utf-8")
        parsed = parse_skill_directory_files(
            {"SKILL.md": skill_md, "mindatlas.yaml": mindatlas},
            expected_root_name=None,
        )
        self.package = self.pkg_svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, origin="api")
        )
        self.db.commit()

    def tearDown(self) -> None:
        os.environ.pop("ASSISTANT_SKILL_PUBLISH_GATE_MODE", None)
        from app.config import get_settings

        get_settings.cache_clear()
        self.db.close()

    def _operator(self):
        from app.assistant.skills.principal import OperatorPrincipal

        return OperatorPrincipal(principal_id="op-gate", role="operator")

    def _enable_catalog_digest(self, package, version) -> str:
        from app.assistant.evaluation.gates import skill_catalog_pin_digest

        return skill_catalog_pin_digest(
            package_id=package.id,
            canonical_name=str(package.canonical_name),
            published_version_id=package.published_version_id or version.id,
            content_digest=str(version.content_digest),
        )

    def _publish_catalog_digest(self, package, *, live_enabled: bool = False) -> str:
        from app.assistant.evaluation.gates import skill_catalog_pin_digest

        return skill_catalog_pin_digest(
            package_id=package.id,
            canonical_name=str(package.canonical_name),
            published_version_id=package.published_version_id,
            catalog_enabled=live_enabled,
        )

    def _create_qualifying_gate(
        self,
        *,
        subject_kind: str,
        aggregate_id,
        version_id,
        content_digest: str,
        binding_digest: str,
        action_hint: str = "skill_publish",
        catalog_digest: str | None = None,
        profile_digest: str | None = None,
    ):
        """Create completed eval run + server-derived passing gate."""
        from app.assistant.evaluation.assertions import THRESHOLD_POLICY_VERSION
        from app.assistant.evaluation.gates import (
            PublishGateService,
            make_create_gate_request,
            build_publish_gate_subject,
            current_build_revision,
            current_gate_environment_pins,
        )

        del action_hint
        dataset = self.repo.create_dataset(
            stable_key=f"ds-{uuid.uuid4().hex[:8]}",
            display_name="Gate DS",
            ownership="custom",
        )
        snapshot = [
            {
                "case_key": "c1",
                "ordinal": 0,
                "locale": "en",
                "input_messages": [{"role": "user", "content": "hi"}],
                "expected_mode": "golden_skill",
                "case_digest": DIGEST_E,
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

        build_rev = current_build_revision()
        run = self.repo.create_run(
            subject_kind=subject_kind,  # type: ignore[arg-type]
            subject_aggregate_id=aggregate_id,
            subject_version_id=version_id,
            subject_content_digest=content_digest,
            subject_binding_digest=binding_digest,
            dataset_version_ids=[published.version_id],
            threshold_policy_version=THRESHOLD_POLICY_VERSION,
            mode="dataset_scripted",
            isolation_namespace_id=_uuid(),
            runtime_contract_version=1,
            required_build_revision=build_rev,
            isolation_digest=DIGEST_C,
            actor_principal="tester",
        )
        self.repo.transition_run(
            run_id=run.id, expected_revision=0, to_status="running"
        )
        self.repo.transition_run(
            run_id=run.id,
            expected_revision=1,
            to_status="completed",
            gate_eligible=True,
            aggregate_metrics=_passing_metrics(),
        )
        self.db.commit()

        pins = current_gate_environment_pins(
            self.db,
            profile_digest=profile_digest,
            catalog_digest=catalog_digest,
            dataset_version_ids=(published.version_id,),
            build_revision=build_rev,
        )
        subject = build_publish_gate_subject(
            kind=subject_kind,
            aggregate_id=aggregate_id,
            version_id=version_id,
            content_digest=content_digest,
            binding_digest=binding_digest,
            profile_digest=pins.profile_digest,
            catalog_digest=pins.catalog_digest,
            dataset_version_ids=pins.dataset_version_ids,
            runtime_contract_version=pins.runtime_contract_version,
            policy_version=pins.policy_version,
            threshold_version=pins.threshold_version,
            build_revision=pins.build_revision,
        )
        svc = PublishGateService(self.db)
        result = svc.create_gate(
            make_create_gate_request(
                subject=subject, qualifying_eval_run_ids=(run.id,)
            ),
            actor_principal="op-gate",
        )
        self.db.commit()
        return result.gate, subject, run

    def test_observe_ungated_publish_when_disabled(self) -> None:
        from app.assistant.skills.schemas import PublishSkillVersionCommand
        from app.assistant.skills.models import AssistantSkillPackage

        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "observe"
        from app.config import get_settings

        get_settings.cache_clear()

        assert self.package.draft_version is not None
        draft_id = self.package.draft_version.id
        published = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(draft_version_id=draft_id),
        )
        self.assertEqual(published.version_source, "publish")
        row = self.db.get(AssistantSkillPackage, self.package.id)
        assert row is not None
        self.assertFalse(row.catalog_enabled)
        self.assertEqual(row.published_version_id, published.id)

    def test_observe_enabled_publish_without_gate_fails_atomically(self) -> None:
        from app.assistant.skills.schemas import (
            AggregateRevisionCommand,
            PublishSkillVersionCommand,
            SaveSkillDraftCommand,
        )
        from app.assistant.skills.models import AssistantSkillPackage
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.common.exceptions import ApiException

        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "observe"
        from app.config import get_settings

        get_settings.cache_clear()

        assert self.package.draft_version is not None
        draft_id = self.package.draft_version.id
        pub1 = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(draft_version_id=draft_id),
        )
        detail = self.pkg_svc.get_package(self.package.id)
        version = self.db.get(
            __import__(
                "app.assistant.skills.models", fromlist=["AssistantSkillVersion"]
            ).AssistantSkillVersion,
            pub1.id,
        )
        assert version is not None
        pkg_row = self.db.get(AssistantSkillPackage, self.package.id)
        assert pkg_row is not None
        gate, _, _ = self._create_qualifying_gate(
            subject_kind="skill_version",
            aggregate_id=self.package.id,
            version_id=pub1.id,
            content_digest=str(version.content_digest),
            binding_digest=str(version.binding_set_digest or DIGEST_B),
            catalog_digest=self._enable_catalog_digest(pkg_row, version),
        )
        enabled = self.admin.enable_catalog(
            self.package.id,
            AggregateRevisionCommand(
                request_id="en-gate-1",
                expected_aggregate_revision=detail.aggregate_revision,
                gate_id=gate.id,
            ),
            principal=self._operator(),
            expected_published_version_id=pub1.id,
            gate_id=gate.id,
        )
        self.assertTrue(enabled.catalog_enabled)
        rev_before = enabled.aggregate_revision
        pointer_before = (
            enabled.published_version.id if enabled.published_version else None
        )

        # New draft content for second publish.
        skill_md = (
            "---\nname: gate-skill\ndescription: "
            "Gate matrix skill for publish tests and evaluation coverage.\n---\n\n"
            "# Gate skill v2\n\nBody changed.\n"
        ).encode("utf-8")
        mindatlas = (
            "version: 1\n"
            "display_name: Gate Skill\n"
            "legacy_aliases: []\n"
            "routing:\n"
            "  include_examples: []\n"
            "  exclude_examples: []\n"
            "  conflict_rules: []\n"
            "capabilities:\n"
            "  - type: tool\n"
            "    key: search_entries\n"
            "policy:\n"
            "  allowed_side_effects:\n"
            "    - read\n"
            "    - compute\n"
            "  max_skill_calls: 16\n"
            "  max_same_read_calls: 3\n"
            "  requires_terminal_output: true\n"
            "  terminal_text_allowed: true\n"
            "provider_aliases: {}\n"
        ).encode("utf-8")
        parsed = parse_skill_directory_files(
            {"SKILL.md": skill_md, "mindatlas.yaml": mindatlas},
            expected_root_name="gate-skill",
        )
        draft2 = self.pkg_svc.save_draft(
            SaveSkillDraftCommand(
                package_id=self.package.id, parsed=parsed, origin="api"
            ),
        )

        with self.assertRaises(ApiException) as ctx:
            self.pkg_svc.publish(
                self.package.id,
                PublishSkillVersionCommand(draft_version_id=draft2.id),
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40980)

        row = self.db.get(AssistantSkillPackage, self.package.id)
        assert row is not None
        self.assertEqual(row.published_version_id, pointer_before)
        self.assertEqual(int(row.aggregate_revision or 0), rev_before)
        self.assertTrue(row.catalog_enabled)

    def test_enable_without_gate_always_fails(self) -> None:
        from app.assistant.skills.schemas import (
            AggregateRevisionCommand,
            PublishSkillVersionCommand,
        )
        from app.common.exceptions import ApiException

        assert self.package.draft_version is not None
        draft_id = self.package.draft_version.id
        pub = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(draft_version_id=draft_id),
        )
        detail = self.pkg_svc.get_package(self.package.id)
        with self.assertRaises(ApiException) as ctx:
            self.admin.enable_catalog(
                self.package.id,
                AggregateRevisionCommand(
                    request_id="en-no-gate",
                    expected_aggregate_revision=detail.aggregate_revision,
                ),
                principal=self._operator(),
                expected_published_version_id=pub.id,
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40980)

    def test_enforce_requires_gate_even_when_disabled(self) -> None:
        from app.assistant.skills.schemas import PublishSkillVersionCommand
        from app.common.exceptions import ApiException

        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "enforce"
        from app.config import get_settings

        get_settings.cache_clear()

        assert self.package.draft_version is not None
        draft_id = self.package.draft_version.id
        with self.assertRaises(ApiException) as ctx:
            self.pkg_svc.publish(
                self.package.id,
                PublishSkillVersionCommand(draft_version_id=draft_id),
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40980)

    def test_enforce_publish_with_matching_gate_succeeds(self) -> None:
        from app.assistant.skills.schemas import PublishSkillVersionCommand
        from app.assistant.skills.models import AssistantSkillPackage
        from app.assistant.evaluation.models import AssistantSkillPublishGateUse

        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "enforce"
        from app.config import get_settings

        get_settings.cache_clear()

        assert self.package.draft_version is not None
        draft_id = self.package.draft_version.id
        draft = self.db.get(
            __import__(
                "app.assistant.skills.models", fromlist=["AssistantSkillVersion"]
            ).AssistantSkillVersion,
            draft_id,
        )
        assert draft is not None
        # Binding digest is computed at publish; create gate after a dry resolve
        # by publishing once in observe then... For enforce we need binding
        # digest before publish. Create gate with content digest; service rebuilds
        # subject from gate pins for non-content fields and recomputes binding.
        # So we first compute binding via a temporary observe publish of a clone
        # is heavy — instead create gate with placeholder binding and let service
        # load gate pins: content must match draft; binding is recomputed.
        # Gate create stores binding from run subject; service compare uses
        # recomputed set_digest against gate.subject_binding_digest.
        # So we need the real binding digest. Resolve via package service internals
        # by doing observe publish first is simplest: switch to observe, publish,
        # read binding, create gate for a NEW draft, enable enforce.
        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "observe"
        get_settings.cache_clear()
        pub_observe = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(draft_version_id=draft_id),
        )
        # Save a new draft (same content) for enforce publish.
        from app.assistant.skills.schemas import SaveSkillDraftCommand
        from app.assistant.skills.package_io import parse_skill_directory_files

        skill_md = (draft.skill_md or "").encode("utf-8")
        mindatlas = (draft.mindatlas_yaml or "").encode("utf-8")
        parsed = parse_skill_directory_files(
            {"SKILL.md": skill_md, "mindatlas.yaml": mindatlas},
            expected_root_name="gate-skill",
        )
        draft2 = self.pkg_svc.save_draft(
            SaveSkillDraftCommand(
                package_id=self.package.id, parsed=parsed, origin="api"
            ),
        )
        pub_ver = self.db.get(
            __import__(
                "app.assistant.skills.models", fromlist=["AssistantSkillVersion"]
            ).AssistantSkillVersion,
            pub_observe.id,
        )
        assert pub_ver is not None
        binding = str(pub_ver.binding_set_digest or DIGEST_B)
        content = str(draft2.content_digest)
        pkg_row = self.db.get(AssistantSkillPackage, self.package.id)
        assert pkg_row is not None

        gate, _, _ = self._create_qualifying_gate(
            subject_kind="skill_draft",
            aggregate_id=self.package.id,
            version_id=draft2.id,
            content_digest=content,
            binding_digest=binding,
            catalog_digest=self._publish_catalog_digest(pkg_row, live_enabled=False),
        )

        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "enforce"
        get_settings.cache_clear()

        published = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(
                draft_version_id=draft2.id,
                gate_id=gate.id,
                request_id="pub-enforce-1",
            ),
        )
        self.assertEqual(published.version_source, "publish")
        uses = (
            self.db.query(AssistantSkillPublishGateUse)
            .filter(AssistantSkillPublishGateUse.gate_id == gate.id)
            .all()
        )
        self.assertEqual(len(uses), 1)
        self.assertEqual(uses[0].action, "skill_publish")
        row = self.db.get(AssistantSkillPackage, self.package.id)
        assert row is not None
        self.assertEqual(row.published_version_id, published.id)
        self.assertFalse(row.catalog_enabled)

    def test_enable_with_matching_gate_appends_use(self) -> None:
        from app.assistant.skills.schemas import (
            AggregateRevisionCommand,
            PublishSkillVersionCommand,
        )
        from app.assistant.evaluation.models import AssistantSkillPublishGateUse

        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "observe"
        from app.config import get_settings

        get_settings.cache_clear()

        assert self.package.draft_version is not None
        draft_id = self.package.draft_version.id
        pub = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(draft_version_id=draft_id),
        )
        version = self.db.get(
            __import__(
                "app.assistant.skills.models", fromlist=["AssistantSkillVersion"]
            ).AssistantSkillVersion,
            pub.id,
        )
        assert version is not None
        pkg_row = self.db.get(
            __import__(
                "app.assistant.skills.models", fromlist=["AssistantSkillPackage"]
            ).AssistantSkillPackage,
            self.package.id,
        )
        assert pkg_row is not None
        gate, _, _ = self._create_qualifying_gate(
            subject_kind="skill_version",
            aggregate_id=self.package.id,
            version_id=pub.id,
            content_digest=str(version.content_digest),
            binding_digest=str(version.binding_set_digest or DIGEST_B),
            catalog_digest=self._enable_catalog_digest(pkg_row, version),
        )
        detail = self.pkg_svc.get_package(self.package.id)
        enabled = self.admin.enable_catalog(
            self.package.id,
            AggregateRevisionCommand(
                request_id="en-ok",
                expected_aggregate_revision=detail.aggregate_revision,
                gate_id=gate.id,
            ),
            principal=self._operator(),
            expected_published_version_id=pub.id,
            gate_id=gate.id,
        )
        self.assertTrue(enabled.catalog_enabled)
        uses = (
            self.db.query(AssistantSkillPublishGateUse)
            .filter(
                AssistantSkillPublishGateUse.gate_id == gate.id,
                AssistantSkillPublishGateUse.action == "skill_catalog_enable",
            )
            .all()
        )
        self.assertEqual(len(uses), 1)

    def test_env_pin_drift_fails_publish_closed(self) -> None:
        """Gate created with pin A; current env pin B → publish fails drift."""
        from app.assistant.skills.schemas import PublishSkillVersionCommand
        from app.assistant.skills.models import AssistantSkillPackage
        from app.common.exceptions import ApiException

        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "enforce"
        from app.config import get_settings

        get_settings.cache_clear()

        assert self.package.draft_version is not None
        draft_id = self.package.draft_version.id
        # Observe bootstrap first to resolve binding.
        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "observe"
        get_settings.cache_clear()
        pub_observe = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(draft_version_id=draft_id),
        )
        from app.assistant.skills.schemas import SaveSkillDraftCommand
        from app.assistant.skills.package_io import parse_skill_directory_files

        draft = self.db.get(
            __import__(
                "app.assistant.skills.models", fromlist=["AssistantSkillVersion"]
            ).AssistantSkillVersion,
            draft_id,
        )
        assert draft is not None
        skill_md = (draft.skill_md or "").encode("utf-8")
        mindatlas = (draft.mindatlas_yaml or "").encode("utf-8")
        parsed = parse_skill_directory_files(
            {"SKILL.md": skill_md, "mindatlas.yaml": mindatlas},
            expected_root_name="gate-skill",
        )
        draft2 = self.pkg_svc.save_draft(
            SaveSkillDraftCommand(
                package_id=self.package.id, parsed=parsed, origin="api"
            ),
        )
        pub_ver = self.db.get(
            __import__(
                "app.assistant.skills.models", fromlist=["AssistantSkillVersion"]
            ).AssistantSkillVersion,
            pub_observe.id,
        )
        assert pub_ver is not None
        pkg_row = self.db.get(AssistantSkillPackage, self.package.id)
        assert pkg_row is not None
        # Create gate with intentionally wrong catalog pin (stale pin A).
        gate, _, _ = self._create_qualifying_gate(
            subject_kind="skill_draft",
            aggregate_id=self.package.id,
            version_id=draft2.id,
            content_digest=str(draft2.content_digest),
            binding_digest=str(pub_ver.binding_set_digest or DIGEST_B),
            catalog_digest=DIGEST_D,  # not current skill_catalog_pin_digest
        )
        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "enforce"
        get_settings.cache_clear()
        with self.assertRaises(ApiException) as ctx:
            self.pkg_svc.publish(
                self.package.id,
                PublishSkillVersionCommand(
                    draft_version_id=draft2.id,
                    gate_id=gate.id,
                    request_id="pub-env-drift",
                ),
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40982)

    def test_set_catalog_enabled_without_gate_fails(self) -> None:
        """Plan 01 set_catalog_enabled(True) must not bypass the gate."""
        from app.assistant.skills.schemas import PublishSkillVersionCommand
        from app.common.exceptions import ApiException

        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "observe"
        from app.config import get_settings

        get_settings.cache_clear()

        assert self.package.draft_version is not None
        pub = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(
                draft_version_id=self.package.draft_version.id
            ),
        )
        with self.assertRaises(ApiException) as ctx:
            self.pkg_svc.set_catalog_enabled(
                self.package.id,
                enabled=True,
                expected_published_version_id=pub.id,
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40980)
        # Disable remains ungated.
        disabled = self.pkg_svc.set_catalog_enabled(
            self.package.id, enabled=False
        )
        self.assertFalse(disabled.catalog_enabled)


class DatasetRunnerGateEligibilityTests(unittest.TestCase):
    def test_dataset_scripted_zero_production_mutation(self) -> None:
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        runner = EvaluationRunner()
        outcomes = [
            {
                "execution_kind": "golden_skill",
                "activated_skills": ["gate-skill"],
                "acceptable_skills": ["gate-skill"],
                "forbidden_skills": [],
                "capability_path": ["tool", "search_entries"],
                "acceptable_capability_paths": [["tool", "search_entries"]],
                "expect_completion": True,
                "completed": True,
                "legacy_completed": True,
            }
        ] * 100
        # Pad with direct answers for threshold denominators.
        for _ in range(20):
            outcomes.append(
                {
                    "execution_kind": "direct_answer",
                    "activated_skills": [],
                    "acceptable_skills": [],
                    "forbidden_skills": ["gate-skill"],
                    "capability_path": [],
                    "acceptable_capability_paths": [],
                    "direct_answer_allowed": True,
                    "expect_completion": True,
                    "completed": True,
                    "legacy_completed": True,
                }
            )
        result = runner.run_dataset_scripted(
            isolation=isolation,
            identity=identity,
            case_outcomes=outcomes,
            production_delta={"assistant_chat_run": 0, "capability_call": 0},
            safety_counters={
                "budget_policy_bypass": 0,
                "false_completion_pending_obligation": 0,
                "unresolved_obligation_falsely_completed": 0,
                "schema_escape": 0,
                "secret_exposure": 0,
                "duplicate_write": 0,
            },
        )
        self.assertEqual(result.terminal, "completed")
        self.assertTrue(result.zero_production_mutation)
        self.assertTrue(result.gate_eligible)

    def test_production_delta_fails_gate(self) -> None:
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        runner = EvaluationRunner()
        result = runner.run_dataset_scripted(
            isolation=isolation,
            identity=identity,
            case_outcomes=[
                {
                    "execution_kind": "golden_skill",
                    "activated_skills": ["x"],
                    "acceptable_skills": ["x"],
                    "completed": True,
                    "legacy_completed": True,
                }
            ],
            production_delta={"assistant_chat_run": 1},
        )
        self.assertEqual(result.terminal, "failed")
        self.assertFalse(result.gate_eligible)
        self.assertFalse(result.zero_production_mutation)

    def test_missing_production_delta_not_coerced_to_empty(self) -> None:
        """Runner must preserve production_delta=None as indeterminate, not {}."""
        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        runner = EvaluationRunner()
        result = runner.run_dataset_scripted(
            isolation=isolation,
            identity=identity,
            case_outcomes=[
                {
                    "execution_kind": "golden_skill",
                    "activated_skills": ["x"],
                    "acceptable_skills": ["x"],
                    "completed": True,
                    "legacy_completed": True,
                }
            ],
            production_delta=None,
            safety_counters={
                "budget_policy_bypass": 0,
                "false_completion_pending_obligation": 0,
                "unresolved_obligation_falsely_completed": 0,
                "schema_escape": 0,
                "secret_exposure": 0,
                "duplicate_write": 0,
            },
        )
        self.assertEqual(result.terminal, "failed")
        self.assertFalse(result.gate_eligible)
        self.assertFalse(result.zero_production_mutation)
        codes = {
            r.code: r.outcome for r in result.assertion_summary.results if r.hard_safety
        }
        self.assertEqual(codes.get("real_side_effect_in_test"), "indeterminate")

    def test_live_mode_requires_explicit_confirmation(self) -> None:
        from datetime import datetime, timezone

        from app.assistant.evaluation.runner import (
            EvaluationRunner,
            LiveEvalRequirements,
            make_interactive_identity,
        )

        isolation, identity = make_interactive_identity()
        runner = EvaluationRunner()
        live = LiveEvalRequirements(
            model_capability_probe_id="",
            model_id="m",
            model_revision="r",
            actor_confirmed=False,
            cost_bound_usd=1.0,
            deadline_at=datetime.now(timezone.utc) + timedelta(hours=1),
            prompt_digest=DIGEST_A,
            build_revision="b",
        )
        with self.assertRaises(ValueError):
            runner.run_dataset_live(
                isolation=isolation,
                identity=identity,
                case_outcomes=[],
                live=live,
            )


if __name__ == "__main__":
    unittest.main()

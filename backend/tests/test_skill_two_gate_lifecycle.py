"""Plan 09 Task 7 — authoritative two-gate lifecycle.

Publish and enable are separate actions with distinct subject kinds,
version IDs, request IDs, qualifying runs, and gate-use rows. Client
never authors subject closure digests or decisions.

Lifecycle evidence is observed via EvaluationWorker (isolation probes +
real_orchestration). No direct gate_eligible=True seeding and no
``_allow_prebuilt_subject`` bypass on positive paths.
"""

from __future__ import annotations

import os
import unittest
import uuid
from typing import Any
from uuid import UUID

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64

# Soft metric codes a single-case fixture may not meet RELEASE_THRESHOLDS for.
# Hard safety is never waived.
_SOFT_WAIVERS = (
    "min_cases",
    "recall_at_8",
    "false_injection_rate",
    "direct_answer_accuracy",
    "capability_path_accuracy",
    "completion_success_delta_vs_legacy",
)


def _uuid() -> UUID:
    return uuid.uuid4()


class TwoGateLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ.pop("ASSISTANT_SKILL_PUBLISH_GATE_MODE", None)
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session
        from app.assistant.skills.service import AgentSkillService
        from app.assistant.skills.admin_service import SkillAdminService
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.assistant.evaluation.repository import EvaluationRepository
        from app.assistant.evaluation.gates import PublishGateService
        from app.assistant.evaluation.worker import (
            EvalWorkerConfig,
            EvalWorkerIdentity,
            EvaluationWorker,
        )

        self.db = make_session()
        self.pkg_svc = AgentSkillService(self.db)
        self.admin = SkillAdminService(self.db)
        self.repo = EvaluationRepository(self.db)
        self.gates = PublishGateService(self.db)
        self._sessions: list = []
        # Align worker identity with process build pin (may be polluted by
        # earlier suites that set APP_BUILD_REVISION without isolation).
        from app.assistant.evaluation.gates import current_build_revision

        self.worker_build_revision = current_build_revision()
        identity = EvalWorkerIdentity(
            worker_id="two-gate-worker",
            app_build_revision=self.worker_build_revision,
            runtime_contract_version=1,
            runner_contract_version=1,
        )
        self.worker = EvaluationWorker(
            EvalWorkerConfig(identity=identity),
            session_factory=self._session,
        )

        skill_md = (
            "---\nname: two-gate-skill\ndescription: "
            "Two-gate lifecycle skill for publish and enable separation.\n---\n\n"
            "# Two gate skill\n\nBody.\n"
        ).encode("utf-8")
        mindatlas = (
            "version: 1\n"
            "display_name: Two Gate Skill\n"
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
        for s in self._sessions:
            try:
                s.close()
            except Exception:
                pass
        self.db.close()

    def _session(self):
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=self.db.get_bind())
        s = Session()
        self._sessions.append(s)
        return s

    def _operator(self):
        from app.assistant.skills.principal import OperatorPrincipal

        return OperatorPrincipal(principal_id="op-two-gate", role="operator")

    def _run_dataset(
        self,
        *,
        subject_kind: str,
        aggregate_id: UUID,
        version_id: UUID,
        evidence_provenance: str = "real_orchestration",
    ):
        """Admit + execute via EvaluationWorker; never seeds gate_eligible."""
        from app.assistant.evaluation.assertions import THRESHOLD_POLICY_VERSION
        from app.assistant.skills.candidate_closure import resolve_skill_candidate_closure
        from app.assistant.skills.service import MainAgentProfileService

        try:
            MainAgentProfileService(self.db).ensure_default()
            self.db.commit()
        except Exception:
            self.db.rollback()

        closure = resolve_skill_candidate_closure(
            self.db,
            package_id=aggregate_id,
            version_id=version_id,
            subject_kind=subject_kind,  # type: ignore[arg-type]
        )
        dataset = self.repo.create_dataset(
            stable_key=f"ds-{uuid.uuid4().hex[:8]}",
            display_name="TwoGate DS",
            ownership="custom",
        )
        snapshot = [
            {
                "case_key": "c1",
                "ordinal": 0,
                "locale": "en",
                "input_messages": [{"role": "user", "content": "two-gate"}],
                "expected_mode": "golden_skill",
                "acceptable_skill_keys": ["skill-b"],
                "fixture_refs": [
                    {
                        "kind": "provider_script",
                        "script_key": "provider-selects-skill-b",
                        "revision": "eval-v1",
                    }
                ],
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

        create_kwargs: dict[str, Any] = {
            "subject_kind": subject_kind,
            "subject_aggregate_id": aggregate_id,
            "subject_version_id": version_id,
            "subject_content_digest": closure.content_digest,
            "subject_binding_digest": closure.binding_set_digest,
            "dataset_version_ids": [published.version_id],
            "threshold_policy_version": THRESHOLD_POLICY_VERSION,
            "mode": "dataset_scripted",
            "isolation_namespace_id": _uuid(),
            "runtime_contract_version": 1,
            # Exact match with EvalWorkerIdentity.app_build_revision.
            "required_build_revision": self.worker_build_revision,
            "isolation_digest": DIGEST_C,
            "actor_principal": "tester",
            "evidence_provenance": evidence_provenance,
        }
        if evidence_provenance == "real_orchestration":
            create_kwargs.update(
                provider_fixture_revision="eval-v1",
                provider_fixture_digest=DIGEST_D,
            )
        run = self.repo.create_run(**create_kwargs)
        self.db.commit()
        claimed = self.worker.execute_run(run.id)
        self.db.expire_all()
        stored = self.repo.get_run(run.id)
        assert stored is not None
        if evidence_provenance == "real_orchestration" and str(stored.status) == "queued":
            raise AssertionError(
                "worker failed to claim real_orchestration run "
                f"(required_build_revision={stored.required_build_revision!r}, "
                f"worker_build={self.worker_build_revision!r}, claimed={claimed!r})"
            )
        return stored, published.version_id, closure

    def _create_gate(
        self,
        *,
        action: str,
        aggregate_id: UUID,
        version_id: UUID,
        run_ids: list[UUID],
        soft_waivers: tuple[str, ...] = _SOFT_WAIVERS,
    ):
        """Authoritative gate create only — no prebuilt subject."""
        from app.assistant.evaluation.gates import (
            make_create_gate_request,
            summarize_qualifying_runs,
        )

        subject = self.gates.build_authoritative_subject(
            action,  # type: ignore[arg-type]
            aggregate_id,
            version_id,
            tuple(run_ids),
        )
        runs = [self.repo.get_run(rid) for rid in run_ids]
        runs = [r for r in runs if r is not None]
        summary = summarize_qualifying_runs(self.repo, runs, subject=subject)
        failing_soft = tuple(
            code
            for code in soft_waivers
            if any(
                r.code == code and r.outcome in {"fail", "indeterminate"}
                for r in summary.results
            )
        )
        result = self.gates.create_gate(
            make_create_gate_request(
                action=action,  # type: ignore[arg-type]
                subject_aggregate_id=aggregate_id,
                subject_version_id=version_id,
                qualifying_eval_run_ids=tuple(run_ids),
                requested_non_safety_waiver_codes=failing_soft,
                waiver_reason=(
                    "two-gate single-case fixture soft thresholds"
                    if failing_soft
                    else None
                ),
            ),
            actor_principal="op-two-gate",
        )
        self.db.commit()
        return result.gate

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

    def test_gate_api_rejects_client_authored_subject(self) -> None:
        """HTTP CreateGateBody.extra=forbid rejects client subject closure."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.assistant.evaluation.router import PLAN09_EVAL_PREFIX, mount_skill_eval_router
        from app.assistant.skills.admin_router import TRUSTED_MOUNT_ENV
        from app.common.exceptions import register_exception_handlers
        from app.database import get_db

        os.environ[TRUSTED_MOUNT_ENV] = "1"
        app = FastAPI()
        register_exception_handlers(app)
        session = self.db

        def _override_db():
            try:
                yield session
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        mount_skill_eval_router(app, app_env="development")
        client = TestClient(app)
        headers = {
            "X-MindAtlas-Operator-Id": "operator-two-gate",
            "X-MindAtlas-Operator-Role": "operator",
        }
        body = {
            "requestId": str(uuid.uuid4()),
            "action": "skill_publish",
            "subjectAggregateId": str(self.package.id),
            "subjectVersionId": str(self.package.draft_version.id),
            "qualifyingEvalRunIds": [str(uuid.uuid4())],
            "subject": {"catalogDigest": "0" * 64},
        }
        response = client.post(f"{PLAN09_EVAL_PREFIX}/gates", json=body, headers=headers)
        self.assertEqual(response.status_code, 422, response.text)

    def test_publish_gate_cannot_enable_catalog(self) -> None:
        """A skill_publish gate cannot be reused for skill_catalog_enable."""
        from app.assistant.evaluation.gates import PublishGateError
        from app.assistant.skills.models import AssistantSkillVersion
        from app.assistant.skills.schemas import PublishSkillVersionCommand
        from app.common.exceptions import ApiException

        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "enforce"
        from app.config import get_settings

        get_settings.cache_clear()

        assert self.package.draft_version is not None
        draft = self.package.draft_version
        run, _ds, _closure = self._run_dataset(
            subject_kind="skill_draft",
            aggregate_id=self.package.id,
            version_id=draft.id,
        )
        self.assertTrue(bool(run.gate_eligible), "worker must produce gate-eligible run")

        publish_gate = self._create_gate(
            action="skill_publish",
            aggregate_id=self.package.id,
            version_id=draft.id,
            run_ids=[run.id],
        )
        self.assertEqual(publish_gate.action, "skill_publish")

        published = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(
                draft_version_id=draft.id,
                gate_id=publish_gate.id,
                request_id="pub-two-gate-1",
                expected_aggregate_revision=0,
            ),
        )
        self.assertEqual(published.version_source, "publish")

        # Reuse the same publish gate for enable — must fail action mismatch.
        from app.assistant.evaluation.gates import (
            build_publish_gate_subject,
            current_build_revision,
            current_gate_environment_pins,
        )
        from app.assistant.skills.models import AssistantSkillPackage

        version = self.db.get(AssistantSkillVersion, published.id)
        assert version is not None
        pkg_row = self.db.get(AssistantSkillPackage, self.package.id)
        assert pkg_row is not None
        pins = current_gate_environment_pins(
            self.db,
            catalog_digest=self._enable_catalog_digest(pkg_row, version),
            dataset_version_ids=tuple(run.dataset_version_ids or ()),
            build_revision=current_build_revision(),
        )
        enable_subject = build_publish_gate_subject(
            kind="skill_version",
            aggregate_id=self.package.id,
            version_id=published.id,
            content_digest=str(version.content_digest),
            binding_digest=str(version.binding_set_digest or _closure.binding_set_digest),
            profile_digest=pins.profile_digest,
            catalog_digest=pins.catalog_digest,
            dataset_version_ids=pins.dataset_version_ids,
            runtime_contract_version=pins.runtime_contract_version,
            policy_version=pins.policy_version,
            threshold_version=pins.threshold_version,
            build_revision=pins.build_revision,
        )
        with self.assertRaises(PublishGateError) as ctx:
            self.gates.enforce_enable(
                gate_id=publish_gate.id,
                subject=enable_subject,
                action="skill_catalog_enable",
                aggregate_id=self.package.id,
                resulting_version_id=published.id,
                actor_principal="op-two-gate",
                request_id="en-two-gate-bad",
                aggregate_revision=1,
            )
        self.assertEqual(ctx.exception.code, "gate_action_subject_mismatch")

        # Admin path also rejects.
        from app.assistant.skills.schemas import AggregateRevisionCommand

        detail = self.pkg_svc.get_package(self.package.id)
        with self.assertRaises(ApiException):
            self.admin.enable_catalog(
                self.package.id,
                AggregateRevisionCommand(
                    request_id="en-two-gate-admin-bad",
                    expected_aggregate_revision=detail.aggregate_revision,
                    gate_id=publish_gate.id,
                ),
                principal=self._operator(),
                expected_published_version_id=published.id,
                gate_id=publish_gate.id,
            )

    def test_synthetic_run_cannot_qualify_gate(self) -> None:
        from app.assistant.evaluation.gates import (
            PublishGateError,
            make_create_gate_request,
        )

        assert self.package.draft_version is not None
        draft = self.package.draft_version
        run, _ds, _closure = self._run_dataset(
            subject_kind="skill_draft",
            aggregate_id=self.package.id,
            version_id=draft.id,
            evidence_provenance="structural_synthetic",
        )
        # Structural path must not become gate-eligible.
        self.assertFalse(bool(run.gate_eligible))
        with self.assertRaises(PublishGateError) as ctx:
            self.gates.create_gate(
                make_create_gate_request(
                    action="skill_publish",
                    subject_aggregate_id=self.package.id,
                    subject_version_id=draft.id,
                    qualifying_eval_run_ids=(run.id,),
                ),
                actor_principal="op",
            )
        self.assertIn(
            ctx.exception.code,
            {
                "eval_run_not_gate_eligible",
                "eval_run_not_real_orchestration",
            },
        )

    def test_fresh_enable_gate_targets_published_version(self) -> None:
        """Enable requires a fresh skill_catalog_enable gate on the published version."""
        from app.assistant.evaluation.gates import PublishGateError
        from app.assistant.evaluation.models import AssistantSkillPublishGateUse
        from app.assistant.skills.schemas import (
            AggregateRevisionCommand,
            PublishSkillVersionCommand,
        )

        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "enforce"
        from app.config import get_settings

        get_settings.cache_clear()

        assert self.package.draft_version is not None
        draft = self.package.draft_version
        run, _ds, _closure = self._run_dataset(
            subject_kind="skill_draft",
            aggregate_id=self.package.id,
            version_id=draft.id,
        )
        self.assertTrue(bool(run.gate_eligible))

        publish_gate = self._create_gate(
            action="skill_publish",
            aggregate_id=self.package.id,
            version_id=draft.id,
            run_ids=[run.id],
        )
        self.db.commit()

        published = self.pkg_svc.publish(
            self.package.id,
            PublishSkillVersionCommand(
                draft_version_id=draft.id,
                gate_id=publish_gate.id,
                request_id="pub-two-gate-2",
                expected_aggregate_revision=0,
            ),
        )

        enable_run, _enable_ds, _ = self._run_dataset(
            subject_kind="skill_version",
            aggregate_id=self.package.id,
            version_id=published.id,
        )
        self.assertTrue(bool(enable_run.gate_eligible))

        enable_gate = self._create_gate(
            action="skill_catalog_enable",
            aggregate_id=self.package.id,
            version_id=published.id,
            run_ids=[enable_run.id],
        )
        self.db.commit()
        self.assertEqual(enable_gate.action, "skill_catalog_enable")
        self.assertEqual(str(enable_gate.subject_version_id), str(published.id))
        self.assertNotEqual(enable_gate.id, publish_gate.id)

        detail = self.pkg_svc.get_package(self.package.id)
        enabled = self.admin.enable_catalog(
            self.package.id,
            AggregateRevisionCommand(
                request_id="en-two-gate-ok",
                expected_aggregate_revision=detail.aggregate_revision,
                gate_id=enable_gate.id,
            ),
            principal=self._operator(),
            expected_published_version_id=published.id,
            gate_id=enable_gate.id,
        )
        self.assertTrue(enabled.catalog_enabled)

        uses = (
            self.db.query(AssistantSkillPublishGateUse)
            .filter(AssistantSkillPublishGateUse.gate_id == enable_gate.id)
            .all()
        )
        self.assertEqual(len(uses), 1)
        self.assertEqual(uses[0].action, "skill_catalog_enable")

        # Second consume of the same enable gate fails (single consumption).
        from app.assistant.evaluation.gates import (
            build_publish_gate_subject,
            current_build_revision,
            current_gate_environment_pins,
        )
        from app.assistant.skills.models import AssistantSkillPackage, AssistantSkillVersion

        version = self.db.get(AssistantSkillVersion, published.id)
        assert version is not None
        pkg_row = self.db.get(AssistantSkillPackage, self.package.id)
        assert pkg_row is not None
        enable_pins = current_gate_environment_pins(
            self.db,
            catalog_digest=self._enable_catalog_digest(pkg_row, version),
            dataset_version_ids=tuple(enable_run.dataset_version_ids or ()),
            build_revision=current_build_revision(),
        )
        enable_subject = build_publish_gate_subject(
            kind="skill_version",
            aggregate_id=self.package.id,
            version_id=published.id,
            content_digest=str(version.content_digest),
            binding_digest=str(version.binding_set_digest or _closure.binding_set_digest),
            profile_digest=enable_pins.profile_digest,
            catalog_digest=enable_pins.catalog_digest,
            dataset_version_ids=enable_pins.dataset_version_ids,
            runtime_contract_version=enable_pins.runtime_contract_version,
            policy_version=enable_pins.policy_version,
            threshold_version=enable_pins.threshold_version,
            build_revision=enable_pins.build_revision,
        )
        with self.assertRaises(PublishGateError) as ctx:
            self.gates.consume_gate(
                gate_id=enable_gate.id,
                action="skill_catalog_enable",
                subject=enable_subject,
                aggregate_id=self.package.id,
                resulting_version_id=published.id,
                actor_principal="op",
                request_id="en-two-gate-reuse",
                aggregate_revision=99,
            )
        self.assertEqual(ctx.exception.code, "gate_already_used")

    def test_create_gate_subject_kwarg_rebuilds_without_prebuilt_flag(self) -> None:
        """subject= without the test flag must rebuild authoritatively (no digest bypass)."""
        from app.assistant.evaluation.gates import (
            build_publish_gate_subject,
            make_create_gate_request,
        )

        assert self.package.draft_version is not None
        draft = self.package.draft_version
        run, ds_id, closure = self._run_dataset(
            subject_kind="skill_draft",
            aggregate_id=self.package.id,
            version_id=draft.id,
        )
        self.assertTrue(bool(run.gate_eligible))
        # Caller-supplied digests that deliberately disagree with the real package.
        forged = build_publish_gate_subject(
            kind="skill_draft",
            aggregate_id=self.package.id,
            version_id=draft.id,
            content_digest="a" * 64,
            binding_digest="b" * 64,
            profile_digest="c" * 64,
            catalog_digest="d" * 64,
            dataset_version_ids=(ds_id,),
            runtime_contract_version=1,
            policy_version="p",
            threshold_version=str(run.threshold_policy_version),
            build_revision=str(run.required_build_revision),
        )
        # Discover soft waivers against the *real* subject so create can pass.
        real_subject = self.gates.build_authoritative_subject(
            "skill_publish",
            self.package.id,
            draft.id,
            (run.id,),
        )
        from app.assistant.evaluation.gates import summarize_qualifying_runs

        summary = summarize_qualifying_runs(self.repo, [run], subject=real_subject)
        failing_soft = tuple(
            code
            for code in _SOFT_WAIVERS
            if any(
                r.code == code and r.outcome in {"fail", "indeterminate"}
                for r in summary.results
            )
        )
        result = self.gates.create_gate(
            make_create_gate_request(
                action="skill_publish",
                subject=forged,
                qualifying_eval_run_ids=(run.id,),
                requested_non_safety_waiver_codes=failing_soft,
                waiver_reason="two-gate soft" if failing_soft else None,
            ),
            actor_principal="op",
            subject=forged,
            # no _allow_prebuilt_subject → must rebuild and ignore forged digests
        )
        self.db.commit()
        self.assertEqual(result.gate.subject_content_digest, closure.content_digest)
        self.assertEqual(
            result.gate.subject_binding_digest, closure.binding_set_digest
        )
        self.assertNotEqual(result.gate.subject_content_digest, "a" * 64)

    def test_build_authoritative_subject_maps_action_to_kind(self) -> None:
        from app.assistant.evaluation.gates import PublishGateError

        assert self.package.draft_version is not None
        draft = self.package.draft_version
        run, _ds, closure = self._run_dataset(
            subject_kind="skill_draft",
            aggregate_id=self.package.id,
            version_id=draft.id,
        )
        self.assertTrue(bool(run.gate_eligible))
        subject = self.gates.build_authoritative_subject(
            "skill_publish",
            self.package.id,
            draft.id,
            (run.id,),
        )
        self.assertEqual(subject.subject.kind, "skill_draft")
        self.assertEqual(subject.subject.content_digest, closure.content_digest)
        self.assertEqual(subject.subject.resolved_binding_digest, closure.binding_set_digest)

        # Wrong action for draft identity fails.
        with self.assertRaises(PublishGateError) as ctx:
            self.gates.build_authoritative_subject(
                "skill_catalog_enable",
                self.package.id,
                draft.id,
                (run.id,),
            )
        self.assertEqual(ctx.exception.code, "gate_action_subject_mismatch")


if __name__ == "__main__":
    unittest.main()

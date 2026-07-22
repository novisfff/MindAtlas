"""Plan 09 Task 12 — process-level create→enable lifecycle harness.

This is an **in-process** API + service harness (TestClient + EvaluationRepository
+ EvaluationWorker + PublishGateService + SkillAdminService), not a multi-process
API+worker spawn. It proves the authoritative two-gate lifecycle end-to-end at
process level:

  create → draft save (CAS) → real_orchestration eval (worker observations) →
  skill_publish gate (authoritative subject rebuild) → publish → catalog empty →
  published real_orchestration eval → skill_catalog_enable gate → enable →
  catalog contains

Negative matrix covers synthetic evidence, wrong action reuse, missing CAS,
client-authored gate subject, and publish-gate enable rejection.

Evidence is observed by EvaluationWorker (isolation probes + real orchestration).
No direct gate_eligible=True writes and no ``_allow_prebuilt_subject`` bypass.
"""

from __future__ import annotations

import os
import unittest
import uuid
from dataclasses import dataclass
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


def _skill_md(name: str) -> bytes:
    return (
        f"---\nname: {name}\n"
        "description: Plan 09 lifecycle e2e skill for publish and catalog enable.\n"
        "---\n\n# Lifecycle skill\n\nBody.\n"
    ).encode("utf-8")


def _mindatlas_yaml(display: str = "Lifecycle Skill") -> bytes:
    return (
        "version: 1\n"
        f"display_name: {display}\n"
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


@dataclass
class Plan09Package:
    id: UUID
    canonical_name: str
    aggregate_revision: int
    draft_version_id: UUID | None
    published_version_id: UUID | None
    catalog_enabled: bool


class Plan09System:
    """In-process Plan 09 lifecycle surface used by the e2e harness."""

    def __init__(self) -> None:
        from tests._db import make_session
        from app.assistant.skills.service import AgentSkillService
        from app.assistant.skills.admin_service import SkillAdminService
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
        self._name = f"plan09-life-{uuid.uuid4().hex[:8]}"
        self._sessions: list = []

        identity = EvalWorkerIdentity(
            worker_id="plan09-e2e-worker",
            app_build_revision="development",
            runtime_contract_version=1,
            runner_contract_version=1,
        )
        self.worker = EvaluationWorker(
            EvalWorkerConfig(identity=identity),
            session_factory=self._session,
        )

    def _session(self):
        from sqlalchemy.orm import sessionmaker

        Session = sessionmaker(bind=self.db.get_bind())
        s = Session()
        self._sessions.append(s)
        return s

    def close(self) -> None:
        for s in self._sessions:
            try:
                s.close()
            except Exception:
                pass
        self.db.close()

    def _operator(self):
        from app.assistant.skills.principal import OperatorPrincipal

        return OperatorPrincipal(principal_id="op-plan09-e2e", role="operator")

    def create_package(self) -> Plan09Package:
        from app.assistant.skills.schemas import CreateSkillPackageCommand
        from app.assistant.skills.package_io import parse_skill_directory_files

        parsed = parse_skill_directory_files(
            {"SKILL.md": _skill_md(self._name), "mindatlas.yaml": _mindatlas_yaml()},
            expected_root_name=None,
        )
        detail = self.pkg_svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, origin="api")
        )
        self.db.commit()
        return self._as_package(detail)

    def save_draft(self, package: Plan09Package, *, expected_revision: int) -> Plan09Package:
        from app.assistant.skills.schemas import SaveSkillDraftCommand
        from app.assistant.skills.package_io import parse_skill_directory_files

        # Content-only edit that preserves resources and advances revision.
        body = (
            f"---\nname: {package.canonical_name}\n"
            "description: Plan 09 lifecycle e2e skill for publish and catalog enable.\n"
            "---\n\n# Lifecycle skill\n\nDraft revision body.\n"
        ).encode("utf-8")
        parsed = parse_skill_directory_files(
            {"SKILL.md": body, "mindatlas.yaml": _mindatlas_yaml("Lifecycle Draft")},
            expected_root_name=None,
        )
        self.pkg_svc.save_draft(
            SaveSkillDraftCommand(
                package_id=package.id,
                parsed=parsed,
                origin="api",
                expected_aggregate_revision=expected_revision,
                request_id=f"draft-{uuid.uuid4().hex[:10]}",
                preserve_previous_resources=True,
            )
        )
        self.db.commit()
        return self.refresh(package.id)

    def refresh(self, package_id: UUID) -> Plan09Package:
        detail = self.pkg_svc.get_package(package_id)
        return self._as_package(detail)

    def _as_package(self, detail) -> Plan09Package:
        draft_id = detail.draft_version.id if detail.draft_version is not None else None
        published_id = (
            detail.published_version.id if detail.published_version is not None else None
        )
        return Plan09Package(
            id=detail.id,
            canonical_name=str(detail.canonical_name),
            aggregate_revision=int(detail.aggregate_revision),
            draft_version_id=draft_id,
            published_version_id=published_id,
            catalog_enabled=bool(detail.catalog_enabled),
        )

    def run_real_dataset(
        self,
        package: Plan09Package,
        *,
        subject_kind: str,
        version_id: UUID,
        evidence_provenance: str = "real_orchestration",
        gate_eligible: bool = True,
    ):
        """Admit + execute a dataset_scripted run via EvaluationWorker.

        Process-level equivalent of worker claim/process — never writes
        gate_eligible=True directly. Isolation probes + orchestration
        observations decide eligibility. ``gate_eligible`` is unused for
        seeding (kept for call-site compatibility on synthetic negatives).
        """
        del gate_eligible  # never seed eligibility; worker/repo decide
        from app.assistant.evaluation.assertions import THRESHOLD_POLICY_VERSION
        from app.assistant.evaluation.gates import current_build_revision
        from app.assistant.skills.candidate_closure import resolve_skill_candidate_closure

        closure = resolve_skill_candidate_closure(
            self.db,
            package_id=package.id,
            version_id=version_id,
            subject_kind=subject_kind,  # type: ignore[arg-type]
        )
        dataset = self.repo.create_dataset(
            stable_key=f"ds-{uuid.uuid4().hex[:8]}",
            display_name="Plan09 lifecycle DS",
            ownership="custom",
        )
        # Acceptable skill matches builtin fixture provider-selects-skill-b so
        # real_orchestration can observe skill recall under isolation probes.
        snapshot = [
            {
                "case_key": "c1",
                "ordinal": 0,
                "locale": "en",
                "input_messages": [{"role": "user", "content": "lifecycle"}],
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
            actor="plan09-e2e",
        )
        self.db.commit()

        create_kwargs: dict[str, Any] = {
            "subject_kind": subject_kind,
            "subject_aggregate_id": package.id,
            "subject_version_id": version_id,
            "subject_content_digest": closure.content_digest,
            "subject_binding_digest": closure.binding_set_digest,
            "dataset_version_ids": [published.version_id],
            "threshold_policy_version": THRESHOLD_POLICY_VERSION,
            "mode": "dataset_scripted",
            "isolation_namespace_id": _uuid(),
            "runtime_contract_version": 1,
            "required_build_revision": current_build_revision(),
            "isolation_digest": DIGEST_C,
            "actor_principal": "plan09-e2e",
            "evidence_provenance": evidence_provenance,
        }
        if evidence_provenance == "real_orchestration":
            create_kwargs.update(
                provider_fixture_revision="eval-v1",
                provider_fixture_digest=DIGEST_D,
            )
        run = self.repo.create_run(**create_kwargs)
        self.db.commit()

        # In-process worker method call — process-level equivalent of claim/process.
        # Does not invent gate_eligible; observations come from isolation probes.
        self.worker.execute_run(run.id)

        self.db.expire_all()
        stored = self.repo.get_run(run.id)
        assert stored is not None
        return stored, published.version_id, closure

    def create_gate(
        self,
        action: str,
        package: Plan09Package,
        version_id: UUID,
        run_ids: list[UUID],
        *,
        catalog_digest: str | None = None,
        soft_waivers: tuple[str, ...] = _SOFT_WAIVERS,
    ):
        """Create gate via authoritative subject rebuild only (no prebuilt bypass)."""
        from app.assistant.evaluation.gates import (
            make_create_gate_request,
            summarize_qualifying_runs,
        )

        del catalog_digest  # authoritative rebuild derives catalog pin

        # Discover currently-failing soft codes so we only waive real failures
        # (waiver of non-failing codes is rejected by derive_gate_decision).
        subject = self.gates.build_authoritative_subject(
            action,  # type: ignore[arg-type]
            package.id,
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
                subject_aggregate_id=package.id,
                subject_version_id=version_id,
                qualifying_eval_run_ids=tuple(run_ids),
                requested_non_safety_waiver_codes=failing_soft,
                waiver_reason=(
                    "plan09 e2e single-case fixture soft thresholds"
                    if failing_soft
                    else None
                ),
            ),
            actor_principal="op-plan09-e2e",
            # No subject= / no _allow_prebuilt_subject — authoritative rebuild only.
        )
        self.db.commit()
        return result.gate

    def publish(self, package: Plan09Package, gate) -> Plan09Package:
        from app.assistant.skills.schemas import PublishSkillVersionCommand

        assert package.draft_version_id is not None
        self.pkg_svc.publish(
            package.id,
            PublishSkillVersionCommand(
                draft_version_id=package.draft_version_id,
                gate_id=gate.id,
                request_id=f"pub-{uuid.uuid4().hex[:10]}",
                expected_aggregate_revision=package.aggregate_revision,
            ),
        )
        self.db.commit()
        return self.refresh(package.id)

    def enable_catalog(self, package: Plan09Package, gate) -> Plan09Package:
        from app.assistant.skills.schemas import AggregateRevisionCommand

        assert package.published_version_id is not None
        self.admin.enable_catalog(
            package.id,
            AggregateRevisionCommand(
                request_id=f"en-{uuid.uuid4().hex[:10]}",
                expected_aggregate_revision=package.aggregate_revision,
                gate_id=gate.id,
            ),
            principal=self._operator(),
            expected_published_version_id=package.published_version_id,
            gate_id=gate.id,
        )
        self.db.commit()
        return self.refresh(package.id)

    def catalog_contains(self, canonical_name: str) -> bool:
        for item in self.pkg_svc.list_packages():
            if str(item.canonical_name) == canonical_name and bool(item.catalog_enabled):
                return True
        return False

    def snapshot_state(self, package_id: UUID) -> dict[str, Any]:
        from app.assistant.skills.models import AssistantSkillPackage

        row = self.db.get(AssistantSkillPackage, package_id)
        assert row is not None
        return {
            "aggregate_revision": int(row.aggregate_revision or 0),
            "published_version_id": row.published_version_id,
            "draft_version_id": row.draft_version_id,
            "catalog_enabled": bool(row.catalog_enabled),
            "catalog_enabled_at": row.catalog_enabled_at,
            "catalog_enabled_by": row.catalog_enabled_by,
        }


class Plan09LifecycleE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["ASSISTANT_SKILL_PUBLISH_GATE_MODE"] = "enforce"
        from app.config import get_settings

        get_settings.cache_clear()
        self.system = Plan09System()

    def tearDown(self) -> None:
        os.environ.pop("ASSISTANT_SKILL_PUBLISH_GATE_MODE", None)
        from app.config import get_settings

        get_settings.cache_clear()
        self.system.close()

    # ------------------------------------------------------------------ positive

    def test_create_real_eval_publish_fresh_eval_promote_enable(self) -> None:
        package = self.system.create_package()
        self.assertFalse(self.system.catalog_contains(package.canonical_name))
        self.assertIsNotNone(package.draft_version_id)

        draft = self.system.save_draft(
            package, expected_revision=package.aggregate_revision
        )
        self.assertGreater(draft.aggregate_revision, package.aggregate_revision)
        assert draft.draft_version_id is not None

        draft_run, _ds, _closure = self.system.run_real_dataset(
            draft,
            subject_kind="skill_draft",
            version_id=draft.draft_version_id,
        )
        self.assertTrue(draft_run.gate_eligible)
        self.assertEqual(draft_run.evidence_provenance, "real_orchestration")

        publish_gate = self.system.create_gate(
            "skill_publish",
            draft,
            draft.draft_version_id,
            [draft_run.id],
        )
        self.assertEqual(publish_gate.action, "skill_publish")
        self.assertIn(publish_gate.decision, {"passed", "waived_non_safety"})

        published = self.system.publish(draft, publish_gate)
        self.assertIsNotNone(published.published_version_id)
        self.assertFalse(self.system.catalog_contains(package.canonical_name))
        self.assertFalse(published.catalog_enabled)

        assert published.published_version_id is not None
        promo_run, _ds2, _c2 = self.system.run_real_dataset(
            published,
            subject_kind="skill_version",
            version_id=published.published_version_id,
        )
        self.assertTrue(promo_run.gate_eligible)

        promotion_gate = self.system.create_gate(
            "skill_catalog_enable",
            published,
            published.published_version_id,
            [promo_run.id],
        )
        self.assertEqual(promotion_gate.action, "skill_catalog_enable")
        self.assertNotEqual(promotion_gate.id, publish_gate.id)

        enabled = self.system.enable_catalog(published, promotion_gate)
        self.assertTrue(enabled.catalog_enabled)
        self.assertTrue(self.system.catalog_contains(package.canonical_name))

    # ------------------------------------------------------------------ negatives

    def test_synthetic_cannot_qualify_publish_gate(self) -> None:
        from app.assistant.evaluation.gates import PublishGateError

        package = self.system.create_package()
        assert package.draft_version_id is not None
        before = self.system.snapshot_state(package.id)

        run, _ds, _c = self.system.run_real_dataset(
            package,
            subject_kind="skill_draft",
            version_id=package.draft_version_id,
            evidence_provenance="structural_synthetic",
            gate_eligible=False,
        )
        self.assertFalse(run.gate_eligible)

        with self.assertRaises(PublishGateError) as ctx:
            self.system.create_gate(
                "skill_publish",
                package,
                package.draft_version_id,
                [run.id],
            )
        self.assertIn(
            ctx.exception.code,
            {
                "eval_run_not_gate_eligible",
                "eval_run_not_real_orchestration",
            },
        )
        after = self.system.snapshot_state(package.id)
        self.assertEqual(before, after)
        self.assertFalse(self.system.catalog_contains(package.canonical_name))

    def test_wrong_action_publish_gate_cannot_enable(self) -> None:
        from app.common.exceptions import ApiException

        package = self.system.create_package()
        assert package.draft_version_id is not None
        draft_run, _ds, _c = self.system.run_real_dataset(
            package,
            subject_kind="skill_draft",
            version_id=package.draft_version_id,
        )
        publish_gate = self.system.create_gate(
            "skill_publish",
            package,
            package.draft_version_id,
            [draft_run.id],
        )
        published = self.system.publish(package, publish_gate)
        before = self.system.snapshot_state(published.id)

        # Attempt enable with the publish gate (wrong action).
        from app.assistant.skills.schemas import AggregateRevisionCommand

        with self.assertRaises(ApiException):
            self.system.admin.enable_catalog(
                published.id,
                AggregateRevisionCommand(
                    request_id=f"en-bad-{uuid.uuid4().hex[:8]}",
                    expected_aggregate_revision=published.aggregate_revision,
                    gate_id=publish_gate.id,
                ),
                principal=self.system._operator(),
                expected_published_version_id=published.published_version_id,
                gate_id=publish_gate.id,
            )
        self.db_refresh_state(published.id, before)

    def db_refresh_state(self, package_id: UUID, before: dict[str, Any]) -> None:
        self.system.db.rollback()
        after = self.system.snapshot_state(package_id)
        self.assertEqual(before["aggregate_revision"], after["aggregate_revision"])
        self.assertEqual(before["published_version_id"], after["published_version_id"])
        self.assertEqual(before["catalog_enabled"], after["catalog_enabled"])
        self.assertFalse(after["catalog_enabled"])

    def test_missing_cas_publish_is_422(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.assistant.skills.router import skill_package_router
        from app.common.exceptions import register_exception_handlers
        from app.database import get_db
        from tests._db import make_session

        app = FastAPI()
        register_exception_handlers(app)
        session = make_session()

        def _override_db():
            try:
                yield session
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        app.include_router(skill_package_router)
        client = TestClient(app)

        name = f"cas-miss-{uuid.uuid4().hex[:8]}"
        create = client.post(
            "/api/assistant-config/skill-packages",
            json={
                "skillMd": _skill_md(name).decode("utf-8"),
                "mindatlasYaml": _mindatlas_yaml().decode("utf-8"),
                "resources": [],
            },
        )
        self.assertEqual(create.status_code, 200, create.text)
        package_id = create.json()["data"]["id"]
        draft_id = create.json()["data"]["draftVersion"]["id"]

        # Missing expectedAggregateRevision + requestId → 422.
        r = client.post(
            f"/api/assistant-config/skill-packages/{package_id}/publish",
            json={"draftVersionId": draft_id},
        )
        self.assertEqual(r.status_code, 422, r.text)
        session.close()

    def test_client_authored_gate_subject_is_422(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.assistant.evaluation.router import PLAN09_EVAL_PREFIX, mount_skill_eval_router
        from app.assistant.skills.admin_router import TRUSTED_MOUNT_ENV
        from app.common.exceptions import register_exception_handlers
        from app.database import get_db

        os.environ[TRUSTED_MOUNT_ENV] = "1"
        app = FastAPI()
        register_exception_handlers(app)
        session = self.system.db

        def _override_db():
            try:
                yield session
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        mount_skill_eval_router(app, app_env="development")
        client = TestClient(app)
        headers = {
            "X-MindAtlas-Operator-Id": "op-plan09-e2e",
            "X-MindAtlas-Operator-Role": "operator",
        }
        body = {
            "requestId": str(uuid.uuid4()),
            "action": "skill_publish",
            "subjectAggregateId": str(uuid.uuid4()),
            "subjectVersionId": str(uuid.uuid4()),
            "qualifyingEvalRunIds": [str(uuid.uuid4())],
            "subject": {
                "catalogDigest": "0" * 64,
                "contentDigest": "1" * 64,
            },
            "passed": True,
            "decision": "passed",
        }
        response = client.post(f"{PLAN09_EVAL_PREFIX}/gates", json=body, headers=headers)
        self.assertIn(response.status_code, {422, 400}, response.text)
        os.environ.pop(TRUSTED_MOUNT_ENV, None)

    def test_real_orchestration_probes_required_for_gate_eligibility(self) -> None:
        """Process-level proof that probe-less real orchestration is not gate-eligible."""
        from app.assistant.evaluation.isolation import build_isolation_context
        from app.assistant.evaluation.observations import observed_to_case_outcome_mapping
        from app.assistant.evaluation.orchestration import (
            EvaluationOrchestrator,
            EvaluationOrchestratorConfig,
            install_default_isolation_probes,
            zero_production_delta_probe,
            zero_safety_counter_probe,
        )
        from app.assistant.evaluation.assertions import evaluate_dataset_assertions
        from app.assistant.evaluation.contracts import EVAL_OWNER_KIND, EvalExecutionIdentity
        from dataclasses import dataclass, field

        @dataclass
        class _Case:
            id: UUID = field(default_factory=uuid.uuid4)
            case_key: str = "case-1"
            locale: str = "en"
            input_messages: list = field(
                default_factory=lambda: [{"role": "user", "content": "hi"}]
            )
            fixture_refs: list = field(
                default_factory=lambda: [
                    {
                        "kind": "provider_script",
                        "script_key": "provider-selects-skill-b",
                        "revision": "eval-v1",
                    }
                ]
            )
            expected_mode: str = "golden_skill"
            acceptable_skill_keys: list = field(default_factory=lambda: ["skill-b"])
            forbidden_skill_keys: list = field(default_factory=list)
            acceptable_capability_paths: list = field(default_factory=list)
            expect_completion: bool = True
            assertion_json: dict = field(default_factory=dict)

        # Without probes → not gate eligible.
        isolation = build_isolation_context(
            namespace_id=uuid.uuid4(),
            subject_digest=DIGEST_A,
            dataset_version_ids=(uuid.uuid4(),),
            memory_mode="empty",
            data_mode="fixture",
        )
        orch_no_probe = EvaluationOrchestrator(
            config=EvaluationOrchestratorConfig(app_build_revision="test"),
            safety_counter_probe=None,
            production_delta_probe=zero_production_delta_probe,
        )
        case = _Case()
        identity = EvalExecutionIdentity(
            eval_run_id=uuid.uuid4(),
            eval_case_id=case.id,
            namespace_id=isolation.namespace_id,
            owner_kind=EVAL_OWNER_KIND,
            subject_kind="skill_draft",
            subject_aggregate_id=uuid.uuid4(),
            subject_version_id=uuid.uuid4(),
        )
        observed = orch_no_probe.execute_case(isolation, case, None, identity=identity)
        mapping = observed_to_case_outcome_mapping(observed, case=case)
        summary = evaluate_dataset_assertions(
            case_outcomes=[mapping],
            safety_counters=observed.safety_counters,
            production_delta=observed.production_delta,
            isolation_breached=False,
        )
        missing = any(v is None for v in (observed.safety_counters or {}).values())
        self.assertTrue(missing)
        self.assertFalse(bool(summary.gate_eligible and not missing))

        # Default probes are honest-missing (None) — cannot invent hard-safety pass.
        safety_default, prod_default = install_default_isolation_probes()
        orch_default = EvaluationOrchestrator(
            config=EvaluationOrchestratorConfig(app_build_revision="test"),
            safety_counter_probe=safety_default,
            production_delta_probe=prod_default,
        )
        observed_default = orch_default.execute_case(
            isolation, case, None, identity=identity
        )
        self.assertTrue(
            all(v is None for v in observed_default.safety_counters.values())
        )
        self.assertTrue(
            all(v is None for v in observed_default.production_delta.values())
        )

        # Explicit zero probes + matching skill → gate may be true.
        orch = EvaluationOrchestrator(
            config=EvaluationOrchestratorConfig(app_build_revision="test"),
            safety_counter_probe=zero_safety_counter_probe,
            production_delta_probe=zero_production_delta_probe,
        )
        observed2 = orch.execute_case(isolation, case, None, identity=identity)
        mapping2 = observed_to_case_outcome_mapping(observed2, case=case)
        summary2 = evaluate_dataset_assertions(
            case_outcomes=[mapping2],
            safety_counters=observed2.safety_counters,
            production_delta=observed2.production_delta,
            isolation_breached=False,
        )
        self.assertTrue(all(v is not None for v in observed2.safety_counters.values()))
        self.assertTrue(observed2.actual_active_skills == ("skill-b",))
        self.assertTrue(summary2.gate_eligible)

    def test_openapi_unmounted_has_no_plan09_paths(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.assistant.evaluation.router import mount_skill_eval_router
        from app.assistant.skills.admin_router import (
            TRUSTED_MOUNT_ENV,
            mount_skill_admin_router,
        )
        from app.assistant.skills.router import skill_package_router
        from app.common.exceptions import register_exception_handlers
        from app.database import get_db
        from tests._db import make_session

        os.environ.pop(TRUSTED_MOUNT_ENV, None)
        app = FastAPI()
        register_exception_handlers(app)
        session = make_session()

        def _override_db():
            try:
                yield session
            finally:
                pass

        app.dependency_overrides[get_db] = _override_db
        app.include_router(skill_package_router)
        self.assertFalse(mount_skill_admin_router(app, app_env="production"))
        self.assertFalse(mount_skill_eval_router(app, app_env="production"))
        client = TestClient(app)
        paths = client.get("/openapi.json").json().get("paths") or {}
        for path in paths:
            self.assertNotIn("/skill-admin", path)
            self.assertNotIn("/skill-eval", path)
            self.assertNotIn("/catalog/enable", path)
        self.assertIn("/api/assistant-config/skill-packages", paths)
        session.close()


if __name__ == "__main__":
    unittest.main()

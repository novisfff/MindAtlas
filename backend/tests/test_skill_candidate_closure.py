"""Plan 09 remediation Task 4 — shared skill candidate-closure resolver."""

from __future__ import annotations

import os
import unittest
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

# Module-local pin only — never leave process-global APP_BUILD_REVISION drifted
# after this module (lifecycle workers pin identity to "development").
_CLOSURE_BUILD_REVISION = "test-build-c25d03f"
_CLOSURE_APP_ENV = "test"


def _minimal_skill_md(name: str) -> bytes:
    description = (
        "Review MindAtlas entries over a time range; use for weekly summaries and retrospectives."
    )
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n\n# Body\n"
    ).encode("utf-8")


def _mindatlas_yaml(capabilities: str) -> bytes:
    return (
        "version: 1\n"
        "display_name: Test Skill\n"
        "legacy_aliases: []\n"
        "routing:\n"
        "  include_examples: []\n"
        "  exclude_examples: []\n"
        "  conflict_rules: []\n"
        "capabilities:\n"
        f"{capabilities}"
        "policy:\n"
        "  allowed_side_effects:\n"
        "    - read\n"
        "  max_skill_calls: 8\n"
        "  max_same_read_calls: 2\n"
        "  requires_terminal_output: false\n"
        "  terminal_text_allowed: true\n"
        "provider_aliases: {}\n"
        "metadata: {}\n"
    ).encode("utf-8")


def _parse(name: str, capabilities: str):
    from app.assistant.skills.package_io import parse_skill_directory_files

    return parse_skill_directory_files(
        {
            "SKILL.md": _minimal_skill_md(name=name),
            "mindatlas.yaml": _mindatlas_yaml(capabilities),
        },
        expected_root_name=None,
    )


class SkillCandidateClosureTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        self._prev_build = os.environ.get("APP_BUILD_REVISION")
        self._prev_env = os.environ.get("APP_ENV")
        os.environ["APP_BUILD_REVISION"] = _CLOSURE_BUILD_REVISION
        os.environ["APP_ENV"] = _CLOSURE_APP_ENV
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session
        from app.assistant.skills.service import AgentSkillService
        from tests.agent_skill_test_support import create_default_model_binding

        self.db = make_session()
        create_default_model_binding(self.db)
        self.db.commit()
        self.svc = AgentSkillService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        # Restore process env so later suites (lifecycle workers on
        # app_build_revision="development") are not polluted.
        if self._prev_build is None:
            os.environ.pop("APP_BUILD_REVISION", None)
        else:
            os.environ["APP_BUILD_REVISION"] = self._prev_build
        if self._prev_env is None:
            os.environ.pop("APP_ENV", None)
        else:
            os.environ["APP_ENV"] = self._prev_env
        from app.config import get_settings

        get_settings.cache_clear()

    def _create_package(self, name: str, capabilities: str):
        from app.assistant.skills.schemas import CreateSkillPackageCommand

        parsed = _parse(name, capabilities)
        return self.svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="draft-1")
        )

    def test_eval_and_publish_resolve_identical_draft_closure(self) -> None:
        from app.assistant.evaluation.repository import EvaluationRepository
        from app.assistant.skills.candidate_closure import resolve_skill_candidate_closure
        from app.assistant.skills.schemas import PublishSkillVersionCommand

        package = self._create_package(
            f"closure-parity-{uuid4().hex[:8]}",
            "  - type: tool\n    key: search_entries\n",
        )
        draft_id = package.draft_version.id  # type: ignore[union-attr]
        package_id = package.id

        closure = resolve_skill_candidate_closure(
            self.db,
            package_id=package_id,
            version_id=draft_id,
            subject_kind="skill_draft",
        )
        self.assertEqual(closure.schema_version, 1)
        self.assertEqual(closure.subject_kind, "skill_draft")
        self.assertEqual(len(closure.content_digest), 64)
        self.assertEqual(len(closure.binding_set_digest), 64)
        self.assertEqual(len(closure.version_digest), 64)
        self.assertTrue(closure.bindings)

        # Evaluation admission must pin the same binding digest (server-resolved).
        repo = EvaluationRepository(self.db)
        run = repo.create_run(
            subject_kind="skill_draft",
            subject_aggregate_id=package_id,
            subject_version_id=draft_id,
            subject_content_digest=closure.content_digest,
            subject_binding_digest=closure.binding_set_digest,
            dataset_version_ids=[],
            threshold_policy_version="plan09-policy-v1",
            mode="interactive_scripted",
            isolation_namespace_id=uuid4(),
            runtime_contract_version=1,
            required_build_revision="development",
            isolation_digest="0" * 64,
            actor_principal="operator:closure-test",
            request_id=f"eval-{uuid4().hex[:8]}",
        )
        self.db.commit()
        self.assertEqual(run.subject_content_digest, closure.content_digest)
        self.assertEqual(run.subject_binding_digest, closure.binding_set_digest)

        published = self.svc.publish(
            package_id,
            PublishSkillVersionCommand(
                draft_version_id=draft_id,
                request_id=f"pub-{uuid4().hex[:8]}",
                expected_aggregate_revision=0,
            ),
        )
        self.assertEqual(published.binding_set_digest, closure.binding_set_digest)
        self.assertEqual(published.version_digest, closure.version_digest)
        self.assertEqual(published.content_digest, closure.content_digest)

    def test_target_version_drift_changes_candidate_closure(self) -> None:
        from app.assistant.skills.candidate_closure import resolve_skill_candidate_closure
        from app.assistant_config.schemas import AssistantToolUpdateRequest
        from app.assistant_config.service import AssistantConfigService
        from tests.agent_skill_test_support import create_remote_tool

        tool = create_remote_tool(self.db, name=f"remote_closure_{uuid4().hex[:6]}")
        self.db.commit()
        package = self._create_package(
            f"closure-drift-{uuid4().hex[:8]}",
            (
                f"  - type: tool\n"
                f"    key: {tool.name}\n"
                "    contract:\n"
                "      output_schema:\n"
                "        type: object\n"
                "        properties:\n"
                "          ok:\n"
                "            type: boolean\n"
            ),
        )
        draft_id = package.draft_version.id  # type: ignore[union-attr]
        before = resolve_skill_candidate_closure(
            self.db,
            package_id=package.id,
            version_id=draft_id,
            subject_kind="skill_draft",
        )
        cfg = AssistantConfigService(self.db)
        cfg.update_tool(tool.id, AssistantToolUpdateRequest(timeout_seconds=30))
        after = resolve_skill_candidate_closure(
            self.db,
            package_id=package.id,
            version_id=draft_id,
            subject_kind="skill_draft",
        )
        self.assertEqual(before.content_digest, after.content_digest)
        self.assertNotEqual(before.binding_set_digest, after.binding_set_digest)
        self.assertNotEqual(before.version_digest, after.version_digest)

    def test_content_digest_drift_raises(self) -> None:
        from app.assistant.skills.candidate_closure import (
            CandidateClosureError,
            resolve_skill_candidate_closure,
        )
        from app.assistant.skills.models import AssistantSkillVersion

        package = self._create_package(
            f"closure-content-drift-{uuid4().hex[:8]}",
            "  - type: tool\n    key: search_entries\n",
        )
        draft_id = package.draft_version.id  # type: ignore[union-attr]
        version = self.db.get(AssistantSkillVersion, draft_id)
        assert version is not None
        # Corrupt the stored content digest without touching payload bytes.
        version.content_digest = "a" * 64
        self.db.flush()
        with self.assertRaises(CandidateClosureError) as ctx:
            resolve_skill_candidate_closure(
                self.db,
                package_id=package.id,
                version_id=draft_id,
                subject_kind="skill_draft",
            )
        self.assertEqual(str(ctx.exception), "skill_content_digest_drift")

    def test_missing_package_and_version_raise(self) -> None:
        from app.assistant.skills.candidate_closure import (
            CandidateClosureError,
            resolve_skill_candidate_closure,
        )

        package = self._create_package(
            f"closure-missing-{uuid4().hex[:8]}",
            "  - type: tool\n    key: search_entries\n",
        )
        draft_id = package.draft_version.id  # type: ignore[union-attr]
        with self.assertRaises(CandidateClosureError) as ctx:
            resolve_skill_candidate_closure(
                self.db,
                package_id=uuid4(),
                version_id=draft_id,
                subject_kind="skill_draft",
            )
        self.assertEqual(str(ctx.exception), "skill_package_not_found")
        with self.assertRaises(CandidateClosureError) as ctx2:
            resolve_skill_candidate_closure(
                self.db,
                package_id=package.id,
                version_id=uuid4(),
                subject_kind="skill_draft",
            )
        self.assertEqual(str(ctx2.exception), "skill_version_not_found")

    def test_resolver_does_not_flush_or_commit(self) -> None:
        from app.assistant.skills.candidate_closure import resolve_skill_candidate_closure
        from app.assistant.skills.models import AssistantSkillPackage

        package = self._create_package(
            f"closure-pure-{uuid4().hex[:8]}",
            "  - type: tool\n    key: search_entries\n",
        )
        draft_id = package.draft_version.id  # type: ignore[union-attr]
        row = self.db.get(AssistantSkillPackage, package.id)
        assert row is not None
        row.display_name = "mutated-in-memory-only"
        # Resolver must not flush this dirty state.
        closure = resolve_skill_candidate_closure(
            self.db,
            package_id=package.id,
            version_id=draft_id,
            subject_kind="skill_draft",
        )
        self.assertEqual(len(closure.binding_set_digest), 64)
        self.db.rollback()
        reloaded = self.db.get(AssistantSkillPackage, package.id)
        assert reloaded is not None
        self.assertNotEqual(reloaded.display_name, "mutated-in-memory-only")


if __name__ == "__main__":
    unittest.main()

"""Golden-path rollout tests (Plan 04 Task 9)."""

from __future__ import annotations

import os
import unittest
from uuid import uuid4

from tests._bootstrap import bootstrap_backend_imports, reset_caches

bootstrap_backend_imports()
reset_caches()

os.environ.setdefault("APP_BUILD_REVISION", "plan04-dev")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "AI_PROVIDER_FERNET_KEY", "07v02gVBdreNrXjLJZkIMdohHtgy6aDFKBHxakHjbrQ="
)


class MainAgentRolloutTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_BUILD_REVISION"] = "plan04-dev"
        os.environ["APP_ENV"] = "test"
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session
        from tests.agent_skill_test_support import create_default_model_binding

        self.db = make_session()
        create_default_model_binding(self.db)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        from app.config import get_settings

        get_settings.cache_clear()
        reset_caches()

    def test_plan_dry_run_does_not_enable_flags(self) -> None:
        from app.assistant.main_agent.rollout import plan_rollout
        from app.assistant.skills.models import (
            AssistantMainAgentProfile,
            AssistantSkillPackage,
        )

        report = plan_rollout(self.db, dry_run=True, allow_create_fixture=True)
        self.assertTrue(report.success)
        self.assertTrue(report.dry_run)
        # Dry-run without existing package reports pending fixture or existing plan.
        self.assertIn(report.reason_code, {"plan_ok", "plan_dry_run_fixture_pending"})

        enabled_pkgs = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.catalog_enabled.is_(True))
            .count()
        )
        self.assertEqual(enabled_pkgs, 0)
        profiles = self.db.query(AssistantMainAgentProfile).all()
        for profile in profiles:
            self.assertFalse(profile.runtime_enabled)

    def test_enable_disable_fixture_golden_path(self) -> None:
        from app.assistant.main_agent.control_capabilities import MAIN_AGENT_CONTROL_KEYS
        from app.assistant.main_agent.golden_path import GOLDEN_FIXTURE_CANONICAL_NAME
        from app.assistant.main_agent.rollout import (
            disable_rollout,
            enable_rollout,
            plan_rollout,
        )
        from app.assistant.skills.models import (
            AssistantMainAgentProfile,
            AssistantMainAgentProfileVersion,
            AssistantSkillPackage,
            AssistantSkillVersion,
        )

        plan = plan_rollout(
            self.db,
            dry_run=False,
            prefer_quick_stats=False,
            allow_create_fixture=True,
        )
        self.assertTrue(plan.success, plan.message)
        self.assertIsNotNone(plan.expected)
        assert plan.expected is not None
        self.assertEqual(plan.expected.package_canonical_name, GOLDEN_FIXTURE_CANONICAL_NAME)
        self.assertFalse(plan.package_catalog_enabled)
        self.assertFalse(plan.profile_runtime_enabled)

        package = self.db.get(AssistantSkillPackage, plan.expected.package_id)
        assert package is not None
        self.assertFalse(package.catalog_enabled)
        self.assertIsNotNone(package.published_version_id)
        version = self.db.get(AssistantSkillVersion, package.published_version_id)
        assert version is not None
        self.assertEqual(version.version_source, "publish")

        profile = (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.is_default.is_(True))
            .one()
        )
        self.assertFalse(profile.runtime_enabled)
        pub = self.db.get(AssistantMainAgentProfileVersion, profile.published_version_id)
        assert pub is not None
        keys = tuple((pub.snapshot or {}).get("controlCapabilityKeys") or ())
        self.assertEqual(keys, MAIN_AGENT_CONTROL_KEYS)
        scope = (pub.snapshot or {}).get("skillCatalogScope") or {}
        self.assertEqual(scope.get("mode"), "allowlist")
        self.assertEqual(scope.get("packageIds"), [str(package.id)])

        enabled = enable_rollout(
            self.db,
            dry_run=False,
            prefer_quick_stats=False,
            allow_create_fixture=True,
            expected=plan.expected,
            require_probe=False,
        )
        self.assertTrue(enabled.success, enabled.message)
        self.assertTrue(enabled.package_catalog_enabled)
        self.assertEqual(enabled.package_migration_state, "cutover")
        self.assertTrue(enabled.profile_runtime_enabled)
        self.assertEqual(enabled.other_catalog_enabled_packages, ())

        package = self.db.get(AssistantSkillPackage, package.id)
        assert package is not None
        self.assertTrue(package.catalog_enabled)
        self.assertEqual(package.migration_state, "cutover")
        published_before = package.published_version_id
        version_count_before = (
            self.db.query(AssistantSkillVersion)
            .filter(AssistantSkillVersion.skill_package_id == package.id)
            .count()
        )
        profile = (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.is_default.is_(True))
            .one()
        )
        self.assertTrue(profile.runtime_enabled)
        profile_pub_before = profile.published_version_id
        profile_version_count_before = (
            self.db.query(AssistantMainAgentProfileVersion)
            .filter(AssistantMainAgentProfileVersion.profile_id == profile.id)
            .count()
        )

        # Idempotent re-enable.
        again = enable_rollout(
            self.db,
            dry_run=False,
            prefer_quick_stats=False,
            allow_create_fixture=True,
            expected=plan.expected,
            require_probe=False,
        )
        self.assertTrue(again.success, again.message)
        self.assertEqual(again.reason_code, "enable_idempotent")

        disabled = disable_rollout(self.db, dry_run=False, package_id=package.id)
        self.assertTrue(disabled.success, disabled.message)
        package = self.db.get(AssistantSkillPackage, package.id)
        assert package is not None
        self.assertFalse(package.catalog_enabled)
        self.assertEqual(package.published_version_id, published_before)
        version_count_after = (
            self.db.query(AssistantSkillVersion)
            .filter(AssistantSkillVersion.skill_package_id == package.id)
            .count()
        )
        self.assertEqual(version_count_before, version_count_after)

        profile = (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.is_default.is_(True))
            .one()
        )
        self.assertFalse(profile.runtime_enabled)
        self.assertEqual(profile.published_version_id, profile_pub_before)
        profile_version_count_after = (
            self.db.query(AssistantMainAgentProfileVersion)
            .filter(AssistantMainAgentProfileVersion.profile_id == profile.id)
            .count()
        )
        self.assertEqual(profile_version_count_before, profile_version_count_after)

    def test_enable_fails_on_digest_drift(self) -> None:
        from app.assistant.main_agent.rollout import (
            RolloutError,
            RolloutExpectedState,
            enable_rollout,
            plan_rollout,
            run_rollout,
        )

        plan = plan_rollout(
            self.db,
            dry_run=False,
            prefer_quick_stats=False,
            allow_create_fixture=True,
        )
        self.assertTrue(plan.success, plan.message)
        assert plan.expected is not None
        drifted = RolloutExpectedState(
            package_id=plan.expected.package_id,
            package_canonical_name=plan.expected.package_canonical_name,
            package_version_id=plan.expected.package_version_id,
            package_version_digest="0" * 64,
            package_content_digest=plan.expected.package_content_digest,
            profile_id=plan.expected.profile_id,
            profile_version_id=plan.expected.profile_version_id,
            profile_content_digest=plan.expected.profile_content_digest,
            golden_strategy=plan.expected.golden_strategy,
        )
        with self.assertRaises(RolloutError) as ctx:
            enable_rollout(
                self.db,
                dry_run=False,
                expected=drifted,
                require_probe=False,
            )
        self.assertEqual(ctx.exception.reason_code, "package_digest_drift")
        # run_rollout normalizes errors into a report for the operator CLI.
        report = run_rollout(self.db, "enable", dry_run=True, allow_create_fixture=True)
        self.assertTrue(report.success or report.reason_code)

    def test_only_golden_package_becomes_catalog_visible(self) -> None:
        from app.assistant.main_agent.rollout import enable_rollout, plan_rollout
        from app.assistant.skills.models import AssistantSkillPackage
        from app.assistant.skills.package_io import parse_skill_directory_files
        from app.assistant.skills.schemas import (
            CreateSkillPackageCommand,
            PublishSkillVersionCommand,
        )
        from app.assistant.skills.service import AgentSkillService

        # Create an extra published-but-disabled package.
        other_name = f"other-skill-{uuid4().hex[:8]}"
        skill_md = (
            f"---\nname: {other_name}\ndescription: "
            "Other package that must stay catalog-disabled during golden enable.\n"
            "---\n\n# Other\n"
        ).encode("utf-8")
        mindatlas = (
            "version: 1\n"
            "display_name: Other\n"
            "legacy_aliases: []\n"
            "routing:\n"
            "  include_examples: []\n"
            "  exclude_examples: []\n"
            "  conflict_rules: []\n"
            "capabilities:\n"
            "  - type: tool\n"
            "    key: get_statistics\n"
            "policy:\n"
            "  allowed_side_effects:\n"
            "    - read\n"
            "  max_skill_calls: 4\n"
            "  max_same_read_calls: 2\n"
            "  requires_terminal_output: false\n"
            "  terminal_text_allowed: true\n"
            "provider_aliases: {}\n"
            "metadata: {}\n"
        ).encode("utf-8")
        parsed = parse_skill_directory_files(
            {"SKILL.md": skill_md, "mindatlas.yaml": mindatlas},
            expected_root_name=None,
        )
        svc = AgentSkillService(self.db)
        other = svc.create_native_package(
            CreateSkillPackageCommand(parsed=parsed, version_name="draft-1")
        )
        draft_id = other.draft_version.id if other.draft_version else None
        assert draft_id is not None
        svc.publish(other.id, PublishSkillVersionCommand(draft_version_id=draft_id, request_id="pub-req-1", expected_aggregate_revision=0))

        plan = plan_rollout(
            self.db,
            dry_run=False,
            prefer_quick_stats=False,
            allow_create_fixture=True,
        )
        self.assertTrue(plan.success, plan.message)
        enabled = enable_rollout(
            self.db,
            dry_run=False,
            prefer_quick_stats=False,
            allow_create_fixture=True,
            expected=plan.expected,
            require_probe=False,
        )
        self.assertTrue(enabled.success, enabled.message)

        enabled_names = [
            row.canonical_name
            for row in self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.catalog_enabled.is_(True))
            .all()
        ]
        self.assertEqual(enabled_names, [plan.expected.package_canonical_name])  # type: ignore[union-attr]
        other_pkg = self.db.get(AssistantSkillPackage, other.id)
        assert other_pkg is not None
        self.assertFalse(other_pkg.catalog_enabled)


class LegacyAdapterCutoverSkipTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_BUILD_REVISION"] = "plan04-dev"
        os.environ["APP_ENV"] = "test"
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()
        from app.config import get_settings

        get_settings.cache_clear()
        reset_caches()

    def test_cutover_package_stops_automatic_sync(self) -> None:
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import AssistantSkillPackage, AssistantSkillVersion
        from app.assistant_config.models import AssistantSkill
        from tests.agent_skill_test_support import (
            create_default_model_binding,
            create_published_workflow,
        )

        create_default_model_binding(self.db)
        self.db.commit()
        name = f"cutover_skip_{uuid4().hex[:8]}"
        workflow, _version = create_published_workflow(
            self.db,
            name=f"{name}__workflow",
            tool_names=["get_statistics"],
        )
        skill = AssistantSkill(
            name=name,
            description="legacy skill for cutover skip",
            intent_examples=["stats please"],
            tools=[],
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            is_system=False,
            enabled=True,
            workflow_id=workflow.id,
            agent_profile_id=None,
        )
        self.db.add(skill)
        self.db.commit()

        adapter = LegacySkillShadowAdapter()
        item = adapter.sync_one(self.db, skill.id)
        self.assertEqual(item.status, "published", item.diagnostics)
        self.assertIsNotNone(item.shadow_package_id)
        pkg = self.db.get(AssistantSkillPackage, item.shadow_package_id)
        assert pkg is not None
        before = (
            self.db.query(AssistantSkillVersion)
            .filter(AssistantSkillVersion.skill_package_id == pkg.id)
            .count()
        )
        pkg.migration_state = "cutover"
        self.db.commit()

        skill.description = "should not resync after cutover"
        self.db.commit()
        stopped = adapter.sync_one(self.db, skill.id)
        self.assertEqual(stopped.status, "unchanged")
        self.assertTrue(
            any(d.reason_code == "shadow_sync_stopped_native" for d in stopped.diagnostics),
            stopped.diagnostics,
        )
        after = (
            self.db.query(AssistantSkillVersion)
            .filter(AssistantSkillVersion.skill_package_id == pkg.id)
            .count()
        )
        self.assertEqual(before, after)
        refreshed = self.db.get(AssistantSkill, skill.id)
        assert refreshed is not None
        self.assertTrue(refreshed.enabled)


if __name__ == "__main__":
    unittest.main()

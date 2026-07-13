from __future__ import annotations

import os
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


def _skill_stub(
    *,
    name: str,
    skill_id: uuid.UUID | None = None,
    description: str = "does a thing",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=skill_id or uuid.uuid4(),
        name=name,
        description=description,
        intent_examples=["example"],
        tools=[],
        system_prompt=None,
        kb_config={"enabled": False},
        langgraph_pattern="workflow_dag",
        enabled=True,
        is_system=False,
        workflow_id=None,
        agent_profile_id=None,
    )


class CanonicalNameMappingTests(unittest.TestCase):
    def test_preserves_lowercase_ascii_and_maps_underscores(self) -> None:
        from app.assistant.skills.legacy_adapter import (
            legacy_skill_canonical_name,
            map_legacy_name_to_canonical_base,
        )

        skill = _skill_stub(name="quick_stats")
        self.assertEqual(map_legacy_name_to_canonical_base("quick_stats", skill.id), "quick-stats")
        self.assertEqual(legacy_skill_canonical_name(skill), "quick-stats")

        skill2 = _skill_stub(name="smart_capture")
        self.assertEqual(legacy_skill_canonical_name(skill2), "smart-capture")

    def test_whitespace_and_invalid_runs_collapse(self) -> None:
        from app.assistant.skills.legacy_adapter import map_legacy_name_to_canonical_base

        sid = uuid.uuid4()
        self.assertEqual(map_legacy_name_to_canonical_base("Hello World", sid), "hello-world")
        self.assertEqual(map_legacy_name_to_canonical_base("a__b---c", sid), "a-b-c")
        self.assertEqual(map_legacy_name_to_canonical_base("--foo--", sid), "foo")

    def test_empty_or_non_ascii_falls_back_to_uuid_prefix(self) -> None:
        from app.assistant.skills.legacy_adapter import map_legacy_name_to_canonical_base

        sid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        self.assertEqual(
            map_legacy_name_to_canonical_base("你好", sid),
            "legacy-skill-123456781234",
        )
        self.assertEqual(
            map_legacy_name_to_canonical_base("___", sid),
            "legacy-skill-123456781234",
        )

    def test_truncates_without_trailing_hyphen(self) -> None:
        from app.assistant.skills.legacy_adapter import map_legacy_name_to_canonical_base

        sid = uuid.uuid4()
        long_name = "a" * 70 + "_x"
        mapped = map_legacy_name_to_canonical_base(long_name, sid)
        self.assertLessEqual(len(mapped), 64)
        self.assertFalse(mapped.endswith("-"))
        self.assertTrue(mapped.startswith("a"))

    def test_collision_uses_stable_uuid_suffix(self) -> None:
        from app.assistant.skills.legacy_adapter import legacy_skill_canonical_name

        sid = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        skill = _skill_stub(name="quick_stats", skill_id=sid)
        collided = legacy_skill_canonical_name(skill, occupied={"quick-stats"})
        self.assertTrue(collided.startswith("quick-stats-") or "aaaa" in collided)
        self.assertNotEqual(collided, "quick-stats")
        # Stable across calls.
        self.assertEqual(
            collided,
            legacy_skill_canonical_name(skill, occupied={"quick-stats"}),
        )

    def test_general_chat_is_reserved(self) -> None:
        from app.assistant.skills.legacy_adapter import legacy_skill_canonical_name

        with self.assertRaises(ValueError):
            legacy_skill_canonical_name(_skill_stub(name="general_chat"))
        with self.assertRaises(ValueError):
            legacy_skill_canonical_name(_skill_stub(name="general-chat"))


class LegacyMirrorSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_ENV"] = "test"
        os.environ["APP_BUILD_REVISION"] = "test-build-task7"
        from app.config import get_settings

        get_settings.cache_clear()

        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()
        reset_caches()

    def _create_legacy_skill(
        self,
        *,
        name: str,
        target: str = "workflow",
        model_source: str = "default",
        publish_target: bool = True,
        description: str = "Captures knowledge; use for structured intake.",
    ):
        from app.assistant_config.models import AssistantSkill
        from tests.agent_skill_test_support import (
            create_published_agent,
            create_published_workflow,
        )

        if target == "workflow":
            workflow, version = create_published_workflow(
                self.db,
                name=f"{name}__workflow",
                model_source=model_source,
            )
            if not publish_target:
                workflow.published_version_id = None
                self.db.flush()
            skill = AssistantSkill(
                name=name,
                description=description,
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
            return skill, workflow, version
        agent, version = create_published_agent(
            self.db,
            name=f"{name}__agent",
            tools=["list_tags"],
            model_source=model_source,
        )
        if not publish_target:
            agent.published_version_id = None
            self.db.flush()
        skill = AssistantSkill(
            name=name,
            description=description,
            intent_examples=["chat about tags"],
            tools=["list_tags"],
            mode="langgraph",
            langgraph_pattern="agent_loop",
            is_system=False,
            enabled=True,
            workflow_id=None,
            agent_profile_id=agent.id,
        )
        self.db.add(skill)
        self.db.commit()
        return skill, agent, version

    def test_render_package_policy_and_capability_shapes(self) -> None:
        from app.assistant.skills.legacy_adapter import (
            legacy_skill_canonical_name,
            render_legacy_skill_package,
            resolve_legacy_target_ref,
        )
        from app.assistant.skills.package_io import parse_skill_md

        skill, workflow, _version = self._create_legacy_skill(name="quick_stats")
        target_ref = resolve_legacy_target_ref(self.db, skill)
        assert target_ref is not None
        parsed = render_legacy_skill_package(skill, target_ref=target_ref)
        self.assertEqual(parsed.canonical_name, "quick-stats")
        self.assertEqual(legacy_skill_canonical_name(skill), "quick-stats")
        fm = parse_skill_md(parsed.skill_md_bytes)
        self.assertEqual(fm.name, "quick-stats")
        self.assertTrue(fm.description)
        assert parsed.manifest is not None
        self.assertEqual(parsed.manifest.policy.allowed_side_effects, ())
        self.assertEqual(len(parsed.manifest.capabilities), 1)
        cap = parsed.manifest.capabilities[0]
        self.assertEqual(cap.type, "workflow")
        self.assertEqual(cap.key, workflow.name)
        self.assertIn("quick_stats", parsed.manifest.legacy_aliases)

        # Agent capability includes explicit contract.
        skill_a, agent, _ = self._create_legacy_skill(name="research_bot", target="agent")
        ref_a = resolve_legacy_target_ref(self.db, skill_a)
        assert ref_a is not None
        parsed_a = render_legacy_skill_package(skill_a, target_ref=ref_a)
        assert parsed_a.manifest is not None
        cap_a = parsed_a.manifest.capabilities[0]
        self.assertEqual(cap_a.type, "agent")
        self.assertEqual(cap_a.key, agent.name)
        assert cap_a.contract is not None
        self.assertIn("input", (cap_a.contract.input_schema or {}).get("required", []))
        self.assertIn("text", (cap_a.contract.output_schema or {}).get("required", []))

    def test_sync_creates_disabled_shadow_and_publishes_when_resolvable(self) -> None:
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import (
            AssistantSkillCapabilityBinding,
            AssistantSkillPackage,
            AssistantSkillPackageAlias,
            AssistantSkillVersion,
        )
        from tests.agent_skill_test_support import create_default_model_binding

        create_default_model_binding(self.db)
        self.db.commit()

        skill, workflow, wf_version = self._create_legacy_skill(name="quick_stats")
        report_item = LegacySkillShadowAdapter().sync_one(self.db, skill.id)
        self.assertEqual(report_item.status, "published")
        self.assertIsNotNone(report_item.shadow_package_id)

        packages = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.legacy_skill_id == skill.id)
            .all()
        )
        self.assertEqual(len(packages), 1)
        pkg = packages[0]
        self.assertEqual(pkg.migration_state, "shadow")
        self.assertFalse(pkg.catalog_enabled)
        self.assertEqual(pkg.canonical_name, "quick-stats")
        self.assertIsNotNone(pkg.draft_version_id)
        self.assertIsNotNone(pkg.published_version_id)
        self.assertIsNotNone(pkg.legacy_source_digest)

        aliases = (
            self.db.query(AssistantSkillPackageAlias)
            .filter(AssistantSkillPackageAlias.skill_package_id == pkg.id)
            .all()
        )
        alias_values = {a.alias for a in aliases}
        self.assertIn("quick-stats", alias_values)
        self.assertIn("quick_stats", alias_values)

        published = self.db.get(AssistantSkillVersion, pkg.published_version_id)
        assert published is not None
        self.assertEqual(published.version_source, "publish")
        self.assertEqual(published.origin, "legacy")
        self.assertIsNotNone(published.binding_set_digest)
        self.assertIsNotNone(published.version_digest)

        bindings = (
            self.db.query(AssistantSkillCapabilityBinding)
            .filter(AssistantSkillCapabilityBinding.skill_version_id == published.id)
            .all()
        )
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].resolution_status, "resolved")
        self.assertEqual(bindings[0].resolved_workflow_version_id, wf_version.id)
        self.assertEqual(bindings[0].target_identity, f"workflow:{workflow.id}")

        # Idempotent re-sync.
        again = LegacySkillShadowAdapter().sync_one(self.db, skill.id)
        self.assertEqual(again.status, "unchanged")
        versions = (
            self.db.query(AssistantSkillVersion)
            .filter(AssistantSkillVersion.skill_package_id == pkg.id)
            .all()
        )
        publish_count = sum(1 for v in versions if v.version_source == "publish")
        self.assertEqual(publish_count, 1)

    def test_changed_source_appends_publish_never_edits_old(self) -> None:
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import AssistantSkillPackage, AssistantSkillVersion
        from tests.agent_skill_test_support import create_default_model_binding

        create_default_model_binding(self.db)
        self.db.commit()
        skill, _workflow, _ = self._create_legacy_skill(name="smart_capture")
        adapter = LegacySkillShadowAdapter()
        first = adapter.sync_one(self.db, skill.id)
        self.assertEqual(first.status, "published")
        pkg = self.db.get(AssistantSkillPackage, first.shadow_package_id)
        assert pkg is not None
        first_pub_id = pkg.published_version_id

        skill.description = "Updated capture description for intake and review."
        self.db.commit()

        second = adapter.sync_one(self.db, skill.id)
        self.assertEqual(second.status, "published")
        self.db.refresh(pkg)
        self.assertNotEqual(pkg.published_version_id, first_pub_id)
        old = self.db.get(AssistantSkillVersion, first_pub_id)
        assert old is not None
        self.assertEqual(old.version_source, "publish")
        # Old row still present and unchanged as publish history.
        publish_rows = (
            self.db.query(AssistantSkillVersion)
            .filter(
                AssistantSkillVersion.skill_package_id == pkg.id,
                AssistantSkillVersion.version_source == "publish",
            )
            .all()
        )
        self.assertEqual(len(publish_rows), 2)

    def test_native_package_stops_automatic_sync(self) -> None:
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import AssistantSkillPackage, AssistantSkillVersion
        from tests.agent_skill_test_support import create_default_model_binding

        create_default_model_binding(self.db)
        self.db.commit()
        skill, _, _ = self._create_legacy_skill(name="native_stop_skill")
        adapter = LegacySkillShadowAdapter()
        item = adapter.sync_one(self.db, skill.id)
        self.assertEqual(item.status, "published")
        pkg = self.db.get(AssistantSkillPackage, item.shadow_package_id)
        assert pkg is not None
        before = (
            self.db.query(AssistantSkillVersion)
            .filter(AssistantSkillVersion.skill_package_id == pkg.id)
            .count()
        )
        pkg.migration_state = "native"
        self.db.commit()

        skill.description = "admin owned now"
        self.db.commit()
        stopped = adapter.sync_one(self.db, skill.id)
        self.assertEqual(stopped.status, "unchanged")
        after = (
            self.db.query(AssistantSkillVersion)
            .filter(AssistantSkillVersion.skill_package_id == pkg.id)
            .count()
        )
        self.assertEqual(before, after)

    def test_unpublished_target_is_draft_unresolved(self) -> None:
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import AssistantSkillPackage

        skill, _, _ = self._create_legacy_skill(
            name="missing_target_skill",
            publish_target=False,
        )
        item = LegacySkillShadowAdapter().sync_one(self.db, skill.id)
        self.assertEqual(item.status, "draft_unresolved")
        self.assertTrue(item.diagnostics)
        self.assertEqual(item.diagnostics[0].reason_code, "target_unpublished")
        pkg = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.legacy_skill_id == skill.id)
            .one()
        )
        self.assertIsNone(pkg.published_version_id)
        self.assertIsNotNone(pkg.draft_version_id)
        self.assertFalse(pkg.catalog_enabled)

    def test_unbound_default_model_diagnostic_and_reconciliation(self) -> None:
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import AssistantSkillPackage, AssistantSkillVersion
        from tests.agent_skill_test_support import create_default_model_binding

        skill, _, _ = self._create_legacy_skill(name="unbound_model_skill")
        adapter = LegacySkillShadowAdapter()
        item = adapter.sync_one(self.db, skill.id)
        self.assertEqual(item.status, "draft_unresolved")
        self.assertTrue(
            any(d.reason_code == "unbound_default_model" for d in item.diagnostics),
            item.diagnostics,
        )
        pkg = self.db.get(AssistantSkillPackage, item.shadow_package_id)
        assert pkg is not None
        self.assertIsNone(pkg.published_version_id)
        draft_id = pkg.draft_version_id
        draft_count = (
            self.db.query(AssistantSkillVersion)
            .filter(
                AssistantSkillVersion.skill_package_id == pkg.id,
                AssistantSkillVersion.version_source == "save",
            )
            .count()
        )
        self.assertEqual(draft_count, 1)

        create_default_model_binding(self.db)
        self.db.commit()
        fixed = adapter.sync_one(self.db, skill.id)
        self.assertEqual(fixed.status, "published")
        self.db.refresh(pkg)
        self.assertIsNotNone(pkg.published_version_id)
        # Exact draft reused (no duplicate byte-identical draft).
        self.assertEqual(pkg.draft_version_id, draft_id)
        draft_count_after = (
            self.db.query(AssistantSkillVersion)
            .filter(
                AssistantSkillVersion.skill_package_id == pkg.id,
                AssistantSkillVersion.version_source == "save",
            )
            .count()
        )
        self.assertEqual(draft_count_after, 1)

    def test_non_immutable_build_revision_diagnostic(self) -> None:
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import AssistantSkillPackage
        from app.config import get_settings
        from tests.agent_skill_test_support import create_default_model_binding

        create_default_model_binding(self.db)
        self.db.commit()
        skill, _, _ = self._create_legacy_skill(name="build_rev_skill")

        os.environ["APP_ENV"] = "production"
        os.environ["APP_BUILD_REVISION"] = "development"
        get_settings.cache_clear()

        item = LegacySkillShadowAdapter().sync_one(self.db, skill.id)
        self.assertEqual(item.status, "draft_unresolved")
        self.assertTrue(
            any(d.reason_code == "non_immutable_build_revision" for d in item.diagnostics),
            item.diagnostics,
        )
        pkg = self.db.get(AssistantSkillPackage, item.shadow_package_id)
        assert pkg is not None
        self.assertIsNone(pkg.published_version_id)
        self.assertFalse(pkg.catalog_enabled)

        # Old runtime bootstrap path still works under same env.
        from app.assistant_config.service import AssistantConfigService

        AssistantConfigService(self.db).sync_system_skills()

        # Restore test env.
        os.environ["APP_ENV"] = "test"
        os.environ["APP_BUILD_REVISION"] = "test-build-task7"
        get_settings.cache_clear()

    def test_sync_all_report_statuses_and_isolation(self) -> None:
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from tests.agent_skill_test_support import create_default_model_binding

        create_default_model_binding(self.db)
        self.db.commit()
        ok, _, _ = self._create_legacy_skill(name="ok_skill")
        bad, _, _ = self._create_legacy_skill(name="bad_skill", publish_target=False)

        report = LegacySkillShadowAdapter().sync_all(self.db)
        statuses = {item.legacy_skill_id: item.status for item in report.items}
        self.assertEqual(statuses.get(ok.id), "published")
        self.assertEqual(statuses.get(bad.id), "draft_unresolved")
        self.assertGreaterEqual(report.published, 1)
        self.assertGreaterEqual(report.draft_unresolved, 1)
        for item in report.items:
            for diag in item.diagnostics:
                payload = diag.as_dict()
                self.assertNotIn("api_key", str(payload).lower())
                self.assertNotIn("authorization", str(payload).lower())
                self.assertIn("reasonCode", payload)


    def test_source_unpublishable_clears_published_pointer_and_republishes(self) -> None:
        """Published V1 → target becomes unpublished → draft_unresolved + NULL pointer.

        After the target is repaired, V2 publishes while V1 history remains.
        A failed republish after prior success must not permanently stick on unchanged.
        """
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import AssistantSkillPackage, AssistantSkillVersion
        from app.assistant_config.models import AssistantWorkflow
        from tests.agent_skill_test_support import create_default_model_binding

        create_default_model_binding(self.db)
        self.db.commit()
        skill, workflow, wf_version = self._create_legacy_skill(name="republish_stuck_skill")
        adapter = LegacySkillShadowAdapter()
        first = adapter.sync_one(self.db, skill.id)
        self.assertEqual(first.status, "published")
        pkg = self.db.get(AssistantSkillPackage, first.shadow_package_id)
        assert pkg is not None
        v1_id = pkg.published_version_id
        self.assertIsNotNone(v1_id)

        # Make target unpublishable by clearing workflow published pointer.
        workflow = self.db.get(AssistantWorkflow, workflow.id)
        assert workflow is not None
        workflow.published_version_id = None
        self.db.commit()

        # Change source so digest no longer matches (description change).
        skill.description = "now unpublishable source revision"
        self.db.commit()

        unresolved = adapter.sync_one(self.db, skill.id)
        self.assertEqual(unresolved.status, "draft_unresolved")
        self.db.refresh(pkg)
        self.assertIsNone(pkg.published_version_id)
        # V1 history row must remain.
        v1 = self.db.get(AssistantSkillVersion, v1_id)
        assert v1 is not None
        self.assertEqual(v1.version_source, "publish")

        # Re-sync with cleared pointer must not report unchanged.
        again_unresolved = adapter.sync_one(self.db, skill.id)
        self.assertEqual(again_unresolved.status, "draft_unresolved")
        self.assertNotEqual(again_unresolved.status, "unchanged")

        # Repair target → publishes V2.
        workflow.published_version_id = wf_version.id
        self.db.commit()
        repaired = adapter.sync_one(self.db, skill.id)
        self.assertEqual(repaired.status, "published")
        self.db.refresh(pkg)
        self.assertIsNotNone(pkg.published_version_id)
        self.assertNotEqual(pkg.published_version_id, v1_id)
        publish_rows = (
            self.db.query(AssistantSkillVersion)
            .filter(
                AssistantSkillVersion.skill_package_id == pkg.id,
                AssistantSkillVersion.version_source == "publish",
            )
            .all()
        )
        self.assertEqual(len(publish_rows), 2)
        publish_ids = {row.id for row in publish_rows}
        self.assertIn(v1_id, publish_ids)
        self.assertIn(pkg.published_version_id, publish_ids)

        # Idempotent after success.
        stable = adapter.sync_one(self.db, skill.id)
        self.assertEqual(stable.status, "unchanged")


class GeneralChatBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_ENV"] = "test"
        os.environ["APP_BUILD_REVISION"] = "test-build-task7"
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()
        reset_caches()

    def _seed_general_chat(self):
        from app.assistant_config.service import AssistantConfigService
        from app.assistant_config.models import AssistantSkill

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        skill = (
            self.db.query(AssistantSkill)
            .filter(AssistantSkill.name == "general_chat")
            .one()
        )
        return skill

    def test_general_chat_bridges_to_main_agent_not_package(self) -> None:
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import (
            AssistantMainAgentProfile,
            AssistantMainAgentProfileVersion,
            AssistantSkillPackage,
        )
        from app.assistant_config.models import AssistantSkill

        skill = self._seed_general_chat()
        legacy_count_before = self.db.query(AssistantSkill).count()
        adapter = LegacySkillShadowAdapter()
        item = adapter.sync_one(self.db, skill.id)
        self.assertIn(item.status, {"published", "unchanged"})

        packages = (
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.canonical_name.in_(("general-chat", "general_chat")))
            .all()
        )
        self.assertEqual(packages, [])

        profile = (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.is_default.is_(True))
            .one()
        )
        self.assertEqual(profile.legacy_skill_id, skill.id)
        self.assertIsNotNone(profile.legacy_source_digest)
        self.assertIsNotNone(profile.published_version_id)
        self.assertFalse(profile.runtime_enabled)

        published = self.db.get(
            AssistantMainAgentProfileVersion, profile.published_version_id
        )
        assert published is not None
        self.assertEqual(published.version_source, "publish")
        self.assertEqual(published.origin, "legacy")
        snap = published.snapshot or {}
        # Based on published agent system_prompt, not empty bootstrap placeholder alone.
        self.assertTrue(str(snap.get("basePrompt") or "").strip())
        # Plan 01 publish forbids non-empty control keys; tools stay in source_ref.
        self.assertEqual(snap.get("controlCapabilityKeys") or [], [])
        assert published.source_ref is not None
        self.assertEqual(published.source_ref.get("legacySkillName"), "general_chat")
        self.assertIn("agentVersionId", published.source_ref)

        # Legacy row unchanged.
        self.assertEqual(self.db.query(AssistantSkill).count(), legacy_count_before)
        reloaded = self.db.get(AssistantSkill, skill.id)
        assert reloaded is not None
        self.assertEqual(reloaded.name, "general_chat")
        self.assertTrue(reloaded.enabled)

        # Idempotent.
        again = adapter.sync_one(self.db, skill.id)
        self.assertEqual(again.status, "unchanged")

    def test_changed_published_agent_appends_main_agent_version(self) -> None:
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import (
            AssistantMainAgentProfile,
            AssistantMainAgentProfileVersion,
        )
        from app.assistant_config.models import AssistantAgentProfileVersion

        skill = self._seed_general_chat()
        adapter = LegacySkillShadowAdapter()
        first = adapter.sync_one(self.db, skill.id)
        self.assertEqual(first.status, "published")
        profile = (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.is_default.is_(True))
            .one()
        )
        first_pub = profile.published_version_id
        before_count = (
            self.db.query(AssistantMainAgentProfileVersion)
            .filter(AssistantMainAgentProfileVersion.profile_id == profile.id)
            .count()
        )

        # Append a new published agent version (do not mutate old).
        agent = skill.agent_profile
        assert agent is not None
        current = self.db.get(AssistantAgentProfileVersion, agent.published_version_id)
        assert current is not None
        new_snap = dict(current.snapshot or {})
        new_snap["system_prompt"] = (new_snap.get("system_prompt") or "") + "\nUpdated."
        new_version = AssistantAgentProfileVersion(
            agent_profile_id=agent.id,
            sequence_no=int(current.sequence_no) + 1,
            version_name="v-bridge-2",
            version_source="publish",
            snapshot=new_snap,
        )
        self.db.add(new_version)
        self.db.flush()
        agent.published_version_id = new_version.id
        self.db.commit()

        second = adapter.sync_one(self.db, skill.id)
        self.assertEqual(second.status, "published")
        self.db.refresh(profile)
        self.assertNotEqual(profile.published_version_id, first_pub)
        after_count = (
            self.db.query(AssistantMainAgentProfileVersion)
            .filter(AssistantMainAgentProfileVersion.profile_id == profile.id)
            .count()
        )
        self.assertGreater(after_count, before_count)
        # Old publish row retained.
        self.assertIsNotNone(self.db.get(AssistantMainAgentProfileVersion, first_pub))

    def test_bridge_keeps_migration_state_shadow(self) -> None:
        """Legacy general_chat bridge must not promote Main Agent to native."""
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import AssistantMainAgentProfile

        skill = self._seed_general_chat()
        adapter = LegacySkillShadowAdapter()
        item = adapter.sync_one(self.db, skill.id)
        self.assertIn(item.status, {"published", "unchanged"})

        profile = (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.is_default.is_(True))
            .one()
        )
        self.assertEqual(profile.migration_state, "shadow")
        self.assertEqual(profile.legacy_skill_id, skill.id)

        # Re-bridge remains shadow (never native promotion via legacy origin).
        again = adapter.sync_one(self.db, skill.id)
        self.assertEqual(again.status, "unchanged")
        self.db.refresh(profile)
        self.assertEqual(profile.migration_state, "shadow")

    def test_bridge_stops_after_admin_native_save(self) -> None:
        """Once admin owns the Main Agent profile, shadow bridge must not overwrite."""
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import (
            AssistantMainAgentProfile,
            AssistantMainAgentProfileVersion,
        )
        from app.assistant.skills.schemas import (
            MainAgentProfileSnapshotV1,
            PublishMainAgentProfileCommand,
            SaveMainAgentProfileDraftCommand,
            default_main_agent_profile_snapshot,
        )
        from app.assistant.skills.service import MainAgentProfileService
        from app.assistant_config.models import AssistantAgentProfileVersion

        skill = self._seed_general_chat()
        adapter = LegacySkillShadowAdapter()
        first = adapter.sync_one(self.db, skill.id)
        self.assertEqual(first.status, "published")

        profile = (
            self.db.query(AssistantMainAgentProfile)
            .filter(AssistantMainAgentProfile.is_default.is_(True))
            .one()
        )
        shadow_pub_id = profile.published_version_id
        shadow_digest = profile.legacy_source_digest
        self.assertEqual(profile.migration_state, "shadow")

        # Admin takes ownership via native save + publish.
        svc = MainAgentProfileService(self.db)
        admin_snap = MainAgentProfileSnapshotV1.model_validate(
            default_main_agent_profile_snapshot()
            .model_dump(by_alias=True)
            | {"basePrompt": "admin owned main agent prompt"}
        )
        draft = svc.save_draft(
            profile.id,
            SaveMainAgentProfileDraftCommand(
                snapshot=admin_snap,
                version_name="admin-native",
                origin="api",
            ),
        )
        refreshed = svc.get_default()
        self.assertEqual(refreshed.migration_state, "native")
        svc.publish(
            profile.id,
            PublishMainAgentProfileCommand(draft_version_id=draft.id),
        )
        self.db.refresh(profile)
        admin_pub_id = profile.published_version_id
        self.assertNotEqual(admin_pub_id, shadow_pub_id)
        self.assertEqual(profile.migration_state, "native")

        # Change published agent so bridge would otherwise append a new version.
        agent = skill.agent_profile
        assert agent is not None
        current = self.db.get(AssistantAgentProfileVersion, agent.published_version_id)
        assert current is not None
        new_snap = dict(current.snapshot or {})
        new_snap["system_prompt"] = (new_snap.get("system_prompt") or "") + "\nBridge should ignore."
        new_version = AssistantAgentProfileVersion(
            agent_profile_id=agent.id,
            sequence_no=int(current.sequence_no) + 1,
            version_name="v-bridge-ignored",
            version_source="publish",
            snapshot=new_snap,
        )
        self.db.add(new_version)
        self.db.flush()
        agent.published_version_id = new_version.id
        self.db.commit()

        stopped = adapter.sync_one(self.db, skill.id)
        self.assertEqual(stopped.status, "unchanged")
        reason_codes = [d.reason_code for d in stopped.diagnostics]
        self.assertIn("shadow_sync_stopped_native", reason_codes)

        self.db.refresh(profile)
        self.assertEqual(profile.migration_state, "native")
        self.assertEqual(profile.published_version_id, admin_pub_id)
        # Admin publish content must not be overwritten by bridge.
        published = self.db.get(AssistantMainAgentProfileVersion, admin_pub_id)
        assert published is not None
        self.assertEqual(
            (published.snapshot or {}).get("basePrompt"),
            "admin owned main agent prompt",
        )
        # legacy_source_digest may still hold the pre-native bridge digest; bridge
        # must not advance it after native ownership.
        self.assertEqual(profile.legacy_source_digest, shadow_digest)



class BootstrapHookInvarianceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()
        os.environ["APP_ENV"] = "test"
        os.environ["APP_BUILD_REVISION"] = "test-build-task7"
        from app.config import get_settings

        get_settings.cache_clear()
        from tests._db import make_session

        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()
        reset_caches()

    def test_catalog_warm_runs_shadow_sync_without_breaking_legacy(self) -> None:
        from app.assistant.skills.models import AssistantSkillPackage
        from app.assistant_config.models import AssistantSkill
        from app.assistant_config.service import AssistantConfigService
        from tests.agent_skill_test_support import create_default_model_binding

        create_default_model_binding(self.db)
        self.db.commit()

        svc = AssistantConfigService(self.db)
        # Force full sync path.
        with mock.patch.object(svc, "_system_catalog_needs_sync", return_value=True):
            svc.ensure_system_catalog_warm()

        skills = self.db.query(AssistantSkill).filter(AssistantSkill.is_system.is_(True)).all()
        self.assertGreaterEqual(len(skills), 1)
        general = next(s for s in skills if s.name == "general_chat")
        self.assertTrue(general.enabled)
        self.assertIsNotNone(general.agent_profile_id)

        # Shadow packages exist for non-general_chat system skills (may be unresolved
        # without full model closures, but aggregates/drafts must exist when targets resolve).
        for skill in skills:
            if skill.name == "general_chat":
                pkg = (
                    self.db.query(AssistantSkillPackage)
                    .filter(AssistantSkillPackage.legacy_skill_id == skill.id)
                    .one_or_none()
                )
                self.assertIsNone(pkg)
                continue
            pkg = (
                self.db.query(AssistantSkillPackage)
                .filter(AssistantSkillPackage.legacy_skill_id == skill.id)
                .one_or_none()
            )
            # Sync is best-effort after catalog; when present must stay disabled.
            if pkg is not None:
                self.assertEqual(pkg.migration_state, "shadow")
                self.assertFalse(pkg.catalog_enabled)


if __name__ == "__main__":
    unittest.main()

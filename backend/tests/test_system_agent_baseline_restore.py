from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class SystemAgentBaselineRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def _create_system_agent_with_legacy_baseline(self):
        from app.assistant_config.models import AssistantAgentProfile  # noqa: E402
        from app.assistant_config.schemas import AgentPublishDraftInput  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        profile = (
            self.db.query(AssistantAgentProfile)
            .filter(
                AssistantAgentProfile.name == "general_chat__agent",
                AssistantAgentProfile.is_system.is_(True),
            )
            .first()
        )
        self.assertIsNotNone(profile)

        legacy_draft = AgentPublishDraftInput.model_validate(
            {
                "system_prompt": "legacy prompt",
                "tools": ["list_tags"],
                "kb_config": {"enabled": False},
                "model_source": "default",
                "model_id": None,
            }
        )
        profile.description = "legacy baseline"
        profile.system_prompt = legacy_draft.system_prompt
        profile.tools = list(legacy_draft.tools or [])
        profile.kb_config = legacy_draft.kb_config
        mutated_version = svc._create_agent_profile_version(  # noqa: SLF001
            agent_profile=profile,
            draft=legacy_draft,
            version_source="publish",
            version_name="Legacy baseline",
        )
        profile.draft_version_id = mutated_version.id
        profile.published_version_id = mutated_version.id
        self.db.commit()
        return svc, profile.id

    def test_sync_system_agent_baseline_uses_json_default(self) -> None:
        from app.assistant.skill_catalog.defaults_loader import get_system_agent_baseline  # noqa: E402
        from app.assistant_config.models import AssistantAgentProfileVersion  # noqa: E402

        svc, profile_id = self._create_system_agent_with_legacy_baseline()
        canonical = get_system_agent_baseline("general_chat")
        self.assertIsNotNone(canonical)

        current_published = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(
                AssistantAgentProfileVersion.agent_profile_id == profile_id,
            )
            .order_by(AssistantAgentProfileVersion.sequence_no.desc())
            .first()
        )
        self.assertIsNotNone(current_published)
        self.assertNotEqual((current_published.snapshot or {}).get("system_prompt"), canonical.system_prompt)

        svc.sync_system_skills()

        restored = svc.get_agent_profile(profile_id)
        restored_draft = svc._get_agent_profile_draft(restored)  # noqa: SLF001
        self.assertEqual(restored_draft.system_prompt, canonical.system_prompt)
        self.assertEqual(list(restored_draft.tools or []), list(canonical.tools or []))
        self.assertEqual(restored_draft.model_source, canonical.model_source)
        self.assertEqual(restored_draft.model_id, canonical.model_id)
        self.assertEqual(
            bool((restored_draft.kb_config or {}).get("enabled", False)),
            bool((canonical.kb_config or {}).get("enabled", False)),
        )

        remaining_versions = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(AssistantAgentProfileVersion.agent_profile_id == profile_id)
            .all()
        )
        self.assertEqual(len(remaining_versions), 1)
        self.assertEqual((remaining_versions[0].snapshot or {}).get("system_prompt"), canonical.system_prompt)
        self.assertEqual(list((remaining_versions[0].snapshot or {}).get("tools") or []), list(canonical.tools or []))


    @unittest.skip("legacy AssistantSkill shadow bridge removed (Plan 10 B2)")
    def test_general_chat_legacy_row_unaffected_by_shadow_bridge(self) -> None:
        """Legacy runtime invariance: general_chat remains the old-runtime skill."""
        from app.assistant.skills.legacy_adapter import LegacySkillShadowAdapter
        from app.assistant.skills.models import AssistantSkillPackage
        from app.assistant_config.models import AssistantSkill
        from app.assistant_config.service import AssistantConfigService

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        skill = (
            self.db.query(AssistantSkill)
            .filter(AssistantSkill.name == "general_chat", AssistantSkill.is_system.is_(True))
            .one()
        )
        before_enabled = skill.enabled
        before_agent = skill.agent_profile_id
        LegacySkillShadowAdapter().sync_one(self.db, skill.id)
        reloaded = self.db.get(AssistantSkill, skill.id)
        assert reloaded is not None
        self.assertEqual(reloaded.enabled, before_enabled)
        self.assertEqual(reloaded.agent_profile_id, before_agent)
        self.assertEqual(reloaded.name, "general_chat")
        self.assertIsNone(
            self.db.query(AssistantSkillPackage)
            .filter(AssistantSkillPackage.canonical_name.in_(("general-chat", "general_chat")))
            .one_or_none()
        )


if __name__ == "__main__":
    unittest.main()

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
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.schemas import AssistantAgentProfileCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        profile = svc.create_agent_profile(
            AssistantAgentProfileCreateRequest(
                name="general_chat__agent",
                description="legacy baseline",
                system_prompt="legacy prompt",
                tools=["list_tags"],
                kb_config={"enabled": False},
                model_source="default",
                enabled=True,
            )
        )
        profile.is_system = True
        skill = AssistantSkill(
            name="general_chat",
            description="默认兜底对话",
            intent_examples=[],
            tools=list(profile.tools or []),
            mode="langgraph",
            langgraph_pattern="agent_loop",
            system_prompt=profile.system_prompt,
            kb_config=profile.kb_config,
            is_system=True,
            enabled=True,
            workflow_id=None,
            agent_profile_id=profile.id,
        )
        self.db.add(skill)
        self.db.commit()
        return svc, profile.id

    def test_rollback_system_agent_baseline_uses_json_default(self) -> None:
        from app.assistant.skill_catalog.defaults_loader import get_system_agent_baseline  # noqa: E402
        from app.assistant_config.models import AssistantAgentProfileVersion  # noqa: E402

        svc, profile_id = self._create_system_agent_with_legacy_baseline()
        canonical = get_system_agent_baseline("general_chat")
        self.assertIsNotNone(canonical)

        baseline_version = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(
                AssistantAgentProfileVersion.agent_profile_id == profile_id,
                AssistantAgentProfileVersion.version_source == "publish",
            )
            .order_by(AssistantAgentProfileVersion.sequence_no.asc())
            .first()
        )
        self.assertIsNotNone(baseline_version)
        self.assertNotEqual((baseline_version.snapshot or {}).get("system_prompt"), canonical.system_prompt)

        response = svc.rollback_agent_profile_version(profile_id, baseline_version.id)
        self.assertIsNotNone(response.agent_draft)
        self.assertEqual(response.draft_version_id, baseline_version.id)
        self.assertEqual(response.agent_draft.system_prompt, canonical.system_prompt)
        self.assertEqual(list(response.agent_draft.tools or []), list(canonical.tools or []))
        self.assertEqual(response.agent_draft.model_source, canonical.model_source)
        self.assertEqual(response.agent_draft.model_id, canonical.model_id)
        self.assertEqual(
            bool((response.agent_draft.kb_config or {}).get("enabled", False)),
            bool((canonical.kb_config or {}).get("enabled", False)),
        )

        self.db.refresh(baseline_version)
        self.assertEqual((baseline_version.snapshot or {}).get("system_prompt"), canonical.system_prompt)
        self.assertEqual(list((baseline_version.snapshot or {}).get("tools") or []), list(canonical.tools or []))


if __name__ == "__main__":
    unittest.main()


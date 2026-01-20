from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


class AssistantConfigServiceMoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_create_update_delete_remote_tool(self) -> None:
        from app.assistant_config.models import AssistantTool  # noqa: E402
        from app.assistant_config.schemas import AssistantToolCreateRequest, AssistantToolUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        with patch("app.assistant_config.service.encrypt_api_key", return_value="enc"), patch(
            "app.assistant_config.service.api_key_hint", return_value="****"
        ):
            tool = svc.create_tool(
                AssistantToolCreateRequest(
                    name="rt",
                    description="d",
                    kind="remote",
                    enabled=True,
                    endpoint_url="https://api.example.com/endpoint",
                    http_method="POST",
                    api_key="k",
                )
            )
        self.assertEqual(self.db.query(AssistantTool).count(), 1)

        tool2 = svc.update_tool(
            tool.id,
            AssistantToolUpdateRequest(description="d2", timeout_seconds=10),
        )
        self.assertEqual(tool2.description, "d2")
        self.assertEqual(tool2.timeout_seconds, 10)

        svc.delete_tool(tool.id)
        self.assertEqual(self.db.query(AssistantTool).count(), 0)

    def test_create_update_delete_skill_non_system(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.schemas import AssistantSkillStepInput  # noqa: E402
        from app.assistant_config.schemas import AssistantSkillCreateRequest, AssistantSkillUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        created = svc.create_skill(
            AssistantSkillCreateRequest(
                name="s1",
                description="d",
                intent_examples=[],
                tools=[],
                mode="steps",
                system_prompt=None,
                enabled=True,
                steps=[AssistantSkillStepInput(type="summary", instruction="x")],
            )
        )
        self.assertEqual(self.db.query(AssistantSkill).count(), 1)

        updated = svc.update_skill(created.id, AssistantSkillUpdateRequest(description="d2"))
        self.assertEqual(updated.description, "d2")

        svc.delete_skill(created.id)
        self.assertEqual(self.db.query(AssistantSkill).count(), 0)

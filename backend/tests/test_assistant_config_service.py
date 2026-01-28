from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()

from app.common.exceptions import ApiException  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402


class _SysTool:
    def __init__(self, name: str, description: str | None = None) -> None:
        self.name = name
        self.description = description


class AssistantConfigServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    def test_sync_system_tools_does_not_seed_records(self) -> None:
        from app.assistant_config.models import AssistantTool  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        with patch("app.assistant_config.service.ToolRegistry.list_system_tools", return_value=[_SysTool("t1", "d")]):
            svc.sync_system_tools()

        tool = self.db.query(AssistantTool).filter(AssistantTool.name == "t1").first()
        self.assertIsNone(tool)

    def test_sync_system_tools_prunes_stale_and_skill_refs(self) -> None:
        from app.assistant_config.models import AssistantSkill, AssistantTool  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        # Pre-existing system tool that no longer exists in code
        self.db.add(AssistantTool(name="old_tool", description="d", kind="local", is_system=True, enabled=True))
        self.db.add(
            AssistantSkill(
                name="s1",
                description="d",
                intent_examples=[],
                tools=["old_tool", "t1"],
                mode="steps",
                system_prompt=None,
                is_system=False,
                enabled=True,
            )
        )
        self.db.commit()

        svc = AssistantConfigService(self.db)
        with patch("app.assistant_config.service.ToolRegistry.list_system_tools", return_value=[_SysTool("t1", "d")]):
            svc.sync_system_tools()

        self.assertIsNone(self.db.query(AssistantTool).filter(AssistantTool.name == "old_tool").first())
        skill = self.db.query(AssistantSkill).filter(AssistantSkill.name == "s1").first()
        self.assertIsNotNone(skill)
        self.assertEqual(skill.tools, ["t1"])

    def test_set_system_tool_enabled_creates_override_only_when_disabled(self) -> None:
        from app.assistant_config.models import AssistantTool  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        with patch("app.assistant_config.service.ToolRegistry.list_system_tools", return_value=[_SysTool("t1", "d")]):
            svc.set_system_tool_enabled("t1", enabled=False)

        rec = self.db.query(AssistantTool).filter(AssistantTool.name == "t1").first()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.kind, "local")
        self.assertTrue(rec.is_system)
        self.assertFalse(rec.enabled)

        with patch("app.assistant_config.service.ToolRegistry.list_system_tools", return_value=[_SysTool("t1", "d")]):
            svc.set_system_tool_enabled("t1", enabled=True)

        rec2 = self.db.query(AssistantTool).filter(AssistantTool.name == "t1").first()
        self.assertIsNone(rec2)

    def test_sync_system_tools_integrity_error_40910(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        with (
            patch("app.assistant_config.service.ToolRegistry.list_system_tools", return_value=[_SysTool("t1", "d")]),
            patch.object(self.db, "commit", side_effect=IntegrityError("stmt", "params", Exception("orig"))),
        ):
            with self.assertRaises(ApiException) as ctx:
                svc.sync_system_tools()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40910)

    def test_sync_system_skills_creates_records_and_backfills(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        class FakeStep:
            def __init__(self) -> None:
                self.type = "analysis"
                self.instruction = "x"
                self.tool_name = None
                self.args_from = None

        class FakeSkill:
            name = "s1"
            description = "d"
            intent_examples = []
            tools = []
            mode = "agent"
            system_prompt = "p"
            steps = [FakeStep()]

        # existing record that should be backfilled (steps->agent)
        existing = AssistantSkill(
            name="s1",
            description="old",
            intent_examples=[],
            tools=[],
            mode="steps",
            system_prompt=None,
            is_system=True,
            enabled=True,
        )
        self.db.add(existing)
        self.db.commit()

        svc = AssistantConfigService(self.db)
        with patch("app.assistant_config.service.SkillRegistry.list_system_skills", return_value=[FakeSkill()]):
            svc.sync_system_skills()

        skill = self.db.query(AssistantSkill).filter(AssistantSkill.name == "s1").first()
        self.assertIsNotNone(skill)
        self.assertTrue(skill.is_system)
        # gentle backfill applied
        self.assertEqual(skill.mode, "agent")
        self.assertEqual(skill.system_prompt, "p")

    def test_sync_system_skills_integrity_error_40911(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)

        class FakeSkill:
            name = "s1"
            description = "d"
            intent_examples = []
            tools = []
            mode = "steps"
            system_prompt = None
            steps = []

        with (
            patch("app.assistant_config.service.SkillRegistry.list_system_skills", return_value=[FakeSkill()]),
            patch.object(self.db, "commit", side_effect=IntegrityError("stmt", "params", Exception("orig"))),
        ):
            with self.assertRaises(ApiException) as ctx:
                svc.sync_system_skills()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40911)

    def test_update_system_tool_only_allows_enabled(self) -> None:
        from app.assistant_config.models import AssistantTool  # noqa: E402
        from app.assistant_config.schemas import AssistantToolUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        tool = AssistantTool(name="t", description="d", kind="local", is_system=True, enabled=True)
        self.db.add(tool)
        self.db.commit()

        svc = AssistantConfigService(self.db)

        with self.assertRaises(ApiException) as ctx:
            svc.update_tool(tool.id, AssistantToolUpdateRequest(description="x"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40012)

        updated = svc.update_tool(tool.id, AssistantToolUpdateRequest(enabled=False))
        self.assertFalse(updated.enabled)

    def test_delete_system_tool_forbidden(self) -> None:
        from app.assistant_config.models import AssistantTool  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        tool = AssistantTool(name="t", description="d", kind="local", is_system=True, enabled=True)
        self.db.add(tool)
        self.db.commit()

        svc = AssistantConfigService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.delete_tool(tool.id)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40013)

    def test_create_tool_kind_local_reserved(self) -> None:
        from app.assistant_config.schemas import AssistantToolCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        req = AssistantToolCreateRequest(name="x", description=None, kind="local", enabled=True)
        with self.assertRaises(ApiException) as ctx:
            svc.create_tool(req)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40010)

    def test_reset_skill_requires_confirm_and_system(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        skill = AssistantSkill(name="s", description="d", is_system=False, enabled=True, mode="steps")
        self.db.add(skill)
        self.db.commit()

        svc = AssistantConfigService(self.db)

        with self.assertRaises(ApiException) as ctx1:
            svc.reset_skill(skill.id, confirm=False)
        self.assertEqual(ctx1.exception.code, 40023)

        with self.assertRaises(ApiException) as ctx2:
            svc.reset_skill(skill.id, confirm=True)
        self.assertEqual(ctx2.exception.code, 40024)

    def test_reset_skill_default_not_found_40412(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        skill = AssistantSkill(name="s", description="d", is_system=True, enabled=True, mode="steps")
        self.db.add(skill)
        self.db.commit()

        svc = AssistantConfigService(self.db)

        with patch("app.assistant.skills.definitions.get_skill_by_name", return_value=None):
            with self.assertRaises(ApiException) as ctx:
                svc.reset_skill(skill.id, confirm=True)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.code, 40412)

    def test_update_system_skill_cannot_rename(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.schemas import AssistantSkillUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        skill = AssistantSkill(name="s", description="d", is_system=True, enabled=True, mode="steps")
        self.db.add(skill)
        self.db.commit()

        svc = AssistantConfigService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.update_skill(skill.id, AssistantSkillUpdateRequest(name="s2"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40021)

    def test_delete_system_skill_forbidden(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        skill = AssistantSkill(name="s", description="d", is_system=True, enabled=True, mode="steps")
        self.db.add(skill)
        self.db.commit()

        svc = AssistantConfigService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.delete_skill(skill.id)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40022)

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

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

    def _create_workflow_target(self):
        from app.assistant_config.models import AssistantWorkflow  # noqa: E402

        workflow = AssistantWorkflow(
            name=f"wf_target_{uuid4().hex[:10]}",
            description="test workflow target",
            enabled=True,
        )
        self.db.add(workflow)
        self.db.flush()
        return workflow

    def _create_agent_target(self):
        from app.assistant_config.models import AssistantAgentProfile  # noqa: E402

        profile = AssistantAgentProfile(
            name=f"agent_target_{uuid4().hex[:10]}",
            description="test agent target",
            system_prompt="",
            tools=[],
            kb_config={"enabled": False},
            enabled=True,
        )
        self.db.add(profile)
        self.db.flush()
        return profile

    def _new_skill_with_binding(self, **kwargs):
        from app.assistant_config.models import AssistantSkill  # noqa: E402

        has_workflow = kwargs.get("workflow_id") is not None
        has_agent = kwargs.get("agent_profile_id") is not None
        if not has_workflow and not has_agent:
            pattern = str(kwargs.get("langgraph_pattern") or "agent_loop")
            if pattern == "workflow_dag":
                workflow = self._create_workflow_target()
                kwargs["workflow_id"] = workflow.id
                kwargs["agent_profile_id"] = None
            else:
                profile = self._create_agent_target()
                kwargs["workflow_id"] = None
                kwargs["agent_profile_id"] = profile.id
        return AssistantSkill(**kwargs)

    def _get_seeded_system_skill(self, name: str):
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        skill = (
            self.db.query(AssistantSkill)
            .filter(
                AssistantSkill.name == name,
                AssistantSkill.is_system.is_(True),
            )
            .first()
        )
        self.assertIsNotNone(skill)
        return svc, skill

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
            self._new_skill_with_binding(
                name="s1",
                description="d",
                intent_examples=[],
                tools=["old_tool", "t1"],
                mode="langgraph",
                langgraph_pattern="agent_loop",
                system_prompt="p",
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

    def test_validate_workflow_dependencies_adds_implicit_kb_search_for_agent_and_knowledge_nodes(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        workflow = SimpleNamespace(
            nodes=[
                {"node_id": "start", "node_type": "start", "config": {}},
                {"node_id": "kr_1", "node_type": "knowledge_retrieval", "config": {"query": "{{start.user_input}}"}},
                {
                    "node_id": "agent_1",
                    "node_type": "agent",
                    "config": {
                        "toolNames": ["search_entries"],
                        "knowledgeEnabled": True,
                    },
                },
                {
                    "node_id": "iter_1",
                    "node_type": "iteration",
                    "config": {
                        "bodyNodes": [
                            {"nodeId": "start", "nodeType": "start", "config": {}},
                            {
                                "nodeId": "agent_body",
                                "nodeType": "agent",
                                "config": {"toolNames": [], "knowledgeEnabled": True},
                            },
                        ],
                        "bodyEdges": [],
                    },
                },
            ]
        )

        with patch(
            "app.assistant_config.service.ToolRegistry.list_system_tools",
            return_value=[_SysTool("search_entries")],
        ):
            deps = svc.validate_workflow_dependencies(workflow)

        self.assertEqual(deps, {"search_entries", "kb_search"})

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

    def test_sync_system_skills_forces_langgraph_mode(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        class FakeSkill:
            name = "s1"
            description = "d"
            intent_examples = []
            tools = []
            mode = "langgraph"
            langgraph_pattern = "agent_loop"
            system_prompt = "p"
            kb = None
            workflow_nodes = []
            workflow_edges = []

        # existing record should be normalized to langgraph
        existing = self._new_skill_with_binding(
            name="s1",
            description="old",
            intent_examples=[],
            tools=[],
            mode="agent",
            langgraph_pattern=None,
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
        self.assertEqual(skill.mode, "langgraph")
        self.assertEqual(skill.langgraph_pattern, "agent_loop")

    def test_sync_system_skills_backfills_missing_langgraph_pattern(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        class FakeSkill:
            name = "smart_capture"
            description = "d"
            intent_examples = []
            tools = []
            mode = "langgraph"
            langgraph_pattern = "workflow_dag"
            system_prompt = None
            kb = None
            workflow_nodes = []
            workflow_edges = []

        existing = self._new_skill_with_binding(
            name="smart_capture",
            description="old",
            intent_examples=[],
            tools=[],
            mode="langgraph",
            langgraph_pattern=None,
            system_prompt=None,
            is_system=True,
            enabled=True,
        )
        self.db.add(existing)
        self.db.commit()

        svc = AssistantConfigService(self.db)
        with patch("app.assistant_config.service.SkillRegistry.list_system_skills", return_value=[FakeSkill()]):
            svc.sync_system_skills()

        skill = self.db.query(AssistantSkill).filter(AssistantSkill.name == "smart_capture").first()
        self.assertIsNotNone(skill)
        self.assertEqual(skill.mode, "langgraph")
        self.assertEqual(skill.langgraph_pattern, "workflow_dag")

    def test_sync_system_skills_migrates_tool_text_refs_to_result(self) -> None:
        svc, skill = self._get_seeded_system_skill("smart_capture")
        self.assertIsNotNone(skill)
        self.assertIsNotNone(skill.workflow)

        llm_node = next((node for node in (skill.workflow.nodes or []) if node.node_id == "llm_output"), None)
        self.assertIsNotNone(llm_node)
        llm_node.config = {
            **dict(llm_node.config or {}),
            "userInput": "{{tool_create.text}}",
        }
        self.db.commit()

        svc.sync_system_skills()

        refreshed = svc.get_skill(skill.id)
        self.assertIsNotNone(refreshed.workflow)
        llm_node = next((node for node in (refreshed.workflow.nodes or []) if node.node_id == "llm_output"), None)
        self.assertIsNotNone(llm_node)
        self.assertEqual(llm_node.config.get("userInput"), "{{tool_create.result}}")

    def test_sync_system_skills_migrates_legacy_workflow_output_to_output_node(self) -> None:
        svc, skill = self._get_seeded_system_skill("smart_capture")
        self.assertIsNotNone(skill)
        self.assertIsNotNone(skill.workflow)

        workflow = skill.workflow
        for node in list(workflow.nodes or []):
            if node.node_id in {"output_created", "output_cancelled"}:
                self.db.delete(node)
        for edge in list(workflow.edges or []):
            if edge.target_node_id in {"output_created", "output_cancelled"} or edge.source_node_id in {
                "output_created",
                "output_cancelled",
            }:
                self.db.delete(edge)
        llm_node = next((node for node in (workflow.nodes or []) if node.node_id == "llm_output"), None)
        self.assertIsNotNone(llm_node)
        llm_node.config = {**dict(llm_node.config or {}), "isOutput": True}
        self.db.commit()

        svc.sync_system_skills()

        refreshed = svc.get_skill(skill.id)
        self.assertIsNotNone(refreshed.workflow)
        nodes = list(refreshed.workflow.nodes or [])
        edges = list(refreshed.workflow.edges or [])
        output_nodes = sorted(node.node_id for node in nodes if node.node_type == "output")
        self.assertEqual(output_nodes, ["output_cancelled", "output_created"])
        self.assertFalse(any(isinstance(node.config, dict) and "isOutput" in node.config for node in nodes))
        self.assertTrue(any(edge.target_node_id == "output_created" for edge in edges))
        self.assertTrue(any(edge.target_node_id == "output_cancelled" for edge in edges))
        self.assertFalse(any(edge.source_node_id in {"output_created", "output_cancelled"} for edge in edges))

    def test_reset_skill_restores_langgraph_pattern(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        class DefaultSkill:
            description = "d"
            intent_examples = []
            tools = []
            mode = "langgraph"
            langgraph_pattern = "workflow_dag"
            system_prompt = None
            kb = None
            workflow_nodes = []
            workflow_edges = []

        skill = self._new_skill_with_binding(
            name="smart_capture",
            description="old",
            intent_examples=[],
            tools=[],
            mode="langgraph",
            langgraph_pattern=None,
            system_prompt=None,
            is_system=True,
            enabled=True,
        )
        self.db.add(skill)
        self.db.commit()

        svc = AssistantConfigService(self.db)
        with patch("app.assistant.skill_catalog.definitions.get_skill_by_name", return_value=DefaultSkill()):
            out = svc.reset_skill(skill.id, confirm=True)

        self.assertEqual(out.mode, "langgraph")
        self.assertEqual(out.langgraph_pattern, "workflow_dag")

    def test_sync_system_skills_integrity_error_40920(self) -> None:
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)

        class FakeSkill:
            name = "s1"
            description = "d"
            intent_examples = []
            tools = []
            mode = "langgraph"
            langgraph_pattern = "agent_loop"
            system_prompt = None
            kb = None
            workflow_nodes = []
            workflow_edges = []

        with (
            patch("app.assistant_config.service.SkillRegistry.list_system_skills", return_value=[FakeSkill()]),
            patch.object(self.db, "commit", side_effect=IntegrityError("stmt", "params", Exception("orig"))),
        ):
            with self.assertRaises(ApiException) as ctx:
                svc.sync_system_skills()
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, 40920)

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

        skill = self._new_skill_with_binding(
            name="s",
            description="d",
            is_system=False,
            enabled=True,
            mode="langgraph",
            langgraph_pattern="agent_loop",
            system_prompt="x",
        )
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

        skill = self._new_skill_with_binding(
            name="s",
            description="d",
            is_system=True,
            enabled=True,
            mode="langgraph",
            langgraph_pattern="agent_loop",
            system_prompt="x",
        )
        self.db.add(skill)
        self.db.commit()

        svc = AssistantConfigService(self.db)

        with patch("app.assistant.skill_catalog.definitions.get_skill_by_name", return_value=None):
            with self.assertRaises(ApiException) as ctx:
                svc.reset_skill(skill.id, confirm=True)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.code, 40412)

    def test_reset_skill_rebinds_to_system_workflow_and_keeps_custom_workflow_unchanged(self) -> None:
        from app.assistant_config.models import AssistantWorkflowVersion  # noqa: E402
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        skill = self._get_seeded_system_skill("quick_stats")[1]
        canonical_workflow_id = skill.workflow_id
        self.assertIsNotNone(canonical_workflow_id)

        custom_workflow = svc.create_workflow(
            AssistantWorkflowCreateRequest(
                name="user_wf_for_reset",
                description="custom wf",
                enabled=True,
            )
        )
        custom_workflow_id = custom_workflow.id
        custom_workflow_version = custom_workflow.workflow_version
        custom_positions = sorted((node.node_id, node.position_x, node.position_y) for node in custom_workflow.nodes)

        skill.description = "mutated"
        skill.intent_examples = ["x"]
        skill.workflow_id = custom_workflow_id
        skill.agent_profile_id = None
        self.db.commit()

        svc.reset_skill(skill.id, confirm=True)

        out_skill = svc.get_skill(skill.id)
        self.assertEqual(out_skill.workflow_id, canonical_workflow_id)
        self.assertEqual(out_skill.agent_profile_id, None)
        self.assertEqual(out_skill.langgraph_pattern, "workflow_dag")

        reset_target = svc.get_workflow(out_skill.workflow_id)
        self.assertTrue(reset_target.is_system)
        self.assertEqual(reset_target.name, "quick_stats__workflow")
        self.assertEqual(reset_target.draft_version_id, reset_target.published_version_id)

        reset_versions = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == reset_target.id)
            .all()
        )
        self.assertEqual(len(reset_versions), 1)
        self.assertEqual(reset_versions[0].version_source, "publish")
        self.assertEqual(reset_versions[0].id, reset_target.published_version_id)

        custom_after = svc.get_workflow(custom_workflow_id)
        self.assertEqual(custom_after.workflow_version, custom_workflow_version)
        self.assertEqual(
            sorted((node.node_id, node.position_x, node.position_y) for node in custom_after.nodes),
            custom_positions,
        )

    def test_reset_skill_rebinds_to_system_agent_and_keeps_custom_agent_unchanged(self) -> None:
        from app.assistant.skill_catalog.defaults_loader import get_system_agent_baseline  # noqa: E402
        from app.assistant_config.models import AssistantAgentProfileVersion  # noqa: E402
        from app.assistant_config.schemas import AssistantAgentProfileCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        skill = self._get_seeded_system_skill("general_chat")[1]
        canonical_agent_id = skill.agent_profile_id
        self.assertIsNotNone(canonical_agent_id)

        custom_agent = svc.create_agent_profile(
            AssistantAgentProfileCreateRequest(
                name="user_agent_for_reset",
                description="custom agent",
                system_prompt="custom prompt",
                tools=["list_entry_types"],
                kb_config={"enabled": False},
                model_source="default",
                enabled=True,
            )
        )
        custom_agent_id = custom_agent.id
        custom_prompt = custom_agent.system_prompt
        custom_tools = list(custom_agent.tools or [])
        custom_kb_enabled = bool((custom_agent.kb_config or {}).get("enabled", False))

        skill.description = "mutated"
        skill.tools = list(custom_tools)
        skill.system_prompt = custom_prompt
        skill.kb_config = {"enabled": custom_kb_enabled}
        skill.workflow_id = None
        skill.agent_profile_id = custom_agent_id
        self.db.commit()

        svc.reset_skill(skill.id, confirm=True)

        out_skill = svc.get_skill(skill.id)
        self.assertEqual(out_skill.agent_profile_id, canonical_agent_id)
        self.assertEqual(out_skill.workflow_id, None)
        self.assertEqual(out_skill.langgraph_pattern, "agent_loop")

        reset_profile = svc.get_agent_profile(out_skill.agent_profile_id)
        canonical = get_system_agent_baseline("general_chat")
        self.assertIsNotNone(canonical)
        self.assertTrue(reset_profile.is_system)
        self.assertEqual(reset_profile.name, "general_chat__agent")
        self.assertEqual(reset_profile.system_prompt, canonical.system_prompt)
        self.assertEqual(list(reset_profile.tools or []), list(canonical.tools or []))
        self.assertEqual(
            bool((reset_profile.kb_config or {}).get("enabled", False)),
            bool((canonical.kb_config or {}).get("enabled", False)),
        )
        self.assertEqual(reset_profile.draft_version_id, reset_profile.published_version_id)

        reset_versions = (
            self.db.query(AssistantAgentProfileVersion)
            .filter(AssistantAgentProfileVersion.agent_profile_id == reset_profile.id)
            .all()
        )
        self.assertEqual(len(reset_versions), 1)
        self.assertEqual(reset_versions[0].version_source, "publish")
        self.assertEqual(reset_versions[0].id, reset_profile.published_version_id)

        custom_after = svc.get_agent_profile(custom_agent_id)
        self.assertEqual(custom_after.system_prompt, custom_prompt)
        self.assertEqual(list(custom_after.tools or []), custom_tools)
        self.assertEqual(bool((custom_after.kb_config or {}).get("enabled", False)), custom_kb_enabled)

    def test_reset_all_system_skills_rebinds_only_system_skills(self) -> None:
        from app.assistant_config.models import AssistantSkill, AssistantWorkflowVersion  # noqa: E402
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        system_skill = self._get_seeded_system_skill("quick_stats")[1]
        canonical_workflow_id = system_skill.workflow_id
        self.assertIsNotNone(canonical_workflow_id)

        custom_workflow = svc.create_workflow(
            AssistantWorkflowCreateRequest(
                name="user_wf_for_reset_all",
                description="custom wf",
                enabled=True,
            )
        )
        custom_workflow_id = custom_workflow.id
        custom_workflow_version = custom_workflow.workflow_version

        system_skill.description = "mutated system skill"
        system_skill.workflow_id = custom_workflow_id
        system_skill.agent_profile_id = None
        custom_skill = AssistantSkill(
            name="my_custom_skill",
            description="custom skill",
            intent_examples=[],
            tools=[],
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            system_prompt=None,
            kb_config={"enabled": False},
            is_system=False,
            enabled=True,
            workflow_id=custom_workflow_id,
            agent_profile_id=None,
        )
        self.db.add(custom_skill)
        self.db.commit()

        result = svc.reset_all_system_skills(confirm=True)

        self.assertGreaterEqual(result["resetCount"], 1)
        self.assertEqual(result["createdCount"], 0)
        self.assertIn("quick_stats", {item["name"] for item in result["affected"]})
        self.assertNotIn("my_custom_skill", {item["name"] for item in result["affected"]})

        refreshed_system_skill = svc.get_skill(system_skill.id)
        self.assertEqual(refreshed_system_skill.workflow_id, canonical_workflow_id)
        self.assertEqual(refreshed_system_skill.agent_profile_id, None)

        refreshed_custom_skill = svc.get_skill(custom_skill.id)
        self.assertEqual(refreshed_custom_skill.workflow_id, custom_workflow_id)

        custom_after = svc.get_workflow(custom_workflow_id)
        self.assertEqual(custom_after.workflow_version, custom_workflow_version)

        reset_versions = (
            self.db.query(AssistantWorkflowVersion)
            .filter(AssistantWorkflowVersion.workflow_id == canonical_workflow_id)
            .all()
        )
        self.assertEqual(len(reset_versions), 1)
        self.assertEqual(reset_versions[0].version_source, "publish")

    def test_update_system_skill_cannot_rename(self) -> None:
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.schemas import AssistantSkillUpdateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        skill = self._new_skill_with_binding(
            name="s",
            description="d",
            is_system=True,
            enabled=True,
            mode="langgraph",
            langgraph_pattern="agent_loop",
            system_prompt="x",
        )
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

        skill = self._new_skill_with_binding(
            name="s",
            description="d",
            is_system=True,
            enabled=True,
            mode="langgraph",
            langgraph_pattern="agent_loop",
            system_prompt="x",
        )
        self.db.add(skill)
        self.db.commit()

        svc = AssistantConfigService(self.db)
        with self.assertRaises(ApiException) as ctx:
            svc.delete_skill(skill.id)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, 40022)

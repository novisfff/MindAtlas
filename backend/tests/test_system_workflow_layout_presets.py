from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


EXPECTED_WORKFLOW_POSITIONS: dict[str, dict[str, tuple[int, int]]] = {
    "quick_stats": {
        "start": (120, 220),
        "tool_stats": (530, 220),
        "llm_output": (940, 220),
        "output_final": (1350, 220),
    },
    "smart_capture": {
        "start": (80, 320),
        "tool_types": (490, 245),
        "tool_tags": (490, 396),
        "llm_title": (900, 306),
        "llm_summary": (1310, 306),
        "llm_content": (1720, 306),
        "llm_type": (2130, 306),
        "llm_tags": (2540, 306),
        "llm_time": (2950, 306),
        "human_confirm": (3360, 306),
        "tool_create": (3770, 245),
        "llm_cancel": (3770, 396),
        "llm_output": (4180, 245),
        "output_final": (4590, 320),
    },
    "periodic_review": {
        "start": (120, 320),
        "llm_dates": (530, 320),
        "tool_entries": (940, 245),
        "tool_activity": (940, 396),
        "llm_output": (1350, 315),
        "output_final": (1760, 315),
    },
}


def _node_position_map(workflow) -> dict[str, tuple[int, int]]:
    return {
        str(node.node_id): (int(round(float(node.position_x))), int(round(float(node.position_y))))
        for node in (workflow.nodes or [])
    }


class SystemWorkflowLayoutPresetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = make_session()

    def tearDown(self) -> None:
        self.db.close()

    @staticmethod
    def _workflow_input_from_definition(definition):
        from app.assistant_config.schemas import WorkflowInput  # noqa: E402

        return WorkflowInput.model_validate(
            {
                "nodes": [
                    {
                        "node_id": node.node_id,
                        "node_type": node.node_type,
                        "label": node.label,
                        "position_x": node.position_x,
                        "position_y": node.position_y,
                        "config": node.config,
                    }
                    for node in (definition.workflow_nodes or [])
                ],
                "edges": [
                    {
                        "edge_id": edge.edge_id,
                        "source_node_id": edge.source_node_id,
                        "target_node_id": edge.target_node_id,
                        "source_handle": edge.source_handle,
                        "target_handle": edge.target_handle,
                        "condition_type": edge.condition_type,
                        "condition_expr": edge.condition_expr.model_dump() if edge.condition_expr else None,
                        "label": edge.label,
                    }
                    for edge in (definition.workflow_edges or [])
                ],
                "viewport": getattr(definition, "workflow_viewport", None),
            }
        )

    def _create_system_skill_with_workflow(self, definition):
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.schemas import AssistantWorkflowCreateRequest  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        workflow = svc.create_workflow(
            AssistantWorkflowCreateRequest(
                name=f"{definition.name}__workflow",
                description=definition.description,
                enabled=True,
                workflow=self._workflow_input_from_definition(definition),
            )
        )
        workflow.is_system = True
        skill = AssistantSkill(
            name=definition.name,
            description=definition.description,
            intent_examples=list(definition.intent_examples or []),
            tools=list(definition.tools or []),
            mode="langgraph",
            langgraph_pattern="workflow_dag",
            system_prompt=None,
            kb_config={"enabled": False},
            is_system=True,
            enabled=True,
            workflow_id=workflow.id,
            agent_profile_id=None,
        )
        self.db.add(skill)
        self.db.commit()
        return svc, skill.id

    def test_system_workflow_definitions_use_optimized_layout_presets(self) -> None:
        from app.assistant.skill_catalog.definitions import PERIODIC_REVIEW, QUICK_STATS, SMART_CAPTURE  # noqa: E402

        definitions = {
            QUICK_STATS.name: QUICK_STATS,
            SMART_CAPTURE.name: SMART_CAPTURE,
            PERIODIC_REVIEW.name: PERIODIC_REVIEW,
        }
        for skill_name, expected_positions in EXPECTED_WORKFLOW_POSITIONS.items():
            definition = definitions[skill_name]
            position_map = {
                node.node_id: (int(round(float(node.position_x))), int(round(float(node.position_y))))
                for node in (definition.workflow_nodes or [])
            }
            for node_id, expected in expected_positions.items():
                self.assertEqual(position_map.get(node_id), expected, f"{skill_name}.{node_id} position mismatch")

    def test_reset_skill_restores_system_workflow_layout_preset(self) -> None:
        from app.assistant.skill_catalog.definitions import QUICK_STATS  # noqa: E402
        from app.assistant_config.models import AssistantSkill  # noqa: E402

        svc, skill_id = self._create_system_skill_with_workflow(QUICK_STATS)
        skill = self.db.query(AssistantSkill).filter(AssistantSkill.id == skill_id).first()
        self.assertIsNotNone(skill)
        self.assertIsNotNone(skill.workflow)

        for node in skill.workflow.nodes or []:
            node.position_x = float(node.position_x) + 77.0
            node.position_y = float(node.position_y) - 33.0
        self.db.commit()

        svc.reset_skill(skill.id, confirm=True)

        refreshed = self.db.query(AssistantSkill).filter(AssistantSkill.id == skill_id).first()
        self.assertIsNotNone(refreshed)
        self.assertIsNotNone(refreshed.workflow)
        self.assertEqual(
            _node_position_map(refreshed.workflow),
            EXPECTED_WORKFLOW_POSITIONS["quick_stats"],
        )

    def test_sync_does_not_override_existing_system_workflow_positions(self) -> None:
        from app.assistant.skill_catalog.definitions import QUICK_STATS  # noqa: E402
        from app.assistant_config.models import AssistantSkill  # noqa: E402

        svc, skill_id = self._create_system_skill_with_workflow(QUICK_STATS)
        skill = self.db.query(AssistantSkill).filter(AssistantSkill.id == skill_id).first()
        self.assertIsNotNone(skill)
        self.assertIsNotNone(skill.workflow)

        start_node = next((node for node in (skill.workflow.nodes or []) if node.node_id == "start"), None)
        self.assertIsNotNone(start_node)
        start_node.position_x = 999.0
        start_node.position_y = 777.0
        self.db.commit()

        with patch(
            "app.assistant_config.service.SkillRegistry.list_system_skills",
            return_value=[QUICK_STATS],
        ):
            svc.sync_system_skills()

        refreshed = self.db.query(AssistantSkill).filter(AssistantSkill.id == skill_id).first()
        self.assertIsNotNone(refreshed)
        self.assertIsNotNone(refreshed.workflow)
        refreshed_start = next((node for node in (refreshed.workflow.nodes or []) if node.node_id == "start"), None)
        self.assertIsNotNone(refreshed_start)
        self.assertEqual((int(round(refreshed_start.position_x)), int(round(refreshed_start.position_y))), (999, 777))

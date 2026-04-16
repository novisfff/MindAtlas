from __future__ import annotations

import unittest
from unittest.mock import patch

from tests._bootstrap import bootstrap_backend_imports, reset_caches
from tests._db import make_session


bootstrap_backend_imports()
reset_caches()


EXPECTED_WORKFLOW_POSITIONS: dict[str, dict[str, tuple[int, int]]] = {
    "quick_stats": {
        "start": (120, 320),
        "llm_intent": (530, 320),
        "tool_stats": (940, 170),
        "tool_activity": (940, 320),
        "tool_tags": (940, 470),
        "llm_output": (1350, 320),
        "output_final": (1760, 320),
    },
    "smart_capture": {
        "start": (80, 320),
        "tool_types": (480, 320),
        "tool_tags": (880, 320),
        "llm_prepare_lookup": (1280, 320),
        "tool_search_similar": (1680, 320),
        "llm_rank_candidates": (2080, 320),
        "if_has_candidates": (2480, 320),
        "human_triage": (2880, 170),
        "output_triage_cancelled": (3280, 30),
        "if_triage_route": (3280, 170),
        "output_merge_target_required": (3680, 30),
        "llm_materialize": (3680, 320),
        "if_write_mode": (4080, 320),
        "tool_get_existing": (4480, 170),
        "llm_merge_rewrite": (4880, 170),
        "code_prepare_write_payload": (5280, 320),
        "human_confirm_write": (5680, 320),
        "output_write_cancelled": (6080, 460),
        "if_persist_route": (6080, 320),
        "tool_update": (6480, 170),
        "tool_create": (6480, 460),
        "call_relation_followup": (6880, 320),
        "llm_finalize_reply": (7280, 320),
        "output_final": (7680, 320),
    },
    "periodic_review": {
        "start": (120, 320),
        "llm_request": (530, 320),
        "call_core": (940, 320),
        "output_final": (1350, 320),
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

    def _get_system_skill_with_workflow(self, definition):
        from app.assistant_config.models import AssistantSkill  # noqa: E402
        from app.assistant_config.service import AssistantConfigService  # noqa: E402

        svc = AssistantConfigService(self.db)
        svc.sync_system_skills()
        skill = (
            self.db.query(AssistantSkill)
            .filter(
                AssistantSkill.name == definition.name,
                AssistantSkill.is_system.is_(True),
            )
            .first()
        )
        self.assertIsNotNone(skill)
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

        svc, skill_id = self._get_system_skill_with_workflow(QUICK_STATS)
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

    def test_sync_restores_existing_system_workflow_positions(self) -> None:
        from app.assistant.skill_catalog.definitions import QUICK_STATS  # noqa: E402
        from app.assistant_config.models import AssistantSkill  # noqa: E402

        svc, skill_id = self._get_system_skill_with_workflow(QUICK_STATS)
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
        self.assertEqual(
            _node_position_map(refreshed.workflow),
            EXPECTED_WORKFLOW_POSITIONS["quick_stats"],
        )

    def test_sync_clears_system_workflow_viewport_when_baseline_has_none(self) -> None:
        from app.assistant.skill_catalog.definitions import QUICK_STATS  # noqa: E402
        from app.assistant_config.models import AssistantSkill  # noqa: E402

        svc, skill_id = self._get_system_skill_with_workflow(QUICK_STATS)
        skill = self.db.query(AssistantSkill).filter(AssistantSkill.id == skill_id).first()
        self.assertIsNotNone(skill)
        self.assertIsNotNone(skill.workflow)

        skill.workflow.workflow_viewport = {"x": 0, "y": 0, "zoom": 1}
        self.db.commit()

        with patch(
            "app.assistant_config.service.SkillRegistry.list_system_skills",
            return_value=[QUICK_STATS],
        ):
            svc.sync_system_skills()

        refreshed = self.db.query(AssistantSkill).filter(AssistantSkill.id == skill_id).first()
        self.assertIsNotNone(refreshed)
        self.assertIsNotNone(refreshed.workflow)
        self.assertIsNone(refreshed.workflow.workflow_viewport)

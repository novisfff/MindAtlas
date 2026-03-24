from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class SystemDefaultsLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.assistant.skill_catalog.defaults_loader import clear_system_defaults_cache  # noqa: E402

        clear_system_defaults_cache()

    def tearDown(self) -> None:
        from app.assistant.skill_catalog.defaults_loader import clear_system_defaults_cache  # noqa: E402

        clear_system_defaults_cache()

    def test_load_system_defaults_success(self) -> None:
        from app.assistant.skill_catalog.defaults_loader import load_system_skill_defaults  # noqa: E402

        defaults = load_system_skill_defaults()
        names = {item.name for item in defaults}
        self.assertIn("quick_stats", names)
        self.assertIn("smart_capture", names)
        self.assertIn("periodic_review", names)
        self.assertIn("general_chat", names)

    def test_get_system_baselines_success(self) -> None:
        from app.assistant.skill_catalog.defaults_loader import (  # noqa: E402
            get_system_agent_baseline,
            get_system_workflow_baseline,
        )

        workflow = get_system_workflow_baseline("quick_stats")
        self.assertIsNotNone(workflow)
        self.assertEqual(len(workflow.nodes), 4)

        agent = get_system_agent_baseline("general_chat")
        self.assertIsNotNone(agent)
        self.assertTrue(bool((agent.system_prompt or "").strip()))
        self.assertTrue(bool((agent.kb_config or {}).get("enabled", False)))

    def test_system_defaults_locales_share_skill_keys_and_localized_text(self) -> None:
        from app.assistant.skill_catalog.defaults_loader import load_system_skill_defaults  # noqa: E402

        zh_defaults = load_system_skill_defaults(locale="zh")
        en_defaults = load_system_skill_defaults(locale="en")

        self.assertEqual({item.name for item in zh_defaults}, {item.name for item in en_defaults})

        zh_quick_stats = next(item for item in zh_defaults if item.name == "quick_stats")
        en_quick_stats = next(item for item in en_defaults if item.name == "quick_stats")
        self.assertNotEqual(zh_quick_stats.description, en_quick_stats.description)
        self.assertTrue(bool((zh_quick_stats.description or "").strip()))
        self.assertTrue(bool((en_quick_stats.description or "").strip()))

    def test_system_behavior_default_workflows_locales_share_graph_structure(self) -> None:
        from app.assistant_config.system_behavior_defaults_loader import get_system_behavior_default_workflow_by_key  # noqa: E402

        for behavior_key in ("weekly_report_generation", "monthly_report_generation"):
            with self.subTest(behavior_key=behavior_key):
                zh_workflow = get_system_behavior_default_workflow_by_key(behavior_key, locale="zh")
                en_workflow = get_system_behavior_default_workflow_by_key(behavior_key, locale="en")

                self.assertIsNotNone(zh_workflow)
                self.assertIsNotNone(en_workflow)
                assert zh_workflow is not None
                assert en_workflow is not None

                self.assertEqual(
                    [(node.node_id, node.node_type) for node in zh_workflow.nodes],
                    [(node.node_id, node.node_type) for node in en_workflow.nodes],
                )
                self.assertEqual(
                    [
                        (edge.edge_id, edge.source_node_id, edge.target_node_id, edge.source_handle, edge.target_handle)
                        for edge in zh_workflow.edges
                    ],
                    [
                        (edge.edge_id, edge.source_node_id, edge.target_node_id, edge.source_handle, edge.target_handle)
                        for edge in en_workflow.edges
                    ],
                )

                zh_labels = {node.node_id: node.label for node in zh_workflow.nodes}
                en_labels = {node.node_id: node.label for node in en_workflow.nodes}
                self.assertNotEqual(zh_labels, en_labels)

    def test_smart_capture_baseline_contains_human_confirm_branches(self) -> None:
        from app.assistant.skill_catalog.defaults_loader import get_system_workflow_baseline  # noqa: E402

        workflow = get_system_workflow_baseline("smart_capture")
        self.assertIsNotNone(workflow)

        node_map = {str(node.node_id): node for node in (workflow.nodes or [])}
        self.assertIn("human_confirm", node_map)
        self.assertEqual(str(node_map["human_confirm"].node_type), "human_in_loop")

        edges = list(workflow.edges or [])
        approved_targets = [
            str(edge.target_node_id)
            for edge in edges
            if str(edge.source_node_id) == "human_confirm" and str(edge.source_handle or "").strip().lower() == "approved"
        ]
        rejected_targets = [
            str(edge.target_node_id)
            for edge in edges
            if str(edge.source_node_id) == "human_confirm" and str(edge.source_handle or "").strip().lower() == "rejected"
        ]
        self.assertEqual(approved_targets, ["tool_create"])
        self.assertEqual(len(rejected_targets), 1)

        output_nodes = [str(node.node_id) for node in (workflow.nodes or []) if str(node.node_type) == "output"]
        self.assertCountEqual(output_nodes, ["output_created", "output_cancelled"])

        llm_output_targets = [
            str(edge.target_node_id)
            for edge in edges
            if str(edge.source_node_id) == "llm_output"
        ]
        llm_cancel_targets = [
            str(edge.target_node_id)
            for edge in edges
            if str(edge.source_node_id) == "llm_cancel"
        ]
        self.assertEqual(llm_output_targets, ["output_created"])
        self.assertEqual(llm_cancel_targets, ["output_cancelled"])

    def test_fail_fast_when_preset_file_missing(self) -> None:
        from app.assistant.skill_catalog.defaults_loader import _load_system_skill_defaults_from_dir  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            manifest = {
                "schemaVersion": 1,
                "skills": [
                    {
                        "name": "x",
                        "description": "x",
                        "intentExamples": [],
                        "targetType": "workflow",
                        "presetFile": "workflows/missing.json",
                    }
                ],
            }
            (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(RuntimeError):
                _load_system_skill_defaults_from_dir(base)


if __name__ == "__main__":
    unittest.main()

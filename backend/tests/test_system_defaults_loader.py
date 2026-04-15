from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()


class SystemDefaultsLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_caches()

    def tearDown(self) -> None:
        reset_caches()

    def test_central_registry_lists_expected_system_assets(self) -> None:
        from app.assistant.workflow.system_assets import list_system_assets  # noqa: E402

        workflow_assets = list_system_assets(kind="workflow", locale="zh")
        agent_assets = list_system_assets(kind="agent", locale="zh")

        self.assertEqual(len(workflow_assets), 7)
        self.assertEqual(
            {item.asset_key for item in workflow_assets},
            {
                "quick_stats",
                "smart_capture",
                "periodic_review",
                "periodic_review_core",
                "context_capture",
                "weekly_report",
                "monthly_report",
            },
        )
        self.assertEqual(len(agent_assets), 1)
        self.assertEqual(agent_assets[0].asset_key, "general_chat")

    def test_central_assets_load_for_zh_and_en(self) -> None:
        from app.assistant.workflow.system_assets import (  # noqa: E402
            load_system_agent_asset,
            load_system_workflow_asset,
            list_system_assets,
        )

        for asset in list_system_assets(locale="zh"):
            with self.subTest(asset_key=asset.asset_key, kind=asset.kind):
                if asset.kind == "workflow":
                    zh_workflow = load_system_workflow_asset(asset.asset_key, locale="zh")
                    en_workflow = load_system_workflow_asset(asset.asset_key, locale="en")
                    self.assertGreater(len(zh_workflow.nodes), 0)
                    self.assertEqual(
                        [(node.node_id, node.node_type) for node in zh_workflow.nodes],
                        [(node.node_id, node.node_type) for node in en_workflow.nodes],
                    )
                    self.assertEqual(
                        [
                            (
                                edge.edge_id,
                                edge.source_node_id,
                                edge.target_node_id,
                                edge.source_handle,
                                edge.target_handle,
                            )
                            for edge in zh_workflow.edges
                        ],
                        [
                            (
                                edge.edge_id,
                                edge.source_node_id,
                                edge.target_node_id,
                                edge.source_handle,
                                edge.target_handle,
                            )
                            for edge in en_workflow.edges
                        ],
                    )
                    continue

                zh_agent = load_system_agent_asset(asset.asset_key, locale="zh")
                en_agent = load_system_agent_asset(asset.asset_key, locale="en")
                self.assertTrue(bool((zh_agent.system_prompt or "").strip()))
                self.assertTrue(bool((en_agent.system_prompt or "").strip()))
                self.assertNotEqual(zh_agent.system_prompt, en_agent.system_prompt)

    def test_invalid_system_asset_requests_fail_fast(self) -> None:
        from app.assistant.workflow.system_assets import load_system_workflow_asset, list_system_assets  # noqa: E402

        with self.assertRaises(RuntimeError):
            load_system_workflow_asset("../escape", locale="zh")

        with self.assertRaises(RuntimeError):
            load_system_workflow_asset("quick_stats", locale="fr")

        with self.assertRaises(RuntimeError):
            list_system_assets(locale="fr")

        with self.assertRaises(RuntimeError):
            load_system_workflow_asset("missing_asset", locale="zh")

    def test_system_skill_defaults_still_resolve_from_central_assets(self) -> None:
        from app.assistant.skill_catalog.defaults_loader import (  # noqa: E402
            get_system_agent_baseline,
            get_system_workflow_baseline,
            load_system_skill_defaults,
        )
        from app.assistant.workflow.system_assets import get_system_skill_asset  # noqa: E402

        defaults = load_system_skill_defaults()
        self.assertEqual(
            {item.name for item in defaults},
            {"quick_stats", "smart_capture", "periodic_review", "general_chat"},
        )

        general_chat_asset = get_system_skill_asset("general_chat", locale="zh")
        self.assertIsNotNone(general_chat_asset)
        assert general_chat_asset is not None
        self.assertEqual(general_chat_asset.kind, "agent")
        self.assertTrue(general_chat_asset.hidden)

        workflow = get_system_workflow_baseline("quick_stats", locale="zh")
        self.assertIsNotNone(workflow)
        assert workflow is not None
        self.assertEqual(len(workflow.nodes), 7)

        agent = get_system_agent_baseline("general_chat", locale="zh")
        self.assertIsNotNone(agent)
        assert agent is not None
        self.assertTrue(bool((agent.kb_config or {}).get("enabled", False)))

        quick_stats = next(item for item in defaults if item.name == "quick_stats")
        self.assertIn("看下我近7天的录入趋势", quick_stats.intent_examples)
        self.assertIn("按标签统计一下我最近常用什么", quick_stats.intent_examples)
        self.assertIn("统计 2026-03-01 到 2026-03-31 的记录概况", quick_stats.intent_examples)

    def test_periodic_review_assets_expose_wrapper_and_core_workflows(self) -> None:
        from app.assistant.workflow.system_assets import load_system_workflow_asset  # noqa: E402

        wrapper = load_system_workflow_asset("periodic_review", locale="zh")
        core = load_system_workflow_asset("periodic_review_core", locale="zh")

        wrapper_nodes = {node.node_id: node for node in wrapper.nodes}
        core_nodes = {node.node_id: node for node in core.nodes}

        self.assertEqual(
            [node.node_id for node in wrapper.nodes],
            ["start", "llm_request", "call_core", "output_final"],
        )
        self.assertEqual(wrapper_nodes["call_core"].node_type, "workflow_call")
        self.assertEqual(wrapper_nodes["call_core"].config["targetSystemAssetKey"], "periodic_review_core")
        self.assertEqual(wrapper_nodes["call_core"].config["inputBindings"]["focus"], "{{llm_request.focus}}")
        self.assertEqual(core_nodes["output_final"].config["outputMode"], "structured")
        self.assertEqual(core_nodes["output_final"].config["outputFields"][0]["name"], "content")

    def test_quick_stats_asset_uses_focus_extraction_and_parallel_stats_tools(self) -> None:
        from app.assistant.workflow.system_assets import load_system_workflow_asset  # noqa: E402

        workflow = load_system_workflow_asset("quick_stats", locale="zh")
        nodes = {node.node_id: node for node in workflow.nodes}

        self.assertEqual(
            [node.node_id for node in workflow.nodes],
            ["start", "llm_intent", "tool_stats", "tool_activity", "tool_tags", "llm_output", "output_final"],
        )
        self.assertEqual(nodes["llm_intent"].config["outputFields"][1]["name"], "start_date")
        self.assertEqual(nodes["llm_intent"].config["outputFields"][2]["name"], "end_date")
        self.assertEqual(nodes["tool_stats"].config["inputBindings"]["start_date"], "{{llm_intent.start_date}}")
        self.assertEqual(nodes["tool_activity"].config["inputBindings"]["end_date"], "{{llm_intent.end_date}}")
        self.assertEqual(nodes["tool_tags"].config["toolName"], "get_tag_statistics")
        self.assertEqual(nodes["tool_tags"].config["inputBindings"]["start_date"], "{{llm_intent.start_date}}")
        self.assertEqual(nodes["tool_tags"].config["inputBindings"]["top_n"], "5")
        self.assertIn("## 我先帮你看了下", nodes["llm_output"].config["systemPrompt"])
        self.assertIn("有陪伴感", nodes["llm_output"].config["systemPrompt"])
        self.assertIn("归一化时间范围起点", nodes["llm_output"].config["userInput"])

    def test_system_behavior_workflows_resolve_from_asset_keys(self) -> None:
        from app.assistant.workflow.system_assets import load_system_workflow_asset  # noqa: E402
        from app.assistant_config.system_behavior_registry import get_system_behavior_definition  # noqa: E402

        for behavior_key, expected_name in (
            ("weekly_report_generation", "system_weekly_report__workflow"),
            ("monthly_report_generation", "system_monthly_report__workflow"),
        ):
            with self.subTest(behavior_key=behavior_key):
                definition = get_system_behavior_definition(behavior_key, locale="zh")
                self.assertIsNotNone(definition)
                assert definition is not None
                self.assertEqual(definition.default_target.canonical_name, expected_name)
                self.assertTrue(bool(definition.default_target.default_target_asset_key))

                zh_workflow = load_system_workflow_asset(
                    definition.default_target.default_target_asset_key or "",
                    locale="zh",
                )
                en_workflow = load_system_workflow_asset(
                    definition.default_target.default_target_asset_key or "",
                    locale="en",
                )
                self.assertEqual(
                    [(node.node_id, node.node_type) for node in zh_workflow.nodes],
                    [(node.node_id, node.node_type) for node in en_workflow.nodes],
                )


if __name__ == "__main__":
    unittest.main()

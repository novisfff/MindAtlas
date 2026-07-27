from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class _FakeHumanLoopRuntime:  # patched proxy not needed; subclass created lazily inside tests
    pass


class WorkflowHumanInLoopNodeTests(unittest.TestCase):
    def _node_outputs(self):
        return {
            "start": {
                "status": "ok",
                "text": "",
                "raw": {"user_input": "补充 OpenClaw 接入收敛说明"},
                "json_fields": {"user_input": "补充 OpenClaw 接入收敛说明"},
            },
            "llm_duplicate_notice": {
                "status": "ok",
                "text": "可能重复的已有记录：OpenClaw 接入记录",
                "raw": "可能重复的已有记录：OpenClaw 接入记录",
                "json_fields": {
                    "response": "可能重复的已有记录：OpenClaw 接入记录",
                    "title": "OpenClaw 接入记录",
                },
            },
            "tool_tags": {
                "status": "ok",
                "text": "",
                "raw": [{"name": "openclaw"}, {"name": "workflow"}],
                "json_fields": {
                    "items": [{"name": "openclaw"}, {"name": "workflow"}],
                    "result": [{"name": "openclaw"}, {"name": "workflow"}],
                },
            },
        }

    def _node_cfg(self):
        return {
            "__node_label": "人工确认",
            "title": "确认：{{llm_duplicate_notice.title}}",
            "instruction": "原始内容：{{start.user_input}}\n提示：{{llm_duplicate_notice.response}}\n日期：{{sys.date}}",
            "approveLabel": "创建 {{llm_duplicate_notice.title}}",
            "rejectLabel": "取消并转到 {{env.target_mode}}",
            "requireRejectComment": False,
            "fields": [
                {
                    "name": "tags",
                    "label": "标签",
                    "type": "array",
                    "widget": "tag_selector",
                    "optionsTemplate": "{{tool_tags.items}}",
                    "optionValueKey": "name",
                    "allowCustom": True,
                    "valueTemplate": "[\"openclaw\"]",
                }
            ],
        }

    def test_capability_scope_rejects_duck_typed_runtime_without_invoking_it(self) -> None:
        from types import SimpleNamespace

        from app.assistant.workflow.engine.node_builders.human_in_loop_node import (
            build_human_in_loop_node,
        )

        invoked = False

        class DuckRuntime:
            def create_and_wait(self, **kwargs):  # noqa: ANN003
                nonlocal invoked
                invoked = True
                return {}

        node_fn = build_human_in_loop_node(
            "human_confirm",
            self._node_cfg(),
            execution_scope=SimpleNamespace(allow_ambient_memory=False),
        )

        with self.assertRaisesRegex(RuntimeError, "unsupported under capability scope"):
            node_fn(
                {
                    "metadata": {"human_loop_runtime": DuckRuntime()},
                    "node_outputs": self._node_outputs(),
                    "sys_vars": {},
                    "env_vars": {},
                }
            )

        self.assertFalse(invoked)

    def test_runtime_resolves_templated_request_text_and_options(self) -> None:
        from app.assistant.workflow.engine.node_builders.human_in_loop_node import build_human_in_loop_node  # noqa: E402
        from app.assistant.workflow.human_approval_runtime import HumanLoopRuntime  # noqa: E402

        captured: dict[str, object] = {}

        class FakeRuntime(HumanLoopRuntime):
            def __init__(self) -> None:
                pass

            def create_and_wait(self, **kwargs):  # noqa: ANN003
                captured.update(kwargs)
                return {
                    "id": "approval-1",
                    "decision": "approved",
                    "submittedValues": {},
                    "comment": "",
                }

        node_fn = build_human_in_loop_node("human_confirm", self._node_cfg())
        result = node_fn(
            {
                "metadata": {"human_loop_runtime": FakeRuntime()},
                "node_outputs": self._node_outputs(),
                "sys_vars": {"date": "2026-04-15"},
                "env_vars": {"target_mode": "上下文入库"},
            }
        )

        request_payload = captured["request_payload"]
        assert isinstance(request_payload, dict)
        self.assertEqual(request_payload["title"], "确认：OpenClaw 接入记录")
        self.assertIn("补充 OpenClaw 接入收敛说明", request_payload["instruction"])
        self.assertIn("可能重复的已有记录：OpenClaw 接入记录", request_payload["instruction"])
        self.assertEqual(request_payload["approveLabel"], "创建 OpenClaw 接入记录")
        self.assertEqual(request_payload["rejectLabel"], "取消并转到 上下文入库")

        field_schema = captured["field_schema"]
        assert isinstance(field_schema, list)
        self.assertEqual(field_schema[0]["options"], ["openclaw", "workflow"])
        self.assertTrue(field_schema[0]["allowCustom"])
        self.assertEqual(captured["initial_values"], {"tags": ["openclaw"]})
        self.assertEqual(result["branch_decisions"]["human_confirm"], "approved")

    def test_snapshot_preview_resolves_templated_request_text_and_options(self) -> None:
        from app.assistant.workflow.engine.snapshot_input_resolvers import (  # noqa: E402
            SnapshotInputContext,
            build_node_snapshot_input,
        )

        node_outputs = self._node_outputs()
        preview = build_node_snapshot_input(
            "human_in_loop",
            self._node_cfg(),
            SnapshotInputContext(
                state={"user_input": "", "structured_input": {}},
                node_outputs=node_outputs,
                start_inputs={"user_input": "补充 OpenClaw 接入收敛说明"},
                sys_vars={"date": "2026-04-15"},
                env_vars={"target_mode": "上下文入库"},
                env_specs={},
                text_preview_limit=8000,
            ),
        )

        self.assertEqual(preview["title"], "确认：OpenClaw 接入记录")
        self.assertIn("提示：可能重复的已有记录：OpenClaw 接入记录", preview["instruction"])
        self.assertEqual(preview["approveLabel"], "创建 OpenClaw 接入记录")
        self.assertEqual(preview["rejectLabel"], "取消并转到 上下文入库")
        self.assertEqual(preview["fields"][0]["options"], ["openclaw", "workflow"])
        self.assertTrue(preview["fields"][0]["allowCustom"])
        self.assertEqual(preview["initialValues"], {"tags": ["openclaw"]})

    def test_runtime_preserves_object_options_for_checkbox_group(self) -> None:
        from app.assistant.workflow.engine.node_builders.human_in_loop_node import build_human_in_loop_node  # noqa: E402
        from app.assistant.workflow.human_approval_runtime import HumanLoopRuntime  # noqa: E402

        captured: dict[str, object] = {}

        class FakeRuntime(HumanLoopRuntime):
            def __init__(self) -> None:
                pass

            def create_and_wait(self, **kwargs):  # noqa: ANN003
                captured.update(kwargs)
                return {
                    "id": "approval-2",
                    "decision": "approved",
                    "submittedValues": {"relations": ["entry-1"]},
                    "comment": "",
                }

        node_cfg = {
            "__node_label": "关系确认",
            "title": "确认推荐关系",
            "instruction": "请勾选要创建的关系",
            "approveLabel": "创建关系",
            "rejectLabel": "跳过",
            "requireRejectComment": False,
            "fields": [
                {
                    "name": "relations",
                    "label": "推荐关系",
                    "type": "array",
                    "widget": "checkbox_group",
                    "optionsTemplate": "{{llm_duplicate_notice.response}}",
                    "required": False,
                    "valueTemplate": "[\"entry-1\"]",
                }
            ],
        }
        node_outputs = {
            "start": self._node_outputs()["start"],
            "llm_duplicate_notice": {
                "status": "ok",
                "text": "",
                "raw": [
                    {
                        "value": "entry-1",
                        "label": "OpenClaw 接入记录",
                        "description": "RELATED_TO · 0.92",
                    },
                    {
                        "value": "entry-2",
                        "label": "系统工作流说明",
                    },
                ],
                "json_fields": {
                    "response": [
                        {
                            "value": "entry-1",
                            "label": "OpenClaw 接入记录",
                            "description": "RELATED_TO · 0.92",
                        },
                        {
                            "value": "entry-2",
                            "label": "系统工作流说明",
                        },
                    ]
                },
            },
        }

        node_fn = build_human_in_loop_node("human_confirm_relations", node_cfg)
        result = node_fn(
            {
                "metadata": {"human_loop_runtime": FakeRuntime()},
                "node_outputs": node_outputs,
                "sys_vars": {"date": "2026-04-15"},
                "env_vars": {},
            }
        )

        field_schema = captured["field_schema"]
        assert isinstance(field_schema, list)
        self.assertEqual(
            field_schema[0]["options"],
            [
                {
                    "value": "entry-1",
                    "label": "OpenClaw 接入记录",
                    "description": "RELATED_TO · 0.92",
                },
                {
                    "value": "entry-2",
                    "label": "系统工作流说明",
                },
            ],
        )
        self.assertEqual(captured["initial_values"], {"relations": ["entry-1"]})
        self.assertEqual(result["node_outputs"]["human_confirm_relations"]["json_fields"]["relations"], ["entry-1"])

    def test_snapshot_preview_preserves_object_options_for_checkbox_group(self) -> None:
        from app.assistant.workflow.engine.snapshot_input_resolvers import (  # noqa: E402
            SnapshotInputContext,
            build_node_snapshot_input,
        )

        preview = build_node_snapshot_input(
            "human_in_loop",
            {
                "title": "确认推荐关系",
                "instruction": "请勾选要创建的关系",
                "approveLabel": "创建关系",
                "rejectLabel": "跳过",
                "fields": [
                    {
                        "name": "relations",
                        "label": "推荐关系",
                        "type": "array",
                        "widget": "checkbox_group",
                        "optionsTemplate": "{{llm_duplicate_notice.response}}",
                        "required": False,
                        "valueTemplate": "[\"entry-2\"]",
                    }
                ],
            },
            SnapshotInputContext(
                state={"user_input": "", "structured_input": {}},
                node_outputs={
                    "llm_duplicate_notice": {
                        "status": "ok",
                        "text": "",
                        "raw": [],
                        "json_fields": {
                            "response": [
                                {
                                    "value": "entry-1",
                                    "label": "OpenClaw 接入记录",
                                    "description": "RELATED_TO · 0.92",
                                },
                                {
                                    "value": "entry-2",
                                    "label": "系统工作流说明",
                                },
                            ]
                        },
                    }
                },
                start_inputs={"user_input": ""},
                sys_vars={},
                env_vars={},
                env_specs={},
                text_preview_limit=8000,
            ),
        )

        self.assertEqual(
            preview["fields"][0]["options"],
            [
                {
                    "value": "entry-1",
                    "label": "OpenClaw 接入记录",
                    "description": "RELATED_TO · 0.92",
                },
                {
                    "value": "entry-2",
                    "label": "系统工作流说明",
                },
            ],
        )
        self.assertEqual(preview["initialValues"], {"relations": ["entry-2"]})

    def test_runtime_normalizes_iso_datetime_initial_value_for_date_widget(self) -> None:
        from app.assistant.workflow.engine.node_builders.human_in_loop_node import build_human_in_loop_node  # noqa: E402
        from app.assistant.workflow.human_approval_runtime import HumanLoopRuntime  # noqa: E402

        captured: dict[str, object] = {}

        class FakeRuntime(HumanLoopRuntime):
            def __init__(self) -> None:
                pass

            def create_and_wait(self, **kwargs):  # noqa: ANN003
                captured.update(kwargs)
                return {
                    "id": "approval-date-1",
                    "decision": "approved",
                    "submittedValues": {},
                    "comment": "",
                }

        node_fn = build_human_in_loop_node(
            "human_confirm_date",
            {
                "__node_label": "日期确认",
                "title": "确认日期",
                "instruction": "请确认日期",
                "approveLabel": "继续",
                "rejectLabel": "取消",
                "requireRejectComment": False,
                "fields": [
                    {
                        "name": "time_at",
                        "label": "时间点",
                        "type": "string",
                        "widget": "date",
                        "required": False,
                        "valueTemplate": "{{llm_duplicate_notice.time_at}}",
                    }
                ],
            },
        )
        node_outputs = self._node_outputs()
        node_outputs["llm_duplicate_notice"]["json_fields"]["time_at"] = "2026-04-16T00:00:00+00:00"

        node_fn(
            {
                "metadata": {"human_loop_runtime": FakeRuntime()},
                "node_outputs": node_outputs,
                "sys_vars": {"date": "2026-04-15"},
                "env_vars": {},
            }
        )

        self.assertEqual(captured["initial_values"], {"time_at": "2026-04-16"})

    def test_snapshot_preview_normalizes_iso_datetime_initial_value_for_date_widget(self) -> None:
        from app.assistant.workflow.engine.snapshot_input_resolvers import (  # noqa: E402
            SnapshotInputContext,
            build_node_snapshot_input,
        )

        preview = build_node_snapshot_input(
            "human_in_loop",
            {
                "title": "确认日期",
                "instruction": "请确认日期",
                "approveLabel": "继续",
                "rejectLabel": "取消",
                "fields": [
                    {
                        "name": "time_at",
                        "label": "时间点",
                        "type": "string",
                        "widget": "date",
                        "required": False,
                        "valueTemplate": "{{llm_duplicate_notice.time_at}}",
                    }
                ],
            },
            SnapshotInputContext(
                state={"user_input": "", "structured_input": {}},
                node_outputs={
                    "llm_duplicate_notice": {
                        "status": "ok",
                        "text": "",
                        "raw": {"time_at": "2026-04-16T00:00:00+00:00"},
                        "json_fields": {"time_at": "2026-04-16T00:00:00+00:00"},
                    }
                },
                start_inputs={"user_input": ""},
                sys_vars={},
                env_vars={},
                env_specs={},
                text_preview_limit=8000,
            ),
        )

        self.assertEqual(preview["initialValues"], {"time_at": "2026-04-16"})


if __name__ == "__main__":
    unittest.main()

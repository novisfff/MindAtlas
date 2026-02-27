from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class WorkflowHumanInLoopValidatorTests(unittest.TestCase):
    @staticmethod
    def _human_node() -> dict:
        return {
            "node_id": "hitl_1",
            "node_type": "human_in_loop",
            "label": "Confirm",
            "config": {
                "instruction": "Please confirm before create",
                "fields": [
                    {
                        "name": "title",
                        "type": "string",
                        "required": True,
                        "valueTemplate": "{{start.user_input}}",
                    }
                ],
                "requireRejectComment": True,
            },
        }

    def test_human_in_loop_with_approved_rejected_edges_is_valid(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            self._human_node(),
            {"node_id": "llm_ok", "node_type": "llm", "label": "Approved", "config": {}},
            {"node_id": "llm_ng", "node_type": "llm", "label": "Rejected", "config": {}},
            {
                "node_id": "output_1",
                "node_type": "output",
                "label": "Output",
                "config": {"outputMode": "text", "textTemplate": "{{llm_ok.response}}"},
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "hitl_1", "source_handle": "output"},
            {"source_node_id": "hitl_1", "target_node_id": "llm_ok", "source_handle": "approved"},
            {"source_node_id": "hitl_1", "target_node_id": "llm_ng", "source_handle": "rejected"},
            {"source_node_id": "llm_ok", "target_node_id": "output_1", "source_handle": "output"},
            {"source_node_id": "llm_ng", "target_node_id": "output_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertTrue(result.valid, [e.message for e in result.errors])

    def test_human_in_loop_missing_rejected_edge_is_invalid(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            self._human_node(),
            {"node_id": "llm_ok", "node_type": "llm", "label": "Approved", "config": {}},
            {
                "node_id": "output_1",
                "node_type": "output",
                "label": "Output",
                "config": {"outputMode": "text", "textTemplate": "{{llm_ok.response}}"},
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "hitl_1", "source_handle": "output"},
            {"source_node_id": "hitl_1", "target_node_id": "llm_ok", "source_handle": "approved"},
            {"source_node_id": "llm_ok", "target_node_id": "output_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("handle 'rejected' must map to exactly one outgoing edge" in e.message for e in result.errors))

    def test_iteration_body_human_in_loop_requires_dual_branches(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "iter_1",
                "node_type": "iteration",
                "label": "Iteration",
                "config": {
                    "inputSource": "{{start.user_input}}",
                    "outputVariable": "results",
                    "outputSelector": "{{container.item}}",
                    "bodyNodes": [
                        {"nodeId": "start", "nodeType": "start", "label": "Start", "config": {}},
                        {
                            "nodeId": "hitl_body",
                            "nodeType": "human_in_loop",
                            "label": "Confirm",
                            "config": {
                                "instruction": "confirm item",
                                "fields": [{"name": "value", "type": "string", "required": True}],
                            },
                        },
                        {"nodeId": "llm_ok", "nodeType": "llm", "label": "Approved", "config": {}},
                    ],
                    "bodyEdges": [
                        {"edgeId": "be1", "sourceNodeId": "start", "targetNodeId": "hitl_body", "sourceHandle": "output", "targetHandle": "input"},
                        {"edgeId": "be2", "sourceNodeId": "hitl_body", "targetNodeId": "llm_ok", "sourceHandle": "approved", "targetHandle": "input"},
                    ],
                },
            },
            {
                "node_id": "output_1",
                "node_type": "output",
                "label": "Output",
                "config": {"outputMode": "text", "textTemplate": "{{iter_1.results}}"},
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "iter_1", "source_handle": "output"},
            {"source_node_id": "iter_1", "target_node_id": "output_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("handle 'rejected' must map to exactly one outgoing edge" in e.message for e in result.errors))

    def test_human_in_loop_select_requires_options(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        node = self._human_node()
        node["config"]["fields"] = [
            {
                "name": "priority",
                "type": "string",
                "widget": "select",
                "required": True,
                "valueTemplate": "{{start.user_input}}",
            }
        ]
        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            node,
            {"node_id": "llm_ok", "node_type": "llm", "label": "Approved", "config": {}},
            {"node_id": "llm_ng", "node_type": "llm", "label": "Rejected", "config": {}},
            {"node_id": "output_1", "node_type": "output", "label": "Output", "config": {"outputMode": "text", "textTemplate": "{{llm_ok.response}}"}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "hitl_1", "source_handle": "output"},
            {"source_node_id": "hitl_1", "target_node_id": "llm_ok", "source_handle": "approved"},
            {"source_node_id": "hitl_1", "target_node_id": "llm_ng", "source_handle": "rejected"},
            {"source_node_id": "llm_ok", "target_node_id": "output_1", "source_handle": "output"},
            {"source_node_id": "llm_ng", "target_node_id": "output_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("options or optionsTemplate are required for select" in e.message for e in result.errors))

    def test_human_in_loop_tag_selector_requires_array_type(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        node = self._human_node()
        node["config"]["fields"] = [
            {
                "name": "tags",
                "type": "string",
                "widget": "tag_selector",
                "required": False,
            }
        ]
        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            node,
            {"node_id": "llm_ok", "node_type": "llm", "label": "Approved", "config": {}},
            {"node_id": "llm_ng", "node_type": "llm", "label": "Rejected", "config": {}},
            {"node_id": "output_1", "node_type": "output", "label": "Output", "config": {"outputMode": "text", "textTemplate": "{{llm_ok.response}}"}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "hitl_1", "source_handle": "output"},
            {"source_node_id": "hitl_1", "target_node_id": "llm_ok", "source_handle": "approved"},
            {"source_node_id": "hitl_1", "target_node_id": "llm_ng", "source_handle": "rejected"},
            {"source_node_id": "llm_ok", "target_node_id": "output_1", "source_handle": "output"},
            {"source_node_id": "llm_ng", "target_node_id": "output_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("widget 'tag_selector' is incompatible with type 'string'" in e.message for e in result.errors))

    def test_human_in_loop_non_option_widgets_ignore_empty_option_keys(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        node = self._human_node()
        node["config"]["fields"] = [
            {
                "name": "title",
                "type": "string",
                "widget": "input",
                "optionValueKey": "",
                "allowCustom": False,
                "required": True,
                "valueTemplate": "{{start.user_input}}",
            }
        ]
        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            node,
            {"node_id": "llm_ok", "node_type": "llm", "label": "Approved", "config": {}},
            {"node_id": "llm_ng", "node_type": "llm", "label": "Rejected", "config": {}},
            {"node_id": "output_1", "node_type": "output", "label": "Output", "config": {"outputMode": "text", "textTemplate": "{{llm_ok.response}}"}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "hitl_1", "source_handle": "output"},
            {"source_node_id": "hitl_1", "target_node_id": "llm_ok", "source_handle": "approved"},
            {"source_node_id": "hitl_1", "target_node_id": "llm_ng", "source_handle": "rejected"},
            {"source_node_id": "llm_ok", "target_node_id": "output_1", "source_handle": "output"},
            {"source_node_id": "llm_ng", "target_node_id": "output_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertTrue(result.valid, [e.message for e in result.errors])

    def test_human_in_loop_non_option_widgets_reject_non_empty_option_key(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        node = self._human_node()
        node["config"]["fields"] = [
            {
                "name": "title",
                "type": "string",
                "widget": "input",
                "optionValueKey": "code",
                "required": True,
                "valueTemplate": "{{start.user_input}}",
            }
        ]
        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            node,
            {"node_id": "llm_ok", "node_type": "llm", "label": "Approved", "config": {}},
            {"node_id": "llm_ng", "node_type": "llm", "label": "Rejected", "config": {}},
            {"node_id": "output_1", "node_type": "output", "label": "Output", "config": {"outputMode": "text", "textTemplate": "{{llm_ok.response}}"}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "hitl_1", "source_handle": "output"},
            {"source_node_id": "hitl_1", "target_node_id": "llm_ok", "source_handle": "approved"},
            {"source_node_id": "hitl_1", "target_node_id": "llm_ng", "source_handle": "rejected"},
            {"source_node_id": "llm_ok", "target_node_id": "output_1", "source_handle": "output"},
            {"source_node_id": "llm_ng", "target_node_id": "output_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("optionValueKey is only supported for select/radio/tag_selector" in e.message for e in result.errors))


if __name__ == "__main__":
    unittest.main()

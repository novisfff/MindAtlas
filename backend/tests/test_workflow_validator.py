from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class WorkflowValidatorTests(unittest.TestCase):
    def test_answer_node_is_rejected(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "answer_1", "node_type": "answer", "label": "Answer", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "answer_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("no longer supported" in e.message for e in result.errors))

    def test_missing_llm_output_is_rejected(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "llm_1", "node_type": "llm", "label": "LLM", "config": {"isOutput": False}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("isOutput=true" in e.message for e in result.errors))

    def test_valid_workflow_passes(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "llm_output",
                "node_type": "llm",
                "label": "Final Reply",
                "config": {"systemPrompt": "reply", "isOutput": True, "outputMode": "text"},
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_output", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertTrue(result.valid, [e.message for e in result.errors])

    def test_tool_node_requires_tool_name(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "tool_1", "node_type": "tool", "label": "Create Record", "config": {}},
            {"node_id": "llm_output", "node_type": "llm", "label": "Summary", "config": {"isOutput": True}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "tool_1", "source_handle": "output"},
            {"source_node_id": "tool_1", "target_node_id": "llm_output", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("requires toolName" in e.message for e in result.errors))

    def test_tool_input_bindings_must_be_object(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "tool_1",
                "node_type": "tool",
                "label": "Create Record",
                "config": {"toolName": "create_entry", "inputBindings": "invalid"},
            },
            {"node_id": "llm_output", "node_type": "llm", "label": "Summary", "config": {"isOutput": True}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "tool_1", "source_handle": "output"},
            {"source_node_id": "tool_1", "target_node_id": "llm_output", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("inputBindings must be an object" in e.message for e in result.errors))

    def test_tool_input_bindings_required(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "tool_1",
                "node_type": "tool",
                "label": "Create Record",
                "config": {"toolName": "create_entry"},
            },
            {"node_id": "llm_output", "node_type": "llm", "label": "Summary", "config": {"isOutput": True}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "tool_1", "source_handle": "output"},
            {"source_node_id": "tool_1", "target_node_id": "llm_output", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("requires inputBindings" in e.message for e in result.errors))

    def test_if_else_with_branches_and_else_edge_is_valid(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "if_1",
                "node_type": "if_else",
                "label": "Condition",
                "config": {
                    "branches": [
                        {
                            "id": "if_main",
                            "label": "IF",
                            "logic": "and",
                            "conditions": [
                                {
                                    "id": "cond_1",
                                    "variable": "start.user_input",
                                    "operator": "contains",
                                    "value": "hello",
                                }
                            ],
                        }
                    ],
                    "elseHandle": "else",
                },
            },
            {"node_id": "llm_true", "node_type": "llm", "label": "True Reply", "config": {"isOutput": True}},
            {"node_id": "llm_else", "node_type": "llm", "label": "Else Reply", "config": {"isOutput": False}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "if_1", "source_handle": "output"},
            {"source_node_id": "if_1", "target_node_id": "llm_true", "source_handle": "if_main"},
            {"source_node_id": "if_1", "target_node_id": "llm_else", "source_handle": "else"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertTrue(result.valid, [e.message for e in result.errors])

    def test_if_else_missing_else_edge_is_rejected(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "if_1",
                "node_type": "if_else",
                "label": "Condition",
                "config": {
                    "branches": [
                        {
                            "id": "if_main",
                            "label": "IF",
                            "logic": "and",
                            "conditions": [
                                {
                                    "id": "cond_1",
                                    "variable": "start.user_input",
                                    "operator": "contains",
                                    "value": "hello",
                                }
                            ],
                        }
                    ],
                    "elseHandle": "else",
                },
            },
            {"node_id": "llm_true", "node_type": "llm", "label": "True Reply", "config": {"isOutput": True}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "if_1", "source_handle": "output"},
            {"source_node_id": "if_1", "target_node_id": "llm_true", "source_handle": "if_main"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("exactly one 'else' outgoing edge" in e.message for e in result.errors))

    def test_if_else_operator_value_requirement_and_sys_var_validation(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "if_1",
                "node_type": "if_else",
                "label": "Condition",
                "config": {
                    "branches": [
                        {
                            "id": "if_main",
                            "label": "IF",
                            "logic": "and",
                            "conditions": [
                                {
                                    "id": "cond_1",
                                    "variable": "sys.unknown_field",
                                    "operator": "contains",
                                    "value": "",
                                }
                            ],
                        }
                    ],
                    "elseHandle": "else",
                },
            },
            {"node_id": "llm_true", "node_type": "llm", "label": "True Reply", "config": {"isOutput": True}},
            {"node_id": "llm_else", "node_type": "llm", "label": "Else Reply", "config": {"isOutput": False}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "if_1", "source_handle": "output"},
            {"source_node_id": "if_1", "target_node_id": "llm_true", "source_handle": "if_main"},
            {"source_node_id": "if_1", "target_node_id": "llm_else", "source_handle": "else"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        messages = [e.message for e in result.errors]
        self.assertTrue(any("Unsupported sys variable in condition" in m for m in messages))
        self.assertTrue(any("requires value" in m for m in messages))

    def test_duplicate_label_case_insensitive_is_rejected(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "llm_1", "node_type": "llm", "label": "Summary", "config": {"isOutput": True}},
            {"node_id": "llm_2", "node_type": "llm", "label": "summary", "config": {"isOutput": False}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
            {"source_node_id": "start", "target_node_id": "llm_2", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("Duplicate node label" in e.message for e in result.errors))

    def test_label_with_dot_is_rejected(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "llm_1", "node_type": "llm", "label": "Reply.v1", "config": {"isOutput": True}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("must not contain '.'" in e.message for e in result.errors))

    def test_empty_label_is_rejected(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "llm_1", "node_type": "llm", "label": " ", "config": {"isOutput": True}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("label is required" in e.message for e in result.errors))


if __name__ == "__main__":
    unittest.main()

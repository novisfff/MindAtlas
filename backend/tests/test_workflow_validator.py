from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class WorkflowValidatorTests(unittest.TestCase):
    @staticmethod
    def _append_output_node(
        nodes: list[dict],
        edges: list[dict],
        *source_node_ids: str,
    ) -> tuple[list[dict], list[dict]]:
        output_node_id = "output_final"
        next_nodes = [
            *nodes,
            {
                "node_id": output_node_id,
                "node_type": "output",
                "label": "Output",
                "config": {
                    "outputMode": "text",
                    "textTemplate": f"{{{{{source_node_ids[0]}.response}}}}" if source_node_ids else "{{start.user_input}}",
                },
            },
        ]
        next_edges = [
            *edges,
            *[
                {
                    "source_node_id": source_node_id,
                    "target_node_id": output_node_id,
                    "source_handle": "output",
                }
                for source_node_id in source_node_ids
            ],
        ]
        return next_nodes, next_edges

    def test_answer_node_is_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

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

    def test_removed_node_types_are_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "tpl_1", "node_type": "template", "label": "Tpl", "config": {"template": "x"}},
            {"node_id": "llm_1", "node_type": "llm", "label": "LLM", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "tpl_1", "source_handle": "output"},
            {"source_node_id": "tpl_1", "target_node_id": "llm_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("template" in e.message and "removed" in e.message for e in result.errors))

    def test_missing_output_node_is_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "llm_1", "node_type": "llm", "label": "LLM", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("at least one output node" in e.message for e in result.errors))

    def test_multiple_output_nodes_are_allowed_when_terminal(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "llm_a", "node_type": "llm", "label": "Branch A", "config": {}},
            {"node_id": "llm_b", "node_type": "llm", "label": "Branch B", "config": {}},
            {
                "node_id": "output_a",
                "node_type": "output",
                "label": "Output A",
                "config": {"outputMode": "text", "textTemplate": "{{llm_a.response}}"},
            },
            {
                "node_id": "output_b",
                "node_type": "output",
                "label": "Output B",
                "config": {"outputMode": "text", "textTemplate": "{{llm_b.response}}"},
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_a", "source_handle": "output"},
            {"source_node_id": "start", "target_node_id": "llm_b", "source_handle": "output"},
            {"source_node_id": "llm_a", "target_node_id": "output_a", "source_handle": "output"},
            {"source_node_id": "llm_b", "target_node_id": "output_b", "source_handle": "output"},
        ]

        result = validate_workflow(nodes, edges)
        self.assertTrue(result.valid, [e.message for e in result.errors])

    def test_valid_workflow_passes(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "llm_output",
                "node_type": "llm",
                "label": "Final Reply",
                "config": {"systemPrompt": "reply", "outputMode": "text"},
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_output", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_output")

        result = validate_workflow(nodes, edges)
        self.assertTrue(result.valid, [e.message for e in result.errors])

    def test_tool_node_requires_tool_name(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "tool_1", "node_type": "tool", "label": "Create Record", "config": {}},
            {"node_id": "llm_output", "node_type": "llm", "label": "Summary", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "tool_1", "source_handle": "output"},
            {"source_node_id": "tool_1", "target_node_id": "llm_output", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_output")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("requires toolName" in e.message for e in result.errors))

    def test_tool_input_bindings_must_be_object(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "tool_1",
                "node_type": "tool",
                "label": "Create Record",
                "config": {"toolName": "create_entry", "inputBindings": "invalid"},
            },
            {"node_id": "llm_output", "node_type": "llm", "label": "Summary", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "tool_1", "source_handle": "output"},
            {"source_node_id": "tool_1", "target_node_id": "llm_output", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_output")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("inputBindings must be an object" in e.message for e in result.errors))

    def test_tool_input_bindings_required(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "tool_1",
                "node_type": "tool",
                "label": "Create Record",
                "config": {"toolName": "create_entry"},
            },
            {"node_id": "llm_output", "node_type": "llm", "label": "Summary", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "tool_1", "source_handle": "output"},
            {"source_node_id": "tool_1", "target_node_id": "llm_output", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_output")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("requires inputBindings" in e.message for e in result.errors))

    def test_workflow_call_requires_target_workflow_id(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "call_1",
                "node_type": "workflow_call",
                "label": "Call Child",
                "config": {
                    "bindingMode": "pinned",
                    "targetPublishedVersionId": "00000000-0000-0000-0000-000000000001",
                    "inputBindings": {},
                },
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "call_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "call_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("targetWorkflowId is required" in e.message for e in result.errors))

    def test_iteration_body_allows_workflow_call_node(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "iteration_1",
                "node_type": "iteration",
                "label": "Loop",
                "config": {
                    "inputSource": "{{start.user_input}}",
                    "outputVariable": "results",
                    "outputSelector": "{{container.item}}",
                    "bodyNodes": [
                        {"nodeId": "start", "nodeType": "start", "label": "Start", "config": {}},
                        {
                            "nodeId": "call_1",
                            "nodeType": "workflow_call",
                            "label": "Call Child",
                            "config": {
                                "targetWorkflowId": "00000000-0000-0000-0000-000000000001",
                                "bindingMode": "pinned",
                                "targetPublishedVersionId": "00000000-0000-0000-0000-000000000002",
                                "inputBindings": {"name": "{{container.item}}"},
                            },
                        },
                    ],
                    "bodyEdges": [
                        {"edgeId": "be1", "sourceNodeId": "start", "targetNodeId": "call_1"},
                    ],
                },
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "iteration_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "iteration_1")

        result = validate_workflow(nodes, edges)
        self.assertTrue(result.valid, [e.message for e in result.errors])

    def test_if_else_with_branches_and_else_edge_is_valid(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

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
            {"node_id": "llm_true", "node_type": "llm", "label": "True Reply", "config": {}},
            {"node_id": "llm_else", "node_type": "llm", "label": "Else Reply", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "if_1", "source_handle": "output"},
            {"source_node_id": "if_1", "target_node_id": "llm_true", "source_handle": "if_main"},
            {"source_node_id": "if_1", "target_node_id": "llm_else", "source_handle": "else"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_true", "llm_else")

        result = validate_workflow(nodes, edges)
        self.assertTrue(result.valid, [e.message for e in result.errors])

    def test_if_else_missing_else_edge_is_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

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
            {"node_id": "llm_true", "node_type": "llm", "label": "True Reply", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "if_1", "source_handle": "output"},
            {"source_node_id": "if_1", "target_node_id": "llm_true", "source_handle": "if_main"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_true")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("exactly one 'else' outgoing edge" in e.message for e in result.errors))

    def test_if_else_operator_value_requirement_and_sys_var_validation(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

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
            {"node_id": "llm_true", "node_type": "llm", "label": "True Reply", "config": {}},
            {"node_id": "llm_else", "node_type": "llm", "label": "Else Reply", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "if_1", "source_handle": "output"},
            {"source_node_id": "if_1", "target_node_id": "llm_true", "source_handle": "if_main"},
            {"source_node_id": "if_1", "target_node_id": "llm_else", "source_handle": "else"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_true", "llm_else")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        messages = [e.message for e in result.errors]
        self.assertTrue(any("Unsupported sys variable in condition" in m for m in messages))
        self.assertTrue(any("requires value" in m for m in messages))

    def test_duplicate_label_case_insensitive_is_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "llm_1", "node_type": "llm", "label": "Summary", "config": {}},
            {"node_id": "llm_2", "node_type": "llm", "label": "summary", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
            {"source_node_id": "start", "target_node_id": "llm_2", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("Duplicate node label" in e.message for e in result.errors))

    def test_label_with_dot_is_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "llm_1", "node_type": "llm", "label": "Reply.v1", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("must not contain '.'" in e.message for e in result.errors))

    def test_empty_label_is_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {"node_id": "llm_1", "node_type": "llm", "label": " ", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("label is required" in e.message for e in result.errors))

    def test_llm_knowledge_source_must_be_kr_node(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "tool_1",
                "node_type": "tool",
                "label": "Tool",
                "config": {"toolName": "dummy_tool", "inputBindings": {}},
            },
            {
                "node_id": "llm_1",
                "node_type": "llm",
                "label": "LLM",
                "config": {
                    "knowledgeEnabled": True,
                    "knowledgeSourceNodeIds": ["tool_1"],
                },
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "tool_1", "source_handle": "output"},
            {"source_node_id": "tool_1", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("knowledge_retrieval" in e.message for e in result.errors))

    def test_llm_knowledge_source_must_be_upstream(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "llm_1",
                "node_type": "llm",
                "label": "LLM",
                "config": {
                    "knowledgeEnabled": True,
                    "knowledgeSourceNodeIds": ["kr_later"],
                },
            },
            {
                "node_id": "kr_later",
                "node_type": "knowledge_retrieval",
                "label": "KR",
                "config": {"query": "{{start.user_input}}"},
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
            {"source_node_id": "llm_1", "target_node_id": "kr_later", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("upstream" in e.message for e in result.errors))

    def test_node_model_source_custom_requires_model_id(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "extract_1",
                "node_type": "parameter_extractor",
                "label": "Extract",
                "config": {"modelSource": "custom", "instruction": "extract"},
            },
            {"node_id": "llm_1", "node_type": "llm", "label": "LLM", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "extract_1", "source_handle": "output"},
            {"source_node_id": "extract_1", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("requires modelId" in e.message for e in result.errors))

    def test_node_model_id_uuid_and_default_conflict_validation(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "llm_1",
                "node_type": "llm",
                "label": "LLM",
                "config": {
                    "modelSource": "default",
                    "modelId": "not-a-uuid",
                },
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        messages = [e.message for e in result.errors]
        self.assertTrue(any("must not provide modelId" in m for m in messages))
        self.assertTrue(any("must be UUID" in m for m in messages))

    def test_parameter_extractor_requires_non_empty_output_fields(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "extract_1",
                "node_type": "parameter_extractor",
                "label": "Extract",
                "config": {
                    "inputContent": "{{start.user_input}}",
                    "outputFields": [],
                },
            },
            {"node_id": "llm_1", "node_type": "llm", "label": "LLM", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "extract_1", "source_handle": "output"},
            {"source_node_id": "extract_1", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("outputFields must be a non-empty list" in e.message for e in result.errors))

    def test_parameter_extractor_input_content_must_be_string(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "extract_1",
                "node_type": "parameter_extractor",
                "label": "Extract",
                "config": {
                    "inputContent": 123,
                    "outputFields": [{"name": "city", "type": "string"}],
                },
            },
            {"node_id": "llm_1", "node_type": "llm", "label": "LLM", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "extract_1", "source_handle": "output"},
            {"source_node_id": "extract_1", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("inputContent must be a string" in e.message for e in result.errors))

    def test_parameter_extractor_output_field_schema_validation(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "extract_1",
                "node_type": "parameter_extractor",
                "label": "Extract",
                "config": {
                    "inputContent": "{{start.user_input}}",
                    "outputFields": [
                        {"name": "bad-name", "type": "string"},
                        {"name": "items", "type": "array"},
                        {"name": "tags", "type": "array", "itemsType": "array"},
                        {"name": "status", "type": "string", "enum": [1, 2]},
                    ],
                },
            },
            {"node_id": "llm_1", "node_type": "llm", "label": "LLM", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "extract_1", "source_handle": "output"},
            {"source_node_id": "extract_1", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        messages = [e.message for e in result.errors]
        self.assertTrue(any("Invalid parameter_extractor output field name" in m for m in messages))
        self.assertTrue(any("requires itemsType" in m for m in messages))
        self.assertTrue(any("itemsType cannot be array" in m for m in messages))
        self.assertTrue(any("enum must be string array" in m for m in messages))

    def test_parameter_extractor_input_content_template_must_reference_upstream(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "extract_1",
                "node_type": "parameter_extractor",
                "label": "Extract",
                "config": {
                    "inputContent": "{{llm_1.response}}",
                    "outputFields": [{"name": "city", "type": "string"}],
                },
            },
            {"node_id": "llm_1", "node_type": "llm", "label": "LLM", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "extract_1", "source_handle": "output"},
            {"source_node_id": "extract_1", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("non-upstream node: llm_1" in e.message for e in result.errors))

    def test_iteration_node_requires_input_output_config(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "iter_1",
                "node_type": "iteration",
                "label": "Iter",
                "config": {
                    "inputSource": "",
                    "outputVariable": "bad-name",
                    "outputSelector": "",
                    "bodyNodes": [{"nodeId": "start", "nodeType": "start", "label": "Start", "config": {}}],
                    "bodyEdges": [],
                },
            },
            {"node_id": "llm_1", "node_type": "llm", "label": "LLM", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "iter_1", "source_handle": "output"},
            {"source_node_id": "iter_1", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")
        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        messages = [e.message for e in result.errors]
        self.assertTrue(any("iteration inputSource is required" in m for m in messages))
        self.assertTrue(any("iteration outputVariable" in m for m in messages))
        self.assertTrue(any("iteration outputSelector is required" in m for m in messages))

    def test_container_body_disallows_nested_container_nodes(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "iter_1",
                "node_type": "iteration",
                "label": "Iter",
                "config": {
                    "inputSource": "{{start.user_input}}",
                    "outputVariable": "results",
                    "outputSelector": "{{container.item}}",
                    "bodyNodes": [
                        {"nodeId": "start", "nodeType": "start", "label": "Start", "config": {}},
                        {"nodeId": "inner_loop", "nodeType": "loop", "label": "Loop", "config": {}},
                    ],
                    "bodyEdges": [
                        {"sourceNodeId": "start", "targetNodeId": "inner_loop", "sourceHandle": "output"},
                    ],
                },
            },
            {"node_id": "llm_1", "node_type": "llm", "label": "LLM", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "iter_1", "source_handle": "output"},
            {"source_node_id": "iter_1", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")
        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("must not nest iteration/loop" in e.message for e in result.errors))

    def test_loop_node_max_iterations_range_validation(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "loop_1",
                "node_type": "loop",
                "label": "Loop",
                "config": {
                    "maxIterations": 0,
                    "bodyNodes": [{"nodeId": "start", "nodeType": "start", "label": "Start", "config": {}}],
                    "bodyEdges": [],
                },
            },
            {"node_id": "llm_1", "node_type": "llm", "label": "LLM", "config": {}},
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "loop_1", "source_handle": "output"},
            {"source_node_id": "loop_1", "target_node_id": "llm_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "llm_1")
        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("loop maxIterations must be between 1 and 1000" in e.message for e in result.errors))

    def test_agent_node_requires_tool_names_or_knowledge(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow, validate_workflow_compile

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "agent_1",
                "node_type": "agent",
                "label": "Agent",
                "config": {
                    "userInput": "{{start.user_input}}",
                    "toolNames": [],
                },
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "agent_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "agent_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("requires at least one toolNames entry or knowledgeEnabled=true" in e.message for e in result.errors)
        )

        compile_result = validate_workflow_compile(nodes, edges, tool_names=set())
        self.assertFalse(compile_result.valid)
        self.assertTrue(
            any(
                "requires at least one toolNames entry or knowledgeEnabled=true" in e.message
                for e in compile_result.errors
            )
        )

    def test_agent_node_allows_knowledge_enabled_without_tool_names(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow, validate_workflow_compile

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "agent_1",
                "node_type": "agent",
                "label": "Agent",
                "config": {
                    "userInput": "{{start.user_input}}",
                    "toolNames": [],
                    "knowledgeEnabled": True,
                    "knowledgeMode": "hybrid",
                    "knowledgeTopK": 5,
                },
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "agent_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "agent_1")

        result = validate_workflow(nodes, edges)
        self.assertTrue(result.valid, [e.message for e in result.errors])

        compile_result = validate_workflow_compile(nodes, edges, tool_names={"kb_search"})
        self.assertTrue(compile_result.valid, [e.message for e in compile_result.errors])

    def test_agent_node_rejects_kb_search_in_tool_names(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow, validate_workflow_compile

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "agent_1",
                "node_type": "agent",
                "label": "Agent",
                "config": {
                    "userInput": "{{start.user_input}}",
                    "toolNames": ["kb_search"],
                    "knowledgeEnabled": True,
                },
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "agent_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "agent_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("must not include kb_search; use knowledgeEnabled instead" in e.message for e in result.errors),
            [e.message for e in result.errors],
        )

        compile_result = validate_workflow_compile(nodes, edges, tool_names={"kb_search"})
        self.assertFalse(compile_result.valid)
        self.assertTrue(
            any(
                "must not include kb_search; use knowledgeEnabled instead" in e.message
                for e in compile_result.errors
            ),
            [e.message for e in compile_result.errors],
        )

    def test_agent_node_rejects_invalid_knowledge_config(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow, validate_workflow_compile

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "agent_1",
                "node_type": "agent",
                "label": "Agent",
                "config": {
                    "userInput": "{{start.user_input}}",
                    "toolNames": [],
                    "knowledgeEnabled": True,
                    "knowledgeMode": "unsupported",
                    "knowledgeTopK": 99,
                },
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "agent_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "agent_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("knowledgeMode is invalid" in e.message for e in result.errors), [e.message for e in result.errors])
        self.assertTrue(any("knowledgeTopK must be between 1 and 50" in e.message for e in result.errors), [e.message for e in result.errors])

        compile_result = validate_workflow_compile(nodes, edges, tool_names={"kb_search"})
        self.assertFalse(compile_result.valid)
        self.assertTrue(any("knowledgeMode is invalid" in e.message for e in compile_result.errors), [e.message for e in compile_result.errors])
        self.assertTrue(any("knowledgeTopK must be between 1 and 50" in e.message for e in compile_result.errors), [e.message for e in compile_result.errors])

    def test_agent_node_max_iterations_range_validation(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "agent_1",
                "node_type": "agent",
                "label": "Agent",
                "config": {
                    "userInput": "{{start.user_input}}",
                    "toolNames": ["create_entry"],
                    "maxIterations": 99,
                },
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "agent_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "agent_1")

        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        self.assertTrue(any("maxIterations must be between 1 and 20" in e.message for e in result.errors))

    def test_container_body_agent_uses_same_validation_rules(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow, validate_workflow_compile

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "iter_1",
                "node_type": "iteration",
                "label": "Iter",
                "config": {
                    "inputSource": "{{start.user_input}}",
                    "outputVariable": "results",
                    "outputSelector": "{{container.item}}",
                    "bodyNodes": [
                        {"nodeId": "start", "nodeType": "start", "label": "Start", "config": {}},
                        {
                            "nodeId": "agent_body",
                            "nodeType": "agent",
                            "label": "Agent Body",
                            "config": {
                                "userInput": "{{container.item}}",
                                "toolNames": [],
                                "knowledgeEnabled": True,
                                "knowledgeMode": "local",
                                "knowledgeTopK": 3,
                            },
                        },
                    ],
                    "bodyEdges": [
                        {"sourceNodeId": "start", "targetNodeId": "agent_body", "sourceHandle": "output"},
                    ],
                },
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "iter_1", "source_handle": "output"},
        ]
        nodes, edges = self._append_output_node(nodes, edges, "iter_1")

        result = validate_workflow(nodes, edges)
        self.assertTrue(result.valid, [e.message for e in result.errors])

        compile_result = validate_workflow_compile(nodes, edges, tool_names={"kb_search"})
        self.assertTrue(compile_result.valid, [e.message for e in compile_result.errors])


if __name__ == "__main__":
    unittest.main()

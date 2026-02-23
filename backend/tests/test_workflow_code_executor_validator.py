from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class WorkflowCodeExecutorValidatorTests(unittest.TestCase):
    @staticmethod
    def _build_workflow_nodes_edges(
        *,
        code: str,
        language: str = "python",
        input_bindings: dict[str, str] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        nodes = [
            {
                "node_id": "start",
                "node_type": "start",
                "label": "Start",
                "config": {},
            },
            {
                "node_id": "code_1",
                "node_type": "code_executor",
                "label": "Code",
                "config": {
                    "language": language,
                    "code": code,
                    "entrypoint": "main",
                    "inputBindings": input_bindings if input_bindings is not None else {
                        "arg1": "{{start.user_input}}",
                        "arg2": "",
                    },
                    "outputFields": [
                        {"name": "result", "type": "string", "nullable": False},
                    ],
                },
            },
            {
                "node_id": "output_final",
                "node_type": "output",
                "label": "Output",
                "config": {
                    "outputMode": "text",
                    "textTemplate": "{{code_1.response}}",
                },
            },
        ]
        edges = [
            {
                "source_node_id": "start",
                "target_node_id": "code_1",
                "source_handle": "output",
            },
            {
                "source_node_id": "code_1",
                "target_node_id": "output_final",
                "source_handle": "output",
            },
        ]
        return nodes, edges

    def test_code_executor_python_workflow_is_valid(self) -> None:
        from app.assistant.skills.workflow_validator import (
            validate_workflow,
            validate_workflow_compile,
        )

        nodes, edges = self._build_workflow_nodes_edges(
            code=(
                "def main(arg1: str, arg2: str):\n"
                "    return {'result': f'{arg1}{arg2}'}\n"
            ),
            language="python",
        )

        save_validation = validate_workflow(nodes, edges)
        compile_validation = validate_workflow_compile(nodes, edges, tool_names=set())

        self.assertTrue(save_validation.valid, [item.message for item in save_validation.errors])
        self.assertTrue(compile_validation.valid, [item.message for item in compile_validation.errors])

    def test_code_executor_python_workflow_with_custom_binding_is_valid(self) -> None:
        from app.assistant.skills.workflow_validator import (
            validate_workflow,
            validate_workflow_compile,
        )

        nodes, edges = self._build_workflow_nodes_edges(
            code=(
                "def main(text: str):\n"
                "    return {'result': text.upper()}\n"
            ),
            language="python",
            input_bindings={"text": "{{start.user_input}}"},
        )

        save_validation = validate_workflow(nodes, edges)
        compile_validation = validate_workflow_compile(nodes, edges, tool_names=set())

        self.assertTrue(save_validation.valid, [item.message for item in save_validation.errors])
        self.assertTrue(compile_validation.valid, [item.message for item in compile_validation.errors])

    def test_code_executor_rejects_signature_binding_mismatch(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes, edges = self._build_workflow_nodes_edges(
            code=(
                "def main(inputs, context):\n"
                "    return {'result': str(inputs)}\n"
            ),
        )

        validation = validate_workflow(nodes, edges)
        self.assertFalse(validation.valid)
        self.assertTrue(
            any("signature must match inputBindings keys" in item.message for item in validation.errors),
            [item.message for item in validation.errors],
        )

    def test_code_executor_rejects_invalid_input_binding_key(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes, edges = self._build_workflow_nodes_edges(
            code="def main(valid_key: str):\n    return {'result': valid_key}\n",
            input_bindings={"invalid-key": "{{start.user_input}}"},
        )

        validation = validate_workflow(nodes, edges)
        self.assertFalse(validation.valid)
        self.assertTrue(
            any("input binding key is invalid" in item.message for item in validation.errors),
            [item.message for item in validation.errors],
        )

    def test_code_executor_allows_empty_input_bindings_with_main_no_args(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes, edges = self._build_workflow_nodes_edges(
            code="def main():\n    return {'result': 'ok'}\n",
            input_bindings={},
        )

        validation = validate_workflow(nodes, edges)
        self.assertTrue(validation.valid, [item.message for item in validation.errors])

    def test_code_executor_rejects_disallowed_python_import(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes, edges = self._build_workflow_nodes_edges(
            code=(
                "import os\n\n"
                "def main(arg1: str, arg2: str):\n"
                "    return {'result': f'{arg1}{arg2}'}\n"
            ),
            language="python",
        )

        validation = validate_workflow(nodes, edges)
        self.assertFalse(validation.valid)
        self.assertTrue(
            any("imports not allowed" in item.message for item in validation.errors),
            [item.message for item in validation.errors],
        )

    def test_code_executor_rejects_dynamic_javascript_import(self) -> None:
        from app.assistant.skills.workflow_validator import validate_workflow

        nodes, edges = self._build_workflow_nodes_edges(
            code=(
                "async function main(arg1, arg2) {\n"
                "  const mod = await import('node:crypto')\n"
                "  return { result: String(Boolean(mod)) }\n"
                "}\n"
            ),
            language="javascript",
        )

        validation = validate_workflow(nodes, edges)
        self.assertFalse(validation.valid)
        self.assertTrue(
            any("dynamic JavaScript import()" in item.message for item in validation.errors),
            [item.message for item in validation.errors],
        )

    def test_iteration_body_allows_code_executor_node(self) -> None:
        from app.assistant.skills.workflow_validator import (
            validate_workflow,
            validate_workflow_compile,
        )

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
                    "parallelMode": False,
                    "errorStrategy": "fail_fast",
                    "flattenOutput": True,
                    "bodyNodes": [
                        {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
                        {
                            "node_id": "code_body",
                            "node_type": "code_executor",
                            "label": "Code",
                            "config": {
                                "language": "python",
                                "code": (
                                    "def main(item: str):\n"
                                    "    return {'result': item}\n"
                                ),
                                "entrypoint": "main",
                                "inputBindings": {
                                    "item": "{{container.item}}",
                                },
                                "outputFields": [{"name": "result", "type": "string", "nullable": False}],
                            },
                        },
                    ],
                    "bodyEdges": [
                        {"source_node_id": "start", "target_node_id": "code_body", "source_handle": "output"},
                    ],
                },
            },
            {
                "node_id": "output_final",
                "node_type": "output",
                "label": "Output",
                "config": {"outputMode": "text", "textTemplate": "{{start.user_input}}"},
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "iter_1", "source_handle": "output"},
            {"source_node_id": "iter_1", "target_node_id": "output_final", "source_handle": "output"},
        ]

        save_validation = validate_workflow(nodes, edges)
        compile_validation = validate_workflow_compile(nodes, edges, tool_names=set())

        self.assertTrue(save_validation.valid, [item.message for item in save_validation.errors])
        self.assertTrue(compile_validation.valid, [item.message for item in compile_validation.errors])

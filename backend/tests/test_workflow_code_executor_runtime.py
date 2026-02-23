from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class WorkflowCodeExecutorRuntimeTests(unittest.TestCase):
    def test_execute_code_python_success_with_custom_binding_key(self) -> None:
        from app.assistant.skills.code_executor import execute_code

        result = execute_code(
            language="python",
            code=(
                "def main(text: str):\n"
                "    return {'result': text.upper()}\n"
            ),
            entrypoint="main",
            inputs={"text": "mindatlas"},
            output_fields=[{"name": "result", "type": "string", "nullable": False}],
        )

        self.assertEqual(result.output, {"result": "MINDATLAS"})

    def test_execute_code_javascript_success_with_multiple_custom_keys(self) -> None:
        from app.assistant.skills.code_executor import execute_code

        result = execute_code(
            language="javascript",
            code=(
                "function main(text, suffix) {\n"
                "  return { result: String(text ?? '').toUpperCase() + String(suffix ?? '').toUpperCase() }\n"
                "}\n"
            ),
            entrypoint="main",
            inputs={"text": "mind", "suffix": "atlas"},
            output_fields=[{"name": "result", "type": "string", "nullable": False}],
        )

        self.assertEqual(result.output, {"result": "MINDATLAS"})

    def test_execute_code_rejects_schema_mismatch(self) -> None:
        from app.assistant.skills.code_executor import CodeExecutionError, execute_code

        with self.assertRaises(CodeExecutionError) as ctx:
            execute_code(
                language="python",
                code="def main(text: str):\n    return {'result': text, 'extra': 'x'}\n",
                entrypoint="main",
                inputs={"text": "ok"},
                output_fields=[{"name": "result", "type": "string", "nullable": False}],
            )

        self.assertIn("unexpected fields", str(ctx.exception))

    def test_execute_code_timeout_fails_fast(self) -> None:
        from app.assistant.skills.code_executor import CodeExecutionError, execute_code

        with self.assertRaises(CodeExecutionError) as ctx:
            execute_code(
                language="python",
                code=(
                    "def main(text: str):\n"
                    "    while True:\n"
                    "        pass\n"
                ),
                entrypoint="main",
                inputs={"text": "x"},
                output_fields=[{"name": "result", "type": "string", "nullable": True}],
                timeout_ms=200,
            )

        self.assertIn("timed out", str(ctx.exception).lower())

    def test_container_body_supports_code_executor_node(self) -> None:
        from app.assistant.skills.langgraph_engine import _execute_container_body

        container_result = _execute_container_body(
            container_node_id="iter_1",
            container_node_type="iteration",
            node_cfg={
                "bodyNodes": [
                    {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
                    {
                        "node_id": "code_1",
                        "node_type": "code_executor",
                        "label": "Code",
                        "config": {
                            "language": "python",
                            "code": (
                                "def main(text: str):\n"
                                "    return {'echo': text}\n"
                            ),
                            "entrypoint": "main",
                            "inputBindings": {"text": "{{container.item}}"},
                            "outputFields": [{"name": "echo", "type": "string", "nullable": False}],
                        },
                    },
                ],
                "bodyEdges": [
                    {
                        "source_node_id": "start",
                        "target_node_id": "code_1",
                        "source_handle": "output",
                    },
                ],
            },
            parent_state={"metadata": {}, "node_outputs": {}, "sys_vars": {}, "node_llms": {}},
            llm=object(),
            args_llm=object(),
            tool_map={},
            db_bind=None,
            node_llms={},
            container_input="hello",
            container_fields={"item": "hello", "index": 0},
        )

        node_outputs = container_result.get("node_outputs", {})
        self.assertIn("code_1", node_outputs)
        self.assertEqual(node_outputs["code_1"]["json_fields"]["echo"], "hello")
        self.assertIn("code_1", container_result.get("execution_trace", []))

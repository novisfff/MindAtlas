from __future__ import annotations

import unittest

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class WorkflowHttpRequestValidatorTests(unittest.TestCase):
    @staticmethod
    def _build_nodes_edges(http_config: dict | None = None) -> tuple[list[dict], list[dict]]:
        config = {
            "method": "GET",
            "url": "https://api.example.com/items",
            "headers": [{"key": "Accept", "value": "application/json", "enabled": True}],
            "queryParams": [{"key": "q", "value": "{{start.user_input}}", "enabled": True}],
            "bodyType": "none",
            "authType": "none",
            "timeoutMs": 15000,
            "retryEnabled": False,
            "maxRetries": 2,
            "retryIntervalMs": 200,
            "verifySsl": True,
        }
        if http_config:
            config.update(http_config)

        nodes = [
            {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
            {
                "node_id": "http_1",
                "node_type": "http_request",
                "label": "HTTP",
                "config": config,
            },
            {
                "node_id": "output_1",
                "node_type": "output",
                "label": "Output",
                "config": {"outputMode": "text", "textTemplate": "{{http_1.body}}"},
            },
        ]
        edges = [
            {"source_node_id": "start", "target_node_id": "http_1", "source_handle": "output"},
            {"source_node_id": "http_1", "target_node_id": "output_1", "source_handle": "output"},
        ]
        return nodes, edges

    def test_http_request_minimal_valid_config_passes(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow, validate_workflow_compile

        nodes, edges = self._build_nodes_edges()
        save_result = validate_workflow(nodes, edges)
        compile_result = validate_workflow_compile(nodes, edges, tool_names=set())

        self.assertTrue(save_result.valid, [err.message for err in save_result.errors])
        self.assertTrue(compile_result.valid, [err.message for err in compile_result.errors])

    def test_http_request_missing_url_is_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes, edges = self._build_nodes_edges({"url": ""})
        result = validate_workflow(nodes, edges)

        self.assertFalse(result.valid)
        self.assertTrue(
            any("url is required" in err.message for err in result.errors),
            [err.message for err in result.errors],
        )

    def test_http_request_invalid_method_body_auth_are_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes, edges = self._build_nodes_edges(
            {
                "method": "TRACE",
                "bodyType": "xml",
                "authType": "basic",
            }
        )
        result = validate_workflow(nodes, edges)

        self.assertFalse(result.valid)
        messages = [err.message for err in result.errors]
        self.assertTrue(any("method is invalid" in msg for msg in messages), messages)
        self.assertTrue(any("bodyType is invalid" in msg for msg in messages), messages)
        self.assertTrue(any("authType is invalid" in msg for msg in messages), messages)

    def test_http_request_invalid_retry_and_timeout_range_are_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes, edges = self._build_nodes_edges(
            {
                "timeoutMs": 999999,
                "retryEnabled": True,
                "maxRetries": 99,
                "retryIntervalMs": 7000,
            }
        )
        result = validate_workflow(nodes, edges)

        self.assertFalse(result.valid)
        messages = [err.message for err in result.errors]
        self.assertTrue(any("timeoutMs must be between" in msg for msg in messages), messages)
        self.assertTrue(any("maxRetries must be between" in msg for msg in messages), messages)
        self.assertTrue(any("retryIntervalMs must be between" in msg for msg in messages), messages)

    def test_http_request_form_data_invalid_type_is_rejected(self) -> None:
        from app.assistant.workflow.validation.validator import validate_workflow

        nodes, edges = self._build_nodes_edges(
            {
                "method": "POST",
                "bodyType": "form-data",
                "formBody": [
                    {"key": "file1", "type": "blob", "value": "/tmp/mock.bin", "enabled": True},
                ],
            }
        )
        result = validate_workflow(nodes, edges)
        self.assertFalse(result.valid)
        messages = [err.message for err in result.errors]
        self.assertTrue(any("type must be one of: text,file" in msg for msg in messages), messages)

    def test_iteration_body_supports_http_request_node(self) -> None:
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
                        {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
                        {
                            "node_id": "http_body",
                            "node_type": "http_request",
                            "label": "HTTP",
                            "config": {
                                "method": "GET",
                                "url": "https://api.example.com/item",
                                "queryParams": [{"key": "value", "value": "{{container.item}}", "enabled": True}],
                                "bodyType": "none",
                                "authType": "none",
                                "timeoutMs": 15000,
                                "retryEnabled": False,
                                "maxRetries": 2,
                                "retryIntervalMs": 200,
                                "verifySsl": True,
                            },
                        },
                    ],
                    "bodyEdges": [
                        {"source_node_id": "start", "target_node_id": "http_body", "source_handle": "output"},
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

        save_result = validate_workflow(nodes, edges)
        compile_result = validate_workflow_compile(nodes, edges, tool_names=set())

        self.assertTrue(save_result.valid, [err.message for err in save_result.errors])
        self.assertTrue(compile_result.valid, [err.message for err in compile_result.errors])


if __name__ == "__main__":
    unittest.main()

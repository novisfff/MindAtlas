from __future__ import annotations

import io
import os
import tempfile
import unittest
from email.message import Message
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from tests._bootstrap import bootstrap_backend_imports


bootstrap_backend_imports()


class _FakeResponse:
    def __init__(self, *, status_code: int, body: str, reason: str = "OK", headers: Message | None = None):
        self._status_code = status_code
        self._body = io.BytesIO(body.encode("utf-8"))
        self.reason = reason
        self.headers = headers or Message()
        if "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "application/json; charset=utf-8"

    def getcode(self) -> int:
        return self._status_code

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(status: int, message: str, body: str) -> HTTPError:
    headers = Message()
    headers["Content-Type"] = "application/json; charset=utf-8"
    return HTTPError(
        url="https://api.example.com/test",
        code=status,
        msg=message,
        hdrs=headers,
        fp=io.BytesIO(body.encode("utf-8")),
    )


class WorkflowHttpRequestRuntimeTests(unittest.TestCase):
    def test_http_request_200_response_mapping(self) -> None:
        from app.assistant.workflow.http_request import execute_http_request

        opener = Mock()
        opener.open.return_value = _FakeResponse(status_code=200, body='{"ok":true}')

        with (
            patch("app.assistant.workflow.http_request.validate_url_ssrf", return_value=None),
            patch("app.assistant.workflow.http_request._build_http_opener", return_value=opener),
        ):
            result = execute_http_request(
                method="GET",
                url="https://api.example.com/users",
                timeout_ms=15000,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body, '{"ok":true}')
        self.assertEqual(result.response, result.body)
        self.assertEqual(result.error_message, "")

    def test_http_request_404_returns_structured_failure_without_raise(self) -> None:
        from app.assistant.workflow.http_request import execute_http_request

        opener = Mock()
        opener.open.side_effect = _http_error(404, "Not Found", '{"detail":"missing"}')

        with (
            patch("app.assistant.workflow.http_request.validate_url_ssrf", return_value=None),
            patch("app.assistant.workflow.http_request._build_http_opener", return_value=opener),
        ):
            result = execute_http_request(
                method="GET",
                url="https://api.example.com/users/404",
                retry_enabled=True,
                max_retries=2,
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.status_code, 404)
        self.assertIn("HTTP 404", result.error_message)
        self.assertIn("missing", result.body)

    def test_http_request_5xx_retries_then_succeeds(self) -> None:
        from app.assistant.workflow.http_request import execute_http_request

        opener = Mock()
        opener.open.side_effect = [
            _http_error(502, "Bad Gateway", '{"detail":"upstream"}'),
            _FakeResponse(status_code=200, body='{"result":"ok"}'),
        ]

        with (
            patch("app.assistant.workflow.http_request.validate_url_ssrf", return_value=None),
            patch("app.assistant.workflow.http_request._build_http_opener", return_value=opener),
        ):
            result = execute_http_request(
                method="GET",
                url="https://api.example.com/retry",
                retry_enabled=True,
                max_retries=2,
                retry_interval_ms=0,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(opener.open.call_count, 2)

    def test_http_request_transport_failure_retries_then_raises(self) -> None:
        from app.assistant.workflow.http_request import execute_http_request

        opener = Mock()
        opener.open.side_effect = URLError("timed out")

        with (
            patch("app.assistant.workflow.http_request.validate_url_ssrf", return_value=None),
            patch("app.assistant.workflow.http_request._build_http_opener", return_value=opener),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                execute_http_request(
                    method="GET",
                    url="https://api.example.com/timeout",
                    retry_enabled=True,
                    max_retries=2,
                    retry_interval_ms=0,
                )

        self.assertIn("transport failed", str(ctx.exception))
        self.assertEqual(opener.open.call_count, 3)

    def test_http_request_verify_ssl_false_path_is_used(self) -> None:
        from app.assistant.workflow.http_request import execute_http_request

        opener = Mock()
        opener.open.return_value = _FakeResponse(status_code=200, body='{"ok":true}')

        with (
            patch("app.assistant.workflow.http_request.validate_url_ssrf", return_value=None),
            patch("app.assistant.workflow.http_request._build_http_opener", return_value=opener) as mocked_builder,
        ):
            execute_http_request(
                method="GET",
                url="https://api.example.com/ssl",
                verify_ssl=False,
            )

        mocked_builder.assert_called_with(verify_ssl=False)

    def test_http_request_form_data_supports_text_and_file_rows(self) -> None:
        from app.assistant.workflow.http_request import execute_http_request

        opener = Mock()
        captured: dict[str, object] = {}

        with tempfile.NamedTemporaryFile("wb", delete=False) as handle:
            handle.write(b"hello-file")
            file_path = handle.name

        def _open(request, timeout=None):
            captured["request"] = request
            return _FakeResponse(status_code=200, body='{"ok":true}')

        opener.open.side_effect = _open

        try:
            with (
                patch("app.assistant.workflow.http_request.validate_url_ssrf", return_value=None),
                patch("app.assistant.workflow.http_request._build_http_opener", return_value=opener),
            ):
                result = execute_http_request(
                    method="POST",
                    url="https://api.example.com/upload",
                    body_type="form-data",
                    form_body=[
                        {"key": "desc", "type": "text", "value": "hello"},
                        {"key": "asset", "type": "file", "value": file_path},
                    ],
                )
        finally:
            os.unlink(file_path)

        self.assertTrue(result.ok)
        request = captured.get("request")
        self.assertIsNotNone(request)
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertIn("content-type", headers)
        self.assertIn("multipart/form-data", headers["content-type"])
        body = request.data or b""
        self.assertIn(b'name=\"desc\"', body)
        self.assertIn(b"hello", body)
        self.assertIn(b'name=\"asset\"', body)
        self.assertIn(b"filename=", body)
        self.assertIn(b"hello-file", body)

    def test_container_body_supports_http_request_node(self) -> None:
        from app.assistant.workflow.engine.container_runtime import execute_container_body
        from app.assistant.workflow.http_request import HttpRequestResult

        with patch(
            "app.assistant.workflow.engine.node_builders.http_request_node.execute_http_request",
            return_value=HttpRequestResult(
                body='{"item":"ok"}',
                status_code=200,
                headers={"content-type": "application/json"},
                ok=True,
                error_message="",
                response='{"item":"ok"}',
            ),
        ):
            result = execute_container_body(
                container_node_id="iter_1",
                container_node_type="iteration",
                node_cfg={
                    "bodyNodes": [
                        {"node_id": "start", "node_type": "start", "label": "Start", "config": {}},
                        {
                            "node_id": "http_body",
                            "node_type": "http_request",
                            "label": "HTTP",
                            "config": {
                                "method": "GET",
                                "url": "https://api.example.com/item/{{container.item}}",
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
                parent_state={"metadata": {}, "node_outputs": {}, "sys_vars": {}, "node_llms": {}},
                llm=object(),
                args_llm=object(),
                tool_map={},
                db_bind=None,
                node_llms={},
                container_input="value",
                container_fields={"item": "value", "index": 0},
            )

        node_outputs = result.get("node_outputs", {})
        self.assertIn("http_body", node_outputs)
        self.assertEqual(node_outputs["http_body"]["json_fields"]["status_code"], 200)
        self.assertTrue(node_outputs["http_body"]["json_fields"]["ok"])


if __name__ == "__main__":
    unittest.main()

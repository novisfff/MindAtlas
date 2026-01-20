import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError
import io

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()

import app.assistant_config.remote_tool as remote_tool  # noqa: E402


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


class RemoteToolTests(unittest.TestCase):
    def _headers(self, req) -> dict[str, str]:
        return {k.lower(): v for k, v in req.header_items()}

    def test_validate_url_security_blocks_localhost(self):
        with self.assertRaises(remote_tool.SSRFError):
            remote_tool.validate_url_security("http://localhost:8000/ping")

    def test_validate_url_security_blocks_private_ip(self):
        with self.assertRaises(remote_tool.SSRFError):
            remote_tool.validate_url_security("http://127.0.0.1/ping")
        with self.assertRaises(remote_tool.SSRFError):
            remote_tool.validate_url_security("http://192.168.1.2/ping")

    def test_validate_url_security_blocks_bad_scheme(self):
        with self.assertRaises(remote_tool.SSRFError):
            remote_tool.validate_url_security("ftp://example.com/a")

    def test_validate_url_security_requires_hostname(self):
        with self.assertRaises(remote_tool.SSRFError):
            remote_tool.validate_url_security("http:///no-host")

    def test_validate_url_security_allows_dns_failure(self):
        with patch.object(remote_tool.socket, "getaddrinfo", side_effect=remote_tool.socket.gaierror()):
            # DNS failure is treated as allowed (warning log), should not raise.
            remote_tool.validate_url_security("https://example.com/api")

    def test_validate_url_security_blocks_if_dns_resolves_private(self):
        with patch.object(
            remote_tool.socket,
            "getaddrinfo",
            return_value=[(remote_tool.socket.AF_INET, 0, 0, "", ("127.0.0.1", 0))],
        ):
            with self.assertRaises(remote_tool.SSRFError):
                remote_tool.validate_url_security("https://example.com/api")

    def test_invoke_post_json_body_and_query_params_and_auth(self):
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["req"] = req
            captured["timeout"] = timeout
            return _FakeResponse(b"ok")

        safe_dns = [
            (remote_tool.socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),  # example.com
        ]

        tool = remote_tool.RemoteTool(
            name="t",
            description="d",
            input_params=None,
            endpoint_url="https://api.example.com/endpoint",
            http_method="POST",
            headers={"x-user": "{{user}}"},
            query_params={"q": "{{keyword}}"},
            body_type="json",
            body_content='{"k":{{keyword}},"q":"{{user_input}}"}',
            auth_type="bearer",
            auth_header_name="Authorization",
            auth_scheme="Bearer",
            api_key_encrypted="encrypted",
            timeout_seconds=7,
        )

        with (
            patch.object(remote_tool.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_tool, "decrypt_api_key", return_value="token123"),
            patch.object(remote_tool, "urlopen", new=fake_urlopen),
        ):
            out = tool.invoke({"keyword": "abc", "user_input": "hi", "user": "u1"})

        self.assertEqual(out, "ok")
        req = captured["req"]
        headers = self._headers(req)
        self.assertIn("q=abc", req.full_url)
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(headers.get("x-user"), "u1")
        self.assertEqual(headers.get("authorization"), "Bearer token123")
        self.assertEqual(headers.get("content-type"), "application/json")
        self.assertEqual(req.data.decode("utf-8"), '{"k":"abc","q":"hi"}')

    def test_invoke_get_merges_query_params_and_args(self):
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["req"] = req
            return _FakeResponse(b"ok")

        safe_dns = [
            (remote_tool.socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
        ]

        tool = remote_tool.RemoteTool(
            name="t",
            description=None,
            input_params=None,
            endpoint_url="https://api.example.com/search",
            http_method="GET",
            query_params={"fixed": "1"},
        )

        with (
            patch.object(remote_tool.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_tool, "urlopen", new=fake_urlopen),
        ):
            tool.invoke({"a": 1, "b": "x"})

        parsed = urlparse(captured["req"].full_url)
        qs = parse_qs(parsed.query)
        self.assertEqual(qs["fixed"], ["1"])
        self.assertEqual(qs["a"], ["1"])
        self.assertEqual(qs["b"], ['"x"'])

    def test_invoke_form_data_sets_boundary_and_body(self):
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["req"] = req
            return _FakeResponse(b"ok")

        safe_dns = [
            (remote_tool.socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
        ]

        tool = remote_tool.RemoteTool(
            name="t",
            description=None,
            input_params=None,
            endpoint_url="https://api.example.com/upload",
            http_method="POST",
            body_type="form-data",
        )

        with (
            patch.object(remote_tool.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_tool, "urlopen", new=fake_urlopen),
        ):
            tool.invoke({"a": "1"})

        req = captured["req"]
        headers = self._headers(req)
        self.assertEqual(req.get_method(), "POST")
        self.assertIsInstance(req.data, (bytes, bytearray))
        self.assertIn(b'Content-Disposition: form-data; name="a"', req.data)
        self.assertIn(b"\r\n\r\n1\r\n", req.data)
        self.assertIn("multipart/form-data; boundary=", headers.get("content-type", ""))

    def test_render_json_template_quoted_placeholder_escapes_content(self):
        tpl = '{"q":"{{user_input}}"}'
        rendered = remote_tool._render_json_template(tpl, {"user_input": '"x"'})
        self.assertEqual(rendered, '{"q":"\\"x\\""}')

    def test_invoke_xml_sets_content_type(self):
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["req"] = req
            return _FakeResponse(b"ok")

        safe_dns = [(remote_tool.socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
        tool = remote_tool.RemoteTool(
            name="t",
            description=None,
            input_params=None,
            endpoint_url="https://api.example.com/xml",
            http_method="POST",
            body_type="xml",
            body_content="<x>{{a}}</x>",
        )

        with (
            patch.object(remote_tool.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_tool, "urlopen", new=fake_urlopen),
        ):
            tool.invoke({"a": 1})

        req = captured["req"]
        headers = self._headers(req)
        self.assertEqual(headers.get("content-type"), "application/xml")
        self.assertEqual(req.data.decode("utf-8"), "<x>1</x>")

    def test_invoke_payload_wrapper(self):
        captured = {}

        def fake_urlopen(req, timeout=0):
            captured["req"] = req
            return _FakeResponse(b"ok")

        safe_dns = [(remote_tool.socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
        tool = remote_tool.RemoteTool(
            name="t",
            description=None,
            input_params=None,
            endpoint_url="https://api.example.com/endpoint",
            http_method="POST",
            body_type="none",
            body_content="",
            payload_wrapper="payload",
        )

        with (
            patch.object(remote_tool.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_tool, "urlopen", new=fake_urlopen),
        ):
            tool.invoke({"a": 1})

        req = captured["req"]
        self.assertEqual(req.data.decode("utf-8"), '{"payload": {"a": 1}}')

    def test_do_request_http_error_includes_body(self):
        err = HTTPError(
            url="https://api.example.com/endpoint",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b"oops"),
        )
        with patch.object(remote_tool, "urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as ctx:
                remote_tool.RemoteTool._do_request(remote_tool.Request("https://api.example.com"), timeout=1)
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertIn("oops", str(ctx.exception))

    def test_do_request_url_error(self):
        with patch.object(remote_tool, "urlopen", side_effect=URLError("down")):
            with self.assertRaises(RuntimeError) as ctx:
                remote_tool.RemoteTool._do_request(remote_tool.Request("https://api.example.com"), timeout=1)
        self.assertIn("Connection failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

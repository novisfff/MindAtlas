import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError, URLError
import io
import socket

from tests._bootstrap import bootstrap_backend_imports, reset_caches


bootstrap_backend_imports()
reset_caches()

import app.assistant_config.remote_tool as remote_tool  # noqa: E402
from app.common.ssrf import SSRFError, validate_url_ssrf  # noqa: E402
import app.common.ssrf as ssrf_mod  # noqa: E402


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


class _FakeOpener:
    """Fake opener that captures the request instead of making a real HTTP call."""

    def __init__(self, captured: dict, response_body: bytes = b"ok"):
        self._captured = captured
        self._response_body = response_body

    def open(self, req, timeout=0):
        self._captured["req"] = req
        self._captured["timeout"] = timeout
        return _FakeResponse(self._response_body)


class RemoteToolTests(unittest.TestCase):
    def _headers(self, req) -> dict[str, str]:
        return {k.lower(): v for k, v in req.header_items()}

    def test_validate_url_security_blocks_localhost(self):
        with self.assertRaises(SSRFError):
            validate_url_ssrf("http://localhost:8000/ping")

    def test_validate_url_security_blocks_private_ip(self):
        with self.assertRaises(SSRFError):
            validate_url_ssrf("http://127.0.0.1/ping")
        with self.assertRaises(SSRFError):
            validate_url_ssrf("http://192.168.1.2/ping")

    def test_validate_url_security_blocks_bad_scheme(self):
        with self.assertRaises(SSRFError):
            validate_url_ssrf("ftp://example.com/a")

    def test_validate_url_security_requires_hostname(self):
        with self.assertRaises(SSRFError):
            validate_url_ssrf("http:///no-host")

    def test_validate_url_security_allows_dns_failure(self):
        with patch.object(ssrf_mod.socket, "getaddrinfo", side_effect=socket.gaierror()):
            # DNS failure is treated as allowed (warning log), should not raise.
            validate_url_ssrf("https://example.com/api")

    def test_validate_url_security_blocks_if_dns_resolves_private(self):
        with patch.object(
            ssrf_mod.socket,
            "getaddrinfo",
            return_value=[(socket.AF_INET, 0, 0, "", ("127.0.0.1", 0))],
        ):
            with self.assertRaises(SSRFError):
                validate_url_ssrf("https://example.com/api")

    def test_validate_url_security_allows_fake_ip_dns_result(self):
        with patch.object(
            ssrf_mod.socket,
            "getaddrinfo",
            return_value=[(socket.AF_INET, 0, 0, "", ("198.18.0.34", 0))],
        ):
            validate_url_ssrf("https://api-vip.codex-for.me/v1")

    def test_validate_url_security_blocks_direct_fake_ip_literal(self):
        with self.assertRaises(SSRFError):
            validate_url_ssrf("http://198.18.0.34/ping")

    def test_invoke_post_json_body_and_query_params_and_auth(self):
        captured = {}

        safe_dns = [
            (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),  # example.com
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
            patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_tool, "decrypt_api_key", return_value="token123"),
            patch.object(remote_tool, "build_opener", return_value=_FakeOpener(captured)),
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

        safe_dns = [
            (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
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
            patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_tool, "build_opener", return_value=_FakeOpener(captured)),
        ):
            tool.invoke({"a": 1, "b": "x"})

        parsed = urlparse(captured["req"].full_url)
        qs = parse_qs(parsed.query)
        self.assertEqual(qs["fixed"], ["1"])
        self.assertEqual(qs["a"], ["1"])
        self.assertEqual(qs["b"], ['"x"'])

    def test_invoke_form_data_sets_boundary_and_body(self):
        captured = {}

        safe_dns = [
            (socket.AF_INET, 0, 0, "", ("93.184.216.34", 0)),
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
            patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_tool, "build_opener", return_value=_FakeOpener(captured)),
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

        safe_dns = [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
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
            patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_tool, "build_opener", return_value=_FakeOpener(captured)),
        ):
            tool.invoke({"a": 1})

        req = captured["req"]
        headers = self._headers(req)
        self.assertEqual(headers.get("content-type"), "application/xml")
        self.assertEqual(req.data.decode("utf-8"), "<x>1</x>")

    def test_invoke_payload_wrapper(self):
        captured = {}

        safe_dns = [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
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
            patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns),
            patch.object(remote_tool, "build_opener", return_value=_FakeOpener(captured)),
        ):
            tool.invoke({"a": 1})

        req = captured["req"]
        self.assertEqual(req.data.decode("utf-8"), '{"payload": {"a": 1}}')

    def test_do_request_http_error_is_secret_safe(self):
        secret_body = b"oops-api_key=sk-live-LEAKED"
        err = HTTPError(
            url="https://api.example.com/endpoint",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(secret_body),
        )

        class _ErrorOpener:
            def open(self, req, timeout=0):
                raise err

        with patch.object(remote_tool, "build_opener", return_value=_ErrorOpener()):
            with self.assertRaises(remote_tool.RemoteToolRequestError) as ctx:
                remote_tool.RemoteTool._do_request(
                    remote_tool.Request("https://api.example.com"),
                    timeout=1,
                    safe_endpoint_host="api.example.com",
                )
        exc = ctx.exception
        self.assertEqual(exc.category, "http")
        self.assertEqual(exc.http_status, 400)
        # Compatibility string and structured fields must not include the body.
        self.assertNotIn("oops", str(exc))
        self.assertNotIn("sk-live-LEAKED", str(exc))
        self.assertNotIn("oops", repr(exc))
        self.assertNotIn("sk-live-LEAKED", repr(exc))
        self.assertIn("HTTP", str(exc))

    def test_do_request_url_error(self):
        class _ErrorOpener:
            def open(self, req, timeout=0):
                raise URLError("down-secret-reason")

        with patch.object(remote_tool, "build_opener", return_value=_ErrorOpener()):
            with self.assertRaises(remote_tool.RemoteToolRequestError) as ctx:
                remote_tool.RemoteTool._do_request(
                    remote_tool.Request("https://api.example.com"),
                    timeout=1,
                    safe_endpoint_host="api.example.com",
                )
        exc = ctx.exception
        self.assertEqual(exc.category, "connection")
        self.assertTrue(exc.is_connection)
        self.assertNotIn("down-secret-reason", str(exc))
        self.assertNotIn("down-secret-reason", repr(exc))
        self.assertIn("connection", str(exc).lower())

    def test_do_request_timeout_maps_to_timeout_category(self):
        class _ErrorOpener:
            def open(self, req, timeout=0):
                raise URLError(TimeoutError("secret-timeout-detail"))

        with patch.object(remote_tool, "build_opener", return_value=_ErrorOpener()):
            with self.assertRaises(remote_tool.RemoteToolRequestError) as ctx:
                remote_tool.RemoteTool._do_request(
                    remote_tool.Request("https://api.example.com"),
                    timeout=1,
                    safe_endpoint_host="api.example.com",
                )
        exc = ctx.exception
        self.assertEqual(exc.category, "timeout")
        self.assertTrue(exc.is_timeout)
        self.assertNotIn("secret-timeout-detail", str(exc))

    # --- IPv6-mapped IPv4 SSRF bypass tests ---

    def test_validate_url_blocks_ipv6_mapped_loopback(self):
        """::ffff:127.0.0.1 must be blocked (IPv6-mapped IPv4 loopback)."""
        with self.assertRaises(SSRFError):
            validate_url_ssrf("http://[::ffff:127.0.0.1]/ping")

    def test_validate_url_blocks_ipv6_mapped_private(self):
        """::ffff:192.168.1.1 must be blocked (IPv6-mapped private IP)."""
        with self.assertRaises(SSRFError):
            validate_url_ssrf("http://[::ffff:192.168.1.1]/ping")

    def test_validate_url_blocks_ipv6_mapped_10_net(self):
        """::ffff:10.0.0.1 must be blocked (IPv6-mapped 10.x private)."""
        with self.assertRaises(SSRFError):
            validate_url_ssrf("http://[::ffff:10.0.0.1]/api")

    def test_validate_url_blocks_ipv6_loopback(self):
        """Pure IPv6 loopback ::1 must be blocked."""
        with self.assertRaises(SSRFError):
            validate_url_ssrf("http://[::1]/ping")

    def test_validate_url_blocks_dns_resolving_to_ipv6_mapped(self):
        """DNS resolving to ::ffff:127.0.0.1 must be blocked."""
        fake_addrs = [
            (socket.AF_INET6, 0, 0, "", ("::ffff:127.0.0.1", 0, 0, 0)),
        ]
        with patch.object(ssrf_mod.socket, "getaddrinfo", return_value=fake_addrs):
            with self.assertRaises(SSRFError):
                validate_url_ssrf("https://evil.example.com/api")

    # --- Redirect-based SSRF tests ---

    def test_redirect_to_internal_ip_is_blocked(self):
        """Redirect from external to internal IP must be blocked by SSRFSafeRedirectHandler."""
        handler = remote_tool._SSRFSafeRedirectHandler()
        req = remote_tool.Request("https://api.example.com/endpoint")
        with self.assertRaises(SSRFError):
            handler.redirect_request(
                req, None, 302, "Found", {}, "http://127.0.0.1/secret"
            )

    def test_redirect_to_ipv6_mapped_loopback_is_blocked(self):
        """Redirect to IPv6-mapped loopback must be blocked."""
        handler = remote_tool._SSRFSafeRedirectHandler()
        req = remote_tool.Request("https://api.example.com/endpoint")
        with self.assertRaises(SSRFError):
            handler.redirect_request(
                req, None, 302, "Found", {}, "http://[::ffff:127.0.0.1]/secret"
            )

    def test_redirect_to_safe_url_is_allowed(self):
        """Redirect to a safe external URL should be allowed."""
        handler = remote_tool._SSRFSafeRedirectHandler()
        req = remote_tool.Request("https://api.example.com/endpoint")
        safe_dns = [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
        with patch.object(ssrf_mod.socket, "getaddrinfo", return_value=safe_dns):
            result = handler.redirect_request(
                req, None, 302, "Found", {}, "https://other.example.com/ok"
            )
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()

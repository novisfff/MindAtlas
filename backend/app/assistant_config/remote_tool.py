from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener

from app.ai_provider.crypto import decrypt_api_key
from app.common.ssrf import validate_url_ssrf


class _SSRFSafeRedirectHandler(HTTPRedirectHandler):
    """Redirect handler that validates each redirect target against SSRF rules."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url_ssrf(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

if TYPE_CHECKING:
    from app.assistant_config.models import AssistantTool

logger = logging.getLogger(__name__)


_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

RemoteErrorCategory = Literal[
    "http",
    "connection",
    "timeout",
    "ssrf",
    "protocol",
    "config",
]


class RemoteToolRequestError(Exception):
    """Internal structured remote failure without secret-bearing payload.

    Compatibility callers that stringify the exception receive only a generic
    safe message. Never put response bodies, headers, credentials, full query
    strings, or raw ``URLError.reason`` text into this object.
    """

    def __init__(
        self,
        *,
        category: RemoteErrorCategory,
        http_status: int | None = None,
        is_timeout: bool = False,
        is_connection: bool = False,
        safe_endpoint_host: str | None = None,
    ) -> None:
        self.category = category
        self.http_status = http_status
        self.is_timeout = bool(is_timeout)
        self.is_connection = bool(is_connection)
        self.safe_endpoint_host = safe_endpoint_host
        super().__init__(self.safe_message)

    @property
    def safe_message(self) -> str:
        if self.category == "timeout" or self.is_timeout:
            return "Remote tool request timed out"
        if self.category == "http" and self.http_status is not None:
            return f"Remote tool HTTP error ({self.http_status})"
        if self.category == "ssrf":
            return "Remote tool endpoint rejected"
        if self.category == "config":
            return "Remote tool configuration invalid"
        if self.category == "connection" or self.is_connection:
            return "Remote tool connection failed"
        return "Remote tool request failed"

    def __str__(self) -> str:
        return self.safe_message

    def __repr__(self) -> str:
        return (
            f"RemoteToolRequestError(category={self.category!r}, "
            f"http_status={self.http_status!r}, is_timeout={self.is_timeout!r}, "
            f"is_connection={self.is_connection!r})"
        )


def _stringify_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _render_string_template(template: str | None, args: dict) -> str:
    tpl = template or ""

    def repl(match: re.Match) -> str:
        key = match.group(1)
        return _stringify_value(args.get(key))

    return _TEMPLATE_VAR_RE.sub(repl, tpl)


def _render_json_template(template: str | None, args: dict) -> str:
    """渲染 JSON 文本模板。

    - 形如 `{{var}}` 的占位符会替换为 JSON 字面量（json.dumps）。
    - 若占位符两侧刚好被双引号包裹（"...{{var}}..."），则替换为“JSON 转义后的字符串内容”（不含外层引号），避免双引号嵌套。
    """
    tpl = template or ""

    def repl(match: re.Match) -> str:
        key = match.group(1)
        value = args.get(key)
        start, end = match.span()
        before = tpl[start - 1] if start > 0 else ""
        after = tpl[end] if end < len(tpl) else ""

        if before == '"' and after == '"':
            # 在 JSON 字符串字面量内部：只插入转义后的内容（不含外层引号）
            as_text = _stringify_value(value)
            dumped = json.dumps(as_text, ensure_ascii=False)
            return dumped[1:-1] if len(dumped) >= 2 else dumped

        return json.dumps(value, ensure_ascii=False, default=str)

    return _TEMPLATE_VAR_RE.sub(repl, tpl)


def _safe_host_from_url(url: str) -> str | None:
    try:
        from urllib.parse import urlsplit

        host = (urlsplit(url).hostname or "").strip().lower()
        return host or None
    except Exception:
        return None


@dataclass
class RemoteTool:
    """远程工具 - 兼容 LangChain tool 接口"""

    name: str
    description: str | None
    # 输入参数定义（来自数据库 assistant_tool.input_params）
    input_params: list[dict[str, Any]] | None = None
    # 输出参数定义（当前默认 None；后续 DB 扩展后可持久化）
    output_params: list[dict[str, Any]] | None = None
    endpoint_url: str = ""
    http_method: str = "POST"
    headers: dict[str, str] | None = None
    query_params: dict[str, str] | None = None
    body_type: str | None = None
    body_content: str | None = None
    auth_type: str | None = None
    auth_header_name: str | None = "Authorization"
    auth_scheme: str | None = "Bearer"
    api_key_encrypted: str | None = None
    timeout_seconds: int = 15
    payload_wrapper: str | None = None

    @classmethod
    def from_model(cls, tool: "AssistantTool") -> "RemoteTool":
        return cls(
            name=tool.name,
            description=tool.description,
            input_params=(tool.input_params or None),
            output_params=(getattr(tool, "output_params", None) or None),
            endpoint_url=(tool.endpoint_url or "").strip(),
            http_method=(tool.http_method or "POST").strip().upper(),
            headers=(tool.headers or None),
            query_params=(tool.query_params or None),
            body_type=(tool.body_type or None),
            body_content=(tool.body_content or None),
            auth_type=(tool.auth_type or None),
            auth_header_name=(tool.auth_header_name or "Authorization").strip() if tool.auth_header_name else None,
            auth_scheme=(tool.auth_scheme or "Bearer").strip() if tool.auth_scheme else None,
            api_key_encrypted=tool.api_key_encrypted,
            timeout_seconds=int(tool.timeout_seconds or 15),
            payload_wrapper=(tool.payload_wrapper or None),
        )

    def func(self, **kwargs: Any) -> str:
        return self.invoke(kwargs)

    def invoke(self, args: dict) -> str:
        url = (self.endpoint_url or "").strip()
        if not url:
            raise RemoteToolRequestError(category="config")

        # SSRF 安全检查
        try:
            validate_url_ssrf(url)
        except Exception as exc:
            # Do not leak URL/body text from SSRFError.
            raise RemoteToolRequestError(
                category="ssrf",
                safe_endpoint_host=_safe_host_from_url(url),
            ) from None

        method = (self.http_method or "POST").strip().upper()
        timeout = max(1, int(self.timeout_seconds or 15))
        safe_host = _safe_host_from_url(url)

        # 组装 headers（支持模板变量）
        headers: dict[str, str] = {}
        if self.headers:
            rendered_headers = {
                str(k): _render_string_template(str(v), args or {})
                for k, v in self.headers.items()
            }
            headers.update(rendered_headers)

        # 添加认证头（覆盖同名 header）— decrypt only immediately before request.
        auth_type = (self.auth_type or "none").strip().lower()
        if self.api_key_encrypted and self.auth_header_name and auth_type != "none":
            api_key = decrypt_api_key(self.api_key_encrypted)
            if auth_type == "bearer":
                scheme = (self.auth_scheme or "Bearer").strip()
                headers[self.auth_header_name] = f"{scheme} {api_key}" if scheme else api_key
            elif auth_type == "api-key":
                headers[self.auth_header_name] = api_key
            elif auth_type == "basic":
                headers[self.auth_header_name] = f"Basic {api_key}"

        # 组装 query params（支持模板变量；GET/DELETE 会把 args 也塞进 query）
        query_params: dict[str, str] = {}
        if self.query_params:
            for k, v in self.query_params.items():
                query_params[str(k)] = _render_string_template(str(v), args or {})

        if method in ("GET", "DELETE"):
            query_params.update({k: json.dumps(v, ensure_ascii=False, default=str) for k, v in (args or {}).items()})
            query = urlencode(query_params)
            final_url = url + ("&" if "?" in url else "?") + query if query else url
            req = Request(final_url, headers=headers, method=method)
            return self._do_request(req, timeout, safe_endpoint_host=safe_host)

        # 非 GET/DELETE：query_params 仍然拼到 URL 上
        if query_params:
            query = urlencode(query_params)
            url = url + ("&" if "?" in url else "?") + query

        body_type = (self.body_type or "none").strip().lower()

        # 默认兼容：body_type=none 且未配置 body_content 时，沿用旧行为 -> JSON(args)
        if body_type in ("none", "") and not (self.body_content or "").strip():
            body_type = "json"

        data: bytes | None = None
        content_type: str | None = None

        if body_type == "json":
            if (self.body_content or "").strip():
                rendered = _render_json_template(self.body_content, args or {})
                data = rendered.encode("utf-8")
            else:
                payload: Any = args or {}
                if self.payload_wrapper:
                    payload = {self.payload_wrapper: args or {}}
                data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            content_type = "application/json"
        elif body_type == "xml":
            rendered = _render_string_template(self.body_content, args or {})
            data = rendered.encode("utf-8")
            content_type = "application/xml"
        elif body_type == "raw":
            rendered = _render_string_template(self.body_content, args or {})
            data = rendered.encode("utf-8")
            content_type = "text/plain"
        elif body_type == "x-www-form-urlencoded":
            if (self.body_content or "").strip():
                rendered = _render_string_template(self.body_content, args or {})
                data = rendered.encode("utf-8")
            else:
                encoded = urlencode({k: _stringify_value(v) for k, v in (args or {}).items()})
                data = encoded.encode("utf-8")
            content_type = "application/x-www-form-urlencoded"
        elif body_type == "form-data":
            boundary = f"----MindAtlasBoundary{uuid.uuid4().hex}"
            parts: list[bytes] = []
            for k, v in (args or {}).items():
                key = str(k)
                value = _stringify_value(v)
                parts.append(f"--{boundary}\r\n".encode("utf-8"))
                parts.append(
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8")
                )
                parts.append(value.encode("utf-8"))
                parts.append(b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode("utf-8"))
            data = b"".join(parts)
            content_type = f"multipart/form-data; boundary={boundary}"
        else:
            # 未识别的类型：兜底为 JSON(args)
            payload = args or {}
            if self.payload_wrapper:
                payload = {self.payload_wrapper: args or {}}
            data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            content_type = "application/json"

        if content_type:
            headers.setdefault("content-type", content_type)

        req = Request(url, data=data, headers=headers, method=method)
        return self._do_request(req, timeout, safe_endpoint_host=safe_host)

    @staticmethod
    def _do_request(
        req: Request,
        timeout: int,
        *,
        safe_endpoint_host: str | None = None,
    ) -> str:
        try:
            opener = build_opener(_SSRFSafeRedirectHandler)
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                return raw.decode("utf-8", errors="ignore")
        except HTTPError as exc:
            # Intentionally discard response body — it may contain secrets.
            try:
                exc.read()
            except Exception:
                pass
            status = getattr(exc, "code", None)
            logger.warning(
                "remote_tool_http_error http_status=%s host=%s",
                status if status is not None else "-",
                safe_endpoint_host or "-",
            )
            raise RemoteToolRequestError(
                category="http",
                http_status=int(status) if isinstance(status, int) else None,
                safe_endpoint_host=safe_endpoint_host,
            ) from None
        except URLError as exc:
            reason = getattr(exc, "reason", None)
            reason_name = type(reason).__name__.lower() if reason is not None else ""
            is_timeout = bool(
                isinstance(reason, TimeoutError)
                or (isinstance(reason, OSError) and getattr(reason, "errno", None) in {60, 110})
                or "timeout" in reason_name
                or "timed out" in reason_name
            )
            # Never log URLError.reason — it can embed secret-bearing server text.
            logger.warning(
                "remote_tool_url_error is_timeout=%s host=%s exc_class=%s",
                is_timeout,
                safe_endpoint_host or "-",
                type(reason).__name__ if reason is not None else type(exc).__name__,
            )
            if is_timeout:
                raise RemoteToolRequestError(
                    category="timeout",
                    is_timeout=True,
                    is_connection=True,
                    safe_endpoint_host=safe_endpoint_host,
                ) from None
            raise RemoteToolRequestError(
                category="connection",
                is_connection=True,
                safe_endpoint_host=safe_endpoint_host,
            ) from None
        except TimeoutError:
            logger.warning(
                "remote_tool_timeout host=%s",
                safe_endpoint_host or "-",
            )
            raise RemoteToolRequestError(
                category="timeout",
                is_timeout=True,
                safe_endpoint_host=safe_endpoint_host,
            ) from None
        except RemoteToolRequestError:
            raise
        except Exception as exc:
            # Redirect SSRF and other transport failures stay secret-free.
            from app.common.ssrf import SSRFError

            if isinstance(exc, SSRFError):
                logger.warning(
                    "remote_tool_ssrf host=%s",
                    safe_endpoint_host or "-",
                )
                raise RemoteToolRequestError(
                    category="ssrf",
                    safe_endpoint_host=safe_endpoint_host,
                ) from None
            logger.warning(
                "remote_tool_protocol_error host=%s exc_class=%s",
                safe_endpoint_host or "-",
                type(exc).__name__,
            )
            raise RemoteToolRequestError(
                category="protocol",
                safe_endpoint_host=safe_endpoint_host,
            ) from None

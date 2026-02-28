from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
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
            raise ValueError("RemoteTool endpoint_url is empty")

        # SSRF 安全检查
        validate_url_ssrf(url)

        method = (self.http_method or "POST").strip().upper()
        timeout = max(1, int(self.timeout_seconds or 15))

        # 组装 headers（支持模板变量）
        headers: dict[str, str] = {}
        if self.headers:
            rendered_headers = {
                str(k): _render_string_template(str(v), args or {})
                for k, v in self.headers.items()
            }
            headers.update(rendered_headers)

        # 添加认证头（覆盖同名 header）
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
            return self._do_request(req, timeout)

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
        return self._do_request(req, timeout)

    @staticmethod
    def _do_request(req: Request, timeout: int) -> str:
        try:
            opener = build_opener(_SSRFSafeRedirectHandler)
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                return raw.decode("utf-8", errors="ignore")
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            message = f"HTTP {exc.code}: {exc.reason}"
            if body:
                message += f" - {body[:500]}"
            raise RuntimeError(message) from exc
        except URLError as exc:
            raise RuntimeError(f"Connection failed: {exc.reason}") from exc

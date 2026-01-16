from __future__ import annotations

import ipaddress
import json
import logging
import socket
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from app.ai_provider.crypto import decrypt_api_key
from app.assistant_config.models import AssistantTool

logger = logging.getLogger(__name__)

# 允许的 URL scheme
ALLOWED_SCHEMES = {"http", "https"}

# 禁止访问的内网 IP 范围
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # Loopback
    ipaddress.ip_network("10.0.0.0/8"),       # Private Class A
    ipaddress.ip_network("172.16.0.0/12"),    # Private Class B
    ipaddress.ip_network("192.168.0.0/16"),   # Private Class C
    ipaddress.ip_network("169.254.0.0/16"),   # Link-local / Cloud metadata
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 private
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]


class SSRFError(ValueError):
    """SSRF 安全错误"""
    pass


def validate_url_security(url: str) -> None:
    """验证 URL 安全性，防止 SSRF 攻击

    检查项:
    1. URL scheme 必须是 http 或 https
    2. 不能访问内网 IP 地址
    3. 不能访问 localhost
    """
    parsed = urlparse(url)

    # 检查 scheme
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise SSRFError(f"不允许的 URL scheme: {parsed.scheme}，只允许 http/https")

    # 检查 hostname
    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("URL 缺少主机名")

    # 检查 localhost 变体
    if hostname.lower() in ("localhost", "localhost.localdomain"):
        raise SSRFError("不允许访问 localhost")

    # 解析 IP 地址并检查是否为内网
    try:
        # 尝试直接解析为 IP
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # 不是 IP 地址，尝试 DNS 解析
        try:
            resolved = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(resolved)
        except socket.gaierror:
            # DNS 解析失败，允许继续（可能是临时网络问题）
            logger.warning("DNS 解析失败: %s", hostname)
            return

    # 检查是否为内网 IP
    for network in BLOCKED_IP_NETWORKS:
        if ip in network:
            raise SSRFError(f"不允许访问内网地址: {ip}")


@dataclass
class RemoteTool:
    """远程工具 - 兼容 LangChain tool 接口"""

    name: str
    description: str | None
    # 输入参数定义（来自数据库 assistant_tool.input_params）
    input_params: list[dict[str, Any]] | None
    endpoint_url: str
    http_method: str = "POST"
    headers: dict[str, str] | None = None
    auth_header_name: str | None = "Authorization"
    auth_scheme: str | None = "Bearer"
    api_key_encrypted: str | None = None
    timeout_seconds: int = 15
    payload_wrapper: str | None = None

    @classmethod
    def from_model(cls, tool: AssistantTool) -> "RemoteTool":
        return cls(
            name=tool.name,
            description=tool.description,
            input_params=(tool.input_params or None),
            endpoint_url=(tool.endpoint_url or "").strip(),
            http_method=(tool.http_method or "POST").strip().upper(),
            headers=(tool.headers or None),
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
        validate_url_security(url)

        method = (self.http_method or "POST").strip().upper()
        headers: dict[str, str] = {}
        if self.headers:
            headers.update({str(k): str(v) for k, v in self.headers.items()})
        headers.setdefault("content-type", "application/json")

        # 添加认证头
        if self.api_key_encrypted and self.auth_header_name:
            api_key = decrypt_api_key(self.api_key_encrypted)
            if self.auth_scheme:
                headers[self.auth_header_name] = f"{self.auth_scheme} {api_key}"
            else:
                headers[self.auth_header_name] = api_key

        payload: Any = args
        if self.payload_wrapper:
            payload = {self.payload_wrapper: args}

        timeout = max(1, int(self.timeout_seconds or 15))

        if method in ("GET", "DELETE"):
            query = urlencode({k: json.dumps(v, ensure_ascii=False) for k, v in (args or {}).items()})
            final_url = url + ("&" if "?" in url else "?") + query if query else url
            req = Request(final_url, headers=headers, method=method)
            return self._do_request(req, timeout)

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = Request(url, data=data, headers=headers, method=method)
        return self._do_request(req, timeout)

    @staticmethod
    def _do_request(req: Request, timeout: int) -> str:
        try:
            with urlopen(req, timeout=timeout) as resp:
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

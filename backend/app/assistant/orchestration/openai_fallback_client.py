from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterator
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.assistant.openai_compat import build_openai_compat_request_headers


@dataclass(frozen=True)
class OpenAiFallbackConfig:
    api_key: str
    base_url: str
    model: str


class OpenAiFallbackClient:
    def build_api_url(self, base_url: str, endpoint: str) -> str:
        base = (base_url or "").rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        return base + endpoint

    def stream_chat(self, cfg: OpenAiFallbackConfig, messages: list[dict]) -> Iterator[str]:
        url = self.build_api_url(cfg.base_url, "/chat/completions")
        body = {
            "model": cfg.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
        }
        req = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=build_openai_compat_request_headers(cfg.api_key),
            method="POST",
        )
        with urlopen(req, timeout=60) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except Exception:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0] or {}).get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield content

    def call_chat(self, cfg: OpenAiFallbackConfig, messages: list[dict]) -> str | None:
        url = self.build_api_url(cfg.base_url, "/chat/completions")
        body = {
            "model": cfg.model,
            "messages": messages,
            "temperature": 0.7,
        }
        req = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=build_openai_compat_request_headers(cfg.api_key),
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except (URLError, Exception):
            return None

    def parse_chat_content(self, raw: str | None) -> str:
        if not raw:
            return ""
        try:
            payload = json.loads(raw)
            return (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            ) or ""
        except Exception:
            return ""

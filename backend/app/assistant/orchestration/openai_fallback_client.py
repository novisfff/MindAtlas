from __future__ import annotations

import json
import logging
import ssl
from dataclasses import dataclass
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.assistant.openai_compat import build_openai_compat_request_headers

logger = logging.getLogger(__name__)


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

    @staticmethod
    def _wrap_content_as_chat_response(content: str) -> str:
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": content,
                        }
                    }
                ]
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _requires_stream_mode(status_code: int | None, body_preview: str) -> bool:
        if int(status_code or 0) != 400:
            return False
        lowered = str(body_preview or "").lower()
        return "stream must be set to true" in lowered

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
        except HTTPError as exc:
            body_preview = ""
            try:
                body_preview = exc.read().decode("utf-8", errors="ignore")[:1000]
            except Exception:
                body_preview = ""
            logger.warning(
                "openai_fallback call_chat http_error status=%s reason=%s url=%s body=%s",
                getattr(exc, "code", None),
                getattr(exc, "reason", None),
                url,
                body_preview,
            )
            if self._requires_stream_mode(getattr(exc, "code", None), body_preview):
                logger.info("openai_fallback call_chat retry_with_stream url=%s", url)
                try:
                    aggregated = "".join(self.stream_chat(cfg, messages))
                    if aggregated:
                        return self._wrap_content_as_chat_response(aggregated)
                except Exception:
                    logger.exception("openai_fallback call_chat retry_with_stream_failed url=%s", url)
            return None
        except (URLError, TimeoutError, ssl.SSLError, OSError) as exc:
            logger.warning(
                "openai_fallback call_chat transport_error url=%s reason=%s",
                url,
                getattr(exc, "reason", exc),
            )
            return None
        except Exception:
            logger.exception("openai_fallback call_chat unexpected_error url=%s", url)
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

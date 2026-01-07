from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.ai.schemas import AiGenerateRequest, AiGenerateResponse
from app.config import get_settings


@dataclass(frozen=True)
class _OpenAiConfig:
    api_key: str
    base_url: str
    model: str


class AiService:
    def generate(self, request: AiGenerateRequest) -> AiGenerateResponse:
        settings = get_settings()
        if settings.ai_provider.lower() != "openai":
            return AiGenerateResponse(summary=None, suggested_tags=[])

        if not settings.ai_api_key or not settings.ai_api_key.strip():
            return AiGenerateResponse(summary=None, suggested_tags=[])

        cfg = _OpenAiConfig(
            api_key=settings.ai_api_key.strip(),
            base_url=settings.ai_base_url,
            model=settings.ai_model,
        )
        prompt = self._build_prompt(request)
        raw = self._call_openai(cfg, prompt)
        return self._parse_openai_response(raw)

    def _build_prompt(self, request: AiGenerateRequest) -> str:
        return (
            "分析以下内容，生成摘要和标签建议。\n\n"
            f"标题: {request.title}\n"
            f"类型: {request.type_name}\n"
            "内容:\n"
            f"{request.content}\n\n"
            "请以JSON格式返回，包含:\n"
            "1. summary: 一句话摘要（50字以内）\n"
            "2. tags: 3-5个相关标签（数组格式）\n\n"
            "只返回JSON，不要其他内容。"
        )

    def _call_openai(self, cfg: _OpenAiConfig, prompt: str) -> str | None:
        url = cfg.base_url.rstrip("/") + "/chat/completions"
        body = {
            "model": cfg.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
        }

        req = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {cfg.api_key}",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=30) as resp:
                data = resp.read()
        except URLError:
            return None
        except Exception:
            return None

        try:
            return data.decode("utf-8")
        except Exception:
            return None

    def _parse_openai_response(self, raw: str | None) -> AiGenerateResponse:
        if not raw:
            return AiGenerateResponse(summary=None, suggested_tags=[])

        try:
            payload = json.loads(raw)
            content = (
                payload.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
        except Exception:
            return AiGenerateResponse(summary=None, suggested_tags=[])

        result = self._parse_json_from_text(content)
        if not isinstance(result, dict):
            return AiGenerateResponse(summary=None, suggested_tags=[])

        summary = result.get("summary")
        tags = result.get("tags") or result.get("suggestedTags") or []
        if not isinstance(tags, list):
            tags = []
        tags_out: list[str] = [str(t).strip() for t in tags if str(t).strip()]
        return AiGenerateResponse(summary=str(summary) if summary else None, suggested_tags=tags_out)

    def _parse_json_from_text(self, text: str) -> Any:
        text = text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass

        # Best-effort: extract the first JSON object from a longer response.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            return None


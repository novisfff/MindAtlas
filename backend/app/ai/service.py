from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from app.ai.schemas import AiGenerateRequest, AiGenerateResponse
from app.ai_provider.crypto import decrypt_api_key
from app.ai_provider.models import AiProvider
from app.tag.models import Tag


@dataclass(frozen=True)
class _OpenAiConfig:
    api_key: str
    base_url: str
    model: str


class AiService:
    def __init__(self, db: Session):
        self.db = db

    def generate(self, request: AiGenerateRequest) -> AiGenerateResponse:
        provider = self._get_active_provider()
        if not provider:
            return AiGenerateResponse(summary=None, suggested_tags=[])

        try:
            api_key = decrypt_api_key(provider.api_key_encrypted)
        except Exception:
            return AiGenerateResponse(summary=None, suggested_tags=[])

        # Custom tag fetching
        tags = self.db.query(Tag).all()
        tag_names = [t.name for t in tags]

        cfg = _OpenAiConfig(
            api_key=api_key,
            base_url=provider.base_url,
            model=provider.model,
        )
        prompt = self._build_prompt(request, tag_names)
        raw = self._call_openai(cfg, prompt)
        return self._parse_openai_response(raw)

    def _get_active_provider(self) -> AiProvider | None:
        return self.db.query(AiProvider).filter(AiProvider.is_active.is_(True)).first()

    def _build_prompt(self, request: AiGenerateRequest, existing_tags: list[str]) -> str:
        return f"""
    Analyze the following journal entry to provide organized content and suggest relevant tags.

    Input Data:
    - Entry Type: {request.type_name}
    - Title: {request.title}
    - Content (Markdown): {request.content}
    - Available Tags: {", ".join(existing_tags)}

    Instructions:
    1. **Language Detection**: Detect the primary language used in the 'Title' and 'Content'.
    2. **AI Content (Refinement)**: 
       - **Goal**: Structure the original content for clarity without expanding it.
       - **Content Only**: Do NOT repeat the Title or Entry Type. Focus only on the body content.
       - **Strict Constraint**: Do NOT add ANY new information, details, or explanations that are not explicitly in the source.
       - **No Expansion**: If the source text is brief, the output MUST be brief. Do not elaborate or add filler text.
       - **Refinement**: You may fix grammar, formatting, and structure (e.g., grouping related points).
       - **Format**: Can use **Markdown** (headers, bullet points, bold text) to make the content easy to read.
       - **Language**: Maintain the **SAME language** as the detected content.
    3. **Tag Selection**:
       - Suggest 3-5 relevant tags.
       - **CRITICAL**: You MUST prioritize using tags from the 'Available Tags' list if they are relevant.
       - Only create new tags if absolutely necessary and no existing tags are suitable.
       - Tags should be in the same language as the content or the existing tags style.

    Output Format:
    Return ONLY a valid JSON object with no markdown formatting or other text.
    {{
      "ai_content": "The organized and refined content using markdown",
      "tags": ["tag1", "tag2", "tag3"]
    }}
    """

    def _build_api_url(self, base_url: str, endpoint: str) -> str:
        """构建 API URL，自动添加 /v1 前缀（如果需要）"""
        base = base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        return base + endpoint

    def _call_openai(self, cfg: _OpenAiConfig, prompt: str) -> str | None:
        url = self._build_api_url(cfg.base_url, "/chat/completions")
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

        summary = result.get("ai_content") or result.get("summary")
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


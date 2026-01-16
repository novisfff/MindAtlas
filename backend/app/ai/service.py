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
        tags_text = ", ".join(existing_tags[:100]) if existing_tags else "暂无"
        return f"""你是 MindAtlas 的内容整理助手。请帮助用户整理和优化他们的记录内容。

【输入信息】
- 记录类型：{request.type_name}
- 标题：{request.title}
- 原始内容：
{request.content}

【已有标签库】
{tags_text}

【你的任务】

1. **生成摘要 (summary)**
   - 用一段简洁的话概括这条记录的核心内容
   - 长度控制在 50-150 字
   - 便于用户快速了解记录主旨
   - 使用与原文相同的语言

2. **整理内容 (refined_content)**
   - 对原始内容进行格式化和结构优化
   - 可以使用 Markdown 格式（二级标题、列表、加粗等）
   - 禁止使用一级标题（# 开头的行）
   - 修正明显的错别字和语法问题
   - 提炼要点，去除冗余信息
   - 保留原文的核心信息，不要编造新内容
   - 如果原文很简短，整理后也应保持简短
   - 使用与原文相同的语言

3. **推荐标签 (tags)**
   - 推荐 3-5 个相关标签
   - 优先从【已有标签库】中选择匹配的标签
   - 只有在没有合适的已有标签时才创建新标签
   - 标签语言与内容保持一致

【输出格式】
只输出一个 JSON 对象，不要包含任何其他文字或 Markdown 代码块：
{{"summary": "摘要内容", "refined_content": "整理后的内容", "tags": ["标签1", "标签2"]}}
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
            return AiGenerateResponse(summary=None, refined_content=None, suggested_tags=[])

        summary = result.get("summary")
        refined_content = result.get("refined_content") or result.get("ai_content")
        tags = result.get("tags") or result.get("suggestedTags") or []
        if not isinstance(tags, list):
            tags = []
        tags_out: list[str] = [str(t).strip() for t in tags if str(t).strip()]
        return AiGenerateResponse(
            summary=str(summary) if summary else None,
            refined_content=str(refined_content) if refined_content else None,
            suggested_tags=tags_out
        )

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


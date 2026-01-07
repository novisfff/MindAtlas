from __future__ import annotations

from pydantic import Field

from app.common.schemas import CamelModel


class AiGenerateRequest(CamelModel):
    title: str
    content: str
    type_name: str


class AiGenerateResponse(CamelModel):
    summary: str | None = None
    suggested_tags: list[str] = Field(default_factory=list)

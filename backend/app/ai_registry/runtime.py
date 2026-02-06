from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai_provider.crypto import decrypt_api_key
from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel

AiComponent = Literal["assistant", "lightrag"]
AiModelType = Literal["llm", "embedding"]


def _normalize_openai_compat_base_url(base_url: str) -> str:
    value = (base_url or "").strip().rstrip("/")
    if not value:
        return ""
    if not value.endswith("/v1"):
        value += "/v1"
    return value


@dataclass(frozen=True)
class OpenAICompatConfig:
    """OpenAI 兼容配置 (用于运行时解析)"""
    api_key: str
    base_url: str
    model: str
    credential_id: UUID
    model_id: UUID


def resolve_openai_compat_config(
    db: Session,
    *,
    component: AiComponent,
    model_type: AiModelType,
) -> OpenAICompatConfig | None:
    """
    根据组件和模型类型解析 OpenAI 兼容配置。

    Args:
        db: 数据库会话
        component: 组件名称 (assistant / lightrag)
        model_type: 模型类型 (llm / embedding)

    Returns:
        OpenAICompatConfig 或 None (未配置时)
    """
    binding = (
        db.query(AiComponentBinding)
        .filter(AiComponentBinding.component == component)
        .first()
    )
    if not binding:
        return None

    selected_model_id = binding.llm_model_id if model_type == "llm" else binding.embedding_model_id
    if not selected_model_id:
        return None

    model = db.query(AiModel).filter(AiModel.id == selected_model_id).first()
    if not model or (model.model_type or "").strip() != model_type:
        return None

    credential = db.query(AiCredential).filter(AiCredential.id == model.credential_id).first()
    if not credential:
        return None

    try:
        api_key = decrypt_api_key(credential.api_key_encrypted)
    except Exception:
        return None

    model_name = (model.name or "").strip()
    normalized_base_url = _normalize_openai_compat_base_url(credential.base_url)
    normalized_api_key = (api_key or "").strip()
    if not (model_name and normalized_base_url and normalized_api_key):
        return None

    return OpenAICompatConfig(
        api_key=normalized_api_key,
        base_url=normalized_base_url,
        model=model_name,
        credential_id=credential.id,
        model_id=model.id,
    )

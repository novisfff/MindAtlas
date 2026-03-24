from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.ai_provider.crypto import decrypt_api_key
from app.ai_registry.models import AiComponentBinding, AiCredential, AiModel
from app.common.ssrf import normalize_openai_base_url

AiComponent = Literal["assistant", "lightrag", "workflow_copilot"]
AiModelType = Literal["llm", "embedding"]


@dataclass(frozen=True)
class OpenAICompatConfig:
    """OpenAI 兼容配置 (用于运行时解析)"""
    api_key: str
    base_url: str
    model: str
    credential_id: UUID
    model_id: UUID


def _resolve_config_from_model(
    db: Session,
    *,
    model: AiModel | None,
    model_type: AiModelType,
) -> OpenAICompatConfig | None:
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
    normalized_base_url = normalize_openai_base_url(credential.base_url)
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
        component: 组件名称 (assistant / lightrag / workflow_copilot)
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

    return resolve_openai_compat_config_by_model_id(
        db,
        model_id=selected_model_id,
        model_type=model_type,
    )


def resolve_openai_compat_config_by_model_id(
    db: Session,
    *,
    model_id: UUID | str,
    model_type: AiModelType = "llm",
) -> OpenAICompatConfig | None:
    """根据模型 ID 解析 OpenAI 兼容配置。"""
    parsed_model_id: UUID | None = None
    if isinstance(model_id, UUID):
        parsed_model_id = model_id
    elif isinstance(model_id, str):
        text = model_id.strip()
        if not text:
            return None
        try:
            parsed_model_id = UUID(text)
        except Exception:
            return None

    if parsed_model_id is None:
        return None

    model = db.query(AiModel).filter(AiModel.id == parsed_model_id).first()
    return _resolve_config_from_model(
        db,
        model=model,
        model_type=model_type,
    )

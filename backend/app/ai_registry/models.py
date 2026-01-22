from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class AiCredential(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """AI 服务商凭据 (API Key + Base URL)"""
    __tablename__ = "ai_credential"

    name = Column(String(128), nullable=False, unique=True, index=True)
    base_url = Column(String(2048), nullable=False)
    api_key_encrypted = Column(Text, nullable=False)
    api_key_hint = Column(String(64), nullable=False)

    models = relationship(
        "AiModel",
        back_populates="credential",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AiModel(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """AI 模型配置 (关联到凭据, 区分 LLM/Embedding 类型)"""
    __tablename__ = "ai_model"

    credential_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_credential.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    # llm | embedding
    model_type = Column(String(32), nullable=False)

    credential = relationship("AiCredential", back_populates="models")

    __table_args__ = (
        CheckConstraint("model_type IN ('llm','embedding')", name="ck_ai_model_type"),
        Index(
            "uq_ai_model_credential_name_type",
            "credential_id",
            "name",
            "model_type",
            unique=True,
        ),
        Index("idx_ai_model_credential_type", "credential_id", "model_type"),
    )


class AiComponentBinding(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """AI 组件绑定 (系统助手/LightRAG 各自绑定的 LLM/Embedding 模型)"""
    __tablename__ = "ai_component_binding"

    # assistant | lightrag
    component = Column(String(32), nullable=False, unique=True, index=True)

    llm_model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_model.id", ondelete="SET NULL"),
        nullable=True,
    )
    embedding_model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_model.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint("component IN ('assistant','lightrag')", name="ck_ai_component_binding_component"),
    )

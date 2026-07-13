from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.common.time import utcnow
from app.database import Base


class AiCredential(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """AI 服务商凭据 (API Key + Base URL)"""
    __tablename__ = "ai_credential"

    name = Column(String(128), nullable=False, unique=True, index=True)
    base_url = Column(String(2048), nullable=False)
    api_key_encrypted = Column(Text, nullable=False)
    api_key_hint = Column(String(64), nullable=False)
    # Execution-sensitive credential-slot revision (Plan 01 Decision 8).
    runtime_revision = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    models = relationship(
        "AiModel",
        back_populates="credential",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AiModelCapabilityProbe(UuidPrimaryKeyMixin, Base):
    """Immutable live model capability probe evidence (Plan 03 Task 8).

    Rows are append-only history. There is no ``updated_at`` and the service
    exposes no update/delete. Deleting the owning model cascades history.
    """

    __tablename__ = "ai_model_capability_probe"

    model_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ai_model.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    probe_contract_version = Column(Integer, nullable=False)
    adapter_key = Column(String(64), nullable=False)
    adapter_revision = Column(String(128), nullable=False)
    model_config_digest = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False)
    capabilities = Column(JSONB, nullable=False)
    probe_digest = Column(String(64), nullable=False)
    safe_error_code = Column(String(64), nullable=True)
    safe_error_summary = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    model = relationship(
        "AiModel",
        back_populates="capability_probes",
        foreign_keys=[model_id],
    )

    __table_args__ = (
        CheckConstraint(
            "probe_contract_version > 0",
            name="ck_ai_model_capability_probe_contract_version_positive",
        ),
        CheckConstraint(
            "status IN ('passed','partial','failed')",
            name="ck_ai_model_capability_probe_status",
        ),
        CheckConstraint(
            "length(model_config_digest) = 64",
            name="ck_ai_model_capability_probe_model_config_digest_len",
        ),
        CheckConstraint(
            "length(probe_digest) = 64",
            name="ck_ai_model_capability_probe_probe_digest_len",
        ),
        CheckConstraint(
            "length(adapter_key) >= 1 AND length(adapter_key) <= 64",
            name="ck_ai_model_capability_probe_adapter_key_len",
        ),
        CheckConstraint(
            "length(adapter_revision) >= 1 AND length(adapter_revision) <= 128",
            name="ck_ai_model_capability_probe_adapter_revision_len",
        ),
        CheckConstraint(
            "safe_error_code IS NULL OR (length(safe_error_code) >= 1 AND length(safe_error_code) <= 64)",
            name="ck_ai_model_capability_probe_safe_error_code_len",
        ),
        CheckConstraint(
            "safe_error_summary IS NULL OR (length(safe_error_summary) >= 1 AND length(safe_error_summary) <= 200)",
            name="ck_ai_model_capability_probe_safe_error_summary_len",
        ),
        # PostgreSQL migration adds jsonb_typeof(object) CHECK; SQLite create_all
        # relies on service-layer Pydantic validation only.
        Index(
            "idx_ai_model_capability_probe_model_created_id",
            "model_id",
            "created_at",
            "id",
        ),
        Index("idx_ai_model_capability_probe_status", "status"),
        Index("idx_ai_model_capability_probe_config_digest", "model_config_digest"),
        Index("idx_ai_model_capability_probe_probe_digest", "probe_digest"),
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
    # Execution-sensitive model revision (Plan 01 Decision 8).
    runtime_revision = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    # Plan 03 current probe pointer (nullable; SET NULL on probe delete).
    current_capability_probe_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "ai_model_capability_probe.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_ai_model_current_capability_probe_id",
        ),
        nullable=True,
        index=True,
    )

    credential = relationship("AiCredential", back_populates="models")
    capability_probes = relationship(
        "AiModelCapabilityProbe",
        back_populates="model",
        foreign_keys="AiModelCapabilityProbe.model_id",
        passive_deletes=True,
    )
    current_capability_probe = relationship(
        "AiModelCapabilityProbe",
        foreign_keys=[current_capability_probe_id],
        post_update=True,
        uselist=False,
    )

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
    """AI 组件绑定 (系统助手/LightRAG/Workflow Copilot 绑定的默认模型)"""
    __tablename__ = "ai_component_binding"

    # assistant | lightrag | workflow_copilot
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
        CheckConstraint(
            "component IN ('assistant','lightrag','workflow_copilot')",
            name="ck_ai_component_binding_component",
        ),
    )

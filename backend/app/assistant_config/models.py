from __future__ import annotations

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class AssistantTool(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """AI 助手工具配置"""
    __tablename__ = "assistant_tool"

    name = Column(String(128), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)

    # local | remote
    kind = Column(String(32), nullable=False, default="local")
    is_system = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)

    # 输入参数定义 (JSON array)
    input_params = Column(JSON, nullable=True)

    # Remote tool config (only when kind == "remote")
    endpoint_url = Column(String(2048), nullable=True)
    http_method = Column(String(10), nullable=True)
    headers = Column(JSON, nullable=True)

    # Query params (JSON object)
    query_params = Column(JSON, nullable=True)

    # Request body config
    body_type = Column(String(32), nullable=True)  # none|form-data|x-www-form-urlencoded|json|xml|raw
    body_content = Column(Text, nullable=True)

    # Auth config
    auth_type = Column(String(32), nullable=True)  # none|bearer|basic|api-key
    auth_header_name = Column(String(128), nullable=True)
    auth_scheme = Column(String(32), nullable=True)
    api_key_encrypted = Column(Text, nullable=True)
    api_key_hint = Column(String(64), nullable=True)

    timeout_seconds = Column(Integer, nullable=True)
    payload_wrapper = Column(String(64), nullable=True)


class AssistantSkill(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """AI 助手技能配置"""
    __tablename__ = "assistant_skill"

    name = Column(String(128), nullable=False, unique=True, index=True)
    description = Column(String(512), nullable=False, default="")
    intent_examples = Column(JSON, nullable=True)
    tools = Column(JSON, nullable=True)

    # 执行模式: steps (步骤模式) | agent (Agent 模式)
    mode = Column(String(32), nullable=False, default="steps")
    # Agent 模式的系统提示词
    system_prompt = Column(Text, nullable=True)
    # 知识库配置 (JSON)
    kb_config = Column(JSON, nullable=True)

    is_system = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)

    steps = relationship(
        "AssistantSkillStep",
        back_populates="skill",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AssistantSkillStep.step_order.asc()",
    )


class AssistantSkillStep(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """AI 助手技能步骤"""
    __tablename__ = "assistant_skill_step"

    skill_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_order = Column(Integer, nullable=False)
    type = Column(String(32), nullable=False)  # analysis | tool | summary
    instruction = Column(Text, nullable=True)
    tool_name = Column(String(128), nullable=True)
    args_from = Column(String(32), nullable=True)  # context | previous | custom
    args_template = Column(Text, nullable=True)  # custom 模式的模板
    # analysis 输出配置：text | json
    output_mode = Column(String(16), nullable=True)
    # analysis 输出字段白名单（仅 output_mode=json 时生效）
    output_fields = Column(JSON, nullable=True)
    # 是否将该步骤信息提供给 summary（默认 True）
    include_in_summary = Column(Boolean, nullable=False, default=True)
    # Step 级别知识库配置 (JSON)
    kb_config = Column(JSON, nullable=True)

    skill = relationship("AssistantSkill", back_populates="steps")

    __table_args__ = (
        Index("uq_assistant_skill_step_skill_order", "skill_id", "step_order", unique=True),
    )

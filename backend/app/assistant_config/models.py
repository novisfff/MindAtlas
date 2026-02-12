from __future__ import annotations

from sqlalchemy import Boolean, Column, Float, ForeignKey, Index, Integer, JSON, String, Text
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

    # 执行模式固定为 langgraph（保留列用于显式约束）
    mode = Column(String(32), nullable=False, default="langgraph")
    # LangGraph 子图模式: agent_loop | workflow_dag
    langgraph_pattern = Column(String(32), nullable=True)
    # agent_loop 模式的系统提示词
    system_prompt = Column(Text, nullable=True)
    # 知识库配置 (JSON)
    kb_config = Column(JSON, nullable=True)
    # 工作流版本号 (workflow_dag 模式)
    workflow_version = Column(Integer, nullable=False, default=1)
    # 画布视口状态 (JSON: {x, y, zoom})
    workflow_viewport = Column(JSON, nullable=True)

    is_system = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)

    nodes = relationship(
        "AssistantSkillNode",
        back_populates="skill",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    edges = relationship(
        "AssistantSkillEdge",
        back_populates="skill",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

class AssistantSkillNode(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """工作流 DAG 节点"""
    __tablename__ = "assistant_skill_node"

    skill_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id = Column(String(128), nullable=False)
    node_type = Column(String(32), nullable=False)
    label = Column(String(256), nullable=False, default="")
    position_x = Column(Float, nullable=False, default=0.0)
    position_y = Column(Float, nullable=False, default=0.0)
    config = Column(JSON, nullable=True)

    skill = relationship("AssistantSkill", back_populates="nodes")

    __table_args__ = (
        Index("uq_skill_node_id", "skill_id", "node_id", unique=True),
    )


class AssistantSkillEdge(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """工作流 DAG 边"""
    __tablename__ = "assistant_skill_edge"

    skill_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    edge_id = Column(String(128), nullable=False)
    source_node_id = Column(String(128), nullable=False)
    target_node_id = Column(String(128), nullable=False)
    source_handle = Column(String(64), nullable=False, default="output")
    target_handle = Column(String(64), nullable=False, default="input")
    condition_type = Column(String(32), nullable=True)
    condition_expr = Column(JSON, nullable=True)
    label = Column(String(256), nullable=True)

    skill = relationship("AssistantSkill", back_populates="edges")

    __table_args__ = (
        Index(
            "uq_skill_edge",
            "skill_id", "source_node_id", "source_handle",
            "target_node_id", "target_handle",
            unique=True,
        ),
    )

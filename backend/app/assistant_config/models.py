from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Column, Float, ForeignKey, Index, Integer, JSON, String, Text
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
    # 新绑定模型：Skill 必须绑定 workflow 或 agent_profile 之一（迁移后由 DB 约束保证）
    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    agent_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_agent_profile.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    is_system = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)

    workflow = relationship("AssistantWorkflow", back_populates="skills")
    agent_profile = relationship("AssistantAgentProfile", back_populates="skills")

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

    __table_args__ = (
        CheckConstraint(
            "(workflow_id IS NOT NULL AND agent_profile_id IS NULL) OR "
            "(workflow_id IS NULL AND agent_profile_id IS NOT NULL)",
            name="ck_assistant_skill_single_target_binding",
        ),
    )


class AssistantWorkflow(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """可复用 Workflow 执行体"""
    __tablename__ = "assistant_workflow"

    name = Column(String(128), nullable=False, unique=True, index=True)
    description = Column(String(512), nullable=False, default="")
    workflow_version = Column(Integer, nullable=False, default=1)
    workflow_viewport = Column(JSON, nullable=True)
    draft_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow_version.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    published_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow_version.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_system = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)

    nodes = relationship(
        "AssistantWorkflowNode",
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    edges = relationship(
        "AssistantWorkflowEdge",
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    versions = relationship(
        "AssistantWorkflowVersion",
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="AssistantWorkflowVersion.workflow_id",
    )
    skills = relationship("AssistantSkill", back_populates="workflow")


class AssistantWorkflowNode(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """独立 Workflow DAG 节点"""
    __tablename__ = "assistant_workflow_node"

    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id = Column(String(128), nullable=False)
    node_type = Column(String(32), nullable=False)
    label = Column(String(256), nullable=False, default="")
    position_x = Column(Float, nullable=False, default=0.0)
    position_y = Column(Float, nullable=False, default=0.0)
    config = Column(JSON, nullable=True)

    workflow = relationship("AssistantWorkflow", back_populates="nodes")

    __table_args__ = (
        Index("uq_workflow_node_id", "workflow_id", "node_id", unique=True),
    )


class AssistantWorkflowEdge(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """独立 Workflow DAG 边"""
    __tablename__ = "assistant_workflow_edge"

    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow.id", ondelete="CASCADE"),
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

    workflow = relationship("AssistantWorkflow", back_populates="edges")

    __table_args__ = (
        Index(
            "uq_workflow_edge",
            "workflow_id", "source_node_id", "source_handle",
            "target_node_id", "target_handle",
            unique=True,
        ),
    )


class AssistantWorkflowVersion(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Workflow 历史版本快照。"""
    __tablename__ = "assistant_workflow_version"

    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_no = Column(Integer, nullable=False)
    version_name = Column(String(255), nullable=False)
    version_source = Column(String(32), nullable=False)  # save | publish
    snapshot = Column(JSON, nullable=False)

    workflow = relationship(
        "AssistantWorkflow",
        back_populates="versions",
        foreign_keys=[workflow_id],
    )

    __table_args__ = (
        CheckConstraint("version_source IN ('save','publish')", name="ck_assistant_workflow_version_source"),
        Index("uq_assistant_workflow_version_seq", "workflow_id", "sequence_no", unique=True),
        Index("ix_assistant_workflow_version_workflow_created", "workflow_id", "created_at"),
    )


class AssistantAgentProfile(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """可复用 Agent 执行体配置"""
    __tablename__ = "assistant_agent_profile"

    name = Column(String(128), nullable=False, unique=True, index=True)
    description = Column(String(512), nullable=False, default="")
    system_prompt = Column(Text, nullable=True)
    kb_config = Column(JSON, nullable=True)
    tools = Column(JSON, nullable=True)
    draft_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_agent_profile_version.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    published_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_agent_profile_version.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_system = Column(Boolean, nullable=False, default=False)
    enabled = Column(Boolean, nullable=False, default=True)

    skills = relationship("AssistantSkill", back_populates="agent_profile")
    versions = relationship(
        "AssistantAgentProfileVersion",
        back_populates="agent_profile",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="AssistantAgentProfileVersion.agent_profile_id",
    )


class AssistantAgentProfileVersion(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Agent 配置历史版本快照。"""
    __tablename__ = "assistant_agent_profile_version"

    agent_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_agent_profile.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_no = Column(Integer, nullable=False)
    version_name = Column(String(255), nullable=False)
    version_source = Column(String(32), nullable=False)  # save | publish
    snapshot = Column(JSON, nullable=False)

    agent_profile = relationship(
        "AssistantAgentProfile",
        back_populates="versions",
        foreign_keys=[agent_profile_id],
    )

    __table_args__ = (
        CheckConstraint("version_source IN ('save','publish')", name="ck_assistant_agent_profile_version_source"),
        Index("uq_assistant_agent_profile_version_seq", "agent_profile_id", "sequence_no", unique=True),
        Index("ix_assistant_agent_profile_version_agent_created", "agent_profile_id", "created_at"),
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

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


@dataclass
class AssistantWorkflowNode:
    node_id: str
    node_type: str
    label: str = ""
    position_x: float = 0.0
    position_y: float = 0.0
    config: dict | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class AssistantWorkflowEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    source_handle: str = "output"
    target_handle: str = "input"
    condition_type: str | None = None
    condition_expr: dict | None = None
    label: str | None = None
    id: uuid.UUID | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


AssistantSkillNode = AssistantWorkflowNode
AssistantSkillEdge = AssistantWorkflowEdge


def _graph_timestamp(owner: TimestampMixin) -> datetime:
    return owner.updated_at or owner.created_at or datetime.utcnow()


def _workflow_nodes_from_snapshot(*, workflow_id: uuid.UUID, snapshot: dict | None, ts: datetime) -> list[AssistantWorkflowNode]:
    payload = snapshot if isinstance(snapshot, dict) else {}
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [
        AssistantWorkflowNode(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"{workflow_id}:node:{str(item.get('node_id') or '')}"),
            node_id=str(item.get("node_id") or ""),
            node_type=str(item.get("node_type") or ""),
            label=str(item.get("label") or ""),
            position_x=float(item.get("position_x") or 0.0),
            position_y=float(item.get("position_y") or 0.0),
            config=item.get("config") if isinstance(item.get("config"), dict) else {},
            created_at=ts,
            updated_at=ts,
        )
        for item in nodes
        if isinstance(item, dict)
    ]


def _workflow_edges_from_snapshot(*, workflow_id: uuid.UUID, snapshot: dict | None, ts: datetime) -> list[AssistantWorkflowEdge]:
    payload = snapshot if isinstance(snapshot, dict) else {}
    edges = payload.get("edges")
    if not isinstance(edges, list):
        return []
    return [
        AssistantWorkflowEdge(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"{workflow_id}:edge:{str(item.get('edge_id') or '')}"),
            edge_id=str(item.get("edge_id") or ""),
            source_node_id=str(item.get("source_node_id") or ""),
            target_node_id=str(item.get("target_node_id") or ""),
            source_handle=str(item.get("source_handle") or "output"),
            target_handle=str(item.get("target_handle") or "input"),
            condition_type=str(item.get("condition_type")) if item.get("condition_type") is not None else None,
            condition_expr=item.get("condition_expr") if isinstance(item.get("condition_expr"), dict) else None,
            label=str(item.get("label")) if item.get("label") is not None else None,
            created_at=ts,
            updated_at=ts,
        )
        for item in edges
        if isinstance(item, dict)
    ]


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

    # Execution-sensitive config revision (Plan 01). Name/description-only edits do not advance it.
    config_revision = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    __table_args__ = (
        CheckConstraint(
            "config_revision > 0",
            name="ck_assistant_tool_config_revision_positive",
        ),
    )



class AssistantTargetFolder(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Folder for organizing reusable workflow and agent targets."""

    __tablename__ = "assistant_target_folder"

    name = Column(String(128), nullable=False, index=True)
    description = Column(
        String(512), nullable=False, default="", server_default=text("''")
    )
    parent_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_target_folder.id",
            ondelete="SET NULL",
            name="fk_assistant_target_folder_parent_id",
        ),
        nullable=True,
        index=True,
    )
    color_token = Column(
        String(32), nullable=False, default="slate", server_default=text("'slate'")
    )
    icon_key = Column(
        String(32), nullable=False, default="folder", server_default=text("'folder'")
    )

    parent = relationship(
        "AssistantTargetFolder",
        remote_side="AssistantTargetFolder.id",
        back_populates="children",
    )
    children = relationship(
        "AssistantTargetFolder",
        back_populates="parent",
        foreign_keys="AssistantTargetFolder.parent_id",
    )
    workflows = relationship("AssistantWorkflow", back_populates="folder")
    agent_profiles = relationship("AssistantAgentProfile", back_populates="folder")

    __table_args__ = (
        Index("ix_assistant_target_folder_parent_name", "parent_id", "name"),
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
        ForeignKey(
            "assistant_workflow_version.id",
            ondelete="SET NULL",
            name="fk_assistant_workflow_draft_version",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    published_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_workflow_version.id",
            ondelete="SET NULL",
            name="fk_assistant_workflow_published_version",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    is_system = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    enabled = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    folder_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_target_folder.id",
            ondelete="SET NULL",
            name="fk_assistant_workflow_folder_id",
        ),
        nullable=True,
        index=True,
    )
    folder = relationship("AssistantTargetFolder", back_populates="workflows")
    draft_version = relationship(
        "AssistantWorkflowVersion",
        foreign_keys=[draft_version_id],
        uselist=False,
        post_update=True,
    )
    published_version = relationship(
        "AssistantWorkflowVersion",
        foreign_keys=[published_version_id],
        uselist=False,
        post_update=True,
    )
    versions = relationship(
        "AssistantWorkflowVersion",
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="AssistantWorkflowVersion.workflow_id",
    )
    system_behavior_bindings = relationship("AssistantSystemBehaviorBinding", back_populates="workflow")

    @property
    def graph_snapshot(self) -> dict:
        draft_version = getattr(self, "draft_version", None)
        if (
            draft_version is not None
            and getattr(draft_version, "id", None) == getattr(self, "draft_version_id", None)
            and isinstance(getattr(draft_version, "snapshot", None), dict)
        ):
            return dict(draft_version.snapshot)
        published_version = getattr(self, "published_version", None)
        if (
            published_version is not None
            and getattr(published_version, "id", None) == getattr(self, "published_version_id", None)
            and isinstance(getattr(published_version, "snapshot", None), dict)
        ):
            return dict(published_version.snapshot)
        return {}

    @property
    def nodes(self) -> list[AssistantWorkflowNode]:
        return _workflow_nodes_from_snapshot(
            workflow_id=self.id,
            snapshot=self.graph_snapshot,
            ts=_graph_timestamp(self),
        )

    @property
    def edges(self) -> list[AssistantWorkflowEdge]:
        return _workflow_edges_from_snapshot(
            workflow_id=self.id,
            snapshot=self.graph_snapshot,
            ts=_graph_timestamp(self),
        )


class AssistantWorkflowVersion(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Workflow 历史版本快照。"""
    __tablename__ = "assistant_workflow_version"

    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow.id", ondelete="CASCADE"),
        nullable=False,
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
        ForeignKey(
            "assistant_agent_profile_version.id",
            ondelete="SET NULL",
            name="fk_assistant_agent_profile_draft_version",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    published_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_agent_profile_version.id",
            ondelete="SET NULL",
            name="fk_assistant_agent_profile_published_version",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    is_system = Column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    enabled = Column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    folder_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_target_folder.id",
            ondelete="SET NULL",
            name="fk_assistant_agent_profile_folder_id",
        ),
        nullable=True,
        index=True,
    )
    folder = relationship("AssistantTargetFolder", back_populates="agent_profiles")

    versions = relationship(
        "AssistantAgentProfileVersion",
        back_populates="agent_profile",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="AssistantAgentProfileVersion.agent_profile_id",
    )
    system_behavior_bindings = relationship("AssistantSystemBehaviorBinding", back_populates="agent_profile")


class AssistantAgentProfileVersion(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Agent 配置历史版本快照。"""
    __tablename__ = "assistant_agent_profile_version"

    agent_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_agent_profile.id", ondelete="CASCADE"),
        nullable=False,
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


class AssistantSystemBehaviorBinding(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Binding from a built-in system AI behavior to a workflow or agent target."""

    __tablename__ = "assistant_system_behavior_binding"

    behavior_key = Column(String(128), nullable=False, unique=True, index=True)
    target_type = Column(String(32), nullable=False)
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

    workflow = relationship("AssistantWorkflow", back_populates="system_behavior_bindings")
    agent_profile = relationship("AssistantAgentProfile", back_populates="system_behavior_bindings")

    __table_args__ = (
        CheckConstraint(
            "target_type IN ('workflow','agent')",
            name="ck_assistant_system_behavior_binding_target_type",
        ),
        CheckConstraint(
            "(workflow_id IS NOT NULL AND agent_profile_id IS NULL AND target_type = 'workflow') OR "
            "(workflow_id IS NULL AND agent_profile_id IS NOT NULL AND target_type = 'agent')",
            name="ck_assistant_system_behavior_binding_single_target",
        ),
    )

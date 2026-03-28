from __future__ import annotations

from sqlalchemy import Boolean, CheckConstraint, Column, Index, JSON, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.database import Base


class OpenClawCapabilityItem(UuidPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "openclaw_capability_item"

    capability_key = Column(String(128), nullable=False, unique=True, index=True)
    tool_name = Column(String(128), nullable=False, unique=True, index=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=False, default="")

    source_type = Column(String(32), nullable=False)
    system_capability_key = Column(String(128), nullable=True)
    source_tool_name = Column(String(128), nullable=True)
    tool_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_tool.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_agent_profile.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    enabled = Column(Boolean, nullable=False, default=True)
    is_system_preset = Column(Boolean, nullable=False, default=False)
    input_schema_json = Column(JSON, nullable=False)
    output_schema_json = Column(JSON, nullable=False)
    input_summary = Column(Text, nullable=False, default="")
    output_summary = Column(Text, nullable=False, default="")
    tool_response_mode = Column(String(32), nullable=False, default="json_schema")

    tool = relationship("AssistantTool")
    workflow = relationship("AssistantWorkflow")
    agent_profile = relationship("AssistantAgentProfile")

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('system_adapter','tool','workflow','agent')",
            name="ck_openclaw_capability_item_source_type",
        ),
        CheckConstraint(
            "tool_response_mode IN ('json_schema','text_field')",
            name="ck_openclaw_capability_item_tool_response_mode",
        ),
        CheckConstraint(
            "("
            "source_type = 'system_adapter' "
            "AND system_capability_key IS NOT NULL "
            "AND source_tool_name IS NULL "
            "AND tool_id IS NULL "
            "AND workflow_id IS NULL "
            "AND agent_profile_id IS NULL "
            "AND is_system_preset = true"
            ") OR ("
            "source_type = 'tool' "
            "AND system_capability_key IS NULL "
            "AND workflow_id IS NULL "
            "AND agent_profile_id IS NULL "
            "AND is_system_preset = false "
            "AND (tool_id IS NOT NULL OR source_tool_name IS NOT NULL)"
            ") OR ("
            "source_type = 'workflow' "
            "AND source_tool_name IS NULL "
            "AND tool_id IS NULL "
            "AND workflow_id IS NOT NULL "
            "AND agent_profile_id IS NULL "
            "AND ("
            "(is_system_preset = true AND system_capability_key IS NOT NULL) "
            "OR (is_system_preset = false AND system_capability_key IS NULL)"
            ")"
            ") OR ("
            "source_type = 'agent' "
            "AND system_capability_key IS NULL "
            "AND source_tool_name IS NULL "
            "AND tool_id IS NULL "
            "AND workflow_id IS NULL "
            "AND agent_profile_id IS NOT NULL "
            "AND is_system_preset = false"
            ")",
            name="ck_openclaw_capability_item_single_source",
        ),
        Index("ix_openclaw_capability_item_system_capability_key", "system_capability_key"),
    )

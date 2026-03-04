from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.models import TimestampMixin, UuidPrimaryKeyMixin
from app.common.time import utcnow
from app.database import Base


class Conversation(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """对话会话"""
    __tablename__ = "assistant_conversation"

    title = Column(String(200), nullable=True)
    summary = Column(Text, nullable=True)
    is_archived = Column(Boolean, nullable=False, default=False)
    last_message_at = Column(DateTime(timezone=True), nullable=True, default=utcnow)

    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at.asc()",
    )
    chat_runs = relationship(
        "AssistantChatRun",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AssistantChatRun.created_at.asc()",
    )
    l1_memory = relationship(
        "AssistantConversationL1Memory",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    l2_memories = relationship(
        "AssistantConversationSkillL2Memory",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Message(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """对话消息"""
    __tablename__ = "assistant_message"

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_conversation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(20), nullable=False)  # user / assistant / system / tool
    content = Column(Text, nullable=False, default="")
    tool_calls = Column(JSON, nullable=True)
    tool_results = Column(JSON, nullable=True)
    skill_calls = Column(JSON, nullable=True)  # Skill 调用记录
    analysis = Column(JSON, nullable=True)  # 分析过程记录

    conversation = relationship("Conversation", back_populates="messages")


class AssistantChatRun(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Assistant 对话运行记录（后台执行生命周期）。"""

    __tablename__ = "assistant_chat_run"

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_conversation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_message.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assistant_message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_message.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    status = Column(String(32), nullable=False, default="queued", index=True)
    error_message = Column(Text, nullable=True)
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    last_event_seq = Column(Integer, nullable=False, default=0)
    checkpoint_seq = Column(Integer, nullable=False, default=0)

    conversation = relationship("Conversation", back_populates="chat_runs")
    events = relationship(
        "AssistantChatRunEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AssistantChatRunEvent.seq.asc()",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','waiting_approval','cancelling','completed','failed','cancelled')",
            name="ck_assistant_chat_run_status",
        ),
        Index("ix_assistant_chat_run_conversation_status", "conversation_id", "status"),
    )


class AssistantChatRunEvent(Base):
    """Assistant 对话运行事件日志（用于回放/追流）。"""

    __tablename__ = "assistant_chat_run_event"

    # BigInteger on PostgreSQL, Integer on sqlite to preserve autoincrement in unit tests.
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq = Column(Integer, nullable=False)
    event_name = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    run = relationship("AssistantChatRun", back_populates="events")

    __table_args__ = (
        Index("ix_assistant_chat_run_event_run_seq", "run_id", "seq", unique=True),
    )


class AssistantConversationL1Memory(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Conversation-level L1 incremental summary memory."""

    __tablename__ = "assistant_conversation_l1_memory"

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_conversation.id", ondelete="CASCADE"),
        nullable=False,
    )
    summary_text = Column(Text, nullable=False, default="")

    conversation = relationship("Conversation", back_populates="l1_memory")

    __table_args__ = (
        Index("ix_assistant_l1_memory_conversation_id", "conversation_id", unique=True),
    )


class AssistantConversationSkillL2Memory(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Conversation+skill scoped L2 facts memory."""

    __tablename__ = "assistant_conversation_skill_l2_memory"

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_conversation.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_name = Column(String(100), nullable=False)
    facts = Column(JSON, nullable=False, default=list)
    version = Column(Integer, nullable=False, default=1)

    conversation = relationship("Conversation", back_populates="l2_memories")

    __table_args__ = (
        Index(
            "ix_assistant_l2_memory_conversation_skill",
            "conversation_id",
            "skill_name",
            unique=True,
        ),
    )

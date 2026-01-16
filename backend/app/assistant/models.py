from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, JSON, String, Text
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

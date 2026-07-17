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
    text,
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
    workflow_call_memories = relationship(
        "AssistantConversationWorkflowCallMemory",
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
    """Assistant 对话运行记录（后台执行生命周期）。

    Plan 06 adds durable Main Agent fields. ``runtime_kind`` is immutable after
    creation (service-enforced; DB default ``legacy`` for backfill).
    """

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

    # Plan 06 durable foundation
    runtime_kind = Column(
        String(32),
        nullable=False,
        default="legacy",
        server_default=text("'legacy'"),
    )
    runtime_contract_version = Column(Integer, nullable=True)
    required_app_build_revision = Column(String(160), nullable=True)
    state_revision = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    current_manifest_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_run_manifest_revision.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_assistant_chat_run_current_manifest_revision_id",
        ),
        nullable=True,
    )
    current_policy_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_run_policy_revision.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_assistant_chat_run_current_policy_revision_id",
        ),
        nullable=True,
    )
    current_checkpoint_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_run_checkpoint.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_assistant_chat_run_current_checkpoint_id",
        ),
        nullable=True,
    )
    current_budget_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_run_budget_revision.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_assistant_chat_run_current_budget_revision_id",
        ),
        nullable=True,
    )
    current_obligation_revision_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_run_obligation_revision.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_assistant_chat_run_current_obligation_revision_id",
        ),
        nullable=True,
    )
    lease_owner = Column(String(160), nullable=True)
    lease_generation = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    recovery_count = Column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    deadline_at = Column(DateTime(timezone=True), nullable=True)
    failure_code = Column(String(64), nullable=True)
    memory_commit_status = Column(
        String(32),
        nullable=False,
        default="not_applicable",
        server_default=text("'not_applicable'"),
    )
    memory_committed_at = Column(DateTime(timezone=True), nullable=True)
    # Plan 08 capability ledger admission mode. Nullable for runtime_kind=legacy.
    # Main Agent Runs created before Plan 08 are backfilled to legacy_read_only.
    capability_ledger_mode = Column(String(32), nullable=True)

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
            "status IN ("
            "'queued','running','recovering','waiting_approval','waiting_input',"
            "'cancelling','needs_reconciliation','completed','failed','cancelled'"
            ")",
            name="ck_assistant_chat_run_status",
        ),
        CheckConstraint(
            "runtime_kind IN ('legacy','main_agent')",
            name="ck_assistant_chat_run_runtime_kind",
        ),
        CheckConstraint(
            "state_revision >= 0",
            name="ck_assistant_chat_run_state_revision",
        ),
        CheckConstraint(
            "lease_generation >= 0",
            name="ck_assistant_chat_run_lease_generation",
        ),
        CheckConstraint(
            "recovery_count >= 0",
            name="ck_assistant_chat_run_recovery_count",
        ),
        CheckConstraint(
            "runtime_contract_version IS NULL OR runtime_contract_version > 0",
            name="ck_assistant_chat_run_runtime_contract_version",
        ),
        CheckConstraint(
            "memory_commit_status IN ('not_applicable','pending','committed','failed')",
            name="ck_assistant_chat_run_memory_commit_status",
        ),
        CheckConstraint(
            "capability_ledger_mode IS NULL OR capability_ledger_mode IN ("
            "'legacy_read_only','enforced'"
            ")",
            name="ck_assistant_chat_run_capability_ledger_mode",
        ),
        # Main Agent rows require contract version + app build revision.
        # capability_ledger_mode is frozen for main_agent after Plan 08 backfill;
        # legacy runtime always leaves it null. ORM allows null main_agent only
        # for pre-backfill test fixtures; migration enforces non-null after upgrade.
        CheckConstraint(
            "("
            "  runtime_kind = 'legacy'"
            "  AND runtime_contract_version IS NULL"
            "  AND capability_ledger_mode IS NULL"
            ") OR ("
            "  runtime_kind = 'main_agent'"
            "  AND runtime_contract_version IS NOT NULL"
            "  AND required_app_build_revision IS NOT NULL"
            "  AND ("
            "    capability_ledger_mode IS NULL"
            "    OR capability_ledger_mode IN ('legacy_read_only','enforced')"
            "  )"
            ")",
            name="ck_assistant_chat_run_runtime_kind_shape",
        ),
        Index("ix_assistant_chat_run_conversation_status", "conversation_id", "status"),
        Index(
            "uq_assistant_chat_run_active_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "status IN ("
                "'queued','running','recovering','waiting_approval','waiting_input',"
                "'cancelling','needs_reconciliation'"
                ")"
            ),
            sqlite_where=text(
                "status IN ("
                "'queued','running','recovering','waiting_approval','waiting_input',"
                "'cancelling','needs_reconciliation'"
                ")"
            ),
        ),
        Index(
            "ix_assistant_chat_run_lease_claim",
            "status",
            "next_attempt_at",
            "created_at",
        ),
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

    # Plan 06 additive event columns
    event_key = Column(String(255), nullable=True)
    payload_version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    visibility = Column(
        String(16),
        nullable=False,
        default="public",
        server_default=text("'public'"),
    )

    run = relationship("AssistantChatRun", back_populates="events")

    __table_args__ = (
        Index("ix_assistant_chat_run_event_run_seq", "run_id", "seq", unique=True),
        CheckConstraint(
            "payload_version > 0",
            name="ck_assistant_chat_run_event_payload_version",
        ),
        CheckConstraint(
            "visibility IN ('public','internal')",
            name="ck_assistant_chat_run_event_visibility",
        ),
        Index(
            "uq_assistant_chat_run_event_key",
            "run_id",
            "event_key",
            unique=True,
            postgresql_where=text("event_key IS NOT NULL"),
            sqlite_where=text("event_key IS NOT NULL"),
        ),
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
    last_applied_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="SET NULL"),
        nullable=True,
    )

    conversation = relationship("Conversation", back_populates="l1_memory")

    __table_args__ = (
        Index("ix_assistant_l1_memory_conversation_id", "conversation_id", unique=True),
    )


class AssistantConversationSkillL2Memory(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Conversation+skill scoped L2 facts memory.

    Plan 06 splits uniqueness:
    - Legacy: unique (conversation_id, skill_name) where skill_package_id IS NULL
    - Native: unique (conversation_id, skill_package_id, memory_namespace)
      where skill_package_id IS NOT NULL
    """

    __tablename__ = "assistant_conversation_skill_l2_memory"

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_conversation.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_name = Column(String(100), nullable=False)
    facts = Column(JSON, nullable=False, default=list)
    version = Column(Integer, nullable=False, default=1)
    skill_package_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_skill_package.id", ondelete="SET NULL"),
        nullable=True,
    )
    memory_namespace = Column(String(128), nullable=True)
    facts_v2 = Column(JSON, nullable=True)
    last_applied_run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_chat_run.id", ondelete="SET NULL"),
        nullable=True,
    )

    conversation = relationship("Conversation", back_populates="l2_memories")

    __table_args__ = (
        # Native rows require a nonempty namespace when package is set (DB-level).
        CheckConstraint(
            "("
            "  skill_package_id IS NULL AND memory_namespace IS NULL"
            ") OR ("
            "  skill_package_id IS NOT NULL"
            "  AND memory_namespace IS NOT NULL"
            "  AND length(trim(memory_namespace)) > 0"
            ")",
            name="ck_assistant_l2_memory_package_namespace_shape",
        ),
        Index(
            "uq_assistant_l2_memory_legacy_conversation_skill",
            "conversation_id",
            "skill_name",
            unique=True,
            postgresql_where=text("skill_package_id IS NULL"),
            sqlite_where=text("skill_package_id IS NULL"),
        ),
        Index(
            "uq_assistant_l2_memory_native_package_namespace",
            "conversation_id",
            "skill_package_id",
            "memory_namespace",
            unique=True,
            postgresql_where=text("skill_package_id IS NOT NULL"),
            sqlite_where=text("skill_package_id IS NOT NULL"),
        ),
    )


class AssistantConversationWorkflowCallMemory(UuidPrimaryKeyMixin, TimestampMixin, Base):
    """Conversation+workflow_call scope memory for nested workflow reuse."""

    __tablename__ = "assistant_conversation_workflow_call_memory"

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_conversation.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_node_scope = Column(String(512), nullable=False)
    target_workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey("assistant_workflow.id", ondelete="CASCADE"),
        nullable=False,
    )
    summary_text = Column(Text, nullable=False, default="")
    facts = Column(JSON, nullable=False, default=list)
    version = Column(Integer, nullable=False, default=1)

    conversation = relationship("Conversation", back_populates="workflow_call_memories")

    __table_args__ = (
        Index(
            "ix_assistant_workflow_call_memory_scope",
            "conversation_id",
            "source_workflow_id",
            "source_node_scope",
            "target_workflow_id",
            unique=True,
        ),
    )

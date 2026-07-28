"""Durable single-operator account, session, and append-only audit models."""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.common.time import utcnow
from app.database import Base

# Portable ORM digest checks (length only). Full lowercase-hex regex is
# enforced in the PostgreSQL Alembic migration, matching skills/durable pattern.


def _sha256_check(column: str, *, name: str) -> CheckConstraint:
    return CheckConstraint(f"length({column}) = 64", name=name)


# Bounded revoke reasons used by session lifecycle (Task 4+) and maintenance.
OPERATOR_SESSION_REVOKE_REASONS = (
    "logout",
    "revoke_all",
    "password_changed",
    "idle_expired",
    "absolute_expired",
    "hmac_key_removed",
    "account_disabled",
    "password_revision_mismatch",
    "maintenance",
)

_REVOKE_REASON_SQL = ", ".join(f"'{r}'" for r in OPERATOR_SESSION_REVOKE_REASONS)


class OperatorAccount(Base):
    """Singleton self-hosted operator account.

    At most one row may exist (``singleton_key = 'operator'`` unique + check).
    """

    __tablename__ = "operator_account"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    singleton_key = Column(String(32), nullable=False, default="operator")
    role = Column(String(16), nullable=False, default="operator")
    password_hash = Column(Text, nullable=False)
    password_revision = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=True)
    failed_login_window_started_at = Column(DateTime(timezone=True), nullable=True)
    failed_login_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    password_changed_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    sessions = relationship(
        "OperatorSession",
        back_populates="account",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("singleton_key", name="uq_operator_account_singleton_key"),
        CheckConstraint(
            "singleton_key = 'operator'",
            name="ck_operator_account_singleton_key",
        ),
        CheckConstraint(
            "role IN ('viewer', 'operator')",
            name="ck_operator_account_role",
        ),
        CheckConstraint(
            "password_revision > 0",
            name="ck_operator_account_password_revision_positive",
        ),
        CheckConstraint(
            "failed_login_count >= 0",
            name="ck_operator_account_failed_login_count_nonnegative",
        ),
        Index("idx_operator_account_lockout", "locked_until", "failed_login_count"),
    )


class OperatorSession(Base):
    """Durable browser session row. Raw tokens are never stored."""

    __tablename__ = "operator_session"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operator_account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("operator_account.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_digest = Column(String(64), nullable=False, unique=True)
    csrf_digest = Column(String(64), nullable=False)
    hmac_key_id = Column(String(64), nullable=False)
    password_revision = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)
    idle_expires_at = Column(DateTime(timezone=True), nullable=False)
    absolute_expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(String(64), nullable=True)
    request_digest = Column(String(64), nullable=False)
    user_agent_digest = Column(String(64), nullable=False)
    network_digest = Column(String(64), nullable=False)

    account = relationship("OperatorAccount", back_populates="sessions")

    __table_args__ = (
        _sha256_check(
            "token_digest",
            name="ck_operator_session_token_digest_hex",
        ),
        _sha256_check(
            "csrf_digest",
            name="ck_operator_session_csrf_digest_hex",
        ),
        _sha256_check(
            "request_digest",
            name="ck_operator_session_request_digest_hex",
        ),
        _sha256_check(
            "user_agent_digest",
            name="ck_operator_session_user_agent_digest_hex",
        ),
        _sha256_check(
            "network_digest",
            name="ck_operator_session_network_digest_hex",
        ),
        CheckConstraint(
            "password_revision > 0",
            name="ck_operator_session_password_revision_positive",
        ),
        CheckConstraint(
            "absolute_expires_at > created_at",
            name="ck_operator_session_absolute_after_created",
        ),
        CheckConstraint(
            "idle_expires_at <= absolute_expires_at",
            name="ck_operator_session_idle_within_absolute",
        ),
        CheckConstraint(
            "idle_expires_at > created_at",
            name="ck_operator_session_idle_after_created",
        ),
        CheckConstraint(
            f"(revoked_at IS NULL AND revoke_reason IS NULL) OR "
            f"(revoked_at IS NOT NULL AND revoke_reason IN ({_REVOKE_REASON_SQL}))",
            name="ck_operator_session_revoke_reason",
        ),
        # Active session lookup by token digest (unique already indexes token_digest).
        Index(
            "idx_operator_session_active_lookup",
            "token_digest",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "idx_operator_session_account_active",
            "operator_account_id",
            "revoked_at",
        ),
        Index(
            "idx_operator_session_absolute_expires",
            "absolute_expires_at",
        ),
    )


class OperatorAuditEvent(Base):
    """Append-only control-plane audit event.

    PostgreSQL rejects UPDATE and DELETE via
    ``trg_operator_audit_event_append_only`` (SQLSTATE 55000).
    """

    __tablename__ = "operator_audit_event"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    event_type = Column(String(64), nullable=False)
    outcome = Column(String(32), nullable=False)
    operator_id = Column(UUID(as_uuid=True), nullable=True)
    session_id = Column(UUID(as_uuid=True), nullable=True)
    request_id = Column(String(128), nullable=False)
    request_digest = Column(String(64), nullable=False)
    user_agent_digest = Column(String(64), nullable=False)
    network_digest = Column(String(64), nullable=False)
    reason_code = Column(String(64), nullable=True)
    metadata_json = Column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        _sha256_check(
            "request_digest",
            name="ck_operator_audit_event_request_digest_hex",
        ),
        _sha256_check(
            "user_agent_digest",
            name="ck_operator_audit_event_user_agent_digest_hex",
        ),
        _sha256_check(
            "network_digest",
            name="ck_operator_audit_event_network_digest_hex",
        ),
        CheckConstraint(
            "outcome IN ('succeeded', 'rejected', 'failed')",
            name="ck_operator_audit_event_outcome",
        ),
        Index(
            "idx_operator_audit_event_occurred_at",
            "occurred_at",
            "id",
        ),
        Index(
            "idx_operator_audit_event_operator_occurred",
            "operator_id",
            "occurred_at",
        ),
        Index(
            "idx_operator_audit_event_type_occurred",
            "event_type",
            "occurred_at",
        ),
    )
